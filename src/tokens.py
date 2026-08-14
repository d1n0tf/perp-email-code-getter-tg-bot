"""Secondary API key activation and administration service."""
from __future__ import annotations

import asyncio
import html
import json
import secrets
import string
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

TOKENS_ACCESS_COOKIE = "tokens_access_key"
TOKENS_ADMIN_COOKIE = "tokens_admin_session"
TOKENS_USER_CSRF_COOKIE = "tokens_user_csrf"
TOKENS_ADMIN_CSRF_COOKIE = "tokens_admin_csrf"
TOKEN_CODE_ALPHABET = string.ascii_uppercase + string.digits
SERVICE_OPTIONS = (
    "Claude", "OpenAI", "Google", "Grok", "DeepSeek", "Alibaba Cloud",
    "Z.AI (GLM)", "KIMI", "Xiaomi", "NVIDIA",
)

TOKEN_TEXT = {
    "ru": {
        "switch": "English", "title": "Сервис активации", "intro": "Здесь вы сможете активировать и использовать API ключи с токенами для сервисов Claude / Codex / Grok / Google и другие.<br>Для этого следуйте инструкции ниже.",
        "activation": "Активация ключа", "access": "Ключ доступа", "access_hint": "➥ Здесь вводите ключ доступа который получили от продавца.", "activate": "АКТИВИРОВАТЬ КЛЮЧ",
        "info": "ИНФОРМАЦИЯ", "service": "Подключенный сервис:", "activated": "Дата активации ключа:", "limit": "Количество токенов:", "remaining": "Оставшиеся токены:", "status": "Статус:", "api": "API ключ:",
        "exhausted": "Токены были полностью использованы. Дата: {date}", "not_activated": "Не активирован", "activated_status": "Активирован ({date})", "exhausted_status": "Исчерпан ({date})",
        "instructions": "ИНСТРУКЦИЯ ПО ИСПОЛЬЗОВАНИЮ", "instructions_intro": "Если вы не знаете как использовать API ключ, мы поможем, для начала выберите через что вы будете использовать API",
        "choose_service": "1. Выберите сервис", "choose_app": "2. Выберите приложение", "choose_os": "3. Выберите операционную систему", "selected": "Выбрано:", "description": "Описание:", "os": "Операционная система:",
        "manual": "Ручная настройка с готовым скриптом", "shell_windows": "PowerShell", "shell_other": "Терминал", "open": "Откройте {shell} на машине, где запускается выбранное приложение.", "run": "Скопируйте и выполните скрипт ниже. API ключ уже подставлен автоматически.", "restart": "Перезапустите приложение после завершения настройки.", "warning": "⚠️ Копируйте скрипт полностью или воспользуйтесь кнопкой «Скопировать».", "copy": "Скопировать", "copied": "Скопировано", "remove_hint": "Для удаления настройки повторите инструкцию из документации сервиса или удалите добавленные строки из конфигурации.",
        "remaining_sep": "из", "remove_script": "Скрипт удаления настройки",
        "grok_open_windows": "Открой PowerShell.", "grok_open_other": "Открой терминал.", "grok_run": "Выполни команду ниже.", "grok_restart": "Перезапусти терминал и введи grok.",
        "required": "Введите ключ доступа.", "missing": "Ключ доступа не существует.", "success": "Ключ успешно активирован. API-ключ готов к использованию.",
        "balance_unavailable": "Не удалось обновить баланс токенов.",
    },
    "en": {
        "switch": "Русский", "title": "Activation Service", "intro": "Activate and use token-based API keys for Claude / Codex / Grok / Google and other services.<br>Follow the instructions below.",
        "activation": "Key activation", "access": "Access key", "access_hint": "➥ Enter the access key received from the seller.", "activate": "ACTIVATE KEY",
        "info": "INFORMATION", "service": "Connected service:", "activated": "Key activation date:", "limit": "Token amount:", "remaining": "Remaining tokens:", "status": "Status:", "api": "API key:",
        "exhausted": "All tokens have been used. Date: {date}", "not_activated": "Not activated", "activated_status": "Activated ({date})", "exhausted_status": "Exhausted ({date})",
        "instructions": "INSTRUCTIONS FOR USE", "instructions_intro": "If you do not know how to use the API key, we can help. First choose how you will use the API.",
        "choose_service": "1. Choose a service", "choose_app": "2. Choose an application", "choose_os": "3. Choose an operating system", "selected": "Selected:", "description": "Description:", "os": "Operating system:",
        "manual": "Manual setup with a ready-made script", "shell_windows": "PowerShell", "shell_other": "Terminal", "open": "Open {shell} on the machine where the selected application runs.", "run": "Copy and run the script below. The API key is already inserted automatically.", "restart": "Restart the application after setup is complete.", "warning": "⚠️ Copy the complete script or use the «Copy» button.", "copy": "Copy", "copied": "Copied", "remove_hint": "To remove the setup, follow the service documentation or remove the added configuration lines.",
        "remaining_sep": "of", "remove_script": "Uninstall script",
        "grok_open_windows": "Open PowerShell.", "grok_open_other": "Open the terminal.", "grok_run": "Run the command below.", "grok_restart": "Restart the terminal and type grok.",
        "required": "Enter an access key.", "missing": "The access key does not exist.", "success": "The key was activated successfully. The API key is ready to use.",
        "balance_unavailable": "Could not refresh the token balance.",
    },
}


def token_locale(value: str | None) -> str:
    return "en" if value == "en" else "ru"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: object) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def normalize_access_code(value: str) -> str:
    return "".join(value.strip().upper().split())


def generate_access_code(existing: set[str]) -> str:
    while True:
        code = "".join(secrets.choice(TOKEN_CODE_ALPHABET) for _ in range(20))
        if code not in existing:
            return code


def format_tokens(value: int) -> str:
    return f"{max(0, value):,}".replace(",", " ")


