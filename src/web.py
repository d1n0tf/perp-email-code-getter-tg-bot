import html
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from src.config import settings
from src.messages import DEFAULT_LOCALE, SUPPORTED_LOCALES, translate
from src.service import ActivatedSubscription, BotService
from src.storage import (
    EmailAccount,
    SubscriptionKey,
    UserKeyActivation,
    normalize_email,
    normalize_key_code,
)


WEB_USER_COOKIE_NAME = "perp_web_user_id"
WEB_ADMIN_COOKIE_NAME = "perp_admin_session"
ADMIN_CONTROL_PATH = "/admin_control"
ADMIN_CONTROL_LOGIN_PATH = f"{ADMIN_CONTROL_PATH}/login"
ADMIN_CONTROL_LOGOUT_PATH = f"{ADMIN_CONTROL_PATH}/logout"
ADMIN_CONTROL_ADD_PATH = f"{ADMIN_CONTROL_PATH}/accounts/add"
ADMIN_CONTROL_UPDATE_PATH = f"{ADMIN_CONTROL_PATH}/accounts/update"
ADMIN_CONTROL_DELETE_PATH = f"{ADMIN_CONTROL_PATH}/accounts/delete"

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
        "admin_title": "Admin Control",
        "admin_subtitle": "Управление активированными подписками и почтовыми аккаунтами.",
        "admin_login_title": "Вход в Admin Control",
        "admin_password_label": "Пароль",
        "admin_login_button": "Войти",
        "admin_logout_button": "Выйти",
        "admin_login_failed": "Неверный пароль.",
        "admin_password_missing": "WEB_ADMIN_PASSWORD не настроен в .env.",
        "admin_session_required": "Сессия администратора истекла. Войдите снова.",
        "admin_add_button": "Добавить аккаунт",
        "admin_add_label": "Строка в формате /add",
        "admin_add_placeholder": "EMAIL:PASS:EMAIL:PASS:TOKEN:ID",
        "admin_add_submit": "Добавить",
        "admin_cancel_button": "Отмена",
        "admin_table_empty": "Активированных подписок пока нет.",
        "admin_col_id": "ID",
        "admin_col_duration": "Срок подписки",
        "admin_col_activated": "Дата активации",
        "admin_col_expires": "Дата окончания",
        "admin_col_days_left": "Дней осталось",
        "admin_col_key": "Ключ",
        "admin_col_email": "Почта",
        "admin_col_actions": "Получить данные",
        "admin_show_data_button": "Получить данные",
        "admin_hide_data_button": "Скрыть",
        "admin_edit_button": "Изменить",
        "admin_delete_button": "Удалить",
        "admin_save_button": "Сохранить",
        "admin_account_missing": "Аккаунт для этой почты не найден.",
        "admin_account_details_title": "Данные аккаунта",
        "admin_field_login_email": "Логин",
        "admin_field_login_password": "Пароль",
        "admin_field_recovery_email": "Почта восстановления",
        "admin_field_recovery_password": "Пароль восстановления",
        "admin_field_refresh_token": "Refresh token",
        "admin_field_client_id": "Client ID",
        "admin_field_raw": "Raw /add",
        "admin_account_added": "Аккаунт добавлен.",
        "admin_account_updated": "Аккаунт сохранён.",
        "admin_account_deleted": "Аккаунт удалён.",
        "admin_account_exists": "Аккаунт уже существовал и был обновлён.",
        "admin_account_update_conflict": "Аккаунт с таким логином уже существует.",
        "admin_account_update_missing": "Исходный аккаунт не найден.",
        "admin_account_invalid": "Не удалось разобрать данные аккаунта.",
        "admin_delete_confirm": (
            "Удалить аккаунт `{email}`? Привязанные ключи останутся, "
            "но перестанут выдавать данные аккаунта."
        ),
        "admin_delete_confirm_button": "Подтвердить удаление",
        "admin_delete_missing": "Аккаунт уже удалён.",
        "admin_id_web": "web",
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
        "admin_title": "Admin Control",
        "admin_subtitle": "Manage activated subscriptions and mailbox accounts.",
        "admin_login_title": "Admin Control Login",
        "admin_password_label": "Password",
        "admin_login_button": "Log in",
        "admin_logout_button": "Log out",
        "admin_login_failed": "Invalid password.",
        "admin_password_missing": "WEB_ADMIN_PASSWORD is not configured in .env.",
        "admin_session_required": "The admin session expired. Log in again.",
        "admin_add_button": "Add account",
        "admin_add_label": "Value in /add format",
        "admin_add_placeholder": "EMAIL:PASS:EMAIL:PASS:TOKEN:ID",
        "admin_add_submit": "Add",
        "admin_cancel_button": "Cancel",
        "admin_table_empty": "There are no activated subscriptions yet.",
        "admin_col_id": "ID",
        "admin_col_duration": "Subscription term",
        "admin_col_activated": "Activated at",
        "admin_col_expires": "End date",
        "admin_col_days_left": "Days left",
        "admin_col_key": "Key",
        "admin_col_email": "Email",
        "admin_col_actions": "Account data",
        "admin_show_data_button": "Get data",
        "admin_hide_data_button": "Hide",
        "admin_edit_button": "Edit",
        "admin_delete_button": "Delete",
        "admin_save_button": "Save",
        "admin_account_missing": "The account for this email was not found.",
        "admin_account_details_title": "Account details",
        "admin_field_login_email": "Login",
        "admin_field_login_password": "Password",
        "admin_field_recovery_email": "Recovery email",
        "admin_field_recovery_password": "Recovery password",
        "admin_field_refresh_token": "Refresh token",
        "admin_field_client_id": "Client ID",
        "admin_field_raw": "Raw /add",
        "admin_account_added": "The account has been added.",
        "admin_account_updated": "The account has been saved.",
        "admin_account_deleted": "The account has been deleted.",
        "admin_account_exists": "The account already existed and was updated.",
        "admin_account_update_conflict": "An account with this login already exists.",
        "admin_account_update_missing": "The source account was not found.",
        "admin_account_invalid": "Could not parse the account data.",
        "admin_delete_confirm": (
            "Delete account `{email}`? Linked keys will stay in place, "
            "but they will stop returning account data."
        ),
        "admin_delete_confirm_button": "Confirm deletion",
        "admin_delete_missing": "The account has already been deleted.",
        "admin_id_web": "web",
    },
}


