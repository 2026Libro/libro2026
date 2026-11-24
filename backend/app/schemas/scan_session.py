"""스캔 세션 API 요청/응답 형식."""
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.enums import ScanSessionStatus
from app.core.ids import RobotId, ShelfId
from app.schemas.common import UtcDatetime


class ScanSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    robot_id: RobotId
    shelf_id: ShelfId


class ScanSessionFinish(BaseModel):
    """로봇이 서가 촬영을 끝냈을 때 보고. 중간에 멈췄으면 INCOMPLETE."""

    model_config = ConfigDict(extra="forbid")

    status: Literal[ScanSessionStatus.COMPLETED, ScanSessionStatus.INCOMPLETE]


class ScanSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    robot_id: str
    shelf_id: str
    status: ScanSessionStatus
    started_at: UtcDatetime
    finished_at: UtcDatetime | None
    observation_count: int
