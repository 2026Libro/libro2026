"""칸별 청구기호 범위표 API 형식."""
from pydantic import BaseModel, ConfigDict, Field

from app.core.ids import SlotId
from app.schemas.common import UtcDatetime


class CallRangeUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room: str = Field(min_length=1, max_length=50, examples=["대출실"],
                      description="자료실. '고양캠퍼스 ' 생략 가능 (복본의 room과 같은 자료실끼리만 비교)")
    start_call_number: str = Field(min_length=1, max_length=100, examples=["811.16 김54ㄱ"],
                                   description="이 칸 맨 왼쪽 책의 청구기호 (포함)")
    end_call_number: str = Field(min_length=1, max_length=100, examples=["811.16 박99ㅎ"],
                                 description="이 칸 맨 오른쪽 책의 청구기호 (포함). 복본·권차를 안 적으면 그 책 전체 포함")
    note: str | None = Field(default=None, max_length=500)


class CallRangeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slot_id: str
    room: str
    start_call_number: str
    end_call_number: str
    note: str | None
    updated_by: str
    updated_at: UtcDatetime


class CallRangeBulkItem(CallRangeUpsert):
    slot_id: SlotId


class CallRangeBulk(BaseModel):
    """여러 칸 범위를 한 번에 저장 (전부 성공하거나 전부 실패)."""

    model_config = ConfigDict(extra="forbid")

    ranges: list[CallRangeBulkItem] = Field(min_length=1, max_length=2000)


class CallRangeHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slot_id: str
    room: str | None
    old_start: str | None
    old_end: str | None
    new_start: str | None
    new_end: str | None
    changed_by: str
    changed_at: UtcDatetime


class LocateResult(BaseModel):
    call_number: str
    room: str
    slot_id: str | None = Field(description="이 청구기호가 들어갈 칸. 어느 범위에도 없으면 null")


class ApplyResult(BaseModel):
    """범위표 적용 결과 (복본 수)."""

    dry_run: bool
    assigned: int = Field(description="등록 위치가 없던 복본에 칸을 새로 지정")
    moved: int = Field(description="범위표로 정한 칸이 바뀜")
    unchanged: int
    removed: int = Field(description="범위표로 정했던 칸이 이제 어느 범위에도 없어 등록 위치를 지움")
    kept_manual: int = Field(description="관리자가 직접 지정한 위치라 그대로 둠")
    uncovered: int = Field(description="어느 범위에도 들지 않아 칸을 정하지 못함")
    no_call_number: int = Field(description="청구기호가 없어 계산 못 함")
