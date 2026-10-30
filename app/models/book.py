"""도서 서지 정보, 개별 복본, 등록 위치와 그 변경 이력.

도서 위치는 세 가지로 분리한다.
1. 등록 위치   : book_locations (DB 기준 원래 있어야 할 칸)
2. 관측 위치   : observations.slot_id (특정 스캔에서 AI가 본 칸)
3. 현재 물리 위치: book_copies.physical_* (마지막으로 확인된 실제 보관 위치 + 확정/추정 구분)
"""
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import LoanStatus, LocationCertainty, PhysicalLocationType
from app.db.base import Base, TimestampMixin, UtcDateTime, enum_type, utcnow


class Book(TimestampMixin, Base):
    """서지 정보. 같은 제목의 책이 여러 권이면 복본(BookCopy)이 여러 개다."""

    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    isbn: Mapped[str | None] = mapped_column(String(13), unique=True)  # ISBN 없는 자료도 있어 nullable
    title: Mapped[str] = mapped_column(String(500), index=True)
    author: Mapped[str | None] = mapped_column(String(300))
    publisher: Mapped[str | None] = mapped_column(String(200))
    call_number: Mapped[str | None] = mapped_column(String(100), index=True)  # 청구기호

    copies: Mapped[list["BookCopy"]] = relationship(back_populates="book")


class BookCopy(TimestampMixin, Base):
    """개별 복본. 제목이 같아도 복본 ID로 구분한다."""

    __tablename__ = "book_copies"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # BOOK-125
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), index=True)
    loan_status: Mapped[LoanStatus] = mapped_column(enum_type(LoanStatus), default=LoanStatus.AVAILABLE)

    # 현재 물리 위치. 로봇이 확인하지 않은 위치는 ESTIMATED로 둔다.
    physical_location_type: Mapped[PhysicalLocationType] = mapped_column(
        enum_type(PhysicalLocationType), default=PhysicalLocationType.UNKNOWN
    )
    physical_slot_id: Mapped[str | None] = mapped_column(ForeignKey("shelf_slots.id"))      # SHELF일 때
    physical_robot_id: Mapped[str | None] = mapped_column(ForeignKey("robots.id"))          # GRIPPER/BASKET일 때
    physical_location_certainty: Mapped[LocationCertainty] = mapped_column(
        enum_type(LocationCertainty), default=LocationCertainty.ESTIMATED
    )
    physical_location_confirmed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    book: Mapped[Book] = relationship(back_populates="copies")
    registered_location: Mapped["BookLocation | None"] = relationship(back_populates="book_copy")


class BookLocation(TimestampMixin, Base):
    """등록 위치. 복본 1개당 1행."""

    __tablename__ = "book_locations"

    book_copy_id: Mapped[str] = mapped_column(ForeignKey("book_copies.id"), primary_key=True)
    slot_id: Mapped[str] = mapped_column(ForeignKey("shelf_slots.id"), index=True)

    book_copy: Mapped[BookCopy] = relationship(back_populates="registered_location")


class BookLocationHistory(Base):
    """등록 위치 변경 이력 (관측 결과로 자동 변경하지 않는다)."""

    __tablename__ = "book_location_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    book_copy_id: Mapped[str] = mapped_column(ForeignKey("book_copies.id"), index=True)
    from_slot_id: Mapped[str | None] = mapped_column(ForeignKey("shelf_slots.id"))  # 최초 등록이면 NULL
    to_slot_id: Mapped[str | None] = mapped_column(ForeignKey("shelf_slots.id"))
    changed_by: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str | None] = mapped_column(String(500))
    changed_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
