import asyncio
from contextlib import suppress

from aiogram import Bot, Dispatcher
import uvicorn

from src.config import settings
from src.handlers import build_router
from src.service import BotService
from src.storage import JsonStorage
from src.web import create_web_app


async def _main() -> None:
    storage = JsonStorage(
        email_store_path=settings.email_store_path,
        taken_email_store_path=settings.taken_email_store_path,
        user_locale_store_path=settings.user_locale_store_path,
    )
    service = BotService(settings=settings, storage=storage)

    try:
        async with asyncio.TaskGroup() as task_group:
            task_group.create_task(_run_web_server(service))
            if settings.tg_token:
                task_group.create_task(_run_telegram_bot(service))
    finally:
        await service.shutdown()


async def _run_telegram_bot(service: BotService) -> None:
    if not settings.tg_token:
        return

    bot = Bot(token=settings.tg_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(service))

    try:
        await dispatcher.start_polling(bot, handle_signals=False)
    finally:
        await bot.session.close()


async def _run_web_server(service: BotService) -> None:
    app = create_web_app(service)
    config = uvicorn.Config(
        app,
        host=settings.web_host,
        port=settings.web_port,
        reload=False,
        log_level="info",
    )
    server = uvicorn.Server(config)

    try:
        await server.serve()
    except asyncio.CancelledError:
        server.should_exit = True
        raise
    finally:
        with suppress(Exception):
            server.should_exit = True


def run() -> None:
    asyncio.run(_main())
