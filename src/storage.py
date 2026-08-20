import asyncio
import json
import os
import secrets
import string
import tempfile
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from src.time_utils import moscow_end_of_day, to_moscow


class JsonStorageCorruptionError(RuntimeError):
    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"JSON store '{path}' is corrupted: {reason}")


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_key_code(value: str) -> str:
    return value.strip().upper()


def normalize_promo_code(value: str) -> str:
    return "".join(value.strip().upper().split())


def _generate_subscription_code(used_codes: set[str]) -> str:
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(15))
        if code not in used_codes:
            return code


def _looks_like_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _parse_datetime(raw_value: Any) -> datetime:
    if isinstance(raw_value, datetime):
        parsed = raw_value
    else:
        text = str(raw_value or "").strip()
        if not text:
            return datetime.now(timezone.utc)
        parsed = datetime.fromisoformat(text)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(slots=True, frozen=True)
class EmailAccount:
    login_email: str
    login_password: str
    recovery_email: str
    recovery_password: str
    refresh_token: str
    client_id: str
    raw: str

    @classmethod
    def from_add_string(cls, raw_value: str) -> "EmailAccount":
        raw = raw_value.strip()
        parts = raw.split(":", 5)
        if len(parts) != 6:
            raise ValueError("Expected 6 parts in /add payload")

        login_email = normalize_email(parts[0])
        login_password = parts[1].strip()
        recovery_email = normalize_email(parts[2])
        recovery_password = parts[3].strip()
        fifth_part = parts[4].strip()
        sixth_part = parts[5].strip()

        if _looks_like_uuid(fifth_part) and not _looks_like_uuid(sixth_part):
            client_id = fifth_part
            refresh_token = sixth_part
        elif _looks_like_uuid(sixth_part) and not _looks_like_uuid(fifth_part):
            client_id = sixth_part
            refresh_token = fifth_part
        else:
            refresh_token = fifth_part
            client_id = sixth_part

        return cls(
            login_email=login_email,
            login_password=login_password,
            recovery_email=recovery_email,
            recovery_password=recovery_password,
            refresh_token=refresh_token,
            client_id=client_id,
            raw=raw,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EmailAccount":
        return cls(
            login_email=normalize_email(str(data["login_email"])),
            login_password=str(data["login_password"]),
            recovery_email=normalize_email(str(data["recovery_email"])),
            recovery_password=str(data["recovery_password"]),
            refresh_token=str(data["refresh_token"]),
            client_id=str(data["client_id"]),
            raw=str(data.get("raw", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class SubscriptionKey:
    code: str
    email_address: str
    duration_days: int
    created_at: datetime
    expires_at: datetime
    access_version: int = 1

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SubscriptionKey":
        return cls(
            code=normalize_key_code(str(data["code"])),
            email_address=normalize_email(str(data["email_address"])),
            duration_days=int(data["duration_days"]),
            created_at=_parse_datetime(data["created_at"]),
            expires_at=_parse_datetime(data["expires_at"]),
            # Keys written before access versioning are upgraded on read. Their
            # old activations intentionally do not get this version.
            access_version=max(1, int(data.get("access_version", 1))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "email_address": self.email_address,
            "duration_days": self.duration_days,
            "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
            "expires_at": self.expires_at.astimezone(timezone.utc).isoformat(),
            "access_version": self.access_version,
        }

    def is_expired(self, now: datetime | None = None) -> bool:
        reference = now or datetime.now(timezone.utc)
        return reference >= self.expires_at


@dataclass(slots=True, frozen=True)
class UserKeyActivation:
    requester_id: str
    user_id: int
    chat_id: int
    username: str | None
    full_name: str | None
    code: str
    activated_at: datetime
    last_used_at: datetime
    access_version: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserKeyActivation":
        return cls(
            requester_id=str(data["requester_id"]),
            user_id=int(data["user_id"]),
            chat_id=int(data["chat_id"]),
            username=data.get("username"),
            full_name=data.get("full_name"),
            code=normalize_key_code(str(data["code"])),
            activated_at=_parse_datetime(data["activated_at"]),
            last_used_at=_parse_datetime(data.get("last_used_at") or data["activated_at"]),
            access_version=(
                int(data["access_version"])
                if data.get("access_version") is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requester_id": self.requester_id,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "username": self.username,
            "full_name": self.full_name,
            "code": self.code,
            "activated_at": self.activated_at.astimezone(timezone.utc).isoformat(),
            "last_used_at": self.last_used_at.astimezone(timezone.utc).isoformat(),
            "access_version": self.access_version,
        }


@dataclass(slots=True, frozen=True)
class LegacyUser:
    requester_id: str
    user_id: int
    chat_id: int | None
    username: str | None
    full_name: str | None
    source_email: str
    captured_at: datetime

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LegacyUser":
        return cls(
            requester_id=str(data["requester_id"]),
            user_id=int(data["user_id"]),
            chat_id=int(data["chat_id"]) if data.get("chat_id") is not None else None,
            username=data.get("username"),
            full_name=data.get("full_name"),
            source_email=normalize_email(str(data.get("source_email") or "")),
            captured_at=_parse_datetime(data["captured_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requester_id": self.requester_id,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "username": self.username,
            "full_name": self.full_name,
            "source_email": self.source_email,
            "captured_at": self.captured_at.astimezone(timezone.utc).isoformat(),
        }


@dataclass(slots=True, frozen=True)
class LoginCodeHistoryEntry:
    """A deduplicated Perplexity login code received by a mailbox."""

    id: str
    email_address: str
    code: str
    received_at: datetime
    message_key: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LoginCodeHistoryEntry":
        return cls(
            id=str(data["id"]),
            email_address=normalize_email(str(data["email_address"])),
            code=str(data["code"]),
            received_at=_parse_datetime(data["received_at"]),
            message_key=str(data["message_key"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "email_address": self.email_address,
            "code": self.code,
            "received_at": self.received_at.astimezone(timezone.utc).isoformat(),
            "message_key": self.message_key,
        }


@dataclass(slots=True, frozen=True)
class PerplexityPromoCode:
    """A one-time promo that extends a Perplexity subscription."""

    code: str
    additional_days: int
    used_at: datetime | None = None
    used_key_code: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PerplexityPromoCode":
        used_at = data.get("used_at")
        created_at = data.get("created_at")
        used_key_code = data.get("used_key_code")
        return cls(
            code=normalize_promo_code(str(data["code"])),
            additional_days=max(0, int(data["additional_days"])),
            created_at=_parse_datetime(created_at) if created_at else (
                _parse_datetime(used_at) if used_at else datetime.now(timezone.utc)
            ),
            used_at=_parse_datetime(used_at) if used_at else None,
            used_key_code=(
                normalize_key_code(str(used_key_code)) if used_key_code else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "additional_days": self.additional_days,
            "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
            "used_at": self.used_at.astimezone(timezone.utc).isoformat() if self.used_at else None,
            "used_key_code": self.used_key_code,
        }


class JsonStorage:
    def __init__(
        self,
        *,
        email_store_path: Path,
        taken_email_store_path: Path,
        subscription_key_store_path: Path,
        activated_key_store_path: Path,
        login_code_history_store_path: Path | None = None,
        perplexity_promo_code_store_path: Path | None = None,
        legacy_user_store_path: Path,
        user_locale_store_path: Path,
    ) -> None:
        self.email_store_path = email_store_path
        self.taken_email_store_path = taken_email_store_path
        self.subscription_key_store_path = subscription_key_store_path
        self.activated_key_store_path = activated_key_store_path
        self.login_code_history_store_path = (
            login_code_history_store_path
            if login_code_history_store_path is not None
            else activated_key_store_path.with_name("perplexity_login_codes.json")
        )
        self.perplexity_promo_code_store_path = (
            perplexity_promo_code_store_path
            if perplexity_promo_code_store_path is not None
            else activated_key_store_path.with_name("perplexity_promo_codes.json")
        )
        self.legacy_user_store_path = legacy_user_store_path
        self.legacy_user_backup_store_path = legacy_user_store_path.with_name(
            f"{legacy_user_store_path.stem}.backup{legacy_user_store_path.suffix}"
        )
        self.user_locale_store_path = user_locale_store_path
        self._email_lock = asyncio.Lock()
        self._taken_lock = asyncio.Lock()
        self._key_lock = asyncio.Lock()
        self._activation_lock = asyncio.Lock()
        self._login_code_history_lock = asyncio.Lock()
        self._promo_lock = asyncio.Lock()
        self._legacy_lock = asyncio.Lock()
        self._locale_lock = asyncio.Lock()

    @staticmethod
    def _remove_activations_for_email_locked(
        *,
        key_data: dict[str, Any],
        activation_data: dict[str, Any],
        email_address: str,
    ) -> None:
        """Remove bindings for every key that can disclose this account.

        The caller must hold the email, key, and activation locks.  Account
        credentials are mutable while an activation is merely a browser or
        Telegram requester binding, so retaining that binding after a
        credential change would give old key holders the replacement account.
        """
        protected_codes: set[str] = set()
        for raw_key in key_data.values():
            if not isinstance(raw_key, dict):
                continue
            try:
                key = SubscriptionKey.from_dict(raw_key)
            except (KeyError, TypeError, ValueError):
                continue
            if key.email_address == email_address:
                protected_codes.add(key.code)

        for requester_id, record in list(activation_data.items()):
            if (
                isinstance(record, dict)
                and normalize_key_code(str(record.get("code") or "")) in protected_codes
            ):
                activation_data.pop(requester_id, None)

    @staticmethod
    def _bump_key_access_versions_for_email_locked(
        *,
        key_data: dict[str, Any],
        email_address: str,
    ) -> None:
        """Revoke all existing bindings for an account without trusting cleanup."""
        for code, raw_key in key_data.items():
            if not isinstance(raw_key, dict):
                continue
            try:
                key = SubscriptionKey.from_dict(raw_key)
            except (KeyError, TypeError, ValueError):
                continue
            if key.email_address != email_address:
                continue
            key_data[code] = SubscriptionKey(
                code=key.code,
                email_address=key.email_address,
                duration_days=key.duration_days,
                created_at=key.created_at,
                expires_at=key.expires_at,
                access_version=key.access_version + 1,
            ).to_dict()

    async def upsert_account(self, account: EmailAccount) -> bool:
        async with self._email_lock, self._key_lock, self._activation_lock:
            data = self._load_json(self.email_store_path, default={}, strict=True)
            existing_account_data = data.get(account.login_email)
            existed = existing_account_data is not None
            account_changed = existing_account_data != account.to_dict()
            data[account.login_email] = account.to_dict()

            if existed and account_changed:
                key_data = self._load_json(
                    self.subscription_key_store_path,
                    default={},
                    strict=True,
                )
                activation_data = self._load_json(
                    self.activated_key_store_path,
                    default={},
                    strict=True,
                )
                self._bump_key_access_versions_for_email_locked(
                    key_data=key_data,
                    email_address=account.login_email,
                )
                self._remove_activations_for_email_locked(
                    key_data=key_data,
                    activation_data=activation_data,
                    email_address=account.login_email,
                )
                self._write_json(self.activated_key_store_path, activation_data)
                self._write_json(self.subscription_key_store_path, key_data)

            self._write_json(self.email_store_path, data)
            return existed

    async def get_account(self, email_address: str) -> EmailAccount | None:
        normalized_email = normalize_email(email_address)
        async with self._email_lock:
            data = self._load_json(self.email_store_path, default={})
            account_data = data.get(normalized_email)
            if not isinstance(account_data, dict):
                return None
            return EmailAccount.from_dict(account_data)

    async def list_accounts(self) -> list[EmailAccount]:
        async with self._email_lock:
            data = self._load_json(self.email_store_path, default={})

        accounts: list[EmailAccount] = []
        for raw_value in data.values():
            if not isinstance(raw_value, dict):
                continue
            try:
                accounts.append(EmailAccount.from_dict(raw_value))
            except (KeyError, TypeError, ValueError):
                continue

        return sorted(accounts, key=lambda item: item.login_email)

    async def replace_account(
        self,
        original_email: str,
        account: EmailAccount,
    ) -> str:
        normalized_original_email = normalize_email(original_email)
        normalized_target_email = normalize_email(account.login_email)

        async with self._email_lock, self._key_lock, self._activation_lock:
            email_data = self._load_json(self.email_store_path, default={}, strict=True)
            existing_account = email_data.get(normalized_original_email)
            if not isinstance(existing_account, dict):
                return "missing"

            if (
                normalized_target_email != normalized_original_email
                and normalized_target_email in email_data
            ):
                return "conflict"

            if normalized_target_email != normalized_original_email:
                email_data.pop(normalized_original_email, None)
            email_data[normalized_target_email] = account.to_dict()

            key_data = self._load_json(
                self.subscription_key_store_path,
                default={},
                strict=True,
            )
            for code, raw_value in key_data.items():
                if not isinstance(raw_value, dict):
                    continue
                try:
                    key = SubscriptionKey.from_dict(raw_value)
                except (KeyError, TypeError, ValueError):
                    continue
                if key.email_address != normalized_original_email:
                    continue
                key_data[code] = SubscriptionKey(
                    code=key.code,
                    email_address=normalized_target_email,
                    duration_days=key.duration_days,
                    created_at=key.created_at,
                    expires_at=key.expires_at,
                    access_version=key.access_version,
                ).to_dict()

            if EmailAccount.from_dict(existing_account) != account:
                activation_data = self._load_json(
                    self.activated_key_store_path,
                    default={},
                    strict=True,
                )
                self._remove_activations_for_email_locked(
                    key_data=key_data,
                    activation_data=activation_data,
                    email_address=normalized_target_email,
                )
                self._bump_key_access_versions_for_email_locked(
                    key_data=key_data,
                    email_address=normalized_target_email,
                )
                self._write_json(self.activated_key_store_path, activation_data)

            self._write_json(self.email_store_path, email_data)
            self._write_json(self.subscription_key_store_path, key_data)
            return "updated"

    async def upsert_account_with_subscription_key(
        self,
        *,
        account: EmailAccount,
        duration_days: int,
        key_code: str | None = None,
    ) -> tuple[str, SubscriptionKey | None]:
        normalized_key_code = normalize_key_code(key_code) if key_code and key_code.strip() else None

        async with self._email_lock, self._key_lock, self._activation_lock:
            email_data = self._load_json(self.email_store_path, default={}, strict=True)
            key_data = self._load_json(
                self.subscription_key_store_path,
                default={},
                strict=True,
            )

            if normalized_key_code and normalized_key_code in key_data:
                return "conflict_key", None

            existing_account_data = email_data.get(account.login_email)
            existed = existing_account_data is not None
            account_changed = existing_account_data != account.to_dict()
            email_data[account.login_email] = account.to_dict()

            used_codes = {normalize_key_code(code) for code in key_data}
            final_key_code = normalized_key_code or _generate_subscription_code(used_codes)
            created_at = datetime.now(timezone.utc)
            expires_on = to_moscow(created_at).date() + timedelta(days=duration_days)
            expires_at = moscow_end_of_day(expires_on)
            if existed and account_changed:
                activation_data = self._load_json(
                    self.activated_key_store_path,
                    default={},
                    strict=True,
                )
                self._bump_key_access_versions_for_email_locked(
                    key_data=key_data,
                    email_address=account.login_email,
                )
                self._remove_activations_for_email_locked(
                    key_data=key_data,
                    activation_data=activation_data,
                    email_address=account.login_email,
                )
                self._write_json(self.activated_key_store_path, activation_data)

            key = SubscriptionKey(
                code=final_key_code,
                email_address=account.login_email,
                duration_days=duration_days,
                created_at=created_at,
                expires_at=expires_at,
            )
            key_data[final_key_code] = key.to_dict()

            self._write_json(self.email_store_path, email_data)
            self._write_json(self.subscription_key_store_path, key_data)
            return ("updated" if existed else "created"), key

    async def replace_subscription_bundle(
        self,
        *,
        original_email: str,
        original_code: str,
        account: EmailAccount,
        key_code: str,
        duration_days: int,
        activated_at: datetime,
        selected_requester_id: str | None = None,
    ) -> str:
        normalized_original_email = normalize_email(original_email)
        normalized_target_email = normalize_email(account.login_email)
        normalized_original_code = normalize_key_code(original_code)
        normalized_target_code = normalize_key_code(key_code)
        normalized_activated_at = _parse_datetime(activated_at)

        async with self._email_lock, self._key_lock, self._activation_lock:
            email_data = self._load_json(self.email_store_path, default={}, strict=True)
            existing_account = email_data.get(normalized_original_email)
            if not isinstance(existing_account, dict):
                return "missing_account"

            if (
                normalized_target_email != normalized_original_email
                and normalized_target_email in email_data
            ):
                return "conflict_account"

            key_data = self._load_json(
                self.subscription_key_store_path,
                default={},
                strict=True,
            )
            existing_key_data = key_data.get(normalized_original_code)
            if not isinstance(existing_key_data, dict):
                return "missing_key"

            if (
                normalized_target_code != normalized_original_code
                and normalized_target_code in key_data
            ):
                return "conflict_key"

            if normalized_target_email != normalized_original_email:
                email_data.pop(normalized_original_email, None)
            email_data[normalized_target_email] = account.to_dict()

            original_key = SubscriptionKey.from_dict(existing_key_data)
            original_account = EmailAccount.from_dict(existing_account)
            access_changed = (
                normalized_target_code != normalized_original_code
                or account != original_account
            )
            expires_on = to_moscow(normalized_activated_at).date() + timedelta(days=duration_days)
            expires_at = moscow_end_of_day(expires_on)
            updated_key = SubscriptionKey(
                code=normalized_target_code,
                email_address=normalized_target_email,
                duration_days=duration_days,
                created_at=normalized_activated_at,
                expires_at=expires_at,
                access_version=(
                    original_key.access_version + 1
                    if access_changed
                    else original_key.access_version
                ),
            )

            if normalized_target_code != normalized_original_code:
                key_data.pop(normalized_original_code, None)
            key_data[normalized_target_code] = updated_key.to_dict()

            activation_data = self._load_json(
                self.activated_key_store_path,
                default={},
                strict=True,
            )
            if access_changed:
                # A browser cookie identifies the requester, not a specific
                # subscription version. Do not transfer its old activation to
                # a replacement code or account.
                activation_data = {
                    requester_id: record
                    for requester_id, record in activation_data.items()
                    if not isinstance(record, dict)
                    or normalize_key_code(str(record.get("code") or "")) != normalized_original_code
                }

            self._write_json(self.email_store_path, email_data)
            self._write_json(self.subscription_key_store_path, key_data)
            self._write_json(self.activated_key_store_path, activation_data)
            return "updated"

    async def delete_account(self, email_address: str) -> bool:
        normalized_email = normalize_email(email_address)
        async with self._email_lock, self._key_lock, self._activation_lock:
            data = self._load_json(self.email_store_path, default={}, strict=True)
            removed = data.pop(normalized_email, None)
            if removed is None:
                return False

            key_data = self._load_json(
                self.subscription_key_store_path,
                default={},
                strict=True,
            )
            removed_codes: set[str] = set()
            filtered_key_data: dict[str, Any] = {}
            for code, raw_value in key_data.items():
                if not isinstance(raw_value, dict):
                    filtered_key_data[code] = raw_value
                    continue
                try:
                    key = SubscriptionKey.from_dict(raw_value)
                except (KeyError, TypeError, ValueError):
                    filtered_key_data[code] = raw_value
                    continue
                if key.email_address == normalized_email:
                    removed_codes.add(key.code)
                    continue
                filtered_key_data[code] = raw_value

            activation_data = self._load_json(
                self.activated_key_store_path,
                default={},
                strict=True,
            )
            filtered_activation_data = {
                requester_id: record
                for requester_id, record in activation_data.items()
                if not isinstance(record, dict)
                or normalize_key_code(str(record.get("code") or "")) not in removed_codes
            }

            self._write_json(self.email_store_path, data)
            self._write_json(self.subscription_key_store_path, filtered_key_data)
            self._write_json(self.activated_key_store_path, filtered_activation_data)
            return True

    async def reserve_email(
        self,
        email_address: str,
        *,
        owner_id: str,
        owner_kind: str,
        user_id: int | None,
        chat_id: int | None,
        username: str | None,
        full_name: str | None,
    ) -> bool:
        normalized_email = normalize_email(email_address)
        async with self._taken_lock:
            data = self._load_json(self.taken_email_store_path, default={}, strict=True)
            normalized_record = self._normalize_taken_record(data.get(normalized_email))
            now = datetime.now(timezone.utc).isoformat()

            if normalized_record is not None:
                created_at = str(normalized_record.get("created_at") or now)
                request_count_raw = normalized_record.get("request_count", 1)
                request_count = (
                    request_count_raw
                    if isinstance(request_count_raw, int) and request_count_raw > 0
                    else 1
                )
            else:
                created_at = now
                request_count = 0

            # Legacy flow only: email_taken.json remains a usage log for
            # users that already worked through the old email-based scheme.
            data[normalized_email] = {
                "owner_id": owner_id,
                "owner_kind": owner_kind,
                "user_id": user_id,
                "chat_id": chat_id,
                "username": username,
                "full_name": full_name,
                "created_at": created_at,
                "last_used_at": now,
                "request_count": request_count + 1,
            }
            self._write_json(self.taken_email_store_path, data)
            return True

    async def add_subscription_keys(self, keys: list[SubscriptionKey]) -> None:
        if not keys:
            return

        async with self._key_lock:
            data = self._load_json(self.subscription_key_store_path, default={}, strict=True)
            for key in keys:
                data[key.code] = key.to_dict()
            self._write_json(self.subscription_key_store_path, data)

    async def get_subscription_key(self, code: str) -> SubscriptionKey | None:
        normalized_code = normalize_key_code(code)
        async with self._key_lock:
            data = self._load_json(self.subscription_key_store_path, default={})
            key_data = data.get(normalized_code)
            if not isinstance(key_data, dict):
                return None
            return SubscriptionKey.from_dict(key_data)

    async def list_subscription_keys(self) -> list[SubscriptionKey]:
        async with self._key_lock:
            data = self._load_json(self.subscription_key_store_path, default={})

        keys: list[SubscriptionKey] = []
        for raw_value in data.values():
            if not isinstance(raw_value, dict):
                continue
            try:
                keys.append(SubscriptionKey.from_dict(raw_value))
            except (KeyError, TypeError, ValueError):
                continue

        return sorted(keys, key=lambda item: (item.is_expired(), item.expires_at, item.code))

    async def delete_subscription_key(self, code: str) -> bool:
        normalized_code = normalize_key_code(code)
        async with self._key_lock, self._activation_lock:
            keys_data = self._load_json(
                self.subscription_key_store_path,
                default={},
                strict=True,
            )
            removed = keys_data.pop(normalized_code, None)
            if removed is None:
                return False

            activation_data = self._load_json(
                self.activated_key_store_path,
                default={},
                strict=True,
            )
            filtered_activation_data = {
                requester_id: record
                for requester_id, record in activation_data.items()
                if not isinstance(record, dict)
                or normalize_key_code(str(record.get("code") or "")) != normalized_code
            }

            self._write_json(self.subscription_key_store_path, keys_data)
            self._write_json(self.activated_key_store_path, filtered_activation_data)
            return True

    async def activate_subscription_key(
        self,
        *,
        requester_id: str,
        user_id: int,
        chat_id: int,
        username: str | None,
        full_name: str | None,
        code: str,
        access_version: int,
    ) -> UserKeyActivation:
        activation = UserKeyActivation(
            requester_id=requester_id,
            user_id=user_id,
            chat_id=chat_id,
            username=username,
            full_name=full_name,
            code=normalize_key_code(code),
            activated_at=datetime.now(timezone.utc),
            last_used_at=datetime.now(timezone.utc),
            access_version=access_version,
        )
        async with self._activation_lock:
            data = self._load_json(
                self.activated_key_store_path,
                default={},
                strict=True,
            )
            # Persist bindings per requester so a single subscription key can
            # stay active for any number of users at the same time.
            data[requester_id] = activation.to_dict()
            self._write_json(self.activated_key_store_path, data)
        return activation

    async def get_user_activation(self, requester_id: str) -> UserKeyActivation | None:
        async with self._activation_lock:
            data = self._load_json(self.activated_key_store_path, default={})
            activation_data = data.get(requester_id)
            if not isinstance(activation_data, dict):
                return None
            return UserKeyActivation.from_dict(activation_data)

    async def list_user_activations(self) -> list[UserKeyActivation]:
        async with self._activation_lock:
            data = self._load_json(self.activated_key_store_path, default={})

        activations: list[UserKeyActivation] = []
        for raw_value in data.values():
            if not isinstance(raw_value, dict):
                continue
            try:
                activations.append(UserKeyActivation.from_dict(raw_value))
            except (KeyError, TypeError, ValueError):
                continue

        return sorted(
            activations,
            key=lambda item: (item.activated_at, item.requester_id),
            reverse=True,
        )

    async def clear_user_activation(self, requester_id: str) -> bool:
        async with self._activation_lock:
            data = self._load_json(
                self.activated_key_store_path,
                default={},
                strict=True,
            )
            removed = data.pop(requester_id, None)
            if removed is None:
                return False
            self._write_json(self.activated_key_store_path, data)
            return True

    async def list_login_code_history(
        self,
        email_address: str,
    ) -> list[LoginCodeHistoryEntry]:
        normalized_email = normalize_email(email_address)
        async with self._login_code_history_lock:
            data = self._load_json(self.login_code_history_store_path, default={})

        entries: list[LoginCodeHistoryEntry] = []
        for raw_entry in data.get(normalized_email, []):
            if not isinstance(raw_entry, dict):
                continue
            try:
                entries.append(LoginCodeHistoryEntry.from_dict(raw_entry))
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(entries, key=lambda entry: (entry.received_at, entry.id), reverse=True)

    async def add_login_code_history_entries(
        self,
        entries: list[LoginCodeHistoryEntry],
        *,
        limit_per_email: int = 100,
    ) -> list[LoginCodeHistoryEntry]:
        """Persist unseen codes, deduplicated by the immutable mailbox message key."""
        if limit_per_email < 1:
            raise ValueError("limit_per_email must be positive")

        grouped: dict[str, list[LoginCodeHistoryEntry]] = {}
        for entry in entries:
            grouped.setdefault(normalize_email(entry.email_address), []).append(entry)
        if not grouped:
            return []

        async with self._login_code_history_lock:
            data = self._load_json(
                self.login_code_history_store_path,
                default={},
                strict=True,
            )
            added: list[LoginCodeHistoryEntry] = []
            changed = False
            for email_address, new_entries in grouped.items():
                existing: list[LoginCodeHistoryEntry] = []
                for raw_entry in data.get(email_address, []):
                    if not isinstance(raw_entry, dict):
                        continue
                    try:
                        existing.append(LoginCodeHistoryEntry.from_dict(raw_entry))
                    except (KeyError, TypeError, ValueError):
                        continue

                known_keys = {entry.message_key for entry in existing}
                for entry in new_entries:
                    if entry.message_key in known_keys:
                        continue
                    known_keys.add(entry.message_key)
                    existing.append(entry)
                    added.append(entry)
                    changed = True

                existing.sort(key=lambda entry: (entry.received_at, entry.id), reverse=True)
                trimmed = existing[:limit_per_email]
                if len(trimmed) != len(existing):
                    changed = True
                data[email_address] = [entry.to_dict() for entry in trimmed]

            if changed:
                self._write_json(self.login_code_history_store_path, data)
            return sorted(added, key=lambda entry: (entry.received_at, entry.id), reverse=True)

    async def add_perplexity_promo_codes(self, created: list[PerplexityPromoCode]) -> None:
        """Atomically append unused promo codes after checking uniqueness."""
        if not created:
            return
        async with self._promo_lock:
            data = self._load_json(
                self.perplexity_promo_code_store_path,
                default=[],
                strict=True,
            )
            if not isinstance(data, list):
                raise JsonStorageCorruptionError(
                    self.perplexity_promo_code_store_path,
                    "root is not a list",
                )
            existing = {
                promo.code
                for raw in data
                if isinstance(raw, dict)
                for promo in [self._promo_from_dict(raw)]
                if promo is not None
            }
            new_codes = [promo.code for promo in created]
            if len(set(new_codes)) != len(new_codes) or any(code in existing for code in new_codes):
                raise ValueError("Promo code already exists.")
            data.extend(promo.to_dict() for promo in created)
            self._write_json(self.perplexity_promo_code_store_path, data)

    async def claim_perplexity_promo_code(
        self,
        code: str,
        key_code: str,
    ) -> PerplexityPromoCode | None:
        normalized_code = normalize_promo_code(code)
        normalized_key_code = normalize_key_code(key_code)
        if not normalized_code or not normalized_key_code:
            return None
        async with self._promo_lock:
            data = self._load_json(
                self.perplexity_promo_code_store_path,
                default=[],
                strict=True,
            )
            if not isinstance(data, list):
                raise JsonStorageCorruptionError(
                    self.perplexity_promo_code_store_path,
                    "root is not a list",
                )
            for index, raw in enumerate(data):
                if not isinstance(raw, dict):
                    continue
                promo = self._promo_from_dict(raw)
                if promo is None or promo.code != normalized_code or promo.used_at is not None:
                    continue
                claimed = replace(
                    promo,
                    used_at=datetime.now(timezone.utc),
                    used_key_code=normalized_key_code,
                )
                data[index] = claimed.to_dict()
                self._write_json(self.perplexity_promo_code_store_path, data)
                return claimed
        return None

    async def restore_unclaimed_perplexity_promo_code(
        self,
        code: str,
        key_code: str,
    ) -> bool:
        """Undo a provisional claim when the subscription credit was rejected."""
        normalized_code = normalize_promo_code(code)
        normalized_key_code = normalize_key_code(key_code)
        async with self._promo_lock:
            data = self._load_json(
                self.perplexity_promo_code_store_path,
                default=[],
                strict=True,
            )
            if not isinstance(data, list):
                raise JsonStorageCorruptionError(
                    self.perplexity_promo_code_store_path,
                    "root is not a list",
                )
            for index, raw in enumerate(data):
                if not isinstance(raw, dict):
                    continue
                promo = self._promo_from_dict(raw)
                if (
                    promo is None
                    or promo.code != normalized_code
                    or promo.used_key_code != normalized_key_code
                ):
                    continue
                data[index] = replace(promo, used_at=None, used_key_code=None).to_dict()
                self._write_json(self.perplexity_promo_code_store_path, data)
                return True
        return False

    async def extend_subscription_key(
        self,
        code: str,
        additional_days: int,
    ) -> SubscriptionKey | None:
        if additional_days < 1:
            return None
        normalized_code = normalize_key_code(code)
        async with self._key_lock:
            data = self._load_json(
                self.subscription_key_store_path,
                default={},
                strict=True,
            )
            raw_key = data.get(normalized_code)
            if not isinstance(raw_key, dict):
                return None
            key = SubscriptionKey.from_dict(raw_key)
            now = datetime.now(timezone.utc)
            base_date = to_moscow(key.expires_at if key.expires_at > now else now).date()
            updated = SubscriptionKey(
                code=key.code,
                email_address=key.email_address,
                duration_days=key.duration_days + additional_days,
                created_at=key.created_at,
                expires_at=moscow_end_of_day(base_date + timedelta(days=additional_days)),
                access_version=key.access_version,
            )
            data[normalized_code] = updated.to_dict()
            self._write_json(self.subscription_key_store_path, data)
            return updated

    @staticmethod
    def _promo_from_dict(raw: dict[str, Any]) -> PerplexityPromoCode | None:
        try:
            return PerplexityPromoCode.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            return None

    async def freeze_legacy_users(self) -> None:
        """Take a one-time backup of the legacy direct-email allow-list.

        `email_taken.json` is mutable request telemetry, not an authorization
        database.  Older versions promoted entries from it every time a user
        interacted with the bot; a legacy user could therefore request an
        arbitrary existing mailbox and be recorded against it.  On the first
        run of this version we preserve the existing legacy list, supplement
        it from historical telemetry only when necessary, and write an
        immutable-on-normal-operation backup.  Future calls deliberately do
        not import any new entries from request telemetry.
        """
        async with self._taken_lock, self._legacy_lock:
            if self.legacy_user_backup_store_path.exists():
                return

            legacy_data = self._load_json(
                self.legacy_user_store_path,
                default={},
                strict=True,
            )
            if not legacy_data:
                taken_data = self._load_json(self.taken_email_store_path, default={})
                candidates: dict[str, LegacyUser] = {}
                for email_address, raw_record in taken_data.items():
                    normalized_record = self._normalize_taken_record(raw_record)
                    if normalized_record is None:
                        continue
                    if str(normalized_record.get("owner_kind")) != "telegram":
                        continue

                    requester_id = str(normalized_record.get("owner_id") or "").strip()
                    if not requester_id:
                        continue
                    user_id = normalized_record.get("user_id")
                    if not isinstance(user_id, int) or user_id <= 0:
                        if requester_id.startswith("tg:") and requester_id[3:].isdigit():
                            user_id = int(requester_id[3:])
                        else:
                            continue

                    candidate = LegacyUser(
                        requester_id=requester_id,
                        user_id=user_id,
                        chat_id=normalized_record.get("chat_id")
                        if isinstance(normalized_record.get("chat_id"), int)
                        else None,
                        username=normalized_record.get("username")
                        if isinstance(normalized_record.get("username"), str)
                        else None,
                        full_name=normalized_record.get("full_name")
                        if isinstance(normalized_record.get("full_name"), str)
                        else None,
                        source_email=normalize_email(str(email_address)),
                        captured_at=_parse_datetime(
                            normalized_record.get("created_at")
                            or normalized_record.get("last_used_at")
                        ),
                    )
                    existing = candidates.get(requester_id)
                    if existing is None or candidate.captured_at < existing.captured_at:
                        candidates[requester_id] = candidate

                legacy_data = {
                    requester_id: legacy_user.to_dict()
                    for requester_id, legacy_user in candidates.items()
                }
                self._write_json(self.legacy_user_store_path, legacy_data)

            self._write_json(self.legacy_user_backup_store_path, legacy_data)

    async def sync_legacy_users_from_taken(self) -> None:
        """Backward-compatible name for the now one-time legacy migration."""
        await self.freeze_legacy_users()

    async def get_legacy_user(self, requester_id: str) -> LegacyUser | None:
        await self.freeze_legacy_users()
        async with self._legacy_lock:
            data = self._load_json(
                self.legacy_user_backup_store_path,
                default={},
                strict=True,
            )
            raw_user = data.get(requester_id)
            if not isinstance(raw_user, dict):
                return None
            return LegacyUser.from_dict(raw_user)

    async def is_legacy_requester(self, requester_id: str) -> bool:
        return await self.get_legacy_user(requester_id) is not None

    async def get_locale(self, user_id: int, default_locale: str = "ru") -> str:
        async with self._locale_lock:
            data = self._load_json(self.user_locale_store_path, default={})
            locale = data.get(str(user_id), default_locale)
            if locale not in {"ru", "en"}:
                return default_locale
            return locale

    async def has_locale(self, user_id: int) -> bool:
        async with self._locale_lock:
            data = self._load_json(self.user_locale_store_path, default={})
            locale = data.get(str(user_id))
            return locale in {"ru", "en"}

    async def set_locale(self, user_id: int, locale: str) -> None:
        async with self._locale_lock:
            data = self._load_json(self.user_locale_store_path, default={}, strict=True)
            data[str(user_id)] = locale
            self._write_json(self.user_locale_store_path, data)

    def _load_json(self, path: Path, *, default: Any, strict: bool = False) -> Any:
        if not path.exists():
            return default

        raw_content = path.read_text(encoding="utf-8").strip()
        if not raw_content:
            # Fresh deployments often keep placeholder JSON files checked in as
            # zero-byte files. Treat them as empty stores; strict mode only
            # rejects malformed non-empty content.
            return default

        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            if strict:
                raise JsonStorageCorruptionError(path, str(exc)) from exc
            return default

        if isinstance(default, dict) and not isinstance(data, dict):
            if strict:
                raise JsonStorageCorruptionError(path, "expected a JSON object")
            return default
        if isinstance(default, list) and not isinstance(data, list):
            if strict:
                raise JsonStorageCorruptionError(path, "expected a JSON array")
            return default
        return data

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as temporary_file:
            json.dump(data, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            temp_name = temporary_file.name

        os.replace(temp_name, path)

    def _normalize_taken_record(self, record: Any) -> dict[str, Any] | None:
        if not isinstance(record, dict):
            return None

        normalized_record = dict(record)
        owner_id = normalized_record.get("owner_id")
        owner_kind = normalized_record.get("owner_kind")

        if not owner_id:
            legacy_user_id = normalized_record.get("user_id")
            legacy_username = normalized_record.get("username")
            legacy_full_name = normalized_record.get("full_name")
            legacy_created_at = normalized_record.get("created_at")

            if isinstance(legacy_user_id, int) and legacy_user_id > 0:
                owner_id = f"tg:{legacy_user_id}"
                owner_kind = "telegram"
            elif legacy_username == "web":
                legacy_marker = legacy_full_name or legacy_created_at or "anonymous"
                owner_id = f"web-legacy:{legacy_marker}"
                owner_kind = "web"
            else:
                legacy_marker = legacy_created_at or "unknown"
                owner_id = f"legacy:{legacy_marker}"
                owner_kind = "unknown"

        normalized_record["owner_id"] = str(owner_id)
        normalized_record["owner_kind"] = str(owner_kind)

        request_count = normalized_record.get("request_count")
        if not isinstance(request_count, int) or request_count <= 0:
            normalized_record["request_count"] = 1

        return normalized_record
