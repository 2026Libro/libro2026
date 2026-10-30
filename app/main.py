"""FastAPI 애플리케이션 진입점.

실행: uvicorn app.main:app --reload
"""
import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.watchdog import run_task_watchdog
from app.routers import (
    basket,
    books,
    copies,
    health,
    observations,
    robots,
    scan_sessions,
    shelves,
    tasks,
    verifications,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    watchdog = asyncio.create_task(run_task_watchdog())  # 로봇 통신 끊김 감시 시작
    yield
    watchdog.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await watchdog


app = FastAPI(title=get_settings().app_name, version="0.1.0", lifespan=lifespan)
register_exception_handlers(app)

app.include_router(health.router)
app.include_router(books.router)
app.include_router(copies.router)
app.include_router(shelves.router)
app.include_router(scan_sessions.router)
app.include_router(observations.router)
app.include_router(verifications.router)
app.include_router(robots.router)
app.include_router(tasks.router)
app.include_router(basket.router)
