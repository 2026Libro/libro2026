"""관리자 기능 API: 알림, 관리자 처리 이력, 통합 이력."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import AdminActionType, AlertStatus, AlertType
from app.core.ids import BOOK_COPY_ID_PATTERN, ROBOT_ID_PATTERN
from app.core.security import require_admin
from app.db.session import get_db
from app.models import AdminAction
from app.schemas.admin import AdminActionRead, AlertActionRequest, AlertRead, HistoryItem
from app.schemas.common import Page
from app.services import alerts as alert_service
from app.services import history as history_service

router = APIRouter(tags=["admin"])


@router.get("/alerts", response_model=Page[AlertRead])
def list_alerts(
    open_only: bool = Query(default=False, description="true면 아직 해결되지 않은(OPEN·ACKNOWLEDGED) 알림만"),
    status: AlertStatus | None = None,
    alert_type: AlertType | None = None,
    book_copy_id: str | None = Query(default=None, pattern=BOOK_COPY_ID_PATTERN),
    robot_id: str | None = Query(default=None, pattern=ROBOT_ID_PATTERN),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """알림 목록 (최신순). 대시보드에는 open_only=true 를 권장."""
    items, total = alert_service.list_alerts(db, status, alert_type, open_only, book_copy_id, robot_id, limit, offset)
    return Page[AlertRead](items=items, total=total, limit=limit, offset=offset)


@router.post("/alerts/{alert_id}/confirm", response_model=AlertRead)
def confirm_alert(alert_id: int, data: AlertActionRequest | None = None, db: Session = Depends(get_db),
                  admin_id: str = Depends(require_admin)):
    """알림 확인 (OPEN → ACKNOWLEDGED). 원인 처리가 끝나면 /resolve 한다."""
    return alert_service.confirm(db, alert_id, admin_id, data.comment if data else None)


@router.post("/alerts/{alert_id}/resolve", response_model=AlertRead)
def resolve_alert(alert_id: int, data: AlertActionRequest | None = None, db: Session = Depends(get_db),
                  admin_id: str = Depends(require_admin)):
    """알림 처리 완료 (→ RESOLVED). 연결된 검증 결과가 관리자 검토 상태면 함께 닫는다."""
    return alert_service.resolve(db, alert_id, admin_id, data.comment if data else None)


@router.get("/admin-actions", response_model=Page[AdminActionRead])
def list_admin_actions(
    admin_id: str | None = None,
    action_type: AdminActionType | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
):
    """관리자 처리 이력 (승인·거절·취소·확인·회수·위치 변경 등). 관리자만 조회할 수 있다."""
    stmt = select(AdminAction)
    if admin_id:
        stmt = stmt.where(AdminAction.admin_id == admin_id)
    if action_type:
        stmt = stmt.where(AdminAction.action_type == action_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(stmt.order_by(AdminAction.created_at.desc(), AdminAction.id.desc()).limit(limit).offset(offset)))
    return Page[AdminActionRead](items=items, total=total, limit=limit, offset=offset)


@router.get("/history", response_model=list[HistoryItem])
def get_history(
    book_copy_id: str | None = Query(default=None, pattern=BOOK_COPY_ID_PATTERN),
    robot_id: str | None = Query(default=None, pattern=ROBOT_ID_PATTERN),
    task_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """통합 이력 (최신순). 예: 책 한 권의 일생 → book_copy_id=BOOK-201"""
    return history_service.get_history(db, book_copy_id, robot_id, task_id, limit)
