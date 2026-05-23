import asyncio
import json
from dataclasses import dataclass
from time import monotonic
from typing import Any

import aiohttp


DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
DEVICE_FLOW_SCOPE = "offline_access https://outlook.office.com/IMAP.AccessAsUser.All"


@dataclass(slots=True, frozen=True)
class DeviceCodeResponse:
    client_id: str
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    expires_in: int
    interval: int
    message: str | None
    raw: dict[str, Any]


@dataclass(slots=True, frozen=True)
class RefreshTokenResponse:
    refresh_token: str
    raw: dict[str, Any]


class MicrosoftDeviceFlowError(RuntimeError):
    def __init__(
        self,
        *,
        step: str,
        status: int | None = None,
        data: dict[str, Any] | None = None,
        raw_text: str | None = None,
        message: str | None = None,
    ) -> None:
        self.step = step
        self.status = status
        self.data = data
        self.raw_text = raw_text
        super().__init__(message or self._build_message())

    def _build_message(self) -> str:
        parts = [f"step={self.step}"]
        if self.status is not None:
            parts.append(f"http_status={self.status}")

        if self.data:
            error = str(self.data.get("error") or "").strip()
            description = str(self.data.get("error_description") or "").strip()
            correlation_id = str(self.data.get("correlation_id") or "").strip()
            trace_id = str(self.data.get("trace_id") or "").strip()

            if error:
                parts.append(f"error={error}")
            if description:
                parts.append(f"description={description}")
            if correlation_id:
                parts.append(f"correlation_id={correlation_id}")
            if trace_id:
                parts.append(f"trace_id={trace_id}")

        if self.raw_text and not self.data:
            compact_body = " ".join(self.raw_text.split())
            if compact_body:
                parts.append(f"body={compact_body[:400]}")

        return "Microsoft device flow failed: " + ", ".join(parts)


class MicrosoftDeviceFlowClient:
    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def request_device_code(self, client_id: str) -> DeviceCodeResponse:
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            status, data, raw_text, _headers = await self._post_form(
                session,
                DEVICE_CODE_URL,
                {
                    "client_id": client_id,
                    "scope": DEVICE_FLOW_SCOPE,
                },
                step="request_device_code",
            )

        if not 200 <= status < 300:
            raise MicrosoftDeviceFlowError(
                step="request_device_code",
                status=status,
                data=data,
                raw_text=raw_text,
            )
        if data is None:
            raise MicrosoftDeviceFlowError(
                step="request_device_code",
                status=status,
                raw_text=raw_text,
                message="Microsoft returned an empty or non-JSON device code payload.",
            )

        device_code = self._read_required_str(data, "device_code", step="request_device_code")
        user_code = self._read_required_str(data, "user_code", step="request_device_code")
        verification_uri = self._read_required_str(
            data,
            "verification_uri",
            step="request_device_code",
        )
        expires_in = self._read_required_int(data, "expires_in", step="request_device_code")
        interval = self._read_optional_int(data, "interval") or 5
        verification_uri_complete = self._read_optional_str(data, "verification_uri_complete")
        message = self._read_optional_str(data, "message")

        return DeviceCodeResponse(
            client_id=client_id,
            device_code=device_code,
            user_code=user_code,
            verification_uri=verification_uri,
            verification_uri_complete=verification_uri_complete,
            expires_in=max(1, expires_in),
            interval=max(1, interval),
            message=message,
            raw=data,
        )

    async def poll_for_refresh_token(
        self,
        client_id: str,
        device_code: str,
        *,
        expires_in: int,
        interval: int,
    ) -> RefreshTokenResponse:
        deadline = monotonic() + max(1, expires_in)
        poll_interval = max(1, interval)
        transient_failures = 0

        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            while True:
                if monotonic() >= deadline:
                    raise MicrosoftDeviceFlowError(
                        step="poll_for_refresh_token",
                        message="The device code expired before Microsoft returned a refresh_token.",
                    )

                status, data, raw_text, headers = await self._post_form(
                    session,
                    TOKEN_URL,
                    {
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "client_id": client_id,
                        "device_code": device_code,
                    },
                    step="poll_for_refresh_token",
                )

                if 200 <= status < 300:
                    if data is None:
                        raise MicrosoftDeviceFlowError(
                            step="poll_for_refresh_token",
                            status=status,
                            raw_text=raw_text,
                            message="Microsoft returned an empty or non-JSON token payload.",
                        )
                    refresh_token = self._read_required_str(
                        data,
                        "refresh_token",
                        step="poll_for_refresh_token",
                    )
                    return RefreshTokenResponse(refresh_token=refresh_token, raw=data)

                error_code = ""
                if data is not None:
                    error_code = str(data.get("error") or "").strip().lower()

                if error_code == "authorization_pending":
                    await asyncio.sleep(poll_interval)
                    continue

                if error_code == "slow_down" or status == 429:
                    retry_after = self._parse_retry_after(headers.get("Retry-After"))
                    poll_interval = max(poll_interval + 5, retry_after or 0, 1)
                    await asyncio.sleep(poll_interval)
                    continue

                if 500 <= status < 600:
                    transient_failures += 1
                    if transient_failures <= 3:
                        await asyncio.sleep(poll_interval)
                        continue

                raise MicrosoftDeviceFlowError(
                    step="poll_for_refresh_token",
                    status=status,
                    data=data,
                    raw_text=raw_text,
                )

    async def _post_form(
        self,
        session: aiohttp.ClientSession,
        url: str,
        data: dict[str, str],
        *,
        step: str,
    ) -> tuple[int, dict[str, Any] | None, str, dict[str, str]]:
        try:
            async with session.post(
                url,
                data=data,
                headers={"Accept": "application/json"},
            ) as response:
                raw_text = await response.text()
                headers = dict(response.headers)
                parsed = self._parse_json_object(raw_text)
                return response.status, parsed, raw_text, headers
        except asyncio.TimeoutError as exc:
            raise MicrosoftDeviceFlowError(
                step=step,
                message=f"Request to {url} timed out.",
            ) from exc
        except aiohttp.ClientError as exc:
            raise MicrosoftDeviceFlowError(
                step=step,
                message=f"Network error while requesting {url}: {exc}",
            ) from exc

    def _parse_json_object(self, raw_text: str) -> dict[str, Any] | None:
        if not raw_text.strip():
            return None

        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            return None

        if not isinstance(parsed, dict):
            return None
        return parsed

    def _read_required_str(
        self,
        data: dict[str, Any],
        field_name: str,
        *,
        step: str,
    ) -> str:
        value = data.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise MicrosoftDeviceFlowError(
            step=step,
            data=data,
            message=f"Microsoft response does not contain a valid '{field_name}' string.",
        )

    def _read_optional_str(self, data: dict[str, Any], field_name: str) -> str | None:
        value = data.get(field_name)
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return None

    def _read_required_int(
        self,
        data: dict[str, Any],
        field_name: str,
        *,
        step: str,
    ) -> int:
        value = self._read_optional_int(data, field_name)
        if value is not None:
            return value
        raise MicrosoftDeviceFlowError(
            step=step,
            data=data,
            message=f"Microsoft response does not contain a valid '{field_name}' integer.",
        )

    def _read_optional_int(self, data: dict[str, Any], field_name: str) -> int | None:
        value = data.get(field_name)
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                return int(stripped)
        return None

    def _parse_retry_after(self, value: str | None) -> int | None:
        if value is None:
            return None
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
        return None

