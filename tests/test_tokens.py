import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

import httpx
from fastapi import FastAPI

from src.tokens import (
    INSTRUCTION_ENDPOINTS,
    INSTRUCTION_GROUPS,
    INSTRUCTION_REMOVE_ENDPOINTS,
    INSTRUCTION_SYSTEMS_BY_APP,
    SERVICE_OPTIONS,
    TokenKey,
    TokenKeyStore,
    create_tokens_routes,
    default_instruction_choice,
    instruction_command,
    instruction_remove_command,
    instruction_steps,
    manual_instruction_command,
    manual_instruction_note,
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
        self.balance_calls: list[str] = []
        self.balance_value: int = 1_234_567
        self.primary_balance_value: int = 10_000_000
        self.balance_error: Exception | None = None
        self.primary_balance_error: Exception | None = None

    async def create_key(self, *, name: str, token_limit: int) -> str:
        self.calls.append((name, token_limit))
        return "sk-test-" + str(len(self.calls))

    async def get_token_balance(self, *, api_key: str) -> int:
        self.balance_calls.append(api_key)
        if self.balance_error is not None:
            raise self.balance_error
        return self.balance_value

    async def get_primary_token_balance(self) -> int:
        if self.primary_balance_error is not None:
            raise self.primary_balance_error
        return self.primary_balance_value


class TokensRoutesTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = TokenKeyStore(Path(self.directory.name) / "token_keys.json")
        self.key_client = FakeKeyClient()
        self.app = FastAPI()
        create_tokens_routes(
            self.app,
            store=self.store,
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

    async def test_unknown_access_key_is_reported(self) -> None:
        form, headers = await self.csrf("/ai/tokens", "tokens_user_csrf")
        response = await self.client.post(
            "/ai/tokens", data={**form, "access_code": "ABCDEFGHIJKLMNOPQRST"}, headers=headers
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("\u041a\u043b\u044e\u0447 \u0434\u043e\u0441\u0442\u0443\u043f\u0430 \u043d\u0435 \u0441\u0443\u0449\u0435\u0441\u0442\u0432\u0443\u0435\u0442", response.text)

    async def test_public_page_supports_english_locale(self) -> None:
        response = await self.client.get("/ai/tokens?lang=en")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<h1>Activation Service</h1>", response.text)
        self.assertIn("/ai/tokens?lang=ru", response.text)
        self.assertIn("name='lang' value='en'", response.text)

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


if __name__ == "__main__":
    unittest.main()
