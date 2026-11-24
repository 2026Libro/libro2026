"""로봇 작업 규격 (기획서 9장 초안 기준).

- RobotTaskCommand : 백엔드 → 로봇. 로봇이 GET /robots/{id}/tasks/next 로 받아 가는 형식
- TaskAck          : 로봇 → 백엔드. 작업 수락/거절
- TaskResultReport : 로봇 → 백엔드. 진행·완료·실패·취소 보고
- TaskCreate       : 관리자 → 백엔드. 작업 생성

백엔드는 작업을 요청할 뿐, 모터·로봇팔을 직접 제어하지 않는다.

[가정] 로봇 담당자와 협의 전이다. 특히 작업 종류별 필수 항목(_REQUIRED_FIELDS)과
보고 필드는 실제 로봇이 필요로 하는 정보에 맞춰 바뀔 수 있다.
"""
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.core.enums import EventSource, RobotTaskStatus, RobotTaskType
from app.core.ids import BookCopyId, ObservationId, RobotId, ScanSessionId, ShelfId, SlotId, TaskId, slot_belongs_to_shelf
from app.schemas.common import UtcDatetime

_REQUIRED_FIELDS: dict[RobotTaskType, tuple[str, ...]] = {
    RobotTaskType.MOVE_TO_SHELF: ("shelf_id",),
    RobotTaskType.SCAN_SHELF: ("shelf_id", "scan_session_id"),
    RobotTaskType.REOBSERVE_BOOK: ("shelf_id", "slot_id", "reobservation_of"),
    RobotTaskType.RETRIEVE_MISPLACED_BOOK: ("shelf_id", "slot_id", "book_copy_id"),
    RobotTaskType.CANCEL_TASK: ("target_task_id",),
}


class RobotTaskCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: TaskId
    task_type: RobotTaskType
    robot_id: RobotId
    shelf_id: ShelfId | None = None
    slot_id: SlotId | None = None
    book_copy_id: BookCopyId | None = None
    scan_session_id: ScanSessionId | None = None
    reobservation_of: ObservationId | None = None  # 재관측 대상이 된 원래 관측
    target_task_id: TaskId | None = None           # CANCEL_TASK가 취소할 작업
    issued_at: AwareDatetime

    @model_validator(mode="after")
    def _check_required_fields(self) -> "RobotTaskCommand":
        missing = [f for f in _REQUIRED_FIELDS[self.task_type] if getattr(self, f) is None]
        if missing:
            raise ValueError(f"{self.task_type} 작업에는 {', '.join(missing)} 값이 필요합니다.")

        if self.shelf_id and self.slot_id and not slot_belongs_to_shelf(self.slot_id, self.shelf_id):
            raise ValueError(f"slot_id '{self.slot_id}'는 shelf_id '{self.shelf_id}'에 속하지 않습니다.")

        if self.target_task_id == self.task_id:
            raise ValueError("작업이 자기 자신을 취소할 수 없습니다.")
        return self


class TaskCreate(BaseModel):
    """관리자 작업 생성.

    - MOVE_TO_SHELF           : shelf_id
    - SCAN_SHELF              : shelf_id (스캔 세션이 자동으로 만들어진다)
    - REOBSERVE_BOOK          : verification_id (재관측 대기 상태인 검증 결과)
    - RETRIEVE_MISPLACED_BOOK : verification_id (오배가 확정 결과). PENDING_APPROVAL로 만들어지며
                                POST /tasks/{id}/approve 로 승인해야 로봇에 전달된다.
    취소는 POST /tasks/{id}/cancel 을 쓴다.
    """

    model_config = ConfigDict(extra="forbid")

    task_type: Literal[RobotTaskType.MOVE_TO_SHELF, RobotTaskType.SCAN_SHELF, RobotTaskType.REOBSERVE_BOOK,
                       RobotTaskType.RETRIEVE_MISPLACED_BOOK]
    robot_id: RobotId
    shelf_id: ShelfId | None = None
    verification_id: int | None = None

    @model_validator(mode="after")
    def _check_fields(self) -> "TaskCreate":
        if self.task_type in (RobotTaskType.REOBSERVE_BOOK, RobotTaskType.RETRIEVE_MISPLACED_BOOK):
            if self.verification_id is None:
                raise ValueError(f"{self.task_type} 작업에는 verification_id가 필요합니다.")
        elif self.shelf_id is None:
            raise ValueError(f"{self.task_type} 작업에는 shelf_id가 필요합니다.")
        return self


class _RobotReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 같은 보고를 재전송해도 한 번만 처리하기 위한 키. 로봇이 보고마다 새로 만든다 (예: UUID).
    idempotency_key: str = Field(min_length=8, max_length=100)
    reported_at: AwareDatetime


class TaskAck(_RobotReport):
    """로봇은 ack 응답이 ACCEPTED로 온 경우에만 작업을 실행해야 한다 (그 사이 취소됐을 수 있음)."""

    accepted: bool
    reason: str | None = Field(default=None, max_length=500)


class TaskResultReport(_RobotReport):
    status: Literal[RobotTaskStatus.IN_PROGRESS, RobotTaskStatus.COMPLETED,
                    RobotTaskStatus.FAILED, RobotTaskStatus.CANCELLED]
    event_type: str | None = Field(
        default=None, max_length=50,
        description="세부 단계 이름. 예: ARRIVED. 인출 작업은 GRIPPED(파지), STORED_IN_BASKET(바구니 보관)을 보고하면 "
                    "도서 물리 위치가 갱신된다.",
    )
    message: str | None = Field(default=None, max_length=1000)
    payload: dict[str, Any] | None = None


class TaskEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    source: EventSource
    from_status: RobotTaskStatus | None
    to_status: RobotTaskStatus | None
    payload: dict[str, Any] | None
    occurred_at: UtcDatetime


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_type: RobotTaskType
    status: RobotTaskStatus
    robot_id: str | None
    shelf_id: str | None
    slot_id: str | None
    book_copy_id: str | None
    scan_session_id: str | None
    reobservation_of: str | None
    target_task_id: str | None
    verification_id: int | None
    created_by: str
    approved_by: str | None
    approved_at: UtcDatetime | None
    accepted_at: UtcDatetime | None
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None
    result_message: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class TaskReject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500, description="거절 사유 (관리자 처리 이력에 기록)")


class TaskDetail(TaskRead):
    events: list[TaskEventRead]
