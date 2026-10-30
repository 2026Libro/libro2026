"""개별 복본 API (대출 상태, 등록 위치 변경, 위치 변경 이력)."""
from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.core.ids import BOOK_COPY_ID_PATTERN
from app.core.security import require_admin
from app.db.session import get_db
from app.schemas.book import CopyRead, LoanStatusUpdate, LocationHistoryRead, RegisteredLocationUpdate
from app.services import book as book_service

router = APIRouter(prefix="/copies", tags=["copies"])

CopyIdPath = Path(pattern=BOOK_COPY_ID_PATTERN, examples=["BOOK-125"])


@router.get("/{copy_id}", response_model=CopyRead)
def get_copy(copy_id: str = CopyIdPath, db: Session = Depends(get_db)):
    return CopyRead.from_model(book_service.get_copy(db, copy_id))


@router.put("/{copy_id}/loan-status", response_model=CopyRead)
def update_loan_status(
    data: LoanStatusUpdate,
    copy_id: str = CopyIdPath,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
):
    return CopyRead.from_model(book_service.update_loan_status(db, copy_id, data))


@router.put("/{copy_id}/registered-location", response_model=CopyRead)
def change_registered_location(
    data: RegisteredLocationUpdate,
    copy_id: str = CopyIdPath,
    db: Session = Depends(get_db),
    admin_id: str = Depends(require_admin),
):
    """등록 위치 변경. 이력과 관리자 처리 기록이 남고, 현재 물리 위치는 바뀌지 않는다."""
    return CopyRead.from_model(book_service.change_registered_location(db, copy_id, data, admin_id))


@router.get("/{copy_id}/location-history", response_model=list[LocationHistoryRead])
def get_location_history(copy_id: str = CopyIdPath, db: Session = Depends(get_db)):
    return book_service.get_location_history(db, copy_id)
