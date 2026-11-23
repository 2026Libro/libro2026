"""위치 검증 결과 API 응답 형식."""
from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    IdentificationStatus,
    LocationVerdict,
    ObservationStatus,
    ResultBadge,
    ShelfOrderStatus,
    WorkflowStatus,
)
from app.schemas.common import UtcDatetime


class VerificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scan_session_id: str
    observation_id: str | None        # 미관측 결과면 null
    book_copy_id: str | None          # 복본을 확정하지 못했으면 null
    title: str | None                 # book_copy_id의 도서 제목. 복본 미확정이면 null
    identification_status: IdentificationStatus | None
    location_verdict: LocationVerdict
    observation_status: ObservationStatus
    workflow_status: WorkflowStatus | None  # null = 처리할 일 없음
    sequence_status: ShelfOrderStatus | None  # 청구기호 흐름 검사 (AI 분석 완료 후 기록). null = 검사 전·대상 아님
    badge: ResultBadge                # 화면 대표 판정 (서버 계산)
    registered_slot_id: str | None
    observed_slot_id: str | None
    reobservation_count: int
    # 이 결과를 대체한 재관측 결과의 id. null이면 최신 결과다.
    superseded_by: int | None
    note: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class UnobservedCheckResult(BaseModel):
    scan_session_id: str
    observation_status: ObservationStatus   # NOT_OBSERVED(스캔 완료) 또는 SCAN_INCOMPLETE(스캔 중단)
    registered_copy_count: int              # 이 서가에 등록된 복본 수
    unobserved_copy_count: int              # 그중 이번 스캔에서 한 번도 보이지 않은 복본 수
    review_required_count: int              # 그중 관리자 검토가 필요한 수 (대출 중 등은 제외)
    out_of_place_count: int = 0             # 청구기호 흐름 검사에서 찾은 엉뚱한 책 수 (관리자 검토로 넘김)
    checked_at: UtcDatetime


# ---------- 서가 청구기호 흐름 검사 ----------

class SlotOrderBook(BaseModel):
    order: int = Field(description="사진 안 왼쪽부터 순번 (AI가 보낸 값)")
    observation_id: str
    book_copy_id: str | None = Field(description="식별된 복본. 같은 책의 복본 중 어느 것인지 모르면 null")
    call_number: str | None = Field(description="비교에 쓴 청구기호. 복본을 모르면 복본 기호(c.N)를 뗀 값")
    title: str | None
    bbox: list[int] | None
    status: ShelfOrderStatus
    neighbors: list[str] = Field(default_factory=list,
                                 description="이탈한 책이면 흐름상 앞·뒤 책의 청구기호 (판단 근거)")


class SlotOrderImage(BaseModel):
    image_ref: str
    image_url: str | None = Field(description="이 사진 조회 경로 (GET /observations/{id}/image)")
    image_width: int | None
    image_height: int | None
    out_of_order_count: int
    out_of_place_count: int
    books: list[SlotOrderBook]


class SlotOrderCheck(BaseModel):
    scan_session_id: str
    slot_id: str
    out_of_order_count: int = Field(description="순서만 어긋난 책 수")
    out_of_place_count: int = Field(description="엉뚱한 책 수")
    images: list[SlotOrderImage]


class ShelfOrderCheck(BaseModel):
    scan_session_id: str
    shelf_id: str
    out_of_order_count: int
    out_of_place_count: int
    slots: list[SlotOrderCheck] = Field(description="칸 순서대로. 위치 정보가 있는 관측이 있는 칸만")
