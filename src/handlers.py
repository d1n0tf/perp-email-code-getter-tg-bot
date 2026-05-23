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
from src.service import BotService


EMAIL_REGEX = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.IGNORECASE)


def build_router(service: BotService) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def start_handler(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return

        requested_locale = (command.args or "").strip().lower()
        if requested_locale in SUPPORTED_LOCALES:
            await service.set_locale(message.from_user.id, requested_locale)
            await message.answer(translate(requested_locale, "language_set"))
            await message.answer(translate(requested_locale, "start_text"))
            await message.answer(translate(requested_locale, "help_text"))
            return

        locale = await service.get_locale(message.from_user.id)
        await message.answer(
            translate(locale, "choose_language"),
            reply_markup=language_keyboard(),
        )

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
                await callback.message.edit_reply_markup(reply_markup=None)  # type: ignore
            await callback.message.answer(translate(locale, "language_set"))
            await callback.message.answer(translate(locale, "help_text"))
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

        if callback.message is not None and callback.message.reply_markup is not None:  # type: ignore
            url_rows: list[list[InlineKeyboardButton]] = []
            for row in callback.message.reply_markup.inline_keyboard:  # type: ignore
                url_buttons = [button for button in row if button.url]
                if url_buttons:
                    url_rows.append(url_buttons)
            with suppress(Exception):
                await callback.message.edit_reply_markup(  # type: ignore
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=url_rows)
                    if url_rows
                    else None
                )

        await callback.answer(translate(locale, "refresh_acknowledged"))

    @router.message(Command("help"))
    async def help_handler(message: Message) -> None:
        if message.from_user is None:
            return
        locale = await service.get_locale(message.from_user.id)
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
    async def email_handler(message: Message) -> None:
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

        if not EMAIL_REGEX.fullmatch(candidate):
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
        if status == "started":
            await message.answer(
                translate(
                    locale,
                    "email_waiting",
                    email=candidate.strip().lower(),
                )
            )
            return

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
