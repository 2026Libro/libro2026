"""서가 API."""
from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.orm import Session

from app.core.ids import SHELF_ID_PATTERN
from app.core.security import require_admin
from app.db.session import get_db
from app.schemas.shelf import ShelfCreate, ShelfDetail, ShelfRead
from app.services import shelf as shelf_service

router = APIRouter(prefix="/shelves", tags=["shelves"])


@router.post("", response_model=ShelfRead, status_code=status.HTTP_201_CREATED)
def create_shelf(data: ShelfCreate, db: Session = Depends(get_db), _admin: str = Depends(require_admin)):
    """서가와 칸을 함께 만든다."""
    return shelf_service.create_shelf(db, data)


@router.get("", response_model=list[ShelfRead])
def list_shelves(db: Session = Depends(get_db)):
    return shelf_service.list_shelves(db)


@router.get("/{shelf_id}", response_model=ShelfDetail)
def get_shelf(shelf_id: str = Path(pattern=SHELF_ID_PATTERN, examples=["A-03"]), db: Session = Depends(get_db)):
    """서가 상세: 칸별로 등록된 복본 목록 (등록 위치 기준)."""
    return shelf_service.get_shelf_detail(db, shelf_id)
