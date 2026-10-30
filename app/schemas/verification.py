"""위치 검증 결과 API 응답 형식."""
from pydantic import BaseModel, ConfigDict

from app.core.enums import IdentificationStatus, LocationVerdict, ObservationStatus, WorkflowStatus
from app.schemas.common import UtcDatetime


class VerificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scan_session_id: str
    observation_id: str | None        # 미관측 결과면 null
    book_copy_id: str | None          # 복본을 확정하지 못했으면 null
    identification_status: IdentificationStatus | None
    location_verdict: LocationVerdict
    observation_status: ObservationStatus
    workflow_status: WorkflowStatus | None  # null = 처리할 일 없음
    registered_slot_id: str | None
    observed_slot_id: str | None
    reobservation_count: int
    note: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class UnobservedCheckResult(BaseModel):
    scan_session_id: str
    observation_status: ObservationStatus   # NOT_OBSERVED(스캔 완료) 또는 SCAN_INCOMPLETE(스캔 중단)
    registered_copy_count: int              # 이 서가에 등록된 복본 수
    unobserved_copy_count: int              # 그중 이번 스캔에서 한 번도 보이지 않은 복본 수
    review_required_count: int              # 그중 관리자 검토가 필요한 수 (대출 중 등은 제외)
    checked_at: UtcDatetime
