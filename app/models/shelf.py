"""서가(shelves)와 서가 칸(shelf_slots).

[가정] 서가 ID 'A-03' = 구역 A의 3번 서가, 칸 ID 'A-03-02' = 그 서가의 2번 칸.
기획서 테이블 목록에는 shelves만 있으나, 도서 위치를 칸 단위로 관리하기 위해 shelf_slots를 추가했다.
"""
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


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
