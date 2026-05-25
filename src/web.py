import html
from urllib.parse import parse_qs, urlencode
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from src.config import settings
from src.messages import DEFAULT_LOCALE, SUPPORTED_LOCALES, translate
from src.service import ActivatedSubscription, BotService
from src.storage import SubscriptionKey, normalize_key_code


WEB_USER_COOKIE_NAME = "perp_web_user_id"

WEB_TEXTS = {
    "ru": {
        "title": "Perplexity Access",
        "subtitle": "Активируйте код от продавца и запрашивайте коды для входа.",
        "activation_heading": "Активировать код",
        "activation_label": "Код от продавца",
        "activation_placeholder": "XHASHDAUSHFAFS",
        "activation_button": "Активировать код",
        "activation_success": "Код успешно активирован.",
        "code_required": "Введите код от продавца.",
        "subscription_heading": "Подписка активна",
        "subscription_details_web": (
            "1️⃣ Почта для входа: {email}\n"
            "2️⃣ Срок подписки: {duration_days} дней\n"
            "3️⃣ Конец подписки: {end_date}\n"
            "4️⃣ Активированный код: {code}"
        ),
        "subscription_hint": (
            "Нажмите кнопку «Запросить код», чтобы получить код для входа в Perplexity."
        ),
        "change_success": "Текущий аккаунт отвязан. Введите новый код от продавца.",
        "waiting_title": "Ожидание кода",
        "waiting_text": "Жду код для входа для {email}. Страница будет ждать сколько угодно.",
        "polling_error": "Нет связи с сервером. Продолжаю пытаться...",
        "code_found_web": "Код для `{email}`: `{code}`",
        "request_missing": "Запрос кода не найден или уже недоступен.",
        "lang_ru": "Русский",
        "lang_en": "English",
    },
    "en": {
        "title": "Perplexity Access",
        "subtitle": "Activate the seller code and request login codes when you need them.",
        "activation_heading": "Activate code",
        "activation_label": "Seller code",
        "activation_placeholder": "XHASHDAUSHFAFS",
        "activation_button": "Activate code",
        "activation_success": "The code has been activated successfully.",
        "code_required": "Enter the seller code.",
        "subscription_heading": "Subscription active",
        "subscription_details_web": (
            "1️⃣ Login email: {email}\n"
            "2️⃣ Subscription term: {duration_days} days\n"
            "3️⃣ Subscription ends: {end_date}\n"
            "4️⃣ Activated code: {code}"
        ),
        "subscription_hint": "Tap “Request code” to get a Perplexity login code.",
        "change_success": "The current account has been unlinked. Enter a new seller code.",
        "waiting_title": "Waiting for code",
        "waiting_text": "Waiting for a login code for {email}. This page will keep waiting as long as needed.",
        "polling_error": "Connection issue. Retrying...",
        "code_found_web": "Code for `{email}`: `{code}`",
        "request_missing": "The code request was not found or is no longer available.",
        "lang_ru": "Русский",
        "lang_en": "English",
    },
}


