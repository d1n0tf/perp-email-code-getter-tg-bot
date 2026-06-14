import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx

from src.config import Settings, settings
from src.service import BotService
from src.storage import EmailAccount, JsonStorage
from src.web import WEB_USER_COOKIE_NAME, build_web_path, create_web_app


class BaseWebFlowTestCase(unittest.IsolatedAsyncioTestCase):
    service_class = BotService

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
        self.service = self.service_class(
            settings=Settings(
                email_store_path=self.base_path / "email.json",
                taken_email_store_path=self.base_path / "email_taken.json",
                subscription_key_store_path=self.base_path / "keys.json",
                activated_key_store_path=self.base_path / "activated_keys.json",
                legacy_user_store_path=self.base_path / "legacy_users.json",
                user_locale_store_path=self.base_path / "user_locales.json",
                web_admin_password="secret-password",
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

        self.app = create_web_app(self.service)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.service.shutdown()
        self.temp_dir.cleanup()

    def route(self, path: str) -> str:
        return build_web_path(settings.web_base_path, path)

    async def activate_key(self, *, locale: str = "en") -> httpx.Response:
        return await self.client.post(
            self.route("/activate-code"),
            data={"lang": locale, "code": self.key.code},
        )

    async def wait_for_request_status(
        self,
        request_id: str,
        *,
        locale: str = "en",
        expected_status: str = "success",
        expected_http_status: int = 200,
    ) -> httpx.Response:
        last_response: httpx.Response | None = None
        for _ in range(40):
            response = await self.client.get(
                self.route("/request-status"),
                params={"request_id": request_id, "lang": locale},
            )
            last_response = response
            if response.status_code == expected_http_status:
                payload = response.json()
                if payload.get("status") == expected_status:
                    return response
            await asyncio.sleep(0.01)

        self.fail(
            f"Timed out waiting for request status {expected_status!r}. "
            f"Last response: {None if last_response is None else last_response.text}"
        )


class WebFlowTests(BaseWebFlowTestCase):
    service_class = None  # type: ignore[assignment]

    async def asyncSetUp(self) -> None:
        self.service_class = ImmediateWebCodeService
        await super().asyncSetUp()

    async def test_index_shows_key_activation_form_without_legacy_email_input(self) -> None:
        response = await self.client.get(f"{self.route('/')}?lang=en")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Activate code", response.text)
        self.assertIn("Seller code", response.text)
        self.assertNotIn("example@outlook.com", response.text)
        self.assertNotIn('type="email"', response.text)

    async def test_activate_code_and_request_login_code_successfully(self) -> None:
        activate_response = await self.activate_key(locale="en")

        self.assertEqual(activate_response.status_code, 200)
        self.assertIn("shared@example.com", activate_response.text)
        self.assertIn("Request code", activate_response.text)
        self.assertIn("Change account", activate_response.text)
        self.assertIn(WEB_USER_COOKIE_NAME, self.client.cookies)

        request_response = await self.client.post(
            self.route("/request-code"),
            data={"lang": "en"},
            follow_redirects=False,
        )

        self.assertEqual(request_response.status_code, 303)
        wait_url = request_response.headers["location"]
        self.assertIn("/wait?", wait_url)

        wait_page = await self.client.get(wait_url)
        self.assertEqual(wait_page.status_code, 200)
        self.assertIn("shared@example.com", wait_page.text)

        request_id = parse_qs(urlparse(wait_url).query)["request_id"][0]
        status_response = await self.wait_for_request_status(request_id, locale="en")
        payload = status_response.json()

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["email"], "shared@example.com")
        self.assertEqual(payload["code"], "654321")

    async def test_change_account_returns_activation_form_again(self) -> None:
        await self.activate_key(locale="en")

        response = await self.client.post(
            self.route("/change-account"),
            data={"lang": "en"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Enter a new seller code", response.text)
        self.assertIn("Activate code", response.text)
        self.assertNotIn("shared@example.com", response.text)


class WebFlowCancellationTests(BaseWebFlowTestCase):
    service_class = None  # type: ignore[assignment]

    async def asyncSetUp(self) -> None:
        self.service_class = SlowWebCodeService
        await super().asyncSetUp()

    async def test_change_account_cancels_pending_web_request(self) -> None:
        await self.activate_key(locale="en")

        request_response = await self.client.post(
            self.route("/request-code"),
            data={"lang": "en"},
            follow_redirects=False,
        )
        self.assertEqual(request_response.status_code, 303)
        wait_url = request_response.headers["location"]
        request_id = parse_qs(urlparse(wait_url).query)["request_id"][0]

        await asyncio.wait_for(self.service.fetch_started.wait(), timeout=1)

        change_response = await self.client.post(
            self.route("/change-account"),
            data={"lang": "en"},
        )
        self.assertEqual(change_response.status_code, 200)
        self.assertIn("Enter a new seller code", change_response.text)

        self.service.fetch_release.set()
        status_response = await self.wait_for_request_status(
            request_id,
            locale="en",
            expected_status="missing",
            expected_http_status=404,
        )

        payload = status_response.json()
        self.assertEqual(payload["status"], "missing")


class AdminControlTests(BaseWebFlowTestCase):
    service_class = None  # type: ignore[assignment]

    async def asyncSetUp(self) -> None:
        self.service_class = ImmediateWebCodeService
        await super().asyncSetUp()

    async def login_admin(self, *, locale: str = "ru") -> httpx.Response:
        return await self.client.post(
            self.route("/admin_control/login"),
            data={"lang": locale, "password": "secret-password"},
            follow_redirects=False,
        )

    async def test_admin_control_requires_password_login(self) -> None:
        response = await self.client.get(f"{self.route('/admin_control')}?lang=ru")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Вход в Admin Control", response.text)
        self.assertIn("Пароль", response.text)
        self.assertNotIn("Subscription term", response.text)

    async def test_admin_control_shows_subscription_rows_and_account_details(self) -> None:
        await self.activate_key(locale="ru")
        login_response = await self.login_admin(locale="ru")

        self.assertEqual(login_response.status_code, 303)
        row_id = f"web:{self.client.cookies[WEB_USER_COOKIE_NAME]}|{self.key.code}"
        page = await self.client.get(
            self.route("/admin_control"),
            params={"lang": "ru", "row": row_id, "panel": "details"},
        )

        self.assertEqual(page.status_code, 200)
        self.assertIn("Управление активированными подписками", page.text)
        self.assertIn(self.key.code, page.text)
        self.assertIn("shared@example.com", page.text)
        self.assertIn("refresh-token", page.text)
        self.assertIn("Изменить", page.text)
        self.assertIn("Удалить", page.text)

    async def test_admin_control_can_add_and_update_account(self) -> None:
        login_response = await self.login_admin(locale="ru")
        self.assertEqual(login_response.status_code, 303)

        add_response = await self.client.post(
            self.route("/admin_control/accounts/add"),
            data={
                "lang": "ru",
                "raw_account": (
                    "new@example.com:new-pass:recovery2@example.com:"
                    "recovery-pass-2:new-refresh:new-client"
                ),
            },
        )
        self.assertEqual(add_response.status_code, 200)
        self.assertIn("Аккаунт добавлен", add_response.text)
        stored = await self.storage.get_account("new@example.com")
        self.assertIsNotNone(stored)

        await self.activate_key(locale="ru")
        update_response = await self.client.post(
            self.route("/admin_control/accounts/update"),
            data={
                "lang": "ru",
                "row_id": f"web:{self.client.cookies[WEB_USER_COOKIE_NAME]}|{self.key.code}",
                "original_email": "shared@example.com",
                "login_email": "shared-updated@example.com",
                "login_password": "new-pass",
                "recovery_email": "recovery-updated@example.com",
                "recovery_password": "new-recovery-pass",
                "refresh_token": "new-refresh-token",
                "client_id": "new-client-id",
            },
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertIn("Аккаунт сохранён", update_response.text)
        self.assertIsNone(await self.storage.get_account("shared@example.com"))
        updated_account = await self.storage.get_account("shared-updated@example.com")
        self.assertIsNotNone(updated_account)
        updated_key = await self.storage.get_subscription_key(self.key.code)
        self.assertIsNotNone(updated_key)
        assert updated_key is not None
        self.assertEqual(updated_key.email_address, "shared-updated@example.com")

    async def test_admin_control_delete_requires_confirmation_and_removes_account(self) -> None:
        await self.activate_key(locale="ru")
        login_response = await self.login_admin(locale="ru")
        self.assertEqual(login_response.status_code, 303)

        row_id = f"web:{self.client.cookies[WEB_USER_COOKIE_NAME]}|{self.key.code}"
        confirm_page = await self.client.get(
            self.route("/admin_control"),
            params={"lang": "ru", "row": row_id, "panel": "delete"},
        )
        self.assertEqual(confirm_page.status_code, 200)
        self.assertIn("Подтвердить удаление", confirm_page.text)
        self.assertIn("Привязанные ключи останутся", confirm_page.text)

        delete_response = await self.client.post(
            self.route("/admin_control/accounts/delete"),
            data={
                "lang": "ru",
                "row_id": row_id,
                "email": "shared@example.com",
            },
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertIn("Аккаунт удалён", delete_response.text)
        self.assertIsNone(await self.storage.get_account("shared@example.com"))


class ImmediateWebCodeService(BotService):
    async def fetch_code(self, account: EmailAccount):
        return SimpleNamespace(code="654321")


class SlowWebCodeService(BotService):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fetch_started = asyncio.Event()
        self.fetch_release = asyncio.Event()

    async def fetch_code(self, account: EmailAccount):
        self.fetch_started.set()
        await self.fetch_release.wait()
        return SimpleNamespace(code="123456")


if __name__ == "__main__":
    unittest.main()
