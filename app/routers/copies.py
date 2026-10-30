"""개별 복본 API (대출 상태, 등록 위치 변경, 위치 변경 이력)."""
from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.core.enums import LoanStatus, PhysicalLocationType
from app.core.ids import BOOK_COPY_ID_PATTERN, SHELF_ID_PATTERN
from app.core.security import require_admin
from app.db.session import get_db
from app.schemas.book import CopyRead, LoanStatusUpdate, LocationHistoryRead, RegisteredLocationUpdate
from app.schemas.common import Page
from app.services import book as book_service

router = APIRouter(prefix="/copies", tags=["copies"])

CopyIdPath = Path(pattern=BOOK_COPY_ID_PATTERN, examples=["BOOK-125"])


@router.get("", response_model=Page[CopyRead])
def list_copies(
    q: str | None = Query(default=None, max_length=100, description="제목 또는 복본 ID 검색어"),
    shelf_id: str | None = Query(default=None, pattern=SHELF_ID_PATTERN, description="등록 위치 기준 서가"),
    book_id: int | None = None,
    loan_status: LoanStatus | None = None,
    physical_location_type: PhysicalLocationType | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """복본 목록. 예: 바구니에 있는 책 → physical_location_type=BASKET, 대출 중 → loan_status=CHECKED_OUT"""
    filters = {"book_id": book_id, "loan_status": loan_status, "physical_location_type": physical_location_type}
    items, total = book_service.list_copies(db, filters, shelf_id, q, limit, offset)
    return Page[CopyRead](items=[CopyRead.from_model(c) for c in items], total=total, limit=limit, offset=offset)


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