def create_web_app(service: BotService) -> FastAPI:
    app = FastAPI(title="Perp Mail Bot")
    app.state.service = service
    base_path = normalize_base_path(settings.web_base_path)

    async def index(request: Request) -> HTMLResponse:
        locale = resolve_locale(request.query_params.get("lang"))
        web_user_id = get_or_create_web_user_id(request)
        requester_id = build_web_requester_id(web_user_id)

        subscription = await service.get_requester_activated_subscription(requester_id)
        status_message = ""
        status_kind = "info"
        if subscription is not None:
            if subscription.key.is_expired():
                await service.clear_requester_subscription_activation(requester_id)
                status_message = translate(
                    locale,
                    "key_expired",
                    code=subscription.key.code,
                    end_date=service.format_date(subscription.key.expires_at),
                )
                status_kind = "error"
                subscription = None
            elif subscription.account is None:
                status_message = translate(
                    locale,
                    "key_email_missing",
                    code=subscription.key.code,
                )
                status_kind = "error"
                subscription = None

        return build_page_response(
            locale=locale,
            web_user_id=web_user_id,
            base_path=base_path,
            service=service,
            subscription=subscription,
            status_message=status_message,
            status_kind=status_kind,
        )

    async def activate_code(request: Request) -> HTMLResponse:
        payload = await read_form_body(request)
        locale = resolve_locale(payload.get("lang"))
        code = payload.get("code", "").strip()
        web_user_id = get_or_create_web_user_id(request)
        requester_id = build_web_requester_id(web_user_id)

        if not code:
            return build_page_response(
                locale=locale,
                web_user_id=web_user_id,
                base_path=base_path,
                service=service,
                code_value=code,
                status_message=web_text(locale, "code_required"),
                status_kind="error",
                status_code=400,
            )

        client_host = request.client.host if request.client is not None else "web"
        status, result = await service.activate_requester_subscription_code(
            requester_id=requester_id,
            user_id=0,
            chat_id=0,
            username="web",
            full_name=f"web:{client_host}",
            code=code,
        )

        if status == "missing":
            return build_page_response(
                locale=locale,
                web_user_id=web_user_id,
                base_path=base_path,
                service=service,
                code_value=code,
                status_message=translate(
                    locale,
                    "key_invalid",
                    code=normalize_key_code(code),
                ),
                status_kind="error",
                status_code=404,
            )

        if status == "expired" and isinstance(result, SubscriptionKey):
            return build_page_response(
                locale=locale,
                web_user_id=web_user_id,
                base_path=base_path,
                service=service,
                code_value=code,
                status_message=translate(
                    locale,
                    "key_expired",
                    code=result.code,
                    end_date=service.format_date(result.expires_at),
                ),
                status_kind="error",
                status_code=410,
            )

        if status == "email_missing" and isinstance(result, SubscriptionKey):
            return build_page_response(
                locale=locale,
                web_user_id=web_user_id,
                base_path=base_path,
                service=service,
                code_value=code,
                status_message=translate(
                    locale,
                    "key_email_missing",
                    code=result.code,
                ),
                status_kind="error",
                status_code=404,
            )

        subscription = result if isinstance(result, ActivatedSubscription) else None
        return build_page_response(
            locale=locale,
            web_user_id=web_user_id,
            base_path=base_path,
            service=service,
            subscription=subscription,
            status_message=web_text(locale, "activation_success"),
            status_kind="success",
        )

    async def request_code(request: Request):
        payload = await read_form_body(request)
        locale = resolve_locale(payload.get("lang"))
        web_user_id = get_or_create_web_user_id(request)
        requester_id = build_web_requester_id(web_user_id)

        status, result = await service.start_web_subscription_code_request(
            requester_id=requester_id,
        )
        if status == "inactive":
            return build_page_response(
                locale=locale,
                web_user_id=web_user_id,
                base_path=base_path,
                service=service,
                status_message=translate(locale, "subscription_inactive"),
                status_kind="error",
                status_code=400,
            )
        if status == "expired" and isinstance(result, SubscriptionKey):
            return build_page_response(
                locale=locale,
                web_user_id=web_user_id,
                base_path=base_path,
                service=service,
                status_message=translate(
                    locale,
                    "key_expired",
                    code=result.code,
                    end_date=service.format_date(result.expires_at),
                ),
                status_kind="error",
                status_code=410,
            )
        if status == "email_missing" and isinstance(result, SubscriptionKey):
            return build_page_response(
                locale=locale,
                web_user_id=web_user_id,
                base_path=base_path,
                service=service,
                status_message=translate(
                    locale,
                    "key_email_missing",
                    code=result.code,
                ),
                status_kind="error",
                status_code=404,
            )

        request_id = result if isinstance(result, str) else None
        if request_id is None:
            return build_page_response(
                locale=locale,
                web_user_id=web_user_id,
                base_path=base_path,
                service=service,
                status_message=translate(locale, "code_failed", email=""),
                status_kind="error",
                status_code=500,
            )

        response = RedirectResponse(
            url=build_wait_url(
                base_path=base_path,
                request_id=request_id,
                locale=locale,
            ),
            status_code=303,
        )
        response.set_cookie(
            key=WEB_USER_COOKIE_NAME,
            value=web_user_id,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 365,
        )
        return response

    async def change_account(request: Request) -> HTMLResponse:
        payload = await read_form_body(request)
        locale = resolve_locale(payload.get("lang"))
        web_user_id = get_or_create_web_user_id(request)
        requester_id = build_web_requester_id(web_user_id)
        await service.clear_requester_subscription_activation(requester_id)

        return build_page_response(
            locale=locale,
            web_user_id=web_user_id,
            base_path=base_path,
            service=service,
            status_message=web_text(locale, "change_success"),
            status_kind="success",
        )

    async def wait_page(request: Request) -> HTMLResponse:
        locale = resolve_locale(request.query_params.get("lang"))
        request_id = request.query_params.get("request_id", "").strip()
        web_user_id = get_or_create_web_user_id(request)
        requester_id = build_web_requester_id(web_user_id)
        request_state = await service.get_web_code_request(
            request_id=request_id,
            requester_id=requester_id,
        )
        if request_state is None:
            return build_page_response(
                locale=locale,
                web_user_id=web_user_id,
                base_path=base_path,
                service=service,
                status_message=web_text(locale, "request_missing"),
                status_kind="error",
                status_code=404,
            )

        return build_wait_page_response(
            locale=locale,
            web_user_id=web_user_id,
            base_path=base_path,
            request_id=request_id,
            email_address=request_state.email_address,
        )

    async def request_status(request: Request) -> JSONResponse:
        locale = resolve_locale(request.query_params.get("lang"))
        request_id = request.query_params.get("request_id", "").strip()
        web_user_id = get_or_create_web_user_id(request)
        requester_id = build_web_requester_id(web_user_id)
        request_state = await service.get_web_code_request(
            request_id=request_id,
            requester_id=requester_id,
        )
        if request_state is None:
            response = JSONResponse(
                {
                    "status": "missing",
                    "message": web_text(locale, "request_missing"),
                },
                status_code=404,
            )
            response.set_cookie(
                key=WEB_USER_COOKIE_NAME,
                value=web_user_id,
                httponly=True,
                samesite="lax",
                max_age=60 * 60 * 24 * 365,
            )
            return response

        if request_state.status == "pending":
            message = web_text(
                locale,
                "waiting_text",
                email=request_state.email_address,
            )
        elif request_state.status == "success":
            message = web_text(
                locale,
                "code_found_web",
                email=request_state.email_address,
                code=request_state.code or "",
            )
        elif request_state.status == "timeout":
            message = translate(
                locale,
                "code_timeout",
                email=request_state.email_address,
            )
        else:
            message = translate(
                locale,
                "code_failed",
                email=request_state.email_address,
            )

        response = JSONResponse(
            {
                "status": request_state.status,
                "message": message,
                "email": request_state.email_address,
                "code": request_state.code,
            }
        )
        response.set_cookie(
            key=WEB_USER_COOKIE_NAME,
            value=web_user_id,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 365,
        )
        return response

    for route_path in route_variants("/", base_path):
        app.add_api_route(
            route_path,
            index,
            methods=["GET"],
            response_class=HTMLResponse,
            response_model=None,
        )
    for route_path in route_variants("/activate-code", base_path):
        app.add_api_route(
            route_path,
            activate_code,
            methods=["POST"],
            response_class=HTMLResponse,
            response_model=None,
        )
    for route_path in route_variants("/request-code", base_path):
        app.add_api_route(
            route_path,
            request_code,
            methods=["POST"],
            response_class=HTMLResponse,
            response_model=None,
        )
    for route_path in route_variants("/change-account", base_path):
        app.add_api_route(
            route_path,
            change_account,
            methods=["POST"],
            response_class=HTMLResponse,
            response_model=None,
        )
    for route_path in route_variants("/wait", base_path):
        app.add_api_route(
            route_path,
            wait_page,
            methods=["GET"],
            response_class=HTMLResponse,
            response_model=None,
        )
    for route_path in route_variants("/request-status", base_path):
        app.add_api_route(
            route_path,
            request_status,
            methods=["GET"],
            response_class=JSONResponse,
            response_model=None,
        )

    return app


