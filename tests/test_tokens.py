import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

import httpx
from fastapi import FastAPI

from src.tokens import TokenKey, TokenKeyStore, create_tokens_routes, utc_now


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

    async def create_key(self, *, name: str, token_limit: int) -> str:
        self.calls.append((name, token_limit))
        return "sk-test-" + str(len(self.calls))

    async def get_token_balance(self, *, api_key: str) -> int:
        self.balance_calls.append(api_key)
        return 1_234_567


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
        self.assertIn("Activation Service", response.text)
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
        self.assertEqual(self.key_client.balance_calls, ["sk-cvc-balance-test"])

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


if __name__ == "__main__":
    unittest.main()
