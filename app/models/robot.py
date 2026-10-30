"""로봇, 로봇 작업, 작업 단계별 이벤트, 바구니 보관 정보."""
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Computed, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import EventSource, RobotConnectionStatus, RobotTaskStatus, RobotTaskType
from app.db.base import Base, TimestampMixin, UtcDateTime, enum_type, utcnow

# 아직 끝나지 않은 작업 상태. UNKNOWN(결과 불명)도 포함해야 같은 도서에 인출을 또 보내지 않는다.
ACTIVE_TASK_STATUSES = (
    RobotTaskStatus.PENDING_APPROVAL,
    RobotTaskStatus.PENDING,
    RobotTaskStatus.ACCEPTED,
    RobotTaskStatus.IN_PROGRESS,
    RobotTaskStatus.UNKNOWN,
)

_ACTIVE_RETRIEVAL_EXPR = (
    f"CASE WHEN task_type = '{RobotTaskType.RETRIEVE_MISPLACED_BOOK}' "
    f"AND status IN ({', '.join(repr(s.value) for s in ACTIVE_TASK_STATUSES)}) "
    "THEN book_copy_id END"
)


class Robot(TimestampMixin, Base):
    __tablename__ = "robots"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # R001
    name: Mapped[str | None] = mapped_column(String(100))
    connection_status: Mapped[RobotConnectionStatus] = mapped_column(
        enum_type(RobotConnectionStatus), default=RobotConnectionStatus.OFFLINE
    )
    battery_percent: Mapped[int | None]
    # [가정] 위치 표현(서가 ID, 좌표 등)이 미정이라 JSON으로 받는다.
    current_location: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reported_state: Mapped[str | None] = mapped_column(String(50))  # 로봇이 보고한 작업 상태 문자열 [가정]
    last_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class RobotTask(TimestampMixin, Base):
    """로봇 작업 1건. 인출 + 바구니 보관은 RETRIEVE_MISPLACED_BOOK 하나로 관리하고 단계는 task_events에 남긴다."""

    __tablename__ = "robot_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # TASK-...
    task_type: Mapped[RobotTaskType] = mapped_column(enum_type(RobotTaskType))
    status: Mapped[RobotTaskStatus] = mapped_column(enum_type(RobotTaskStatus), index=True)
    robot_id: Mapped[str | None] = mapped_column(ForeignKey("robots.id"), index=True)

    shelf_id: Mapped[str | None] = mapped_column(ForeignKey("shelves.id"))
    slot_id: Mapped[str | None] = mapped_column(ForeignKey("shelf_slots.id"))
    book_copy_id: Mapped[str | None] = mapped_column(ForeignKey("book_copies.id"), index=True)
    scan_session_id: Mapped[str | None] = mapped_column(ForeignKey("scan_sessions.id"))
    reobservation_of: Mapped[str | None] = mapped_column(ForeignKey("observations.id"))
    target_task_id: Mapped[str | None] = mapped_column(ForeignKey("robot_tasks.id"))  # CANCEL_TASK 대상

    created_by: Mapped[str] = mapped_column(String(100))  # "system" 또는 관리자 ID
    approved_by: Mapped[str | None] = mapped_column(String(100))
    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)  # 수락 ≠ 완료
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    result_message: Mapped[str | None] = mapped_column(Text)

    # 동시에 두 요청이 같은 작업 상태를 바꾸면 나중 요청이 실패하도록 하는 버전 번호 (BE-017)
    version: Mapped[int] = mapped_column(default=1)

    # 같은 도서에 진행 중인 인출 작업이 2개 이상 생기지 않도록 DB에서 막는다 (원칙 12).
    # 활성 인출 작업일 때만 book_copy_id 값이 들어가고, 나머지는 NULL(중복 허용)이다.
    active_retrieval_book_copy_id: Mapped[str | None] = mapped_column(
        String(32), Computed(_ACTIVE_RETRIEVAL_EXPR, persisted=True), unique=True
    )

    __mapper_args__ = {"version_id_col": version}


class TaskEvent(Base):
    """작업 단계별 이벤트 (생성, 승인, 수락, 그리퍼 파지, 바구니 보관, 완료, 실패 등)."""

    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("robot_tasks.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(50))  # [가정] 이벤트 이름은 로봇 담당자와 협의
    source: Mapped[EventSource] = mapped_column(enum_type(EventSource))
    from_status: Mapped[RobotTaskStatus | None] = mapped_column(enum_type(RobotTaskStatus))
    to_status: Mapped[RobotTaskStatus | None] = mapped_column(enum_type(RobotTaskStatus))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # 로봇이 같은 보고를 재전송해도 한 번만 저장하기 위한 키
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime)  # 실제 발생 시각 (로봇 보고 기준)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class BasketItem(Base):
    """로봇 바구니에 보관된 도서. 직원이 회수하면 collected_at이 채워진다."""

    __tablename__ = "basket_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    book_copy_id: Mapped[str] = mapped_column(ForeignKey("book_copies.id"), index=True)
    robot_id: Mapped[str] = mapped_column(ForeignKey("robots.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("robot_tasks.id"))
    stored_at: Mapped[datetime] = mapped_column(UtcDateTime)
    collected_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    collected_by: Mapped[str | None] = mapped_column(String(100))

    # 회수되지 않은 같은 도서가 바구니에 두 번 기록되지 않도록 한다.
    uncollected_book_copy_id: Mapped[str | None] = mapped_column(
        String(32), Computed("CASE WHEN collected_at IS NULL THEN book_copy_id END", persisted=True), unique=True
    )