async def read_form_body(request: Request) -> dict[str, str]:
    raw_body = (await request.body()).decode("utf-8", errors="ignore")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    return {key: values[0] if values else "" for key, values in parsed.items()}


def render_page(
    *,
    locale: str,
    base_path: str,
    code_value: str = "",
    status_message: str = "",
    status_kind: str = "info",
    subscription: ActivatedSubscription | None = None,
    service: BotService,
) -> str:
    locale = resolve_locale(locale)
    base_path = normalize_base_path(base_path)
    safe_code_value = html.escape(code_value, quote=True)
    safe_status_message = html.escape(status_message)
    safe_locale = html.escape(locale, quote=True)
    home_path = build_web_path(base_path, "/")
    activate_code_path = build_web_path(base_path, "/activate-code")
    request_code_path = build_web_path(base_path, "/request-code")
    change_account_path = build_web_path(base_path, "/change-account")
    lang_ru_path = f"{home_path}?lang=ru"
    lang_en_path = f"{home_path}?lang=en"
    status_class = {
        "success": "status success",
        "error": "status error",
    }.get(status_kind, "status")

    status_block = ""
    if safe_status_message:
        status_block = f'<div class="{status_class}">{safe_status_message}</div>'

    if subscription is None:
        content_block = f"""
    <section class="card">
      <h2>{html.escape(web_text(locale, "activation_heading"))}</h2>
      <form action="{html.escape(activate_code_path, quote=True)}" method="post">
        <input type="hidden" name="lang" value="{safe_locale}">
        <label for="code">{html.escape(web_text(locale, "activation_label"))}</label>
        <input
          id="code"
          name="code"
          type="text"
          value="{safe_code_value}"
          placeholder="{html.escape(web_text(locale, "activation_placeholder"), quote=True)}"
          autocomplete="off"
          autocapitalize="characters"
          spellcheck="false"
        >
        <button type="submit">{html.escape(web_text(locale, "activation_button"))}</button>
      </form>
    </section>"""
    else:
        content_block = f"""
    <section class="card">
      <h2>{html.escape(web_text(locale, "subscription_heading"))}</h2>
      <div class="details">{render_subscription_details_html(locale, subscription, service)}</div>
      <p class="hint">{html.escape(web_text(locale, "subscription_hint"))}</p>
      <div class="actions">
        <form action="{html.escape(request_code_path, quote=True)}" method="post">
          <input type="hidden" name="lang" value="{safe_locale}">
          <button type="submit">{html.escape(translate(locale, "subscription_request_button"))}</button>
        </form>
        <form action="{html.escape(change_account_path, quote=True)}" method="post">
          <input type="hidden" name="lang" value="{safe_locale}">
          <button type="submit" class="secondary">{html.escape(translate(locale, "subscription_change_button"))}</button>
        </form>
      </div>
    </section>"""

    return f"""<!DOCTYPE html>
<html lang="{safe_locale}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(web_text(locale, "title"))}</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: Arial, sans-serif;
      background: #f6f7fb;
      color: #111827;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      background:
        radial-gradient(circle at top, rgba(15, 118, 110, 0.12), transparent 38%),
        #f6f7fb;
    }}
    main {{
      max-width: 760px;
      margin: 32px auto;
      padding: 0 16px 32px;
    }}
    .card {{
      background: #ffffff;
      border: 1px solid #d7dce5;
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 16px;
      box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
    }}
    h1, h2 {{
      margin-top: 0;
    }}
    .lang-switch {{
      display: flex;
      gap: 10px;
      margin-bottom: 16px;
    }}
    .lang-switch a {{
      color: #0f766e;
      text-decoration: none;
      font-weight: 700;
    }}
    label {{
      display: block;
      margin-bottom: 8px;
      font-weight: 700;
    }}
    input {{
      width: 100%;
      padding: 12px;
      border: 1px solid #bfc7d4;
      border-radius: 8px;
      margin-bottom: 12px;
      text-transform: uppercase;
    }}
    button {{
      padding: 12px 16px;
      border: 0;
      border-radius: 8px;
      background: #0f766e;
      color: #ffffff;
      cursor: pointer;
      font-weight: 700;
    }}
    button.secondary {{
      background: #1f2937;
    }}
    .actions {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .actions form {{
      margin: 0;
    }}
    .status {{
      margin-bottom: 16px;
      padding: 12px 14px;
      border-radius: 10px;
      background: #eef2ff;
      white-space: pre-wrap;
    }}
    .status.success {{
      background: #dcfce7;
    }}
    .status.error {{
      background: #fee2e2;
    }}
    .details {{
      white-space: pre-wrap;
      line-height: 1.6;
      margin-bottom: 12px;
    }}
    .hint {{
      margin: 0 0 16px;
      color: #334155;
    }}
  </style>
</head>
<body>
  <main>
    <div class="lang-switch">
      <a href="{html.escape(lang_ru_path, quote=True)}">{html.escape(web_text(locale, "lang_ru"))}</a>
      <a href="{html.escape(lang_en_path, quote=True)}">{html.escape(web_text(locale, "lang_en"))}</a>
    </div>
    <section class="card">
      <h1>{html.escape(web_text(locale, "title"))}</h1>
      <p>{html.escape(web_text(locale, "subtitle"))}</p>
    </section>
    {status_block}
    {content_block}
  </main>
</body>
</html>"""


