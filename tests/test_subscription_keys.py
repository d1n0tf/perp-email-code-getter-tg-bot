import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiogram.filters import CommandObject
from aiogram.types import InlineKeyboardMarkup

from src.handlers import KEYLIST_PAGE_SIZE, build_router
from src.messages import translate
from src.config import Settings
from src.service import BotService
from src.storage import EmailAccount, JsonStorage


class SubscriptionKeyReuseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.storage = JsonStorage(
            email_store_path=self.base_path / "email.json",
            taken_email_store_path=self.base_path / "email_taken.json",
            subscription_key_store_path=self.base_path / "keys.json",
            activated_key_store_path=self.base_path / "activated_keys.json",
            legacy_user_store_path=self.base_path / "legacy_users.json",
            user_locale_store_path=self.base_path / "user_locales.json",
        )
        self.service = BotService(
            settings=Settings(
                email_store_path=self.base_path / "email.json",
                taken_email_store_path=self.base_path / "email_taken.json",
                subscription_key_store_path=self.base_path / "keys.json",
                activated_key_store_path=self.base_path / "activated_keys.json",
                legacy_user_store_path=self.base_path / "legacy_users.json",
                user_locale_store_path=self.base_path / "user_locales.json",
                concurrent_mail_workers=1,
            ),
            storage=self.storage,
        )
        await self.storage.upsert_account(
            EmailAccount(
                login_email="shared@example.com",
                login_password="pass",
                recovery_email="recovery@example.com",
                recovery_password="recovery-pass",
                refresh_token="refresh-token",
                client_id="client-id",
                raw="shared@example.com:pass:recovery@example.com:recovery-pass:refresh-token:client-id",
            )
        )

        status, keys = await self.service.add_subscription_keys(
            count=1,
            duration_days=30,
            email_address="shared@example.com",
        )
        self.assertEqual(status, "created")
        self.assertIsNotNone(keys)
        assert keys is not None
        self.key = keys[0]

    async def asyncTearDown(self) -> None:
        await self.service.shutdown()
        self.temp_dir.cleanup()

    async def test_same_key_can_be_activated_by_multiple_users_without_being_consumed(
        self,
    ) -> None:
        for user_id, chat_id in ((101, 1001), (202, 2002), (303, 3003)):
            status, _ = await self.service.activate_subscription_code(
                user_id=user_id,
                chat_id=chat_id,
                username=f"user{user_id}",
                full_name=f"User {user_id}",
                code=self.key.code.lower(),
            )
            self.assertEqual(status, "activated")

        for user_id in (101, 202, 303):
            subscription = await self.service.get_activated_subscription(user_id)
            self.assertIsNotNone(subscription)
            assert subscription is not None
            self.assertEqual(subscription.key.code, self.key.code)
            self.assertEqual(subscription.key.email_address, "shared@example.com")

        stored_key = await self.storage.get_subscription_key(self.key.code)
        self.assertIsNotNone(stored_key)
        assert stored_key is not None
        self.assertEqual(stored_key.code, self.key.code)

        activation_data = json.loads(
            (self.base_path / "activated_keys.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(activation_data), {"tg:101", "tg:202", "tg:303"})
        for requester_id in activation_data:
            self.assertEqual(activation_data[requester_id]["code"], self.key.code)

    async def test_corrupted_key_store_does_not_clear_existing_activation(self) -> None:
        status, _ = await self.service.activate_subscription_code(
            user_id=101,
            chat_id=1001,
            username="user101",
            full_name="User 101",
            code=self.key.code,
        )
        self.assertEqual(status, "activated")

        (self.base_path / "keys.json").write_text("{", encoding="utf-8")

        subscription = await self.service.get_activated_subscription(101)
        self.assertIsNone(subscription)

        activation = await self.storage.get_user_activation("tg:101")
        self.assertIsNotNone(activation)
        assert activation is not None
        self.assertEqual(activation.code, self.key.code)

        (self.base_path / "keys.json").write_text(
            json.dumps({self.key.code: self.key.to_dict()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        restored = await self.service.get_activated_subscription(101)
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.key.code, self.key.code)

    async def test_user_can_switch_to_new_key_when_current_account_store_is_unavailable(
        self,
    ) -> None:
        second_account = EmailAccount(
            login_email="second@example.com",
            login_password="pass",
            recovery_email="recovery@example.com",
            recovery_password="recovery-pass",
            refresh_token="refresh-token",
            client_id="client-id",
            raw="second@example.com:pass:recovery@example.com:recovery-pass:refresh-token:client-id",
        )
        await self.storage.upsert_account(second_account)
        status, second_keys = await self.service.add_subscription_keys(
            count=1,
            duration_days=30,
            email_address="second@example.com",
        )
        self.assertEqual(status, "created")
        self.assertIsNotNone(second_keys)
        assert second_keys is not None
        second_key = second_keys[0]

        status, _ = await self.service.activate_subscription_code(
            user_id=101,
            chat_id=1001,
            username="user101",
            full_name="User 101",
            code=self.key.code,
        )
        self.assertEqual(status, "activated")

        (self.base_path / "email.json").write_text(
            json.dumps(
                {
                    second_account.login_email: second_account.to_dict(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        message = FakeTelegramMessage(
            user_id=101,
            text=second_key.code,
            username="user101",
            full_name="User 101",
            chat_id=1001,
        )
        text_handler = get_message_handler(self.service, "text_handler")
        await text_handler(message)

        subscription = await self.service.get_activated_subscription(101)
        self.assertIsNotNone(subscription)
        assert subscription is not None
        self.assertEqual(subscription.key.code, second_key.code)
        self.assertTrue(
            any("не найдена почта" in text.lower() for text, _ in message.answers),
        )


class StartFlowLocaleTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_shows_language_keyboard_when_locale_not_saved(self) -> None:
        service = FakeRouterService()
        message = FakeTelegramMessage(user_id=777, text="/start", chat_id=7770)
        start_handler = get_message_handler(service, "start_handler")

        await start_handler(
            message,
            CommandObject(prefix="/", command="start", args=None),
        )

        self.assertEqual(len(message.answers), 1)
        text, reply_markup = message.answers[0]
        self.assertEqual(text, translate("ru", "choose_language"))
        self.assertIsInstance(reply_markup, InlineKeyboardMarkup)
        self.assertEqual(len(reply_markup.inline_keyboard), 1)  # type: ignore
        self.assertEqual(len(reply_markup.inline_keyboard[0]), 2)  # type: ignore

    async def test_start_shows_language_keyboard_when_saved_locale_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base_path = Path(td)
            storage = JsonStorage(
                email_store_path=base_path / "email.json",
                taken_email_store_path=base_path / "email_taken.json",
                subscription_key_store_path=base_path / "keys.json",
                activated_key_store_path=base_path / "activated_keys.json",
                legacy_user_store_path=base_path / "legacy_users.json",
                user_locale_store_path=base_path / "user_locales.json",
            )
            service = BotService(
                settings=Settings(
                    email_store_path=base_path / "email.json",
                    taken_email_store_path=base_path / "email_taken.json",
                    subscription_key_store_path=base_path / "keys.json",
                    activated_key_store_path=base_path / "activated_keys.json",
                    legacy_user_store_path=base_path / "legacy_users.json",
                    user_locale_store_path=base_path / "user_locales.json",
                    concurrent_mail_workers=1,
                ),
                storage=storage,
            )
            try:
                (base_path / "user_locales.json").write_text(
                    json.dumps({"777": "de"}, ensure_ascii=False),
                    encoding="utf-8",
                )

                message = FakeTelegramMessage(user_id=777, text="/start", chat_id=7770)
                start_handler = get_message_handler(service, "start_handler")
                await start_handler(
                    message,
                    CommandObject(prefix="/", command="start", args=None),
                )

                self.assertEqual(len(message.answers), 1)
                text, reply_markup = message.answers[0]
                self.assertEqual(text, translate("ru", "choose_language"))
                self.assertIsInstance(reply_markup, InlineKeyboardMarkup)
            finally:
                await service.shutdown()


class StartFlowActivationSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_does_not_clear_activation_when_account_store_is_temporarily_unavailable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as td:
            base_path = Path(td)
            storage = JsonStorage(
                email_store_path=base_path / "email.json",
                taken_email_store_path=base_path / "email_taken.json",
                subscription_key_store_path=base_path / "keys.json",
                activated_key_store_path=base_path / "activated_keys.json",
                legacy_user_store_path=base_path / "legacy_users.json",
                user_locale_store_path=base_path / "user_locales.json",
            )
            service = BotService(
                settings=Settings(
                    email_store_path=base_path / "email.json",
                    taken_email_store_path=base_path / "email_taken.json",
                    subscription_key_store_path=base_path / "keys.json",
                    activated_key_store_path=base_path / "activated_keys.json",
                    legacy_user_store_path=base_path / "legacy_users.json",
                    user_locale_store_path=base_path / "user_locales.json",
                    concurrent_mail_workers=1,
                ),
                storage=storage,
            )
            try:
                await service.set_locale(1, "ru")
                await storage.upsert_account(
                    EmailAccount(
                        login_email="shared@example.com",
                        login_password="pass",
                        recovery_email="recovery@example.com",
                        recovery_password="recovery-pass",
                        refresh_token="refresh-token",
                        client_id="client-id",
                        raw="shared@example.com:pass:recovery@example.com:recovery-pass:refresh-token:client-id",
                    )
                )
                status, keys = await service.add_subscription_keys(
                    count=1,
                    duration_days=30,
                    email_address="shared@example.com",
                )
                self.assertEqual(status, "created")
                self.assertIsNotNone(keys)
                assert keys is not None

                status, _ = await service.activate_subscription_code(
                    user_id=1,
                    chat_id=1,
                    username="user1",
                    full_name="User 1",
                    code=keys[0].code,
                )
                self.assertEqual(status, "activated")

                (base_path / "email.json").write_text("{", encoding="utf-8")

                message = FakeTelegramMessage(
                    user_id=1,
                    text="/start",
                    chat_id=1,
                    username="user1",
                    full_name="User 1",
                )
                start_handler = get_message_handler(service, "start_handler")
                await start_handler(
                    message,
                    CommandObject(prefix="/", command="start", args=None),
                )

                activation = await storage.get_user_activation("tg:1")
                self.assertIsNotNone(activation)
                self.assertTrue(
                    any("не найдена почта" in text.lower() for text, _ in message.answers),
                )
            finally:
                await service.shutdown()


class EmptyPlaceholderStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_writes_accept_existing_empty_placeholder_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base_path = Path(td)
            storage = JsonStorage(
                email_store_path=base_path / "email.json",
                taken_email_store_path=base_path / "email_taken.json",
                subscription_key_store_path=base_path / "keys.json",
                activated_key_store_path=base_path / "activated_keys.json",
                legacy_user_store_path=base_path / "legacy_users.json",
                user_locale_store_path=base_path / "user_locales.json",
            )

            for filename in ("email.json", "email_taken.json", "user_locales.json"):
                (base_path / filename).write_text("", encoding="utf-8")

            existed = await storage.upsert_account(
                EmailAccount(
                    login_email="shared@example.com",
                    login_password="pass",
                    recovery_email="recovery@example.com",
                    recovery_password="recovery-pass",
                    refresh_token="refresh-token",
                    client_id="client-id",
                    raw="shared@example.com:pass:recovery@example.com:recovery-pass:refresh-token:client-id",
                )
            )
            self.assertFalse(existed)

            reserved = await storage.reserve_email(
                "shared@example.com",
                owner_id="tg:1",
                owner_kind="telegram",
                user_id=1,
                chat_id=10,
                username="user1",
                full_name="User 1",
            )
            self.assertTrue(reserved)

            await storage.set_locale(1, "ru")
            self.assertEqual(
                json.loads((base_path / "user_locales.json").read_text(encoding="utf-8")),
                {"1": "ru"},
            )


class SubscriptionRequestCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_switch_account_cancels_active_subscription_code_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base_path = Path(td)
            storage = JsonStorage(
                email_store_path=base_path / "email.json",
                taken_email_store_path=base_path / "email_taken.json",
                subscription_key_store_path=base_path / "keys.json",
                activated_key_store_path=base_path / "activated_keys.json",
                legacy_user_store_path=base_path / "legacy_users.json",
                user_locale_store_path=base_path / "user_locales.json",
            )
            service = SlowCodeService(
                settings=Settings(
                    email_store_path=base_path / "email.json",
                    taken_email_store_path=base_path / "email_taken.json",
                    subscription_key_store_path=base_path / "keys.json",
                    activated_key_store_path=base_path / "activated_keys.json",
                    legacy_user_store_path=base_path / "legacy_users.json",
                    user_locale_store_path=base_path / "user_locales.json",
                    concurrent_mail_workers=1,
                ),
                storage=storage,
            )
            try:
                await storage.upsert_account(
                    EmailAccount(
                        login_email="shared@example.com",
                        login_password="pass",
                        recovery_email="recovery@example.com",
                        recovery_password="recovery-pass",
                        refresh_token="refresh-token",
                        client_id="client-id",
                        raw="shared@example.com:pass:recovery@example.com:recovery-pass:refresh-token:client-id",
                    )
                )
                status, keys = await service.add_subscription_keys(
                    count=1,
                    duration_days=30,
                    email_address="shared@example.com",
                )
                self.assertEqual(status, "created")
                self.assertIsNotNone(keys)
                assert keys is not None

                status, _ = await service.activate_subscription_code(
                    user_id=1,
                    chat_id=1,
                    username="user1",
                    full_name="User 1",
                    code=keys[0].code,
                )
                self.assertEqual(status, "activated")

                bot = FakeBot()
                start_status = await service.start_activated_code_request(
                    bot=bot,  # type: ignore
                    user_id=1,
                    chat_id=1,
                )
                self.assertEqual(start_status, "started")
                await asyncio.wait_for(service.fetch_started.wait(), timeout=1)

                cleared = await service.clear_subscription_activation(1)
                self.assertTrue(cleared)

                service.fetch_release.set()
                await asyncio.sleep(0)
                await asyncio.sleep(0)

                self.assertEqual(bot.messages, [])
                activation = await storage.get_user_activation("tg:1")
                self.assertIsNone(activation)
            finally:
                await service.shutdown()


class KeylistPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.storage = JsonStorage(
            email_store_path=self.base_path / "email.json",
            taken_email_store_path=self.base_path / "email_taken.json",
            subscription_key_store_path=self.base_path / "keys.json",
            activated_key_store_path=self.base_path / "activated_keys.json",
            legacy_user_store_path=self.base_path / "legacy_users.json",
            user_locale_store_path=self.base_path / "user_locales.json",
        )
        self.service = BotService(
            settings=Settings(
                tg_admins=[1],
                email_store_path=self.base_path / "email.json",
                taken_email_store_path=self.base_path / "email_taken.json",
                subscription_key_store_path=self.base_path / "keys.json",
                activated_key_store_path=self.base_path / "activated_keys.json",
                legacy_user_store_path=self.base_path / "legacy_users.json",
                user_locale_store_path=self.base_path / "user_locales.json",
                concurrent_mail_workers=1,
            ),
            storage=self.storage,
        )
        await self.service.set_locale(1, "ru")
        await self.storage.upsert_account(
            EmailAccount(
                login_email="shared@example.com",
                login_password="pass",
                recovery_email="recovery@example.com",
                recovery_password="recovery-pass",
                refresh_token="refresh-token",
                client_id="client-id",
                raw="shared@example.com:pass:recovery@example.com:recovery-pass:refresh-token:client-id",
            )
        )
        status, keys = await self.service.add_subscription_keys(
            count=KEYLIST_PAGE_SIZE + 1,
            duration_days=30,
            email_address="shared@example.com",
        )
        self.assertEqual(status, "created")
        self.assertIsNotNone(keys)

    async def asyncTearDown(self) -> None:
        await self.service.shutdown()
        self.temp_dir.cleanup()

    async def test_keylist_handler_renders_first_page_with_total_count_and_next_button(
        self,
    ) -> None:
        message = FakeTelegramMessage(user_id=1, text="/keylist", chat_id=1001)
        keylist_handler = get_message_handler(self.service, "keylist_handler")

        await keylist_handler(message)

        self.assertEqual(len(message.answers), 1)
        text, reply_markup = message.answers[0]
        self.assertEqual(
            text.splitlines()[:3],
            [
                f"Общее количество: {KEYLIST_PAGE_SIZE + 1}",
                "",
                "КЛЮЧ | Почта | Дата начало - Дата конца",
            ],
        )

        rows = text.splitlines()[3:]
        self.assertEqual(len(rows), KEYLIST_PAGE_SIZE)
        self.assertIsInstance(reply_markup, InlineKeyboardMarkup)
        assert isinstance(reply_markup, InlineKeyboardMarkup)
        buttons = reply_markup.inline_keyboard[0]
        self.assertEqual([button.text for button in buttons], ["1/2", "Вперёд ›"])
        self.assertEqual(buttons[1].callback_data, "keylist:1:1")

    async def test_keylist_callback_renders_second_page_with_prev_button(self) -> None:
        keys = await self.service.list_subscription_keys()
        callback_message = FakeEditableMessage(chat_id=1001)
        callback = FakeCallbackQuery(
            user_id=1,
            data="keylist:1:1",
            message=callback_message,
        )
        keylist_callback_handler = get_callback_handler(
            self.service,
            "keylist_page_callback_handler",
        )

        await keylist_callback_handler(callback)

        self.assertEqual(len(callback.answers), 1)
        self.assertEqual(callback.answers[0], (None, False))
        self.assertIsNotNone(callback_message.text)
        assert callback_message.text is not None
        self.assertIn(keys[-1].code, callback_message.text)
        self.assertNotIn(keys[0].code, callback_message.text)

        self.assertIsInstance(callback_message.reply_markup, InlineKeyboardMarkup)
        assert isinstance(callback_message.reply_markup, InlineKeyboardMarkup)
        buttons = callback_message.reply_markup.inline_keyboard[0]
        self.assertEqual([button.text for button in buttons], ["‹ Назад", "2/2"])
        self.assertEqual(buttons[0].callback_data, "keylist:1:0")


class FakeTelegramMessage:
    def __init__(
        self,
        *,
        user_id: int,
        text: str,
        chat_id: int,
        username: str | None = None,
        full_name: str | None = None,
    ) -> None:
        self.from_user = SimpleNamespace(
            id=user_id,
            username=username,
            full_name=full_name,
        )
        self.chat = SimpleNamespace(id=chat_id)
        self.text = text
        self.bot = object()
        self.answers: list[tuple[str, object | None]] = []

    async def answer(self, text: str, reply_markup=None) -> None:
        self.answers.append((text, reply_markup))


class FakeEditableMessage:
    def __init__(self, *, chat_id: int) -> None:
        self.chat = SimpleNamespace(id=chat_id)
        self.text: str | None = None
        self.reply_markup = None

    async def edit_text(self, text: str, reply_markup=None) -> None:
        self.text = text
        self.reply_markup = reply_markup


class FakeCallbackQuery:
    def __init__(self, *, user_id: int, data: str, message: FakeEditableMessage) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.data = data
        self.message = message
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


class FakeRouterService:
    async def set_locale(self, user_id: int, locale: str) -> None:
        self.saved_locale = locale

    async def get_locale(self, user_id: int) -> str:
        return "ru"

    async def has_locale(self, user_id: int) -> bool:
        return False

    async def is_legacy_user(self, user_id: int) -> bool:
        return False

    async def get_activated_subscription(self, user_id: int):
        return None

    async def clear_subscription_activation(self, user_id: int) -> bool:
        return False

    def is_admin(self, user_id: int) -> bool:
        return False


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:
        self.messages.append((chat_id, text))


class SlowCodeService(BotService):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fetch_started = asyncio.Event()
        self.fetch_release = asyncio.Event()

    async def fetch_code(self, account: EmailAccount):
        self.fetch_started.set()
        await self.fetch_release.wait()
        return SimpleNamespace(code="123456")


def get_message_handler(service, handler_name: str):
    router = build_router(service)
    for handler in router.message.handlers:
        if handler.callback.__name__ == handler_name:
            return handler.callback
    raise AssertionError(f"Handler {handler_name} not found")


def get_callback_handler(service, handler_name: str):
    router = build_router(service)
    for handler in router.callback_query.handlers:
        if handler.callback.__name__ == handler_name:
            return handler.callback
    raise AssertionError(f"Handler {handler_name} not found")


if __name__ == "__main__":
    unittest.main()

