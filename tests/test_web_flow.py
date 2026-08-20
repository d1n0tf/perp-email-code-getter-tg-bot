import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx

from src.config import Settings, settings
from src.email_manager import RecentCodeRecord
from src.service import BotService
from src.storage import EmailAccount, JsonStorage, LoginCodeHistoryEntry, PerplexityPromoCode, SubscriptionKey
from src.time_utils import moscow_end_of_day, to_moscow
from src.web import WEB_USER_COOKIE_NAME, build_web_path, create_web_app, render_wait_page


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
        self.assertIn("PERPLEXITY PANEL", response.text)
        self.assertIn("Activate key", response.text)
        self.assertIn("Access key", response.text)
        self.assertIn("Get bonus", response.text)
        self.assertIn("Help &amp; answers", response.text)
        self.assertIn('class="lucide lucide-gift', response.text)
        self.assertIn('id="get-bonus"', response.text)
        self.assertIn('id="bonus-claim" hidden', response.text)
        self.assertIn("/perp-code-getter/bonus", response.text)
        self.assertIn("To receive bonus days", response.text)
        self.assertIn("Promo code", response.text)
        self.assertNotIn("coming soon", response.text.lower())
        self.assertNotIn('class="secondary action-link"', response.text)
        self.assertEqual(response.text.count('href="#faq"'), 1)
        self.assertIn('id="faq"', response.text)
        self.assertIn("HELP &amp; ANSWERS", response.text)
        self.assertNotIn('class="faq-item"', response.text)
        self.assertNotIn("Where can I get a login code?", response.text)
        self.assertNotIn("Why is the code not here yet?", response.text)
        self.assertNotIn("Can I open the subscription in another browser?", response.text)
        self.assertNotIn("example@outlook.com", response.text)
        self.assertNotIn('type="email"', response.text)
        self.assertNotIn("Log out", response.text)
        self.assertNotIn("Request code", response.text)
        self.assertNotIn("Change account", response.text)
        self.assertIn("__perpLiveNavEnabled", response.text)
        self.assertIn("historyUrl: window.location.href", response.text)

    async def test_get_activate_code_shows_panel_instead_of_method_not_allowed(self) -> None:
        missing_key_page = await self.client.get(self.route("/activate-code"), params={"lang": "en"})
        self.assertEqual(missing_key_page.status_code, 200)
        self.assertIn("Activate key", missing_key_page.text)
        self.assertIn("Access key", missing_key_page.text)

        await self.activate_key(locale="en")
        active_page = await self.client.get(self.route("/activate-code"), params={"lang": "en"})
        self.assertEqual(active_page.status_code, 200)
        self.assertIn("shared@example.com", active_page.text)
        self.assertIn("Log out", active_page.text)

    async def test_activate_code_and_request_login_code_successfully(self) -> None:
        activate_response = await self.activate_key(locale="en")

        self.assertEqual(activate_response.status_code, 200)
        self.assertIn("shared@example.com", activate_response.text)
        self.assertIn("Perplexity PRO subscription", activate_response.text)
        self.assertIn("LOGIN CODES", activate_response.text)
        self.assertIn("login-code-history", activate_response.text)
        self.assertIn("Log out", activate_response.text)
        self.assertNotIn("Request code", activate_response.text)
        self.assertNotIn("Change account", activate_response.text)
        self.assertNotIn("/request-code", activate_response.text)
        self.assertNotIn("/change-account", activate_response.text)
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

    async def test_login_code_history_is_shared_by_active_key_holders(self) -> None:
        await self.activate_key(locale="en")
        await self.storage.add_login_code_history_entries(
            [
                LoginCodeHistoryEntry(
                    id="new",
                    email_address="shared@example.com",
                    code="654321",
                    received_at=datetime(2026, 8, 20, 13, 0, 13, tzinfo=timezone.utc),
                    message_key="message-id:new",
                ),
                LoginCodeHistoryEntry(
                    id="old",
                    email_address="shared@example.com",
                    code="123456",
                    received_at=datetime(2026, 8, 20, 12, 0, 13, tzinfo=timezone.utc),
                    message_key="message-id:old",
                ),
            ]
        )

        response = await self.client.get(
            self.route("/login-code-history"),
            params={"lang": "en"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "active")
        self.assertEqual([entry["code"] for entry in payload["entries"]], ["654321", "123456"])
        self.assertEqual(payload["entries"][0]["received_at"], "20.08.2026 \\ 16:00:13")

    async def test_history_endpoint_imports_scanned_codes_without_duplicates(self) -> None:
        await self.activate_key(locale="en")
        timestamp = datetime(2026, 8, 20, 13, 0, 13, tzinfo=timezone.utc)
        self.service.fetcher.scan_recent_codes = lambda *args, **kwargs: [  # type: ignore[method-assign]
            RecentCodeRecord(
                code="654321",
                timestamp=timestamp,
                folder="INBOX",
                message_identity="message-id:perplexity-login",
            )
        ]
        self.service._login_code_last_scan_at.clear()

        first = await self.client.get(self.route("/login-code-history"), params={"lang": "en"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual([entry["code"] for entry in first.json()["entries"]], ["654321"])

        self.service._login_code_last_scan_at.clear()
        second = await self.client.get(self.route("/login-code-history"), params={"lang": "en"})
        self.assertEqual(second.status_code, 200)
        self.assertEqual([entry["code"] for entry in second.json()["entries"]], ["654321"])

    async def test_login_code_history_is_not_available_after_activation_is_cleared(self) -> None:
        await self.activate_key(locale="en")
        await self.service.clear_requester_subscription_activation(
            f"web:{self.client.cookies[WEB_USER_COOKIE_NAME]}"
        )

        response = await self.client.get(
            self.route("/login-code-history"),
            params={"lang": "en"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["status"], "missing")
        self.assertEqual(response.json()["account_status"], "inactive")
        self.assertEqual(response.json()["entries"], [])

    async def test_login_code_history_reports_expiration_without_disclosing_codes(self) -> None:
        await self.activate_key(locale="en")
        await self.storage.add_login_code_history_entries(
            [
                LoginCodeHistoryEntry(
                    id="old-code",
                    email_address="shared@example.com",
                    code="654321",
                    received_at=datetime.now(timezone.utc),
                    message_key="message-id:old-code",
                )
            ]
        )
        await self.storage.add_subscription_keys(
            [
                SubscriptionKey(
                    code=self.key.code,
                    email_address=self.key.email_address,
                    duration_days=self.key.duration_days,
                    created_at=self.key.created_at,
                    expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                    access_version=self.key.access_version,
                )
            ]
        )

        response = await self.client.get(
            self.route("/login-code-history"),
            params={"lang": "en"},
        )

        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()["status"], "expired")
        self.assertEqual(response.json()["account_status"], "expired")
        self.assertEqual(response.json()["entries"], [])

    async def test_history_store_deduplicates_and_keeps_latest_hundred_codes(self) -> None:
        entries = [
            LoginCodeHistoryEntry(
                id=str(index),
                email_address="shared@example.com",
                code=f"{index:06d}",
                received_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
                message_key=f"message-id:{index}",
            )
            for index in range(101)
        ]
        await self.storage.add_login_code_history_entries(entries)
        await self.storage.add_login_code_history_entries([entries[-1]])

        saved = await self.storage.list_login_code_history("shared@example.com")
        self.assertEqual(len(saved), 100)
        self.assertEqual({entry.message_key for entry in saved}, {f"message-id:{index}" for index in range(1, 101)})

    async def test_change_account_returns_activation_form_again(self) -> None:
        await self.activate_key(locale="en")

        response = await self.client.post(
            self.route("/change-account"),
            data={"lang": "en"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Access key", response.text)
        self.assertIn("Activate key", response.text)
        self.assertNotIn("shared@example.com", response.text)

    async def test_active_page_hides_legacy_actions_and_shows_logout(self) -> None:
        english_page = await self.activate_key(locale="en")
        self.assertEqual(english_page.status_code, 200)
        self.assertIn(">Log out</button>", english_page.text)
        self.assertIn('action="/perp-code-getter/logout"', english_page.text)
        self.assertNotIn("Request code", english_page.text)
        self.assertNotIn("Change account", english_page.text)
        self.assertNotIn('action="/perp-code-getter/request-code"', english_page.text)
        self.assertNotIn('action="/perp-code-getter/change-account"', english_page.text)

        russian_page = (await self.client.get(self.route("/"), params={"lang": "ru"})).text
        self.assertIn(">Выйти</button>", russian_page)
        self.assertNotIn("Запросить код", russian_page)
        self.assertNotIn("Сменить аккаунт", russian_page)

    async def test_logout_clears_current_browser_activation_and_keeps_shared_data(self) -> None:
        other_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
        )
        try:
            await self.activate_key(locale="en")
            other_activation = await other_client.post(
                self.route("/activate-code"),
                data={"lang": "en", "code": self.key.code},
            )
            self.assertEqual(other_activation.status_code, 200)
            await self.storage.add_login_code_history_entries(
                [
                    LoginCodeHistoryEntry(
                        id="kept-code",
                        email_address="shared@example.com",
                        code="654321",
                        received_at=datetime(2026, 8, 20, 13, 0, 13, tzinfo=timezone.utc),
                        message_key="message-id:kept-code",
                    )
                ]
            )
            old_cookie = self.client.cookies[WEB_USER_COOKIE_NAME]
            other_cookie = other_client.cookies[WEB_USER_COOKIE_NAME]
            self.assertNotEqual(old_cookie, other_cookie)

            response = await self.client.post(
                self.route("/logout"),
                data={"lang": "en"},
            )

            self.assertEqual(response.status_code, 200)
            self.assertIn(
                "You have signed out of this browser. Enter an access key.",
                response.text,
            )
            self.assertIn("Access key", response.text)
            self.assertIn("Activate key", response.text)
            self.assertNotIn("shared@example.com", response.text)
            self.assertNotIn("Log out", response.text)
            new_cookie = self.client.cookies[WEB_USER_COOKIE_NAME]
            self.assertNotEqual(new_cookie, old_cookie)
            self.assertIsNone(await self.storage.get_user_activation(f"web:{old_cookie}"))
            self.assertIsNone(await self.storage.get_user_activation(f"web:{new_cookie}"))
            self.assertIsNotNone(await self.storage.get_user_activation(f"web:{other_cookie}"))
            self.assertIsNotNone(await self.storage.get_subscription_key(self.key.code))
            self.assertIsNotNone(await self.storage.get_account("shared@example.com"))
            history = await self.storage.list_login_code_history("shared@example.com")
            self.assertEqual([entry.code for entry in history], ["654321"])

            other_page = await other_client.get(self.route("/"), params={"lang": "en"})
            self.assertIn("shared@example.com", other_page.text)
            self.assertIn("Log out", other_page.text)
        finally:
            await other_client.aclose()

        await self.activate_key(locale="ru")
        russian_logout = await self.client.post(
            self.route("/logout"),
            data={"lang": "ru"},
        )
        self.assertEqual(russian_logout.status_code, 200)
        self.assertIn("Вы вышли из этого браузера. Введите ключ доступа.", russian_logout.text)
        self.assertIn("Ключ доступа", russian_logout.text)

    async def test_bonus_panel_is_hidden_until_requested(self) -> None:
        page = (await self.client.get(self.route("/"), params={"lang": "ru"})).text
        self.assertIn('id="get-bonus"', page)
        self.assertIn('id="bonus-claim" hidden', page)
        self.assertIn("/perp-code-getter/bonus", page)
        self.assertIn("Чтобы получить бонусные дни", page)
        self.assertIn('if(!bonusClaim.hidden)', page)
        self.assertIn("promo-code", page)

    async def test_bonus_promo_extends_active_key_once(self) -> None:
        await self.activate_key(locale="en")
        original = await self.storage.get_subscription_key(self.key.code)
        assert original is not None
        await self.storage.add_perplexity_promo_codes(
            [PerplexityPromoCode(code="BONUS2026", additional_days=7)]
        )

        claimed = await self.client.post(
            self.route("/bonus"),
            data={"lang": "en", "promo_code": "bonus2026"},
        )
        repeated = await self.client.post(
            self.route("/bonus"),
            data={"lang": "en", "promo_code": "BONUS2026"},
        )

        self.assertEqual(claimed.status_code, 200)
        self.assertIn(
            "Promo code #BONUS2026 was activated and your subscription was extended by 7 days.",
            claimed.text,
        )
        updated = await self.storage.get_subscription_key(self.key.code)
        assert updated is not None
        self.assertEqual(updated.duration_days, original.duration_days + 7)
        self.assertEqual(
            updated.expires_at,
            moscow_end_of_day(to_moscow(original.expires_at).date() + timedelta(days=7)),
        )
        self.assertEqual(repeated.status_code, 404)
        self.assertIn("The promo code does not exist or has already been used.", repeated.text)

    async def test_bonus_promo_requires_an_activated_key(self) -> None:
        await self.storage.add_perplexity_promo_codes(
            [PerplexityPromoCode(code="NEEDSACTIVE", additional_days=5)]
        )
        response = await self.client.post(
            self.route("/bonus"),
            data={"lang": "en", "promo_code": "NEEDSACTIVE"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Activate key", response.text)
        still_available = await self.storage.claim_perplexity_promo_code(
            "NEEDSACTIVE",
            self.key.code,
        )
        self.assertIsNotNone(still_available)

    async def test_account_update_hides_already_completed_web_request(self) -> None:
        await self.activate_key(locale="en")

        request_response = await self.client.post(
            self.route("/request-code"),
            data={"lang": "en"},
            follow_redirects=False,
        )
        self.assertEqual(request_response.status_code, 303)
        wait_url = request_response.headers["location"]
        request_id = parse_qs(urlparse(wait_url).query)["request_id"][0]

        successful_response = await self.wait_for_request_status(request_id, locale="en")
        self.assertEqual(successful_response.json()["code"], "654321")

        await self.storage.upsert_account(
            EmailAccount(
                login_email="shared@example.com",
                login_password="new-pass",
                recovery_email="recovery@example.com",
                recovery_password="recovery-pass",
                refresh_token="refresh-token",
                client_id="client-id",
                raw="shared@example.com:new-pass:recovery@example.com:recovery-pass:refresh-token:client-id",
            )
        )

        revoked_response = await self.client.get(
            self.route("/request-status"),
            params={"request_id": request_id, "lang": "en"},
        )
        self.assertEqual(revoked_response.status_code, 404)
        self.assertEqual(revoked_response.json()["status"], "missing")
        self.assertNotIn("email", revoked_response.json())
        self.assertNotIn("code", revoked_response.json())

        wait_page = await self.client.get(wait_url)
        self.assertEqual(wait_page.status_code, 404)


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

    async def test_logout_cancels_pending_web_request(self) -> None:
        await self.activate_key(locale="en")
        old_cookie = self.client.cookies[WEB_USER_COOKIE_NAME]

        request_response = await self.client.post(
            self.route("/request-code"),
            data={"lang": "en"},
            follow_redirects=False,
        )
        self.assertEqual(request_response.status_code, 303)
        request_id = parse_qs(urlparse(request_response.headers["location"]).query)[
            "request_id"
        ][0]

        await asyncio.wait_for(self.service.fetch_started.wait(), timeout=1)

        logout_response = await self.client.post(
            self.route("/logout"),
            data={"lang": "en"},
        )
        self.assertEqual(logout_response.status_code, 200)
        self.assertIn("You have signed out of this browser. Enter an access key.", logout_response.text)
        self.assertIn("Activate key", logout_response.text)
        self.assertNotEqual(self.client.cookies[WEB_USER_COOKIE_NAME], old_cookie)

        stale_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
            cookies={WEB_USER_COOKIE_NAME: old_cookie},
        )
        try:
            self.service.fetch_release.set()
            last_response: httpx.Response | None = None
            for _ in range(40):
                last_response = await stale_client.get(
                    self.route("/request-status"),
                    params={"request_id": request_id, "lang": "en"},
                )
                if last_response.status_code == 404 and last_response.json().get("status") == "missing":
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail(
                    "Timed out waiting for the previous browser cookie to lose the pending request. "
                    f"Last response: {None if last_response is None else last_response.text}"
                )
        finally:
            await stale_client.aclose()

    async def test_account_update_revokes_pending_web_request_after_fetch(self) -> None:
        await self.activate_key(locale="en")

        request_response = await self.client.post(
            self.route("/request-code"),
            data={"lang": "en"},
            follow_redirects=False,
        )
        self.assertEqual(request_response.status_code, 303)
        request_id = parse_qs(urlparse(request_response.headers["location"]).query)[
            "request_id"
        ][0]

        await asyncio.wait_for(self.service.fetch_started.wait(), timeout=1)

        # This path updates persisted credentials and invalidates activations,
        # but does not explicitly cancel a task that is already waiting for a
        # code.  The post-fetch authorization check must still suppress it.
        await self.storage.upsert_account(
            EmailAccount(
                login_email="shared@example.com",
                login_password="new-pass",
                recovery_email="recovery@example.com",
                recovery_password="recovery-pass",
                refresh_token="refresh-token",
                client_id="client-id",
                raw="shared@example.com:new-pass:recovery@example.com:recovery-pass:refresh-token:client-id",
            )
        )

        self.service.fetch_release.set()
        status_response = await self.wait_for_request_status(
            request_id,
            locale="en",
            expected_status="missing",
            expected_http_status=404,
        )

        self.assertEqual(status_response.json()["status"], "missing")

    async def test_wait_page_stops_polling_when_request_is_missing(self) -> None:
        page = render_wait_page(
            locale="en",
            base_path="/perp-code-getter",
            request_id="request-id",
            email_address="shared@example.com",
        )

        self.assertIn("let data = null;", page)
        self.assertIn('data.status === "missing"', page)
        self.assertLess(
            page.index('data.status === "missing"'),
            page.index("if (!response.ok)"),
        )
        self.assertIn("if (response.status < 500)", page)

    async def test_login_history_script_clears_stale_codes_when_access_is_revoked(self) -> None:
        await self.activate_key(locale="en")

        page = (await self.client.get(self.route("/"), params={"lang": "en"})).text

        self.assertIn("data.status==='missing'||data.status==='expired'", page)
        self.assertIn("renderHistory([]);", page)
        self.assertIn("account-inactive", page)


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
        self.assertIn("__perpLiveNavEnabled", response.text)

    async def test_admin_control_shows_available_key_without_activation(self) -> None:
        login_response = await self.login_admin(locale="ru")
        self.assertEqual(login_response.status_code, 303)

        page = await self.client.get(
            self.route("/admin_control"),
            params={"lang": "ru"},
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn(self.key.code, page.text)
        self.assertIn("shared@example.com", page.text)

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
                "duration_days": "30",
                "key_code": "NEWCUSTOMKEY123",
            },
        )
        self.assertEqual(add_response.status_code, 200)
        self.assertIn("new@example.com", add_response.text)
        self.assertIn("NEWCUSTOMKEY123", add_response.text)
        stored = await self.storage.get_account("new@example.com")
        self.assertIsNotNone(stored)
        created_key = await self.storage.get_subscription_key("NEWCUSTOMKEY123")
        self.assertIsNotNone(created_key)
        assert created_key is not None
        self.assertEqual(created_key.email_address, "new@example.com")
        self.assertEqual(created_key.duration_days, 30)

        await self.activate_key(locale="ru")
        update_response = await self.client.post(
            self.route("/admin_control/accounts/update"),
            data={
                "lang": "ru",
                "row_id": f"web:{self.client.cookies[WEB_USER_COOKIE_NAME]}|{self.key.code}",
                "original_email": "shared@example.com",
                "original_code": self.key.code,
                "raw_account": (
                    "shared-updated@example.com:new-pass:"
                    "recovery-updated@example.com:new-recovery-pass:"
                    "new-refresh-token:new-client-id"
                ),
                "key_code": "UPDATEDKEY12345",
                "duration_days": "60",
                "activated_at": "2026-02-10T14:30",
            },
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertIn("Аккаунт сохранён", update_response.text)
        self.assertIsNone(await self.storage.get_account("shared@example.com"))
        updated_account = await self.storage.get_account("shared-updated@example.com")
        self.assertIsNotNone(updated_account)
        assert updated_account is not None
        self.assertEqual(updated_account.login_password, "new-pass")
        self.assertEqual(updated_account.recovery_email, "recovery-updated@example.com")
        self.assertEqual(updated_account.recovery_password, "new-recovery-pass")
        self.assertEqual(updated_account.refresh_token, "new-refresh-token")
        self.assertEqual(updated_account.client_id, "new-client-id")
        self.assertIn("shared-updated@example.com:new-pass", updated_account.raw)
        updated_key = await self.storage.get_subscription_key("UPDATEDKEY12345")
        self.assertIsNotNone(updated_key)
        assert updated_key is not None
        self.assertEqual(updated_key.email_address, "shared-updated@example.com")
        self.assertEqual(updated_key.duration_days, 60)
        self.assertEqual(
            updated_key.created_at,
            datetime(2026, 2, 10, 11, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(
            updated_key.expires_at,
            datetime(2026, 4, 11, 20, 59, 59, 999999, tzinfo=timezone.utc),
        )
        self.assertIsNone(await self.storage.get_subscription_key(self.key.code))
        old_activation = await self.storage.get_user_activation(
            f"web:{self.client.cookies[WEB_USER_COOKIE_NAME]}",
        )
        self.assertIsNone(old_activation)

        old_cookie_page = await self.client.get(f"{self.route('/')}?lang=en")
        self.assertEqual(old_cookie_page.status_code, 200)
        self.assertIn("Activate key", old_cookie_page.text)
        self.assertNotIn("shared-updated@example.com", old_cookie_page.text)

    async def test_admin_add_replacing_account_invalidates_old_cookie_activation(self) -> None:
        await self.activate_key(locale="en")
        old_cookie = self.client.cookies[WEB_USER_COOKIE_NAME]
        login_response = await self.login_admin(locale="en")
        self.assertEqual(login_response.status_code, 303)

        response = await self.client.post(
            self.route("/admin_control/accounts/add"),
            data={
                "lang": "en",
                "raw_account": (
                    "shared@example.com:new-pass:recovery@example.com:"
                    "new-recovery-pass:new-refresh-token:new-client-id"
                ),
                "duration_days": "30",
                "key_code": "REPLACEMENTKEY1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(await self.storage.get_user_activation(f"web:{old_cookie}"))
        old_cookie_page = await self.client.get(f"{self.route('/')}?lang=en")
        self.assertIn("Activate key", old_cookie_page.text)
        self.assertNotIn("new-refresh-token", old_cookie_page.text)

    async def test_admin_control_add_form_generates_key_when_it_is_not_provided(self) -> None:
        login_response = await self.login_admin(locale="ru")
        self.assertEqual(login_response.status_code, 303)

        before_keys = await self.storage.list_subscription_keys()
        response = await self.client.post(
            self.route("/admin_control/accounts/add"),
            data={
                "lang": "ru",
                "raw_account": (
                    "autokey@example.com:auto-pass:autorecovery@example.com:"
                    "auto-recovery-pass:auto-refresh:auto-client"
                ),
                "duration_days": "45",
            },
        )
        self.assertEqual(response.status_code, 200)
        after_keys = await self.storage.list_subscription_keys()
        self.assertEqual(len(after_keys), len(before_keys) + 1)
        created_keys = [key for key in after_keys if key.code not in {item.code for item in before_keys}]
        self.assertEqual(len(created_keys), 1)
        self.assertEqual(created_keys[0].email_address, "autokey@example.com")
        self.assertEqual(created_keys[0].duration_days, 45)
        self.assertIn('action="/perp-code-getter/admin_control/accounts/add"', response.text)
        self.assertIn('name="duration_days"', response.text)
        self.assertIn('name="key_code"', response.text)
        self.assertIn(created_keys[0].code, response.text)
        self.assertIn("autokey@example.com", response.text)

    async def test_admin_control_deduplicates_rows_for_same_key(self) -> None:
        first_status, _ = await self.service.activate_requester_subscription_code(
            requester_id="web:one",
            user_id=0,
            chat_id=0,
            username="web",
            full_name="web:one",
            code=self.key.code,
        )
        second_status, _ = await self.service.activate_requester_subscription_code(
            requester_id="web:two",
            user_id=0,
            chat_id=0,
            username="web",
            full_name="web:two",
            code=self.key.code,
        )
        third_status, _ = await self.service.activate_requester_subscription_code(
            requester_id="tg:303",
            user_id=303,
            chat_id=303,
            username="user303",
            full_name="User 303",
            code=self.key.code,
        )
        self.assertEqual(first_status, "activated")
        self.assertEqual(second_status, "activated")
        self.assertEqual(third_status, "activated")

        status, extra_keys = await self.service.add_subscription_keys(
            count=1,
            duration_days=10,
            email_address="shared@example.com",
        )
        self.assertEqual(status, "created")
        self.assertIsNotNone(extra_keys)
        assert extra_keys is not None

        fourth_status, _ = await self.service.activate_requester_subscription_code(
            requester_id="web:three",
            user_id=0,
            chat_id=0,
            username="web",
            full_name="web:three",
            code=extra_keys[0].code,
        )
        self.assertEqual(fourth_status, "activated")

        subscriptions = await self.service.list_activated_subscriptions()
        self.assertEqual(
            sum(1 for item in subscriptions if item.key.code == self.key.code),
            3,
        )

        login_response = await self.login_admin(locale="en")
        self.assertEqual(login_response.status_code, 303)

        page = await self.client.get(
            self.route("/admin_control"),
            params={"lang": "en"},
        )
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.text.count(f'<td class="mono">{self.key.code}</td>'), 1)
        self.assertEqual(page.text.count(f'<td class="mono">{extra_keys[0].code}</td>'), 1)

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
        self.assertIn("связанные ключи", confirm_page.text)

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
        self.assertIsNone(await self.storage.get_subscription_key(self.key.code))
        self.assertIsNone(
            await self.storage.get_user_activation(
                f"web:{self.client.cookies[WEB_USER_COOKIE_NAME]}",
            )
        )
        self.assertNotIn("Аккаунт для этой почты не найден", delete_response.text)
        self.assertNotIn(f'<td class="mono">{self.key.code}</td>', delete_response.text)
        self.assertNotIn('<td class="mono">shared@example.com</td>', delete_response.text)

        readd_response = await self.client.post(
            self.route("/admin_control/accounts/add"),
            data={
                "lang": "ru",
                "raw_account": (
                    "shared-reused@example.com:pass-2:recovery-reused@example.com:"
                    "recovery-pass-2:refresh-reused:client-reused"
                ),
                "duration_days": "15",
                "key_code": self.key.code,
            },
        )
        self.assertEqual(readd_response.status_code, 200)
        reused_key = await self.storage.get_subscription_key(self.key.code)
        self.assertIsNotNone(reused_key)
        assert reused_key is not None
        self.assertEqual(reused_key.email_address, "shared-reused@example.com")

    async def test_admin_control_sorts_rows_by_selected_column(self) -> None:
        await self.storage.upsert_account(
            EmailAccount(
                login_email="alpha@example.com",
                login_password="pass-2",
                recovery_email="recovery-alpha@example.com",
                recovery_password="recovery-pass-2",
                refresh_token="refresh-token-2",
                client_id="client-id-2",
                raw=(
                    "alpha@example.com:pass-2:recovery-alpha@example.com:"
                    "recovery-pass-2:refresh-token-2:client-id-2"
                ),
            )
        )
        status, extra_keys = await self.service.add_subscription_keys(
            count=1,
            duration_days=10,
            email_address="alpha@example.com",
        )
        self.assertEqual(status, "created")
        self.assertIsNotNone(extra_keys)
        assert extra_keys is not None

        first_status, _ = await self.service.activate_requester_subscription_code(
            requester_id="web:one",
            user_id=0,
            chat_id=0,
            username="web",
            full_name="web:one",
            code=self.key.code,
        )
        second_status, _ = await self.service.activate_requester_subscription_code(
            requester_id="web:two",
            user_id=0,
            chat_id=0,
            username="web",
            full_name="web:two",
            code=extra_keys[0].code,
        )
        self.assertEqual(first_status, "activated")
        self.assertEqual(second_status, "activated")

        login_response = await self.login_admin(locale="en")
        self.assertEqual(login_response.status_code, 303)

        sorted_page = await self.client.get(
            self.route("/admin_control"),
            params={"lang": "en", "sort": "email", "order": "asc"},
        )
        self.assertEqual(sorted_page.status_code, 200)
        self.assertLess(
            sorted_page.text.find("alpha@example.com"),
            sorted_page.text.find("shared@example.com"),
        )
        self.assertIn("sort=email&amp;order=desc", sorted_page.text)

    async def test_admin_control_filters_rows_by_key_or_email_search(self) -> None:
        await self.storage.upsert_account(
            EmailAccount(
                login_email="alpha@example.com",
                login_password="pass-2",
                recovery_email="recovery-alpha@example.com",
                recovery_password="recovery-pass-2",
                refresh_token="refresh-token-2",
                client_id="client-id-2",
                raw=(
                    "alpha@example.com:pass-2:recovery-alpha@example.com:"
                    "recovery-pass-2:refresh-token-2:client-id-2"
                ),
            )
        )
        status, extra_keys = await self.service.add_subscription_keys(
            count=1,
            duration_days=10,
            email_address="alpha@example.com",
        )
        self.assertEqual(status, "created")
        self.assertIsNotNone(extra_keys)
        assert extra_keys is not None

        first_status, _ = await self.service.activate_requester_subscription_code(
            requester_id="web:one",
            user_id=0,
            chat_id=0,
            username="web",
            full_name="web:one",
            code=self.key.code,
        )
        second_status, _ = await self.service.activate_requester_subscription_code(
            requester_id="web:two",
            user_id=0,
            chat_id=0,
            username="web",
            full_name="web:two",
            code=extra_keys[0].code,
        )
        self.assertEqual(first_status, "activated")
        self.assertEqual(second_status, "activated")

        login_response = await self.login_admin(locale="en")
        self.assertEqual(login_response.status_code, 303)

        search_page = await self.client.get(
            self.route("/admin_control"),
            params={"lang": "en", "search": "alpha@example.com"},
        )
        self.assertEqual(search_page.status_code, 200)
        self.assertIn("alpha@example.com", search_page.text)
        self.assertNotIn("shared@example.com", search_page.text)
        self.assertIn("search=alpha%40example.com", search_page.text)

        key_search_page = await self.client.get(
            self.route("/admin_control"),
            params={"lang": "en", "search": extra_keys[0].code},
        )
        self.assertEqual(key_search_page.status_code, 200)
        self.assertIn(extra_keys[0].code, key_search_page.text)
        self.assertNotIn(self.key.code, key_search_page.text)


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