def build_page_response(
    *,
    locale: str,
    web_user_id: str,
    base_path: str,
    service: BotService,
    code_value: str = "",
    status_message: str = "",
    status_kind: str = "info",
    status_code: int = 200,
    subscription: ActivatedSubscription | None = None,
) -> HTMLResponse:
    response = HTMLResponse(
        render_page(
            locale=locale,
            base_path=base_path,
            code_value=code_value,
            status_message=status_message,
            status_kind=status_kind,
            subscription=subscription,
            service=service,
        ),
        status_code=status_code,
    )
    response.set_cookie(
        key=WEB_USER_COOKIE_NAME,
        value=web_user_id,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
    )
    return response


def build_wait_page_response(
    *,
    locale: str,
    web_user_id: str,
    base_path: str,
    request_id: str,
    email_address: str,
) -> HTMLResponse:
    response = HTMLResponse(
        render_wait_page(
            locale=locale,
            base_path=base_path,
            request_id=request_id,
            email_address=email_address,
        )
    )
    response.set_cookie(
        key=WEB_USER_COOKIE_NAME,
        value=web_user_id,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
    )
    return response


def get_or_create_web_user_id(request: Request) -> str:
    raw_cookie = request.cookies.get(WEB_USER_COOKIE_NAME, "").strip()
    if raw_cookie:
        return raw_cookie
    return uuid4().hex


