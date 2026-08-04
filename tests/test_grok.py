import unittest
from html.parser import HTMLParser

import httpx
from fastapi import FastAPI

from src.grok import GrokOrder, create_grok_routes, extract_grok_user_id


class FakeGrokClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def activate(self, *, code: str, user_id: str) -> GrokOrder:
        self.calls.append((code, user_id))
        return GrokOrder(order_id=41, status="queued", product="grok")

    async def get_status(self, order_id: int) -> GrokOrder:
        return GrokOrder(order_id=order_id, status="done", product="grok")


class HiddenInputParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "input":
            values = dict(attrs)
            if values.get("type") == "hidden" and values.get("name") and values.get("value"):
                self.values[values["name"]] = values["value"]


class GrokPageTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.api = FakeGrokClient()
        self.app = FastAPI()
        create_grok_routes(self.app, client=self.api)  # type: ignore[arg-type]
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def csrf_data(self) -> tuple[dict[str, str], dict[str, str]]:
        page = await self.client.get("/ai/grok")
        parser = HiddenInputParser()
        parser.feed(page.text)
        token = parser.values["csrf_token"]
        return {"csrf_token": token}, {"Cookie": f"grok_activation_csrf={token}"}

    async def test_page_and_activation(self) -> None:
        page = await self.client.get("/ai/grok")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Активировать подписку", page.text)
        form, headers = await self.csrf_data()

        response = await self.client.post(
            "/ai/grok",
            data={
                **form,
                "code": "grk-ab12-cd34-ef56",
                "user_id": '{"userId":"12DA03B3-8380-4D02-9FDE-9C7FADF17CFA"}',
            },
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Заявка №41", response.text)
        self.assertEqual(self.api.calls, [("GRK-AB12-CD34-EF56", "12da03b3-8380-4d02-9fde-9c7fadf17cfa")])

    async def test_invalid_values_are_not_sent_to_api(self) -> None:
        form, headers = await self.csrf_data()
        response = await self.client.post("/ai/grok", data={**form, "code": "wrong", "user_id": "no id"}, headers=headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("GRK-XXXX-XXXX-XXXX", response.text)
        self.assertEqual(self.api.calls, [])

    async def test_activation_requires_csrf_token(self) -> None:
        response = await self.client.post("/ai/grok", data={"code": "GRK-AB12-CD34-EF56", "user_id": "12da03b3-8380-4d02-9fde-9c7fadf17cfa"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.api.calls, [])

    async def test_status_endpoint(self) -> None:
        response = await self.client.get("/ai/grok/status", params={"order_id": "41"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "done")


class GrokUserIdTestCase(unittest.TestCase):
    def test_extracts_uuid_from_session_json_and_text(self) -> None:
        user_id = "12DA03B3-8380-4D02-9FDE-9C7FADF17CFA"
        self.assertEqual(extract_grok_user_id('{"userId": "' + user_id + '"}'), user_id.lower())
        self.assertEqual(extract_grok_user_id("session userId=" + user_id), user_id.lower())
        self.assertIsNone(extract_grok_user_id("not a UUID"))