@dataclass(slots=True)
class AdminPageState:
    selected_row_id: str | None = None
    panel: str | None = None
    show_add_form: bool = False
    add_value: str = ""
    edit_values: dict[str, str] | None = None


@dataclass(slots=True)
class AdminSubscriptionRow:
    row_id: str
    display_id: str
    activation: UserKeyActivation
    key: SubscriptionKey
    account: EmailAccount | None
    days_left: int


def create_web_app(service: BotService) -> FastAPI:
    app = FastAPI(title="Perp Mail Bot")
    app.state.service = service
    app.state.admin_sessions = set()
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

    async def admin_control(request: Request) -> HTMLResponse:
        locale = resolve_locale(request.query_params.get("lang"))
        if not service.settings.web_admin_password:
            return build_admin_login_response(
                locale=locale,
                base_path=base_path,
                allow_login=False,
                status_message=web_text(locale, "admin_password_missing"),
                status_kind="error",
                status_code=503,
            )

        if not is_admin_authenticated(request):
            return build_admin_login_response(
                locale=locale,
                base_path=base_path,
            )

        state = admin_state_from_request(request)
        return await build_admin_control_response(
            locale=locale,
            base_path=base_path,
            service=service,
            state=state,
        )

    async def admin_login(request: Request):
        payload = await read_form_body(request)
        locale = resolve_locale(payload.get("lang"))
        if not service.settings.web_admin_password:
            return build_admin_login_response(
                locale=locale,
                base_path=base_path,
                allow_login=False,
                status_message=web_text(locale, "admin_password_missing"),
                status_kind="error",
                status_code=503,
            )

        if payload.get("password", "") != service.settings.web_admin_password:
            return build_admin_login_response(
                locale=locale,
                base_path=base_path,
                status_message=web_text(locale, "admin_login_failed"),
                status_kind="error",
                status_code=401,
            )

        session_token = create_admin_session(request)
        response = RedirectResponse(
            url=build_query_url(
                build_web_path(base_path, ADMIN_CONTROL_PATH),
                {"lang": locale},
            ),
            status_code=303,
        )
        response.set_cookie(
            key=WEB_ADMIN_COOKIE_NAME,
            value=session_token,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 12,
        )
        return response

    async def admin_logout(request: Request):
        payload = await read_form_body(request)
        locale = resolve_locale(payload.get("lang"))
        clear_admin_session(request)
        response = RedirectResponse(
            url=build_query_url(
                build_web_path(base_path, ADMIN_CONTROL_PATH),
                {"lang": locale},
            ),
            status_code=303,
        )
        response.delete_cookie(WEB_ADMIN_COOKIE_NAME)
        return response

    async def admin_add_account(request: Request) -> HTMLResponse:
        payload = await read_form_body(request)
        locale = resolve_locale(payload.get("lang"))
        auth_response = build_admin_auth_error_response(
            request=request,
            locale=locale,
            base_path=base_path,
            service=service,
        )
        if auth_response is not None:
            return auth_response

        raw_value = payload.get("raw_account", "").strip()
        state = AdminPageState(show_add_form=True, add_value=raw_value)
        try:
            _, existed = await service.add_account(raw_value)
        except ValueError:
            return await build_admin_control_response(
                locale=locale,
                base_path=base_path,
                service=service,
                state=state,
                status_message=web_text(locale, "admin_account_invalid"),
                status_kind="error",
                status_code=400,
            )

        return await build_admin_control_response(
            locale=locale,
            base_path=base_path,
            service=service,
            state=AdminPageState(),
            status_message=web_text(
                locale,
                "admin_account_exists" if existed else "admin_account_added",
            ),
            status_kind="success",
        )

    async def admin_update_account(request: Request) -> HTMLResponse:
        payload = await read_form_body(request)
        locale = resolve_locale(payload.get("lang"))
        auth_response = build_admin_auth_error_response(
            request=request,
            locale=locale,
            base_path=base_path,
            service=service,
        )
        if auth_response is not None:
            return auth_response

        row_id = payload.get("row_id", "").strip() or None
        form_values = extract_account_form_values(payload)
        state = AdminPageState(
            selected_row_id=row_id,
            panel="edit",
            edit_values=form_values,
        )

        try:
            account = build_account_from_form_values(form_values)
        except ValueError:
            return await build_admin_control_response(
                locale=locale,
                base_path=base_path,
                service=service,
                state=state,
                status_message=web_text(locale, "admin_account_invalid"),
                status_kind="error",
                status_code=400,
            )

        update_status = await service.update_account(
            payload.get("original_email", ""),
            account,
        )
        if update_status == "missing":
            return await build_admin_control_response(
                locale=locale,
                base_path=base_path,
                service=service,
                state=state,
                status_message=web_text(locale, "admin_account_update_missing"),
                status_kind="error",
                status_code=404,
            )
        if update_status == "conflict":
            return await build_admin_control_response(
                locale=locale,
                base_path=base_path,
                service=service,
                state=state,
                status_message=web_text(locale, "admin_account_update_conflict"),
                status_kind="error",
                status_code=409,
            )

        return await build_admin_control_response(
            locale=locale,
            base_path=base_path,
            service=service,
            state=AdminPageState(selected_row_id=row_id, panel="details"),
            status_message=web_text(locale, "admin_account_updated"),
            status_kind="success",
        )

    async def admin_delete_account(request: Request) -> HTMLResponse:
        payload = await read_form_body(request)
        locale = resolve_locale(payload.get("lang"))
        auth_response = build_admin_auth_error_response(
            request=request,
            locale=locale,
            base_path=base_path,
            service=service,
        )
        if auth_response is not None:
            return auth_response

        row_id = payload.get("row_id", "").strip() or None
        deleted = await service.delete_account(payload.get("email", ""))
        return await build_admin_control_response(
            locale=locale,
            base_path=base_path,
            service=service,
            state=AdminPageState(selected_row_id=row_id, panel="details"),
            status_message=web_text(
                locale,
                "admin_account_deleted" if deleted else "admin_delete_missing",
            ),
            status_kind="success" if deleted else "error",
            status_code=200 if deleted else 404,
        )

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
    for route_path in route_variants(ADMIN_CONTROL_PATH, base_path):
        app.add_api_route(
            route_path,
            admin_control,
            methods=["GET"],
            response_class=HTMLResponse,
            response_model=None,
        )
    for route_path in route_variants(ADMIN_CONTROL_LOGIN_PATH, base_path):
        app.add_api_route(
            route_path,
            admin_login,
            methods=["POST"],
            response_class=HTMLResponse,
            response_model=None,
        )
    for route_path in route_variants(ADMIN_CONTROL_LOGOUT_PATH, base_path):
        app.add_api_route(
            route_path,
            admin_logout,
            methods=["POST"],
            response_class=HTMLResponse,
            response_model=None,
        )
    for route_path in route_variants(ADMIN_CONTROL_ADD_PATH, base_path):
        app.add_api_route(
            route_path,
            admin_add_account,
            methods=["POST"],
            response_class=HTMLResponse,
            response_model=None,
        )
    for route_path in route_variants(ADMIN_CONTROL_UPDATE_PATH, base_path):
        app.add_api_route(
            route_path,
            admin_update_account,
            methods=["POST"],
            response_class=HTMLResponse,
            response_model=None,
        )
    for route_path in route_variants(ADMIN_CONTROL_DELETE_PATH, base_path):
        app.add_api_route(
            route_path,
            admin_delete_account,
            methods=["POST"],
            response_class=HTMLResponse,
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
    safe_locale = html.escape(locale, quote=True)
    home_path = build_web_path(base_path, "/")
    activate_code_path = build_web_path(base_path, "/activate-code")
    request_code_path = build_web_path(base_path, "/request-code")
    change_account_path = build_web_path(base_path, "/change-account")
    lang_ru_path = f"{home_path}?lang=ru"
    lang_en_path = f"{home_path}?lang=en"

    if subscription is None:
        content_block = f"""
    <section class="card">
      <h2>{html.escape(web_text(locale, "activation_heading"))}</h2>
      <form action="{html.escape(activate_code_path, quote=True)}" method="post">
        <input type="hidden" name="lang" value="{safe_locale}">
        <label for="code">{html.escape(web_text(locale, "activation_label"))}</label>
        <input
          id="code"
          class="code-input"
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
    {common_base_styles()}
    main {{
      max-width: 760px;
      margin: 32px auto;
      padding: 0 16px 32px;
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
    .code-input {{
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
    {render_status_block(status_message, status_kind)}
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
    return build_query_url(
        build_web_path(base_path, "/wait"),
        {"request_id": request_id, "lang": locale},
    )


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
        build_query_url(
            build_web_path(base_path, "/request-status"),
            {"request_id": request_id, "lang": locale},
        ),
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
    {common_base_styles()}
    main {{
      max-width: 760px;
      margin: 32px auto;
      padding: 0 16px 32px;
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


async def build_admin_control_response(
    *,
    locale: str,
    base_path: str,
    service: BotService,
    state: AdminPageState,
    status_message: str = "",
    status_kind: str = "info",
    status_code: int = 200,
) -> HTMLResponse:
    subscriptions = await service.list_activated_subscriptions()
    rows = build_admin_rows(subscriptions, locale=locale)
    return HTMLResponse(
        render_admin_control_page(
            locale=locale,
            base_path=base_path,
            rows=rows,
            state=state,
            status_message=status_message,
            status_kind=status_kind,
        ),
        status_code=status_code,
    )


def build_admin_login_response(
    *,
    locale: str,
    base_path: str,
    allow_login: bool = True,
    status_message: str = "",
    status_kind: str = "info",
    status_code: int = 200,
) -> HTMLResponse:
    return HTMLResponse(
        render_admin_login_page(
            locale=locale,
            base_path=base_path,
            allow_login=allow_login,
            status_message=status_message,
            status_kind=status_kind,
        ),
        status_code=status_code,
    )


def build_admin_auth_error_response(
    *,
    request: Request,
    locale: str,
    base_path: str,
    service: BotService,
) -> HTMLResponse | None:
    if not service.settings.web_admin_password:
        return build_admin_login_response(
            locale=locale,
            base_path=base_path,
            allow_login=False,
            status_message=web_text(locale, "admin_password_missing"),
            status_kind="error",
            status_code=503,
        )
    if is_admin_authenticated(request):
        return None
    return build_admin_login_response(
        locale=locale,
        base_path=base_path,
        status_message=web_text(locale, "admin_session_required"),
        status_kind="error",
        status_code=401,
    )


def render_admin_login_page(
    *,
    locale: str,
    base_path: str,
    allow_login: bool,
    status_message: str,
    status_kind: str,
) -> str:
    locale = resolve_locale(locale)
    safe_locale = html.escape(locale, quote=True)
    login_action = build_web_path(base_path, ADMIN_CONTROL_LOGIN_PATH)
    admin_home = build_query_url(
        build_web_path(base_path, ADMIN_CONTROL_PATH),
        {"lang": locale},
    )
    lang_ru_path = build_query_url(
        build_web_path(base_path, ADMIN_CONTROL_PATH),
        {"lang": "ru"},
    )
    lang_en_path = build_query_url(
        build_web_path(base_path, ADMIN_CONTROL_PATH),
        {"lang": "en"},
    )
    form_block = ""
    if allow_login:
        form_block = f"""
    <form action="{html.escape(login_action, quote=True)}" method="post" class="stack">
      <input type="hidden" name="lang" value="{safe_locale}">
      <label for="password">{html.escape(web_text(locale, "admin_password_label"))}</label>
      <input id="password" name="password" type="password" autocomplete="current-password">
      <button type="submit">{html.escape(web_text(locale, "admin_login_button"))}</button>
    </form>"""

    return f"""<!DOCTYPE html>
<html lang="{safe_locale}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(web_text(locale, "admin_title"))}</title>
  <style>
    {common_base_styles()}
    main {{
      max-width: 560px;
      margin: 48px auto;
      padding: 0 16px 32px;
    }}
    .lang-switch {{
      display: flex;
      gap: 12px;
      margin-bottom: 16px;
    }}
    .lang-switch a {{
      color: #0f766e;
      text-decoration: none;
      font-weight: 700;
    }}
    .stack {{
      display: grid;
      gap: 12px;
    }}
    label {{
      font-weight: 700;
    }}
    input {{
      width: 100%;
      padding: 12px;
      border: 1px solid #bfc7d4;
      border-radius: 8px;
    }}
    .subtle-link {{
      color: #0f766e;
      text-decoration: none;
      font-size: 14px;
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
      <h1>{html.escape(web_text(locale, "admin_login_title"))}</h1>
      <p>{html.escape(web_text(locale, "admin_subtitle"))}</p>
      {render_status_block(status_message, status_kind)}
      {form_block}
      <p><a class="subtle-link" href="{html.escape(admin_home, quote=True)}">{html.escape(web_text(locale, "admin_title"))}</a></p>
    </section>
  </main>
</body>
</html>"""


def render_admin_control_page(
    *,
    locale: str,
    base_path: str,
    rows: list[AdminSubscriptionRow],
    state: AdminPageState,
    status_message: str,
    status_kind: str,
) -> str:
    locale = resolve_locale(locale)
    safe_locale = html.escape(locale, quote=True)
    admin_home = build_web_path(base_path, ADMIN_CONTROL_PATH)
    add_action = build_web_path(base_path, ADMIN_CONTROL_ADD_PATH)
    update_action = build_web_path(base_path, ADMIN_CONTROL_UPDATE_PATH)
    delete_action = build_web_path(base_path, ADMIN_CONTROL_DELETE_PATH)
    logout_action = build_web_path(base_path, ADMIN_CONTROL_LOGOUT_PATH)
    lang_ru_path = build_query_url(admin_home, {"lang": "ru"})
    lang_en_path = build_query_url(admin_home, {"lang": "en"})

    add_section = ""
    if state.show_add_form:
        add_section = f"""
    <section class="panel">
      <h2>{html.escape(web_text(locale, "admin_add_button"))}</h2>
      <form action="{html.escape(add_action, quote=True)}" method="post" class="stack">
        <input type="hidden" name="lang" value="{safe_locale}">
        <label for="raw_account">{html.escape(web_text(locale, "admin_add_label"))}</label>
        <textarea id="raw_account" name="raw_account" rows="3" placeholder="{html.escape(web_text(locale, "admin_add_placeholder"), quote=True)}">{html.escape(state.add_value)}</textarea>
        <div class="toolbar-actions">
          <button type="submit">{html.escape(web_text(locale, "admin_add_submit"))}</button>
          <a class="button ghost" href="{html.escape(build_query_url(admin_home, {"lang": locale}), quote=True)}">{html.escape(web_text(locale, "admin_cancel_button"))}</a>
        </div>
      </form>
    </section>"""

    if rows:
        table_rows = "".join(
            render_admin_table_row(
                locale=locale,
                base_path=base_path,
                row=row,
                state=state,
                update_action=update_action,
                delete_action=delete_action,
            )
            for row in rows
        )
    else:
        table_rows = f"""
          <tr>
            <td colspan="8" class="empty">{html.escape(web_text(locale, "admin_table_empty"))}</td>
          </tr>"""

    return f"""<!DOCTYPE html>
<html lang="{safe_locale}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(web_text(locale, "admin_title"))}</title>
  <style>
    {common_base_styles()}
    main {{
      max-width: 1440px;
      margin: 24px auto 40px;
      padding: 0 16px;
    }}
    .lang-switch {{
      display: flex;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .lang-switch a {{
      color: #0f766e;
      text-decoration: none;
      font-weight: 700;
    }}
    .hero {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 16px;
    }}
    .hero h1 {{
      margin-bottom: 8px;
    }}
    .hero p {{
      margin: 0;
      color: #475569;
    }}
    .panel {{
      background: #ffffff;
      border: 1px solid #d7dce5;
      border-radius: 8px;
      padding: 18px;
      margin-bottom: 16px;
      box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
    }}
    .toolbar {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      margin-bottom: 16px;
      flex-wrap: wrap;
    }}
    .toolbar-actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }}
    .toolbar-actions form {{
      margin: 0;
    }}
    .button,
    button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding: 10px 14px;
      border: 0;
      border-radius: 8px;
      background: #0f766e;
      color: #ffffff;
      cursor: pointer;
      font-weight: 700;
      text-decoration: none;
      line-height: 1.2;
    }}
    .button.ghost,
    button.ghost {{
      background: #e2e8f0;
      color: #0f172a;
    }}
    .button.secondary,
    button.secondary {{
      background: #1f2937;
    }}
    .button.danger,
    button.danger {{
      background: #b91c1c;
    }}
    .table-wrap {{
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 1120px;
    }}
    th, td {{
      padding: 12px 10px;
      border-bottom: 1px solid #e2e8f0;
      vertical-align: top;
      text-align: left;
    }}
    thead th {{
      font-size: 13px;
      color: #475569;
      background: #f8fafc;
      position: sticky;
      top: 0;
    }}
    tbody tr:hover > td {{
      background: #fbfdff;
    }}
    .mono {{
      font-family: Consolas, "Courier New", monospace;
      word-break: break-all;
    }}
    .table-actions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .table-actions form {{
      margin: 0;
    }}
    .detail-row td {{
      background: #f8fafc;
      padding: 0;
    }}
    .detail-panel {{
      padding: 16px;
      border-top: 1px solid #e2e8f0;
    }}
    .detail-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(240px, 1fr));
      gap: 12px 20px;
      margin-bottom: 14px;
    }}
    .detail-item {{
      display: grid;
      gap: 4px;
    }}
    .detail-item span {{
      font-size: 12px;
      color: #475569;
      text-transform: uppercase;
      letter-spacing: 0;
    }}
    .detail-item strong {{
      font-size: 14px;
      word-break: break-word;
    }}
    .detail-full {{
      grid-column: 1 / -1;
    }}
    .stack {{
      display: grid;
      gap: 12px;
    }}
    .form-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(240px, 1fr));
      gap: 12px 16px;
    }}
    .form-grid label {{
      display: grid;
      gap: 6px;
      font-weight: 700;
      font-size: 14px;
    }}
    input,
    textarea {{
      width: 100%;
      padding: 10px 12px;
      border: 1px solid #bfc7d4;
      border-radius: 8px;
      font: inherit;
      color: inherit;
      background: #ffffff;
    }}
    textarea {{
      resize: vertical;
      min-height: 92px;
    }}
    .empty {{
      text-align: center;
      color: #475569;
      padding: 20px;
    }}
    .note {{
      color: #475569;
      margin: 0 0 12px;
    }}
    @media (max-width: 900px) {{
      .hero {{
        flex-direction: column;
      }}
      .detail-grid,
      .form-grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <div class="lang-switch">
      <a href="{html.escape(lang_ru_path, quote=True)}">{html.escape(web_text(locale, "lang_ru"))}</a>
      <a href="{html.escape(lang_en_path, quote=True)}">{html.escape(web_text(locale, "lang_en"))}</a>
    </div>
    <section class="hero panel">
      <div>
        <h1>{html.escape(web_text(locale, "admin_title"))}</h1>
        <p>{html.escape(web_text(locale, "admin_subtitle"))}</p>
      </div>
      <form action="{html.escape(logout_action, quote=True)}" method="post">
        <input type="hidden" name="lang" value="{safe_locale}">
        <button type="submit" class="secondary">{html.escape(web_text(locale, "admin_logout_button"))}</button>
      </form>
    </section>
    {render_status_block(status_message, status_kind)}
    <section class="toolbar panel">
      <div class="toolbar-actions">
        <a class="button" href="{html.escape(build_query_url(admin_home, {"lang": locale, "add": "1"}), quote=True)}">{html.escape(web_text(locale, "admin_add_button"))}</a>
      </div>
    </section>
    {add_section}
    <section class="panel">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{html.escape(web_text(locale, "admin_col_id"))}</th>
              <th>{html.escape(web_text(locale, "admin_col_duration"))}</th>
              <th>{html.escape(web_text(locale, "admin_col_activated"))}</th>
              <th>{html.escape(web_text(locale, "admin_col_expires"))}</th>
              <th>{html.escape(web_text(locale, "admin_col_days_left"))}</th>
              <th>{html.escape(web_text(locale, "admin_col_key"))}</th>
              <th>{html.escape(web_text(locale, "admin_col_email"))}</th>
              <th>{html.escape(web_text(locale, "admin_col_actions"))}</th>
            </tr>
          </thead>
          <tbody>
            {table_rows}
          </tbody>
        </table>
      </div>
    </section>
  </main>
</body>
</html>"""


def render_admin_table_row(
    *,
    locale: str,
    base_path: str,
    row: AdminSubscriptionRow,
    state: AdminPageState,
    update_action: str,
    delete_action: str,
) -> str:
    admin_home = build_web_path(base_path, ADMIN_CONTROL_PATH)
    is_selected = state.selected_row_id == row.row_id
    panel = state.panel if is_selected else None
    details_url = build_query_url(
        admin_home,
        {"lang": locale, "row": row.row_id, "panel": "details"},
    )
    hide_url = build_query_url(admin_home, {"lang": locale})
    row_html = f"""
            <tr>
              <td class="mono">{html.escape(row.display_id)}</td>
              <td>{row.key.duration_days}</td>
              <td>{html.escape(format_admin_datetime(row.activation.activated_at))}</td>
              <td>{html.escape(format_admin_datetime(row.key.expires_at, include_time=False))}</td>
              <td>{row.days_left}</td>
              <td class="mono">{html.escape(row.key.code)}</td>
              <td class="mono">{html.escape(row.key.email_address)}</td>
              <td>
                <div class="table-actions">
                  <a class="button ghost" href="{html.escape(hide_url if panel == "details" else details_url, quote=True)}">{html.escape(web_text(locale, "admin_hide_data_button" if panel == "details" else "admin_show_data_button"))}</a>
                </div>
              </td>
            </tr>"""

    if not panel:
        return row_html

    detail_body = render_admin_detail_panel(
        locale=locale,
        base_path=base_path,
        row=row,
        state=state,
        update_action=update_action,
        delete_action=delete_action,
    )
    return f"""{row_html}
            <tr class="detail-row">
              <td colspan="8">
                <div class="detail-panel">
                  {detail_body}
                </div>
              </td>
            </tr>"""


def render_admin_detail_panel(
    *,
    locale: str,
    base_path: str,
    row: AdminSubscriptionRow,
    state: AdminPageState,
    update_action: str,
    delete_action: str,
) -> str:
    admin_home = build_web_path(base_path, ADMIN_CONTROL_PATH)
    cancel_url = build_query_url(
        admin_home,
        {"lang": locale, "row": row.row_id, "panel": "details"},
    )
    close_url = build_query_url(admin_home, {"lang": locale})

    if row.account is None:
        return f"""
      <p class="note">{html.escape(web_text(locale, "admin_account_missing"))}</p>
      <div class="toolbar-actions">
        <a class="button ghost" href="{html.escape(close_url, quote=True)}">{html.escape(web_text(locale, "admin_cancel_button"))}</a>
      </div>"""

    if state.panel == "edit":
        values = state.edit_values or account_to_form_values(row.account)
        return f"""
      <form action="{html.escape(update_action, quote=True)}" method="post" class="stack">
        <input type="hidden" name="lang" value="{html.escape(locale, quote=True)}">
        <input type="hidden" name="row_id" value="{html.escape(row.row_id, quote=True)}">
        <input type="hidden" name="original_email" value="{html.escape(row.account.login_email, quote=True)}">
        <div class="form-grid">
          {render_account_input(locale, "login_email", values["login_email"], input_type="email")}
          {render_account_input(locale, "login_password", values["login_password"])}
          {render_account_input(locale, "recovery_email", values["recovery_email"], input_type="email")}
          {render_account_input(locale, "recovery_password", values["recovery_password"])}
          {render_account_input(locale, "refresh_token", values["refresh_token"])}
          {render_account_input(locale, "client_id", values["client_id"])}
        </div>
        <div class="toolbar-actions">
          <button type="submit">{html.escape(web_text(locale, "admin_save_button"))}</button>
          <a class="button ghost" href="{html.escape(cancel_url, quote=True)}">{html.escape(web_text(locale, "admin_cancel_button"))}</a>
        </div>
      </form>"""

    if state.panel == "delete":
        return f"""
      <p class="note">{html.escape(web_text(locale, "admin_delete_confirm", email=row.account.login_email))}</p>
      <div class="toolbar-actions">
        <form action="{html.escape(delete_action, quote=True)}" method="post">
          <input type="hidden" name="lang" value="{html.escape(locale, quote=True)}">
          <input type="hidden" name="row_id" value="{html.escape(row.row_id, quote=True)}">
          <input type="hidden" name="email" value="{html.escape(row.account.login_email, quote=True)}">
          <button type="submit" class="danger">{html.escape(web_text(locale, "admin_delete_confirm_button"))}</button>
        </form>
        <a class="button ghost" href="{html.escape(cancel_url, quote=True)}">{html.escape(web_text(locale, "admin_cancel_button"))}</a>
      </div>"""

    edit_url = build_query_url(
        admin_home,
        {"lang": locale, "row": row.row_id, "panel": "edit"},
    )
    delete_url = build_query_url(
        admin_home,
        {"lang": locale, "row": row.row_id, "panel": "delete"},
    )
    return f"""
      <h3>{html.escape(web_text(locale, "admin_account_details_title"))}</h3>
      <div class="detail-grid">
        {render_detail_item(locale, "login_email", row.account.login_email)}
        {render_detail_item(locale, "login_password", row.account.login_password)}
        {render_detail_item(locale, "recovery_email", row.account.recovery_email)}
        {render_detail_item(locale, "recovery_password", row.account.recovery_password)}
        {render_detail_item(locale, "refresh_token", row.account.refresh_token)}
        {render_detail_item(locale, "client_id", row.account.client_id)}
        {render_detail_item(locale, "raw", row.account.raw, full_width=True)}
      </div>
      <div class="toolbar-actions">
        <a class="button" href="{html.escape(edit_url, quote=True)}">{html.escape(web_text(locale, "admin_edit_button"))}</a>
        <a class="button danger" href="{html.escape(delete_url, quote=True)}">{html.escape(web_text(locale, "admin_delete_button"))}</a>
        <a class="button ghost" href="{html.escape(close_url, quote=True)}">{html.escape(web_text(locale, "admin_cancel_button"))}</a>
      </div>"""


def render_detail_item(
    locale: str,
    field_name: str,
    value: str,
    *,
    full_width: bool = False,
) -> str:
    extra_class = " detail-full" if full_width else ""
    return (
        f'<div class="detail-item{extra_class}">'
        f"<span>{html.escape(web_text(locale, f'admin_field_{field_name}'))}</span>"
        f'<strong class="mono">{html.escape(value)}</strong>'
        "</div>"
    )


def render_account_input(
    locale: str,
    field_name: str,
    value: str,
    *,
    input_type: str = "text",
) -> str:
    return f"""
          <label>
            {html.escape(web_text(locale, f"admin_field_{field_name}"))}
            <input type="{html.escape(input_type, quote=True)}" name="{html.escape(field_name, quote=True)}" value="{html.escape(value, quote=True)}">
          </label>"""


def common_base_styles() -> str:
    return """
    :root {
      color-scheme: light;
      font-family: Arial, sans-serif;
      background: #f6f7fb;
      color: #111827;
    }
    * {
      box-sizing: border-box;
    }
    body {
      margin: 0;
      background:
        radial-gradient(circle at top, rgba(15, 118, 110, 0.10), transparent 38%),
        #f6f7fb;
    }
    h1, h2, h3 {
      margin-top: 0;
    }
    .card {
      background: #ffffff;
      border: 1px solid #d7dce5;
      border-radius: 8px;
      padding: 20px;
      margin-bottom: 16px;
      box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
    }
    .status {
      margin-bottom: 16px;
      padding: 12px 14px;
      border-radius: 8px;
      background: #eef2ff;
      white-space: pre-wrap;
      border: 1px solid #dbe4ff;
    }
    .status.success {
      background: #dcfce7;
      border-color: #bbf7d0;
    }
    .status.error {
      background: #fee2e2;
      border-color: #fecaca;
    }
    """


def render_status_block(status_message: str, status_kind: str) -> str:
    if not status_message:
        return ""
    status_class = {
        "success": "status success",
        "error": "status error",
    }.get(status_kind, "status")
    return f'<div class="{status_class}">{html.escape(status_message)}</div>'


def build_query_url(path: str, params: dict[str, str | None]) -> str:
    clean_params = {
        key: value
        for key, value in params.items()
        if value is not None and value != ""
    }
    if not clean_params:
        return path
    return f"{path}?{urlencode(clean_params)}"


def is_admin_authenticated(request: Request) -> bool:
    token = request.cookies.get(WEB_ADMIN_COOKIE_NAME, "").strip()
    return bool(token and token in request.app.state.admin_sessions)


def create_admin_session(request: Request) -> str:
    token = uuid4().hex
    request.app.state.admin_sessions.add(token)
    return token


def clear_admin_session(request: Request) -> None:
    token = request.cookies.get(WEB_ADMIN_COOKIE_NAME, "").strip()
    if token:
        request.app.state.admin_sessions.discard(token)


def admin_state_from_request(request: Request) -> AdminPageState:
    selected_row_id = request.query_params.get("row", "").strip() or None
    panel = request.query_params.get("panel", "").strip() or None
    if panel not in {"details", "edit", "delete"}:
        panel = None
    show_add_form = request.query_params.get("add", "").strip() == "1"
    if selected_row_id is None:
        panel = None
    return AdminPageState(
        selected_row_id=selected_row_id,
        panel=panel,
        show_add_form=show_add_form,
    )


def build_admin_rows(
    subscriptions: list[ActivatedSubscription],
    *,
    locale: str,
) -> list[AdminSubscriptionRow]:
    now = datetime.now(timezone.utc).date()
    rows: list[AdminSubscriptionRow] = []
    for subscription in subscriptions:
        expires_on = subscription.key.expires_at.astimezone(timezone.utc).date()
        days_left = max(0, (expires_on - now).days)
        rows.append(
            AdminSubscriptionRow(
                row_id=build_admin_row_id(subscription.activation, subscription.key),
                display_id=render_admin_id(locale, subscription.activation),
                activation=subscription.activation,
                key=subscription.key,
                account=subscription.account,
                days_left=days_left,
            )
        )
    return rows


def build_admin_row_id(activation: UserKeyActivation, key: SubscriptionKey) -> str:
    return f"{activation.requester_id}|{key.code}"


def render_admin_id(locale: str, activation: UserKeyActivation) -> str:
    if activation.requester_id.startswith("tg:") and activation.user_id > 0:
        return str(activation.user_id)
    if activation.requester_id.startswith("web:"):
        return f'{web_text(locale, "admin_id_web")}:{activation.requester_id[4:]}'
    return activation.requester_id


def format_admin_datetime(value: datetime, *, include_time: bool = True) -> str:
    normalized = value.astimezone(timezone.utc)
    if include_time:
        return normalized.strftime("%d.%m.%Y %H:%M")
    return normalized.strftime("%d.%m.%Y")


def extract_account_form_values(payload: dict[str, str]) -> dict[str, str]:
    return {
        "login_email": payload.get("login_email", "").strip(),
        "login_password": payload.get("login_password", ""),
        "recovery_email": payload.get("recovery_email", "").strip(),
        "recovery_password": payload.get("recovery_password", ""),
        "refresh_token": payload.get("refresh_token", ""),
        "client_id": payload.get("client_id", ""),
    }


def account_to_form_values(account: EmailAccount) -> dict[str, str]:
    return {
        "login_email": account.login_email,
        "login_password": account.login_password,
        "recovery_email": account.recovery_email,
        "recovery_password": account.recovery_password,
        "refresh_token": account.refresh_token,
        "client_id": account.client_id,
    }


def build_account_from_form_values(values: dict[str, str]) -> EmailAccount:
    login_email = normalize_email(values["login_email"])
    recovery_email = normalize_email(values["recovery_email"])
    login_password = values["login_password"].strip()
    recovery_password = values["recovery_password"].strip()
    refresh_token = values["refresh_token"].strip()
    client_id = values["client_id"].strip()

    if (
        not login_email
        or not recovery_email
        or not login_password
        or not recovery_password
        or not refresh_token
        or not client_id
    ):
        raise ValueError("All account fields are required")

    raw = ":".join(
        [
            login_email,
            login_password,
            recovery_email,
            recovery_password,
            refresh_token,
            client_id,
        ]
    )
    return EmailAccount(
        login_email=login_email,
        login_password=login_password,
        recovery_email=recovery_email,
        recovery_password=recovery_password,
        refresh_token=refresh_token,
        client_id=client_id,
        raw=raw,
    )