def build_web_requester_id(web_user_id: str) -> str:
    return f"web:{web_user_id}"


def normalize_base_path(base_path: str | None) -> str:
    if not base_path:
        return ""

    normalized = base_path.strip()
    if not normalized or normalized == "/":
        return ""
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized.rstrip("/")


def build_web_path(base_path: str, route_path: str) -> str:
    normalized_base_path = normalize_base_path(base_path)
    if route_path == "/":
        return f"{normalized_base_path}/" if normalized_base_path else "/"
    return f"{normalized_base_path}{route_path}" if normalized_base_path else route_path


def build_wait_url(*, base_path: str, request_id: str, locale: str) -> str:
    query_string = urlencode({"request_id": request_id, "lang": locale})
    return f'{build_web_path(base_path, "/wait")}?{query_string}'


def route_variants(route_path: str, base_path: str) -> list[str]:
    paths = [route_path]
    prefixed_path = build_web_path(base_path, route_path)
    if prefixed_path not in paths:
        paths.append(prefixed_path)
    return paths


def resolve_locale(raw_locale: str | None) -> str:
    if raw_locale is None:
        return DEFAULT_LOCALE
    locale = raw_locale.strip().lower()
    if locale not in SUPPORTED_LOCALES:
        return DEFAULT_LOCALE
    return locale


