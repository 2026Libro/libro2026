"""서가(shelves)와 서가 칸(shelf_slots).

[가정] 서가 ID 'A-03' = 구역 A의 3번 서가, 칸 ID 'A-03-02' = 그 서가의 2번 칸.
기획서 테이블 목록에는 shelves만 있으나, 도서 위치를 칸 단위로 관리하기 위해 shelf_slots를 추가했다.
"""
from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UtcDateTime, utcnow


class Shelf(TimestampMixin, Base):
    __tablename__ = "shelves"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # A-03
    zone: Mapped[str] = mapped_column(String(8))                    # A
    name: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500))

    slots: Mapped[list["ShelfSlot"]] = relationship(back_populates="shelf", order_by="ShelfSlot.position")


class ShelfSlot(TimestampMixin, Base):
    __tablename__ = "shelf_slots"
    __table_args__ = (UniqueConstraint("shelf_id", "position"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # A-03-02
    shelf_id: Mapped[str] = mapped_column(ForeignKey("shelves.id"))
    position: Mapped[int]                                           # 2

    shelf: Mapped[Shelf] = relationship(back_populates="slots")


class SlotCallRange(TimestampMixin, Base):
    """칸별 청구기호 범위 기준표. 칸 1개 = 범위 1개 (시작·끝 모두 포함).

    도서관은 자료실 안에서 청구기호 순으로 책을 꽂는다. 이 범위로 책마다 '있어야 할 칸'(등록 위치)을 계산한다.
    값은 현장 조사·도서관 배치도에서 온다. [가정] 한 칸에 서로 떨어진 범위 두 개가 있는 경우는 없다.
    """

    __tablename__ = "slot_call_ranges"

    slot_id: Mapped[str] = mapped_column(ForeignKey("shelf_slots.id"), primary_key=True)
    room: Mapped[str] = mapped_column(String(50), index=True)     # 고양캠퍼스 대출실 (book_copies.room과 같은 값)
    start_call_number: Mapped[str] = mapped_column(String(100))
    end_call_number: Mapped[str] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(String(500))
    updated_by: Mapped[str] = mapped_column(String(100))


class SlotCallRangeHistory(Base):
    """범위표 변경 이력 (새로 지정·수정·삭제)."""

    __tablename__ = "slot_call_range_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slot_id: Mapped[str] = mapped_column(ForeignKey("shelf_slots.id"), index=True)
    room: Mapped[str | None] = mapped_column(String(50))
    old_start: Mapped[str | None] = mapped_column(String(100))
    old_end: Mapped[str | None] = mapped_column(String(100))
    new_start: Mapped[str | None] = mapped_column(String(100))    # 삭제면 NULL
    new_end: Mapped[str | None] = mapped_column(String(100))
    changed_by: Mapped[str] = mapped_column(String(100))
    changed_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
