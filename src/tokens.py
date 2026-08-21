"""Secondary API key activation and administration service."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import secrets
import string
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, quote, urlencode

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from src.time_utils import MOSCOW_TZ, to_moscow, to_utc

TOKENS_ACCESS_COOKIE = "tokens_access_key"
TOKENS_ADMIN_COOKIE = "tokens_admin_session"
TOKENS_USER_CSRF_COOKIE = "tokens_user_csrf"
TOKENS_ADMIN_CSRF_COOKIE = "tokens_admin_csrf"
TOKENS_RESELLER_COOKIE = "tokens_reseller_session"
TOKENS_RESELLER_CSRF_COOKIE = "tokens_reseller_csrf"
TOKEN_CODE_ALPHABET = string.ascii_uppercase + string.digits
PROMO_CODE_ALPHABET = string.ascii_uppercase + string.digits
logger = logging.getLogger(__name__)
TOKEN_ADMIN_PAGE_SIZE = 100
# The upstream allows one balance request per second for a given API key.
# Allowing a small margin prevents an immediate page render + browser refresh
# from repeatedly tripping that limit.
BALANCE_RATE_LIMIT_BACKOFF_SECONDS = (1.05,)
TOKEN_ADMIN_SORT_KEYS = frozenset({
    "id", "created_at", "access_code", "api_key", "token_limit", "used_tokens", "status",
})
SERVICE_OPTIONS = (
    "Claude", "OpenAI", "Google", "Grok", "DeepSeek", "Alibaba Cloud",
    "Z.AI (GLM)", "KIMI", "Xiaomi", "NVIDIA", "Реселлинг",
)
RESELLING_SERVICE = "Реселлинг"
RESELLER_SERVICE_OPTIONS = tuple(service for service in SERVICE_OPTIONS if service != RESELLING_SERVICE)

TOKEN_TEXT = {
    "ru": {
        "switch": "English", "title": "Сервис активации", "intro": "Здесь вы сможете активировать и использовать API ключи с токенами для сервисов Claude / Codex / Grok / Google и другие.<br>Для этого следуйте инструкции ниже.",
        "activation": "Активация ключа", "access": "Ключ доступа", "access_hint": "➥ Здесь вводите ключ доступа который получили от продавца.", "activate": "АКТИВИРОВАТЬ КЛЮЧ",
        "info": "ИНФОРМАЦИЯ", "service": "Подключенный сервис:", "activated": "Дата активации ключа:", "limit": "Количество токенов:", "remaining": "Оставшиеся токены:", "status": "Статус:", "api": "API ключ:", "bonus": "Получить бонус", "bonus_instructions": "Чтобы получить бонусные токены, оставьте положительный отзыв на странице оплаты у продавца. После этого продавец отправит вам промокод в чат. Введите его в поле ниже и нажмите «Получить».", "promo_code": "Промокод", "claim_bonus": "Получить", "promo_missing": "🔴 Промокод не существует или уже был использован ранее.", "promo_success": "✅ Промокод #{code} был активирован и вам начислено {tokens} токенов.", "promo_error": "Не удалось начислить бонус. Попробуйте ещё раз.", "download_logs": "Скачать логи", "logs_downloading": "Скачивание…", "logs_error": "Не удалось скачать логи. Попробуйте ещё раз.",
        "exhausted": "Токены были полностью использованы. Дата: {date}", "not_activated": "Не активирован", "activated_status": "Активирован ({date})", "exhausted_status": "Исчерпан ({date})",
        "instructions": "ИНСТРУКЦИЯ ПО ИСПОЛЬЗОВАНИЮ", "instructions_intro": "Если вы не знаете как использовать API ключ, мы поможем, для начала выберите через что вы будете использовать API",
        "choose_service": "1. Выберите сервис", "choose_app": "2. Выберите приложение", "choose_os": "3. Выберите операционную систему", "selected": "Выбрано:", "description": "Описание:", "os": "Операционная система:",
        "manual": "Ручная настройка с готовым скриптом", "shell_windows": "PowerShell", "shell_other": "Терминал", "open": "Откройте {shell} на машине, где запускается выбранное приложение.", "run": "Скопируйте и выполните скрипт ниже. API ключ уже подставлен автоматически.", "restart": "Перезапустите приложение после завершения настройки.", "warning": "⚠️ Копируйте скрипт полностью или воспользуйтесь кнопкой «Скопировать».", "copy": "Скопировать", "copied": "Скопировано", "remove_hint": "Для удаления настройки повторите инструкцию из документации сервиса или удалите добавленные строки из конфигурации.",
        "remaining_sep": "из", "remove_script": "Скрипт удаления настройки",
        "script": "Скрипт", "manual_mode": "Вручную", "manual_heading": "Ручная настройка", "remove_integration": "Удалить интеграцию", "remove_integration_hint": "Удаляется только интеграция для выбранного приложения.",
        "grok_open_windows": "Открой PowerShell.", "grok_open_other": "Открой терминал.", "grok_run": "Выполни команду ниже.", "grok_restart": "Перезапусти терминал и введи grok.",
        "required": "Введите ключ доступа.", "missing": "Ключ доступа не существует.", "success": "Ключ успешно активирован. API-ключ готов к использованию.",
        "balance_unavailable": "Не удалось обновить баланс токенов.", "active": "Активен", "frozen": "Заморожен", "freeze": "Заморозить ключ", "unfreeze": "Разморозить ключ", "freeze_success": "Ключ успешно заморожен.", "unfreeze_success": "Ключ успешно разморожен.", "freeze_error": "Не удалось изменить статус ключа. Попробуйте ещё раз.",
    },
    "en": {
        "switch": "Русский", "title": "Activation Service", "intro": "Activate and use token-based API keys for Claude / Codex / Grok / Google and other services.<br>Follow the instructions below.",
        "activation": "Key activation", "access": "Access key", "access_hint": "➥ Enter the access key received from the seller.", "activate": "ACTIVATE KEY",
        "info": "INFORMATION", "service": "Connected service:", "activated": "Key activation date:", "limit": "Token amount:", "remaining": "Remaining tokens:", "status": "Status:", "api": "API key:", "bonus": "Get bonus", "bonus_instructions": "To receive bonus tokens, leave a positive review on the seller's payment page. The seller will then send you a promo code in the chat. Enter it below and click “Get”.", "promo_code": "Promo code", "claim_bonus": "Get", "promo_missing": "🔴 The promo code does not exist or has already been used.", "promo_success": "✅ Promo code #{code} was activated and {tokens} tokens were credited to your account.", "promo_error": "Could not credit the bonus. Please try again.", "download_logs": "Download logs", "logs_downloading": "Downloading…", "logs_error": "Could not download logs. Please try again.",
        "exhausted": "All tokens have been used. Date: {date}", "not_activated": "Not activated", "activated_status": "Activated ({date})", "exhausted_status": "Exhausted ({date})",
        "instructions": "INSTRUCTIONS FOR USE", "instructions_intro": "If you do not know how to use the API key, we can help. First choose how you will use the API.",
        "choose_service": "1. Choose a service", "choose_app": "2. Choose an application", "choose_os": "3. Choose an operating system", "selected": "Selected:", "description": "Description:", "os": "Operating system:",
        "manual": "Setup", "shell_windows": "PowerShell", "shell_other": "Terminal", "open": "Open {shell} on the machine where the selected application runs.", "run": "Copy and run the script below. The API key is already inserted automatically.", "restart": "Restart the application after setup is complete.", "warning": "⚠️ Copy the complete script or use the «Copy» button.", "copy": "Copy", "copied": "Copied", "remove_hint": "To remove the setup, follow the service documentation or remove the added configuration lines.",
        "remaining_sep": "of", "remove_script": "Uninstall script",
        "script": "Script", "manual_mode": "Manually", "manual_heading": "Manual setup", "remove_integration": "Remove integration", "remove_integration_hint": "This removes only this integration for the selected application.",
        "grok_open_windows": "Open PowerShell.", "grok_open_other": "Open the terminal.", "grok_run": "Run the command below.", "grok_restart": "Restart the terminal and type grok.",
        "required": "Enter an access key.", "missing": "The access key does not exist.", "success": "The key was activated successfully. The API key is ready to use.",
        "balance_unavailable": "Could not refresh the token balance.", "active": "Active", "frozen": "Frozen", "freeze": "Freeze key", "unfreeze": "Unfreeze key", "freeze_success": "The key was frozen successfully.", "unfreeze_success": "The key was unfrozen successfully.", "freeze_error": "Could not change the key status. Please try again.",
    },
}


def token_locale(value: str | None) -> str:
    return "en" if value == "en" else "ru"


def reseller_locale(value: str | None) -> str:
    """The reseller portal is English by default, unlike the public token page."""
    return "ru" if value == "ru" else "en"


RESELLER_TEXT = {
    "ru": {
        "title": "Кабинет реселлера", "eyebrow": "УПРАВЛЕНИЕ ТОКЕНАМИ",
        "login_intro": "Введите ключ доступа к кабинету реселлера.", "access_key": "Ключ доступа к кабинету",
        "login": "Войти", "logout": "Выйти", "language": "Язык", "russian": "Русский", "english": "English",
        "dashboard_intro": "Создавайте и управляйте клиентскими API-ключами в рамках баланса токенов.",
        "portal_key": "Ключ доступа к сайту", "portal_key_hint": "Этот ключ используется только для входа в кабинет, это не API-ключ.",
        "regenerate": "Перегенерировать", "regenerate_confirm": "Перегенерировать ключ доступа? Все старые сессии будут завершены.",
        "regenerate_notice": "Ключ доступа перегенерирован. Все старые сессии завершены.",
        "balance": "Баланс токенов", "total": "Общий баланс токенов", "spent": "Израсходовано токенов",
        "available": "Доступно для выдачи", "children_count": "Клиентских ключей", "balance_hint": "Выданные лимиты списываются из баланса и не возвращаются после создания или пополнения ключа.",
        "create_title": "Создать клиентский API-ключ", "create_hint": "Выберите сервис и лимит токенов для нового клиентского ключа.",
        "service": "Сервис", "name": "Название", "token_limit": "Лимит токенов", "create": "Создать ключ",
        "children_title": "Клиентские API-ключи", "children_hint": "Доступны создание, пополнение и заморозка. Уменьшение лимита и удаление не отображаются, пока upstream не поддерживает эти операции безопасно.",
        "id": "ID", "api_key": "API key", "access": "Ключ доступа", "limit": "Лимит токенов", "used": "Израсходовано", "remaining": "Остаток", "status": "Статус", "actions": "Действия",
        "active": "Активен", "frozen": "Заморожен", "freeze": "Заморозить", "unfreeze": "Разморозить", "top_up_placeholder": "Токенов", "top_up": "Пополнить",
        "empty": "Клиентских ключей пока нет.", "pending": "Есть неподтверждённые операции: {count}. Зарезервированный лимит сохранён, чтобы исключить двойное списание.",
        "session_expired": "Сессия реселлера завершена.", "invalid_key": "Реселлерский ключ не существует или заморожен.", "not_found": "Производный ключ не найден.",
        "ordinary_service": "Выберите обычный доступный сервис.", "name_limit": "Введите название и положительное количество токенов.", "service_unavailable": "Сервис создания ключей временно недоступен.",
        "insufficient": "Недостаточно доступного лимита реселлера.", "created": "Производный ключ создан.", "topup_unavailable": "Сервис пополнения временно недоступен.",
        "topup_invalid": "Введите положительное количество токенов.", "topup_rejected": "Внешний сервис отклонил пополнение.", "topup_unknown": "Пополнение не подтверждено. Лимит зарезервирован; повтор отключён во избежание двойного начисления.",
        "topup_success": "Лимит производного ключа увеличен.", "state_unavailable": "Сервис изменения статуса временно недоступен.", "state_error": "Не удалось изменить статус производного ключа.",
        "frozen_notice": "Производный ключ заморожен.", "unfrozen_notice": "Производный ключ разморожен.", "logout_notice": "Вы вышли из кабинета реселлера.",
        "access_code_label": "Ключ доступа", "copy_hint": "Нажмите, чтобы скопировать API key",
    },
    "en": {
        "title": "Reseller dashboard", "eyebrow": "TOKEN MANAGEMENT",
        "login_intro": "Enter the access key for the reseller dashboard.", "access_key": "Dashboard access key",
        "login": "Sign in", "logout": "Sign out", "language": "Language", "russian": "Русский", "english": "English",
        "dashboard_intro": "Create and manage customer API keys within your token balance.",
        "portal_key": "Website access key", "portal_key_hint": "This key is only used to sign in to the dashboard. It is not an API key.",
        "regenerate": "Regenerate", "regenerate_confirm": "Regenerate the access key? All old sessions will be signed out.",
        "regenerate_notice": "The access key was regenerated. All old sessions were signed out.",
        "balance": "Token balance", "total": "Total token balance", "spent": "Tokens allocated",
        "available": "Available to issue", "children_count": "Customer keys", "balance_hint": "Issued limits are deducted from the balance and are not returned after a key is created or topped up.",
        "create_title": "Create customer API key", "create_hint": "Choose a service and token limit for the new customer key.",
        "service": "Service", "name": "Name", "token_limit": "Token limit", "create": "Create key",
        "children_title": "Customer API keys", "children_hint": "Creation, top-ups and freezing are available. Limit reduction and deletion are not shown until the upstream supports these operations safely.",
        "id": "ID", "api_key": "API key", "access": "Access key", "limit": "Token limit", "used": "Allocated", "remaining": "Remaining", "status": "Status", "actions": "Actions",
        "active": "Active", "frozen": "Frozen", "freeze": "Freeze", "unfreeze": "Unfreeze", "top_up_placeholder": "Tokens", "top_up": "Top up",
        "empty": "No customer keys yet.", "pending": "There are {count} unconfirmed operations. The reserved limit is retained to prevent double charging.",
        "session_expired": "The reseller session has expired.", "invalid_key": "The reseller key does not exist or is frozen.", "not_found": "Customer key not found.",
        "ordinary_service": "Choose a supported regular service.", "name_limit": "Enter a name and a positive token amount.", "service_unavailable": "Key creation is temporarily unavailable.",
        "insufficient": "The reseller has insufficient available token balance.", "created": "Customer key created.", "topup_unavailable": "Top-up is temporarily unavailable.",
        "topup_invalid": "Enter a positive token amount.", "topup_rejected": "The upstream service rejected the top-up.", "topup_unknown": "The top-up was not confirmed. The limit is reserved; retrying is disabled to prevent double crediting.",
        "topup_success": "The customer key limit was increased.", "state_unavailable": "Status changes are temporarily unavailable.", "state_error": "Could not change the customer key status.",
        "frozen_notice": "Customer key frozen.", "unfrozen_notice": "Customer key unfrozen.", "logout_notice": "You have signed out of the reseller dashboard.",
        "access_code_label": "Access key", "copy_hint": "Click to copy API key",
    },
}


def reseller_message(locale: str, key: str, **values: object) -> str:
    return RESELLER_TEXT[reseller_locale(locale)][key].format(**values)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: object) -> datetime:
    """Parse values read from the UTC JSON store, including legacy naive values."""
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def parse_admin_datetime(value: object) -> datetime:
    """Parse a datetime-local value entered in the Moscow-time admin UI."""
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return to_utc(parsed.replace(tzinfo=MOSCOW_TZ) if parsed.tzinfo is None else parsed)


def normalize_access_code(value: str) -> str:
    return "".join(value.strip().upper().split())


def normalize_promo_code(value: str) -> str:
    return "".join(value.strip().upper().split())


def generate_access_code(existing: set[str]) -> str:
    while True:
        code = "".join(secrets.choice(TOKEN_CODE_ALPHABET) for _ in range(20))
        if code not in existing:
            return code


def generate_promo_code(existing: set[str]) -> str:
    """Generate a globally unique, seller-friendly promo code."""
    while True:
        code = "".join(secrets.choice(PROMO_CODE_ALPHABET) for _ in range(20))
        if code not in existing:
            return code


def format_tokens(value: int) -> str:
    return f"{max(0, value):,}".replace(",", " ")


def format_datetime(value: datetime | None) -> str:
    return to_moscow(value).strftime("%d.%m.%Y %H:%M") if value else "—"


@dataclass(frozen=True, slots=True)
class TokenAdminPageState:
    """Server-side state for the keys table.

    Sorting and filtering deliberately happen before slicing the result into
    pages.  Keeping this state in the URL also makes every page bookmarkable
    and avoids client-side sorting of just the currently rendered 100 rows.
    """

    page: int = 1
    search_query: str = ""
    sort_key: str = "id"
    sort_order: str = "desc"


def normalize_token_admin_page(value: str | None) -> int:
    try:
        return max(1, int((value or "").strip()))
    except (TypeError, ValueError):
        return 1


def normalize_token_admin_sort_key(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    return candidate if candidate in TOKEN_ADMIN_SORT_KEYS else "id"


def normalize_token_admin_sort_order(value: str | None) -> str:
    return "asc" if (value or "").strip().lower() == "asc" else "desc"


def token_admin_page_state(query: object) -> TokenAdminPageState:
    """Read only the supported table controls from FastAPI query parameters."""
    get = getattr(query, "get")
    return TokenAdminPageState(
        page=normalize_token_admin_page(get("page")),
        search_query=str(get("search") or "").strip(),
        sort_key=normalize_token_admin_sort_key(get("sort")),
        sort_order=normalize_token_admin_sort_order(get("order")),
    )


def filter_token_admin_keys(keys: list[TokenKey], search_query: str) -> list[TokenKey]:
    """Find an access code or an upstream API key, case-insensitively."""
    needle = search_query.strip().lower()
    if not needle:
        return list(keys)
    return [
        key for key in keys
        if needle in key.access_code.lower() or needle in key.api_key.lower()
    ]


def token_admin_status_sort_value(key: TokenKey) -> tuple[int, datetime, int]:
    """Stable non-localized ordering for the human-readable status column."""
    if not key.active:
        return (-1, key.created_at, key.id)
    if key.is_exhausted:
        return (2, key.exhausted_at or key.activated_at or key.created_at, key.id)
    if key.activated_at:
        return (1, key.activated_at, key.id)
    return (0, key.created_at, key.id)


def token_admin_sort_value(key: TokenKey, sort_key: str):
    if sort_key == "created_at":
        return (key.created_at, key.id)
    if sort_key == "access_code":
        return (key.access_code, key.id)
    if sort_key == "api_key":
        return (key.api_key, key.id)
    if sort_key == "token_limit":
        return (key.token_limit, key.id)
    if sort_key == "used_tokens":
        return (key.used_tokens, key.id)
    if sort_key == "status":
        return token_admin_status_sort_value(key)
    return key.id


def sort_token_admin_keys(keys: list[TokenKey], state: TokenAdminPageState) -> list[TokenKey]:
    """Return the full filtered set ordered before pagination is applied."""
    return sorted(
        keys,
        key=lambda key: token_admin_sort_value(key, state.sort_key),
        reverse=state.sort_order == "desc",
    )


def paginate_token_admin_keys(
    keys: list[TokenKey], page: int, page_size: int = TOKEN_ADMIN_PAGE_SIZE,
) -> tuple[list[TokenKey], int, int]:
    """Return ``(items, current_page, total_pages)`` for an already sorted list."""
    if page_size < 1:
        raise ValueError("page_size must be positive")
    total_pages = max(1, (len(keys) + page_size - 1) // page_size)
    current_page = min(max(1, page), total_pages)
    start = (current_page - 1) * page_size
    return keys[start:start + page_size], current_page, total_pages


@dataclass(frozen=True, slots=True)
class TokenKey:
    id: int
    created_at: datetime
    access_code: str
    api_key: str
    service: str
    name: str
    token_limit: int
    used_tokens: int = 0
    activated_at: datetime | None = None
    exhausted_at: datetime | None = None
    # This mirrors the upstream ``active`` flag.  Older store files did not
    # have it, so their keys remain active after migration.
    active: bool = True
    # A child key stays in the standard owner store, but belongs to exactly
    # one local reseller. Existing records omit this field and remain direct
    # administrator keys.
    reseller_id: int | None = None

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.token_limit - self.used_tokens)

    @property
    def is_exhausted(self) -> bool:
        return self.remaining_tokens == 0

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "TokenKey":
        activated = data.get("activated_at")
        exhausted = data.get("exhausted_at")
        return cls(
            id=int(data["id"]),
            created_at=parse_datetime(data["created_at"]),
            access_code=normalize_access_code(str(data["access_code"])),
            api_key=str(data["api_key"]),
            service=str(data["service"]),
            name=str(data.get("name") or ""),
            token_limit=max(0, int(data["token_limit"])),
            used_tokens=max(0, int(data.get("used_tokens") or 0)),
            activated_at=parse_datetime(activated) if activated else None,
            exhausted_at=parse_datetime(exhausted) if exhausted else None,
            active=bool(data.get("active", True)),
            reseller_id=(int(data["reseller_id"]) if data.get("reseller_id") is not None else None),
        )

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["created_at"] = self.created_at.astimezone(timezone.utc).isoformat()
        result["activated_at"] = self.activated_at.astimezone(timezone.utc).isoformat() if self.activated_at else None
        result["exhausted_at"] = self.exhausted_at.astimezone(timezone.utc).isoformat() if self.exhausted_at else None
        return result


@dataclass(frozen=True, slots=True)
class ResellerKey:
    """A local master key that may allocate a fixed irreversible budget."""

    id: int
    created_at: datetime
    access_code: str
    name: str
    token_limit: int
    issued_tokens: int = 0
    active: bool = True

    @property
    def available_tokens(self) -> int:
        return max(0, self.token_limit - self.issued_tokens)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ResellerKey":
        limit = max(0, int(data["token_limit"]))
        issued = min(limit, max(0, int(data.get("issued_tokens") or 0)))
        return cls(
            id=int(data["id"]),
            created_at=parse_datetime(data["created_at"]),
            access_code=normalize_access_code(str(data["access_code"])),
            name=str(data.get("name") or ""),
            token_limit=limit,
            issued_tokens=issued,
            active=bool(data.get("active", True)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
            "access_code": self.access_code,
            "name": self.name,
            "token_limit": self.token_limit,
            "issued_tokens": self.issued_tokens,
            "active": self.active,
        }


class ResellerKeyStore:
    """One atomic local reseller ledger per token-admin owner.

    ``issued_tokens`` never decreases: creation and an upstream-confirmed
    top-up consume budget permanently, including after a child is frozen or
    later archived. The lock makes the check-and-reserve operation atomic.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    def _read(self) -> list[ResellerKey]:
        if not self.path.exists():
            return []
        raw = self.path.read_text(encoding="utf-8").strip()
        if not raw:
            return []
        try:
            payload = json.loads(raw)
            if not isinstance(payload, list):
                raise ValueError("root is not a list")
            return [ResellerKey.from_dict(item) for item in payload if isinstance(item, dict)]
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Reseller key store is unreadable: {exc}") from exc

    def _write(self, keys: list[ResellerKey]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps([key.to_dict() for key in keys], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

    def ensure_exists(self) -> None:
        if not self.path.exists():
            self._write([])

    async def list(self) -> list[ResellerKey]:
        async with self._lock:
            return sorted(self._read(), key=lambda key: key.id, reverse=True)

    async def get(self, key_id: int) -> ResellerKey | None:
        async with self._lock:
            return next((key for key in self._read() if key.id == key_id), None)

    async def get_by_code(self, code: str) -> ResellerKey | None:
        normalized = normalize_access_code(code)
        async with self._lock:
            return next((key for key in self._read() if key.access_code == normalized), None)

    async def add_many(self, records: list[ResellerKey]) -> None:
        async with self._lock:
            keys = self._read()
            keys.extend(records)
            self._write(keys)

    async def update(self, key_id: int, updated: ResellerKey) -> bool:
        async with self._lock:
            keys = self._read()
            for index, key in enumerate(keys):
                if key.id == key_id:
                    keys[index] = updated
                    self._write(keys)
                    return True
        return False

    async def reserve(self, key_id: int, amount: int) -> ResellerKey | None:
        """Permanently allocate ``amount`` only when the whole budget fits."""
        if amount < 1:
            return None
        async with self._lock:
            keys = self._read()
            for index, key in enumerate(keys):
                if key.id != key_id or not key.active or key.available_tokens < amount:
                    return None
                updated = replace(key, issued_tokens=key.issued_tokens + amount)
                keys[index] = updated
                self._write(keys)
                return updated
        return None

    async def release_rejected_reservation(self, key_id: int, amount: int) -> ResellerKey | None:
        """Undo only a request that upstream explicitly rejected.

        This is deliberately not used after a timeout or broken connection:
        the provider may have accepted that request, so returning its budget
        could allow a second allocation to overspend the primary account.
        """
        if amount < 1:
            return None
        async with self._lock:
            keys = self._read()
            for index, key in enumerate(keys):
                if key.id != key_id or key.issued_tokens < amount:
                    return None
                updated = replace(key, issued_tokens=key.issued_tokens - amount)
                keys[index] = updated
                self._write(keys)
                return updated
        return None


@dataclass(frozen=True, slots=True)
class ResellerOperation:
    """Durable audit trail for a master-budget-affecting upstream request."""

    id: str
    created_at: datetime
    reseller_id: int
    action: str
    amount: int
    state: str
    child_key_id: int | None = None
    detail: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ResellerOperation":
        return cls(
            id=str(data["id"]),
            created_at=parse_datetime(data["created_at"]),
            reseller_id=int(data["reseller_id"]),
            action=str(data["action"]),
            amount=max(0, int(data.get("amount") or 0)),
            state=str(data["state"]),
            child_key_id=int(data["child_key_id"]) if data.get("child_key_id") is not None else None,
            detail=str(data.get("detail") or "")[:500],
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
            "reseller_id": self.reseller_id,
            "action": self.action,
            "amount": self.amount,
            "state": self.state,
            "child_key_id": self.child_key_id,
            "detail": self.detail,
        }


class ResellerOperationStore:
    """Append-only-ish persistent ledger of allocation attempts.

    The state transitions ``pending -> confirmed/rejected/unknown`` are
    persisted before an HTTP response is returned. A request that ends in an
    unknown state intentionally retains its budget reservation.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    def _read(self) -> list[ResellerOperation]:
        if not self.path.exists():
            return []
        raw = self.path.read_text(encoding="utf-8").strip()
        if not raw:
            return []
        try:
            payload = json.loads(raw)
            if not isinstance(payload, list):
                raise ValueError("root is not a list")
            return [ResellerOperation.from_dict(item) for item in payload if isinstance(item, dict)]
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Reseller operation store is unreadable: {exc}") from exc

    def _write(self, operations: list[ResellerOperation]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps([operation.to_dict() for operation in operations], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def ensure_exists(self) -> None:
        if not self.path.exists():
            self._write([])

    async def add(self, operation: ResellerOperation) -> None:
        async with self._lock:
            operations = self._read()
            operations.append(operation)
            self._write(operations)

    async def update(self, operation_id: str, **changes: object) -> ResellerOperation | None:
        async with self._lock:
            operations = self._read()
            for index, operation in enumerate(operations):
                if operation.id != operation_id:
                    continue
                updated = replace(operation, **changes)
                operations[index] = updated
                self._write(operations)
                return updated
        return None

    async def list_for_reseller(self, reseller_id: int) -> list[ResellerOperation]:
        async with self._lock:
            return [operation for operation in self._read() if operation.reseller_id == reseller_id]

class TokenKeyStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    def _read(self) -> list[TokenKey]:
        if not self.path.exists():
            return []
        raw = self.path.read_text(encoding="utf-8").strip()
        if not raw:
            return []
        try:
            payload = json.loads(raw)
            if not isinstance(payload, list):
                raise ValueError("root is not a list")
            return [TokenKey.from_dict(item) for item in payload if isinstance(item, dict)]
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Token key store is unreadable: {exc}") from exc

    def _write(self, keys: list[TokenKey]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps([key.to_dict() for key in keys], ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def ensure_exists(self) -> None:
        """Create an empty store without modifying an existing one."""
        if not self.path.exists():
            self._write([])

    async def list(self) -> list[TokenKey]:
        async with self._lock:
            return sorted(self._read(), key=lambda key: key.id, reverse=True)

    async def get(self, key_id: int) -> TokenKey | None:
        async with self._lock:
            return next((key for key in self._read() if key.id == key_id), None)

    async def get_by_code(self, code: str) -> TokenKey | None:
        normalized = normalize_access_code(code)
        async with self._lock:
            return next((key for key in self._read() if key.access_code == normalized), None)

    async def add_many(self, records: list[TokenKey]) -> None:
        async with self._lock:
            keys = self._read()
            keys.extend(records)
            self._write(keys)

    async def activate(self, code: str) -> TokenKey | None:
        normalized = normalize_access_code(code)
        async with self._lock:
            keys = self._read()
            for index, key in enumerate(keys):
                if key.access_code != normalized:
                    continue
                if key.activated_at is None:
                    key = replace(key, activated_at=utc_now())
                    keys[index] = key
                    self._write(keys)
                return key
        return None

    async def update(self, key_id: int, updated: TokenKey) -> bool:
        async with self._lock:
            keys = self._read()
            for index, key in enumerate(keys):
                if key.id == key_id:
                    keys[index] = updated
                    self._write(keys)
                    return True
        return False

    async def delete(self, key_id: int) -> bool:
        async with self._lock:
            keys = self._read()
            kept = [key for key in keys if key.id != key_id]
            if len(kept) == len(keys):
                return False
            self._write(kept)
            return True

    async def apply_remaining(self, key_id: int, remaining: int) -> TokenKey | None:
        async with self._lock:
            keys = self._read()
            for index, key in enumerate(keys):
                if key.id != key_id:
                    continue
                used_tokens = used_tokens_from_remaining(key.token_limit, remaining)
                exhausted_at = key.exhausted_at or utc_now() if used_tokens >= key.token_limit else None
                updated = replace(key, used_tokens=used_tokens, exhausted_at=exhausted_at)
                if updated != key:
                    keys[index] = updated
                    self._write(keys)
                return updated
        return None


@dataclass(frozen=True, slots=True)
class PromoCode:
    code: str
    additional_tokens: int
    used_at: datetime | None = None
    used_access_code: str | None = None
    owner_index: int = 1
    # Kept last to preserve the positional constructor used by existing
    # callers: PromoCode(code, additional_tokens, used_at, ...).
    created_at: datetime = field(default_factory=utc_now)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "PromoCode":
        used_at = data.get("used_at")
        created_at = data.get("created_at")
        return cls(
            code=normalize_promo_code(str(data["code"])),
            additional_tokens=max(0, int(data["additional_tokens"])),
            # Old promo files did not record this field. Preserve the best
            # known timestamp instead of making a legacy record unreadable.
            created_at=parse_datetime(created_at) if created_at else (
                parse_datetime(used_at) if used_at else utc_now()
            ),
            used_at=parse_datetime(used_at) if used_at else None,
            used_access_code=(
                normalize_access_code(str(data["used_access_code"]))
                if data.get("used_access_code") else None
            ),
            owner_index=max(1, int(data.get("owner_index") or 1)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "additional_tokens": self.additional_tokens,
            "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
            "used_at": self.used_at.astimezone(timezone.utc).isoformat() if self.used_at else None,
            "used_access_code": self.used_access_code,
            "owner_index": self.owner_index,
        }


class PromoCodeStore:
    """Persistent one-time promo codes shared by all token-key owners."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    def _read(self) -> list[PromoCode]:
        if not self.path.exists():
            return []
        raw = self.path.read_text(encoding="utf-8").strip()
        if not raw:
            return []
        try:
            payload = json.loads(raw)
            if not isinstance(payload, list):
                raise ValueError("root is not a list")
            return [PromoCode.from_dict(item) for item in payload if isinstance(item, dict)]
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Promo code store is unreadable: {exc}") from exc

    def _write(self, promos: list[PromoCode]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps([promo.to_dict() for promo in promos], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    async def add(self, promo: PromoCode) -> None:
        await self.add_many([promo])

    async def add_many(self, created: list[PromoCode]) -> None:
        """Atomically append promo codes after checking global uniqueness."""
        if not created:
            return
        async with self._lock:
            promos = self._read()
            new_codes = [promo.code for promo in created]
            if len(set(new_codes)) != len(new_codes) or any(
                promo.code in {item.code for item in promos} for promo in created
            ):
                raise ValueError("Promo code already exists.")
            promos.extend(created)
            self._write(promos)

    async def list(self) -> list[PromoCode]:
        async with self._lock:
            return list(self._read())

    async def list_for_owner(self, owner_index: int) -> list[PromoCode]:
        async with self._lock:
            return sorted(
                (promo for promo in self._read() if promo.owner_index == owner_index),
                key=lambda promo: promo.created_at,
                reverse=True,
            )

    async def claim(self, code: str, access_code: str, owner_index: int) -> PromoCode | None:
        normalized_code = normalize_promo_code(code)
        normalized_access_code = normalize_access_code(access_code)
        async with self._lock:
            promos = self._read()
            for index, promo in enumerate(promos):
                if (
                    promo.code != normalized_code
                    or promo.owner_index != owner_index
                    or promo.used_at is not None
                ):
                    continue
                claimed = replace(
                    promo,
                    used_at=utc_now(),
                    used_access_code=normalized_access_code,
                )
                promos[index] = claimed
                self._write(promos)
                return claimed
        return None

    async def restore_unclaimed(self, code: str, access_code: str, owner_index: int) -> bool:
        """Undo a provisional claim when the upstream credit was rejected."""
        normalized_code = normalize_promo_code(code)
        normalized_access_code = normalize_access_code(access_code)
        async with self._lock:
            promos = self._read()
            for index, promo in enumerate(promos):
                if (
                    promo.code != normalized_code
                    or promo.owner_index != owner_index
                    or promo.used_access_code != normalized_access_code
                ):
                    continue
                promos[index] = replace(promo, used_at=None, used_access_code=None)
                self._write(promos)
                return True
        return False


@dataclass(frozen=True, slots=True)
class StoredTokenKey:
    """A key together with the private store that owns it.

    Access codes are public and must be searched globally, while numeric IDs
    are meaningful only inside one owner's separate JSON file.
    """

    owner_index: int
    store: TokenKeyStore
    key: TokenKey


class TokenKeyStores:
    """Coordinate isolated owner stores while keeping the public page global."""

    def __init__(self, stores: list[TokenKeyStore] | tuple[TokenKeyStore, ...]) -> None:
        self._stores = tuple(stores)

    @property
    def count(self) -> int:
        return len(self._stores)

    def for_owner(self, owner_index: int) -> TokenKeyStore | None:
        if not 1 <= owner_index <= len(self._stores):
            return None
        return self._stores[owner_index - 1]

    async def list_all(self) -> list[StoredTokenKey]:
        groups = await asyncio.gather(*(store.list() for store in self._stores))
        return [
            StoredTokenKey(owner_index=index, store=self._stores[index - 1], key=key)
            for index, keys in enumerate(groups, start=1)
            for key in keys
        ]

    async def find_by_code(self, code: str) -> StoredTokenKey | None:
        normalized = normalize_access_code(code)
        if not normalized:
            return None
        for owner_index, store in enumerate(self._stores, start=1):
            key = await store.get_by_code(normalized)
            if key is not None:
                return StoredTokenKey(owner_index=owner_index, store=store, key=key)
        return None

    async def activate(self, code: str) -> StoredTokenKey | None:
        normalized = normalize_access_code(code)
        if not normalized:
            return None
        for owner_index, store in enumerate(self._stores, start=1):
            key = await store.activate(normalized)
            if key is not None:
                return StoredTokenKey(owner_index=owner_index, store=store, key=key)
        return None


@dataclass(frozen=True, slots=True)
class StoredResellerKey:
    owner_index: int
    store: ResellerKeyStore
    key: ResellerKey


class ResellerKeyStores:
    """Coordinate the local reseller stores matching the token-admin owners."""

    def __init__(self, stores: list[ResellerKeyStore] | tuple[ResellerKeyStore, ...]) -> None:
        self._stores = tuple(stores)

    @property
    def count(self) -> int:
        return len(self._stores)

    def for_owner(self, owner_index: int) -> ResellerKeyStore | None:
        if not 1 <= owner_index <= len(self._stores):
            return None
        return self._stores[owner_index - 1]

    async def find_by_code(self, code: str) -> StoredResellerKey | None:
        normalized = normalize_access_code(code)
        if not normalized:
            return None
        for owner_index, store in enumerate(self._stores, start=1):
            key = await store.get_by_code(normalized)
            if key is not None:
                return StoredResellerKey(owner_index=owner_index, store=store, key=key)
        return None

    async def list_all(self) -> list[StoredResellerKey]:
        groups = await asyncio.gather(*(store.list() for store in self._stores))
        return [
            StoredResellerKey(owner_index=index, store=self._stores[index - 1], key=key)
            for index, keys in enumerate(groups, start=1)
            for key in keys
        ]


@dataclass(frozen=True, slots=True)
class TokenAdmin:
    """One password-protected owner area and its primary upstream key."""

    password: str
    primary_api_key: str


def indexed_token_store_path(base_path: Path, owner_index: int) -> Path:
    """Return the predictable, human-maintainable store path for an owner."""
    if owner_index < 1:
        raise ValueError("Owner indexes start at 1.")
    return base_path.with_name(f"{base_path.stem}_{owner_index}{base_path.suffix}")


def indexed_reseller_store_path(base_path: Path, owner_index: int) -> Path:
    if owner_index < 1:
        raise ValueError("Owner indexes start at 1.")
    return base_path.with_name(f"{base_path.stem}_{owner_index}{base_path.suffix}")


def create_token_key_stores(base_path: Path, owner_count: int) -> list[TokenKeyStore]:
    """Build one store per configured password and migrate the old lone file.

    `token_keys.json` was used before per-password stores existed.  On the
    first upgrade it is moved, not copied, to `token_keys_1.json`, so existing
    access codes remain available and are not duplicated.  The numbered file
    names intentionally follow password order and can be renamed manually if
    that order is changed later.
    """
    if owner_count < 0:
        raise ValueError("Owner count cannot be negative.")
    paths = [indexed_token_store_path(base_path, index) for index in range(1, owner_count + 1)]
    if paths and base_path.exists():
        if paths[0].exists():
            raise RuntimeError(
                f"Both legacy store {base_path} and owner store {paths[0]} exist. "
                "Move or merge the legacy file before starting the service."
            )
        base_path.replace(paths[0])
    stores = [TokenKeyStore(path) for path in paths]
    for store in stores:
        store.ensure_exists()
    return stores


def create_reseller_key_stores(base_path: Path, owner_count: int) -> list[ResellerKeyStore]:
    if owner_count < 0:
        raise ValueError("Owner count cannot be negative.")
    stores = [
        ResellerKeyStore(indexed_reseller_store_path(base_path, index))
        for index in range(1, owner_count + 1)
    ]
    for store in stores:
        store.ensure_exists()
    return stores


def used_tokens_from_remaining(token_limit: int, remaining: int) -> int:
    return max(0, token_limit - max(0, remaining))


def trusted_secondary_remaining(
    reported: int,
    token_limit: int,
    primary_remaining: int | None = None,
) -> int | None:
    """Accept a live remaining value only when it belongs to the secondary key.

    cheapvibecode.ru/v1/balance returns the secondary key remaining only while
    that remaining does not exceed the primary key. Otherwise it returns the
    primary remaining, which is usually larger than the secondary limit and
    would zero out used tokens.
    """
    remaining = max(0, reported)
    if remaining > token_limit:
        return None
    if (
        primary_remaining is not None
        and remaining >= primary_remaining
        and token_limit > primary_remaining
    ):
        return None
    return remaining


class SecondaryKeyClient(Protocol):
    async def create_key(self, *, name: str, token_limit: int) -> str: ...

    async def add_tokens(self, *, api_key: str, additional_tokens: int, active: bool = True) -> None: ...

    async def set_key_active(self, *, api_key: str, active: bool) -> None: ...

    async def get_token_balance(self, *, api_key: str) -> int: ...

    async def get_primary_token_balance(self) -> int: ...

    async def export_logs(self, *, api_key: str) -> "LogExport": ...


@dataclass(frozen=True, slots=True)
class LogExport:
    """Downloaded log archive returned by the upstream reseller portal."""

    content: bytes
    content_type: str = "application/octet-stream"
    content_disposition: str | None = None


class KeyServiceError(RuntimeError):
    """An upstream key-service response safe to identify by HTTP status."""

    def __init__(self, status_code: int, detail: str = "") -> None:
        self.status_code = status_code
        self.detail = detail[:500]
        super().__init__(f"Key service rejected the request (HTTP {status_code}): {self.detail}")


def log_key_state_failure(*, owner_index: int | None, key_id: int, error: RuntimeError) -> None:
    """Record actionable upstream diagnostics without logging either API key."""
    if isinstance(error, KeyServiceError):
        logger.warning(
            "Secondary-key state update rejected (owner=%s, key_id=%s, HTTP %s): %s",
            owner_index,
            key_id,
            error.status_code,
            error.detail,
        )
    else:
        logger.warning(
            "Secondary-key state update failed (owner=%s, key_id=%s): %s",
            owner_index,
            key_id,
            error,
        )


class CheapVibeCodeClient:
    def __init__(self, primary_key: str, base_url: str, timeout_seconds: float = 30.0) -> None:
        self.primary_key = primary_key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def create_key(self, *, name: str, token_limit: int) -> str:
        if not self.primary_key:
            raise RuntimeError("CVC_PRIMARY_API_KEY is not configured.")
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    f"{self.base_url}/v1/keys",
                    headers={"Authorization": f"Bearer {self.primary_key}", "Content-Type": "application/json"},
                    json={"name": name, "token_limit": token_limit, "allowed_models": []},
                ) as response:
                    status, raw = response.status, await response.text()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise RuntimeError("Could not connect to the key service.") from exc
        if status >= 400:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                detail = raw
            else:
                detail = payload.get("detail") if isinstance(payload, dict) else raw
            raise KeyServiceError(status, str(detail))
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("The key service returned an invalid response.") from exc
        api_key = payload.get("key") if isinstance(payload, dict) else None
        if not isinstance(api_key, str) or not api_key.strip():
            raise RuntimeError("The key service response did not contain an API key.")
        return api_key.strip()

    async def add_tokens(self, *, api_key: str, additional_tokens: int, active: bool = True) -> None:
        if not self.primary_key:
            raise RuntimeError("CVC_PRIMARY_API_KEY is not configured.")
        if additional_tokens < 1:
            raise ValueError("Additional token amount must be positive.")
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    f"{self.base_url}/v1/keys/edit",
                    headers={"Authorization": f"Bearer {self.primary_key}", "Content-Type": "application/json"},
                    json={"key": api_key, "additional_tokens": additional_tokens, "active": active},
                ) as response:
                    status, raw = response.status, await response.text()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise RuntimeError("Could not connect to the key service.") from exc
        if status >= 400:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                detail = raw
            else:
                detail = payload.get("detail") if isinstance(payload, dict) else raw
            raise KeyServiceError(status, str(detail))

    async def set_key_active(self, *, api_key: str, active: bool) -> None:
        """Freeze or unfreeze a secondary key without changing its balance.

        ``additional_tokens`` is deliberately omitted here.  It is an
        additive field on this endpoint, and sending a speculative zero was
        rejected by the upstream service on production.  The active-state
        update has its own default token delta and must not alter the balance.
        """
        if not self.primary_key:
            raise RuntimeError("CVC_PRIMARY_API_KEY is not configured.")
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    f"{self.base_url}/v1/keys/edit",
                    headers={"Authorization": f"Bearer {self.primary_key}", "Content-Type": "application/json"},
                    json={"key": api_key, "active": active},
                ) as response:
                    status, raw = response.status, await response.text()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise RuntimeError("Could not connect to the key service.") from exc
        if status >= 400:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                detail = raw
            else:
                detail = payload.get("detail") if isinstance(payload, dict) else raw
            raise KeyServiceError(status, str(detail))

    async def get_token_balance(self, *, api_key: str) -> int:
        status = 0
        raw = ""
        for attempt in range(len(BALANCE_RATE_LIMIT_BACKOFF_SECONDS) + 1):
            try:
                async with aiohttp.ClientSession(timeout=self.timeout) as session:
                    async with session.get(
                        f"{self.base_url}/v1/balance",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                    ) as response:
                        status, raw = response.status, await response.text()
                        retry_after = response.headers.get("Retry-After", "")
            except (aiohttp.ClientError, TimeoutError) as exc:
                raise RuntimeError("Could not connect to the balance service.") from exc
            if status != 429 or attempt == len(BALANCE_RATE_LIMIT_BACKOFF_SECONDS):
                break
            delay = BALANCE_RATE_LIMIT_BACKOFF_SECONDS[attempt]
            try:
                # Respect an explicit upstream cooldown, but retain a small
                # safety margin for its documented one-request-per-second cap.
                delay = max(delay, float(retry_after))
            except (TypeError, ValueError):
                pass
            logger.info(
                "Secondary-key balance check was rate limited; retrying in %.2fs (attempt %s/%s)",
                delay,
                attempt + 1,
                len(BALANCE_RATE_LIMIT_BACKOFF_SECONDS),
            )
            await asyncio.sleep(delay)
        if status >= 400:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                detail = raw
            else:
                detail = payload.get("detail") or payload.get("error") if isinstance(payload, dict) else raw
            raise KeyServiceError(status, str(detail))
        try:
            payload = json.loads(raw)
            balance = payload.get("token_balance") if isinstance(payload, dict) else None
            if isinstance(balance, bool):
                raise TypeError
            return max(0, int(balance))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError("The balance service returned an invalid response.") from exc

    async def get_primary_token_balance(self) -> int:
        if not self.primary_key:
            raise RuntimeError("CVC_PRIMARY_API_KEY is not configured.")
        return await self.get_token_balance(api_key=self.primary_key)

    async def export_logs(self, *, api_key: str) -> LogExport:
        """Export one secondary key's logs with its owner's primary key.

        The reseller portal's browser endpoint is session-protected.  A
        visitor on starimg.ru has no cookie for the upstream portal, so the
        browser cannot call it directly.  This server-side call keeps the
        primary credential private and reaches the configured Starimg proxy.
        """
        if not self.primary_key:
            raise RuntimeError("CVC_PRIMARY_API_KEY is not configured.")
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    f"{self.base_url}/api/portal/reseller/logs/export",
                    headers={
                        "Accept": "*/*",
                        "Authorization": f"Bearer {self.primary_key}",
                        "Content-Type": "application/json",
                    },
                    json={"api_key": api_key},
                ) as response:
                    status = response.status
                    content = await response.read()
                    content_type = response.headers.get("Content-Type", "application/octet-stream")
                    disposition = response.headers.get("Content-Disposition")
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise RuntimeError("Could not connect to the log service.") from exc
        if status >= 400:
            raise RuntimeError("The log service rejected the request.")
        return LogExport(
            content=content,
            content_type=content_type,
            content_disposition=disposition,
        )


