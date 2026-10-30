"""스캔 세션, AI 관측 결과, 식별 후보, 식별·위치 검증 결과."""
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    IdentificationStatus,
    LocationVerdict,
    ObservationStatus,
    ScanSessionStatus,
    WorkflowStatus,
)
from app.db.base import Base, TimestampMixin, UtcDateTime, enum_type, utcnow


class ScanSession(TimestampMixin, Base):
    """서가 1개를 촬영하는 단위.

    [가정] 세션 1개 = 서가 1개 전체. COMPLETED일 때만 미관측(NOT_OBSERVED)을 판정한다.
    칸 단위 촬영 범위가 필요하면 로봇 담당자와 협의 후 확장한다.
    """

    __tablename__ = "scan_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # SCAN-001
    robot_id: Mapped[str] = mapped_column(ForeignKey("robots.id"), index=True)
    shelf_id: Mapped[str] = mapped_column(ForeignKey("shelves.id"), index=True)
    status: Mapped[ScanSessionStatus] = mapped_column(
        enum_type(ScanSessionStatus), default=ScanSessionStatus.IN_PROGRESS
    )
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # AI가 분석 완료를 알려 미관측 판정을 마친 시각. 한 세션에 한 번만 판정한다.
    unobserved_checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class Observation(Base):
    """AI 인식 결과 1건. id(observation_id)가 기본키이므로 같은 관측이 두 번 저장되지 않는다."""

    __tablename__ = "observations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # OBS-001
    scan_session_id: Mapped[str] = mapped_column(ForeignKey("scan_sessions.id"), index=True)
    robot_id: Mapped[str] = mapped_column(ForeignKey("robots.id"))
    shelf_id: Mapped[str] = mapped_column(ForeignKey("shelves.id"))
    slot_id: Mapped[str] = mapped_column(ForeignKey("shelf_slots.id"), index=True)  # 관측 위치
    detected_text: Mapped[str | None] = mapped_column(String(500))
    reported_book_copy_id: Mapped[str | None] = mapped_column(ForeignKey("book_copies.id"))  # AI가 보낸 값 그대로
    detection_confidence: Mapped[float]
    ocr_confidence: Mapped[float]
    identification_confidence: Mapped[float]
    reobservation_of: Mapped[str | None] = mapped_column(ForeignKey("observations.id"), index=True)
    image_ref: Mapped[str | None] = mapped_column(String(500))
    captured_at: Mapped[datetime] = mapped_column(UtcDateTime)
    received_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    # 원본 JSON 보관. 아직 합의되지 않은 필드가 바뀌어도 원본을 다시 확인할 수 있다.
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON)

    candidates: Mapped[list["ObservationCandidate"]] = relationship(
        back_populates="observation", order_by="ObservationCandidate.rank"
    )


class ObservationCandidate(Base):
    """AI가 제시한 식별 후보. 점수만으로 복본을 확정하지 않는다."""

    __tablename__ = "observation_candidates"
    __table_args__ = (UniqueConstraint("observation_id", "book_copy_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    observation_id: Mapped[str] = mapped_column(ForeignKey("observations.id"))
    book_copy_id: Mapped[str] = mapped_column(ForeignKey("book_copies.id"), index=True)
    score: Mapped[float]
    rank: Mapped[int]  # 1 = 최고 점수

    observation: Mapped[Observation] = relationship(back_populates="candidates")


class VerificationResult(TimestampMixin, Base):
    """도서 식별 + 위치 검증 결과.

    - 관측된 도서: observation_id가 있다.
    - 미관측 도서: observation_id가 NULL이고 observation_status=NOT_OBSERVED (분실 처리 아님).
    - 위치 판정(location_verdict)과 처리 흐름(workflow_status)은 별도 컬럼이다.
    """

    __tablename__ = "verification_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scan_session_id: Mapped[str] = mapped_column(ForeignKey("scan_sessions.id"), index=True)
    # 관측 1건당 검증 결과 1건 (미관측 행은 NULL이라 여러 개 가능)
    observation_id: Mapped[str | None] = mapped_column(ForeignKey("observations.id"), unique=True)
    book_copy_id: Mapped[str | None] = mapped_column(ForeignKey("book_copies.id"), index=True)  # 식별 실패면 NULL

    identification_status: Mapped[IdentificationStatus | None] = mapped_column(enum_type(IdentificationStatus))
    location_verdict: Mapped[LocationVerdict] = mapped_column(
        enum_type(LocationVerdict), default=LocationVerdict.UNDETERMINED
    )
    observation_status: Mapped[ObservationStatus] = mapped_column(enum_type(ObservationStatus))
    workflow_status: Mapped[WorkflowStatus | None] = mapped_column(enum_type(WorkflowStatus))

    # 판정 당시 값 스냅샷 (등록 위치가 나중에 바뀌어도 당시 근거를 남긴다)
    registered_slot_id: Mapped[str | None] = mapped_column(ForeignKey("shelf_slots.id"))
    observed_slot_id: Mapped[str | None] = mapped_column(ForeignKey("shelf_slots.id"))

    reobservation_count: Mapped[int] = mapped_column(default=0)
    note: Mapped[str | None] = mapped_column(Text)
