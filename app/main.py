"""FastAPI 애플리케이션 진입점.

실행: uvicorn app.main:app --reload
"""
import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.watchdog import run_task_watchdog
from app.routers import (
    admin,
    basket,
    books,
    copies,
    dashboard,
    health,
    observations,
    robots,
    scan_sessions,
    shelves,
    tasks,
    verifications,
    ws,
)
from app.services import alerts, realtime_events  # noqa: F401  DB 변경 감지 리스너 등록 (알림 생성, 실시간 이벤트)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    watchdog = asyncio.create_task(run_task_watchdog())  # 로봇 통신 끊김 감시 시작
    yield
    watchdog.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await watchdog


app = FastAPI(title=get_settings().app_name, version="0.1.0", lifespan=lifespan)
register_exception_handlers(app)
# 다른 주소(포트)에서 실행되는 프론트엔드가 이 API를 호출할 수 있도록 허용 (.env의 CORS_ORIGINS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],  # X-Admin-Token 등
)

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
app.include_router(admin.router)
app.include_router(dashboard.router)
app.include_router(ws.router)
