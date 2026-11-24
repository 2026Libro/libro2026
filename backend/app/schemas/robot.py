"""로봇 API 요청/응답 형식."""
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.core.enums import RobotConnectionStatus
from app.core.ids import RobotId
from app.schemas.common import UtcDatetime


class RobotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: RobotId
    name: str | None = Field(default=None, max_length=100)


class RobotStatusReport(BaseModel):
    """로봇 → 백엔드 주기적 상태 보고 (heartbeat). [가정] 필드는 로봇 담당자와 협의 전."""

    model_config = ConfigDict(extra="forbid")

    battery_percent: int | None = Field(default=None, ge=0, le=100)
    current_location: dict[str, Any] | None = Field(default=None, description="형식 미정. 예: {\"shelf_id\": \"A-03\"}")
    reported_state: str | None = Field(default=None, max_length=50, description="로봇 내부 상태. 예: IDLE, NAVIGATING")
    reported_at: AwareDatetime


class RobotRead(BaseModel):
    id: str
    name: str | None
    connection_status: RobotConnectionStatus  # 마지막 통신 시각 기준으로 계산
    battery_percent: int | None
    current_location: dict[str, Any] | None
    reported_state: str | None
    last_seen_at: UtcDatetime | None


class RobotStatusResponse(BaseModel):
    robot: RobotRead
    # 결과 불명(UNKNOWN) 작업. 로봇은 이 작업들의 실제 결과를 /tasks/{id}/result 로 다시 보고해야 한다.
    tasks_needing_result: list[str]