def web_text(locale: str, key: str, **kwargs: str) -> str:
    bundle = WEB_TEXTS.get(locale, WEB_TEXTS[DEFAULT_LOCALE])
    template = bundle.get(key) or WEB_TEXTS[DEFAULT_LOCALE][key]
    return template.format(**kwargs)


def render_subscription_details_html(
    locale: str,
    subscription: ActivatedSubscription,
    service: BotService,
) -> str:
    details = web_text(
        locale,
        "subscription_details_web",
        email=subscription.key.email_address,
        duration_days=str(subscription.key.duration_days),
        end_date=service.format_date(subscription.key.expires_at),
        code=subscription.key.code,
    )
    return html.escape(details).replace("\n", "<br>")


def render_wait_page(
    *,
    locale: str,
    base_path: str,
    request_id: str,
    email_address: str,
) -> str:
    locale = resolve_locale(locale)
    base_path = normalize_base_path(base_path)
    safe_locale = html.escape(locale, quote=True)
    safe_waiting_title = html.escape(web_text(locale, "waiting_title"))
    safe_initial_message = html.escape(
        web_text(locale, "waiting_text", email=email_address)
    )
    status_url = html.escape(
        f'{build_web_path(base_path, "/request-status")}?{urlencode({"request_id": request_id, "lang": locale})}',
        quote=True,
    )
    polling_error = html.escape(web_text(locale, "polling_error"))

    return f"""<!DOCTYPE html>
<html lang="{safe_locale}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_waiting_title}</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: Arial, sans-serif;
      background: #f6f7fb;
      color: #111827;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      background:
        radial-gradient(circle at top, rgba(15, 118, 110, 0.12), transparent 38%),
        #f6f7fb;
    }}
    main {{
      max-width: 760px;
      margin: 32px auto;
      padding: 0 16px 32px;
    }}
    .card {{
      background: #ffffff;
      border: 1px solid #d7dce5;
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 16px;
      box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
    }}
    .status {{
      padding: 12px 14px;
      border-radius: 10px;
      background: #eef2ff;
      white-space: pre-wrap;
    }}
    .status.success {{
      background: #dcfce7;
    }}
    .status.error {{
      background: #fee2e2;
    }}
  </style>
</head>
<body>
  <main>
    <section class="card">
      <h1>{safe_waiting_title}</h1>
      <div id="status" class="status">{safe_initial_message}</div>
    </section>
  </main>
  <script>
    const statusUrl = "{status_url}";
    const pollingErrorText = "{polling_error}";
    const statusNode = document.getElementById("status");

    async function pollStatus() {{
      try {{
        const response = await fetch(statusUrl, {{
          method: "GET",
          cache: "no-store",
          credentials: "same-origin",
        }});

        if (!response.ok) {{
          throw new Error("Bad status: " + response.status);
        }}

        const data = await response.json();
        statusNode.textContent = data.message || "";
        statusNode.className = "status";

        if (data.status === "success") {{
          statusNode.classList.add("success");
          return;
        }}

        if (data.status === "failed" || data.status === "timeout" || data.status === "missing") {{
          statusNode.classList.add("error");
          return;
        }}
      }} catch (error) {{
        statusNode.textContent = pollingErrorText;
        statusNode.className = "status error";
      }}

      window.setTimeout(pollStatus, 2000);
    }}

    pollStatus();
  </script>
</body>
</html>"""
