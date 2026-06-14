import re
from contextlib import suppress

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.messages import SUPPORTED_LOCALES, translate
from src.service import ActivatedSubscription, BotService
from src.storage import SubscriptionKey, normalize_key_code


EMAIL_REGEX = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.IGNORECASE)
KEYLIST_PAGE_SIZE = 15


def build_router(service: BotService) -> Router:
    router = Router()

    async def send_start_flow(message, *, user_id: int, locale: str) -> None:
        if await service.is_legacy_user(user_id):
            await message.answer(translate(locale, "legacy_start_text"))
            await message.answer(translate(locale, "legacy_help_text"))
            return

        subscription = await service.get_activated_subscription(user_id)
        if subscription is None:
            await message.answer(translate(locale, "key_prompt"))
            return

        if subscription.key.is_expired():
            await service.clear_subscription_activation(user_id)
            await message.answer(
                translate(
                    locale,
                    "key_expired",
                    code=subscription.key.code,
                    end_date=service.format_date(subscription.key.expires_at),
                )
            )
            await message.answer(translate(locale, "key_prompt"))
            return

        if subscription.account is None:
            await message.answer(
                translate(
                    locale,
                    "key_email_missing",
                    code=subscription.key.code,
                )
            )
            await message.answer(translate(locale, "key_prompt"))
            return

        await message.answer(
            render_subscription_details(locale, user_id, subscription, service),
            reply_markup=subscription_keyboard(locale, user_id),
        )

    @router.message(CommandStart())
    async def start_handler(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return

        requested_locale = (command.args or "").strip().lower()
        has_saved_locale = await service.has_locale(message.from_user.id)
        if requested_locale in SUPPORTED_LOCALES:
            await service.set_locale(message.from_user.id, requested_locale)
            has_saved_locale = True

        locale = await service.get_locale(message.from_user.id)
        if requested_locale in SUPPORTED_LOCALES:
            await message.answer(translate(locale, "language_set"))
        elif not has_saved_locale:
            await message.answer(
                translate(locale, "choose_language"),
                reply_markup=language_keyboard(),
            )
            return

        await send_start_flow(message, user_id=message.from_user.id, locale=locale)

    @router.callback_query(F.data.startswith("lang:"))
    async def language_callback_handler(callback: CallbackQuery) -> None:
        if callback.from_user is None or callback.data is None:
            await callback.answer()
            return

        locale = callback.data.split(":", 1)[1]
        if locale not in SUPPORTED_LOCALES:
            await callback.answer()
            return

        await service.set_locale(callback.from_user.id, locale)
        if callback.message is not None:
            with suppress(Exception):
                await callback.message.edit_reply_markup(reply_markup=None)  # type: ignore[arg-type]
            await callback.message.answer(translate(locale, "language_set"))
            await send_start_flow(
                callback.message,
                user_id=callback.from_user.id,
                locale=locale,
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("refresh_ack:"))
    async def refresh_ack_handler(callback: CallbackQuery) -> None:
        if callback.from_user is None or callback.data is None:
            await callback.answer()
            return

        locale = await service.get_locale(callback.from_user.id)
        owner_id_raw = callback.data.split(":", 1)[1]
        if not owner_id_raw.isdigit():
            await callback.answer()
            return

        if callback.from_user.id != int(owner_id_raw):
            await callback.answer(
                translate(locale, "refresh_ack_denied"),
                show_alert=True,
            )
            return

        if callback.message is not None and callback.message.reply_markup is not None:  # type: ignore[truthy-bool]
            url_rows: list[list[InlineKeyboardButton]] = []
            for row in callback.message.reply_markup.inline_keyboard:  # type: ignore[union-attr]
                url_buttons = [button for button in row if button.url]
                if url_buttons:
                    url_rows.append(url_buttons)
            with suppress(Exception):
                await callback.message.edit_reply_markup(  # type: ignore[arg-type]
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=url_rows)
                    if url_rows
                    else None
                )

        await callback.answer(translate(locale, "refresh_acknowledged"))

    @router.callback_query(F.data.startswith("subscription:"))
    async def subscription_callback_handler(callback: CallbackQuery) -> None:
        if (
            callback.from_user is None
            or callback.data is None
            or callback.message is None
            or callback.bot is None
        ):
            await callback.answer()
            return

        locale = await service.get_locale(callback.from_user.id)
        _, action, owner_id_raw = callback.data.split(":", 2)
        if not owner_id_raw.isdigit():
            await callback.answer()
            return

        if callback.from_user.id != int(owner_id_raw):
            await callback.answer(
                translate(locale, "subscription_access_denied"),
                show_alert=True,
            )
            return

        if action == "change":
            await service.clear_subscription_activation(callback.from_user.id)
            with suppress(Exception):
                await callback.message.edit_reply_markup(reply_markup=None)  # type: ignore[arg-type]
            await callback.message.answer(translate(locale, "subscription_change_success"))
            await callback.answer()
            return

        if action != "request":
            await callback.answer()
            return

        subscription = await service.get_activated_subscription(callback.from_user.id)
        if subscription is None:
            await callback.message.answer(translate(locale, "subscription_inactive"))
            await callback.answer()
            return

        if subscription.key.is_expired():
            await service.clear_subscription_activation(callback.from_user.id)
            await callback.message.answer(
                translate(
                    locale,
                    "key_expired",
                    code=subscription.key.code,
                    end_date=service.format_date(subscription.key.expires_at),
                )
            )
            await callback.message.answer(translate(locale, "key_prompt"))
            await callback.answer()
            return

        if subscription.account is None:
            await callback.message.answer(
                translate(
                    locale,
                    "key_email_missing",
                    code=subscription.key.code,
                )
            )
            await callback.message.answer(translate(locale, "key_prompt"))
            await callback.answer()
            return

        status = await service.start_activated_code_request(
            bot=callback.bot,
            user_id=callback.from_user.id,
            chat_id=callback.message.chat.id,
        )
        if status == "running":
            await callback.answer(
                translate(locale, "subscription_request_running"),
                show_alert=True,
            )
            return
        if status == "inactive":
            await callback.message.answer(translate(locale, "subscription_inactive"))
            await callback.answer()
            return
        if status == "expired":
            await callback.message.answer(translate(locale, "key_prompt"))
            await callback.answer()
            return
        if status == "email_missing":
            await callback.message.answer(
                translate(locale, "key_email_missing", code=subscription.key.code)
            )
            await callback.answer()
            return

        await callback.message.answer(translate(locale, "subscription_request_started"))
        await callback.answer()

    @router.callback_query(F.data.startswith("keylist:"))
    async def keylist_page_callback_handler(callback: CallbackQuery) -> None:
        if callback.from_user is None or callback.data is None or callback.message is None:
            await callback.answer()
            return

        locale = await service.get_locale(callback.from_user.id)
        parts = callback.data.split(":", 2)
        if len(parts) != 3:
            await callback.answer()
            return

        _, owner_id_raw, page_raw = parts
        if not owner_id_raw.isdigit():
            await callback.answer()
            return

        try:
            requested_page = int(page_raw)
        except ValueError:
            await callback.answer()
            return

        if callback.from_user.id != int(owner_id_raw):
            await callback.answer(
                translate(locale, "keylist_access_denied"),
                show_alert=True,
            )
            return

        if not service.is_admin(callback.from_user.id):
            await callback.answer(translate(locale, "admin_only"), show_alert=True)
            return

        keys = await service.list_subscription_keys()
        if not keys:
            with suppress(Exception):
                await callback.message.edit_text(translate(locale, "keylist_empty"), reply_markup=None)  # type: ignore[arg-type]
            await callback.answer()
            return

        text, reply_markup = render_keylist_page(
            locale,
            callback.from_user.id,
            keys,
            requested_page,
            service,
        )
        with suppress(Exception):
            await callback.message.edit_text(text, reply_markup=reply_markup)  # type: ignore[arg-type]
        await callback.answer()

    @router.message(Command("help"))
    async def help_handler(message: Message) -> None:
        if message.from_user is None:
            return
        locale = await service.get_locale(message.from_user.id)
        if await service.is_legacy_user(message.from_user.id):
            await message.answer(translate(locale, "legacy_help_text"))
            return
        await message.answer(translate(locale, "help_text"))

    @router.message(Command("add"))
    async def add_handler(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return

        locale = await service.get_locale(message.from_user.id)
        if not service.is_admin(message.from_user.id):
            await message.answer(translate(locale, "admin_only"))
            return

        if not command.args:
            await message.answer(translate(locale, "add_usage"))
            return

        try:
            account, existed = await service.add_account(command.args)
        except ValueError:
            await message.answer(translate(locale, "add_invalid"))
            return

        key = "add_updated" if existed else "add_success"
        await message.answer(translate(locale, key, email=account.login_email))

    @router.message(Command("addkey"))
    async def addkey_handler(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return

        locale = await service.get_locale(message.from_user.id)
        if not service.is_admin(message.from_user.id):
            await message.answer(translate(locale, "admin_only"))
            return

        args = (command.args or "").split()
        if len(args) != 3:
            await message.answer(translate(locale, "addkey_usage"))
            return

        count_raw, duration_raw, email_address = args
        try:
            count = int(count_raw)
            duration_days = int(duration_raw)
        except ValueError:
            await message.answer(translate(locale, "addkey_invalid"))
            return

        if count <= 0 or duration_days <= 0:
            await message.answer(translate(locale, "addkey_invalid"))
            return

        status, keys = await service.add_subscription_keys(
            count=count,
            duration_days=duration_days,
            email_address=email_address,
        )
        if status == "email_missing" or not keys:
            await message.answer(
                translate(
                    locale,
                    "addkey_email_missing",
                    email=email_address.strip().lower(),
                )
            )
            return

        codes_text = "\n".join(f"`{key.code}`" for key in keys)
        text = translate(
            locale,
            "addkey_success",
            count=str(len(keys)),
            email=keys[0].email_address,
            duration_days=str(keys[0].duration_days),
            end_date=service.format_date(keys[0].expires_at),
            codes=codes_text,
        )
        await send_chunks(message, service, text)

    @router.message(Command("delkey"))
    async def delkey_handler(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return

        locale = await service.get_locale(message.from_user.id)
        if not service.is_admin(message.from_user.id):
            await message.answer(translate(locale, "admin_only"))
            return

        raw_code = (command.args or "").strip()
        if not raw_code:
            await message.answer(translate(locale, "delkey_usage"))
            return

        code = normalize_key_code(raw_code)
        deleted = await service.delete_subscription_key(code)
        if not deleted:
            await message.answer(translate(locale, "delkey_missing", code=code))
            return

        await message.answer(translate(locale, "delkey_success", code=code))

    @router.message(Command("keylist"))
    async def keylist_handler(message: Message) -> None:
        if message.from_user is None:
            return

        locale = await service.get_locale(message.from_user.id)
        if not service.is_admin(message.from_user.id):
            await message.answer(translate(locale, "admin_only"))
            return

        keys = await service.list_subscription_keys()
        if not keys:
            await message.answer(translate(locale, "keylist_empty"))
            return

        text, reply_markup = render_keylist_page(
            locale,
            message.from_user.id,
            keys,
            0,
            service,
        )
        await message.answer(text, reply_markup=reply_markup)

    @router.message(Command("refresh"))
    async def refresh_handler(message: Message, command: CommandObject) -> None:
        if message.from_user is None or message.bot is None:
            return

        locale = await service.get_locale(message.from_user.id)
        if not service.is_admin(message.from_user.id):
            await message.answer(translate(locale, "admin_only"))
            return

        client_id = (command.args or "").strip()
        if client_id:
            try:
                status = await service.start_refresh_token_request(
                    bot=message.bot,
                    user_id=message.from_user.id,
                    chat_id=message.chat.id,
                    client_id=client_id,
                )
            except ValueError:
                await service.begin_refresh_prompt(message.from_user.id)
                await message.answer(translate(locale, "refresh_prompt"))
                return

            if status == "running":
                await message.answer(translate(locale, "refresh_running"))
                return

            await message.answer(
                translate(locale, "refresh_started", client_id=client_id.strip())
            )
            return

        await service.begin_refresh_prompt(message.from_user.id)
        await message.answer(translate(locale, "refresh_prompt"))

    @router.message(F.text)
    async def text_handler(message: Message) -> None:
        if message.from_user is None or message.text is None:
            return

        if message.text.startswith("/") or message.bot is None:
            return

        locale = await service.get_locale(message.from_user.id)
        candidate = message.text.strip()

        if service.is_admin(message.from_user.id):
            waiting_for_client_id = await service.consume_refresh_prompt(message.from_user.id)
            if waiting_for_client_id:
                try:
                    status = await service.start_refresh_token_request(
                        bot=message.bot,
                        user_id=message.from_user.id,
                        chat_id=message.chat.id,
                        client_id=candidate,
                    )
                except ValueError:
                    await service.begin_refresh_prompt(message.from_user.id)
                    await message.answer(translate(locale, "refresh_prompt"))
                    return

                if status == "running":
                    await message.answer(translate(locale, "refresh_running"))
                    return

                await message.answer(
                    translate(locale, "refresh_started", client_id=candidate)
                )
                return

        if await service.is_legacy_user(message.from_user.id):
            if not EMAIL_REGEX.fullmatch(candidate):
                await message.answer(translate(locale, "legacy_unknown_text"))
                return

            status = await service.start_code_request(
                bot=message.bot,
                requester_id=f"tg:{message.from_user.id}",
                requester_kind="telegram",
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name,
                email_address=candidate,
            )

            if status == "missing":
                await message.answer(translate(locale, "email_missing"))
                return
            if status == "taken":
                await message.answer(translate(locale, "email_taken"))
                return

            await message.answer(
                translate(
                    locale,
                    "email_waiting",
                    email=candidate.strip().lower(),
                )
            )
            return

        subscription = await service.get_activated_subscription(message.from_user.id)
        if subscription is not None:
            if subscription.key.is_expired():
                await service.clear_subscription_activation(message.from_user.id)
                await message.answer(
                    translate(
                        locale,
                        "key_expired",
                        code=subscription.key.code,
                        end_date=service.format_date(subscription.key.expires_at),
                    )
                )
                await message.answer(translate(locale, "key_prompt"))
                return

            if subscription.account is None:
                await message.answer(
                    translate(
                        locale,
                        "key_email_missing",
                        code=subscription.key.code,
                    )
                )
            else:
                await message.answer(
                    translate(locale, "subscription_already_active"),
                    reply_markup=subscription_keyboard(locale, message.from_user.id),
                )
                await message.answer(
                    render_subscription_details(
                        locale,
                        message.from_user.id,
                        subscription,
                        service,
                    )
                )
                return

        status, payload = await service.activate_subscription_code(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            code=candidate,
        )

        if status == "missing":
            await message.answer(
                translate(
                    locale,
                    "key_invalid",
                    code=normalize_key_code(candidate),
                )
            )
            return

        if status == "expired" and isinstance(payload, SubscriptionKey):
            await message.answer(
                translate(
                    locale,
                    "key_expired",
                    code=payload.code,
                    end_date=service.format_date(payload.expires_at),
                )
            )
            await message.answer(translate(locale, "key_prompt"))
            return

        if status == "email_missing" and isinstance(payload, SubscriptionKey):
            await message.answer(
                translate(
                    locale,
                    "key_email_missing",
                    code=payload.code,
                )
            )
            return

        if status == "activated" and isinstance(payload, ActivatedSubscription):
            await message.answer(
                render_subscription_details(
                    locale,
                    message.from_user.id,
                    payload,
                    service,
                ),
                reply_markup=subscription_keyboard(locale, message.from_user.id),
            )
            return

        await message.answer(translate(locale, "key_prompt"))

    return router


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Русский", callback_data="lang:ru"),
                InlineKeyboardButton(text="English", callback_data="lang:en"),
            ]
        ]
    )


