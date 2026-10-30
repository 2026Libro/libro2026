"""대시보드 API."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.schemas.dashboard import DashboardSummary
from app.services import dashboard as dashboard_service

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/summary", response_model=DashboardSummary)
def summary(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    """대시보드 첫 화면용 현황. 세부 목록은 각 API(/verifications, /alerts, /tasks …)로 조회한다."""
    return dashboard_service.get_summary(db, settings.robot_offline_after_seconds)
