"""서가·칸 API 요청/응답 형식."""
from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import LoanStatus
from app.core.ids import ShelfId


class ShelfCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ShelfId
    name: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    slot_count: int = Field(ge=1, le=99, description="칸 수. A-03이면 A-03-01 ~ A-03-NN 칸이 만들어진다.")


class SlotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    position: int


class ShelfRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    zone: str
    name: str | None
    description: str | None
    slots: list[SlotRead]


class RegisteredCopy(BaseModel):
    book_copy_id: str
    book_id: int
    title: str
    loan_status: LoanStatus


class SlotDetail(SlotRead):
    registered_copies: list[RegisteredCopy]


class ShelfDetail(ShelfRead):
    slots: list[SlotDetail]
