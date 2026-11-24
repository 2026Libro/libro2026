"""관리자 알림(alerts)과 관리자 처리 이력(admin_actions)."""
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import AdminActionType, AlertStatus, AlertType
from app.db.base import Base, TimestampMixin, UtcDateTime, enum_type, utcnow


class Alert(TimestampMixin, Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alert_type: Mapped[AlertType] = mapped_column(enum_type(AlertType), index=True)
    status: Mapped[AlertStatus] = mapped_column(enum_type(AlertStatus), default=AlertStatus.OPEN, index=True)
    message: Mapped[str] = mapped_column(String(500))

    # 알림 대상 (해당하는 것만 채운다)
    book_copy_id: Mapped[str | None] = mapped_column(ForeignKey("book_copies.id"))
    verification_id: Mapped[int | None] = mapped_column(ForeignKey("verification_results.id"))
    robot_id: Mapped[str | None] = mapped_column(ForeignKey("robots.id"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("robot_tasks.id"))

    acknowledged_by: Mapped[str | None] = mapped_column(String(100))  # 확인·해결한 사람 ("system" = 자동 해결)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    # 새로 만든 검증 결과처럼 아직 id가 없는 대상에도 연결할 수 있도록 관계를 둔다.
    verification: Mapped["VerificationResult | None"] = relationship("VerificationResult")  # noqa: F821
    task: Mapped["RobotTask | None"] = relationship("RobotTask")  # noqa: F821


class AdminAction(Base):
    """관리자가 한 모든 확인·승인·거절·취소 기록. 수정·삭제하지 않고 추가만 한다."""

    __tablename__ = "admin_actions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    admin_id: Mapped[str] = mapped_column(String(100), index=True)  # [가정] 관리자 계정 체계는 인증 구현 시 확정
    action_type: Mapped[AdminActionType] = mapped_column(enum_type(AdminActionType))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("robot_tasks.id"))
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alerts.id"))
    book_copy_id: Mapped[str | None] = mapped_column(ForeignKey("book_copies.id"))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