async def read_form(request: Request) -> dict[str, str]:
    payload = parse_qs((await request.body()).decode("utf-8", errors="replace"), keep_blank_values=True)
    return {name: values[0] if values else "" for name, values in payload.items()}


def valid_csrf(request: Request, form: dict[str, str], cookie_name: str) -> bool:
    submitted = form.get("csrf_token", "")
    cookie = request.cookies.get(cookie_name, "")
    return bool(submitted and cookie and secrets.compare_digest(submitted, cookie))


def attach_csrf(response: HTMLResponse, token: str, cookie_name: str, path: str) -> HTMLResponse:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.set_cookie(cookie_name, token, httponly=True, samesite="lax", secure=True, path=path)
    return response


def create_tokens_routes(
    app: FastAPI,
    *,
    key_client: SecondaryKeyClient | None = None,
    owner_key_clients: list[SecondaryKeyClient] | tuple[SecondaryKeyClient, ...] | None = None,
    stores: TokenKeyStores | list[TokenKeyStore] | tuple[TokenKeyStore, ...] | None = None,
    reseller_stores: ResellerKeyStores | list[ResellerKeyStore] | tuple[ResellerKeyStore, ...] | None = None,
    reseller_operation_stores: list[ResellerOperationStore] | tuple[ResellerOperationStore, ...] | None = None,
    promo_store: PromoCodeStore | None = None,
    admins: list[TokenAdmin] | tuple[TokenAdmin, ...] | None = None,
    admin_passwords: list[str] | tuple[str, ...] | None = None,
    # Kept temporarily for callers that still use the single-admin API.
    store: TokenKeyStore | None = None,
    admin_password: str | None = None,
) -> None:
    """Register the public secondary-key page and its password-protected admin page.

    Every configured password owns one isolated store.  The public page does
    not disclose this split: it resolves an access code across every store.
    """
    if stores is None:
        if store is None:
            raise ValueError("At least one token key store must be configured.")
        token_stores = TokenKeyStores([store])
    elif isinstance(stores, TokenKeyStores):
        token_stores = stores
    else:
        token_stores = TokenKeyStores(stores)
    if token_stores.count < 1:
        raise ValueError("At least one token key store must be configured.")
    if reseller_stores is None:
        reseller_key_stores = ResellerKeyStores([
            ResellerKeyStore(Path(f"reseller_keys_{index}.json"))
            for index in range(1, token_stores.count + 1)
        ])
    elif isinstance(reseller_stores, ResellerKeyStores):
        reseller_key_stores = reseller_stores
    else:
        reseller_key_stores = ResellerKeyStores(reseller_stores)
    if reseller_key_stores.count != token_stores.count:
        raise ValueError("Every token key store needs a matching reseller key store.")
    for owner_index in range(1, reseller_key_stores.count + 1):
        reseller_store = reseller_key_stores.for_owner(owner_index)
        if reseller_store is not None:
            reseller_store.ensure_exists()
    if reseller_operation_stores is None:
        operation_stores = tuple(
            ResellerOperationStore(
                reseller_key_stores.for_owner(owner_index).path.with_name(
                    reseller_key_stores.for_owner(owner_index).path.stem + "_operations.json"
                )
            )
            for owner_index in range(1, reseller_key_stores.count + 1)
        )
    else:
        operation_stores = tuple(reseller_operation_stores)
    if len(operation_stores) != reseller_key_stores.count:
        raise ValueError("Every reseller key store needs a matching operation store.")
    for operation_store in operation_stores:
        operation_store.ensure_exists()
    promo_codes = promo_store or PromoCodeStore(Path("promo_codes.json"))

    configured_admins = tuple(admin for admin in (admins or ()) if admin.password and admin.primary_api_key)
    if not configured_admins:
        configured_passwords = tuple(password for password in (admin_passwords or ()) if password)
        if not configured_passwords and admin_password:
            configured_passwords = (admin_password,)
        if key_client is None and configured_passwords:
            raise ValueError("A primary key client is required for every tokens admin.")
        configured_admins = tuple(
            TokenAdmin(password=password, primary_api_key="") for password in configured_passwords
        )
        configured_clients = tuple(key_client for _ in configured_admins)
    else:
        configured_clients = tuple(owner_key_clients or ())
        if len(configured_clients) != len(configured_admins):
            raise ValueError("Every tokens admin needs its own primary key client.")
    if len({admin.password for admin in configured_admins}) != len(configured_admins):
        raise ValueError("Tokens admin passwords must be unique.")
    if len(configured_admins) > token_stores.count:
        raise ValueError("Every configured admin password needs its own token key store.")

    app.state.token_key_store = token_stores.for_owner(1)
    app.state.token_key_stores = token_stores
    app.state.reseller_key_stores = reseller_key_stores
    app.state.reseller_operation_stores = operation_stores
    app.state.promo_code_store = promo_codes
    app.state.secondary_key_client = key_client
    app.state.owner_secondary_key_clients = configured_clients
    admin_sessions: dict[str, int] = {}
    reseller_sessions: dict[str, tuple[int, int]] = {}
    creation_lock = asyncio.Lock()

    def user_response(
        *,
        locale: str = "ru",
        key: TokenKey | None = None,
        error: str = "",
        notice: str = "",
        submitted_code: str = "",
        status_code: int = 200,
    ) -> HTMLResponse:
        csrf_token = secrets.token_urlsafe(32)
        response = render_tokens_page(
            csrf_token=csrf_token,
            locale=locale,
            key=key,
            error=error,
            notice=notice,
            submitted_code=submitted_code,
        )
        response.status_code = status_code
        return attach_csrf(response, csrf_token, TOKENS_USER_CSRF_COOKIE, "/ai/tokens")

    def admin_owner(request: Request) -> int | None:
        session = request.cookies.get(TOKENS_ADMIN_COOKIE, "")
        owner_index = admin_sessions.get(session)
        return owner_index if owner_index is not None and token_stores.for_owner(owner_index) is not None else None

    def is_admin(request: Request) -> bool:
        return admin_owner(request) is not None

    def reseller_identity(request: Request) -> tuple[int, int] | None:
        session = request.cookies.get(TOKENS_RESELLER_COOKIE, "")
        identity = reseller_sessions.get(session)
        if identity is None:
            return None
        owner_index, reseller_id = identity
        reseller_store = reseller_key_stores.for_owner(owner_index)
        if reseller_store is None:
            return None
        return identity

    async def reseller_from_request(request: Request) -> StoredResellerKey | None:
        identity = reseller_identity(request)
        if identity is None:
            return None
        owner_index, reseller_id = identity
        reseller_store = reseller_key_stores.for_owner(owner_index)
        key = await reseller_store.get(reseller_id) if reseller_store is not None else None
        if key is None or not key.active:
            return None
        return StoredResellerKey(owner_index=owner_index, store=reseller_store, key=key)

    def invalidate_reseller_sessions(owner_index: int, reseller_id: int) -> None:
        identity = (owner_index, reseller_id)
        for session, session_identity in list(reseller_sessions.items()):
            if session_identity == identity:
                reseller_sessions.pop(session, None)

    def issue_reseller_session(owner_index: int, reseller_id: int) -> str:
        session = secrets.token_urlsafe(32)
        reseller_sessions[session] = (owner_index, reseller_id)
        return session

    def set_reseller_session_cookie(response: Response, session: str) -> None:
        response.set_cookie(
            TOKENS_RESELLER_COOKIE,
            session,
            httponly=True,
            samesite="lax",
            secure=True,
            path="/ai/tokens/reselling",
            max_age=60 * 60 * 24 * 30,
        )

    def owner_client(owner_index: int) -> SecondaryKeyClient | None:
        """Return the owner's upstream client when it is configured.

        The public activation page may still have legacy local key files when
        no admin/primary-key configuration has been deployed yet.  That must
        show the saved balance, not crash the whole page with ``IndexError``.
        """
        if not 1 <= owner_index <= len(configured_clients):
            return None
        return configured_clients[owner_index - 1]

    def operation_store_for_owner(owner_index: int) -> ResellerOperationStore | None:
        if not 1 <= owner_index <= len(operation_stores):
            return None
        return operation_stores[owner_index - 1]

    async def upstream_call_with_backoff(call, *, retry_server_errors: bool = False):
        """Retry definitive transient throttling/failures, never timeouts.

        A timeout can mean the provider committed a create/top-up but lost its
        response. Callers record that as ``unknown`` and retain the reserved
        reseller budget rather than risk a duplicate primary-key allocation.
        """
        for attempt, delay in enumerate((0.4, 1.0), start=1):
            try:
                return await call()
            except KeyServiceError as exc:
                if exc.status_code != 429 and not (retry_server_errors and 500 <= exc.status_code < 600):
                    raise
                logger.info("Reseller upstream request rejected transiently; retrying in %.1fs (%s/2)", delay, attempt)
                await asyncio.sleep(delay)
            except RuntimeError:
                raise
        return await call()

    async def read_primary_remaining(owner_index: int) -> int | None:
        client = owner_client(owner_index)
        if client is None:
            return None
        try:
            return await client.get_primary_token_balance()
        except RuntimeError:
            return None

    async def refresh_stored_balance(
        key: TokenKey,
        key_store: TokenKeyStore,
        owner_index: int,
        primary_remaining: int | None = None,
    ) -> TokenKey:
        client = owner_client(owner_index)
        if client is None:
            return key
        try:
            reported = await client.get_token_balance(api_key=key.api_key)
        except RuntimeError:
            return key
        remaining = trusted_secondary_remaining(reported, key.token_limit, primary_remaining)
        if remaining is None:
            return key
        updated = await key_store.apply_remaining(key.id, remaining)
        return updated or key

    async def refresh_stored_balances(keys: list[TokenKey], key_store: TokenKeyStore, owner_index: int) -> list[TokenKey]:
        if not keys:
            return keys
        primary_remaining = await read_primary_remaining(owner_index)
        refreshed = await asyncio.gather(
            *(refresh_stored_balance(key, key_store, owner_index, primary_remaining) for key in keys)
        )
        return sorted(refreshed, key=lambda key: key.id, reverse=True)

    async def admin_response(
        request: Request,
        *,
        error: str = "",
        notice: str = "",
        status_code: int = 200,
        created_access_codes: list[str] | None = None,
        created_promo_codes: list[str] | None = None,
    ) -> HTMLResponse:
        csrf_token = secrets.token_urlsafe(32)
        table_state = token_admin_page_state(request.query_params)
        owner_index = admin_owner(request)
        owner_store = token_stores.for_owner(owner_index) if owner_index is not None else None
        reseller_store = reseller_key_stores.for_owner(owner_index) if owner_index is not None else None
        authenticated = owner_store is not None
        all_keys = await owner_store.list() if owner_store else []
        resellers = await reseller_store.list() if reseller_store else []
        # Children are represented only inside their reseller's expandable
        # panel, never as ordinary top-level admin records.
        top_level_keys = [key for key in all_keys if key.reseller_id is None]
        promos = await promo_codes.list_for_owner(owner_index) if owner_index is not None else []
        # Search and global ordering must happen before the list is sliced for
        # the 100-row page.  Doing it in the browser would order only the
        # currently visible rows and make later pages inconsistent.
        matching_keys = filter_token_admin_keys(top_level_keys, table_state.search_query)
        ordered_keys = sort_token_admin_keys(matching_keys, table_state)
        keys, current_page, total_pages = paginate_token_admin_keys(ordered_keys, table_state.page)
        table_state = replace(table_state, page=current_page)
        # Only displayed records need a live balance request.  Filtering and
        # ordering still use the complete local store above, but opening one
        # page must not fan out into an upstream request for every key owned
        # by this admin.
        if owner_store is not None and owner_index is not None:
            keys = await refresh_stored_balances(keys, owner_store, owner_index)
        response = render_tokens_admin(
            csrf_token=csrf_token,
            authenticated=authenticated,
            password_configured=bool(configured_admins),
            keys=keys,
            total_key_count=len(top_level_keys),
            matching_key_count=len(matching_keys),
            page_state=table_state,
            total_pages=total_pages,
            promos=promos,
            error=error,
            notice=notice,
            created_access_codes=created_access_codes or [],
            created_promo_codes=created_promo_codes or [],
            resellers=resellers,
            reseller_children={
                reseller.id: [key for key in all_keys if key.reseller_id == reseller.id]
                for reseller in resellers
            },
        )
        response.status_code = status_code
        return attach_csrf(response, csrf_token, TOKENS_ADMIN_CSRF_COOKIE, "/ai/tokens/adm")

    async def page(request: Request) -> HTMLResponse:
        locale = token_locale(request.query_params.get("lang"))
        # Sellers may hand the buyer a ready-to-use activation URL, e.g.
        # ``/ai/tokens?key=ABCDEFGHIJKLMNOPQRST``. It only pre-fills the
        # form; activation still requires an explicit POST protected by CSRF.
        # ``access_code`` is accepted too, matching the form field name.
        linked_code = request.query_params.get("key") or request.query_params.get("access_code") or ""
        submitted_code = normalize_access_code(linked_code)
        access_code = request.cookies.get(TOKENS_ACCESS_COOKIE, "")
        found = await token_stores.find_by_code(access_code) if access_code else None
        key = found.key if found is not None else None
        # The page's script fetches /balance immediately after rendering. Do
        # not make the same upstream request here as well: the provider
        # permits just one check per second per API key, and duplicate page
        # render + browser requests were needlessly causing HTTP 429.
        error = exhausted_message(key, locale) if key is not None and key.is_exhausted else ""
        return user_response(locale=locale, key=key, error=error, submitted_code=submitted_code)

    async def activate(request: Request) -> HTMLResponse:
        form = await read_form(request)
        locale = token_locale(form.get("lang"))
        if not valid_csrf(request, form, TOKENS_USER_CSRF_COOKIE):
            return RedirectResponse(url=f"/ai/tokens?lang={locale}", status_code=303)
        access_code = normalize_access_code(form.get("access_code", ""))
        if not access_code:
            return user_response(locale=locale, error=TOKEN_TEXT[locale]["required"], status_code=400)
        found = await token_stores.activate(access_code)
        if found is None:
            return user_response(locale=locale, error=TOKEN_TEXT[locale]["missing"], submitted_code=access_code, status_code=404)
        # A new key begins with the locally stored limit. The browser's one
        # live /balance check updates it after this response is rendered.
        key = found.key
        if key.is_exhausted:
            response = user_response(locale=locale, key=key, error=exhausted_message(key, locale), submitted_code=access_code)
        else:
            response = user_response(locale=locale, key=key, notice=TOKEN_TEXT[locale]["success"], submitted_code=access_code)
        response.set_cookie(
            TOKENS_ACCESS_COOKIE, access_code, httponly=True, samesite="lax", secure=True,
            path="/ai/tokens", max_age=60 * 60 * 24 * 365,
        )
        return response

    async def balance(request: Request) -> JSONResponse:
        access_code = request.cookies.get(TOKENS_ACCESS_COOKIE, "")
        found = await token_stores.find_by_code(access_code) if access_code else None
        if found is None:
            return JSONResponse({"ok": False, "error": "access_key_missing"}, status_code=401, headers={"Cache-Control": "no-store"})
        key = found.key
        client = owner_client(found.owner_index)
        if client is None:
            logger.warning(
                "Secondary-key balance unavailable: no client (owner=%s, key_id=%s)",
                found.owner_index,
                key.id,
            )
            return JSONResponse({"ok": False, "error": "balance_unavailable"}, status_code=502, headers={"Cache-Control": "no-store"})
        try:
            reported = await client.get_token_balance(api_key=key.api_key)
        except KeyServiceError as exc:
            if exc.status_code != 429:
                log_key_state_failure(owner_index=found.owner_index, key_id=key.id, error=exc)
                return JSONResponse({"ok": False, "error": "balance_unavailable"}, status_code=502, headers={"Cache-Control": "no-store"})
            # The retry budget was exhausted. A locally stored balance is
            # preferable to making the user's page show a 502; the next
            # browser refresh will try the live balance again.
            logger.info(
                "Using stored balance after upstream rate limit (owner=%s, key_id=%s)",
                found.owner_index,
                key.id,
            )
            remaining = key.remaining_tokens
            return JSONResponse(
                {
                    "ok": True,
                    "token_balance": remaining,
                    "formatted": format_tokens(remaining),
                    "token_limit": key.token_limit,
                    "token_limit_formatted": format_tokens(key.token_limit),
                    "used_tokens": key.used_tokens,
                    "stale": True,
                },
                headers={"Cache-Control": "no-store"},
            )
        except RuntimeError as exc:
            log_key_state_failure(owner_index=found.owner_index, key_id=key.id, error=exc)
            return JSONResponse({"ok": False, "error": "balance_unavailable"}, status_code=502, headers={"Cache-Control": "no-store"})
        remaining = trusted_secondary_remaining(
            reported, key.token_limit, await read_primary_remaining(found.owner_index)
        )
        if remaining is None:
            # Some upstream accounts return their *primary* balance here for
            # a secondary key.  That number is not a per-key balance and was
            # the reason every browser refresh became a 502.  Keep the last
            # known per-key amount instead: the page remains usable and we
            # never overwrite a key's usage with the owner's total balance.
            logger.info(
                "Ignoring aggregate balance for secondary key (owner=%s, key_id=%s, reported=%s, limit=%s)",
                found.owner_index,
                key.id,
                reported,
                key.token_limit,
            )
            remaining = key.remaining_tokens
        else:
            updated = await found.store.apply_remaining(key.id, remaining)
            key = updated or key
        return JSONResponse(
            {
                "ok": True,
                "token_balance": remaining,
                "formatted": format_tokens(remaining),
                "token_limit": key.token_limit,
                "token_limit_formatted": format_tokens(key.token_limit),
                "used_tokens": key.used_tokens,
                "used_tokens_formatted": format_tokens(key.used_tokens),
            },
            headers={"Cache-Control": "no-store"},
        )

    async def export_logs(request: Request) -> Response:
        """Download logs without exposing an upstream portal credential."""
        access_code = request.cookies.get(TOKENS_ACCESS_COOKIE, "")
        found = await token_stores.find_by_code(access_code) if access_code else None
        if found is None:
            return JSONResponse(
                {"ok": False, "error": "access_key_missing"},
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )
        client = owner_client(found.owner_index)
        if client is None:
            return JSONResponse(
                {"ok": False, "error": "logs_unavailable"},
                status_code=502,
                headers={"Cache-Control": "no-store"},
            )
        try:
            exported = await client.export_logs(api_key=found.key.api_key)
        except RuntimeError:
            return JSONResponse(
                {"ok": False, "error": "logs_unavailable"},
                status_code=502,
                headers={"Cache-Control": "no-store"},
            )
        content_type = exported.content_type if "\r" not in exported.content_type and "\n" not in exported.content_type else "application/octet-stream"
        disposition = exported.content_disposition or 'attachment; filename="logs.json"'
        if "\r" in disposition or "\n" in disposition:
            disposition = 'attachment; filename="logs.json"'
        return Response(
            content=exported.content,
            media_type=content_type,
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": disposition,
            },
        )

    async def claim_bonus(request: Request) -> HTMLResponse:
        form = await read_form(request)
        locale = token_locale(form.get("lang"))
        if not valid_csrf(request, form, TOKENS_USER_CSRF_COOKIE):
            return RedirectResponse(url=f"/ai/tokens?lang={locale}", status_code=303)
        access_code = request.cookies.get(TOKENS_ACCESS_COOKIE, "")
        found = await token_stores.find_by_code(access_code) if access_code else None
        if found is None:
            return user_response(locale=locale, error=TOKEN_TEXT[locale]["missing"], status_code=401)
        promo = await promo_codes.claim(
            form.get("promo_code", ""), access_code, found.owner_index
        )
        if promo is None:
            return user_response(locale=locale, key=found.key, error=TOKEN_TEXT[locale]["promo_missing"], status_code=404)
        try:
            client = owner_client(found.owner_index)
            if client is None:
                raise RuntimeError("Primary key client is not configured.")
            await client.add_tokens(
                api_key=found.key.api_key,
                additional_tokens=promo.additional_tokens,
                active=found.key.active,
            )
        except (RuntimeError, ValueError):
            await promo_codes.restore_unclaimed(promo.code, access_code, found.owner_index)
            return user_response(locale=locale, key=found.key, error=TOKEN_TEXT[locale]["promo_error"], status_code=502)
        updated_key = replace(found.key, token_limit=found.key.token_limit + promo.additional_tokens)
        await found.store.update(found.key.id, updated_key)
        return user_response(
            locale=locale,
            key=updated_key,
            notice=TOKEN_TEXT[locale]["promo_success"].format(
                code=promo.code,
                tokens=format_tokens(promo.additional_tokens),
            ),
        )

    async def toggle_user_key_freeze(request: Request) -> HTMLResponse:
        """Let the holder of an activated access key freeze only that key."""
        form = await read_form(request)
        locale = token_locale(form.get("lang"))
        if not valid_csrf(request, form, TOKENS_USER_CSRF_COOKIE):
            return RedirectResponse(url=f"/ai/tokens?lang={locale}", status_code=303)
        access_code = request.cookies.get(TOKENS_ACCESS_COOKIE, "")
        found = await token_stores.find_by_code(access_code) if access_code else None
        if found is None:
            return user_response(locale=locale, error=TOKEN_TEXT[locale]["missing"], status_code=401)
        client = owner_client(found.owner_index)
        if client is None:
            return user_response(locale=locale, key=found.key, error=TOKEN_TEXT[locale]["freeze_error"], status_code=503)
        target_active = not found.key.active
        try:
            await client.set_key_active(api_key=found.key.api_key, active=target_active)
        except (RuntimeError, ValueError) as exc:
            log_key_state_failure(owner_index=found.owner_index, key_id=found.key.id, error=exc)
            return user_response(locale=locale, key=found.key, error=TOKEN_TEXT[locale]["freeze_error"], status_code=502)
        updated_key = replace(found.key, active=target_active)
        await found.store.update(found.key.id, updated_key)
        return user_response(
            locale=locale,
            key=updated_key,
            notice=TOKEN_TEXT[locale]["unfreeze_success" if target_active else "freeze_success"],
        )

    async def admin_page(request: Request) -> HTMLResponse:
        return await admin_response(request)

    async def admin_login(request: Request) -> HTMLResponse:
        form = await read_form(request)
        if not valid_csrf(request, form, TOKENS_ADMIN_CSRF_COOKIE):
            return RedirectResponse(url="/ai/tokens/adm", status_code=303)
        if not configured_admins:
            return await admin_response(
                request,
                error="Админ-панель недоступна: задайте TOKENS_ADMINS в .env.",
                status_code=503,
            )
        supplied = form.get("password", "")
        owner_index = next(
            (
                index
                for index, admin in enumerate(configured_admins, start=1)
                if secrets.compare_digest(supplied, admin.password)
            ),
            None,
        )
        if owner_index is None:
            return await admin_response(
                request,
                error="Неверный пароль.",
                status_code=401,
            )
        session = secrets.token_urlsafe(32)
        admin_sessions[session] = owner_index
        response = RedirectResponse(url="/ai/tokens/adm", status_code=303)
        response.headers["Cache-Control"] = "no-store"
        response.set_cookie(
            TOKENS_ADMIN_COOKIE,
            session,
            httponly=True,
            samesite="lax",
            secure=True,
            path="/ai/tokens/adm",
            max_age=60 * 60 * 24 * 30,
        )
        return response

    async def admin_logout(request: Request) -> HTMLResponse:
        form = await read_form(request)
        if not valid_csrf(request, form, TOKENS_ADMIN_CSRF_COOKIE):
            return RedirectResponse(url="/ai/tokens/adm", status_code=303)
        session = request.cookies.get(TOKENS_ADMIN_COOKIE, "")
        admin_sessions.pop(session, None)
        response = await admin_response(request, notice="Вы вышли из админ-панели.")
        response.delete_cookie(TOKENS_ADMIN_COOKIE, path="/ai/tokens/adm")
        return response

    async def admin_create(request: Request) -> HTMLResponse:
        form = await read_form(request)
        owner_index = admin_owner(request)
        owner_store = token_stores.for_owner(owner_index) if owner_index is not None else None
        if owner_store is None:
            return await admin_response(request, error="Сессия администратора завершена.", status_code=401)
        if not valid_csrf(request, form, TOKENS_ADMIN_CSRF_COOKIE):
            return RedirectResponse(url="/ai/tokens/adm", status_code=303)
        service = form.get("service", "")
        name = form.get("name", "").strip()
        token_limit = positive_int(form.get("token_limit", ""))
        quantity = positive_int(form.get("quantity", ""))
        if service not in SERVICE_OPTIONS:
            return await admin_response(request, error="Выберите доступный сервис.", status_code=400)
        if not name:
            return await admin_response(request, error="Введите название ключа.", status_code=400)
        if token_limit is None or token_limit < 1:
            return await admin_response(request, error="Количество токенов должно быть положительным целым числом.", status_code=400)
        if quantity is None or not 1 <= quantity <= 100:
            return await admin_response(request, error="Количество ключей должно быть от 1 до 100.", status_code=400)
        async with creation_lock:
            existing = await owner_store.list()
            all_stored_keys = await token_stores.list_all()
            all_resellers = await reseller_key_stores.list_all()
            existing_codes = {
                stored.key.access_code for stored in all_stored_keys
            } | {
                stored.key.access_code for stored in all_resellers
            }
            if service == RESELLING_SERVICE:
                reseller_store = reseller_key_stores.for_owner(owner_index)
                if reseller_store is None:
                    return await admin_response(request, error="Не настроено хранилище реселлеров.", status_code=503)
                existing_resellers = await reseller_store.list()
                records: list[ResellerKey] = []
                for index in range(quantity):
                    # Reserve each generated code before making the next one;
                    # uniqueness holds for normal, reseller, and same-batch
                    # keys alike.
                    access_code = generate_access_code(existing_codes)
                    existing_codes.add(access_code)
                    records.append(ResellerKey(
                        id=max((key.id for key in existing_resellers), default=0) + index + 1,
                        created_at=utc_now(),
                        access_code=access_code,
                        name=name,
                        token_limit=token_limit,
                    ))
                await reseller_store.add_many(records)
                return await admin_response(
                    request,
                    notice=f"Создано реселлерских ключей: {len(records)}. Внешние ключи не создавались.",
                    created_access_codes=[record.access_code for record in records],
                )
            next_id = max((key.id for key in existing), default=0) + 1
            records: list[TokenKey] = []
            try:
                for index in range(quantity):
                    client = owner_client(owner_index)
                    if client is None:
                        raise RuntimeError("Не настроен основной API-ключ администратора.")
                    api_key = await client.create_key(name=name, token_limit=token_limit)
                    access_code = generate_access_code(existing_codes)
                    existing_codes.add(access_code)
                    records.append(TokenKey(
                        id=next_id + index,
                        created_at=utc_now(),
                        access_code=access_code,
                        api_key=api_key,
                        service=service,
                        name=name,
                        token_limit=token_limit,
                    ))
            except RuntimeError as exc:
                # Keep every successfully created upstream key visible to the
                # administrator. Otherwise a later failed request would leave
                # a paid external key with no local access code to manage.
                if records:
                    await owner_store.add_many(records)
                    return await admin_response(
                        request,
                        error=(
                            f"Создано ключей: {len(records)} из {quantity}. "
                            f"Остальные не созданы: {str(exc)}"
                        ),
                        status_code=502,
                        created_access_codes=[record.access_code for record in records],
                    )
                return await admin_response(
                    request,
                    error=f"Ключи не созданы: {str(exc)}",
                    status_code=502,
                )
            await owner_store.add_many(records)
        return await admin_response(
            request,
            notice=f"Создано ключей: {len(records)}.",
            created_access_codes=[record.access_code for record in records],
        )

    async def admin_create_promos(request: Request) -> HTMLResponse:
        form = await read_form(request)
        owner_index = admin_owner(request)
        if owner_index is None:
            return await admin_response(request, error="Сессия администратора завершена.", status_code=401)
        if not valid_csrf(request, form, TOKENS_ADMIN_CSRF_COOKIE):
            return RedirectResponse(url="/ai/tokens/adm", status_code=303)
        additional_tokens = positive_int(form.get("additional_tokens", ""))
        quantity = positive_int(form.get("quantity", ""))
        if additional_tokens is None or additional_tokens < 1:
            return await admin_response(
                request,
                error="Количество начисляемых токенов должно быть положительным целым числом.",
                status_code=400,
            )
        if quantity is None or not 1 <= quantity <= 100:
            return await admin_response(
                request,
                error="Количество промокодов должно быть от 1 до 100.",
                status_code=400,
            )
        async with creation_lock:
            existing_codes = {promo.code for promo in await promo_codes.list()}
            records: list[PromoCode] = []
            for _ in range(quantity):
                code = generate_promo_code(existing_codes)
                existing_codes.add(code)
                records.append(PromoCode(
                    code=code,
                    additional_tokens=additional_tokens,
                    owner_index=owner_index,
                ))
            await promo_codes.add_many(records)
        return await admin_response(
            request,
            notice=f"Создано промокодов: {len(records)}.",
            created_promo_codes=[record.code for record in records],
        )

    async def reseller_response(
        request: Request,
        *,
        reseller: StoredResellerKey | None = None,
        locale: str = "en",
        error: str = "",
        notice: str = "",
        submitted_code: str = "",
        status_code: int = 200,
    ) -> HTMLResponse:
        csrf_token = secrets.token_urlsafe(32)
        children: list[TokenKey] = []
        operations: list[ResellerOperation] = []
        if reseller is not None:
            owner_store = token_stores.for_owner(reseller.owner_index)
            if owner_store is not None:
                children = [key for key in await owner_store.list() if key.reseller_id == reseller.key.id]
            operation_store = operation_store_for_owner(reseller.owner_index)
            if operation_store is not None:
                operations = await operation_store.list_for_reseller(reseller.key.id)
        response = render_reseller_page(
            csrf_token=csrf_token,
            reseller=reseller.key if reseller is not None else None,
            children=children,
            operations=operations,
            locale=reseller_locale(locale),
            error=error,
            notice=notice,
            submitted_code=submitted_code,
        )
        response.status_code = status_code
        return attach_csrf(response, csrf_token, TOKENS_RESELLER_CSRF_COOKIE, "/ai/tokens/reselling")

    async def reseller_page(request: Request) -> HTMLResponse:
        reseller = await reseller_from_request(request)
        linked_code = normalize_access_code(request.query_params.get("key") or "")
        locale = reseller_locale(request.query_params.get("lang"))
        return await reseller_response(request, reseller=reseller, locale=locale, submitted_code=linked_code)

    async def reseller_login(request: Request) -> HTMLResponse:
        form = await read_form(request)
        locale = reseller_locale(form.get("lang"))
        if not valid_csrf(request, form, TOKENS_RESELLER_CSRF_COOKIE):
            return RedirectResponse(url=f"/ai/tokens/reselling?lang={locale}", status_code=303)
        found = await reseller_key_stores.find_by_code(form.get("access_code", ""))
        if found is None or not found.key.active:
            return await reseller_response(
                request,
                locale=locale,
                error=reseller_message(locale, "invalid_key"),
                submitted_code=normalize_access_code(form.get("access_code", "")),
                status_code=401,
            )
        session = issue_reseller_session(found.owner_index, found.key.id)
        response = RedirectResponse(url=f"/ai/tokens/reselling?lang={locale}", status_code=303)
        response.headers["Cache-Control"] = "no-store"
        set_reseller_session_cookie(response, session)
        return response

    async def reseller_logout(request: Request) -> HTMLResponse:
        form = await read_form(request)
        locale = reseller_locale(form.get("lang"))
        if not valid_csrf(request, form, TOKENS_RESELLER_CSRF_COOKIE):
            return RedirectResponse(url=f"/ai/tokens/reselling?lang={locale}", status_code=303)
        reseller_sessions.pop(request.cookies.get(TOKENS_RESELLER_COOKIE, ""), None)
        response = await reseller_response(request, locale=locale, notice=reseller_message(locale, "logout_notice"))
        response.delete_cookie(TOKENS_RESELLER_COOKIE, path="/ai/tokens/reselling")
        return response

    async def reseller_regenerate_access(request: Request) -> HTMLResponse:
        form = await read_form(request)
        locale = reseller_locale(form.get("lang"))
        reseller = await reseller_from_request(request)
        if reseller is None:
            return await reseller_response(request, locale=locale, error=reseller_message(locale, "session_expired"), status_code=401)
        if not valid_csrf(request, form, TOKENS_RESELLER_CSRF_COOKIE):
            return RedirectResponse(url=f"/ai/tokens/reselling?lang={locale}", status_code=303)

        async with creation_lock:
            current = await reseller.store.get(reseller.key.id)
            if current is None or not current.active:
                return await reseller_response(request, locale=locale, error=reseller_message(locale, "invalid_key"), status_code=401)
            existing_codes = {stored.key.access_code for stored in await token_stores.list_all()}
            existing_codes.update(stored.key.access_code for stored in await reseller_key_stores.list_all())
            new_code = generate_access_code(existing_codes)
            updated = replace(current, access_code=new_code)
            await reseller.store.update(current.id, updated)

        invalidate_reseller_sessions(reseller.owner_index, reseller.key.id)
        session = issue_reseller_session(reseller.owner_index, reseller.key.id)
        response = await reseller_response(
            request,
            reseller=StoredResellerKey(owner_index=reseller.owner_index, store=reseller.store, key=updated),
            locale=locale,
            notice=reseller_message(locale, "regenerate_notice"),
        )
        response.headers["Cache-Control"] = "no-store"
        set_reseller_session_cookie(response, session)
        return response

    async def reseller_create_child(request: Request) -> HTMLResponse:
        form = await read_form(request)
        locale = reseller_locale(form.get("lang"))
        reseller = await reseller_from_request(request)
        if reseller is None:
            return await reseller_response(request, locale=locale, error=reseller_message(locale, "session_expired"), status_code=401)
        if not valid_csrf(request, form, TOKENS_RESELLER_CSRF_COOKIE):
            return RedirectResponse(url=f"/ai/tokens/reselling?lang={locale}", status_code=303)
        service = form.get("service", "")
        name = form.get("name", "").strip()
        token_limit = positive_int(form.get("token_limit", ""))
        if service not in RESELLER_SERVICE_OPTIONS:
            return await reseller_response(request, reseller=reseller, locale=locale, error=reseller_message(locale, "ordinary_service"), status_code=400)
        if not name or token_limit is None or token_limit < 1:
            return await reseller_response(request, reseller=reseller, locale=locale, error=reseller_message(locale, "name_limit"), status_code=400)
        owner_store = token_stores.for_owner(reseller.owner_index)
        client = owner_client(reseller.owner_index)
        operation_store = operation_store_for_owner(reseller.owner_index)
        if owner_store is None or client is None or operation_store is None:
            return await reseller_response(request, reseller=reseller, locale=locale, error=reseller_message(locale, "service_unavailable"), status_code=503)
        # Reserve first under the reseller-store lock; do not automatically
        # retry an ambiguous create after a network failure.
        reserved = await reseller.store.reserve(reseller.key.id, token_limit)
        if reserved is None:
            return await reseller_response(request, reseller=reseller, locale=locale, error=reseller_message(locale, "insufficient"), status_code=409)
        operation = ResellerOperation(
            id=secrets.token_urlsafe(18), created_at=utc_now(), reseller_id=reseller.key.id,
            action="create", amount=token_limit, state="pending",
        )
        await operation_store.add(operation)
        try:
            api_key = await upstream_call_with_backoff(lambda: client.create_key(name=name, token_limit=token_limit))
        except KeyServiceError as exc:
            if 400 <= exc.status_code < 500 and exc.status_code != 429:
                await reseller.store.release_rejected_reservation(reseller.key.id, token_limit)
                await operation_store.update(operation.id, state="rejected", detail=exc.detail)
                return await reseller_response(request, reseller=reseller, locale=locale, error=reseller_message(locale, "service_unavailable"), status_code=502)
            await operation_store.update(operation.id, state="unknown", detail=exc.detail)
            return await reseller_response(request, reseller=reseller, locale=locale, error=reseller_message(locale, "topup_unknown"), status_code=502)
        except RuntimeError as exc:
            await operation_store.update(operation.id, state="unknown", detail=str(exc))
            return await reseller_response(request, reseller=reseller, locale=locale, error=reseller_message(locale, "topup_unknown"), status_code=502)
        all_codes = {stored.key.access_code for stored in await token_stores.list_all()} | {
            stored.key.access_code for stored in await reseller_key_stores.list_all()
        }
        child = TokenKey(
            id=max((key.id for key in await owner_store.list()), default=0) + 1,
            created_at=utc_now(), access_code=generate_access_code(all_codes), api_key=api_key,
            service=service, name=name, token_limit=token_limit, reseller_id=reseller.key.id,
        )
        await owner_store.add_many([child])
        await operation_store.update(operation.id, state="confirmed", child_key_id=child.id)
        current = await reseller_from_request(request)
        return await reseller_response(request, reseller=current, locale=locale, notice=reseller_message(locale, "created"))

    async def reseller_top_up_child(request: Request, key_id: int) -> HTMLResponse:
        form = await read_form(request)
        locale = reseller_locale(form.get("lang"))
        reseller = await reseller_from_request(request)
        if reseller is None:
            return await reseller_response(request, locale=locale, error=reseller_message(locale, "session_expired"), status_code=401)
        if not valid_csrf(request, form, TOKENS_RESELLER_CSRF_COOKIE):
            return RedirectResponse(url=f"/ai/tokens/reselling?lang={locale}", status_code=303)
        additional_tokens = positive_int(form.get("additional_tokens", ""))
        owner_store = token_stores.for_owner(reseller.owner_index)
        client = owner_client(reseller.owner_index)
        operation_store = operation_store_for_owner(reseller.owner_index)
        child = await owner_store.get(key_id) if owner_store is not None else None
        if child is None or child.reseller_id != reseller.key.id:
            return await reseller_response(request, reseller=reseller, locale=locale, error=reseller_message(locale, "not_found"), status_code=404)
        if additional_tokens is None or additional_tokens < 1:
            return await reseller_response(request, reseller=reseller, locale=locale, error=reseller_message(locale, "topup_invalid"), status_code=400)
        if client is None or operation_store is None:
            return await reseller_response(request, reseller=reseller, locale=locale, error=reseller_message(locale, "topup_unavailable"), status_code=503)
        reserved = await reseller.store.reserve(reseller.key.id, additional_tokens)
        if reserved is None:
            return await reseller_response(request, reseller=reseller, locale=locale, error=reseller_message(locale, "insufficient"), status_code=409)
        operation = ResellerOperation(
            id=secrets.token_urlsafe(18), created_at=utc_now(), reseller_id=reseller.key.id,
            action="top_up", amount=additional_tokens, state="pending", child_key_id=child.id,
        )
        await operation_store.add(operation)
        try:
            await upstream_call_with_backoff(lambda: client.add_tokens(api_key=child.api_key, additional_tokens=additional_tokens, active=child.active))
        except KeyServiceError as exc:
            if 400 <= exc.status_code < 500 and exc.status_code != 429:
                await reseller.store.release_rejected_reservation(reseller.key.id, additional_tokens)
                await operation_store.update(operation.id, state="rejected", detail=exc.detail)
                return await reseller_response(request, reseller=reseller, locale=locale, error=reseller_message(locale, "topup_rejected"), status_code=502)
            await operation_store.update(operation.id, state="unknown", detail=exc.detail)
            return await reseller_response(request, reseller=reseller, locale=locale, error=reseller_message(locale, "topup_unknown"), status_code=502)
        except RuntimeError as exc:
            await operation_store.update(operation.id, state="unknown", detail=str(exc))
            return await reseller_response(request, reseller=reseller, locale=locale, error=reseller_message(locale, "topup_unknown"), status_code=502)
        updated = replace(child, token_limit=child.token_limit + additional_tokens)
        await owner_store.update(child.id, updated)
        await operation_store.update(operation.id, state="confirmed")
        current = await reseller_from_request(request)
        return await reseller_response(request, reseller=current, locale=locale, notice=reseller_message(locale, "topup_success"))

    async def reseller_toggle_child(request: Request, key_id: int) -> HTMLResponse:
        form = await read_form(request)
        locale = reseller_locale(form.get("lang"))
        reseller = await reseller_from_request(request)
        if reseller is None:
            return await reseller_response(request, locale=locale, error=reseller_message(locale, "session_expired"), status_code=401)
        if not valid_csrf(request, form, TOKENS_RESELLER_CSRF_COOKIE):
            return RedirectResponse(url=f"/ai/tokens/reselling?lang={locale}", status_code=303)
        owner_store = token_stores.for_owner(reseller.owner_index)
        client = owner_client(reseller.owner_index)
        child = await owner_store.get(key_id) if owner_store is not None else None
        if child is None or child.reseller_id != reseller.key.id:
            return await reseller_response(request, reseller=reseller, locale=locale, error=reseller_message(locale, "not_found"), status_code=404)
        if client is None:
            return await reseller_response(request, reseller=reseller, locale=locale, error=reseller_message(locale, "state_unavailable"), status_code=503)
        target_active = not child.active
        try:
            await upstream_call_with_backoff(
                lambda: client.set_key_active(api_key=child.api_key, active=target_active),
                retry_server_errors=True,
            )
        except RuntimeError as exc:
            log_key_state_failure(owner_index=reseller.owner_index, key_id=child.id, error=exc)
            return await reseller_response(request, reseller=reseller, locale=locale, error=reseller_message(locale, "state_error"), status_code=502)
        await owner_store.update(child.id, replace(child, active=target_active))
        current = await reseller_from_request(request)
        return await reseller_response(
            request, reseller=current, locale=locale,
            notice=reseller_message(locale, "unfrozen_notice" if target_active else "frozen_notice"),
        )

    async def admin_update(request: Request, key_id: int) -> HTMLResponse:
        form = await read_form(request)
        owner_index = admin_owner(request)
        owner_store = token_stores.for_owner(owner_index) if owner_index is not None else None
        if owner_store is None:
            return await admin_response(request, error="Сессия администратора завершена.", status_code=401)
        if not valid_csrf(request, form, TOKENS_ADMIN_CSRF_COOKIE):
            return RedirectResponse(url="/ai/tokens/adm", status_code=303)
        current = await owner_store.get(key_id)
        if current is None:
            return await admin_response(request, error="Ключ не найден.", status_code=404)
        try:
            all_keys = [stored.key for stored in await token_stores.list_all()]
            updated = record_from_admin_form(form, current, all_keys)
        except ValueError as exc:
            return await admin_response(request, error=str(exc), status_code=400)
        await owner_store.update(key_id, updated)
        return await admin_response(request, notice=f"Ключ #{key_id} обновлен.")

    async def admin_toggle_reseller(request: Request, reseller_id: int) -> HTMLResponse:
        """Freeze/unfreeze a local reseller and mirror it to every child.

        Local state changes only after the upstream action for each child has
        succeeded. A later retry can safely repeat ``active=False/True``.
        """
        form = await read_form(request)
        owner_index = admin_owner(request)
        reseller_store = reseller_key_stores.for_owner(owner_index) if owner_index is not None else None
        owner_store = token_stores.for_owner(owner_index) if owner_index is not None else None
        client = owner_client(owner_index) if owner_index is not None else None
        if reseller_store is None or owner_store is None:
            return await admin_response(request, error="Сессия администратора завершена.", status_code=401)
        if not valid_csrf(request, form, TOKENS_ADMIN_CSRF_COOKIE):
            return RedirectResponse(url="/ai/tokens/adm", status_code=303)
        reseller = await reseller_store.get(reseller_id)
        if reseller is None:
            return await admin_response(request, error="Реселлерский ключ не найден.", status_code=404)
        target_active = not reseller.active
        children = [key for key in await owner_store.list() if key.reseller_id == reseller.id]
        if children and client is None:
            return await admin_response(request, error="Не настроен основной API-ключ администратора.", status_code=503)
        for child in children:
            if child.active == target_active:
                continue
            try:
                await upstream_call_with_backoff(
                    lambda child=child: client.set_key_active(api_key=child.api_key, active=target_active),
                    retry_server_errors=True,
                )
            except RuntimeError as exc:
                log_key_state_failure(owner_index=owner_index, key_id=child.id, error=exc)
                return await admin_response(
                    request,
                    error=f"Каскадная смена статуса остановлена на производном ключе #{child.id}. Повторите операцию.",
                    status_code=502,
                )
            await owner_store.update(child.id, replace(child, active=target_active))
        await reseller_store.update(reseller.id, replace(reseller, active=target_active))
        return await admin_response(
            request,
            notice=("Реселлер и все производные ключи разморожены." if target_active else "Реселлер и все производные ключи заморожены."),
        )

    async def admin_delete(request: Request, key_id: int) -> HTMLResponse:
        form = await read_form(request)
        owner_index = admin_owner(request)
        owner_store = token_stores.for_owner(owner_index) if owner_index is not None else None
        if owner_store is None:
            return await admin_response(request, error="Сессия администратора завершена.", status_code=401)
        if not valid_csrf(request, form, TOKENS_ADMIN_CSRF_COOKIE):
            return RedirectResponse(url="/ai/tokens/adm", status_code=303)
        if not await owner_store.delete(key_id):
            return await admin_response(request, error="Ключ не найден.", status_code=404)
        return await admin_response(request, notice=f"Ключ #{key_id} удален.")

    async def admin_toggle_freeze(request: Request, key_id: int) -> HTMLResponse:
        """Mirror the upstream active flag before changing the local record."""
        form = await read_form(request)
        owner_index = admin_owner(request)
        owner_store = token_stores.for_owner(owner_index) if owner_index is not None else None
        if owner_store is None:
            return await admin_response(request, error="Сессия администратора завершена.", status_code=401)
        if not valid_csrf(request, form, TOKENS_ADMIN_CSRF_COOKIE):
            return RedirectResponse(url="/ai/tokens/adm", status_code=303)
        current = await owner_store.get(key_id)
        if current is None:
            return await admin_response(request, error="Ключ не найден.", status_code=404)
        client = owner_client(owner_index)
        if client is None:
            return await admin_response(
                request,
                error="Не настроен основной API-ключ администратора.",
                status_code=503,
            )
        target_active = not current.active
        try:
            # State changes are idempotent upstream, so transient throttling
            # and gateway failures can be retried without changing a token
            # limit or risking a duplicate allocation.
            await upstream_call_with_backoff(
                lambda: client.set_key_active(api_key=current.api_key, active=target_active),
                retry_server_errors=True,
            )
        except (RuntimeError, ValueError) as exc:
            log_key_state_failure(owner_index=owner_index, key_id=key_id, error=exc)
            return await admin_response(
                request,
                error=f"Не удалось {'разморозить' if target_active else 'заморозить'} ключ #{key_id}.",
                status_code=502,
            )
        await owner_store.update(key_id, replace(current, active=target_active))
        return await admin_response(
            request,
            notice=f"Ключ #{key_id} {'разморожен' if target_active else 'заморожен'}.",
        )

    app.add_api_route("/ai/tokens", page, methods=["GET"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens", activate, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/balance", balance, methods=["GET"], response_model=None)
    app.add_api_route("/ai/tokens/logs/export", export_logs, methods=["GET"], response_model=None)
    app.add_api_route("/ai/tokens/bonus", claim_bonus, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/freeze", toggle_user_key_freeze, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/adm", admin_page, methods=["GET"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/adm/login", admin_login, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/adm/logout", admin_logout, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/adm/create", admin_create, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/adm/promos/create", admin_create_promos, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/adm/{key_id}/update", admin_update, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/adm/{key_id}/freeze", admin_toggle_freeze, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/adm/{key_id}/delete", admin_delete, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/adm/resellers/{reseller_id}/freeze", admin_toggle_reseller, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/reselling", reseller_page, methods=["GET"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/reselling/login", reseller_login, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/reselling/logout", reseller_logout, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/reselling/regenerate", reseller_regenerate_access, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/reselling/create", reseller_create_child, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/reselling/{key_id}/top-up", reseller_top_up_child, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/reselling/{key_id}/freeze", reseller_toggle_child, methods=["POST"], response_class=HTMLResponse, response_model=None)


def positive_int(value: str) -> int | None:
    try:
        parsed = int(value.strip().replace(" ", ""))
    except (TypeError, ValueError):
        return None
    return parsed


def exhausted_message(key: TokenKey, locale: str = "ru") -> str:
    text = TOKEN_TEXT[token_locale(locale)]
    return text["exhausted"].format(date=format_datetime(key.exhausted_at or key.activated_at))

def input_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    return to_moscow(value).strftime("%Y-%m-%dT%H:%M")


def record_from_admin_form(form: dict[str, str], current: TokenKey, all_keys: list[TokenKey]) -> TokenKey:
    service = form.get("service", "")
    # Reseller masters are local records with their own ledger and editing
    # form. They must never be converted from/to ordinary upstream keys.
    if service not in RESELLER_SERVICE_OPTIONS:
        raise ValueError("Выберите доступный сервис.")
    name = form.get("name", "").strip()
    api_key = form.get("api_key", "").strip()
    access_code = normalize_access_code(form.get("access_code", ""))
    token_limit = positive_int(form.get("token_limit", ""))
    used_tokens = positive_int(form.get("used_tokens", ""))
    if not name:
        raise ValueError("Название ключа не может быть пустым.")
    if not api_key:
        raise ValueError("API key не может быть пустым.")
    if len(access_code) != 20 or any(char not in TOKEN_CODE_ALPHABET for char in access_code):
        raise ValueError("Ключ доступа должен состоять из 20 заглавных латинских букв и цифр.")
    if any(key.id != current.id and key.access_code == access_code for key in all_keys):
        raise ValueError("Такой ключ доступа уже существует.")
    if token_limit is None or token_limit < 1:
        raise ValueError("Количество токенов должно быть положительным целым числом.")
    if used_tokens is None or not 0 <= used_tokens <= token_limit:
        raise ValueError("Использовано должно быть от 0 до лимита токенов.")
    try:
        created_at = parse_admin_datetime(form.get("created_at", ""))
        activated_raw = form.get("activated_at", "").strip()
        activated_at = parse_admin_datetime(activated_raw) if activated_raw else None
        exhausted_raw = form.get("exhausted_at", "").strip()
        exhausted_at = parse_admin_datetime(exhausted_raw) if exhausted_raw else None
    except (TypeError, ValueError):
        raise ValueError("Введите корректные даты.") from None
    if used_tokens >= token_limit:
        exhausted_at = exhausted_at or current.exhausted_at or utc_now()
    else:
        exhausted_at = None
    return TokenKey(
        id=current.id,
        created_at=created_at,
        access_code=access_code,
        api_key=api_key,
        service=service,
        name=name,
        token_limit=token_limit,
        used_tokens=used_tokens,
        activated_at=activated_at,
        exhausted_at=exhausted_at,
        active=current.active,
    )


def render_tokens_page(
    *,
    csrf_token: str,
    locale: str = "ru",
    key: TokenKey | None = None,
    error: str = "",
    notice: str = "",
    submitted_code: str = "",
) -> HTMLResponse:
    locale = token_locale(locale)
    text = TOKEN_TEXT[locale]
    flash = f"<div class='flash error'>{html.escape(error)}</div>" if error else (f"<div class='flash success'>{html.escape(notice)}</div>" if notice else "")
    info = render_key_information(key, csrf_token=csrf_token, locale=locale) if key is not None else ""
    instructions = render_instructions(key, locale) if key is not None else ""
    faq = render_faq(locale)
    opposite_locale = "ru" if locale == "en" else "en"
    faq_label = "Help / errors" if locale == "en" else "Ответы на вопросы / ошибки"
    language_url = f"/ai/tokens?lang={opposite_locale}"
    if submitted_code:
        language_url += f"&key={quote(submitted_code, safe='')}"
    content = f"""
    <main class='page'>
      <nav class='top-links' aria-label='Page links'><a href='#faq'>{html.escape(faq_label)}</a><a href='{html.escape(language_url, quote=True)}'>{html.escape(text['switch'])}</a></nav>
      <section class='card'><h2>{html.escape(text['activation'])}</h2>{flash}
        <form method='post' action='/ai/tokens' class='activation-form'>
          <input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'><input type='hidden' name='lang' value='{locale}'>
          <label for='access_code'>{html.escape(text['access'])}:</label>
          <input id='access_code' name='access_code' value='{html.escape(submitted_code, quote=True)}' autocomplete='off' spellcheck='false' required>
          <p class='hint'>{html.escape(text['access_hint'])}</p><button class='primary wide' type='submit'>{html.escape(text['activate'])}</button>
        </form>
      </section>{info}{instructions}{faq}
    </main>"""
    return HTMLResponse(render_layout(text['title'], content, locale))


def render_key_information(key: TokenKey, *, csrf_token: str, locale: str = "ru") -> str:
    text = TOKEN_TEXT[token_locale(locale)]
    status = text["frozen"] if not key.active else (text["exhausted_status"].format(date=format_datetime(key.exhausted_at or key.activated_at)) if key.is_exhausted else (text["activated_status"].format(date=format_datetime(key.activated_at)) if key.activated_at else text["not_activated"]))
    freeze_label = text["freeze"] if key.active else text["unfreeze"]
    freeze_class = "freeze-key frozen" if not key.active else "freeze-key"
    return f"""
    <section class='card info-card'><h2>{html.escape(text['info'])}</h2><dl class='details'>
      <dt>{html.escape(text['service'])}</dt><dd>{html.escape(key.service.upper())}</dd>
      <dt>{html.escape(text['activated'])}</dt><dd>{format_datetime(key.activated_at)}</dd>
      <dt>{html.escape(text['limit'])}</dt><dd>{format_tokens(key.token_limit)}</dd>
      <dt>{html.escape(text['remaining'])}</dt><dd id='token-balance' data-separator='{html.escape(text['remaining_sep'], quote=True)}' data-fallback='{format_tokens(key.remaining_tokens)}'>{format_tokens(key.remaining_tokens)} {html.escape(text['remaining_sep'])} {format_tokens(key.token_limit)}</dd>
      <dt>{html.escape(text['status'])}</dt><dd>{html.escape(status)}</dd>
      <dt>{html.escape(text['api'])}</dt><dd><code class='api-key'>{html.escape(key.api_key)}</code></dd>
    </dl>
    <div class='info-actions'>
      <button class='bonus-button' type='button' id='get-bonus' aria-label='{html.escape(text['bonus'], quote=True)}'>
        <svg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round' class='lucide lucide-gift h-5 w-5' aria-hidden='true'><rect x='3' y='8' width='18' height='4' rx='1'></rect><path d='M12 8v13'></path><path d='M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7'></path><path d='M7.5 8a2.5 2.5 0 0 1 0-5A4.8 8 0 0 1 12 8a4.8 8 0 0 1 4.5-5 2.5 2.5 0 0 1 0 5'></path></svg>
        <span>{html.escape(text['bonus'])}</span>
      </button>
      <form method='post' action='/ai/tokens/freeze' class='user-freeze-form'>
        <input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'>
        <input type='hidden' name='lang' value='{locale}'>
        <button class='{freeze_class}' type='submit'>{html.escape(freeze_label)}</button>
      </form>
    </div>
    <section class='bonus-claim' id='bonus-claim' hidden>
      <p>{html.escape(text['bonus_instructions'])}</p>
      <form method='post' action='/ai/tokens/bonus'>
        <input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'>
        <input type='hidden' name='lang' value='{locale}'>
        <label for='promo-code'>{html.escape(text['promo_code'])}</label>
        <input id='promo-code' name='promo_code' autocomplete='off' autocapitalize='characters' required>
        <button class='bonus-button wide' type='submit'>{html.escape(text['claim_bonus'])}</button>
      </form>
    </section></section>"""


INSTRUCTION_GROUPS = (
    ("codex", "Codex", ("VS Code", "App", "CLI")),
    ("claude", "Claude", ("Claude Code CLI", "Claude App")),
    ("grok", "Grok", ("Grok Build",)),
    ("other", "Другие", ("Hermes Desktop", "Cheap Code", "Kimi Code CLI", "ZCode", "Pi", "OpenCode", "Cursor")),
)
INSTRUCTION_SYSTEMS = ("Windows", "macOS", "Linux")
INSTRUCTION_SYSTEMS_BY_APP = {
    "VS Code": ("Windows", "macOS", "Linux"), "App": ("Windows", "macOS"), "CLI": ("Windows", "macOS", "Linux"),
    "Claude Code CLI": ("Windows", "macOS", "Linux"), "Claude App": ("Windows", "macOS"),
    "Hermes Desktop": ("Windows", "macOS", "Linux"), "Cheap Code": ("Windows", "macOS", "Linux"),
    "Grok Build": ("Windows", "macOS", "Linux"), "Kimi Code CLI": ("Windows", "macOS", "Linux"),
    "ZCode": ("Windows", "macOS", "Linux"), "Pi": ("Windows", "macOS", "Linux"),
    "OpenCode": ("Windows", "macOS", "Linux"), "Cursor": ("Windows", "macOS", "Linux"),
}
INSTRUCTION_ENDPOINTS = {
    "VS Code": {"Windows": "iw", "macOS": "ivm", "Linux": "i"},
    "App": {"Windows": "icw", "macOS": "im", "Linux": "i"},
    "CLI": {"Windows": "icw", "macOS": "icm", "Linux": "i"},
    "Claude Code CLI": {"Windows": "cw", "macOS": "cm", "Linux": "cl"},
    "Claude App": {"Windows": "iclaudew", "macOS": "iclaudem", "Linux": "i"},
    "Hermes Desktop": {"Windows": "ihermesw", "macOS": "ihermesm", "Linux": "ihermesl"},
    "Cheap Code": {"Windows": "igw", "macOS": "igm", "Linux": "igl"},
    "Grok Build": {"Windows": "igw", "macOS": "igm", "Linux": "igl"},
    "Kimi Code CLI": {"Windows": "ikw", "macOS": "ikm", "Linux": "ikl"},
    "ZCode": {"Windows": "izw", "macOS": "izm", "Linux": "izl"},
    "Pi": {"Windows": "ipw", "macOS": "ipm", "Linux": "ipl"},
    "OpenCode": {"Windows": "iow", "macOS": "iom", "Linux": "iol"},
    "Cursor": {"Windows": "icrw", "macOS": "icrm", "Linux": "icr"},
}
INSTRUCTION_REMOVE_ENDPOINTS = {
    "VS Code": {"Windows": "uc?shell=powershell", "macOS": "uc?shell=bash", "Linux": "uc?shell=bash"},
    "App": {"Windows": "uc?shell=powershell", "macOS": "uc?shell=bash"},
    "CLI": {"Windows": "uc?shell=powershell", "macOS": "uc?shell=bash", "Linux": "uc?shell=bash"},
    "Claude Code CLI": {"Windows": "crw", "macOS": "crm", "Linux": "crl"},
    "Claude App": {"Windows": "rclaudew", "macOS": "rclaudem"},
    "Hermes Desktop": {"Windows": "rhermesw", "macOS": "rhermesm", "Linux": "rhermesl"},
    "Cheap Code": {"Windows": "rcheapcode-windows", "macOS": "rcheapcode", "Linux": "rcheapcode"},
    "Grok Build": {"Windows": "rgw", "macOS": "rgm", "Linux": "rgl"},
    "Kimi Code CLI": {"Windows": "rkw", "macOS": "rkm", "Linux": "rkl"},
    "ZCode": {"Windows": "rzw", "macOS": "rzm", "Linux": "rzl"},
    "Pi": {"Windows": "rpw", "macOS": "rpm", "Linux": "rpl"},
    "OpenCode": {"Windows": "row", "macOS": "rom", "Linux": "rol"},
    "Cursor": {"Windows": "rcrw", "macOS": "rcrm", "Linux": "rcrl"},
}


def instruction_slug(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")


def instruction_base_url(application: str) -> str:
    """Return the documented proxy endpoint for a client integration."""
    return "https://starimg.ru/ai/common"


def instruction_command(application: str, system: str, api_key: str, locale: str = "ru") -> str:
    endpoint = INSTRUCTION_ENDPOINTS[application][system]
    url = f"{instruction_base_url(application)}/{endpoint}"
    if application == "Claude Code CLI":
        if system == "Windows":
            return f"$h=@{{Authorization='Bearer {api_key}'}}; iex(irm -Headers $h '{url}')"
        return f"bash <(curl -fsSL -H 'Authorization: Bearer {api_key}' '{url}')"
    if application == "Cheap Code":
        return "npm install -g @cheapcode/cli@latest"
    if application == "Grok Build":
        if system == "Windows":
            return f"$env:CVC_API_KEY='{api_key}'; iex(irm '{url}')"
        return f"bash <(curl -fsSL '{url}') '{api_key}'"
    if system == "Windows":
        return f"$env:CVC_API_KEY='{api_key}'; iex(irm '{url}')"
    return f"bash <(curl -fsSL '{url}') '{api_key}'"


def instruction_remove_command(application: str, system: str) -> str | None:
    endpoint = INSTRUCTION_REMOVE_ENDPOINTS.get(application, {}).get(system)
    if not endpoint:
        return None
    url = f"{instruction_base_url(application)}/{endpoint}"
    if system == "Windows":
        return f"iex(irm '{url}')"
    return f"bash <(curl -fsSL '{url}')"




def manual_instruction_note(application: str, system: str, locale: str = "ru") -> str:
    """Return the exact per-client note shown before manual configuration."""
    notes_ru = {
        "VS Code": "Создай папку ~/.codex, скачай config.toml, auth.json и .env, положи их в эту папку и полностью перезапусти Codex. auth.json и .env содержат ключ — не публикуй их.",
        "App": "Создай папку ~/.codex, скачай config.toml, auth.json и .env, положи их в эту папку и полностью перезапусти Codex. auth.json и .env содержат ключ — не публикуй их.",
        "CLI": "Создай папку ~/.codex, скачай config.toml, auth.json и .env, положи их в эту папку и полностью перезапусти Codex. auth.json и .env содержат ключ — не публикуй их.",
        "Claude Code CLI": "Добавь объект env в ~/.claude/settings.json, сохранив остальные существующие поля.",
        "Claude App": "Основные значения Gateway-профиля. Claude App сам оставляет совместимые Anthropic-модели из динамического каталога; активный профиль configLibrary установщик обновляет автоматически.",
        "Hermes Desktop": "Скрипт пишет эти значения в ~/.hermes/config.yaml и сохраняет ключ в ~/.hermes/.env. Старые Hermes JSON-файлы только бэкапятся.",
        "Cheap Code": "Если вход через сайт не подходит, запусти cheapcode auth login --key или создай профиль вручную.",
        "Grok Build": "Пример двух секций с effort-меню. api_backend = chat_completions обязателен: без него встроенные Grok-модели наследуют Responses backend и ошибочно попадают в Codex-пул.",
        "Kimi Code CLI": "Минимальный пример для одной модели в ~/.kimi-code/config.toml. Сохрани остальные существующие секции файла. Ключ хранится открытым текстом, поэтому оставь права файла 600. Скрипт удобнее: он импортирует весь каталог через штатный реестр Kimi.",
        "ZCode": "Добавь provider в ~/.zcode/v2/config.json, сохранив встроенные и пользовательские секции. Автоматический скрипт безопаснее: он проверяет, что ZCode закрыт, делает backup и переносит весь доступный ключу каталог с корректными context/output limits.",
        "Pi": "Минимальный пример для одной модели. Автоматический скрипт предпочтительнее: он переносит весь доступный ключу каталог, context limits, vision и reasoning-уровни.",
        "OpenCode": "Ручной путь и файл, если хочешь проверить или поставить без установщика.",
        "Cursor": "Если скрипт не подходит для твоей версии Cursor, эти значения можно вставить вручную в Settings -> Models.",
    }
    notes_en = {
        "VS Code": "Create ~/.codex, download config.toml, auth.json, and .env, place them in that folder, then fully restart Codex. auth.json and .env contain the key — do not share them.",
        "App": "Create ~/.codex, download config.toml, auth.json, and .env, place them in that folder, then fully restart Codex. auth.json and .env contain the key — do not share them.",
        "CLI": "Create ~/.codex, download config.toml, auth.json, and .env, place them in that folder, then fully restart Codex. auth.json and .env contain the key — do not share them.",
        "Claude Code CLI": "Add the env object to ~/.claude/settings.json while preserving the other existing fields.",
        "Claude App": "Core Gateway profile values. Claude App keeps compatible Anthropic models from the dynamic catalog; the installer updates the active configLibrary profile automatically.",
        "Hermes Desktop": "The script writes these values to ~/.hermes/config.yaml and stores the key in ~/.hermes/.env. Legacy Hermes JSON files are backed up only.",
        "Cheap Code": "If website login is not suitable, run cheapcode auth login --key or create the profile manually.",
        "Grok Build": "Example with two sections and an effort menu. api_backend = chat_completions is required; otherwise built-in Grok models inherit the Responses backend and are incorrectly routed to the Codex pool.",
        "Kimi Code CLI": "Minimal example for one model in ~/.kimi-code/config.toml. Keep the other existing sections in the file. The key is stored in plain text, so keep file permissions at 600. The script is more convenient because it imports the full catalog through Kimi native registry.",
        "ZCode": "Add the provider to ~/.zcode/v2/config.json while preserving built-in and custom sections. The automatic script is safer: it checks that ZCode is closed, makes a backup, and imports the full catalog available to the key with correct context/output limits.",
        "Pi": "Minimal example for one model. The automatic script is preferred because it imports the full catalog available to the key, context limits, vision, and reasoning levels.",
        "OpenCode": "Manual path and file if you want to check it or install without the installer.",
        "Cursor": "If the script does not suit your Cursor version, you can enter these values manually in Settings -> Models.",
    }
    return (notes_en if token_locale(locale) == "en" else notes_ru)[application]

def manual_download_buttons(application: str, api_key: str, locale: str = "ru") -> str:
    """Download ready-to-place Codex files without exposing the upstream domain."""
    if application not in {"VS Code", "App", "CLI"}:
        return ""
    base_url = f"{instruction_base_url(application)}/v1"
    config = f'''model = "gpt-5.6-terra"
model_reasoning_effort = "high"
model_provider = "starimg"
web_search = "live"

[model_providers.starimg]
name = "Starimg AI"
base_url = "{base_url}"
wire_api = "responses"
requires_openai_auth = true
'''
    auth = json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": api_key}, ensure_ascii=False, indent=2) + "\n"
    env = f"CVC_API_KEY={api_key}\nOPENAI_API_KEY={api_key}\n"
    labels = ("Download config.toml", "Download auth.json", "Download .env") if token_locale(locale) == "en" else ("Скачать config.toml", "Скачать auth.json", "Скачать .env")
    files = (("config.toml", "text/plain", config), ("auth.json", "application/json", auth), (".env", "text/plain", env))
    links = "".join(
        f"<a class='download-file' href='data:{mime};charset=utf-8,{quote(content)}' download='{filename}'>{html.escape(label)}</a>"
        for (filename, mime, content), label in zip(files, labels, strict=True)
    )
    return f"<div class='manual-downloads'>{links}</div>"

def manual_instruction_command(application: str, system: str, api_key: str) -> str:
    """Render a manual setup based on the supplied CVC instruction document."""
    base_url = instruction_base_url(application)
    if application in {"VS Code", "App", "CLI"}:
        return f"""# Windows PowerShell
$d=Join-Path $HOME '.codex'; $p=Join-Path $d '.env'; New-Item -ItemType Directory -Force $d | Out-Null; $lines=if(Test-Path $p){{@(Get-Content $p | Where-Object {{ $_ -notmatch '^ *(CVC_API_KEY|OPENAI_API_KEY) *=' }})}}else{{@()}}; [IO.File]::WriteAllLines($p,@($lines)+'CVC_API_KEY={api_key}'+'OPENAI_API_KEY={api_key}')

# macOS / Linux
mkdir -p ~/.codex; {{ grep -Ev '^(CVC_API_KEY|OPENAI_API_KEY)=' ~/.codex/.env 2>/dev/null || true; printf 'CVC_API_KEY=%s\nOPENAI_API_KEY=%s\n' '{api_key}' '{api_key}'; }} > ~/.codex/.env.tmp; mv ~/.codex/.env.tmp ~/.codex/.env; chmod 600 ~/.codex/.env"""
    if application == "Claude Code CLI":
        return f'''# ~/.claude/settings.json
{{
  "env": {{
    "ANTHROPIC_BASE_URL": "https://starimg.ru/ai/common",
    "ANTHROPIC_AUTH_TOKEN": "{api_key}"
  }}
}}'''
    if application == "Claude App":
        return f'''# %LOCALAPPDATA%\\Claude-3p\\claude_desktop_config.json (Windows)
# ~/Library/Application Support/Claude-3p/claude_desktop_config.json (macOS)
{{
  "deploymentMode": "3p",
  "enterpriseConfig": {{
    "inferenceProvider": "gateway",
    "inferenceGatewayBaseUrl": "https://starimg.ru/ai/common",
    "inferenceGatewayApiKey": "{api_key}",
    "inferenceGatewayAuthScheme": "bearer",
    "modelDiscoveryEnabled": true
  }}
}}'''
    if application == "Hermes Desktop":
        return f'''# config.yaml
custom_providers:
  - name: "starimg"
    base_url: "https://starimg.ru/ai/common/v1"
    key_env: "CVC_API_KEY"
    api_mode: chat_completions

model:
  provider: "custom:starimg"
  default: "claude-sonnet-4-6"

# .env
CVC_API_KEY={api_key}
CHEAPCODE_API_KEY={api_key}'''
    if application == "Cheap Code":
        return f'''# ~/.cheapcode-cli/.cheapcode-profile.json
{{
  "profile": "openai",
  "env": {{
    "CHEAPCODE_USE_OPENAI": "1",
    "CVC_API_KEY": "{api_key}",
    "OPENAI_API_KEY": "{api_key}",
    "OPENAI_BASE_URL": "https://starimg.ru/ai/common/v1",
    "OPENAI_MODEL": "gpt-5.6-terra",
    "OPENAI_API_FORMAT": "responses",
    "CHEAPCODE_CLI_PROFILE": "1"
  }}
}}

# Then run:
cheapcode'''
    if application == "Grok Build":
        return f'''# ~/.grok/config.toml
[models]
default = "grok-4.6"
default_reasoning_effort = "xhigh"

[model."grok-4.6"]
model = "grok-4.6"
base_url = "https://starimg.ru/ai/common/v1"
name = "grok-4.6"
description = "xAI · Grok 4.6"
api_key = "{api_key}"
api_backend = "chat_completions"
context_window = 500000
supports_reasoning_effort = true
reasoning_effort = "xhigh"
reasoning_efforts = [
  {{ id = "low", value = "low", label = "Low Effort", description = "Quick reasoning with minimal overhead", default = false }},
  {{ id = "medium", value = "medium", label = "Medium Effort", description = "Balanced reasoning and implementation", default = false }},
  {{ id = "high", value = "high", label = "High Effort", description = "Deep reasoning with extensive implementation", default = false }},
  {{ id = "xhigh", value = "xhigh", label = "Extra High Effort", description = "Maximum reasoning depth", default = true }},
]

[model."grok-composer-2.5-fast"]
model = "composer-2.5-fast"
base_url = "https://starimg.ru/ai/common/v1"
name = "composer-2.5-fast"
description = "xAI · Composer 2.5 Fast"
api_key = "{api_key}"
api_backend = "chat_completions"
context_window = 200000'''
    if application == "Kimi Code CLI":
        return f'''# ~/.kimi-code/config.toml
default_model = "starimg/gpt-5.6-terra"

[providers.starimg]
type = "openai"
base_url = "{base_url}/v1"
api_key = "{api_key}"

[models."starimg/gpt-5.6-terra"]
provider = "starimg"
model = "gpt-5.6-terra"
max_context_size = 353000
max_input_size = 225000
capabilities = ["tool_use", "thinking", "image_in"]
display_name = "GPT 5.6 Terra"
support_efforts = ["none", "low", "medium", "high", "xhigh", "max"]
default_effort = "high"

[thinking]
enabled = true
effort = "high"
keep = "all"'''
    if application == "ZCode":
        return f'''# Merge only provider.starimg into ~/.zcode/v2/config.json.
{{
  "provider": {{
    "starimg": {{
      "name": "Starimg AI",
      "kind": "openai-compatible",
      "options": {{
        "apiKey": "{api_key}",
        "baseURL": "{base_url}/v1",
        "apiKeyRequired": true
      }},
      "models": {{
        "gpt-5.6-terra": {{
          "name": "GPT 5.6 Terra",
          "limit": {{ "context": 353000, "output": 128000 }},
          "modalities": {{ "input": ["text", "image"], "output": ["text"] }},
          "supportsTools": true,
          "supportsStructuredOutput": true
        }}
      }},
      "enabled": true
    }}
  }}
}}'''
    if application == "Pi":
        return f'''# ~/.pi/agent/models.json
{{
  "providers": {{
    "starimg": {{
      "baseUrl": "{base_url}/v1",
      "api": "openai-completions",
      "authHeader": true,
      "models": [{{ "id": "gpt-5.6-terra", "name": "GPT 5.6 Terra", "reasoning": true, "input": ["text", "image"], "contextWindow": 353000, "maxTokens": 128000 }}]
    }}
  }}
}}

# ~/.pi/agent/auth.json
{{ "starimg": {{ "type": "api_key", "key": "{api_key}" }} }}

# ~/.pi/agent/settings.json
{{ "defaultProvider": "starimg", "defaultModel": "gpt-5.6-terra" }}'''
    if application == "OpenCode":
        return f'''File: https://starimg.ru/ai/common/downloads/opencode.jsonc
Configuration: ~/.config/opencode/opencode.json

The script creates a backup, downloads the configuration, inserts the API key, sorts models, and selects starimg/gpt-5.6-terra.
apiKey: {api_key}
baseURL: https://starimg.ru/ai/common/v1
model: starimg/gpt-5.6-terra'''
    if application == "Cursor":
        return f'''Base URL : https://starimg.ru/ai/common/v1
API key  : {api_key}
Model    : gpt-5.6-terra-cursor

Alias rule: model id + "-cursor"

Examples:
gpt-5.6-terra-cursor = GPT 5.6 Terra
grok-4.5-cursor = Grok 4.5
grok-4.6-cursor = Grok 4.6
composer-2.5-fast-cursor = Composer 2.5 Fast'''
    raise ValueError(f"Unsupported instruction application: {application}")


def instruction_steps(application: str, system: str, locale: str = "ru") -> list[str]:
    text = TOKEN_TEXT[token_locale(locale)]
    if application == "Grok Build":
        opener = text["grok_open_windows"] if system == "Windows" else text["grok_open_other"]
        return [opener, text["grok_run"], text["grok_restart"]]
    shell = text["shell_windows"] if system == "Windows" else text["shell_other"]
    return [text["open"].format(shell=shell), text["run"], text["restart"]]


def default_instruction_choice(service: str) -> tuple[str, str]:
    normalized = service.strip().lower()
    if normalized == "grok":
        return "grok", "Grok Build"
    if normalized in {"openai", "codex"}:
        return "codex", "CLI"
    if normalized == "claude":
        return "claude", "Claude Code CLI"
    return "other", "Hermes Desktop"


def render_instructions(key: TokenKey, locale: str = "ru") -> str:
    text = TOKEN_TEXT[token_locale(locale)]
    default_provider, default_app = default_instruction_choice(key.service)
    script_label = text["script"]
    manual_label = text["manual_mode"]
    manual_heading = text["manual_heading"]
    remove_label = text["remove_integration"]
    remove_help = text["remove_integration_hint"]
    cards: list[str] = []
    for _, _, applications in INSTRUCTION_GROUPS:
        for application in applications:
            for system in INSTRUCTION_SYSTEMS_BY_APP[application]:
                slug = f"{instruction_slug(application)}-{instruction_slug(system)}"
                command = html.escape(instruction_command(application, system, key.api_key, locale))
                manual_command = html.escape(manual_instruction_command(application, system, key.api_key))
                manual_note = html.escape(manual_instruction_note(application, system, locale))
                manual_downloads = manual_download_buttons(application, key.api_key, locale)
                description = text["manual"]
                steps = "".join(f"<li>{html.escape(step)}</li>" for step in instruction_steps(application, system, locale))
                remove_command = instruction_remove_command(application, system)
                remove_block = ""
                if remove_command:
                    remove_slug = f"{slug}-remove"
                    remove_block = f"""
                  <details class='remove-integration'>
                    <summary>{html.escape(remove_label)}</summary>
                    <p class='hint'>{html.escape(remove_help)}</p>
                    <pre id='instruction-script-{remove_slug}'>{html.escape(remove_command)}</pre>
                    <button type='button' class='secondary copy-instruction' data-copy-target='instruction-script-{remove_slug}' data-copy-label='{html.escape(text['copy'])}' data-copied-label='{html.escape(text['copied'])}'>{html.escape(text['copy'])}</button>
                  </details>"""
                cards.append(f"""
                <article class='instruction-card' id='instruction-card-{slug}' data-provider-app='{html.escape(application)}' data-instruction-system='{system}' hidden>
                  <h3>{html.escape(application)} · {html.escape(system)}</h3>
                  <p><strong>• {html.escape(text['selected'])}</strong> {html.escape(application)}<br><strong>• {html.escape(text['description'])}</strong> {html.escape(description)}<br><strong>• {html.escape(text['os'])}</strong> {html.escape(system)}</p>
                  <ol>{steps}</ol>
                  <div class='instruction-mode-tabs' role='tablist' aria-label='{html.escape(application)}'>
                    <button type='button' class='instruction-mode active' data-instruction-mode='script' aria-selected='true'>{html.escape(script_label)}</button>
                    <button type='button' class='instruction-mode' data-instruction-mode='manual' aria-selected='false'>{html.escape(manual_label)}</button>
                  </div>
                  <div class='instruction-mode-panel' data-instruction-mode-panel='script'>
                    <p class='warning'>{html.escape(text['warning'])}</p>
                    <pre id='instruction-script-{slug}'>{command}</pre>
                    <button type='button' class='primary copy-instruction' data-copy-target='instruction-script-{slug}' data-copy-label='{html.escape(text['copy'])}' data-copied-label='{html.escape(text['copied'])}'>{html.escape(text['copy'])}</button>
                  </div>
                  <div class='instruction-mode-panel' data-instruction-mode-panel='manual' hidden>
                    <p class='manual-heading'>{html.escape(manual_heading)}</p>
                    <p class='manual-note'>{manual_note}</p>{manual_downloads}
                    <pre id='instruction-manual-{slug}'>{manual_command}</pre>
                    <button type='button' class='primary copy-instruction' data-copy-target='instruction-manual-{slug}' data-copy-label='{html.escape(text['copy'])}' data-copied-label='{html.escape(text['copied'])}'>{html.escape(text['copy'])}</button>
                  </div>
                  {remove_block}
                </article>""")
    groups = "".join(
        f"<button type='button' class='choice {'active' if group_key == default_provider else ''}' data-instruction-provider='{group_key}'>{html.escape('Others' if group_key == 'other' and locale == 'en' else label)}</button>"
        for group_key, label, _ in INSTRUCTION_GROUPS
    )
    apps = "".join(
        f"<button type='button' class='choice {'active' if app == default_app else ''}' data-instruction-app='{html.escape(app)}' data-provider='{group_key}'>{html.escape(app)}</button>"
        for group_key, _, applications in INSTRUCTION_GROUPS for app in applications
    )
    systems = "".join(
        f"<button type='button' class='choice {'active' if system == 'Windows' else ''}' data-instruction-os='{system}'>{system}</button>"
        for system in INSTRUCTION_SYSTEMS
    )
    return f"""
    <section class='card instructions' data-default-provider='{html.escape(default_provider, quote=True)}' data-default-app='{html.escape(default_app, quote=True)}'>
      <h2>{html.escape(text['instructions'])}</h2>
      <p>{html.escape(text['instructions_intro'])}</p>
      <h3 class='step-title'>{html.escape(text['choose_service'])}</h3>
      <div class='choice-row' role='group' aria-label='{html.escape(text['choose_service'])}'>{groups}</div>
      <h3 class='step-title'>{html.escape(text['choose_app'])}</h3>
      <div class='choice-row instruction-apps' role='group' aria-label='{html.escape(text['choose_app'])}'>{apps}</div>
      <h3 class='step-title'>{html.escape(text['choose_os'])}</h3>
      <div class='choice-row' role='group' aria-label='{html.escape(text['choose_os'])}'>{systems}</div>
      <div class='selected-line'>• {html.escape(text['selected'])} <strong id='selected-method'>{html.escape(default_app)}</strong><br>• {html.escape(text['description'])} <strong id='selected-description'>{html.escape(text['manual'])}</strong><br>• {html.escape(text['os'])} <strong id='selected-os'>Windows</strong></div>
      <div id='instruction-content'>{''.join(cards)}</div>
    </section>
    """


def render_faq(locale: str = "ru") -> str:
    """Local support FAQ: excludes upstream billing and account-management topics."""
    locale = token_locale(locale)
    if locale == "en":
        title = "Help and common errors"
        intro = "Answers about connection, setup, and common application errors."
        items = (
            ("setup", "Which instruction should I choose?", "Choose the client you actually use: Codex for VS Code/Codex App/Codex CLI, Claude for Claude Code CLI/Claude App, and Others for Hermes, Cheap Code, Grok Build, Kimi, ZCode, Pi, OpenCode, Cursor, and similar clients. Then choose the application and operating system."),
            ("setup", "What is the difference between Codex, OpenAI, and Anthropic endpoints?", "Codex clients use the prepared Codex provider at /backend-api/codex. OpenAI-compatible clients use /v1. Claude Code CLI and other Anthropic-compatible clients use /anthropic/v1. The same API key is used in every case; the selected instruction inserts the right endpoint."),
            ("setup", "What is the difference between Script and Manually?", "Script applies the prepared configuration automatically. Manually shows the configuration values and file locations so that you can add them yourself. In both modes the active API key is already inserted."),
            ("setup", "Will setup remove my chats or other providers?", "The setup and removal commands are intended to change only the Starimg AI integration. Close the selected client before setup, and use the hidden Remove integration block when you need to undo it."),
            ("errors", "The client asks to sign in or does not see the API key", "Fully close and reopen the client. For remote development, SSH, VPS, or server workspaces, run the setup on the machine where the client or extension host actually runs."),
            ("errors", "401 / Incorrect API key / requests go to api.openai.com", "The client is still using its direct provider configuration. Run setup on the correct machine, restart the client, then use Remove integration once and run setup again if an old configuration remains."),
            ("errors", "Model not found", "Check the exact model ID supported by your selected client. Codex and ZCode use the regular ID, OpenCode and Kimi use starimg/<model>, while Cursor uses <model>-cursor. Restart the client after changing model or key."),
            ("errors", "What do 429, 502, 503, 504, 524, or capacity_unavailable mean?", "429 is a rate limit: wait and reduce parallel requests. 502/503/capacity_unavailable are temporary upstream availability errors. For 504/524 wait briefly, retry once with backoff, and if needed choose another model."),
            ("errors", "Claude Code is outdated or continually asks yes/no", "Run claude update and restart the CLI for an unsupported version. Yes/no prompts are local Claude permissions, not API-key errors. Claude App and Claude Code CLI are configured separately."),
        )
        placeholder, empty = "Search questions…", "No matching answers."
    else:
        title = "Ответы на вопросы и ошибки"
        intro = "Подключение, настройка и частые ошибки приложений."
        labels = ("Все", "Подключение", "Ошибки")
        items = (
            ("setup", "Чем отличаются Codex, OpenAI и Anthropic endpoint'ы?", "Codex-клиенты используют готовый Codex provider с /backend-api/codex. OpenAI-совместимые клиенты используют Base URL с /v1. Claude Code CLI и другие Anthropic-совместимые клиенты используют /anthropic/v1. Ключ во всех случаях один; нужный endpoint уже подставляет выбранная инструкция."),
            ("setup", "Что выбрать в инструкции: Codex, Claude или «Другие»?", "Выбирайте по клиенту, а не по названию модели: Codex — для VS Code, Codex App и Codex CLI; Claude — для Claude Code CLI и Claude App; «Другие» — для Hermes, Cheap Code, Grok Build, Kimi, ZCode, Pi, OpenCode, Cursor и похожих клиентов. Затем выберите приложение и ОС."),
            ("setup", "Чем отличаются «Скрипт» и «Вручную»?", "Скрипт автоматически применяет готовую настройку. «Вручную» показывает значения и файлы конфигурации, чтобы внести их самостоятельно. В обоих вариантах API-ключ уже подставлен."),
            ("setup", "Слетят ли другие провайдеры, настройки или чаты?", "Команды настройки и удаления предназначены только для интеграции Starimg AI. Перед настройкой полностью закройте выбранный клиент; для отмены используйте скрытый блок «Удалить интеграцию»."),
            ("errors", "Клиент просит войти или не видит CVC_API_KEY", "Полностью закройте и снова откройте клиент. Для Remote/SSH/VPS и server workspace запускайте настройку на машине, где реально работает клиент или extension host."),
            ("errors", "401, Incorrect API key или запрос уходит на api.openai.com", "Клиент всё ещё использует прямую конфигурацию провайдера. Запустите настройку на нужной машине и перезапустите клиент. Если осталась старая конфигурация — один раз выполните «Удалить интеграцию», затем повторите настройку."),
            ("errors", "Модель не появилась или Model not found", "Проверьте точный ID модели для выбранного клиента. В OpenCode и Kimi используется starimg/<model>, в Cursor — <model>-cursor. После смены модели или ключа перезапустите клиент."),
            ("errors", "Что означают 429, 502, 503, 504, 524 и capacity_unavailable?", "429 — ограничение частоты: подождите и уменьшите параллельные запросы. 502/503/capacity_unavailable — временная недоступность upstream. При 504/524 немного подождите, повторите запрос один раз с паузой и при необходимости выберите другую модель."),
            ("errors", "Claude Code устарел или постоянно спрашивает yes / no", "При Unsupported Claude Code version выполните claude update и перезапустите CLI. Запросы yes/no — это локальные разрешения Claude, а не ошибка API-ключа. Claude App и Claude Code CLI настраиваются отдельно."),
        )
        placeholder, empty = "Поиск по вопросам…", "Подходящих ответов не найдено."
    rows = "".join(
        f"<details class='faq-item' data-faq-category='{category}'><summary>{html.escape(question)}</summary><p>{html.escape(answer)}</p></details>"
        for category, question, answer in items
    )
    return f"""
    <section class='card faq' id='faq'>
      <h2>{html.escape(title)}</h2><p>{html.escape(intro)}</p>
      <div class='faq-items'>{rows}</div>
    </section>"""


def render_tokens_admin(
    *,
    csrf_token: str,
    authenticated: bool,
    password_configured: bool,
    keys: list[TokenKey],
    total_key_count: int = 0,
    matching_key_count: int = 0,
    page_state: TokenAdminPageState | None = None,
    total_pages: int = 1,
    promos: list[PromoCode] | None = None,
    error: str = "",
    notice: str = "",
    created_access_codes: list[str] | None = None,
    created_promo_codes: list[str] | None = None,
    resellers: list[ResellerKey] | None = None,
    reseller_children: dict[int, list[TokenKey]] | None = None,
) -> HTMLResponse:
    flash = ""
    if error:
        flash = f"<div class='flash error'>{html.escape(error)}</div>"
    elif notice:
        flash = f"<div class='flash success'>{html.escape(notice)}</div>"
    if not authenticated:
        unavailable = "" if password_configured else "<p class='warning'>TOKENS_ADMINS не задан в .env. Вход отключен.</p>"
        content = f"""
        <main class='page narrow'><section class='card'>
          <h1>СОЗДАНИЯ КЛЮЧЕЙ</h1>
          {flash}{unavailable}
          <form method='post' action='/ai/tokens/adm/login'>
            <input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'>
            <label for='password'>Пароль администратора</label>
            <input id='password' type='password' name='password' autocomplete='current-password' required>
            <button class='primary wide' type='submit' {'disabled' if not password_configured else ''}>Войти</button>
          </form>
        </section></main>"""
        return HTMLResponse(render_layout("Админ-панель ключей", content))
    state = page_state or TokenAdminPageState()
    rows = render_admin_rows(keys, csrf_token, state)
    reseller_rows = render_admin_reseller_rows(
        resellers or [], reseller_children or {}, csrf_token, state,
    )
    promo_rows = render_admin_promo_rows(promos or [])
    created_access_codes = created_access_codes or []
    created_promo_codes = created_promo_codes or []
    created_codes = "\n".join(created_access_codes)
    created_activation_links = "\n".join(
        f"https://starimg.ru/ai/tokens?key={access_code}"
        for access_code in created_access_codes
    )
    copy_created_keys = ""
    if created_access_codes:
        copy_created_keys = f"""
        <div class='created-keys copy-created-codes' data-created-codes='{html.escape(created_codes, quote=True)}' data-created-links='{html.escape(created_activation_links, quote=True)}'>
          <strong>Созданные ключи: {len(created_access_codes)}</strong>
          <div class='created-key-actions'>
            <button type='button' class='secondary copy-created-codes-button'>Скопировать все</button>
            <button type='button' class='secondary copy-created-links-button'>Скопировать ссылками</button>
          </div>
        </div>"""
    created_promo_values = "\n".join(created_promo_codes)
    copy_created_promos = ""
    if created_promo_codes:
        copy_created_promos = f"""
        <div class='created-keys copy-created-codes' data-created-codes='{html.escape(created_promo_values, quote=True)}'>
          <strong>Созданные промокоды: {len(created_promo_codes)}</strong>
          <button type='button' class='secondary copy-created-codes-button'>Скопировать все</button>
        </div>"""
    service_options = "".join(f"<option value='{html.escape(service)}'>{html.escape(service)}</option>" for service in SERVICE_OPTIONS)
    table_headers = render_token_admin_table_headers(state)
    table_pagination = render_token_admin_pagination(
        state=state,
        total_pages=total_pages,
        matching_key_count=matching_key_count,
        total_key_count=total_key_count,
    )
    action_query = urlencode({
        "page": str(state.page),
        "sort": state.sort_key,
        "order": state.sort_order,
        **({"search": state.search_query} if state.search_query else {}),
    })
    content = f"""
    <main class='page admin-page'>
      <section class='card'>
        <div class='title-row'><h1>СОЗДАНИЯ КЛЮЧЕЙ</h1>
        <form method='post' action='/ai/tokens/adm/logout'><input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'><button class='secondary' type='submit'>Выйти</button></form></div>
        {flash}{copy_created_keys}
        <form method='post' action='/ai/tokens/adm/create?{html.escape(action_query, quote=True)}' class='create-form'>
          <input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'>
          <label>ВЫБРАТЬ СЕРВИС:<select name='service' required>{service_options}</select></label>
          <label>ВЫБРАТЬ НАЗВАНИЕ:<input name='name' required maxlength='200' placeholder='Название'></label>
          <label>ВЫБРАТЬ КОЛИЧЕСТВО:<input type='number' name='token_limit' min='1' step='1' required placeholder='Количество токенов'></label>
          <label>ВЫБРАТЬ КОЛИЧЕСТВО КЛЮЧЕЙ:<input type='number' name='quantity' min='1' max='100' step='1' required value='1'></label>
          <button class='primary wide' type='submit'>СОЗДАТЬ</button>
        </form>
      </section>
      <section class='card'>
        <h2>УПРАВЛЕНИЕ КЛЮЧАМИ</h2>
        <p class='hint'>Нажмите «Управлять», чтобы изменить все данные ключа. Сортировка и поиск применяются ко всем ключам до разделения на страницы.</p>
        <form method='get' action='/ai/tokens/adm' class='keys-search-form'>
          <input type='hidden' name='sort' value='{html.escape(state.sort_key, quote=True)}'>
          <input type='hidden' name='order' value='{html.escape(state.sort_order, quote=True)}'>
          <label for='keys-search'>Поиск по ключу или API KEY</label>
          <div class='keys-search-controls'>
            <input id='keys-search' name='search' value='{html.escape(state.search_query, quote=True)}' maxlength='500' autocomplete='off' placeholder='Ключ или API KEY'>
            <button class='secondary' type='submit'>Найти</button>
            <a class='secondary reset-search' href='/ai/tokens/adm'>Сбросить</a>
          </div>
        </form>
        <div class='table-wrap' id='keys-table-wrap'><table id='keys-table'><thead><tr>{table_headers}</tr></thead>
        <tbody>{rows or "<tr><td colspan='9' class='empty'>По вашему запросу ключей не найдено.</td></tr>"}</tbody></table></div>
        <div id='keys-table-groups' hidden></div>
        {table_pagination}
      </section>
      <section class='card'>
        <h2>РЕСЕЛЛЕРСКИЕ КЛЮЧИ</h2>
        <p class='hint'>Бюджет «выдано навсегда» не возвращается при заморозке, удалении или изменении производного ключа. Нажмите «Производные ключи», чтобы раскрыть вложенный список.</p>
        <div class='reseller-list'>{reseller_rows or "<p class='empty'>Реселлерских ключей пока нет.</p>"}</div>
      </section>
      <section class='card'>
        <h2>СОЗДАНИЯ ПРОМОКОДОВ</h2>
        {copy_created_promos}
        <form method='post' action='/ai/tokens/adm/promos/create?{html.escape(action_query, quote=True)}' class='create-form promo-create-form'>
          <input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'>
          <label>КОЛИЧЕСТВО НАЧИСЛЯЕМЫХ ТОКЕНОВ:<input type='number' name='additional_tokens' min='1' step='1' required placeholder='Количество токенов'></label>
          <label>КОЛИЧЕСТВО СОЗДАВАЕМЫХ ПРОМОКОДОВ:<input type='number' name='quantity' min='1' max='100' step='1' required value='1'></label>
          <button class='primary wide' type='submit'>СОЗДАТЬ ПРОМОКОДЫ</button>
        </form>
      </section>
      <section class='card'>
        <h2>УПРАВЛЕНИЕ ПРОМОКОДАМИ</h2>
        <p class='hint'>Промокод можно использовать только один раз.</p>
        <div class='table-wrap'><table class='promos-table'><thead><tr><th>Дата создания</th><th>ПРОМОКОД</th><th>НАЧИСЛЯЕТСЯ ТОКЕНОВ</th><th>СТАТУС</th></tr></thead>
        <tbody>{promo_rows or "<tr><td colspan='4' class='empty'>Промокодов пока нет.</td></tr>"}</tbody></table></div>
      </section>
    </main>"""
    return HTMLResponse(render_layout("Админ-панель ключей", content))


def token_admin_url(
    *,
    page: int,
    search_query: str,
    sort_key: str,
    sort_order: str,
) -> str:
    params = {"page": str(page), "sort": sort_key, "order": sort_order}
    if search_query:
        params["search"] = search_query
    return f"/ai/tokens/adm?{urlencode(params)}"


def render_token_admin_table_headers(state: TokenAdminPageState) -> str:
    columns = (
        ("id", "ID"),
        ("created_at", "Дата создания"),
        (None, "СЕРВИС"),
        ("access_code", "КЛЮЧ"),
        ("api_key", "API KEY"),
        ("token_limit", "ТОКЕНОВ"),
        ("used_tokens", "ИСПОЛЬЗОВАНО"),
        ("status", "СТАТУС"),
        ("management", "УПРАВЛЕНИЕ"),
    )
    headers: list[str] = []
    for sort_key, label in columns:
        if sort_key is None:
            headers.append("<th data-group-service><button type='button' class='service-group-header'>СЕРВИС</button></th>")
            continue
        # The management cells have no sortable data, so preserve the current
        # global order rather than pretending that their identical labels have
        # a meaningful ordering.
        if sort_key == "management":
            headers.append(f"<th>{label}</th>")
            continue
        next_order = "asc" if state.sort_key != sort_key or state.sort_order == "desc" else "desc"
        href = token_admin_url(
            page=1,
            search_query=state.search_query,
            sort_key=sort_key,
            sort_order=next_order,
        )
        headers.append(
            f"<th><a class='sort-link' href='{html.escape(href, quote=True)}'>{label}</a></th>"
        )
    return "".join(headers)


def render_token_admin_pagination(
    *,
    state: TokenAdminPageState,
    total_pages: int,
    matching_key_count: int,
    total_key_count: int,
) -> str:
    if matching_key_count:
        first = (state.page - 1) * TOKEN_ADMIN_PAGE_SIZE + 1
        last = min(state.page * TOKEN_ADMIN_PAGE_SIZE, matching_key_count)
        result_text = f"Показаны {first}–{last} из {matching_key_count} ключей"
    else:
        result_text = "Ключей по запросу не найдено"
    if state.search_query:
        result_text += f" (всего: {total_key_count})"
    if total_pages <= 1:
        return f"<p class='pagination-summary'>{html.escape(result_text)}</p>"

    def link(page: int, label: str, disabled: bool = False) -> str:
        if disabled:
            return f"<span class='page-link disabled'>{label}</span>"
        href = token_admin_url(
            page=page,
            search_query=state.search_query,
            sort_key=state.sort_key,
            sort_order=state.sort_order,
        )
        return f"<a class='page-link' href='{html.escape(href, quote=True)}'>{label}</a>"

    pages: list[int | None] = []
    for candidate in (1, state.page - 1, state.page, state.page + 1, total_pages):
        if not 1 <= candidate <= total_pages or candidate in pages:
            continue
        if pages and candidate - int(pages[-1]) > 1:
            pages.append(None)
        pages.append(candidate)
    page_links = "".join(
        "<span class='page-ellipsis'>…</span>" if page is None else
        (f"<span class='page-link current'>{page}</span>" if page == state.page else link(page, str(page)))
        for page in pages
    )
    return f"""
    <nav class='pagination' aria-label='Страницы ключей'>
      <p class='pagination-summary'>{html.escape(result_text)}</p>
      <div class='pagination-links'>
        {link(state.page - 1, 'Назад', state.page <= 1)}
        {page_links}
        {link(state.page + 1, 'Вперёд', state.page >= total_pages)}
      </div>
    </nav>"""


def render_admin_promo_rows(promos: list[PromoCode]) -> str:
    """Render the deliberately read-only management list for promo codes."""
    rows: list[str] = []
    for promo in promos:
        if promo.used_at is None:
            status = "Не использован"
        else:
            status = f"Использован ({format_datetime(promo.used_at)})"
        rows.append(
            f"<tr><td>{format_datetime(promo.created_at)}</td>"
            f"<td><code>{html.escape(promo.code)}</code></td>"
            f"<td>{format_tokens(promo.additional_tokens)}</td>"
            f"<td>{html.escape(status)}</td></tr>"
        )
    return "".join(rows)


def render_admin_rows(
    keys: list[TokenKey], csrf_token: str, state: TokenAdminPageState | None = None,
) -> str:
    rows: list[str] = []
    state = state or TokenAdminPageState()
    action_query = urlencode({
        "page": str(state.page),
        "sort": state.sort_key,
        "order": state.sort_order,
        **({"search": state.search_query} if state.search_query else {}),
    })
    for key in keys:
        status = (
            "Заморожен"
            if not key.active else
            (f"Истрачен ({format_datetime(key.exhausted_at or key.activated_at)})"
             if key.is_exhausted else
             (f"Активирован ({format_datetime(key.activated_at)})" if key.activated_at else "Не активирован"))
        )
        freeze_label = "Заморозить" if key.active else "Разморозить"
        freeze_title = "Приостановить работу ключа" if key.active else "Возобновить работу ключа"
        freeze_class = "freeze-key frozen" if not key.active else "freeze-key"
        rows.append(f"""
        <tr data-service='{html.escape(key.service, quote=True)}'><td data-sort-value='{key.id}'>{key.id}</td><td data-sort-value='{key.created_at.timestamp()}'>{format_datetime(key.created_at)}</td><td data-sort-value='{html.escape(key.service, quote=True)}'>{html.escape(key.service)}</td><td data-sort-value='{html.escape(key.access_code, quote=True)}'><code>{html.escape(key.access_code)}</code></td>
        <td data-sort-value='{html.escape(key.api_key, quote=True)}' class='copyable-api-key' data-copy-api-key='{html.escape(key.api_key, quote=True)}' role='button' tabindex='0' title='Нажмите, чтобы скопировать API key' aria-label='Скопировать API key'><code class='api-preview'>{html.escape(key.api_key)}</code></td><td data-sort-value='{key.token_limit}'>{format_tokens(key.token_limit)}</td>
        <td data-sort-value='{key.used_tokens}'>{format_tokens(key.used_tokens)}<br><span class='hint'>ост. {format_tokens(key.remaining_tokens)}</span></td><td data-sort-value='{html.escape(status, quote=True)}'>{status}</td>
        <td data-sort-value='Управление'><div class='management-actions'><button type='button' class='secondary' data-edit='row'>Управлять</button>
        <form method='post' action='/ai/tokens/adm/{key.id}/freeze?{html.escape(action_query, quote=True)}' class='inline-freeze-form'>
          <input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'>
          <button class='{freeze_class}' type='submit' title='{freeze_title}'>{freeze_label}</button>
        </form>
        <form method='post' action='/ai/tokens/adm/{key.id}/delete?{html.escape(action_query, quote=True)}' class='inline-delete-form'>
          <input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'>
          <button class='danger' type='submit' onclick="return confirm('Удалить ключ #{key.id}?')">Удалить</button>
        </form></div></td></tr>""")
        options = "".join(
            f"<option value='{html.escape(service, quote=True)}' {'selected' if service == key.service else ''}>{html.escape(service)}</option>"
            for service in RESELLER_SERVICE_OPTIONS
        )
        rows.append(f"""
        <tr class='edit-row' hidden><td colspan='9'>
          <form class='edit-form' method='post' action='/ai/tokens/adm/{key.id}/update?{html.escape(action_query, quote=True)}'>
            <input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'>
            <h3>Управление ключом #{key.id}</h3>
            <div class='edit-grid'>
              <label>Сервис<select name='service'>{options}</select></label>
              <label>Название<input name='name' value='{html.escape(key.name, quote=True)}' required></label>
              <label>Дата создания<input type='datetime-local' name='created_at' value='{input_datetime(key.created_at)}' required></label>
              <label>Ключ доступа<input name='access_code' minlength='20' maxlength='20' pattern='[A-Z0-9]{{20}}' value='{html.escape(key.access_code, quote=True)}' required></label>
              <label>API key<input name='api_key' value='{html.escape(key.api_key, quote=True)}' required></label>
              <label>Лимит токенов<input type='number' name='token_limit' min='1' step='1' value='{key.token_limit}' required></label>
              <label>Использовано<input type='number' name='used_tokens' min='0' step='1' value='{key.used_tokens}' required></label>
              <label>Дата активации<input type='datetime-local' name='activated_at' value='{input_datetime(key.activated_at)}'></label>
              <label>Дата истрачения<input type='datetime-local' name='exhausted_at' value='{input_datetime(key.exhausted_at)}'></label>
            </div>
            <div class='edit-actions'><button class='primary' type='submit'>Сохранить</button>
            <button class='secondary cancel-edit' type='button' data-edit='row'>Отмена</button></div>
          </form>
        </td></tr>
""")
    return "".join(rows)


def render_admin_reseller_rows(
    resellers: list[ResellerKey], children_by_reseller: dict[int, list[TokenKey]], csrf_token: str,
    state: TokenAdminPageState,
) -> str:
    action_query = urlencode({
        "page": str(state.page), "sort": state.sort_key, "order": state.sort_order,
        **({"search": state.search_query} if state.search_query else {}),
    })
    cards: list[str] = []
    for reseller in resellers:
        children = children_by_reseller.get(reseller.id, [])
        status = "Активен" if reseller.active else "Заморожен"
        freeze_label = "Заморозить" if reseller.active else "Разморозить"
        child_rows = "".join(
            f"<tr><td>{child.id}</td><td>{html.escape(child.service)}</td><td>{html.escape(child.name)}</td>"
            f"<td><code>{html.escape(child.access_code)}</code></td>"
            f"<td class='copyable-api-key' data-copy-api-key='{html.escape(child.api_key, quote=True)}' role='button' tabindex='0' title='Нажмите, чтобы скопировать API key' aria-label='Скопировать API key'><code class='api-preview'>{html.escape(child.api_key)}</code></td>"
            f"<td>{format_tokens(child.token_limit)}</td><td>{format_tokens(child.used_tokens)}</td>"
            f"<td>{format_tokens(child.remaining_tokens)}</td><td>{'Активен' if child.active else 'Заморожен'}</td></tr>"
            for child in children
        ) or "<tr><td colspan='9' class='empty'>Производных ключей пока нет.</td></tr>"
        cards.append(f"""
        <article class='reseller-card' data-reseller-id='{reseller.id}'>
          <div class='reseller-summary'>
            <div><strong>{html.escape(reseller.name)}</strong><span class='hint'> · Реселлинг · {status}</span></div>
            <div class='reseller-values'><span>Ключ: <code>{html.escape(reseller.access_code)}</code></span><span>Лимит: {format_tokens(reseller.token_limit)}</span><span>Выдано навсегда: {format_tokens(reseller.issued_tokens)}</span><span>Доступно: {format_tokens(reseller.available_tokens)}</span><span>Производных: {len(children)}</span></div>
            <div class='management-actions'>
              <button type='button' class='secondary' data-reseller-children>Производные ключи</button>
              <form method='post' action='/ai/tokens/adm/resellers/{reseller.id}/freeze?{html.escape(action_query, quote=True)}' class='inline-freeze-form'>
                <input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'><button class='freeze-key {'frozen' if not reseller.active else ''}' type='submit'>{freeze_label}</button>
              </form>
            </div>
          </div>
          <div class='table-wrap reseller-children' hidden><table><thead><tr><th>ID</th><th>Сервис</th><th>Название</th><th>Ключ доступа</th><th>API key</th><th>Лимит</th><th>Использовано</th><th>Остаток</th><th>Статус</th></tr></thead><tbody>{child_rows}</tbody></table></div>
        </article>""")
    return "".join(cards)


def render_reseller_page(
    *, csrf_token: str, reseller: ResellerKey | None, children: list[TokenKey],
    operations: list[ResellerOperation], error: str = "", notice: str = "", submitted_code: str = "",
    locale: str = "en",
) -> HTMLResponse:
    locale = reseller_locale(locale)
    text = RESELLER_TEXT[locale]
    flash = f"<div class='flash error'>{html.escape(error)}</div>" if error else (
        f"<div class='flash success'>{html.escape(notice)}</div>" if notice else ""
    )
    language_switch = (
        f"<div class='reseller-language-switch'><span>{html.escape(text['language'])}:</span> "
        f"<a class='{'active' if locale == 'ru' else ''}' href='/ai/tokens/reselling?lang=ru'>Русский</a>"
        f"<a class='{'active' if locale == 'en' else ''}' href='/ai/tokens/reselling?lang=en'>English</a></div>"
    )
    if reseller is None:
        content = f"""
        <main class='page reseller-page reseller-login-page'><section class='card reseller-login-card'>
          {language_switch}<div class='reseller-eyebrow'>{html.escape(text['eyebrow'])}</div>
          <h1>{html.escape(text['title'])}</h1><p class='reseller-intro'>{html.escape(text['login_intro'])}</p>{flash}
          <form method='post' action='/ai/tokens/reselling/login'>
            <input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'>
            <input type='hidden' name='lang' value='{locale}'>
            <label>{html.escape(text['access_key'])}<input name='access_code' value='{html.escape(submitted_code, quote=True)}' autocomplete='off' required></label>
            <button type='submit' class='primary wide'>{html.escape(text['login'])}</button>
          </form>
        </section></main>"""
        return HTMLResponse(render_layout(text["title"], content, locale=locale))
    services = "".join(
        f"<option value='{html.escape(service, quote=True)}'>{html.escape(service)}</option>"
        for service in RESELLER_SERVICE_OPTIONS
    )
    child_rows: list[str] = []
    for child in children:
        status = text["active"] if child.active else text["frozen"]
        freeze_label = text["freeze"] if child.active else text["unfreeze"]
        freeze_class = "freeze-key" if child.active else "freeze-key frozen"
        api_key = html.escape(child.api_key, quote=True)
        child_rows.append(f"""
        <tr class='reseller-child-row'>
          <td>{child.id}</td><td>{html.escape(child.service)}</td><td>{html.escape(child.name)}</td>
          <td><code class='reseller-key-cell' title='{html.escape(child.access_code, quote=True)}'>{html.escape(child.access_code)}</code></td>
          <td class='copyable-api-key' data-copy-api-key='{api_key}' role='button' tabindex='0' title='{html.escape(text["copy_hint"], quote=True)}' aria-label='{html.escape(text["copy_hint"], quote=True)}'><code class='reseller-key-cell' title='{api_key}'>{api_key}</code></td>
          <td>{format_tokens(child.token_limit)}</td><td>{format_tokens(child.used_tokens)}</td><td>{format_tokens(child.remaining_tokens)}</td><td><span class='reseller-status {'status-active' if child.active else 'status-frozen'}'>{status}</span></td>
          <td class='reseller-child-actions'>
            <form method='post' action='/ai/tokens/reselling/{child.id}/top-up' class='reseller-top-up-form'>
              <input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'><input type='hidden' name='lang' value='{locale}'>
              <input name='additional_tokens' type='number' min='1' step='1' required aria-label='{html.escape(text["top_up"], quote=True)}' placeholder='{html.escape(text["top_up_placeholder"], quote=True)}'><button class='secondary reseller-top-up-button' type='submit' title='{html.escape(text["top_up"], quote=True)}' aria-label='{html.escape(text["top_up"], quote=True)}'>+</button>
            </form>
            <form method='post' action='/ai/tokens/reselling/{child.id}/freeze' class='reseller-freeze-form'><input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'><input type='hidden' name='lang' value='{locale}'><button class='{freeze_class}' type='submit'>{html.escape(freeze_label)}</button></form>
          </td>
        </tr>""")
    unknown_operations = [operation for operation in operations if operation.state == "unknown"]
    pending_notice = "" if not unknown_operations else f"<p class='warning'>{html.escape(text['pending'].format(count=len(unknown_operations)))}</p>"
    total_tokens = format_tokens(reseller.token_limit)
    issued_tokens = format_tokens(reseller.issued_tokens)
    available_tokens = format_tokens(reseller.available_tokens)
    content = f"""
    <main class='page reseller-page'>
      <section class='reseller-hero card'>
        <div class='reseller-header'>{language_switch}<form method='post' action='/ai/tokens/reselling/logout'><input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'><input type='hidden' name='lang' value='{locale}'><button class='secondary' type='submit'>{html.escape(text['logout'])}</button></form></div>
        <div class='reseller-eyebrow'>{html.escape(text['eyebrow'])}</div><h1>{html.escape(text['title'])}</h1><p class='reseller-intro'>{html.escape(text['dashboard_intro'])}</p>
        {flash}{pending_notice}
        <div class='reseller-access-row'><div><div class='reseller-label'>{html.escape(text['portal_key'])}</div><code class='reseller-access-code'>{html.escape(reseller.access_code)}</code><div class='hint'>{html.escape(text['portal_key_hint'])}</div></div>
          <form method='post' action='/ai/tokens/reselling/regenerate' class='reseller-regenerate-form' onsubmit="return confirm('{html.escape(text['regenerate_confirm'], quote=True)}')"><input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'><input type='hidden' name='lang' value='{locale}'><button class='secondary' type='submit'>{html.escape(text['regenerate'])}</button></form>
        </div>
      </section>
      <section class='card reseller-balance-card'><div class='section-heading'><div><div class='reseller-eyebrow'>{html.escape(text['balance'])}</div><h2>{html.escape(text['balance'])}</h2></div><span class='reseller-child-count'>{len(children)} {html.escape(text['children_count'])}</span></div>
        <div class='reseller-balance-grid'><div class='balance-metric'><span>{html.escape(text['total'])}</span><strong>{total_tokens}</strong></div><div class='balance-metric'><span>{html.escape(text['spent'])}</span><strong>{issued_tokens}</strong></div><div class='balance-metric balance-available'><span>{html.escape(text['available'])}</span><strong>{available_tokens}</strong></div></div>
        <p class='hint'>{html.escape(text['balance_hint'])}</p>
      </section>
      <section class='card'><div class='section-heading'><div><h2>{html.escape(text['create_title'])}</h2><p class='hint'>{html.escape(text['create_hint'])}</p></div></div><form method='post' action='/ai/tokens/reselling/create' class='create-form'><input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'><input type='hidden' name='lang' value='{locale}'><label>{html.escape(text['service'])}<select name='service' required>{services}</select></label><label>{html.escape(text['name'])}<input name='name' maxlength='200' required></label><label>{html.escape(text['token_limit'])}<input name='token_limit' type='number' min='1' step='1' required></label><button type='submit' class='primary wide'>{html.escape(text['create'])}</button></form></section>
      <section class='card'><div class='section-heading'><div><h2>{html.escape(text['children_title'])}</h2><p class='hint'>{html.escape(text['children_hint'])}</p></div></div><div class='table-wrap reseller-table-wrap'><table class='reseller-child-table'><thead><tr><th>{html.escape(text['id'])}</th><th>{html.escape(text['service'])}</th><th>{html.escape(text['name'])}</th><th>{html.escape(text['access'])}</th><th>{html.escape(text['api_key'])}</th><th>{html.escape(text['limit'])}</th><th>{html.escape(text['used'])}</th><th>{html.escape(text['remaining'])}</th><th>{html.escape(text['status'])}</th><th>{html.escape(text['actions'])}</th></tr></thead><tbody>{''.join(child_rows) or f"<tr><td colspan='10' class='empty'>{html.escape(text['empty'])}</td></tr>"}</tbody></table></div></section>
    </main>"""
    return HTMLResponse(render_layout(text["title"], content, locale=locale))


def render_layout(title: str, content: str, locale: str = "ru") -> str:
    return f"""<!doctype html>
<html lang='{token_locale(locale)}'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
<title>{html.escape(title)}</title><style>
:root {{ color-scheme: dark; --bg:#080d16; --card:#111a2a; --line:#27364f; --text:#eaf0ff; --muted:#a9b7ce; --accent:#6d9cff; --danger:#ff7885; --success:#5fd5a0; --warn:#ffd46b; }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:radial-gradient(circle at top,#182949 0,#080d16 44rem); color:var(--text); font:16px/1.55 Arial,sans-serif; }}
.page {{ width:min(960px,calc(100% - 32px)); margin:32px auto 64px; }} .page.narrow {{ max-width:520px; }} .admin-page {{ width:calc(100% - 48px); max-width:1800px; }}
.card {{ background:rgba(17,26,42,.96); border:1px solid var(--line); border-radius:16px; padding:24px; margin:18px 0; box-shadow:0 12px 36px rgba(0,0,0,.22); }}
h1,h2,h3 {{ margin:0 0 14px; line-height:1.24; }} h1 {{ font-size:28px; }} h2 {{ font-size:20px; letter-spacing:.02em; }} p {{ margin:9px 0; }}
label {{ display:block; font-weight:700; margin:14px 0 6px; }} input,select {{ display:block; width:100%; margin-top:6px; padding:12px 13px; border:1px solid var(--line); border-radius:9px; background:#0b1321; color:var(--text); font:inherit; }}
button {{ border:0; border-radius:9px; padding:11px 15px; font:700 14px Arial,sans-serif; cursor:pointer; }} button:disabled {{ cursor:not-allowed; opacity:.5; }} .primary {{ color:#071120; background:var(--accent); }} .wide {{ display:block; width:100%; margin-top:18px; }} .secondary {{ color:var(--text); background:#263652; }} .danger {{ color:#26080c; background:var(--danger); margin-top:12px; }}
.hint {{ color:var(--muted); font-size:14px; }} .warning {{ color:var(--warn); font-weight:700; }} .flash {{ border-radius:9px; padding:11px 13px; margin:12px 0; }} .error {{ color:#ffdce0; background:rgba(255,120,133,.18); border:1px solid rgba(255,120,133,.45); }} .success {{ color:#d8ffec; background:rgba(95,213,160,.15); border:1px solid rgba(95,213,160,.45); }}
.top-links {{ display:flex; justify-content:space-between; gap:8px; margin:0 0 -4px; }} .top-links a {{ color:var(--text); font-weight:700; text-decoration:none; padding:7px 10px; border:1px solid var(--line); border-radius:7px; }} .top-links a:hover {{ border-color:var(--accent); color:var(--accent); }}
.details {{ display:grid; grid-template-columns:minmax(210px,auto) 1fr; gap:8px 18px; margin:0; }} .details dt {{ color:var(--muted); }} .details dd {{ margin:0; min-width:0; overflow-wrap:anywhere; }} code,pre {{ font-family:Consolas,'Courier New',monospace; }} .api-key {{ color:#b9d4ff; }}
.info-actions {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin-top:22px; }} .info-actions button,.user-freeze-form {{ width:100%; }} .info-actions button {{ display:inline-flex; justify-content:center; align-items:center; gap:9px; min-height:46px; }} .info-actions .bonus-button {{ font-size:16px; }} .bonus-button {{ color:#281500; background:linear-gradient(135deg,#ffd46b,#f59e0b); }} .bonus-button:hover,.bonus-button:focus {{ background:linear-gradient(135deg,#ffe191,#fbbf24); }} .bonus-button svg {{ width:20px; height:20px; flex:0 0 auto; }} .bonus-claim {{ margin-top:14px; padding:16px; border:1px solid rgba(245,158,11,.45); border-radius:11px; background:rgba(245,158,11,.09); }} .bonus-claim p {{ margin:0 0 12px; color:#ffe2a2; }} .bonus-claim label {{ margin-top:0; }} .log-download-status {{ min-height:22px; margin:8px 0 0; }} .log-download-status.error {{ color:#ffdce0; }}
.choice-row {{ display:flex; flex-wrap:wrap; gap:8px; margin:14px 0; }} .instruction-apps .choice[hidden] {{ display:none; }} .instruction-card {{ margin-top:18px; }} .choice {{ color:var(--text); background:#1b2940; border:1px solid var(--line); }} .choice.active {{ color:#08101e; background:var(--accent); border-color:var(--accent); }} .selected-line {{ padding:12px; margin:16px 0; border-left:3px solid var(--accent); background:#0b1321; }} ol {{ padding-left:24px; }} pre {{ max-width:100%; overflow:auto; padding:14px; white-space:pre-wrap; overflow-wrap:anywhere; background:#080e18; border:1px solid var(--line); border-radius:9px; color:#d9e7ff; }}
.instruction-mode-tabs {{ display:flex; gap:6px; margin:16px 0 10px; border-bottom:1px solid var(--line); }} .instruction-mode {{ color:var(--muted); background:transparent; border-radius:8px 8px 0 0; padding:9px 12px; }} .instruction-mode.active {{ color:var(--text); background:#1b2940; box-shadow:inset 0 -2px 0 var(--accent); }} .instruction-mode-panel {{ padding:2px 0 4px; }} .manual-heading {{ color:var(--text); font-weight:700; }} .manual-downloads {{ display:flex; flex-wrap:wrap; gap:8px; margin:10px 0 14px; }} .download-file {{ color:var(--text); background:#1b2940; border:1px solid var(--line); border-radius:8px; padding:8px 11px; font-size:14px; font-weight:700; text-decoration:none; }} .download-file:hover,.download-file:focus {{ border-color:var(--accent); color:var(--accent); }} .manual-note {{ margin:8px 0 14px; color:var(--muted); line-height:1.55; }} .remove-integration {{ margin-top:14px; overflow:hidden; border:1px solid var(--line); border-radius:10px; background:#0b1321; }} .remove-integration summary,.faq-item summary {{ cursor:pointer; padding:12px 14px; font-weight:700; }} .remove-integration pre {{ margin:0 12px 12px; }} .remove-integration {{ border-color:rgba(255,120,133,.42); background:rgba(255,120,133,.07); }} .remove-integration summary {{ color:#ffdce0; }} .remove-integration .hint,.remove-integration button {{ margin-left:12px; margin-right:12px; }} .remove-integration button {{ margin-bottom:12px; }}
.faq {{ scroll-margin-top:24px; }} .faq-items {{ border-top:1px solid var(--line); }} .faq-item {{ display:block; border-bottom:1px solid var(--line); }} .faq-item summary {{ list-style:none; padding-right:38px; position:relative; }} .faq-item summary::-webkit-details-marker {{ display:none; }} .faq-item summary::after {{ content:'+'; position:absolute; right:14px; color:var(--accent); font-size:20px; line-height:1; }} .faq-item[open] summary::after {{ content:'−'; }} .faq-item p {{ color:var(--muted); padding:0 14px 14px; margin:0; }}
.title-row {{ display:flex; justify-content:space-between; gap:16px; align-items:start; }} .title-row form {{ margin:0; }} .create-form,.edit-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); column-gap:18px; }} .create-form .wide {{ grid-column:1 / -1; }}
 .table-wrap {{ overflow-x:auto; }} .management-actions {{ display:flex; gap:6px; align-items:center; }} .inline-delete-form,.inline-freeze-form,.reseller-child-table form {{ margin:0; }} .inline-delete-form .danger {{ margin:0; padding:9px 11px; }} .freeze-key {{ margin:0; color:#e3f3ff; background:#375d80; }} .freeze-key:hover,.freeze-key:focus {{ background:#48789f; }} .freeze-key.frozen {{ color:#05233c; background:linear-gradient(135deg,#d9f6ff,#83d5f4); box-shadow:0 0 0 1px rgba(174,235,255,.55),0 0 16px rgba(106,210,247,.35); }} .freeze-key.frozen:hover,.freeze-key.frozen:focus {{ background:linear-gradient(135deg,#e9faff,#a9e5fa); }} .copyable-api-key {{ cursor:pointer; }} .copyable-api-key:hover,.copyable-api-key:focus {{ background:rgba(109,156,255,.13); outline:none; }} table {{ width:100%; border-collapse:collapse; min-width:990px; }} .promos-table {{ min-width:640px; }} th,td {{ padding:10px 8px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }} .sort-link,.service-group-header {{ color:inherit; font:inherit; font-weight:700; text-align:left; text-decoration:none; }} .sort-link:hover,.sort-link:focus,.service-group-header:hover,.service-group-header:focus,th[data-group-service].grouped {{ color:var(--accent); }} th[data-group-service] {{ cursor:pointer; user-select:none; }} .service-group-header {{ padding:0; background:transparent; border-radius:0; }} .keys-search-form {{ margin:14px 0 18px; }} .keys-search-form label {{ margin-top:0; }} .keys-search-controls {{ display:flex; gap:9px; align-items:end; }} .keys-search-controls input {{ margin-top:0; min-width:0; flex:1 1 auto; }} .keys-search-controls .secondary,.reset-search {{ white-space:nowrap; }} .reset-search {{ display:inline-flex; align-items:center; justify-content:center; min-height:46px; text-decoration:none; }} .admin-page.is-updating {{ opacity:.64; pointer-events:none; transition:opacity .12s ease; }} .pagination {{ display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:10px; margin-top:18px; }} .pagination-summary {{ margin:0; color:var(--muted); }} .pagination-links {{ display:flex; flex-wrap:wrap; gap:6px; align-items:center; }} .page-link {{ display:inline-flex; align-items:center; justify-content:center; min-width:38px; min-height:36px; padding:7px 10px; border-radius:8px; color:var(--text); background:#263652; font-weight:700; text-decoration:none; }} .page-link:hover,.page-link:focus {{ color:#08101e; background:var(--accent); }} .page-link.current {{ color:#08101e; background:var(--accent); cursor:default; }} .page-link.disabled {{ color:var(--muted); opacity:.55; cursor:not-allowed; }} .page-ellipsis {{ padding:0 3px; color:var(--muted); }} .created-keys {{ display:flex; justify-content:space-between; align-items:center; gap:12px; padding:12px 13px; margin:12px 0; border:1px solid rgba(95,213,160,.45); border-radius:9px; background:rgba(95,213,160,.12); }} .created-key-actions {{ display:flex; flex-wrap:wrap; justify-content:flex-end; gap:8px; }} .reseller-card {{ margin:12px 0; padding:14px; border:1px solid var(--line); border-radius:10px; background:#0b1321; }} .reseller-summary {{ display:grid; gap:10px; }} .reseller-values {{ display:flex; flex-wrap:wrap; gap:8px 16px; color:var(--muted); }} .reseller-children {{ margin-top:12px; }} .reseller-child-actions {{ white-space:nowrap; min-width:260px; }} .reseller-top-up-form {{ display:inline-flex; align-items:center; vertical-align:middle; gap:5px; }} .reseller-top-up-form input {{ width:115px; min-width:0; margin:0; padding:8px 9px; }} .reseller-top-up-button {{ min-width:34px; padding:8px 11px; font-size:18px; line-height:1; }} .reseller-freeze-form {{ display:inline-block; vertical-align:middle; margin-left:6px !important; }} .service-group {{ margin:18px 0 28px; }} .service-group h3 {{ margin-bottom:8px; }} .service-group-toggle {{ color:var(--text); background:transparent; padding:0; font-size:16px; }} .service-group-toggle:hover,.service-group-toggle:focus {{ color:var(--accent); }} .api-preview {{ display:block; max-width:180px; overflow:hidden; text-overflow:ellipsis; }} .empty {{ text-align:center; color:var(--muted); }} .edit-row>td {{ padding:0 8px 14px; border-bottom:1px solid var(--line); }} .edit-form {{ margin:0; padding:18px; border:1px solid var(--line); border-radius:12px; background:#0b1321; }} .edit-actions {{ display:flex; gap:8px; margin-top:16px; }} .delete-form {{ margin-top:4px; }} .reseller-page {{ width:min(1500px,calc(100% - 32px)); margin-top:26px; }} .reseller-login-page {{ max-width:560px; }} .reseller-login-card {{ position:relative; overflow:hidden; }} .reseller-language-switch {{ display:flex; justify-content:flex-end; align-items:center; gap:6px; margin-bottom:18px; color:var(--muted); font-size:14px; }} .reseller-language-switch a {{ color:var(--muted); text-decoration:none; padding:5px 8px; border-radius:7px; }} .reseller-language-switch a:hover,.reseller-language-switch a.active {{ color:#08101e; background:var(--accent); }} .reseller-eyebrow {{ color:var(--accent); font-size:12px; font-weight:800; letter-spacing:.14em; text-transform:uppercase; }} .reseller-intro {{ color:var(--muted); max-width:720px; }} .reseller-header {{ display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:20px; }} .reseller-header .reseller-language-switch {{ margin:0; }} .reseller-access-row {{ display:flex; justify-content:space-between; align-items:center; gap:20px; margin-top:22px; padding:16px; border:1px solid var(--line); border-radius:12px; background:rgba(8,19,33,.72); }} .reseller-label {{ color:var(--muted); font-size:13px; margin-bottom:5px; }} .reseller-access-code {{ color:#b9d4ff; font-size:18px; letter-spacing:.07em; word-break:break-all; }} .reseller-regenerate-form {{ flex:0 0 auto; margin:0; }} .reseller-balance-card {{ border-color:rgba(109,156,255,.38); background:linear-gradient(145deg,rgba(24,43,77,.94),rgba(17,26,42,.98)); }} .section-heading {{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px; }} .reseller-child-count {{ padding:6px 10px; border-radius:999px; color:#b9d4ff; background:rgba(109,156,255,.12); white-space:nowrap; font-size:13px; }} .reseller-balance-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:20px 0 14px; }} .balance-metric {{ display:flex; flex-direction:column; gap:5px; min-width:0; padding:15px; border:1px solid var(--line); border-radius:11px; background:rgba(8,19,33,.66); }} .balance-metric span {{ color:var(--muted); font-size:13px; }} .balance-metric strong {{ color:var(--text); font-size:24px; line-height:1.15; }} .balance-available {{ border-color:rgba(95,213,160,.45); }} .balance-available strong {{ color:var(--success); }} .reseller-table-wrap {{ border:1px solid var(--line); border-radius:11px; }} .reseller-child-table {{ min-width:1240px; }} .reseller-child-table th,.reseller-child-table td {{ white-space:nowrap; }} .reseller-child-row:hover {{ background:rgba(109,156,255,.05); }} .reseller-key-cell {{ display:block; max-width:245px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }} .reseller-status {{ display:inline-flex; align-items:center; padding:4px 8px; border-radius:999px; font-size:13px; }} .status-active {{ color:#b9f6d6; background:rgba(95,213,160,.14); }} .status-frozen {{ color:#c9eeff; background:rgba(131,213,244,.16); }} .reseller-child-actions {{ display:flex; align-items:center; gap:8px; min-width:270px; }} .reseller-freeze-form {{ margin-left:0 !important; }} .reseller-top-up-form {{ flex:0 0 auto; }} .reseller-top-up-form input {{ width:90px; }} 
@media(max-width:650px) {{ .page,.admin-page {{ width:min(100% - 20px,960px); margin-top:12px; }} .card {{ padding:18px; border-radius:12px; }} h1 {{ font-size:24px; }} .details,.create-form,.edit-grid,.info-actions {{ grid-template-columns:1fr; }} .title-row {{ display:block; }} .title-row form {{ margin-top:12px; }} .keys-search-controls {{ flex-wrap:wrap; }} .keys-search-controls input {{ flex-basis:100%; }} .pagination {{ align-items:start; flex-direction:column; }} }}
</style></head><body>{content}<script data-tokens-admin-refresh>
(function() {{
  var root=document.querySelector('.instructions');
  var provider=(root && root.dataset.defaultProvider) || 'claude';
  var app=(root && root.dataset.defaultApp) || 'Claude Code CLI';
  var os='Windows';
  var descriptions={{'VS Code':'Настройка API в VS Code','App':'Настройка приложения','CLI':'Ручная CLI-настройка','Claude Code CLI':'Ручная CLI-настройка','Claude App':'Настройка Claude App','Hermes Desktop':'Настройка Hermes Desktop','Cheap Code':'Настройка Cheap Code','Grok Build':'Настройка Grok Build','Kimi Code CLI':'Настройка Kimi Code CLI','ZCode':'Настройка ZCode','Pi':'Настройка Pi','OpenCode':'Настройка OpenCode','Cursor':'Настройка Cursor'}};
  if(document.documentElement.lang==='en') descriptions={{'VS Code':'API setup in VS Code','App':'Application setup','CLI':'Manual CLI setup','Claude Code CLI':'Manual CLI setup','Claude App':'Claude App setup','Hermes Desktop':'Hermes Desktop setup','Cheap Code':'Cheap Code setup','Grok Build':'Grok Build setup','Kimi Code CLI':'Kimi Code CLI setup','ZCode':'ZCode setup','Pi':'Pi setup','OpenCode':'OpenCode setup','Cursor':'Cursor setup'}};
  function update() {{
    var method=document.getElementById('selected-method'), description=document.getElementById('selected-description'), selectedOs=document.getElementById('selected-os');
    if(method) method.textContent=app;
    if(description) description.textContent=descriptions[app]||'Инструкция по настройке';
    if(selectedOs) selectedOs.textContent=os;
    document.querySelectorAll('[data-instruction-provider]').forEach(function(item) {{ item.classList.toggle('active',item.dataset.instructionProvider===provider); }});
    document.querySelectorAll('[data-instruction-app]').forEach(function(item) {{ var visible=item.dataset.provider===provider; item.hidden=!visible; item.classList.toggle('active',visible && item.dataset.instructionApp===app); }});
    var current=document.querySelector('.instruction-card[data-provider-app=\"'+app+'\"][data-instruction-system=\"'+os+'\"]');
    if(!current) {{ var first=document.querySelector('.instruction-card[data-provider-app=\"'+app+'\"]'); if(first) os=first.dataset.instructionSystem; if(selectedOs) selectedOs.textContent=os; }}
    document.querySelectorAll('[data-instruction-os]').forEach(function(item) {{ var supported=document.querySelector('.instruction-card[data-provider-app=\"'+app+'\"][data-instruction-system=\"'+item.dataset.instructionOs+'\"]'); item.hidden=!supported; item.classList.toggle('active',item.dataset.instructionOs===os); }});
    document.querySelectorAll('.instruction-card').forEach(function(item) {{ item.hidden=!(item.dataset.providerApp===app && item.dataset.instructionSystem===os); }});
  }}
  document.querySelectorAll('[data-instruction-provider]').forEach(function(button) {{ button.addEventListener('click',function() {{ provider=button.dataset.instructionProvider; var first=document.querySelector('[data-instruction-app][data-provider="'+provider+'"]'); if(first) app=first.dataset.instructionApp; update(); }}); }});
  document.querySelectorAll('[data-instruction-app]').forEach(function(button) {{ button.addEventListener('click',function() {{ app=button.dataset.instructionApp; provider=button.dataset.provider; update(); }}); }});
  document.querySelectorAll('[data-instruction-os]').forEach(function(button) {{ button.addEventListener('click',function() {{ os=button.dataset.instructionOs; update(); }}); }});
  document.querySelectorAll('.instruction-mode').forEach(function(button) {{ button.addEventListener('click',function() {{ var card=button.closest('.instruction-card'), mode=button.dataset.instructionMode; if(!card||!mode) return; card.querySelectorAll('.instruction-mode').forEach(function(item) {{ var selected=item===button; item.classList.toggle('active',selected); item.setAttribute('aria-selected',String(selected)); }}); card.querySelectorAll('[data-instruction-mode-panel]').forEach(function(panel) {{ panel.hidden=panel.dataset.instructionModePanel!==mode; }}); }}); }});
  document.querySelectorAll('.copy-instruction').forEach(function(button) {{ button.addEventListener('click',function() {{ var target=document.getElementById(button.dataset.copyTarget); if(!target) return; navigator.clipboard.writeText(target.textContent).then(function() {{ button.textContent=button.dataset.copiedLabel; setTimeout(function() {{ button.textContent=button.dataset.copyLabel; }},1500); }}); }}); }});
  var balance=document.getElementById('token-balance');
  if(balance) fetch('/ai/tokens/balance',{{cache:'no-store'}}).then(function(response) {{ return response.ok ? response.json() : Promise.reject(); }}).then(function(data) {{ if(data.ok) balance.textContent=data.formatted+' '+(balance.dataset.separator||'/')+' '+data.token_limit_formatted; }}).catch(function() {{ /* Keep the locally saved balance visible. */ }});
  var bonusButton=document.getElementById('get-bonus'), bonusClaim=document.getElementById('bonus-claim');
  if(bonusButton&&bonusClaim) bonusButton.addEventListener('click',function() {{ bonusClaim.hidden=!bonusClaim.hidden; if(!bonusClaim.hidden) {{ var promoInput=document.getElementById('promo-code'); if(promoInput) promoInput.focus(); }} }});
  function copyApiKey(cell) {{ var value=cell.dataset.copyApiKey; if(!value) return; navigator.clipboard.writeText(value).then(function() {{ var old=cell.title; cell.title='API key скопирован'; setTimeout(function() {{ cell.title=old; }},1500); }}); }}
  document.querySelectorAll('.copyable-api-key').forEach(function(cell) {{ cell.addEventListener('click',function() {{ copyApiKey(cell); }}); cell.addEventListener('keydown',function(event) {{ if(event.key==='Enter'||event.key===' ') {{ event.preventDefault(); copyApiKey(cell); }} }}); }});
  function copyCreatedValue(container, button, value) {{ if(!button) return; button.addEventListener('click',function() {{ navigator.clipboard.writeText(value||'').then(function() {{ var old=button.textContent; button.textContent='Скопировано'; setTimeout(function() {{ button.textContent=old; }},1500); }}); }}); }}
  document.querySelectorAll('.copy-created-codes').forEach(function(container) {{ copyCreatedValue(container,container.querySelector('.copy-created-codes-button'),container.dataset.createdCodes); copyCreatedValue(container,container.querySelector('.copy-created-links-button'),container.dataset.createdLinks); }});
  document.querySelectorAll('[data-reseller-children]').forEach(function(button) {{ button.addEventListener('click',function() {{ var card=button.closest('.reseller-card'), children=card&&card.querySelector('.reseller-children'); if(!children) return; children.hidden=!children.hidden; button.textContent=children.hidden?'Производные ключи':'Скрыть производные ключи'; }}); }});
  // Keep the admin shell in place: forms and table navigation fetch fresh
  // markup, replace only <main>, and re-run this initializer. This preserves
  // the current document instead of doing a browser-level page reload.
  var adminPage=document.querySelector('.admin-page');
  function refreshAdmin(url, options) {{
    if(!adminPage) {{ window.location.assign(url); return; }}
    adminPage.classList.add('is-updating');
    fetch(url, Object.assign({{credentials:'same-origin',headers:{{'X-Requested-With':'tokens-admin-fragment'}}}}, options||{{}})).then(function(response) {{
      if(!(response.headers.get('content-type')||'').includes('text/html')) throw new Error('admin refresh failed');
      return response.text();
    }}).then(function(markup) {{
      var documentMarkup=new DOMParser().parseFromString(markup,'text/html'), replacement=documentMarkup.querySelector('.admin-page');
      if(!replacement) throw new Error('admin fragment missing');
      adminPage.replaceWith(replacement);
      var nextUrl=options&&options.historyUrl ? options.historyUrl : url;
      window.history.replaceState({{}},'',nextUrl);
      var script=document.querySelector('script[data-tokens-admin-refresh]');
      if(script) {{ var cloned=document.createElement('script'); cloned.dataset.tokensAdminRefresh=''; cloned.text=script.text; script.replaceWith(cloned); }}
    }}).catch(function() {{ window.location.assign(url); }});
  }}
  if(adminPage) {{
    adminPage.addEventListener('submit',function(event) {{
      var form=event.target;
      if(!(form instanceof HTMLFormElement) || !form.matches('.create-form,.keys-search-form,.inline-freeze-form')) return;
      event.preventDefault();
      var method=(form.method||'get').toUpperCase(), target=new URL(form.action,window.location.href), data=new FormData(form);
      if(method==='GET') {{ target.search=''; data.forEach(function(value,name) {{ target.searchParams.append(name,String(value)); }}); refreshAdmin(target.toString(),{{historyUrl:target.pathname+target.search}}); return; }}
      var payload=new URLSearchParams(); data.forEach(function(value,name) {{ payload.append(name,String(value)); }});
      // A POST handler returns the new markup, but the address bar must stay
      // on the GET page. Otherwise a browser refresh would repeat a POST URL.
      refreshAdmin(target.toString(),{{method:method,body:payload.toString(),headers:{{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8','X-Requested-With':'tokens-admin-fragment'}},historyUrl:'/ai/tokens/adm'+target.search}});
    }});
    adminPage.addEventListener('click',function(event) {{
      var reset=event.target.closest('.reset-search'), sort=event.target.closest('.sort-link'), pageLink=event.target.closest('.page-link[href]');
      var link=reset||sort||pageLink;
      if(!link) return;
      event.preventDefault(); refreshAdmin(link.href,{{historyUrl:link.pathname+link.search}});
    }});
  }}
  var keyTable=document.getElementById('keys-table'), tableWrap=document.getElementById('keys-table-wrap'), groups=document.getElementById('keys-table-groups');
  if(keyTable&&tableWrap&&groups) {{
    var body=keyTable.tBodies[0], originalRows=Array.prototype.slice.call(body.rows).filter(function(row) {{ return row.dataset.service; }}), editRows=new Map(), grouped=false;
    originalRows.forEach(function(row) {{ var edit=row.nextElementSibling; if(edit&&edit.classList.contains('edit-row')) editRows.set(row,edit); }});
    function toggleEdit(button) {{ var row=button.closest('tr'), edit=row&&row.nextElementSibling; if(edit&&edit.classList.contains('edit-row')) edit.hidden=!edit.hidden; }}
    function toggleGroups() {{
      grouped=!grouped; var groupHeader=keyTable.querySelector('[data-group-service]'); if(groupHeader) groupHeader.classList.toggle('grouped',grouped);
      if(!grouped) {{ groups.hidden=true; tableWrap.hidden=false; return; }}
      groups.innerHTML=''; var byService={{}};
      originalRows.forEach(function(row) {{ var service=row.dataset.service||''; (byService[service]||(byService[service]=[])).push(row); }});
      Object.keys(byService).sort(function(a,b) {{ return a.localeCompare(b); }}).forEach(function(service) {{
        var section=document.createElement('section'), heading=document.createElement('h3'), toggle=document.createElement('button'), wrap=document.createElement('div'), table=keyTable.cloneNode(false), head=keyTable.tHead.cloneNode(true), newBody=document.createElement('tbody');
        section.className='service-group'; toggle.type='button'; toggle.className='service-group-toggle'; toggle.textContent=service; toggle.setAttribute('aria-expanded','true');
        toggle.addEventListener('click',function() {{ wrap.hidden=!wrap.hidden; toggle.setAttribute('aria-expanded',String(!wrap.hidden)); }});
        heading.appendChild(toggle); wrap.className='table-wrap'; table.removeAttribute('id'); table.appendChild(head);
        byService[service].forEach(function(row) {{ newBody.appendChild(row.cloneNode(true)); var edit=editRows.get(row); if(edit) newBody.appendChild(edit.cloneNode(true)); }});
        table.appendChild(newBody); wrap.appendChild(table); section.appendChild(heading); section.appendChild(wrap); groups.appendChild(section);
      }});
      tableWrap.hidden=true; groups.hidden=false;
    }}
    function handleTableClick(event) {{
      var serviceHeader=event.target.closest('[data-group-service]');
      if(serviceHeader) {{ event.preventDefault(); toggleGroups(); return; }}
      var apiCell=event.target.closest('.copyable-api-key'); if(apiCell) {{ copyApiKey(apiCell); return; }}
      var editButton=event.target.closest('[data-edit]'); if(editButton) toggleEdit(editButton);
    }}
    keyTable.addEventListener('click',handleTableClick);
    groups.addEventListener('click',handleTableClick);
  }}
  update();
}})();
</script></body></html>"""


__all__ = [
    "CheapVibeCodeClient", "SecondaryKeyClient", "SERVICE_OPTIONS", "RESELLING_SERVICE", "RESELLER_SERVICE_OPTIONS", "StoredTokenKey", "StoredResellerKey", "TokenAdmin", "TokenKey", "TokenKeyStore", "TokenKeyStores", "ResellerKey", "ResellerKeyStore", "ResellerKeyStores", "ResellerOperation", "ResellerOperationStore",
    "create_token_key_stores", "create_reseller_key_stores", "indexed_token_store_path", "indexed_reseller_store_path",
    "create_tokens_routes", "default_instruction_choice", "generate_access_code",
    "instruction_command", "instruction_remove_command", "instruction_steps", "manual_instruction_command", "manual_instruction_note", "manual_download_buttons",
    "normalize_access_code", "trusted_secondary_remaining", "used_tokens_from_remaining",
]
