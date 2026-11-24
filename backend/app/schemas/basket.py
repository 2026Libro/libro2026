"""바구니 보관 도서 API 응답 형식."""
from pydantic import BaseModel, ConfigDict

from app.schemas.common import UtcDatetime


class BasketItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    book_copy_id: str
    robot_id: str
    task_id: str
    stored_at: UtcDatetime
    collected_at: UtcDatetime | None  # null = 아직 바구니에 있음
    collected_by: str | None
