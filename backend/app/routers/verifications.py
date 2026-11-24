"""위치 검증 결과 조회 API."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.enums import IdentificationStatus, LocationVerdict, ObservationStatus, WorkflowStatus
from app.core.ids import BOOK_COPY_ID_PATTERN, SLOT_ID_PATTERN
from app.db.session import get_db
from app.schemas.common import Page
from app.schemas.verification import VerificationRead
from app.services import verification as verification_service

router = APIRouter(prefix="/verifications", tags=["verifications"])


@router.get("", response_model=Page[VerificationRead])
def list_verifications(
    scan_session_id: str | None = Query(default=None),
    observation_id: str | None = Query(default=None),
    book_copy_id: str | None = Query(default=None, pattern=BOOK_COPY_ID_PATTERN),
    slot_id: str | None = Query(default=None, pattern=SLOT_ID_PATTERN,
                                description="칸 필터. 관측된 결과는 관측 칸, 미관측 결과는 등록 칸 기준"),
    latest_only: bool = Query(default=False, description="true면 재관측·늦은 관측으로 대체된 결과(superseded_by 있음)를 뺀다"),
    identification_status: IdentificationStatus | None = None,
    location_verdict: LocationVerdict | None = None,
    observation_status: ObservationStatus | None = None,
    workflow_status: WorkflowStatus | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """최신순. 예: 오배가 의심만 → location_verdict=MISPLACED_SUSPECTED, 관리자 검토 대상 → workflow_status=REVIEW_REQUIRED

    화면용 권장: scan_session_id=…&slot_id=…&latest_only=true
    """
    filters = {
        "scan_session_id": scan_session_id,
        "observation_id": observation_id,
        "book_copy_id": book_copy_id,
        "identification_status": identification_status,
        "location_verdict": location_verdict,
        "observation_status": observation_status,
        "workflow_status": workflow_status,
    }
    items, total = verification_service.list_verifications(db, filters, limit, offset, slot_id, latest_only)
    return Page[VerificationRead](items=items, total=total, limit=limit, offset=offset)


@router.get("/{verification_id}", response_model=VerificationRead)
def get_verification(verification_id: int, db: Session = Depends(get_db)):
    return verification_service.get_verification(db, verification_id)
