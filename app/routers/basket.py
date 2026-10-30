"""바구니 보관 도서 API (인출된 도서 → 직원 회수)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.ids import ROBOT_ID_PATTERN
from app.core.security import require_admin
from app.db.session import get_db
from app.schemas.basket import BasketItemRead
from app.services import retrieval as retrieval_service

router = APIRouter(prefix="/basket-items", tags=["basket"])


@router.get("", response_model=list[BasketItemRead])
def list_basket(
    robot_id: str | None = Query(default=None, pattern=ROBOT_ID_PATTERN),
    include_collected: bool = Query(default=False, description="true면 이미 회수한 기록도 포함"),
    db: Session = Depends(get_db),
):
    """로봇 바구니에 있는 도서 목록 (기본: 아직 회수하지 않은 것만)."""
    return retrieval_service.list_basket(db, robot_id, include_collected)


@router.post("/{item_id}/collect", response_model=BasketItemRead)
def collect(item_id: int, db: Session = Depends(get_db), admin_id: str = Depends(require_admin)):
    """직원이 바구니에서 도서를 꺼냄. 도서 물리 위치가 COLLECTED_BY_STAFF가 되고 처리 흐름이 닫힌다."""
    return retrieval_service.collect(db, item_id, admin_id)
