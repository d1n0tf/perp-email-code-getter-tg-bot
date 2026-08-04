"""Grok activation page backed by bypriceactivate.pro."""

import html
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs
from uuid import UUID

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse


UUID_RE = re.compile(
    r"(?<![0-9a-fA-F])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![0-9a-fA-F])"
)
GROK_CODE_RE = re.compile(r"^GRK-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")
CSRF_COOKIE_NAME = "grok_activation_csrf"


class GrokApiError(Exception):
    def __init__(self, message: str, status_code: int = 502) -> None:
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class GrokOrder:
    order_id: int
    status: str
    product: str | None = None
    error: str | None = None


class GrokActivationClient:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def activate(self, *, code: str, user_id: str) -> GrokOrder:
        payload = await self._request("POST", "/api/activate", json={"code": code, "org_id": user_id})
        try:
            return GrokOrder(
                order_id=int(payload["order_id"]),
                status=str(payload.get("status", "queued")),
                product=string_or_none(payload.get("product")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GrokApiError("Сервис активации вернул некорректный ответ.") from exc

    async def get_status(self, order_id: int) -> GrokOrder:
        payload = await self._request("GET", f"/api/activate/{order_id}")
        try:
            return GrokOrder(
                order_id=int(payload["order_id"]),
                status=str(payload["status"]),
                product=string_or_none(payload.get("product")),
                error=string_or_none(payload.get("error")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GrokApiError("Сервис активации вернул некорректный ответ.") from exc

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.request(method, f"{self.base_url}{path}", **kwargs) as response:
                    raw = await response.text()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise GrokApiError("Не удалось подключиться к сервису активации. Повторите попытку.") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GrokApiError("Сервис активации вернул некорректный ответ.") from exc
        if not isinstance(payload, dict):
            raise GrokApiError("Сервис активации вернул некорректный ответ.")
        if response.status >= 400:
            detail = string_or_none(payload.get("detail")) or "Сервис активации отклонил запрос."
            raise GrokApiError(detail, response.status)
        return payload


def string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def normalize_grok_code(raw_code: str) -> str:
    return raw_code.strip().upper().replace(" ", "")


def is_valid_grok_code(code: str) -> bool:
    return bool(GROK_CODE_RE.fullmatch(code))


def extract_grok_user_id(raw_value: str) -> str | None:
    """Accept a UUID, session JSON, or copied session text containing userId."""
    value = raw_value.strip()
    if not value:
        return None
    candidates = [value]
    try:
        document = json.loads(value)
    except json.JSONDecodeError:
        document = None
    if isinstance(document, dict):
        user_id = document.get("userId")
        if isinstance(user_id, str):
            candidates.insert(0, user_id)
        user = document.get("user")
        if isinstance(user, dict) and isinstance(user.get("id"), str):
            candidates.append(user["id"])
    for candidate in candidates:
        match = UUID_RE.search(candidate)
        if match is not None:
            try:
                return str(UUID(match.group(0))).lower()
            except ValueError:
                continue
    return None


async def read_urlencoded_form(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode("utf-8", errors="replace")
    values = parse_qs(raw, keep_blank_values=True)
    return {key: items[0] if items else "" for key, items in values.items()}


def create_grok_routes(app: FastAPI, *, client: GrokActivationClient) -> None:
    app.state.grok_client = client

    def render_with_csrf(**kwargs: object) -> HTMLResponse:
        csrf_token = secrets.token_urlsafe(32)
        response = render_grok_page(csrf_token=csrf_token, **kwargs)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.set_cookie(
            CSRF_COOKIE_NAME,
            csrf_token,
            httponly=True,
            samesite="lax",
            secure=True,
            path="/ai/grok",
        )
        return response

    async def page() -> HTMLResponse:
        return render_with_csrf()

    async def activate(request: Request) -> HTMLResponse:
        form = await read_urlencoded_form(request)
        csrf_token = form.get("csrf_token", "")
        csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME, "")
        if not csrf_token or not csrf_cookie or not secrets.compare_digest(csrf_token, csrf_cookie):
            return render_with_csrf(error="Страница устарела. Обновите её и повторите попытку.", status_code=403)
        code = normalize_grok_code(form.get("code", ""))
        supplied_user_id = form.get("user_id", "").strip()
        user_id = extract_grok_user_id(supplied_user_id)
        if not is_valid_grok_code(code):
            return render_with_csrf(code=code, error="Введите ключ в формате GRK-XXXX-XXXX-XXXX.", status_code=400)
        if user_id is None:
            return render_with_csrf(code=code, error="Не удалось найти корректный Grok User ID (UUID).", status_code=400)
        try:
            order = await request.app.state.grok_client.activate(code=code, user_id=user_id)
        except GrokApiError as exc:
            return render_with_csrf(code=code, error=str(exc), status_code=map_upstream_status(exc.status_code))
        return render_with_csrf(order_id=order.order_id)

    async def status(request: Request) -> JSONResponse:
        def status_response(payload: dict[str, object], status_code: int = 200) -> JSONResponse:
            return JSONResponse(payload, status_code=status_code, headers={"Cache-Control": "no-store"})

        raw_order_id = request.query_params.get("order_id", "")
        if not raw_order_id.isdecimal() or int(raw_order_id) < 1:
            return JSONResponse({"status": "failed", "message": "Некорректный номер заявки."}, status_code=400)
        try:
            order = await request.app.state.grok_client.get_status(int(raw_order_id))
        except GrokApiError as exc:
            terminal = exc.status_code in {400, 404, 409}
            return status_response(
                {"status": "failed" if terminal else "running", "message": str(exc)},
                status_code=map_upstream_status(exc.status_code),
            )
        messages = {
            "queued": "Заявка в очереди на активацию…",
            "running": "Подписка активируется. Обычно это занимает 1–2 минуты…",
            "done": "Подписка SuperGrok Pro успешно активирована.",
            "failed": order.error or "Активация не удалась. Попробуйте позднее.",
        }
        return JSONResponse({"order_id": order.order_id, "status": order.status, "message": messages.get(order.status, "Проверяем статус заявки…")})

    app.add_api_route("/ai/grok", page, methods=["GET"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/grok", activate, methods=["POST"], response_class=HTMLResponse, response_model=None)
    app.add_api_route("/ai/grok/status", status, methods=["GET"], response_class=JSONResponse, response_model=None)


def map_upstream_status(status_code: int) -> int:
    return status_code if status_code in {400, 404, 409} else 502


def render_grok_page(*, csrf_token: str, code: str = "", error: str = "", order_id: int | None = None, status_code: int = 200) -> HTMLResponse:
    message = f'<div class="notice error">{html.escape(error)}</div>' if error else ""
    polling = ""
    if order_id is not None:
        message = f'<div id="activation-status" class="notice">Заявка №{order_id} принята. Проверяем статус…</div>'
        polling = f'''<script>
const target = document.getElementById("activation-status");
async function poll() {{
  try {{
    const response = await fetch("/ai/grok/status?order_id={order_id}", {{cache: "no-store"}});
    const data = await response.json();
    target.textContent = data.message || "Проверяем статус…";
    if (data.status === "done") {{ target.className = "notice success"; return; }}
    if (data.status === "failed") {{ target.className = "notice error"; return; }}
  }} catch (_) {{ target.textContent = "Нет связи с сервером. Повторяем проверку…"; }}
  window.setTimeout(poll, 4000);
}}
poll();
</script>'''
    return HTMLResponse(f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>
body{{font-family:Arial,sans-serif;background:#f3f4f6;color:#172033;margin:0}} main{{max-width:680px;margin:40px auto;padding:0 18px 30px}} .card{{background:#fff;border-radius:14px;padding:26px;box-shadow:0 2px 14px #17203316;margin-bottom:18px}} h1{{margin-top:0}} label{{display:block;font-weight:700;margin:16px 0 7px}} .field-hint{{margin:-3px 0 8px;color:#526176;font-size:.94rem}} input{{box-sizing:border-box;width:100%;padding:12px;border:1px solid #cbd5e1;border-radius:8px;font:inherit}} button{{box-sizing:border-box;width:100%;margin-top:18px;padding:12px 18px;border:0;border-radius:8px;background:#111827;color:#fff;font:inherit;font-weight:700;cursor:pointer}} .notice{{padding:13px;border-radius:8px;background:#e0e7ff;margin:0 0 16px}} .success{{background:#dcfce7}} .error{{background:#fee2e2}} ol{{padding-left:21px;line-height:1.6}} code{{word-break:break-all}} .faq-title{{margin:0 0 14px;font-size:1.15rem}} .faq-item{{border:1px solid #e2e8f0;border-radius:10px;margin:10px 0;overflow:hidden}} .faq-item summary{{padding:14px 16px;font-weight:700;cursor:pointer;list-style:none;display:flex;align-items:center;gap:9px}} .faq-item summary::-webkit-details-marker{{display:none}} .faq-item summary::after{{content:"+";margin-left:auto;font-size:1.3rem;color:#64748b}} .faq-item[open] summary{{border-bottom:1px solid #e2e8f0;background:#f8fafc}} .faq-item[open] summary::after{{content:"?"}} .faq-answer{{padding:14px 16px;line-height:1.55;color:#475569}} .faq-answer p{{margin:0}}
</style></head><body><main><section class="card"><h1>Сервис активации SuperGrok</h1><p>Здесь вы сможете активировать подписку SuperGrok на своем аккаунте, для этого следуйте инструкциям ниже и читайте внимательно весь текст.</p></section><section class="card">{message}<form method="post" action="/ai/grok"><input type="hidden" name="csrf_token" value="{html.escape(csrf_token, quote=True)}"><label for="code">Одноразовый ключ</label><input id="code" name="code" value="{html.escape(code, quote=True)}" placeholder="GRK-XXXX-XXXX-XXXX" autocomplete="off" autocapitalize="characters" required><p class="field-hint">➥ Здесь вводите полученный ключ от продавца</p><label for="user_id">Ваш UserID</label><input id="user_id" name="user_id" autocomplete="off" required><p class="field-hint">➥ Введите userid вашей учетной записи или полный json ответ, инструкция ниже.</p><button type="submit">Активировать подписку</button></form></section><section class="card"><h2 class="faq-title">❗️ ВОЗМОЖНЫЕ ОШИБКИ</h2><details class="faq-item"><summary>❓ Что такое UserID и зачем он нужен?</summary><div class="faq-answer"><p>↪️ UserID - это ваш уникальный номер аккаунта Grok, он нужен для того чтобы сервис понимал кому именно отправлять подписку. С помощью userId нельзя войти в аккаунт или украсть какие-либо данные</p></div></details><details class="faq-item"><summary>❓ Как получить UserID?</summary><div class="faq-answer"><ol><li>1️⃣ Откройте сайт <a href="https://grok.com" target="_blank" rel="noreferrer">grok.com</a> и авторизуйтесь в аккаунт.</li><li>2️⃣ Откройте ссылку — <a href="https://grok.com/api/auth/session" target="_blank" rel="noreferrer">grok.com/api/auth/session</a>.</li><li>3️⃣ Скопируйте <code>userId</code>, либо всю информацию и вставьте в поле ввода UserID.</li></ol></div></details><details class="faq-item"><summary>❓ Выдает ошибку &quot;User: unauthenticated&quot;</summary><div class="faq-answer"><p>↪️ Вы не авторизовались в браузере где перешли по ссылки, авторизуйтесь в свой аккаунт Grok и попробуйте снова.</p></div></details></section></main>{polling}</body></html>''', status_code=status_code)
