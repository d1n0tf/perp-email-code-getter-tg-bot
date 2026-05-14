import html
import re
from urllib.parse import parse_qs, urlencode
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from src.config import settings
from src.messages import DEFAULT_LOCALE, SUPPORTED_LOCALES, translate
from src.service import BotService
from src.storage import normalize_email


EMAIL_REGEX = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.IGNORECASE)
WEB_USER_COOKIE_NAME = "perp_web_user_id"

WEB_TEXTS = {
    "ru": {
        "title": "Perp Mail Bot",
        "subtitle": "",
        "request_heading": "Получить код",
        "request_label": "Email",
        "request_placeholder": "example@outlook.com",
        "request_button": "Запросить код",
        "waiting_title": "Ожидание кода",
        "waiting_text": "Жду код для {email}. Страница будет ждать сколько угодно.",
        "polling_error": "Нет связи с сервером. Продолжаю пытаться...",
        "code_found_web": "Код для {email}: {code}",
        "lang_ru": "Русский",
        "lang_en": "English",
    },
    "en": {
        "title": "Perp Mail Bot",
        "subtitle": "",
        "request_heading": "Get code",
        "request_label": "Email",
        "request_placeholder": "example@outlook.com",
        "request_button": "Request code",
        "waiting_title": "Waiting for code",
        "waiting_text": "Waiting for a code for {email}. This page will keep waiting as long as needed.",
        "polling_error": "Connection issue. Retrying...",
        "code_found_web": "Code for {email}: {code}",
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
        return build_page_response(
            locale=locale,
            web_user_id=web_user_id,
            base_path=base_path,
        )

    async def request_code(request: Request):
        payload = await read_form_body(request)
        locale = resolve_locale(payload.get("lang"))
        email_address = normalize_email(payload.get("email", ""))
        web_user_id = get_or_create_web_user_id(request)

        if not EMAIL_REGEX.fullmatch(email_address):
            return build_page_response(
                locale=locale,
                web_user_id=web_user_id,
                base_path=base_path,
                email_value=email_address,
                status_message=translate(locale, "email_invalid"),
                status_kind="error",
                status_code=400,
            )

        client_host = request.client.host if request.client is not None else "web"
        status, request_id = await service.start_web_code_request(
            requester_id=f"web:{web_user_id}",
            requester_kind="web",
            user_id=None,
            chat_id=None,
            username="web",
            full_name=f"web:{client_host}",
            email_address=email_address,
        )

        if status == "missing":
            return build_page_response(
                locale=locale,
                web_user_id=web_user_id,
                base_path=base_path,
                email_value=email_address,
                status_message=translate(locale, "email_missing"),
                status_kind="error",
                status_code=404,
            )
        # if status == "taken":
        #     return build_page_response(
        #         locale=locale,
        #         web_user_id=web_user_id,
        #         base_path=base_path,
        #         email_value=email_address,
        #         status_message=translate(locale, "email_taken"),
        #         status_kind="error",
        #         status_code=409,
        #     )
        if request_id is None:
            return build_page_response(
                locale=locale,
                web_user_id=web_user_id,
                base_path=base_path,
                email_value=email_address,
                status_message=translate(locale, "code_failed", email=email_address),
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

    async def wait_page(request: Request) -> HTMLResponse:
        locale = resolve_locale(request.query_params.get("lang"))
        request_id = request.query_params.get("request_id", "").strip()
        web_user_id = get_or_create_web_user_id(request)
        requester_id = f"web:{web_user_id}"
        request_state = await service.get_web_code_request(
            request_id=request_id,
            requester_id=requester_id,
        )
        if request_state is None:
            return build_page_response(
                locale=locale,
                web_user_id=web_user_id,
                base_path=base_path,
                status_message=translate(locale, "code_failed", email=""),
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
        requester_id = f"web:{web_user_id}"
        request_state = await service.get_web_code_request(
            request_id=request_id,
            requester_id=requester_id,
        )
        if request_state is None:
            response = JSONResponse(
                {
                    "status": "missing",
                    "message": translate(locale, "code_failed", email=""),
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
    for route_path in route_variants("/request-code", base_path):
        app.add_api_route(
            route_path,
            request_code,
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
    email_value: str = "",
    status_message: str = "",
    status_kind: str = "info",
) -> str:
    locale = resolve_locale(locale)
    base_path = normalize_base_path(base_path)
    safe_email_value = html.escape(email_value, quote=True)
    safe_status_message = html.escape(status_message)
    safe_locale = html.escape(locale, quote=True)
    home_path = build_web_path(base_path, "/")
    request_code_path = build_web_path(base_path, "/request-code")
    lang_ru_path = f"{home_path}?lang=ru"
    lang_en_path = f"{home_path}?lang=en"
    status_class = {
        "success": "status success",
        "error": "status error",
    }.get(status_kind, "status")

    status_block = ""
    if safe_status_message:
        status_block = f'<div class="{status_class}">{safe_status_message}</div>'

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
      background: #f6f7fb;
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
    .status {{
      margin-bottom: 16px;
      padding: 12px 14px;
      border-radius: 10px;
      background: #eef2ff;
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
    <div class="lang-switch">
      <a href="{html.escape(lang_ru_path, quote=True)}">{html.escape(web_text(locale, "lang_ru"))}</a>
      <a href="{html.escape(lang_en_path, quote=True)}">{html.escape(web_text(locale, "lang_en"))}</a>
    </div>
    <h1>{html.escape(web_text(locale, "title"))}</h1>
    <p>{html.escape(web_text(locale, "subtitle"))}</p>
    {status_block}
    <section class="card">
      <h2>{html.escape(web_text(locale, "request_heading"))}</h2>
      <form action="{html.escape(request_code_path, quote=True)}" method="post">
        <input type="hidden" name="lang" value="{safe_locale}">
        <label for="email">{html.escape(web_text(locale, "request_label"))}</label>
        <input
          id="email"
          name="email"
          type="text"
          value="{safe_email_value}"
          placeholder="{html.escape(web_text(locale, "request_placeholder"), quote=True)}"
        >
        <button type="submit">{html.escape(web_text(locale, "request_button"))}</button>
      </form>
    </section>
  </main>
</body>
</html>"""


def build_page_response(
    *,
    locale: str,
    web_user_id: str,
    base_path: str,
    email_value: str = "",
    status_message: str = "",
    status_kind: str = "info",
    status_code: int = 200,
) -> HTMLResponse:
    response = HTMLResponse(
        render_page(
            locale=locale,
            base_path=base_path,
            email_value=email_value,
            status_message=status_message,
            status_kind=status_kind,
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
      background: #f6f7fb;
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
    }}
    .status {{
      padding: 12px 14px;
      border-radius: 10px;
      background: #eef2ff;
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