def format_datetime(value: datetime | None) -> str:
    return value.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M") if value else "\u2014"


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
        )

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["created_at"] = self.created_at.astimezone(timezone.utc).isoformat()
        result["activated_at"] = self.activated_at.astimezone(timezone.utc).isoformat() if self.activated_at else None
        result["exhausted_at"] = self.exhausted_at.astimezone(timezone.utc).isoformat() if self.exhausted_at else None
        return result


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

    async def get_token_balance(self, *, api_key: str) -> int: ...

    async def get_primary_token_balance(self) -> int: ...


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
            raise RuntimeError(f"Key service rejected the request: {detail}")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("The key service returned an invalid response.") from exc
        api_key = payload.get("key") if isinstance(payload, dict) else None
        if not isinstance(api_key, str) or not api_key.strip():
            raise RuntimeError("The key service response did not contain an API key.")
        return api_key.strip()

    async def get_token_balance(self, *, api_key: str) -> int:
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
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise RuntimeError("Could not connect to the balance service.") from exc
        if status >= 400:
            raise RuntimeError("The balance service rejected the request.")
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
    store: TokenKeyStore,
    key_client: SecondaryKeyClient,
    admin_password: str | None,
) -> None:
    """Register the public secondary-key page and its password-protected admin page."""
    app.state.token_key_store = store
    app.state.secondary_key_client = key_client
    admin_sessions: set[str] = set()
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

    def is_admin(request: Request) -> bool:
        session = request.cookies.get(TOKENS_ADMIN_COOKIE, "")
        return bool(session and session in admin_sessions)

    async def read_primary_remaining() -> int | None:
        try:
            return await key_client.get_primary_token_balance()
        except RuntimeError:
            return None

    async def refresh_stored_balance(
        key: TokenKey,
        primary_remaining: int | None = None,
    ) -> TokenKey:
        try:
            reported = await key_client.get_token_balance(api_key=key.api_key)
        except RuntimeError:
            return key
        remaining = trusted_secondary_remaining(reported, key.token_limit, primary_remaining)
        if remaining is None:
            return key
        updated = await store.apply_remaining(key.id, remaining)
        return updated or key

    async def refresh_stored_balances(keys: list[TokenKey]) -> list[TokenKey]:
        if not keys:
            return keys
        primary_remaining = await read_primary_remaining()
        refreshed = await asyncio.gather(
            *(refresh_stored_balance(key, primary_remaining) for key in keys)
        )
        return sorted(refreshed, key=lambda key: key.id, reverse=True)

    async def admin_response(
        request: Request,
        *,
        error: str = "",
        notice: str = "",
        status_code: int = 200,
    ) -> HTMLResponse:
        csrf_token = secrets.token_urlsafe(32)
        authenticated = is_admin(request)
        keys = await refresh_stored_balances(await store.list()) if authenticated else []
        response = render_tokens_admin(
            csrf_token=csrf_token,
            authenticated=authenticated,
            password_configured=bool(admin_password),
            keys=keys,
            error=error,
            notice=notice,
        )
        response.status_code = status_code
        return attach_csrf(response, csrf_token, TOKENS_ADMIN_CSRF_COOKIE, "/ai/tokens/adm")

    async def page(request: Request) -> HTMLResponse:
        locale = token_locale(request.query_params.get("lang"))
        access_code = request.cookies.get(TOKENS_ACCESS_COOKIE, "")
        key = await store.get_by_code(access_code) if access_code else None
        if key is not None:
            key = await refresh_stored_balance(key, await read_primary_remaining())
        error = exhausted_message(key, locale) if key is not None and key.is_exhausted else ""
        return user_response(locale=locale, key=key, error=error)

    async def activate(request: Request) -> HTMLResponse:
        form = await read_form(request)
        locale = token_locale(form.get("lang"))
        if not valid_csrf(request, form, TOKENS_USER_CSRF_COOKIE):
            return RedirectResponse(url=f"/ai/tokens?lang={locale}", status_code=303)
        access_code = normalize_access_code(form.get("access_code", ""))
        if not access_code:
            return user_response(locale=locale, error=TOKEN_TEXT[locale]["required"], status_code=400)
        key = await store.activate(access_code)
        if key is None:
            return user_response(locale=locale, error=TOKEN_TEXT[locale]["missing"], submitted_code=access_code, status_code=404)
        key = await refresh_stored_balance(key, await read_primary_remaining())
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
        key = await store.get_by_code(access_code) if access_code else None
        if key is None:
            return JSONResponse({"ok": False, "error": "access_key_missing"}, status_code=401, headers={"Cache-Control": "no-store"})
        try:
            reported = await key_client.get_token_balance(api_key=key.api_key)
        except RuntimeError:
            return JSONResponse({"ok": False, "error": "balance_unavailable"}, status_code=502, headers={"Cache-Control": "no-store"})
        remaining = trusted_secondary_remaining(
            reported, key.token_limit, await read_primary_remaining()
        )
        if remaining is None:
            return JSONResponse({"ok": False, "error": "balance_unavailable"}, status_code=502, headers={"Cache-Control": "no-store"})
        updated = await store.apply_remaining(key.id, remaining)
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

    async def admin_page(request: Request) -> HTMLResponse:
        return await admin_response(request)

    async def admin_login(request: Request) -> HTMLResponse:
        form = await read_form(request)
        if not valid_csrf(request, form, TOKENS_ADMIN_CSRF_COOKIE):
            return RedirectResponse(url="/ai/tokens/adm", status_code=303)
        if not admin_password:
            return await admin_response(
                request,
                error="\u0410\u0434\u043c\u0438\u043d-\u043f\u0430\u043d\u0435\u043b\u044c \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430: \u0437\u0430\u0434\u0430\u0439\u0442\u0435 TOKENS_ADMIN_PASSWORD \u0432 .env.",
                status_code=503,
            )
        supplied = form.get("password", "")
        if not secrets.compare_digest(supplied, admin_password):
            return await admin_response(
                request,
                error="\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u043f\u0430\u0440\u043e\u043b\u044c.",
                status_code=401,
            )
        session = secrets.token_urlsafe(32)
        admin_sessions.add(session)
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
        admin_sessions.discard(session)
        response = await admin_response(request, notice="\u0412\u044b \u0432\u044b\u0448\u043b\u0438 \u0438\u0437 \u0430\u0434\u043c\u0438\u043d-\u043f\u0430\u043d\u0435\u043b\u0438.")
        response.delete_cookie(TOKENS_ADMIN_COOKIE, path="/ai/tokens/adm")
        return response

    async def admin_create(request: Request) -> HTMLResponse:
        form = await read_form(request)
        if not is_admin(request):
            return await admin_response(request, error="\u0421\u0435\u0441\u0441\u0438\u044f \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430 \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0430.", status_code=401)
        if not valid_csrf(request, form, TOKENS_ADMIN_CSRF_COOKIE):
            return RedirectResponse(url="/ai/tokens/adm", status_code=303)
        service = form.get("service", "")
        name = form.get("name", "").strip()
        token_limit = positive_int(form.get("token_limit", ""))
        quantity = positive_int(form.get("quantity", ""))
        if service not in SERVICE_OPTIONS:
            return await admin_response(request, error="\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b\u0439 \u0441\u0435\u0440\u0432\u0438\u0441.", status_code=400)
        if not name:
            return await admin_response(request, error="\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u043a\u043b\u044e\u0447\u0430.", status_code=400)
        if token_limit is None or token_limit < 1:
            return await admin_response(request, error="\u041a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e \u0442\u043e\u043a\u0435\u043d\u043e\u0432 \u0434\u043e\u043b\u0436\u043d\u043e \u0431\u044b\u0442\u044c \u043f\u043e\u043b\u043e\u0436\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u043c \u0446\u0435\u043b\u044b\u043c \u0447\u0438\u0441\u043b\u043e\u043c.", status_code=400)
        if quantity is None or not 1 <= quantity <= 100:
            return await admin_response(request, error="\u041a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e \u043a\u043b\u044e\u0447\u0435\u0439 \u0434\u043e\u043b\u0436\u043d\u043e \u0431\u044b\u0442\u044c \u043e\u0442 1 \u0434\u043e 100.", status_code=400)
        async with creation_lock:
            existing = await store.list()
            existing_codes = {key.access_code for key in existing}
            next_id = max((key.id for key in existing), default=0) + 1
            records: list[TokenKey] = []
            try:
                for index in range(quantity):
                    api_key = await key_client.create_key(name=name, token_limit=token_limit)
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
                    await store.add_many(records)
                    return await admin_response(
                        request,
                        error=(
                            f"\u0421\u043e\u0437\u0434\u0430\u043d\u043e \u043a\u043b\u044e\u0447\u0435\u0439: {len(records)} \u0438\u0437 {quantity}. "
                            f"\u041e\u0441\u0442\u0430\u043b\u044c\u043d\u044b\u0435 \u043d\u0435 \u0441\u043e\u0437\u0434\u0430\u043d\u044b: {str(exc)}"
                        ),
                        status_code=502,
                    )
                return await admin_response(
                    request,
                    error=f"\u041a\u043b\u044e\u0447\u0438 \u043d\u0435 \u0441\u043e\u0437\u0434\u0430\u043d\u044b: {str(exc)}",
                    status_code=502,
                )
            await store.add_many(records)
        return await admin_response(request, notice=f"\u0421\u043e\u0437\u0434\u0430\u043d\u043e \u043a\u043b\u044e\u0447\u0435\u0439: {len(records)}.")

    async def admin_update(request: Request, key_id: int) -> HTMLResponse:
        form = await read_form(request)
        if not is_admin(request):
            return await admin_response(request, error="\u0421\u0435\u0441\u0441\u0438\u044f \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430 \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0430.", status_code=401)
        if not valid_csrf(request, form, TOKENS_ADMIN_CSRF_COOKIE):
            return RedirectResponse(url="/ai/tokens/adm", status_code=303)
        current = await store.get(key_id)
        if current is None:
            return await admin_response(request, error="\u041a\u043b\u044e\u0447 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d.", status_code=404)
        try:
            updated = record_from_admin_form(form, current, await store.list())
        except ValueError as exc:
            return await admin_response(request, error=str(exc), status_code=400)
        await store.update(key_id, updated)
        return await admin_response(request, notice=f"\u041a\u043b\u044e\u0447 #{key_id} \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d.")

    async def admin_delete(request: Request, key_id: int) -> HTMLResponse:
        form = await read_form(request)
        if not is_admin(request):
            return await admin_response(request, error="\u0421\u0435\u0441\u0441\u0438\u044f \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430 \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0430.", status_code=401)
        if not valid_csrf(request, form, TOKENS_ADMIN_CSRF_COOKIE):
            return RedirectResponse(url="/ai/tokens/adm", status_code=303)
        if not await store.delete(key_id):
            return await admin_response(request, error="\u041a\u043b\u044e\u0447 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d.", status_code=404)
        return await admin_response(request, notice=f"\u041a\u043b\u044e\u0447 #{key_id} \u0443\u0434\u0430\u043b\u0435\u043d.")

    app.add_api_route("/ai/tokens", page, methods=["GET"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens", activate, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/balance", balance, methods=["GET"], response_model=None)
    app.add_api_route("/ai/tokens/adm", admin_page, methods=["GET"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/adm/login", admin_login, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/adm/logout", admin_logout, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/adm/create", admin_create, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/adm/{key_id}/update", admin_update, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/tokens/adm/{key_id}/delete", admin_delete, methods=["POST"], response_class=HTMLResponse, response_model=None)


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
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def record_from_admin_form(form: dict[str, str], current: TokenKey, all_keys: list[TokenKey]) -> TokenKey:
    service = form.get("service", "")
    if service not in SERVICE_OPTIONS:
        raise ValueError("\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b\u0439 \u0441\u0435\u0440\u0432\u0438\u0441.")
    name = form.get("name", "").strip()
    api_key = form.get("api_key", "").strip()
    access_code = normalize_access_code(form.get("access_code", ""))
    token_limit = positive_int(form.get("token_limit", ""))
    used_tokens = positive_int(form.get("used_tokens", ""))
    if not name:
        raise ValueError("\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u043a\u043b\u044e\u0447\u0430 \u043d\u0435 \u043b\u043e\u0436\u0435\u0442 \u0431\u044b\u0442\u044c \u043f\u0443\u0441\u0442\u044b\u043c.")
    if not api_key:
        raise ValueError("API key \u043d\u0435 \u043c\u043e\u0436\u0435\u0442 \u0431\u044b\u0442\u044c \u043f\u0443\u0441\u0442\u044b\u043c.")
    if len(access_code) != 20 or any(char not in TOKEN_CODE_ALPHABET for char in access_code):
        raise ValueError("\u041a\u043b\u044e\u0447 \u0434\u043e\u0441\u0442\u0443\u043f\u0430 \u0434\u043e\u043b\u0436\u0435\u043d \u0441\u043e\u0441\u0442\u043e\u044f\u0442\u044c \u0438\u0437 20 \u0437\u0430\u0433\u043b\u0430\u0432\u043d\u044b\u0445 \u043b\u0430\u0442\u0438\u043d\u0441\u043a\u0438\u0445 \u0431\u0443\u043a\u0432 \u0438 \u0446\u0438\u0444\u0440.")
    if any(key.id != current.id and key.access_code == access_code for key in all_keys):
        raise ValueError("\u0422\u0430\u043a\u043e\u0439 \u043a\u043b\u044e\u0447 \u0434\u043e\u0441\u0442\u0443\u043f\u0430 \u0443\u0436\u0435 \u0441\u0443\u0449\u0435\u0441\u0442\u0432\u0443\u0435\u0442.")
    if token_limit is None or token_limit < 1:
        raise ValueError("\u041a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e \u0442\u043e\u043a\u0435\u043d\u043e\u0432 \u0434\u043e\u043b\u0436\u043d\u043e \u0431\u044b\u0442\u044c \u043f\u043e\u043b\u043e\u0436\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u043c \u0446\u0435\u043b\u044b\u043c \u0447\u0438\u0441\u043b\u043e\u043c.")
    if used_tokens is None or not 0 <= used_tokens <= token_limit:
        raise ValueError("\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u043e \u0434\u043e\u043b\u0436\u043d\u043e \u0431\u044b\u0442\u044c \u043e\u0442 0 \u0434\u043e \u043b\u0438\u043c\u0438\u0442\u0430 \u0442\u043e\u043a\u0435\u043d\u043e\u0432.")
    try:
        created_at = parse_datetime(form.get("created_at", ""))
        activated_raw = form.get("activated_at", "").strip()
        activated_at = parse_datetime(activated_raw) if activated_raw else None
        exhausted_raw = form.get("exhausted_at", "").strip()
        exhausted_at = parse_datetime(exhausted_raw) if exhausted_raw else None
    except (TypeError, ValueError):
        raise ValueError("\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u044b\u0435 \u0434\u0430\u0442\u044b.") from None
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
    info = render_key_information(key, locale) if key is not None else ""
    instructions = render_instructions(key, locale) if key is not None and not key.is_exhausted else ""
    opposite_locale = "ru" if locale == "en" else "en"
    content = f"""
    <main class='page'>
      <nav class='language-switch' aria-label='Language'><a href='/ai/tokens?lang={opposite_locale}'>{html.escape(text['switch'])}</a></nav>
      <section class='card intro'><h1>{html.escape(text['title'])}</h1><p>{text['intro']}</p></section>
      <section class='card'><h2>{html.escape(text['activation'])}</h2>{flash}
        <form method='post' action='/ai/tokens' class='activation-form'>
          <input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'><input type='hidden' name='lang' value='{locale}'>
          <label for='access_code'>{html.escape(text['access'])}:</label>
          <input id='access_code' name='access_code' value='{html.escape(submitted_code, quote=True)}' autocomplete='off' spellcheck='false' required>
          <p class='hint'>{html.escape(text['access_hint'])}</p><button class='primary wide' type='submit'>{html.escape(text['activate'])}</button>
        </form>
      </section>{info}{instructions}
    </main>"""
    return HTMLResponse(render_layout(text['title'], content, locale))


def render_key_information(key: TokenKey, locale: str = "ru") -> str:
    text = TOKEN_TEXT[token_locale(locale)]
    status = text["exhausted_status"].format(date=format_datetime(key.exhausted_at or key.activated_at)) if key.is_exhausted else (text["activated_status"].format(date=format_datetime(key.activated_at)) if key.activated_at else text["not_activated"])
    return f"""
    <section class='card info-card'><h2>{html.escape(text['info'])}</h2><dl class='details'>
      <dt>{html.escape(text['service'])}</dt><dd>{html.escape(key.service.upper())}</dd>
      <dt>{html.escape(text['activated'])}</dt><dd>{format_datetime(key.activated_at)}</dd>
      <dt>{html.escape(text['limit'])}</dt><dd>{format_tokens(key.token_limit)}</dd>
      <dt>{html.escape(text['remaining'])}</dt><dd id='token-balance' data-separator='{html.escape(text['remaining_sep'], quote=True)}' data-fallback='{format_tokens(key.remaining_tokens)}'>{format_tokens(key.remaining_tokens)} {html.escape(text['remaining_sep'])} {format_tokens(key.token_limit)}</dd>
      <dt>{html.escape(text['status'])}</dt><dd>{html.escape(status)}</dd>
      <dt>{html.escape(text['api'])}</dt><dd><code class='api-key'>{html.escape(key.api_key)}</code></dd>
    </dl></section>"""


INSTRUCTION_GROUPS = (
    ("codex", "Codex", ("VS Code", "App", "CLI")),
    ("claude", "Claude", ("Claude Code CLI", "Claude App")),
    ("grok", "Grok", ("Grok Build",)),
    ("other", "Другие", ("Hermes Desktop", "Cheap Code", "Pi", "OpenCode", "Cursor")),
)
INSTRUCTION_SYSTEMS = ("Windows", "macOS", "Linux")
INSTRUCTION_SYSTEMS_BY_APP = {
    "VS Code": ("Windows", "macOS", "Linux"), "App": ("Windows", "macOS"), "CLI": ("Windows", "macOS", "Linux"),
    "Claude Code CLI": ("Windows", "macOS", "Linux"), "Claude App": ("Windows", "macOS"),
    "Hermes Desktop": ("Windows", "macOS", "Linux"), "Cheap Code": ("Windows", "macOS", "Linux"),
    "Grok Build": ("Windows", "macOS", "Linux"), "Pi": ("Windows", "macOS", "Linux"),
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
    "Pi": {"Windows": "ipw", "macOS": "ipm", "Linux": "ipl"},
    "OpenCode": {"Windows": "iow", "macOS": "iom", "Linux": "iol"},
    "Cursor": {"Windows": "icrw", "macOS": "icrm", "Linux": "icr"},
}
INSTRUCTION_REMOVE_ENDPOINTS = {
    "Grok Build": {"Windows": "rgw", "macOS": "rgm", "Linux": "rgl"},
}


def instruction_slug(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")


def instruction_command(application: str, system: str, api_key: str, locale: str = "ru") -> str:
    endpoint = INSTRUCTION_ENDPOINTS[application][system]
    url = f"https://cheapvibecode.ru/{endpoint}"
    if application == "Claude Code CLI":
        if system == "Windows":
            return f"$h=@{{Authorization='Bearer {api_key}'}}; iex(irm -Headers $h '{url}')"
        return f"curl -fsSL -H 'Authorization: Bearer {api_key}' '{url}' | bash"
    if application == "Cheap Code":
        message = "Set the API key in the application settings" if locale == "en" else "Установите API ключ в настройках приложения"
        return f"npm install -g @cheapcode/cli@latest && echo '{message}: {api_key}'"
    if application == "Grok Build":
        if system == "Windows":
            return f"$env:CVC_API_KEY='{api_key}'; iex(irm '{url}')"
        return f"bash <(curl -fsSL '{url}') '{api_key}'"
    if system == "Windows":
        return f"$env:CVC_API_KEY='{api_key}'; iex(irm '{url}')"
    return f"bash <(curl -fsSL '{url}') '{api_key}'"


def instruction_remove_command(application: str, system: str) -> str | None:
    endpoints = INSTRUCTION_REMOVE_ENDPOINTS.get(application)
    if not endpoints:
        return None
    url = f"https://cheapvibecode.ru/{endpoints[system]}"
    if system == "Windows":
        return f"iex(irm '{url}')"
    return f"bash <(curl -fsSL '{url}')"


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
    cards: list[str] = []
    for _, _, applications in INSTRUCTION_GROUPS:
        for application in applications:
            for system in INSTRUCTION_SYSTEMS_BY_APP[application]:
                slug = f"{instruction_slug(application)}-{instruction_slug(system)}"
                command = html.escape(instruction_command(application, system, key.api_key, locale))
                description = text["manual"]
                steps = "".join(f"<li>{html.escape(step)}</li>" for step in instruction_steps(application, system, locale))
                remove_command = instruction_remove_command(application, system)
                remove_block = ""
                if remove_command:
                    remove_slug = f"{slug}-remove"
                    remove_block = f"""
                  <p class='hint'>{html.escape(text['remove_script'])}</p>
                  <pre id='instruction-script-{remove_slug}'>{html.escape(remove_command)}</pre>
                  <button type='button' class='secondary copy-instruction' data-copy-target='instruction-script-{remove_slug}' data-copy-label='{html.escape(text['copy'])}' data-copied-label='{html.escape(text['copied'])}'>{html.escape(text['copy'])}</button>"""
                cards.append(f"""
                <article class='instruction-card' id='instruction-card-{slug}' data-provider-app='{html.escape(application)}' data-instruction-system='{system}' hidden>
                  <h3>{html.escape(application)} · {html.escape(system)}</h3>
                  <p><strong>• {html.escape(text['selected'])}</strong> {html.escape(application)}<br><strong>• {html.escape(text['description'])}</strong> {html.escape(description)}<br><strong>• {html.escape(text['os'])}</strong> {html.escape(system)}</p>
                  <ol>{steps}</ol>
                  <p class='warning'>{html.escape(text['warning'])}</p>
                  <pre id='instruction-script-{slug}'>{command}</pre>
                  <button type='button' class='primary copy-instruction' data-copy-target='instruction-script-{slug}' data-copy-label='{html.escape(text['copy'])}' data-copied-label='{html.escape(text['copied'])}'>{html.escape(text['copy'])}</button>
                  {remove_block}
                  <p class='hint'>{html.escape(text['remove_hint'])}</p>
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


def render_tokens_admin(
    *,
    csrf_token: str,
    authenticated: bool,
    password_configured: bool,
    keys: list[TokenKey],
    error: str = "",
    notice: str = "",
) -> HTMLResponse:
    flash = ""
    if error:
        flash = f"<div class='flash error'>{html.escape(error)}</div>"
    elif notice:
        flash = f"<div class='flash success'>{html.escape(notice)}</div>"
    if not authenticated:
        unavailable = "" if password_configured else "<p class='warning'>TOKENS_ADMIN_PASSWORD \u043d\u0435 \u0437\u0430\u0434\u0430\u043d \u0432 .env. \u0412\u0445\u043e\u0434 \u043e\u0442\u043a\u043b\u044e\u0447\u0435\u043d.</p>"
        content = f"""
        <main class='page narrow'><section class='card'>
          <h1>\u0421\u041e\u0417\u0414\u0410\u041d\u0418\u042f \u041a\u041b\u042e\u0427\u0415\u0419</h1>
          {flash}{unavailable}
          <form method='post' action='/ai/tokens/adm/login'>
            <input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'>
            <label for='password'>\u041f\u0430\u0440\u043e\u043b\u044c \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430</label>
            <input id='password' type='password' name='password' autocomplete='current-password' required>
            <button class='primary wide' type='submit' {'disabled' if not password_configured else ''}>\u0412\u043e\u0439\u0442\u0438</button>
          </form>
        </section></main>"""
        return HTMLResponse(render_layout("\u0410\u0434\u043c\u0438\u043d-\u043f\u0430\u043d\u0435\u043b\u044c \u043a\u043b\u044e\u0447\u0435\u0439", content))
    rows, forms = render_admin_rows(keys, csrf_token)
    service_options = "".join(f"<option value='{html.escape(service)}'>{html.escape(service)}</option>" for service in SERVICE_OPTIONS)
    content = f"""
    <main class='page admin-page'>
      <section class='card'>
        <div class='title-row'><h1>\u0421\u041e\u0417\u0414\u0410\u041d\u0418\u042f \u041a\u041b\u042e\u0427\u0415\u0419</h1>
        <form method='post' action='/ai/tokens/adm/logout'><input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'><button class='secondary' type='submit'>\u0412\u044b\u0439\u0442\u0438</button></form></div>
        {flash}
        <form method='post' action='/ai/tokens/adm/create' class='create-form'>
          <input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'>
          <label>\u0412\u042b\u0411\u0420\u0410\u0422\u042c \u0421\u0415\u0420\u0412\u0418\u0421:<select name='service' required>{service_options}</select></label>
          <label>\u0412\u042b\u0411\u0420\u0410\u0422\u042c \u041d\u0410\u0417\u0412\u0410\u041d\u0418\u0415:<input name='name' required maxlength='200' placeholder='\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435'></label>
          <label>\u0412\u042b\u0411\u0420\u0410\u0422\u042c \u041a\u041e\u041b\u0418\u0427\u0415\u0421\u0422\u0412\u041e:<input type='number' name='token_limit' min='1' step='1' required placeholder='\u041a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e \u0442\u043e\u043a\u0435\u043d\u043e\u0432'></label>
          <label>\u0412\u042b\u0411\u0420\u0410\u0422\u042c \u041a\u041e\u041b\u0418\u0427\u0415\u0421\u0422\u0412\u041e \u041a\u041b\u042e\u0427\u0415\u0419:<input type='number' name='quantity' min='1' max='100' step='1' required value='1'></label>
          <button class='primary wide' type='submit'>\u0421\u041e\u0417\u0414\u0410\u0422\u042c</button>
        </form>
      </section>
      <section class='card'>
        <h2>\u0423\u041f\u0420\u0410\u0412\u041b\u0415\u041d\u0418\u0415 \u041a\u041b\u042e\u0427\u0410\u041c\u0418</h2>
        <p class='hint'>\u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u00ab\u0423\u043f\u0440\u0430\u0432\u043b\u044f\u0442\u044c\u00bb, \u0447\u0442\u043e\u0431\u044b \u0438\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u0432\u0441\u0435 \u0434\u0430\u043d\u043d\u044b\u0435 \u043a\u043b\u044e\u0447\u0430.</p>
        <div class='table-wrap'><table><thead><tr><th>ID</th><th>\u0414\u0430\u0442\u0430 \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u044f</th><th>\u0421\u0415\u0420\u0412\u0418\u0421</th><th>\u041a\u041b\u042e\u0427</th><th>API KEY</th><th>\u0422\u041e\u041a\u0415\u041d\u041e\u0412</th><th>\u0418\u0421\u041f\u041e\u041b\u042c\u0417\u041e\u0412\u0410\u041d\u041e</th><th>\u0421\u0422\u0410\u0422\u0423\u0421</th><th>\u0423\u041f\u0420\u0410\u0412\u041b\u0415\u041d\u0418\u0415</th></tr></thead>
        <tbody>{rows or "<tr><td colspan='9' class='empty'>\u041a\u043b\u044e\u0447\u0435\u0439 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442.</td></tr>"}</tbody></table></div>
        {forms}
      </section>
    </main>"""
    return HTMLResponse(render_layout("\u0410\u0434\u043c\u0438\u043d-\u043f\u0430\u043d\u0435\u043b\u044c \u043a\u043b\u044e\u0447\u0435\u0439", content))


def render_admin_rows(keys: list[TokenKey], csrf_token: str) -> tuple[str, str]:
    rows: list[str] = []
    forms: list[str] = []
    for key in keys:
        status = (
            f"\u0418\u0441\u0442\u0440\u0430\u0447\u0435\u043d ({format_datetime(key.exhausted_at or key.activated_at)})"
            if key.is_exhausted else
            (f"\u0410\u043a\u0442\u0438\u0432\u0438\u0440\u043e\u0432\u0430\u043d ({format_datetime(key.activated_at)})" if key.activated_at else "\u041d\u0435 \u0430\u043a\u0442\u0438\u0432\u0438\u0440\u043e\u0432\u0430\u043d")
        )
        edit_id = f"edit-{key.id}"
        rows.append(f"""
        <tr><td>{key.id}</td><td>{format_datetime(key.created_at)}</td><td>{html.escape(key.service)}</td><td><code>{html.escape(key.access_code)}</code></td>
        <td class='copyable-api-key' data-copy-api-key='{html.escape(key.api_key, quote=True)}' role='button' tabindex='0' title='Нажмите, чтобы скопировать API key' aria-label='Скопировать API key'><code class='api-preview'>{html.escape(key.api_key)}</code></td><td>{format_tokens(key.token_limit)}</td>
        <td>{format_tokens(key.used_tokens)}<br><span class='hint'>ост. {format_tokens(key.remaining_tokens)}</span></td><td>{status}</td>
        <td><div class='management-actions'><button type='button' class='secondary' data-edit='{edit_id}'>\u0423\u043f\u0440\u0430\u0432\u043b\u044f\u0442\u044c</button>
        <form method='post' action='/ai/tokens/adm/{key.id}/delete' class='inline-delete-form'>
          <input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'>
          <button class='danger' type='submit' onclick="return confirm('\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u043a\u043b\u044e\u0447 #{key.id}?')">\u0423\u0434\u0430\u043b\u0438\u0442\u044c</button>
        </form></div></td></tr>""")
        options = "".join(
            f"<option value='{html.escape(service, quote=True)}' {'selected' if service == key.service else ''}>{html.escape(service)}</option>"
            for service in SERVICE_OPTIONS
        )
        forms.append(f"""
        <form id='{edit_id}' class='edit-form' method='post' action='/ai/tokens/adm/{key.id}/update' hidden>
          <input type='hidden' name='csrf_token' value='{html.escape(csrf_token, quote=True)}'>
          <h3>\u0423\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u043a\u043b\u044e\u0447\u043e\u043c #{key.id}</h3>
          <div class='edit-grid'>
            <label>\u0421\u0435\u0440\u0432\u0438\u0441<select name='service'>{options}</select></label>
            <label>\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435<input name='name' value='{html.escape(key.name, quote=True)}' required></label>
            <label>\u0414\u0430\u0442\u0430 \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u044f<input type='datetime-local' name='created_at' value='{input_datetime(key.created_at)}' required></label>
            <label>\u041a\u043b\u044e\u0447 \u0434\u043e\u0441\u0442\u0443\u043f\u0430<input name='access_code' minlength='20' maxlength='20' pattern='[A-Z0-9]{{20}}' value='{html.escape(key.access_code, quote=True)}' required></label>
            <label>API key<input name='api_key' value='{html.escape(key.api_key, quote=True)}' required></label>
            <label>\u041b\u0438\u043c\u0438\u0442 \u0442\u043e\u043a\u0435\u043d\u043e\u0432<input type='number' name='token_limit' min='1' step='1' value='{key.token_limit}' required></label>
            <label>\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u043e<input type='number' name='used_tokens' min='0' step='1' value='{key.used_tokens}' required></label>
            <label>\u0414\u0430\u0442\u0430 \u0430\u043a\u0442\u0438\u0432\u0430\u0446\u0438\u0438<input type='datetime-local' name='activated_at' value='{input_datetime(key.activated_at)}'></label>
            <label>\u0414\u0430\u0442\u0430 \u0438\u0441\u0442\u0440\u0430\u0447\u0435\u043d\u0438\u044f<input type='datetime-local' name='exhausted_at' value='{input_datetime(key.exhausted_at)}'></label>
          </div>
          <div class='edit-actions'><button class='primary' type='submit'>\u0421\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c</button>
          <button class='secondary cancel-edit' type='button' data-edit='{edit_id}'>\u041e\u0442\u043c\u0435\u043d\u0430</button></div>
        </form>
""")
    return "".join(rows), "".join(forms)


def render_layout(title: str, content: str, locale: str = "ru") -> str:
    return f"""<!doctype html>
<html lang='{token_locale(locale)}'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
<title>{html.escape(title)}</title><style>
:root {{ color-scheme: dark; --bg:#080d16; --card:#111a2a; --line:#27364f; --text:#eaf0ff; --muted:#a9b7ce; --accent:#6d9cff; --danger:#ff7885; --success:#5fd5a0; --warn:#ffd46b; }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:radial-gradient(circle at top,#182949 0,#080d16 44rem); color:var(--text); font:16px/1.55 Arial,sans-serif; }}
.page {{ width:min(960px,calc(100% - 32px)); margin:32px auto 64px; }} .page.narrow {{ max-width:520px; }} .admin-page {{ width:min(1320px,calc(100% - 32px)); }}
.card {{ background:rgba(17,26,42,.96); border:1px solid var(--line); border-radius:16px; padding:24px; margin:18px 0; box-shadow:0 12px 36px rgba(0,0,0,.22); }}
h1,h2,h3 {{ margin:0 0 14px; line-height:1.24; }} h1 {{ font-size:28px; }} h2 {{ font-size:20px; letter-spacing:.02em; }} p {{ margin:9px 0; }}
label {{ display:block; font-weight:700; margin:14px 0 6px; }} input,select {{ display:block; width:100%; margin-top:6px; padding:12px 13px; border:1px solid var(--line); border-radius:9px; background:#0b1321; color:var(--text); font:inherit; }}
button {{ border:0; border-radius:9px; padding:11px 15px; font:700 14px Arial,sans-serif; cursor:pointer; }} button:disabled {{ cursor:not-allowed; opacity:.5; }} .primary {{ color:#071120; background:var(--accent); }} .wide {{ display:block; width:100%; margin-top:18px; }} .secondary {{ color:var(--text); background:#263652; }} .danger {{ color:#26080c; background:var(--danger); margin-top:12px; }}
.hint {{ color:var(--muted); font-size:14px; }} .warning {{ color:var(--warn); font-weight:700; }} .flash {{ border-radius:9px; padding:11px 13px; margin:12px 0; }} .error {{ color:#ffdce0; background:rgba(255,120,133,.18); border:1px solid rgba(255,120,133,.45); }} .success {{ color:#d8ffec; background:rgba(95,213,160,.15); border:1px solid rgba(95,213,160,.45); }}
.language-switch {{ display:flex; justify-content:flex-end; margin:0 0 -4px; }} .language-switch a {{ color:var(--text); font-weight:700; text-decoration:none; padding:7px 10px; border:1px solid var(--line); border-radius:7px; }} .language-switch a:hover {{ border-color:var(--accent); color:var(--accent); }}
.details {{ display:grid; grid-template-columns:minmax(210px,auto) 1fr; gap:8px 18px; margin:0; }} .details dt {{ color:var(--muted); }} .details dd {{ margin:0; min-width:0; overflow-wrap:anywhere; }} code,pre {{ font-family:Consolas,'Courier New',monospace; }} .api-key {{ color:#b9d4ff; }}
.choice-row {{ display:flex; flex-wrap:wrap; gap:8px; margin:14px 0; }} .instruction-apps .choice[hidden] {{ display:none; }} .instruction-card {{ margin-top:18px; }} .choice {{ color:var(--text); background:#1b2940; border:1px solid var(--line); }} .choice.active {{ color:#08101e; background:var(--accent); border-color:var(--accent); }} .selected-line {{ padding:12px; margin:16px 0; border-left:3px solid var(--accent); background:#0b1321; }} ol {{ padding-left:24px; }} pre {{ max-width:100%; overflow:auto; padding:14px; white-space:pre-wrap; overflow-wrap:anywhere; background:#080e18; border:1px solid var(--line); border-radius:9px; color:#d9e7ff; }}
.title-row {{ display:flex; justify-content:space-between; gap:16px; align-items:start; }} .title-row form {{ margin:0; }} .create-form,.edit-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); column-gap:18px; }} .create-form .wide {{ grid-column:1 / -1; }}
.table-wrap {{ overflow-x:auto; }} .management-actions {{ display:flex; gap:6px; align-items:center; }} .inline-delete-form {{ margin:0; }} .inline-delete-form .danger {{ margin:0; padding:9px 11px; }} .copyable-api-key {{ cursor:pointer; }} .copyable-api-key:hover,.copyable-api-key:focus {{ background:rgba(109,156,255,.13); outline:none; }} table {{ width:100%; border-collapse:collapse; min-width:990px; }} th,td {{ padding:10px 8px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }} th {{ color:var(--muted); font-size:12px; }} .api-preview {{ display:block; max-width:180px; overflow:hidden; text-overflow:ellipsis; }} .empty {{ text-align:center; color:var(--muted); }} .edit-form {{ margin-top:16px; padding:18px; border:1px solid var(--line); border-radius:12px; background:#0b1321; }} .edit-actions {{ display:flex; gap:8px; margin-top:16px; }} .delete-form {{ margin-top:4px; }}
@media(max-width:650px) {{ .page,.admin-page {{ width:min(100% - 20px,960px); margin-top:12px; }} .card {{ padding:18px; border-radius:12px; }} h1 {{ font-size:24px; }} .details,.create-form,.edit-grid {{ grid-template-columns:1fr; }} .title-row {{ display:block; }} .title-row form {{ margin-top:12px; }} }}
</style></head><body>{content}<script>
(function() {{
  var root=document.querySelector('.instructions');
  var provider=(root && root.dataset.defaultProvider) || 'claude';
  var app=(root && root.dataset.defaultApp) || 'Claude Code CLI';
  var os='Windows';
  var descriptions={{'VS Code':'Настройка API в VS Code','App':'Настройка приложения','CLI':'Ручная CLI-настройка','Claude Code CLI':'Ручная CLI-настройка','Claude App':'Настройка Claude App','Hermes Desktop':'Настройка Hermes Desktop','Cheap Code':'Настройка Cheap Code','Grok Build':'Настройка Grok Build','Pi':'Настройка Pi','OpenCode':'Настройка OpenCode','Cursor':'Настройка Cursor'}};
  if(document.documentElement.lang==='en') descriptions={{'VS Code':'API setup in VS Code','App':'Application setup','CLI':'Manual CLI setup','Claude Code CLI':'Manual CLI setup','Claude App':'Claude App setup','Hermes Desktop':'Hermes Desktop setup','Cheap Code':'Cheap Code setup','Grok Build':'Grok Build setup','Pi':'Pi setup','OpenCode':'OpenCode setup','Cursor':'Cursor setup'}};
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
  document.querySelectorAll('.copy-instruction').forEach(function(button) {{ button.addEventListener('click',function() {{ var target=document.getElementById(button.dataset.copyTarget); if(!target) return; navigator.clipboard.writeText(target.textContent).then(function() {{ button.textContent=button.dataset.copiedLabel; setTimeout(function() {{ button.textContent=button.dataset.copyLabel; }},1500); }}); }}); }});
  var balance=document.getElementById('token-balance');
  if(balance) fetch('/ai/tokens/balance',{{cache:'no-store'}}).then(function(response) {{ return response.ok ? response.json() : Promise.reject(); }}).then(function(data) {{ if(data.ok) balance.textContent=data.formatted+' '+(balance.dataset.separator||'/')+' '+data.token_limit_formatted; }}).catch(function() {{ /* Keep the locally saved balance visible. */ }});
  function copyApiKey(cell) {{ var value=cell.dataset.copyApiKey; if(!value) return; navigator.clipboard.writeText(value).then(function() {{ var old=cell.title; cell.title='API key скопирован'; setTimeout(function() {{ cell.title=old; }},1500); }}); }}
  document.querySelectorAll('.copyable-api-key').forEach(function(cell) {{ cell.addEventListener('click',function() {{ copyApiKey(cell); }}); cell.addEventListener('keydown',function(event) {{ if(event.key==='Enter'||event.key===' ') {{ event.preventDefault(); copyApiKey(cell); }} }}); }});
  document.querySelectorAll('[data-edit]').forEach(function(button) {{ button.addEventListener('click',function() {{ var form=document.getElementById(button.dataset.edit); if(form) form.hidden=!form.hidden; }}); }});
  update();
}})();
</script></body></html>"""


__all__ = [
    "CheapVibeCodeClient", "SecondaryKeyClient", "SERVICE_OPTIONS", "TokenKey", "TokenKeyStore",
    "create_tokens_routes", "default_instruction_choice", "generate_access_code",
    "instruction_command", "instruction_remove_command", "instruction_steps",
    "normalize_access_code", "trusted_secondary_remaining", "used_tokens_from_remaining",
]



