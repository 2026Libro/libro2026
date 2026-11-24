"""스캔 세션 관리 (BE-007).

[가정] 지금은 관리자가 세션을 만들고 로봇이 종료를 보고한다.
7단계에서 SCAN_SHELF 작업을 만들 때 세션도 함께 자동 생성하도록 연결한다.
"""
import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import ScanSessionStatus
from app.core.errors import AppError
from app.db.base import utcnow
from app.models import Observation, Robot, ScanSession, Shelf
from app.schemas.common import ErrorCode
from app.schemas.scan_session import ScanSessionCreate, ScanSessionFinish, ScanSessionRead


def _new_session_id() -> str:
    return f"SCAN-{utcnow():%Y%m%d%H%M%S}-{secrets.token_hex(3)}"


def _to_read(db: Session, session: ScanSession) -> ScanSessionRead:
    count = db.scalar(select(func.count()).where(Observation.scan_session_id == session.id))
    return ScanSessionRead(
        id=session.id, robot_id=session.robot_id, shelf_id=session.shelf_id, status=session.status,
        started_at=session.started_at, finished_at=session.finished_at, observation_count=count,
    )


def get_session(db: Session, session_id: str) -> ScanSession:
    session = db.get(ScanSession, session_id)
    if session is None:
        raise AppError(ErrorCode.NOT_FOUND, f"스캔 세션 '{session_id}'를 찾을 수 없습니다.", status_code=404)
    return session


def create_session(db: Session, data: ScanSessionCreate) -> ScanSessionRead:
    if db.get(Robot, data.robot_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, f"로봇 '{data.robot_id}'를 찾을 수 없습니다.", status_code=404)
    if db.get(Shelf, data.shelf_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, f"서가 '{data.shelf_id}'를 찾을 수 없습니다.", status_code=404)

    session = ScanSession(id=_new_session_id(), robot_id=data.robot_id, shelf_id=data.shelf_id)
    db.add(session)
    db.commit()
    return _to_read(db, session)


def finish_session(db: Session, session_id: str, data: ScanSessionFinish, robot_id: str) -> ScanSessionRead:
    """세션을 맡은 로봇만 종료를 보고할 수 있다."""
    session = get_session(db, session_id)
    if session.robot_id != robot_id:
        raise AppError(ErrorCode.FORBIDDEN, f"이 세션은 로봇 '{session.robot_id}'의 세션입니다.", status_code=403)
    if session.status is not ScanSessionStatus.IN_PROGRESS:
        raise AppError(ErrorCode.INVALID_STATE, f"이미 종료된 세션입니다 ({session.status}).", status_code=409)

    session.status = data.status
    session.finished_at = utcnow()
    db.commit()
    return _to_read(db, session)


def list_sessions(db: Session, shelf_id: str | None, limit: int, offset: int) -> tuple[list[ScanSessionRead], int]:
    stmt = select(ScanSession)
    if shelf_id:
        stmt = stmt.where(ScanSession.shelf_id == shelf_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    sessions = db.scalars(stmt.order_by(ScanSession.started_at.desc()).limit(limit).offset(offset))
    return [_to_read(db, s) for s in sessions], total


def get_session_read(db: Session, session_id: str) -> ScanSessionRead:
    return _to_read(db, get_session(db, session_id))
