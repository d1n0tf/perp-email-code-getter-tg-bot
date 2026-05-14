import asyncio
import contextlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from uuid import uuid4

from aiogram import Bot

from src.config import Settings
from src.email_manager import CodeResult, CodeWaitTimeout, EmailCodeFetcher
from src.messages import DEFAULT_LOCALE
from src.storage import EmailAccount, JsonStorage, normalize_email


@dataclass(slots=True)
class WebCodeRequest:
    request_id: str
    requester_id: str
    email_address: str
    status: str
    code: str | None = None


class BotService:
    def __init__(self, settings: Settings, storage: JsonStorage) -> None:
        self.settings = settings
        self.storage = storage
        self.fetcher = EmailCodeFetcher(settings)
        self.executor = ThreadPoolExecutor(
            max_workers=settings.concurrent_mail_workers,
            thread_name_prefix="mail-worker",
        )
        self._tasks: set[asyncio.Task[None]] = set()
        self._web_requests_lock = asyncio.Lock()
        self._web_requests: dict[str, WebCodeRequest] = {}
        self._active_web_requests: dict[tuple[str, str], str] = {}

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
