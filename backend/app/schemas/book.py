"""도서·복본 API 요청/응답 형식."""

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import LoanStatus, LocationCertainty, PhysicalLocationType
from app.core.ids import BookCopyId, SlotId
from app.models import BookCopy
from app.schemas.common import UtcDatetime


class BookBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    isbn: str | None = Field(default=None, pattern=r"^(\d{9}[\dX]|\d{13})$", description="하이픈 없이 10자리 또는 13자리")
    title: str = Field(min_length=1, max_length=500)
    author: str | None = Field(default=None, max_length=300)
    publisher: str | None = Field(default=None, max_length=200)
    publication_year: str | None = Field(default=None, max_length=10, description="발행연도")
    call_number: str | None = Field(default=None, max_length=100, description="대표 청구기호 (복본 기호 c.N 제외)")


class BookCreate(BookBase):
    pass


class BookUpdate(BookBase):
    """PUT이므로 모든 필드를 보낸 값으로 바꾼다 (보내지 않은 선택 필드는 null이 된다)."""


class BookRead(BookBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    library_bib_id: str | None = Field(description="도서관 서지관리번호 (도서관 자료를 가져온 도서만)")
    created_at: UtcDatetime
    updated_at: UtcDatetime


class CopyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: BookCopyId = Field(description="도서관 등록번호 (예: EM0174130)")
    call_number: str | None = Field(default=None, max_length=100, description="이 복본의 청구기호 (v.N·c.N 포함)")
    room: str | None = Field(default=None, max_length=50, description="자료실 (예: 고양캠퍼스 대출실)")
    slot_id: SlotId | None = Field(default=None, description="등록 위치(원래 있어야 할 칸). 나중에 지정해도 된다.")
    loan_status: LoanStatus = LoanStatus.AVAILABLE


class CopyRead(BaseModel):
    id: str
    book_id: int
    title: str
    call_number: str | None
    room: str | None
    loan_status: LoanStatus
    registered_slot_id: str | None
    physical_location_type: PhysicalLocationType
    physical_slot_id: str | None
    physical_robot_id: str | None
    physical_location_certainty: LocationCertainty
    physical_location_confirmed_at: UtcDatetime | None

    @classmethod
    def from_model(cls, copy: BookCopy) -> "CopyRead":
        return cls(
            id=copy.id,
            book_id=copy.book_id,
            title=copy.book.title,
            call_number=copy.call_number,
            room=copy.room,
            loan_status=copy.loan_status,
            registered_slot_id=copy.registered_location.slot_id if copy.registered_location else None,
            physical_location_type=copy.physical_location_type,
            physical_slot_id=copy.physical_slot_id,
            physical_robot_id=copy.physical_robot_id,
            physical_location_certainty=copy.physical_location_certainty,
            physical_location_confirmed_at=copy.physical_location_confirmed_at,
        )


class BookDetail(BookRead):
    copies: list[CopyRead]


class LoanStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loan_status: LoanStatus


class RegisteredLocationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_id: SlotId
    reason: str = Field(min_length=1, max_length=500, description="변경 사유 (이력에 기록)")


class LocationHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    book_copy_id: str
    from_slot_id: str | None
    to_slot_id: str | None
    changed_by: str
    reason: str | None
    changed_at: UtcDatetime
