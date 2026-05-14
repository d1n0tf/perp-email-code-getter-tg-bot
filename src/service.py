import asyncio
import contextlib
from concurrent.futures import ThreadPoolExecutor

from aiogram import Bot

from src.config import Settings
from src.email_manager import CodeResult, CodeWaitTimeout, EmailCodeFetcher
from src.messages import DEFAULT_LOCALE
from src.storage import EmailAccount, JsonStorage, normalize_email


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