def subscription_keyboard(locale: str, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=translate(locale, "subscription_request_button"),
                    callback_data=f"subscription:request:{user_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=translate(locale, "subscription_change_button"),
                    callback_data=f"subscription:change:{user_id}",
                )
            ],
        ]
    )


def render_subscription_details(
    locale: str,
    user_id: int,
    subscription: ActivatedSubscription,
    service: BotService,
) -> str:
    return translate(
        locale,
        "subscription_details",
        user_id=str(user_id),
        email=subscription.key.email_address,
        duration_days=str(subscription.key.duration_days),
        end_date=service.format_date(subscription.key.expires_at),
        code=subscription.key.code,
    )


def render_keylist_page(
    locale: str,
    user_id: int,
    keys: list[SubscriptionKey],
    page: int,
    service: BotService,
) -> tuple[str, InlineKeyboardMarkup | None]:
    current_page, total_pages = normalize_keylist_page(page, len(keys))
    start_index = current_page * KEYLIST_PAGE_SIZE
    page_keys = keys[start_index : start_index + KEYLIST_PAGE_SIZE]
    rows = "\n".join(format_keylist_row(service, key) for key in page_keys)
    text = translate(
        locale,
        "keylist_page_header",
        total_count=str(len(keys)),
        rows=rows,
    )
    return text, keylist_pagination_keyboard(locale, user_id, current_page, total_pages)


