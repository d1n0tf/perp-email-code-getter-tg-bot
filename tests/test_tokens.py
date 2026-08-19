import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

import httpx
from fastapi import FastAPI

from src.tokens import (
    CheapVibeCodeClient,
    INSTRUCTION_ENDPOINTS,
    INSTRUCTION_GROUPS,
    INSTRUCTION_REMOVE_ENDPOINTS,
    INSTRUCTION_SYSTEMS_BY_APP,
    PromoCode,
    PromoCodeStore,
    SERVICE_OPTIONS,
    TokenKey,
    TokenKeyStore,
    TokenKeyStores,
    TokenAdmin,
    create_token_key_stores,
    create_tokens_routes,
    default_instruction_choice,
    instruction_command,
    instruction_remove_command,
    instruction_steps,
    manual_instruction_command,
    manual_instruction_note,
    manual_download_buttons,
    filter_token_admin_keys,
    paginate_token_admin_keys,
    sort_token_admin_keys,
    token_admin_page_state,
    TokenAdminPageState,
    trusted_secondary_remaining,
    utc_now,
)


class HiddenInputParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "input":
            data = dict(attrs)
            if data.get("type") == "hidden" and data.get("name") and data.get("value"):
                self.values[data["name"]] = data["value"]


class FakeKeyClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.add_token_calls: list[tuple[str, int]] = []
        self.set_active_calls: list[tuple[str, bool]] = []
        self.add_tokens_error: Exception | None = None
        self.balance_calls: list[str] = []
        self.balance_value: int = 1_234_567
        self.primary_balance_value: int = 10_000_000
        self.balance_error: Exception | None = None
        self.primary_balance_error: Exception | None = None
        self.export_log_calls: list[str] = []
        self.export_logs_error: Exception | None = None
        self.exported_log_content: bytes = b'{"entries":[]}'

    async def create_key(self, *, name: str, token_limit: int) -> str:
        self.calls.append((name, token_limit))
        return "sk-test-" + str(len(self.calls))

    async def add_tokens(self, *, api_key: str, additional_tokens: int, active: bool = True) -> None:
        if self.add_tokens_error is not None:
            raise self.add_tokens_error
        self.add_token_calls.append((api_key, additional_tokens))

    async def set_key_active(self, *, api_key: str, active: bool) -> None:
        if self.add_tokens_error is not None:
            raise self.add_tokens_error
        self.set_active_calls.append((api_key, active))

    async def get_token_balance(self, *, api_key: str) -> int:
        self.balance_calls.append(api_key)
        if self.balance_error is not None:
            raise self.balance_error
        return self.balance_value

    async def get_primary_token_balance(self) -> int:
        if self.primary_balance_error is not None:
            raise self.primary_balance_error
        return self.primary_balance_value

    async def export_logs(self, *, api_key: str):
        from src.tokens import LogExport

        if self.export_logs_error is not None:
            raise self.export_logs_error
        self.export_log_calls.append(api_key)
        return LogExport(
            content=self.exported_log_content,
            content_type="application/json",
            content_disposition='attachment; filename="key-logs.json"',
        )


class TokensRoutesTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = TokenKeyStore(Path(self.directory.name) / "token_keys.json")
        self.promo_store = PromoCodeStore(Path(self.directory.name) / "promo_codes.json")
        self.key_client = FakeKeyClient()
        self.app = FastAPI()
        create_tokens_routes(
            self.app,
            store=self.store,
            promo_store=self.promo_store,
            key_client=self.key_client,
            admin_password="secret",
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="https://testserver"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.directory.cleanup()

    async def csrf(self, path: str, cookie: str) -> tuple[dict[str, str], dict[str, str]]:
        page = await self.client.get(path)
        self.assertEqual(page.status_code, 200)
        parser = HiddenInputParser()
        parser.feed(page.text)
        token = parser.values["csrf_token"]
        return {"csrf_token": token}, {"Cookie": f"{cookie}={token}"}

    async def test_access_key_activates_and_shows_api_key(self) -> None:
        access_code = "ABCDEFGHIJKLMNOPQRST"
        await self.store.add_many([
            TokenKey(
                id=1,
                created_at=utc_now(),
                access_code=access_code,
                api_key="sk-cvc-example",
                service="Claude",
                name="Claude integration",
                token_limit=2_000_000,
            )
        ])
        form, headers = await self.csrf("/ai/tokens", "tokens_user_csrf")
        response = await self.client.post(
            "/ai/tokens",
            data={**form, "access_code": access_code},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("\u041a\u043b\u044e\u0447 \u0443\u0441\u043f\u0435\u0448\u043d\u043e \u0430\u043a\u0442\u0438\u0432\u0438\u0440\u043e\u0432\u0430\u043d", response.text)
        self.assertIn("sk-cvc-example", response.text)
        self.assertIn("2 000 000", response.text)
        activated = await self.store.get_by_code(access_code)
        self.assertIsNotNone(activated)
        self.assertIsNotNone(activated.activated_at if activated else None)

    async def test_information_block_has_bonus_placeholder_and_proxy_log_download(self) -> None:
        access_code = "ABCDEFGHIJKLMNOPQRST"
        await self.store.add_many([
            TokenKey(
                id=1,
                created_at=utc_now(),
                access_code=access_code,
                api_key="sk-cvc-log-export",
                service="Claude",
                name="Claude integration",
                token_limit=2_000_000,
                activated_at=utc_now(),
            )
        ])

        response = await self.client.get(
            "/ai/tokens",
            headers={"Cookie": f"tokens_access_key={access_code}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("id='get-bonus'", response.text)
        self.assertIn("Получить бонус", response.text)
        self.assertIn("lucide", response.text)
        self.assertNotIn("download-logs", response.text)
        self.assertNotIn("/ai/tokens/logs/export", response.text)
        self.assertNotIn("cheapvibecode", response.text.lower())

    async def test_log_export_uses_server_side_owner_client_and_streams_download(self) -> None:
        access_code = "ABCDEFGHIJKLMNOPQRST"
        await self.store.add_many([
            TokenKey(1, utc_now(), access_code, "sk-cvc-log-download", "Claude", "Logs", 100)
        ])

        response = await self.client.get(
            "/ai/tokens/logs/export",
            headers={"Cookie": f"tokens_access_key={access_code}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'{"entries":[]}')
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertEqual(response.headers["content-disposition"], 'attachment; filename="key-logs.json"')
        self.assertEqual(self.key_client.export_log_calls, ["sk-cvc-log-download"])

    async def test_log_export_never_accepts_api_key_from_browser_request(self) -> None:
        access_code = "ABCDEFGHIJKLMNOPQRST"
        await self.store.add_many([
            TokenKey(1, utc_now(), access_code, "sk-cvc-stored", "Claude", "Logs", 100)
        ])

        response = await self.client.get(
            "/ai/tokens/logs/export?api_key=sk-cvc-attacker",
            headers={"Cookie": f"tokens_access_key={access_code}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.key_client.export_log_calls, ["sk-cvc-stored"])

    async def test_bonus_promo_credits_owner_key_once(self) -> None:
        access_code = "ABCDEFGHIJKLMNOPQRST"
        await self.store.add_many([
            TokenKey(
                id=1,
                created_at=utc_now(),
                access_code=access_code,
                api_key="sk-cvc-bonus",
                service="Grok",
                name="Grok integration",
                token_limit=2_000_000,
                activated_at=utc_now(),
            )
        ])
        await self.promo_store.add(PromoCode("BONUS2026", 250_000))
        form, headers = await self.csrf("/ai/tokens", "tokens_user_csrf")
        headers["Cookie"] += f"; tokens_access_key={access_code}"

        claimed = await self.client.post(
            "/ai/tokens/bonus",
            data={**form, "lang": "ru", "promo_code": "bonus2026"},
            headers=headers,
        )
        repeated = await self.client.post(
            "/ai/tokens/bonus",
            data={**form, "lang": "ru", "promo_code": "BONUS2026"},
            headers=headers,
        )

        self.assertEqual(claimed.status_code, 200)
        self.assertIn("Промокод #BONUS2026 был активирован и вам начислено 250 000 токенов.", claimed.text)
        self.assertEqual(self.key_client.add_token_calls, [("sk-cvc-bonus", 250_000)])
        updated = await self.store.get_by_code(access_code)
        self.assertEqual(updated.token_limit if updated else None, 2_250_000)
        self.assertEqual(repeated.status_code, 404)
        self.assertIn("Промокод не существует или уже был использован ранее.", repeated.text)

    async def test_bonus_panel_is_hidden_until_requested(self) -> None:
        access_code = "ABCDEFGHIJKLMNOPQRST"
        await self.store.add_many([
            TokenKey(1, utc_now(), access_code, "sk-cvc-bonus", "Claude", "Claude integration", 100, activated_at=utc_now())
        ])

        response = await self.client.get("/ai/tokens", headers={"Cookie": f"tokens_access_key={access_code}"})

        self.assertIn("id='get-bonus'", response.text)
        self.assertIn("id='bonus-claim' hidden", response.text)
        self.assertIn("/ai/tokens/bonus", response.text)
        self.assertIn("Чтобы получить бонусные токены", response.text)

    async def test_key_holder_can_freeze_and_unfreeze_own_key(self) -> None:
        access_code = "ABCDEFGHIJKLMNOPQRST"
        await self.store.add_many([
            TokenKey(1, utc_now(), access_code, "sk-cvc-user-freeze", "Grok", "Grok key", 100, activated_at=utc_now())
        ])
        form, headers = await self.csrf("/ai/tokens", "tokens_user_csrf")
        headers["Cookie"] += f"; tokens_access_key={access_code}"

        frozen = await self.client.post(
            "/ai/tokens/freeze",
            data={**form, "lang": "ru"},
            headers=headers,
        )
        stored = await self.store.get_by_code(access_code)
        self.assertEqual(frozen.status_code, 200)
        self.assertFalse(stored.active if stored else True)
        self.assertEqual(self.key_client.set_active_calls, [("sk-cvc-user-freeze", False)])
        self.assertIn("Ключ успешно заморожен.", frozen.text)
        self.assertIn("Разморозить ключ", frozen.text)
        self.assertIn("freeze-key frozen", frozen.text)

        fresh_form, fresh_headers = await self.csrf("/ai/tokens", "tokens_user_csrf")
        fresh_headers["Cookie"] += f"; tokens_access_key={access_code}"
        unfrozen = await self.client.post(
            "/ai/tokens/freeze",
            data={**fresh_form, "lang": "ru"},
            headers=fresh_headers,
        )
        stored = await self.store.get_by_code(access_code)
        self.assertEqual(unfrozen.status_code, 200)
        self.assertTrue(stored.active if stored else False)
        self.assertEqual(self.key_client.set_active_calls, [("sk-cvc-user-freeze", False), ("sk-cvc-user-freeze", True)])
        self.assertIn("Ключ успешно разморожен.", unfrozen.text)
        self.assertIn("Заморозить ключ", unfrozen.text)

    async def test_user_freeze_failure_preserves_local_key_state(self) -> None:
        access_code = "ABCDEFGHIJKLMNOPQRST"
        await self.store.add_many([
            TokenKey(1, utc_now(), access_code, "sk-cvc-user-freeze-error", "Claude", "Key", 100)
        ])
        self.key_client.add_tokens_error = RuntimeError("upstream down")
        form, headers = await self.csrf("/ai/tokens", "tokens_user_csrf")
        headers["Cookie"] += f"; tokens_access_key={access_code}"
        response = await self.client.post(
            "/ai/tokens/freeze",
            data={**form, "lang": "ru"},
            headers=headers,
        )
        stored = await self.store.get_by_code(access_code)
        self.assertEqual(response.status_code, 502)
        self.assertTrue(stored.active if stored else False)
        self.assertIn("Не удалось изменить статус ключа", response.text)

    async def test_failed_bonus_credit_leaves_promo_available_for_retry(self) -> None:
        access_code = "ABCDEFGHIJKLMNOPQRST"
        await self.store.add_many([
            TokenKey(1, utc_now(), access_code, "sk-cvc-bonus", "Claude", "Claude integration", 100, activated_at=utc_now())
        ])
        await self.promo_store.add(PromoCode("RETRY2026", 50))
        self.key_client.add_tokens_error = RuntimeError("upstream down")
        form, headers = await self.csrf("/ai/tokens", "tokens_user_csrf")
        headers["Cookie"] += f"; tokens_access_key={access_code}"

        failed = await self.client.post(
            "/ai/tokens/bonus",
            data={**form, "lang": "ru", "promo_code": "RETRY2026"},
            headers=headers,
        )
        self.key_client.add_tokens_error = None
        refreshed_form, refreshed_headers = await self.csrf("/ai/tokens", "tokens_user_csrf")
        refreshed_headers["Cookie"] += f"; tokens_access_key={access_code}"
        retried = await self.client.post(
            "/ai/tokens/bonus",
            data={**refreshed_form, "lang": "ru", "promo_code": "RETRY2026"},
            headers=refreshed_headers,
        )

        self.assertEqual(failed.status_code, 502)
        self.assertIn("Не удалось начислить бонус", failed.text)
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(self.key_client.add_token_calls, [("sk-cvc-bonus", 50)])

    async def test_unknown_access_key_is_reported(self) -> None:
        form, headers = await self.csrf("/ai/tokens", "tokens_user_csrf")
        response = await self.client.post(
            "/ai/tokens", data={**form, "access_code": "ABCDEFGHIJKLMNOPQRST"}, headers=headers
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("\u041a\u043b\u044e\u0447 \u0434\u043e\u0441\u0442\u0443\u043f\u0430 \u043d\u0435 \u0441\u0443\u0449\u0435\u0441\u0442\u0432\u0443\u0435\u0442", response.text)

    async def test_public_page_with_legacy_store_and_no_admin_client_does_not_crash(self) -> None:
        access_code = "ABCDEFGHIJKLMNOPQRST"
        await self.store.add_many([
            TokenKey(1, utc_now(), access_code, "sk-cvc-legacy", "Claude", "Legacy", 100)
        ])
        app = FastAPI()
        create_tokens_routes(
            app,
            stores=TokenKeyStores([self.store]),
            # Mirrors a deployment with local keys but no configured owner
            # primary API client yet.
            admins=[],
            owner_key_clients=[],
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as client:
            page = await client.get("/ai/tokens", headers={"Cookie": f"tokens_access_key={access_code}"})
            balance = await client.get("/ai/tokens/balance", headers={"Cookie": f"tokens_access_key={access_code}"})

        self.assertEqual(page.status_code, 200)
        self.assertIn("sk-cvc-legacy", page.text)
        self.assertEqual(balance.status_code, 502)
        self.assertEqual(balance.json()["error"], "balance_unavailable")

    async def test_public_activation_finds_code_in_another_owner_store(self) -> None:
        other_store = TokenKeyStore(Path(self.directory.name) / "token_keys_2.json")
        other_code = "QRSTUVWXYZABCDEFGHIJ"
        await other_store.add_many([
            TokenKey(
                id=1,
                created_at=utc_now(),
                access_code=other_code,
                api_key="sk-owner-two",
                service="Grok",
                name="Owner two key",
                token_limit=2_000_000,
            )
        ])
        app = FastAPI()
        create_tokens_routes(
            app,
            stores=TokenKeyStores([self.store, other_store]),
            admins=[
                TokenAdmin("first", "pk-first"),
                TokenAdmin("second", "pk-second"),
            ],
            owner_key_clients=[self.key_client, self.key_client],
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as client:
            page = await client.get("/ai/tokens")
            parser = HiddenInputParser()
            parser.feed(page.text)
            response = await client.post(
                "/ai/tokens",
                data={"csrf_token": parser.values["csrf_token"], "access_code": other_code},
                headers={"Cookie": f"tokens_user_csrf={parser.values['csrf_token']}"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("sk-owner-two", response.text)
        self.assertIsNone(await self.store.get_by_code(other_code))
        activated = await other_store.get_by_code(other_code)
        self.assertIsNotNone(activated)
        self.assertIsNotNone(activated.activated_at if activated else None)

    async def test_admin_password_sees_only_its_own_store(self) -> None:
        other_store = TokenKeyStore(Path(self.directory.name) / "token_keys_2.json")
        await self.store.add_many([
            TokenKey(1, utc_now(), "ABCDEFGHIJKLMNOPQRST", "sk-first", "Claude", "First", 100)
        ])
        await other_store.add_many([
            TokenKey(1, utc_now(), "QRSTUVWXYZABCDEFGHIJ", "sk-second", "Grok", "Second", 100)
        ])
        app = FastAPI()
        create_tokens_routes(
            app,
            stores=TokenKeyStores([self.store, other_store]),
            admins=[
                TokenAdmin("first", "pk-first"),
                TokenAdmin("second", "pk-second"),
            ],
            owner_key_clients=[self.key_client, self.key_client],
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as client:
            login_page = await client.get("/ai/tokens/adm")
            parser = HiddenInputParser()
            parser.feed(login_page.text)
            login = await client.post(
                "/ai/tokens/adm/login",
                data={"csrf_token": parser.values["csrf_token"], "password": "second"},
                headers={"Cookie": f"tokens_admin_csrf={parser.values['csrf_token']}"},
                follow_redirects=False,
            )
            page = await client.get("/ai/tokens/adm", headers={"Cookie": login.headers["set-cookie"].split(";", 1)[0]})

        self.assertIn("sk-second", page.text)
        self.assertNotIn("sk-first", page.text)

    async def test_admin_creates_key_with_its_own_primary_client(self) -> None:
        second_client = FakeKeyClient()
        app = FastAPI()
        create_tokens_routes(
            app,
            stores=TokenKeyStores([self.store, TokenKeyStore(Path(self.directory.name) / "token_keys_2.json")]),
            admins=[
                TokenAdmin("first", "pk-first"),
                TokenAdmin("second", "pk-second"),
            ],
            owner_key_clients=[self.key_client, second_client],
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as client:
            login_page = await client.get("/ai/tokens/adm")
            parser = HiddenInputParser()
            parser.feed(login_page.text)
            login = await client.post(
                "/ai/tokens/adm/login",
                data={"csrf_token": parser.values["csrf_token"], "password": "second"},
                headers={"Cookie": f"tokens_admin_csrf={parser.values['csrf_token']}"},
                follow_redirects=False,
            )
            session_cookie = login.headers["set-cookie"].split(";", 1)[0]
            create_page = await client.get("/ai/tokens/adm", headers={"Cookie": session_cookie})
            parser = HiddenInputParser()
            parser.feed(create_page.text)
            response = await client.post(
                "/ai/tokens/adm/create",
                data={
                    "csrf_token": parser.values["csrf_token"],
                    "service": "Grok",
                    "name": "Second owner's key",
                    "token_limit": "100",
                    "quantity": "1",
                },
                headers={"Cookie": f"tokens_admin_csrf={parser.values['csrf_token']}; {session_cookie}"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.key_client.calls, [])
        self.assertEqual(second_client.calls, [("Second owner's key", 100)])

    async def test_public_page_supports_english_locale(self) -> None:
        response = await self.client.get("/ai/tokens?lang=en")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<h1>Activation Service</h1>", response.text)
        self.assertIn("/ai/tokens?lang=ru", response.text)
        self.assertIn("name='lang' value='en'", response.text)

    async def test_public_page_prefills_access_key_from_link(self) -> None:
        access_code = "abcdefghijklmnopqrst"

        response = await self.client.get(f"/ai/tokens?key={access_code}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("name='access_code' value='ABCDEFGHIJKLMNOPQRST'", response.text)
        # The language switch must retain the ready-to-activate key too.
        self.assertIn("/ai/tokens?lang=en&amp;key=ABCDEFGHIJKLMNOPQRST", response.text)

    async def test_public_page_accepts_form_field_name_in_access_key_link(self) -> None:
        response = await self.client.get("/ai/tokens?access_code=ABCDEFGHIJKLMNOPQRST")

        self.assertEqual(response.status_code, 200)
        self.assertIn("name='access_code' value='ABCDEFGHIJKLMNOPQRST'", response.text)

    async def test_balance_endpoint_uses_activated_key_api_key(self) -> None:
        access_code = "ABCDEFGHIJKLMNOPQRST"
        await self.store.add_many([
            TokenKey(
                id=1,
                created_at=utc_now(),
                access_code=access_code,
                api_key="sk-cvc-balance-test",
                service="Claude",
                name="Claude integration",
                token_limit=2_000_000,
            )
        ])

        response = await self.client.get(
            "/ai/tokens/balance",
            headers={"Cookie": f"tokens_access_key={access_code}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["token_balance"], 1_234_567)
        self.assertEqual(response.json()["formatted"], "1 234 567")
        self.assertEqual(response.json()["used_tokens"], 765_433)
        self.assertEqual(self.key_client.balance_calls, ["sk-cvc-balance-test"])
        stored = await self.store.get_by_code(access_code)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.used_tokens if stored else None, 765_433)

    async def test_balance_endpoint_keeps_saved_secondary_balance_when_upstream_returns_primary_total(self) -> None:
        access_code = "ABCDEFGHIJKLMNOPQRST"
        await self.store.add_many([
            TokenKey(
                id=1,
                created_at=utc_now(),
                access_code=access_code,
                api_key="sk-cvc-primary-total",
                service="Claude",
                name="Key",
                token_limit=2_000_000,
                used_tokens=765_433,
            )
        ])
        self.key_client.balance_value = 50_000_000
        self.key_client.primary_balance_value = 50_000_000

        response = await self.client.get(
            "/ai/tokens/balance",
            headers={"Cookie": f"tokens_access_key={access_code}"},
        )

        stored = await self.store.get_by_code(access_code)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["token_balance"], 1_234_567)
        self.assertEqual(response.json()["used_tokens"], 765_433)
        self.assertEqual(stored.used_tokens if stored else None, 765_433)

    async def test_admin_creates_secondary_access_keys(self) -> None:
        login_form, login_headers = await self.csrf("/ai/tokens/adm", "tokens_admin_csrf")
        login = await self.client.post(
            "/ai/tokens/adm/login",
            data={**login_form, "password": "secret"},
            headers=login_headers,
            follow_redirects=False,
        )
        self.assertEqual(login.status_code, 303)
        session_cookie = login.headers["set-cookie"].split(";", 1)[0]
        create_form, csrf_headers = await self.csrf("/ai/tokens/adm", "tokens_admin_csrf")
        headers = {"Cookie": csrf_headers["Cookie"] + "; " + session_cookie}
        response = await self.client.post(
            "/ai/tokens/adm/create",
            data={
                **create_form,
                "service": "Claude",
                "name": "Integration key",
                "token_limit": "1000000",
                "quantity": "2",
            },
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.key_client.calls, [("Integration key", 1_000_000)] * 2)
        keys = await self.store.list()
        self.assertEqual(len(keys), 2)
        self.assertEqual({key.api_key for key in keys}, {"sk-test-1", "sk-test-2"})
        self.assertTrue(all(len(key.access_code) == 20 and key.access_code.isalnum() for key in keys))
        self.assertIn("value='Grok'", response.text)
        for key in keys:
            self.assertIn(
                f"https://starimg.ru/ai/tokens?key={key.access_code}",
                response.text,
            )
        self.assertNotIn(
            "data-created-codes='" + "\n".join(key.access_code for key in keys) + "'",
            response.text,
        )

    async def test_admin_freezes_and_unfreezes_key_with_its_owner_client(self) -> None:
        await self.store.add_many([
            TokenKey(1, utc_now(), "ABCDEFGHIJKLMNOPQRST", "sk-cvc-freeze", "Grok", "Frozen", 100)
        ])
        login_form, login_headers = await self.csrf("/ai/tokens/adm", "tokens_admin_csrf")
        login = await self.client.post(
            "/ai/tokens/adm/login",
            data={**login_form, "password": "secret"},
            headers=login_headers,
            follow_redirects=False,
        )
        session_cookie = login.headers["set-cookie"].split(";", 1)[0]
        page_form, csrf_headers = await self.csrf("/ai/tokens/adm", "tokens_admin_csrf")
        headers = {"Cookie": csrf_headers["Cookie"] + "; " + session_cookie}

        frozen = await self.client.post(
            "/ai/tokens/adm/1/freeze",
            data=page_form,
            headers=headers,
        )
        stored = await self.store.get(1)
        self.assertEqual(frozen.status_code, 200)
        self.assertFalse(stored.active if stored else True)
        self.assertEqual(self.key_client.set_active_calls, [("sk-cvc-freeze", False)])
        self.assertIn("Разморозить", frozen.text)
        self.assertIn("freeze-key frozen", frozen.text)

        refreshed_form, refreshed_headers = await self.csrf("/ai/tokens/adm", "tokens_admin_csrf")
        unfrozen = await self.client.post(
            "/ai/tokens/adm/1/freeze",
            data=refreshed_form,
            headers={"Cookie": refreshed_headers["Cookie"] + "; " + session_cookie},
        )
        stored = await self.store.get(1)
        self.assertEqual(unfrozen.status_code, 200)
        self.assertTrue(stored.active if stored else False)
        self.assertEqual(self.key_client.set_active_calls, [("sk-cvc-freeze", False), ("sk-cvc-freeze", True)])
        self.assertIn("Заморозить", unfrozen.text)

    async def test_admin_does_not_change_local_state_when_upstream_freeze_fails(self) -> None:
        await self.store.add_many([
            TokenKey(1, utc_now(), "ABCDEFGHIJKLMNOPQRST", "sk-cvc-freeze-error", "Claude", "Freeze", 100)
        ])
        self.key_client.add_tokens_error = RuntimeError("upstream down")
        login_form, login_headers = await self.csrf("/ai/tokens/adm", "tokens_admin_csrf")
        login = await self.client.post(
            "/ai/tokens/adm/login",
            data={**login_form, "password": "secret"},
            headers=login_headers,
            follow_redirects=False,
        )
        page_form, csrf_headers = await self.csrf("/ai/tokens/adm", "tokens_admin_csrf")
        response = await self.client.post(
            "/ai/tokens/adm/1/freeze",
            data=page_form,
            headers={"Cookie": csrf_headers["Cookie"] + "; " + login.headers["set-cookie"].split(";", 1)[0]},
        )
        stored = await self.store.get(1)
        self.assertEqual(response.status_code, 502)
        self.assertTrue(stored.active if stored else False)
        self.assertIn("Не удалось заморозить ключ #1.", response.text)

    async def test_key_state_request_does_not_send_a_token_delta(self) -> None:
        client = CheapVibeCodeClient("primary", "https://example.test")
        captured: dict[str, object] = {}

        class Response:
            status = 200

            async def text(self) -> str:
                return "{}"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args) -> None:
                return None

        class Session:
            def __init__(self, **kwargs) -> None:
                pass

            def post(self, url: str, **kwargs):
                captured["url"] = url
                captured.update(kwargs)
                return Response()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args) -> None:
                return None

        from unittest.mock import patch
        with patch("src.tokens.aiohttp.ClientSession", Session):
            await client.set_key_active(api_key="sk-cvc-example", active=False)

        self.assertEqual(captured["url"], "https://example.test/v1/keys/edit")
        self.assertEqual(captured["json"], {"key": "sk-cvc-example", "active": False})

    async def test_admin_creates_and_lists_read_only_owner_promo_codes(self) -> None:
        other_store = TokenKeyStore(Path(self.directory.name) / "token_keys_2.json")
        app = FastAPI()
        create_tokens_routes(
            app,
            stores=TokenKeyStores([self.store, other_store]),
            promo_store=self.promo_store,
            admins=[
                TokenAdmin("first", "pk-first"),
                TokenAdmin("second", "pk-second"),
            ],
            owner_key_clients=[self.key_client, FakeKeyClient()],
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as client:
            login_page = await client.get("/ai/tokens/adm")
            parser = HiddenInputParser()
            parser.feed(login_page.text)
            login = await client.post(
                "/ai/tokens/adm/login",
                data={"csrf_token": parser.values["csrf_token"], "password": "second"},
                headers={"Cookie": f"tokens_admin_csrf={parser.values['csrf_token']}"},
                follow_redirects=False,
            )
            session_cookie = login.headers["set-cookie"].split(";", 1)[0]
            admin_page = await client.get("/ai/tokens/adm", headers={"Cookie": session_cookie})
            parser = HiddenInputParser()
            parser.feed(admin_page.text)
            response = await client.post(
                "/ai/tokens/adm/promos/create",
                data={
                    "csrf_token": parser.values["csrf_token"],
                    "additional_tokens": "250000",
                    "quantity": "2",
                },
                headers={"Cookie": f"tokens_admin_csrf={parser.values['csrf_token']}; {session_cookie}"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("СОЗДАНИЯ ПРОМОКОДОВ", response.text)
        self.assertIn("УПРАВЛЕНИЕ ПРОМОКОДАМИ", response.text)
        self.assertIn("Создано промокодов: 2.", response.text)
        self.assertIn("Скопировать все", response.text)
        self.assertNotIn("Управлять промокод", response.text)
        promos = await self.promo_store.list_for_owner(2)
        self.assertEqual(len(promos), 2)
        self.assertTrue(all(promo.additional_tokens == 250_000 for promo in promos))
        self.assertTrue(all(len(promo.code) == 20 and promo.code.isalnum() for promo in promos))
        self.assertEqual(await self.promo_store.list_for_owner(1), [])

    async def test_authenticated_admin_page_uses_wide_desktop_layout(self) -> None:
        login_form, login_headers = await self.csrf("/ai/tokens/adm", "tokens_admin_csrf")
        login = await self.client.post(
            "/ai/tokens/adm/login",
            data={**login_form, "password": "secret"},
            headers=login_headers,
            follow_redirects=False,
        )
        response = await self.client.get(
            "/ai/tokens/adm",
            headers={"Cookie": login.headers["set-cookie"].split(";", 1)[0]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(".admin-page { width:calc(100% - 48px); max-width:1800px; }", response.text)
        self.assertIn("data-tokens-admin-refresh", response.text)
        self.assertIn("fetch(url, Object.assign", response.text)
        self.assertIn("adminPage.replaceWith(replacement)", response.text)
        self.assertIn(".create-form,.keys-search-form", response.text)
        self.assertIn("refreshAdmin(link.href", response.text)

    async def test_admin_search_filters_access_code_and_api_key_before_pagination(self) -> None:
        records = [
            TokenKey(
                id=index,
                created_at=utc_now(),
                access_code=f"ACCESS{index:014d}",
                api_key=f"sk-cvc-api-{index:03d}",
                service="Claude",
                name=f"Key {index}",
                token_limit=100,
            )
            for index in range(1, 152)
        ]
        await self.store.add_many(records)
        login_form, login_headers = await self.csrf("/ai/tokens/adm", "tokens_admin_csrf")
        login = await self.client.post(
            "/ai/tokens/adm/login",
            data={**login_form, "password": "secret"},
            headers=login_headers,
            follow_redirects=False,
        )
        session_cookie = login.headers["set-cookie"].split(";", 1)[0]

        access_response = await self.client.get(
            "/ai/tokens/adm?search=ACCESS000000000001&sort=id&order=asc",
            headers={"Cookie": session_cookie},
        )
        api_response = await self.client.get(
            "/ai/tokens/adm?search=API-151&sort=id&order=asc",
            headers={"Cookie": session_cookie},
        )

        self.assertEqual(access_response.status_code, 200)
        self.assertIn("ACCESS000000000001", access_response.text)
        self.assertNotIn("ACCESS000000000002", access_response.text)
        self.assertEqual(api_response.status_code, 200)
        self.assertIn("ACCESS00000000000151", api_response.text)
        self.assertNotIn("ACCESS00000000000150", api_response.text)

    async def test_admin_sorts_all_keys_before_selecting_page(self) -> None:
        records = [
            TokenKey(
                id=index,
                created_at=utc_now(),
                access_code=f"CODE{index:016d}",
                api_key=f"sk-cvc-{index:03d}",
                service="Claude",
                name=f"Key {index}",
                token_limit=100,
            )
            for index in range(1, 102)
        ]
        await self.store.add_many(records)
        login_form, login_headers = await self.csrf("/ai/tokens/adm", "tokens_admin_csrf")
        login = await self.client.post(
            "/ai/tokens/adm/login",
            data={**login_form, "password": "secret"},
            headers=login_headers,
            follow_redirects=False,
        )
        session_cookie = login.headers["set-cookie"].split(";", 1)[0]
        page = await self.client.get(
            "/ai/tokens/adm?page=2&sort=id&order=asc",
            headers={"Cookie": session_cookie},
        )

        self.assertEqual(page.status_code, 200)
        self.assertIn("CODE0000000000000101", page.text)
        self.assertNotIn("CODE0000000000000100", page.text)
        self.assertIn("Показаны 101–101 из 101 ключей", page.text)

    async def test_admin_page_clamps_out_of_range_page_and_preserves_controls(self) -> None:
        await self.store.add_many([
            TokenKey(1, utc_now(), "ABCDEFGHIJKLMNOPQRST", "sk-cvc-target", "Grok", "Target", 100)
        ])
        login_form, login_headers = await self.csrf("/ai/tokens/adm", "tokens_admin_csrf")
        login = await self.client.post(
            "/ai/tokens/adm/login",
            data={**login_form, "password": "secret"},
            headers=login_headers,
            follow_redirects=False,
        )
        response = await self.client.get(
            "/ai/tokens/adm?page=999&search=target&sort=api_key&order=asc",
            headers={"Cookie": login.headers["set-cookie"].split(";", 1)[0]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("name='search' value='target'", response.text)
        self.assertIn("name='sort' value='api_key'", response.text)
        self.assertIn("name='order' value='asc'", response.text)
        self.assertIn("/ai/tokens/adm?page=1&amp;sort=api_key&amp;order=desc&amp;search=target", response.text)

    async def test_page_refreshes_remaining_tokens_from_balance_api(self) -> None:
        access_code = "ABCDEFGHIJKLMNOPQRST"
        await self.store.add_many([
            TokenKey(
                id=1,
                created_at=utc_now(),
                access_code=access_code,
                api_key="sk-cvc-live",
                service="Grok",
                name="Grok key",
                token_limit=2_000_000,
                used_tokens=0,
                activated_at=utc_now(),
            )
        ])

        response = await self.client.get(
            "/ai/tokens",
            headers={"Cookie": f"tokens_access_key={access_code}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("1 234 567 из 2 000 000", response.text)
        self.assertIn("GROK", response.text)
        stored = await self.store.get_by_code(access_code)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.used_tokens if stored else None, 765_433)
        self.assertEqual(self.key_client.balance_calls, ["sk-cvc-live"])

    async def test_balance_failure_keeps_stored_used_tokens(self) -> None:
        access_code = "ABCDEFGHIJKLMNOPQRST"
        await self.store.add_many([
            TokenKey(
                id=1,
                created_at=utc_now(),
                access_code=access_code,
                api_key="sk-cvc-stale",
                service="Claude",
                name="Claude key",
                token_limit=2_000_000,
                used_tokens=10,
                activated_at=utc_now(),
            )
        ])
        self.key_client.balance_error = RuntimeError("down")

        response = await self.client.get(
            "/ai/tokens",
            headers={"Cookie": f"tokens_access_key={access_code}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("1 999 990 из 2 000 000", response.text)
        stored = await self.store.get_by_code(access_code)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.used_tokens if stored else None, 10)

    async def test_exhausted_key_keeps_setup_instructions_visible(self) -> None:
        access_code = "ZZZZZZZZZZZZZZZZZZZZ"
        await self.store.add_many([
            TokenKey(
                id=99,
                created_at=utc_now(),
                access_code=access_code,
                api_key="sk-cvc-exhausted",
                service="Grok",
                name="Exhausted Grok key",
                token_limit=100,
                used_tokens=100,
                activated_at=utc_now(),
                exhausted_at=utc_now(),
            )
        ])
        response = await self.client.get("/ai/tokens", headers={"Cookie": f"tokens_access_key={access_code}"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("class='card instructions'", response.text)
        self.assertIn("data-default-app='Grok Build'", response.text)
        self.assertIn("data-provider-app='Grok Build'", response.text)

    async def test_grok_instructions_use_html_scripts_and_top_level_service(self) -> None:
        access_code = "ABCDEFGHIJKLMNOPQRST"
        await self.store.add_many([
            TokenKey(
                id=1,
                created_at=utc_now(),
                access_code=access_code,
                api_key="sk-cvc-grok",
                service="Grok",
                name="Grok key",
                token_limit=2_000_000,
                activated_at=utc_now(),
            )
        ])

        response = await self.client.get(
            "/ai/tokens",
            headers={"Cookie": f"tokens_access_key={access_code}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("data-instruction-provider='grok'", response.text)
        self.assertIn("data-default-provider='grok'", response.text)
        self.assertIn("data-default-app='Grok Build'", response.text)
        self.assertIn("Grok Build", response.text)
        self.assertIn("Открой PowerShell.", response.text)
        self.assertIn("Перезапусти терминал и введи grok.", response.text)
        self.assertIn("https://starimg.ru/ai/common/igw", response.text)
        self.assertIn("https://starimg.ru/ai/common/rgw", response.text)
        self.assertIn("https://starimg.ru/ai/common/igm", response.text)
        self.assertIn("$env:CVC_API_KEY=", response.text)
        self.assertIn("sk-cvc-grok", response.text)
        self.assertIn("data-instruction-app='Claude Code CLI'", response.text)
        self.assertIn("data-instruction-mode='script'", response.text)
        self.assertIn("data-instruction-mode='manual'", response.text)
        self.assertIn('Чем отличаются Codex, OpenAI и Anthropic endpoint&#x27;ы?', response.text)
        self.assertNotIn('Платёж создан', response.text)
        self.assertIn("Удалить интеграцию", response.text)
        self.assertIn("Ответы на вопросы и ошибки", response.text)

    async def test_admin_table_refreshes_used_tokens_from_balance_api(self) -> None:
        await self.store.add_many([
            TokenKey(
                id=7,
                created_at=utc_now(),
                access_code="ABCDEFGHIJKLMNOPQRST",
                api_key="sk-cvc-admin",
                service="Grok",
                name="Grok admin",
                token_limit=2_000_000,
                used_tokens=0,
            )
        ])
        login_form, login_headers = await self.csrf("/ai/tokens/adm", "tokens_admin_csrf")
        login = await self.client.post(
            "/ai/tokens/adm/login",
            data={**login_form, "password": "secret"},
            headers=login_headers,
            follow_redirects=False,
        )
        session_cookie = login.headers["set-cookie"].split(";", 1)[0]
        response = await self.client.get("/ai/tokens/adm", headers={"Cookie": session_cookie})

        self.assertEqual(response.status_code, 200)
        self.assertIn("Grok", response.text)
        self.assertIn("765 433", response.text)
        self.assertEqual(self.key_client.balance_calls, ["sk-cvc-admin"])
        stored = await self.store.get(7)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.used_tokens if stored else None, 765_433)

    async def test_admin_does_not_treat_primary_balance_as_secondary_usage(self) -> None:
        await self.store.add_many([
            TokenKey(
                id=8,
                created_at=utc_now(),
                access_code="ABCDEFGHIJKLMNOPQRST",
                api_key="sk-cvc-capped",
                service="Grok",
                name="Grok capped",
                token_limit=2_000_000,
                used_tokens=42_000,
            )
        ])
        self.key_client.balance_value = 50_000_000
        self.key_client.primary_balance_value = 50_000_000
        login_form, login_headers = await self.csrf("/ai/tokens/adm", "tokens_admin_csrf")
        login = await self.client.post(
            "/ai/tokens/adm/login",
            data={**login_form, "password": "secret"},
            headers=login_headers,
            follow_redirects=False,
        )
        session_cookie = login.headers["set-cookie"].split(";", 1)[0]
        response = await self.client.get("/ai/tokens/adm", headers={"Cookie": session_cookie})

        self.assertEqual(response.status_code, 200)
        self.assertIn("42 000", response.text)
        stored = await self.store.get(8)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.used_tokens if stored else None, 42_000)

    async def test_admin_uses_secondary_remaining_when_below_primary(self) -> None:
        await self.store.add_many([
            TokenKey(
                id=9,
                created_at=utc_now(),
                access_code="ZYXWVUTSRQPONMLKJIHG",
                api_key="sk-cvc-small",
                service="Grok",
                name="Grok small",
                token_limit=2_000_000,
                used_tokens=0,
            )
        ])
        self.key_client.balance_value = 50_000
        self.key_client.primary_balance_value = 10_000_000
        login_form, login_headers = await self.csrf("/ai/tokens/adm", "tokens_admin_csrf")
        login = await self.client.post(
            "/ai/tokens/adm/login",
            data={**login_form, "password": "secret"},
            headers=login_headers,
            follow_redirects=False,
        )
        session_cookie = login.headers["set-cookie"].split(";", 1)[0]
        response = await self.client.get("/ai/tokens/adm", headers={"Cookie": session_cookie})

        self.assertEqual(response.status_code, 200)
        self.assertIn("1 950 000", response.text)
        self.assertIn("ост. 50 000", response.text)
        stored = await self.store.get(9)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.used_tokens if stored else None, 1_950_000)


class TokenInstructionHelpersTestCase(unittest.TestCase):
    def test_grok_is_a_first_class_service_and_instruction_group(self) -> None:
        self.assertIn("Grok", SERVICE_OPTIONS)
        self.assertEqual(default_instruction_choice("Grok"), ("grok", "Grok Build"))
        self.assertEqual(
            instruction_command("Grok Build", "Windows", "sk-cvc-example"),
            "$env:CVC_API_KEY='sk-cvc-example'; iex(irm 'https://starimg.ru/ai/common/igw')",
        )
        self.assertEqual(
            instruction_command("Grok Build", "macOS", "sk-cvc-example"),
            "bash <(curl -fsSL 'https://starimg.ru/ai/common/igm') 'sk-cvc-example'",
        )
        self.assertEqual(
            instruction_command("Grok Build", "Linux", "sk-cvc-example"),
            "bash <(curl -fsSL 'https://starimg.ru/ai/common/igl') 'sk-cvc-example'",
        )
        self.assertEqual(instruction_remove_command("Grok Build", "Windows"), "iex(irm 'https://starimg.ru/ai/common/rgw')")
        self.assertEqual(
            instruction_steps("Grok Build", "Windows", "ru"),
            ["Открой PowerShell.", "Выполни команду ниже.", "Перезапусти терминал и введи grok."],
        )
        self.assertEqual(
            instruction_steps("Grok Build", "macOS", "en"),
            ["Open the terminal.", "Run the command below.", "Restart the terminal and type grok."],
        )

    def test_manual_instructions_and_removal_cover_all_instruction_apps(self) -> None:
        manual = manual_instruction_command("Grok Build", "Windows", "sk-cvc-example")
        self.assertIn("~/.grok/config.toml", manual)
        self.assertIn('api_key = "sk-cvc-example"', manual)
        self.assertIn("api_backend = \"chat_completions\"", manual)
        downloads = manual_download_buttons("VS Code", "sk-cvc-example", "ru")
        self.assertIn("Скачать config.toml", downloads)
        self.assertIn("download='auth.json'", downloads)
        self.assertIn("https%3A//starimg.ru/ai/common/v1", downloads)
        self.assertNotIn("cheapvibecode", downloads.lower())
        self.assertEqual(manual_download_buttons("Grok Build", "sk-cvc-example"), "")
        self.assertIn("api_backend = chat_completions", manual_instruction_note("Grok Build", "Windows", "ru"))
        self.assertIn("Grok models", manual_instruction_note("Grok Build", "Windows", "en"))
        applications = " ".join(app for _, _, apps in INSTRUCTION_GROUPS for app in apps)
        self.assertIn("Kimi Code CLI", applications)
        self.assertIn("ZCode", applications)
        self.assertIsNotNone(instruction_remove_command("Claude Code CLI", "Windows"))
        self.assertIsNotNone(instruction_remove_command("Cursor", "Linux"))
        for _, _, instruction_apps in INSTRUCTION_GROUPS:
            for application in instruction_apps:
                for system in INSTRUCTION_SYSTEMS_BY_APP[application]:
                    self.assertIn(system, INSTRUCTION_ENDPOINTS[application])
                    self.assertIn(system, INSTRUCTION_REMOVE_ENDPOINTS[application])
                    self.assertTrue(manual_instruction_command(application, system, "sk-cvc-example"))
                    self.assertTrue(manual_instruction_note(application, system, "ru"))
                    self.assertTrue(manual_instruction_note(application, system, "en"))

    def test_secondary_remaining_is_rejected_when_it_is_the_primary_balance(self) -> None:
        self.assertIsNone(trusted_secondary_remaining(50_000_000, 2_000_000, 50_000_000))
        self.assertIsNone(trusted_secondary_remaining(500_000, 2_000_000, 500_000))
        self.assertEqual(trusted_secondary_remaining(50_000, 2_000_000, 10_000_000), 50_000)
        self.assertEqual(trusted_secondary_remaining(2_000_000, 2_000_000, 10_000_000), 2_000_000)


class TokenStoresConfigurationTestCase(unittest.TestCase):
    def test_owner_store_paths_are_numbered_and_legacy_store_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            legacy_path = Path(directory) / "token_keys.json"
            legacy_path.write_text("[]", encoding="utf-8")

            stores = create_token_key_stores(legacy_path, 2)

            self.assertEqual([store.path.name for store in stores], ["token_keys_1.json", "token_keys_2.json"])
            self.assertFalse(legacy_path.exists())
            self.assertTrue((Path(directory) / "token_keys_1.json").exists())
            self.assertTrue((Path(directory) / "token_keys_2.json").exists())


if __name__ == "__main__":
    unittest.main()
