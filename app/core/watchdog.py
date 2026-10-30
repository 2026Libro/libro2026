"""서버 실행 중 주기적으로 로봇 통신 끊김을 검사하는 백그라운드 작업.

uvicorn으로 서버를 실행하면 자동으로 시작된다. (테스트에서는 실행되지 않으며, 테스트는 mark_unresponsive를 직접 호출한다)
"""
import asyncio
import logging

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.robot_task import mark_unresponsive

logger = logging.getLogger(__name__)


def _check_once() -> None:
    settings = get_settings()
    with SessionLocal() as db:
        if marked := mark_unresponsive(db, settings.robot_offline_after_seconds):
            logger.warning("로봇 무응답으로 결과 불명(UNKNOWN) 처리한 작업: %s", marked)


async def run_task_watchdog() -> None:
    interval = get_settings().task_watchdog_interval_seconds
    while True:
        try:
            await asyncio.to_thread(_check_once)  # DB 작업은 별도 스레드에서 (서버 응답을 막지 않도록)
        except Exception:
            logger.exception("작업 감시 중 오류 (다음 주기에 다시 시도)")
        await asyncio.sleep(interval)
