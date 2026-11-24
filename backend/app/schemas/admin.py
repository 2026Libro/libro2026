"""관리자 알림·처리 이력·통합 이력 API 형식."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import AdminActionType, AlertStatus, AlertType
from app.schemas.common import UtcDatetime


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_type: AlertType
    status: AlertStatus
    message: str
    book_copy_id: str | None
    verification_id: int | None
    robot_id: str | None
    task_id: str | None
    acknowledged_by: str | None  # "system" = 원인 해소로 자동 해결
    acknowledged_at: UtcDatetime | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class AlertActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: str | None = Field(default=None, max_length=1000, description="처리 내용 메모 (관리자 처리 이력에 기록)")


class AdminActionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    admin_id: str
    action_type: AdminActionType
    task_id: str | None
    alert_id: int | None
    book_copy_id: str | None
    comment: str | None
    created_at: UtcDatetime


HistoryKind = Literal["OBSERVATION", "VERIFICATION", "TASK_EVENT", "ADMIN_ACTION", "LOCATION_CHANGE", "ALERT"]


class HistoryItem(BaseModel):
    at: UtcDatetime
    kind: HistoryKind
    summary: str
    ref_id: str                       # 해당 기록의 ID (kind별 테이블 기준)
    book_copy_id: str | None = None
    robot_id: str | None = None
    task_id: str | None = None
