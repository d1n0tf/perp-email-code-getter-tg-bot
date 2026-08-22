import email
import hashlib
import html
import imaplib
import json
import logging
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime

from src.config import Settings
from src.storage import EmailAccount


CODE_REGEX = re.compile(r"(?<!\d)\d{6}(?!\d)")
SPACED_CODE_REGEX = re.compile(
    r"(?<!\d)(?:\d[\s\u00a0\u200b\u200c\u200d\ufeff]*){5}\d(?!\d)"
)
HTML_TAG_REGEX = re.compile(r"<[^>]+>")
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
LOGGER = logging.getLogger(__name__)
# Scanning is done every ten seconds while a page is open.  Fetch this many
# newest messages per folder in one IMAP command, rather than re-reading an
# entire 24-hour mailbox on every poll.
RECENT_SCAN_UID_LIMIT = 50
UID_IN_FETCH_RESPONSE_REGEX = re.compile(r"\bUID\s+(\d+)\b", re.IGNORECASE)


class CodeWaitTimeout(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class CodeResult:
    code: str
    folder: str


@dataclass(slots=True, frozen=True)
class RecentCodeRecord:
    """A login code found while importing the recent Perplexity mail history.

    ``message_identity`` is the RFC ``Message-ID`` when a message has one.
    Mail without that header receives a SHA-256 identity based on its complete
    RFC822 payload, so copies of the same message in multiple folders can still
    be de-duplicated by the caller.
    """

    code: str
    timestamp: datetime
    folder: str
    message_identity: str


class GlobalMailRequestLimiter:
    def __init__(
        self,
        *,
        rate_limit_per_second: float,
        backoff_base_seconds: float,
        backoff_max_seconds: float,
    ) -> None:
        if rate_limit_per_second <= 0:
            raise ValueError("rate_limit_per_second must be positive")

        self.min_interval_seconds = 1.0 / rate_limit_per_second
        self.backoff_base_seconds = max(backoff_base_seconds, self.min_interval_seconds)
        self.backoff_max_seconds = max(
            backoff_max_seconds,
            self.backoff_base_seconds,
        )
        self._condition = threading.Condition()
        self._next_request_at = 0.0
        self._backoff_until = 0.0
        self._consecutive_failures = 0
        self._priority_waiters = 0

    def wait_for_slot(self, *, priority: bool = False) -> None:
        with self._condition:
            if priority:
                self._priority_waiters += 1
                self._condition.notify_all()
            try:
                while True:
                    # A live page must not remain behind a backlog of legacy
                    # ten-minute code waiters.  One priority operation gets
                    # the next available IMAP slot, then normal polling
                    # resumes; this is priority, not a separate rate limit.
                    if not priority and self._priority_waiters:
                        self._condition.wait()
                        continue

                    now = time.monotonic()
                    allowed_at = max(self._next_request_at, self._backoff_until)
                    wait_seconds = allowed_at - now
                    if wait_seconds <= 0:
                        self._next_request_at = now + self.min_interval_seconds
                        return
                    self._condition.wait(timeout=wait_seconds)
            finally:
                if priority:
                    self._priority_waiters -= 1
                    self._condition.notify_all()

    def record_success(self) -> None:
        with self._condition:
            self._consecutive_failures = 0
            if self._backoff_until <= time.monotonic():
                self._backoff_until = 0.0
            self._condition.notify_all()

    def record_failure(self) -> None:
        with self._condition:
            self._consecutive_failures += 1
            backoff_seconds = min(
                self.backoff_max_seconds,
                self.backoff_base_seconds * (2 ** (self._consecutive_failures - 1)),
            )
            self._backoff_until = max(
                self._backoff_until,
                time.monotonic() + backoff_seconds,
            )
            self._condition.notify_all()


class TokenManager:
    def __init__(self, client_id: str, refresh_token: str) -> None:
        self.client_id = client_id
        self.refresh_token = refresh_token
        self.access_token: str | None = None
        self.expire_time: datetime | None = None
        self._lock = threading.Lock()

    def get_token(self) -> str | None:
        # Several open pages may request the same mailbox concurrently.  One
        # cached access token avoids a fresh OAuth exchange for every
        # ten-second history poll, while the lock prevents duplicate refreshes.
        with self._lock:
            now = datetime.now(timezone.utc)
            if self.access_token and self.expire_time and now < self.expire_time:
                return self.access_token

            payload = urllib.parse.urlencode(
                {
                    "client_id": self.client_id,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                    "scope": "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
                }
            ).encode()
            request = urllib.request.Request(
                TOKEN_URL,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )

            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
                return None

            access_token = data.get("access_token")
            if not access_token:
                return None

            expires_in = int(data.get("expires_in", 3600))
            self.access_token = str(access_token)
            self.expire_time = now + timedelta(seconds=max(expires_in - 60, 60))
            return self.access_token


class EmailCodeFetcher:
    def __init__(self, settings: Settings) -> None:
        self.imap_host = settings.imap_host
        self.search_from = settings.search_from
        self.folders = settings.mail_folders
        self.wait_timeout_seconds = settings.mail_wait_timeout_seconds
        self.recent_window_seconds = settings.mail_recent_window_seconds
        self.poll_interval_min_seconds = settings.mail_poll_interval_min_seconds
        self.poll_interval_max_seconds = settings.mail_poll_interval_max_seconds
        self.reconnect_delay_seconds = settings.mail_reconnect_delay_seconds
        self._token_managers: dict[tuple[str, str, str], TokenManager] = {}
        self._token_managers_lock = threading.Lock()
        self._request_priority = threading.local()
        self._unparsed_message_keys: set[str] = set()
        self._unparsed_message_keys_lock = threading.Lock()
        self.limiter = GlobalMailRequestLimiter(
            rate_limit_per_second=settings.mail_global_rate_limit_per_second,
            backoff_base_seconds=settings.mail_global_backoff_base_seconds,
            backoff_max_seconds=settings.mail_global_backoff_max_seconds,
        )

    def wait_for_code(self, account: EmailAccount) -> CodeResult:
        deadline = time.monotonic() + self.wait_timeout_seconds
        started_at = datetime.now(timezone.utc)
        token_manager = self._token_manager_for(account)
        imap: imaplib.IMAP4_SSL | None = None
        baseline_uids: dict[str, str | None] | None = None

        while time.monotonic() < deadline:
            try:
                if imap is None:
                    imap = self._connect(account.login_email, token_manager)
                    recent_result = self._get_recent_code(imap, started_at)
                    if recent_result is not None:
                        self._safe_logout(imap)
                        return recent_result
                    baseline_uids = self._snapshot_latest_uids(imap)

                if baseline_uids is None:
                    baseline_uids = self._snapshot_latest_uids(imap)

                result = self._poll_for_new_code(imap, baseline_uids)
                if result is not None:
                    self._safe_logout(imap)
                    return result

                time.sleep(
                    random.uniform(
                        self.poll_interval_min_seconds,
                        self.poll_interval_max_seconds,
                    )
                )
            except Exception:
                self._safe_logout(imap)
                imap = None
                baseline_uids = None
                time.sleep(self.reconnect_delay_seconds)

        self._safe_logout(imap)
        raise CodeWaitTimeout(account.login_email)

    def scan_recent_codes(
        self,
        account: EmailAccount,
        *,
        limit: int = 20,
        since: datetime | None = None,
        priority: bool = False,
    ) -> list[RecentCodeRecord]:
        """Return up to ``limit`` recent Perplexity login codes, newest first.

        The default import window is the preceding 24 hours.  Supplying
        ``since`` lets a caller make a narrower incremental scan; naive values
        are treated as UTC.  Every configured mail folder is searched and the
        returned records are de-duplicated by a stable message identity.

        This deliberately has no interaction with :meth:`wait_for_code`: the
        latter continues to use its own latest-message and polling behaviour
        for Telegram and legacy code requests.
        """
        if limit <= 0:
            return []

        previous_priority = bool(getattr(self._request_priority, "enabled", False))
        self._request_priority.enabled = priority

        now = datetime.now(timezone.utc)
        cutoff = since or now - timedelta(hours=24)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        else:
            cutoff = cutoff.astimezone(timezone.utc)

        token_manager = self._token_manager_for(account)
        imap: imaplib.IMAP4_SSL | None = None
        records: list[RecentCodeRecord] = []
        identities: set[str] = set()

        try:
            imap = self._connect(account.login_email, token_manager)
            for folder in self.folders:
                try:
                    records_in_folder = self._scan_recent_codes_in_folder(
                        imap,
                        folder=folder,
                        cutoff=cutoff,
                    )
                    # A missing or inaccessible junk folder must not prevent an
                    # otherwise usable inbox from supplying its codes.
                except imaplib.IMAP4.error as exc:
                    # Outlook commonly returns BAD/NO for a configured folder
                    # that is absent or named differently in this mailbox.
                    # That is a normal partial-scan condition, not a failed
                    # history import when another folder remains available.
                    LOGGER.info(
                        "Skipping unavailable mailbox folder %s (%s): %s",
                        folder,
                        account.login_email,
                        exc,
                    )
                    continue
                except Exception as exc:
                    LOGGER.warning(
                        "Recent login-code scan failed for mailbox folder %s (%s): %s",
                        folder,
                        account.login_email,
                        exc,
                    )
                    continue

                for record in records_in_folder:
                    if record.message_identity in identities:
                        continue
                    identities.add(record.message_identity)
                    records.append(record)
        except Exception as exc:
            # The caller is polling a display-only history.  A transient
            # token/IMAP failure must leave its already persisted history
            # usable and be retried on the next scheduled scan.
            LOGGER.warning(
                "Recent login-code scan failed for mailbox %s: %s",
                account.login_email,
                exc,
            )
            return []
        finally:
            self._safe_logout(imap)
            self._request_priority.enabled = previous_priority

        records.sort(
            key=lambda item: (item.timestamp, item.message_identity),
            reverse=True,
        )
        return records[:limit]

    def _scan_recent_codes_in_folder(
        self,
        imap: imaplib.IMAP4_SSL,
        *,
        folder: str,
        cutoff: datetime,
    ) -> list[RecentCodeRecord]:
        status, _ = self._imap_select(imap, folder)
        if status != "OK":
            return []

        # Outlook has returned incomplete results for both ``FROM`` and
        # ``SINCE`` searches in live mailboxes.  ``ALL`` is the only reliable
        # index here; select a bounded newest tail and validate sender/date
        # from the complete RFC822 message locally.
        status, data = self._imap_uid(imap, "search", "ALL")
        if status != "OK":
            return []
        uids = self._extract_uids(data)[-RECENT_SCAN_UID_LIMIT:]
        if not uids:
            return []
        raw_messages = self._fetch_raw_messages_from_selected_folder(imap, uids)
        if not raw_messages:
            return []
        return self._records_from_uids(
            folder=folder,
            cutoff=cutoff,
            uids=uids,
            raw_messages=raw_messages,
        )

    def _records_from_uids(
        self,
        *,
        folder: str,
        cutoff: datetime,
        uids: list[str],
        raw_messages: dict[str, bytes],
    ) -> list[RecentCodeRecord]:
        """Parse unseen UIDs from one selected folder, newest first."""
        records: list[RecentCodeRecord] = []
        for uid in reversed(uids):
            try:
                raw_message = raw_messages.get(uid)
                if raw_message is None:
                    continue

                message = email.message_from_bytes(raw_message)
                if not self._is_expected_sender(message):
                    continue

                # The history import accepts a code from any textual mail
                # part or from the subject.  Keep ``_extract_code`` untouched
                # because the existing wait path retains its parsing.
                code = self._extract_recent_code(message)
                if not code:
                    self._log_unparsed_perplexity_message(message, folder=folder, uid=uid)
                    continue

                timestamp = self._get_message_datetime(message)
                if timestamp is None or timestamp < cutoff:
                    # The message's Date header is supplied by the sender and
                    # can be malformed or delayed.  Outlook's Received
                    # header reflects the actual mailbox delivery time.
                    received_timestamp = self._get_received_datetime(message)
                    if received_timestamp is not None:
                        timestamp = received_timestamp
                if timestamp is None or timestamp < cutoff:
                    continue

                records.append(
                    RecentCodeRecord(
                        code=code,
                        timestamp=timestamp,
                        folder=folder,
                        message_identity=self._message_identity(message, raw_message),
                    )
                )
            except Exception as exc:
                # One malformed message must not hide all other login mails.
                LOGGER.debug(
                    "Skipping unreadable login-code message in %s (UID %s): %s",
                    folder,
                    uid,
                    exc,
                )
                continue
        return records

    @staticmethod
    def _extract_uids(data: object) -> list[str]:
        if not isinstance(data, (list, tuple)):
            return []

        uids: list[str] = []
        for item in data:
            if isinstance(item, bytes):
                chunks = item.split()
            elif isinstance(item, str):
                chunks = item.encode("ascii", errors="ignore").split()
            else:
                continue
            for chunk in chunks:
                try:
                    uids.append(chunk.decode("ascii"))
                except UnicodeDecodeError:
                    continue
        return uids

    @staticmethod
    def _message_identity(message: Message, raw_message: bytes) -> str:
        message_id = message.get("Message-ID")
        if message_id:
            # Header folding and whitespace are irrelevant to Message-ID
            # equality, while normalising them makes copied messages match.
            return f"message-id:{' '.join(message_id.split())}"
        return f"sha256:{hashlib.sha256(raw_message).hexdigest()}"

    def _is_expected_sender(self, message: Message) -> bool:
        """Defence in depth for IMAP servers with loose FROM search rules."""
        _, sender = parseaddr(str(message.get("From") or ""))
        normalized_sender = sender.strip().casefold()
        normalized_expected = self.search_from.strip().casefold()
        if not normalized_sender or not normalized_expected:
            return False
        if normalized_sender == normalized_expected:
            return True

        # Perplexity has used more than one sender mailbox over time.  Only
        # apply the relaxed rule for the provider's own domain; custom
        # deployments keep exact matching semantics.
        expected_domain = normalized_expected.rsplit("@", 1)[-1]
        sender_domain = normalized_sender.rsplit("@", 1)[-1]
        if not (
            expected_domain == "perplexity.ai"
            or expected_domain.endswith(".perplexity.ai")
        ):
            return False
        return sender_domain == "perplexity.ai" or sender_domain.endswith(
            ".perplexity.ai"
        )

    def _extract_recent_code(self, message: Message) -> str | None:
        text_parts: list[str] = [self._decode_header_value(message.get("Subject"))]

        if message.is_multipart():
            for part in message.walk():
                if part.get_content_disposition() == "attachment":
                    continue
                content_type = part.get_content_type()
                if not content_type.startswith("text/"):
                    continue
                content = self._decode_part(part)
                if content_type in {"text/html", "text/x-amp-html"}:
                    text_parts.append(HTML_TAG_REGEX.sub("", content))
                    content = HTML_TAG_REGEX.sub(" ", content)
                text_parts.append(content)
        else:
            content = self._decode_part(message)
            if message.get_content_type() in {"text/html", "text/x-amp-html"}:
                text_parts.append(HTML_TAG_REGEX.sub("", content))
                content = HTML_TAG_REGEX.sub(" ", content)
            text_parts.append(content)

        for content in text_parts:
            code = self._find_login_code(content)
            if code:
                return code
        return None

    @staticmethod
    def _decode_header_value(value: object) -> str:
        text = str(value or "")
        try:
            return str(make_header(decode_header(text)))
        except (TypeError, ValueError):
            return text

    @staticmethod
    def _find_login_code(content: str) -> str | None:
        decoded = html.unescape(content)
        direct_match = CODE_REGEX.search(decoded)
        if direct_match:
            return direct_match.group(0)

        spaced_match = SPACED_CODE_REGEX.search(decoded)
        if spaced_match:
            code = "".join(character for character in spaced_match.group(0) if character.isdigit())
            if len(code) == 6:
                return code
        return None

    def _log_unparsed_perplexity_message(
        self,
        message: Message,
        *,
        folder: str,
        uid: str,
    ) -> None:
        # This is actionable but deliberately excludes the mail body and any
        # code.  It makes future provider-template changes visible instead of
        # silently dropping every new message.
        message_id = str(message.get("Message-ID") or f"{folder}:{uid}").strip()
        with self._unparsed_message_keys_lock:
            if message_id in self._unparsed_message_keys:
                return
            if len(self._unparsed_message_keys) >= 1_000:
                self._unparsed_message_keys.clear()
            self._unparsed_message_keys.add(message_id)
        LOGGER.warning(
            "Perplexity login mail has no readable six-digit code "
            "(folder=%s, uid=%s, subject=%r)",
            folder,
            uid,
            self._decode_header_value(message.get("Subject"))[:160],
        )

    def _connect(
        self,
        email_address: str,
        token_manager: TokenManager,
    ) -> imaplib.IMAP4_SSL:
        access_token = token_manager.get_token()
        if not access_token:
            raise RuntimeError("Failed to acquire Outlook access token")

        auth_string = f"user={email_address}\1auth=Bearer {access_token}\1\1"
        imap = self._run_limited_imap_call(
            lambda: imaplib.IMAP4_SSL(self.imap_host),
            command_name="connect",
        )
        self._run_limited_imap_call(
            lambda: imap.authenticate("XOAUTH2", lambda _: auth_string.encode("utf-8")),
            command_name="authenticate",
        )
        return imap

    def _get_recent_code(
        self,
        imap: imaplib.IMAP4_SSL,
        started_at: datetime,
    ) -> CodeResult | None:
        threshold = started_at - timedelta(seconds=self.recent_window_seconds)
        for folder in self.folders:
            latest_uid = self._search_latest_uid(imap, folder)
            if latest_uid is None:
                continue

            message = self._fetch_message(imap, folder, latest_uid)
            if message is None:
                continue

            received_at = self._get_message_datetime(message)
            if received_at is None or received_at < threshold:
                continue

            code = self._extract_code(message)
            if code:
                return CodeResult(code=code, folder=folder)
        return None

    def _snapshot_latest_uids(self, imap: imaplib.IMAP4_SSL) -> dict[str, str | None]:
        snapshot: dict[str, str | None] = {}
        for folder in self.folders:
            snapshot[folder] = self._search_latest_uid(imap, folder)
        return snapshot

    def _poll_for_new_code(
        self,
        imap: imaplib.IMAP4_SSL,
        baseline_uids: dict[str, str | None],
    ) -> CodeResult | None:
        for folder in self.folders:
            latest_uid = self._search_latest_uid(imap, folder)
            if latest_uid is None:
                continue

            if baseline_uids.get(folder) == latest_uid:
                continue

            baseline_uids[folder] = latest_uid
            message = self._fetch_message(imap, folder, latest_uid)
            if message is None:
                continue

            code = self._extract_code(message)
            if code:
                return CodeResult(code=code, folder=folder)
        return None

    def _search_latest_uid(
        self,
        imap: imaplib.IMAP4_SSL,
        folder: str,
    ) -> str | None:
        status, _ = self._imap_select(imap, folder)
        if status != "OK":
            return None

        status, data = self._imap_uid(imap, "search", f'FROM "{self.search_from}"')
        if status != "OK" or not data or not data[0]:
            return None

        latest_uid = data[0].split()[-1]
        return latest_uid.decode("ascii")

    def _fetch_message(
        self,
        imap: imaplib.IMAP4_SSL,
        folder: str,
        uid: str,
    ) -> Message | None:
        status, _ = self._imap_select(imap, folder)
        if status != "OK":
            return None

        raw_message = self._fetch_raw_message_from_selected_folder(imap, uid)
        if raw_message is None:
            return None
        return email.message_from_bytes(raw_message)

    def _fetch_raw_message_from_selected_folder(
        self,
        imap: imaplib.IMAP4_SSL,
        uid: str,
    ) -> bytes | None:
        status, data = self._imap_uid(imap, "fetch", uid, "(RFC822)")
        if status != "OK" or not data:
            return None

        # RFC822 FETCH responses commonly contain a metadata tuple followed
        # by a trailing bytes marker.  Do not assume the tuple is item zero.
        for item in data:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            raw_message = item[1]
            if isinstance(raw_message, bytes):
                return raw_message
        return None

    def _fetch_raw_messages_from_selected_folder(
        self,
        imap: imaplib.IMAP4_SSL,
        uids: list[str],
    ) -> dict[str, bytes]:
        """Fetch a mailbox tail in one IMAP command, keyed by stable UID."""
        if not uids:
            return {}

        status, data = self._imap_uid(
            imap,
            "fetch",
            ",".join(uids),
            "(UID RFC822)",
        )
        if status != "OK" or not data:
            return {}

        found: dict[str, bytes] = {}
        unresolved: list[bytes] = []
        for item in data:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            metadata, raw_message = item[0], item[1]
            if not isinstance(raw_message, bytes):
                continue
            metadata_text = (
                metadata.decode("utf-8", errors="ignore")
                if isinstance(metadata, bytes)
                else str(metadata)
            )
            uid_match = UID_IN_FETCH_RESPONSE_REGEX.search(metadata_text)
            if uid_match:
                found[uid_match.group(1)] = raw_message
            else:
                unresolved.append(raw_message)

        # Standard UID FETCH responses include UID in their metadata.  The
        # narrow fallback keeps compatibility with atypical servers and test
        # doubles without letting a missing metadata item drop the whole scan.
        if len(unresolved) == 1 and len(uids) == 1:
            found[uids[0]] = unresolved[0]
        elif unresolved:
            for uid in uids:
                if uid not in found:
                    raw_message = self._fetch_raw_message_from_selected_folder(imap, uid)
                    if raw_message is not None:
                        found[uid] = raw_message
        return found

    def _extract_code(self, message: Message) -> str | None:
        body_parts: list[str] = []
        html_parts: list[str] = []

        if message.is_multipart():
            for part in message.walk():
                content_disposition = part.get_content_disposition()
                if content_disposition == "attachment":
                    continue

                content_type = part.get_content_type()
                if content_type == "text/plain":
                    body_parts.append(self._decode_part(part))
                elif content_type == "text/html":
                    html_parts.append(self._decode_part(part))
        else:
            content_type = message.get_content_type()
            if content_type == "text/html":
                html_parts.append(self._decode_part(message))
            else:
                body_parts.append(self._decode_part(message))

        content = "\n".join(body_parts).strip()
        if not content and html_parts:
            html_content = "\n".join(html_parts)
            content = HTML_TAG_REGEX.sub(" ", html_content)

        match = CODE_REGEX.search(content)
        if match:
            return match.group(0)
        return None

    def _decode_part(self, part: Message) -> str:
        payload = part.get_payload(decode=True)
        if payload is None:
            payload_text = part.get_payload()
            return payload_text if isinstance(payload_text, str) else ""

        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="ignore")  # type: ignore
        except LookupError:
            return payload.decode("utf-8", errors="ignore")

    def _get_message_datetime(self, message: Message) -> datetime | None:
        raw_date = message.get("Date")
        if not raw_date:
            return None
        try:
            parsed = parsedate_to_datetime(raw_date)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _get_received_datetime(message: Message) -> datetime | None:
        for raw_received in message.get_all("Received", []):
            if not isinstance(raw_received, str) or ";" not in raw_received:
                continue
            try:
                parsed = parsedate_to_datetime(raw_received.rsplit(";", 1)[-1].strip())
            except (TypeError, ValueError, IndexError, OverflowError):
                continue
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        return None

    def _token_manager_for(self, account: EmailAccount) -> TokenManager:
        key = (account.login_email, account.client_id, account.refresh_token)
        with self._token_managers_lock:
            manager = self._token_managers.get(key)
            if manager is None:
                manager = TokenManager(account.client_id, account.refresh_token)
                self._token_managers[key] = manager
            return manager

    def _safe_logout(self, imap: imaplib.IMAP4_SSL | None) -> None:
        if imap is None:
            return
        try:
            self._run_limited_imap_call(imap.logout, command_name="logout")
        except Exception:
            pass

    def _imap_select(
        self,
        imap: imaplib.IMAP4_SSL,
        folder: str,
    ):
        return self._run_limited_imap_call(
            lambda: imap.select(folder),
            command_name="select",
        )

    def _imap_uid(
        self,
        imap: imaplib.IMAP4_SSL,
        command: str,
        *args: str,
    ):
        return self._run_limited_imap_call(
            lambda: imap.uid(command, *args),
            command_name=f"uid_{command.lower()}",
        )

    def _run_limited_imap_call(self, operation, *, command_name: str):
        self.limiter.wait_for_slot(
            priority=bool(getattr(self._request_priority, "enabled", False))
        )
        try:
            result = operation()
        except Exception as exc:
            if self._should_apply_global_backoff(exc, command_name):
                self.limiter.record_failure()
            raise
        else:
            if self._should_apply_global_backoff_from_response(result):
                self.limiter.record_failure()
            else:
                self.limiter.record_success()
            return result

    def _should_apply_global_backoff_from_response(self, result: object) -> bool:
        if not isinstance(result, tuple) or not result:
            return False

        status = result[0]
        if not isinstance(status, str) or status == "OK":
            return False

        response_chunks: list[str] = []
        if len(result) > 1 and isinstance(result[1], list):
            for item in result[1]:
                if isinstance(item, bytes):
                    response_chunks.append(item.decode("utf-8", errors="ignore"))
                elif isinstance(item, str):
                    response_chunks.append(item)

        lowered_message = " ".join(response_chunks).lower()
        transient_markers = (
            "rate",
            "limit",
            "throttl",
            "tempor",
            "timeout",
            "try again",
            "too many",
            "unavailable",
            "server error",
            "connection closed",
        )
        return any(marker in lowered_message for marker in transient_markers)

    def _should_apply_global_backoff(
        self,
        error: Exception,
        command_name: str,
    ) -> bool:
        if isinstance(error, (imaplib.IMAP4.abort, TimeoutError, OSError)):
            return True

        if not isinstance(error, imaplib.IMAP4.error):
            return False

        lowered_message = str(error).lower()
        transient_markers = (
            "rate",
            "limit",
            "throttl",
            "tempor",
            "timeout",
            "try again",
            "too many",
            "unavailable",
            "server error",
            "connection closed",
        )
        if any(marker in lowered_message for marker in transient_markers):
            return True

        return command_name in {"connect", "authenticate"} and "auth" not in lowered_message