def format_keylist_row(service: BotService, key: SubscriptionKey) -> str:
    return (
        f"{key.code} | {key.email_address} | "
        f"{service.format_date(key.created_at)} - {service.format_date(key.expires_at)}"
    )


def keylist_pagination_keyboard(
    locale: str,
    user_id: int,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup | None:
    if total_pages <= 1:
        return None

    buttons: list[InlineKeyboardButton] = []
    if page > 0:
        buttons.append(
            InlineKeyboardButton(
                text=translate(locale, "keylist_prev_button"),
                callback_data=f"keylist:{user_id}:{page - 1}",
            )
        )

    buttons.append(
        InlineKeyboardButton(
            text=translate(
                locale,
                "keylist_page_button",
                page=str(page + 1),
                total_pages=str(total_pages),
            ),
            callback_data=f"keylist:{user_id}:{page}",
        )
    )

    if page + 1 < total_pages:
        buttons.append(
            InlineKeyboardButton(
                text=translate(locale, "keylist_next_button"),
                callback_data=f"keylist:{user_id}:{page + 1}",
            )
        )
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def normalize_keylist_page(page: int, total_items: int) -> tuple[int, int]:
    total_pages = max(1, (total_items + KEYLIST_PAGE_SIZE - 1) // KEYLIST_PAGE_SIZE)
    current_page = min(max(page, 0), total_pages - 1)
    return current_page, total_pages


async def send_chunks(message: Message, service: BotService, text: str) -> None:
    for chunk in service.split_message(text):
        await message.answer(chunk)
