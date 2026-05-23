import asyncio
import contextlib
import json
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from time import monotonic
from uuid import uuid4

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.config import Settings
from src.email_manager import CodeResult, CodeWaitTimeout, EmailCodeFetcher
from src.microsoft_device_flow import (
    DeviceCodeResponse,
    MicrosoftDeviceFlowClient,
    MicrosoftDeviceFlowError,
)
from src.messages import DEFAULT_LOCALE
from src.storage import EmailAccount, JsonStorage, normalize_email


@dataclass(slots=True)
class WebCodeRequest:
    request_id: str
    requester_id: str
    email_address: str
    status: str
    code: str | None = None


@dataclass(slots=True)
class RefreshPromptState:
    user_id: int
    created_at: float


class BotService:
    def __init__(self, settings: Settings, storage: JsonStorage) -> None:
        self.settings = settings
        self.storage = storage
        self.fetcher = EmailCodeFetcher(settings)
        self.device_flow_client = MicrosoftDeviceFlowClient()
        self.executor = ThreadPoolExecutor(
            max_workers=settings.concurrent_mail_workers,
            thread_name_prefix="mail-worker",
        )
        self._tasks: set[asyncio.Task[None]] = set()
        self._web_requests_lock = asyncio.Lock()
        self._web_requests: dict[str, WebCodeRequest] = {}
        self._active_web_requests: dict[tuple[str, str], str] = {}
        self._refresh_lock = asyncio.Lock()
        self._refresh_prompts: dict[int, RefreshPromptState] = {}
        self._active_refresh_tasks: dict[int, asyncio.Task[None]] = {}
        self._refresh_prompt_timeout_seconds = 15 * 60

    def is_admin(self, user_id: int) -> bool:
        if not self.settings.tg_admins:
            return True
        return user_id in self.settings.tg_admins

    async def get_locale(self, user_id: int) -> str:
        return await self.storage.get_locale(user_id, default_locale=DEFAULT_LOCALE)

    async def set_locale(self, user_id: int, locale: str) -> None:
        await self.storage.set_locale(user_id, locale)

    async def add_account(self, raw_value: str) -> tuple[EmailAccount, bool]:
        account = EmailAccount.from_add_string(raw_value)
        existed = await self.storage.upsert_account(account)
        return account, existed

    async def begin_refresh_prompt(self, user_id: int) -> None:
        async with self._refresh_lock:
            self._refresh_prompts[user_id] = RefreshPromptState(
                user_id=user_id,
                created_at=monotonic(),
            )

    async def consume_refresh_prompt(self, user_id: int) -> bool:
        async with self._refresh_lock:
            prompt = self._refresh_prompts.get(user_id)
            if prompt is None:
                return False

            if monotonic() - prompt.created_at > self._refresh_prompt_timeout_seconds:
                self._refresh_prompts.pop(user_id, None)
                return False

            self._refresh_prompts.pop(user_id, None)
            return True

    async def start_refresh_token_request(
        self,
        *,
        bot: Bot,
        user_id: int,
        chat_id: int,
        client_id: str,
    ) -> str:
        normalized_client_id = client_id.strip().strip("\"'")
        if not normalized_client_id:
            raise ValueError("client_id is empty")

        async with self._refresh_lock:
            active_task = self._active_refresh_tasks.get(user_id)
            if active_task is not None and not active_task.done():
                return "running"

            task = asyncio.create_task(
                self._run_refresh_token_request(
                    bot=bot,
                    user_id=user_id,
                    chat_id=chat_id,
                    client_id=normalized_client_id,
                )
            )
            self._active_refresh_tasks[user_id] = task

        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(
            lambda done_task, refresh_user_id=user_id: self._drop_refresh_task(
                refresh_user_id,
                done_task,
            )
        )
        return "started"

    async def prepare_code_request(
        self,
        *,
        requester_id: str,
        requester_kind: str,
        user_id: int | None,
        chat_id: int | None,
        username: str | None,
        full_name: str | None,
        email_address: str,
    ) -> tuple[str, EmailAccount | None]:
        normalized_email = normalize_email(email_address)
        account = await self.storage.get_account(normalized_email)
        if account is None:
            return "missing", None

        reserved = await self.storage.reserve_email(
            normalized_email,
            owner_id=requester_id,
            owner_kind=requester_kind,
            user_id=user_id,
            chat_id=chat_id,
            username=username,
            full_name=full_name,
        )
        if not reserved:
            return "taken", None

        return "started", account

    async def fetch_code(self, account: EmailAccount) -> CodeResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self.executor,
            self.fetcher.wait_for_code,
            account,
        )

    async def start_web_code_request(
        self,
        *,
        requester_id: str,
        requester_kind: str,
        user_id: int | None,
        chat_id: int | None,
        username: str | None,
        full_name: str | None,
        email_address: str,
    ) -> tuple[str, str | None]:
        normalized_email = normalize_email(email_address)
        request_key = (requester_id, normalized_email)

        async with self._web_requests_lock:
            existing_request_id = self._active_web_requests.get(request_key)
            if existing_request_id is not None:
                existing_request = self._web_requests.get(existing_request_id)
                if existing_request is not None and existing_request.status == "pending":
                    return "started", existing_request_id

            status, account = await self.prepare_code_request(
                requester_id=requester_id,
                requester_kind=requester_kind,
                user_id=user_id,
                chat_id=chat_id,
                username=username,
                full_name=full_name,
                email_address=normalized_email,
            )
            if account is None:
                return status, None

            request_id = uuid4().hex
            self._web_requests[request_id] = WebCodeRequest(
                request_id=request_id,
                requester_id=requester_id,
                email_address=normalized_email,
                status="pending",
            )
            self._active_web_requests[request_key] = request_id

        task = asyncio.create_task(
            self._complete_web_code_request(
                request_id=request_id,
                request_key=request_key,
                account=account,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return "started", request_id

    async def get_web_code_request(
        self,
        *,
        request_id: str,
        requester_id: str,
    ) -> WebCodeRequest | None:
        async with self._web_requests_lock:
            request = self._web_requests.get(request_id)
            if request is None or request.requester_id != requester_id:
                return None
            return WebCodeRequest(
                request_id=request.request_id,
                requester_id=request.requester_id,
                email_address=request.email_address,
                status=request.status,
                code=request.code,
            )

    async def start_code_request(
        self,
        *,
        bot: Bot,
        requester_id: str,
        requester_kind: str,
        user_id: int,
        chat_id: int,
        username: str | None,
        full_name: str | None,
        email_address: str,
    ) -> str:
        status, account = await self.prepare_code_request(
            requester_id=requester_id,
            requester_kind=requester_kind,
            user_id=user_id,
            chat_id=chat_id,
            username=username,
            full_name=full_name,
            email_address=email_address,
        )
        if account is None:
            return status

        task = asyncio.create_task(
            self._deliver_code(
                bot=bot,
                user_id=user_id,
                chat_id=chat_id,
                account=account,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return "started"

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self.executor.shutdown(wait=False, cancel_futures=True)

    async def _run_refresh_token_request(
        self,
        *,
        bot: Bot,
        user_id: int,
        chat_id: int,
        client_id: str,
    ) -> None:
        locale = await self.get_locale(user_id)

        try:
            device_code = await self.device_flow_client.request_device_code(client_id)
            await self._send_long_message(
                bot,
                chat_id,
                self._format_device_code_intro_message(locale, device_code),
                reply_markup=self._build_refresh_login_keyboard(
                    locale,
                    device_code,
                    user_id,
                ),
            )
            await self._send_long_message(
                bot,
                chat_id,
                self._format_device_code_debug_message(device_code),
            )

            refresh_token_result = await self.device_flow_client.poll_for_refresh_token(
                client_id,
                device_code.device_code,
                expires_in=device_code.expires_in,
                interval=device_code.interval,
            )
            await self._send_long_message(
                bot,
                chat_id,
                self._format_refresh_success_message(
                    locale,
                    client_id,
                    refresh_token_result.refresh_token,
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                await self._send_long_message(
                    bot,
                    chat_id,
                    self._format_refresh_error_message(locale, client_id, exc),
                )

    async def _deliver_code(
        self,
        *,
        bot: Bot,
        user_id: int,
        chat_id: int,
        account: EmailAccount,
    ) -> None:
        try:
            result = await self.fetch_code(account)
            locale = await self.get_locale(user_id)
            await bot.send_message(
                chat_id,
                self._format_message(
                    locale,
                    "code_found",
                    email=account.login_email,
                    code=result.code,
                ),
            )
        except CodeWaitTimeout:
            locale = await self.get_locale(user_id)
            await bot.send_message(
                chat_id,
                self._format_message(
                    locale,
                    "code_timeout",
                    email=account.login_email,
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            locale = await self.get_locale(user_id)
            with contextlib.suppress(Exception):
                await bot.send_message(
                    chat_id,
                    self._format_message(
                        locale,
                        "code_failed",
                        email=account.login_email,
                    ),
                )

    def _format_device_code_intro_message(
        self,
        locale: str,
        device_code: DeviceCodeResponse,
    ) -> str:
        login_url = self._get_refresh_login_url(device_code)
        lines = [
            self._format_message(
                locale,
                "refresh_device_code_ready",
                client_id=device_code.client_id,
            ),
            f"user_code: {device_code.user_code}",
            f"verification_uri: {device_code.verification_uri}",
            f"login_url: {login_url}",
        ]
        if device_code.verification_uri_complete:
            lines.append(
                f"verification_uri_complete: {device_code.verification_uri_complete}"
            )
        lines.extend(
            [
                f"expires_in: {device_code.expires_in}",
                f"interval: {device_code.interval}",
                "",
                self._format_message(locale, "refresh_waiting_for_confirmation"),
            ]
        )
        return "\n".join(lines)

    def _format_device_code_debug_message(self, device_code: DeviceCodeResponse) -> str:
        response_json = json.dumps(
            device_code.raw,
            ensure_ascii=False,
            indent=2,
        )
        return "\n".join(
            [
                "Microsoft response:",
                response_json,
            ]
        )

    def _format_refresh_success_message(
        self,
        locale: str,
        client_id: str,
        refresh_token: str,
    ) -> str:
        return self._format_message(
            locale,
            "refresh_success",
            client_id=client_id,
            refresh_token=refresh_token,
        )

    def _format_refresh_error_message(
        self,
        locale: str,
        client_id: str,
        exc: Exception,
    ) -> str:
        lines = [
            self._format_message(locale, "refresh_failed", client_id=client_id),
            f"Exception: {exc.__class__.__name__}: {exc}",
        ]

        if isinstance(exc, MicrosoftDeviceFlowError):
            lines.append(f"Step: {exc.step}")
            if exc.status is not None:
                lines.append(f"HTTP status: {exc.status}")
            if exc.data:
                lines.extend(
                    [
                        "",
                        "Microsoft response JSON:",
                        json.dumps(exc.data, ensure_ascii=False, indent=2),
                    ]
                )
            elif exc.raw_text:
                lines.extend(
                    [
                        "",
                        "Microsoft response text:",
                        exc.raw_text,
                    ]
                )

        trace = "".join(traceback.format_exception(exc))
        lines.extend(
            [
                "",
                "Traceback:",
                trace.rstrip(),
            ]
        )
        return "\n".join(lines)

    async def _send_long_message(
        self,
        bot: Bot,
        chat_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        chunks = self._chunk_message(text)
        for index, chunk in enumerate(chunks):
            await bot.send_message(
                chat_id,
                chunk,
                reply_markup=reply_markup if index == 0 else None,
            )

    def _chunk_message(self, text: str, limit: int = 3900) -> list[str]:
        if len(text) <= limit:
            return [text]

        chunks: list[str] = []
        remaining = text
        while len(remaining) > limit:
            split_at = remaining.rfind("\n", 0, limit)
            if split_at <= 0:
                split_at = limit
            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip("\n")

        if remaining:
            chunks.append(remaining)
        return chunks

    def _drop_refresh_task(self, user_id: int, task: asyncio.Task[None]) -> None:
        current_task = self._active_refresh_tasks.get(user_id)
        if current_task is task:
            self._active_refresh_tasks.pop(user_id, None)

    def _build_refresh_login_keyboard(
        self,
        locale: str,
        device_code: DeviceCodeResponse,
        user_id: int,
    ) -> InlineKeyboardMarkup:
        login_url = self._get_refresh_login_url(device_code)
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=self._format_message(locale, "refresh_open_login_button"),
                        url=login_url,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self._format_message(locale, "refresh_logged_in_button"),
                        callback_data=f"refresh_ack:{user_id}",
                    )
                ],
            ]
        )

    def _get_refresh_login_url(self, device_code: DeviceCodeResponse) -> str:
        return device_code.verification_uri_complete or device_code.verification_uri

    def _format_message(self, locale: str, key: str, **kwargs: str) -> str:
        from src.messages import translate

        return translate(locale, key, **kwargs)

    async def _complete_web_code_request(
        self,
        *,
        request_id: str,
        request_key: tuple[str, str],
        account: EmailAccount,
    ) -> None:
        try:
            result = await self.fetch_code(account)
        except CodeWaitTimeout:
            await self._set_web_request_result(
                request_id=request_id,
                request_key=request_key,
                status="timeout",
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._set_web_request_result(
                request_id=request_id,
                request_key=request_key,
                status="failed",
            )
        else:
            await self._set_web_request_result(
                request_id=request_id,
                request_key=request_key,
                status="success",
                code=result.code,
            )

    async def _set_web_request_result(
        self,
        *,
        request_id: str,
        request_key: tuple[str, str],
        status: str,
        code: str | None = None,
    ) -> None:
        async with self._web_requests_lock:
            request = self._web_requests.get(request_id)
            if request is not None:
                request.status = status
                request.code = code

            active_request_id = self._active_web_requests.get(request_key)
            if active_request_id == request_id:
                self._active_web_requests.pop(request_key, None)
