import asyncio
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID


def normalize_email(value: str) -> str:
    return value.strip().lower()


def _looks_like_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


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


class JsonStorage:
    def __init__(
        self,
        email_store_path: Path,
        taken_email_store_path: Path,
        user_locale_store_path: Path,
    ) -> None:
        self.email_store_path = email_store_path
        self.taken_email_store_path = taken_email_store_path
        self.user_locale_store_path = user_locale_store_path
        self._email_lock = asyncio.Lock()
        self._taken_lock = asyncio.Lock()
        self._locale_lock = asyncio.Lock()

    async def upsert_account(self, account: EmailAccount) -> bool:
        async with self._email_lock:
            data = self._load_json(self.email_store_path, default={})
            existed = account.login_email in data
            data[account.login_email] = account.to_dict()
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
            data = self._load_json(self.taken_email_store_path, default={})
            normalized_record = self._normalize_taken_record(data.get(normalized_email))
            now = datetime.now(timezone.utc).isoformat()

            if normalized_record is not None:
                existing_owner_id = str(normalized_record["owner_id"])
                if existing_owner_id != owner_id:
                    data[normalized_email] = normalized_record
                    self._write_json(self.taken_email_store_path, data)
                    return False

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

    async def get_locale(self, user_id: int, default_locale: str = "ru") -> str:
        async with self._locale_lock:
            data = self._load_json(self.user_locale_store_path, default={})
            locale = data.get(str(user_id), default_locale)
            if locale not in {"ru", "en"}:
                return default_locale
            return locale

    async def set_locale(self, user_id: int, locale: str) -> None:
        async with self._locale_lock:
            data = self._load_json(self.user_locale_store_path, default={})
            data[str(user_id)] = locale
            self._write_json(self.user_locale_store_path, data)

    def _load_json(self, path: Path, *, default: Any) -> Any:
        if not path.exists():
            return default

        raw_content = path.read_text(encoding="utf-8").strip()
        if not raw_content:
            return default

        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError:
            return default

        if isinstance(default, dict) and not isinstance(data, dict):
            return default
        if isinstance(default, list) and not isinstance(data, list):
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
