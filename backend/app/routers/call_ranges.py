"""칸별 청구기호 범위표 API. 조회는 인증 없이, 저장·삭제·적용은 관리자 토큰이 필요하다."""
from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy.orm import Session

from app.core.ids import SHELF_ID_PATTERN, SLOT_ID_PATTERN
from app.core.security import require_admin
from app.db.session import get_db
from app.schemas.call_range import (
    ApplyResult,
    CallRangeBulk,
    CallRangeHistoryRead,
    CallRangeRead,
    CallRangeUpsert,
    LocateResult,
)
from app.services import call_range as range_service

router = APIRouter(prefix="/call-ranges", tags=["call-ranges"])

SlotPath = Path(pattern=SLOT_ID_PATTERN, examples=["A-01-03"])


@router.get("", response_model=list[CallRangeRead])
def list_ranges(
    room: str | None = Query(default=None, max_length=50, description="자료실 ('고양캠퍼스 ' 생략 가능)"),
    shelf_id: str | None = Query(default=None, pattern=SHELF_ID_PATTERN),
    db: Session = Depends(get_db),
):
    """범위표 (자료실별 청구기호 순)."""
    return range_service.list_ranges(db, room, shelf_id)


@router.get("/locate", response_model=LocateResult)
def locate(call_number: str = Query(min_length=1, max_length=100, examples=["811.16 김54ㄱ c.2"]),
           room: str = Query(default="대출실", max_length=50), db: Session = Depends(get_db)):
    """청구기호가 들어갈 칸 찾기 (범위표 기준)."""
    return range_service.locate(db, call_number, room)


@router.get("/history", response_model=list[CallRangeHistoryRead])
def history(slot_id: str | None = Query(default=None, pattern=SLOT_ID_PATTERN),
            limit: int = Query(default=50, ge=1, le=500), db: Session = Depends(get_db)):
    """범위표 변경 이력 (최근 순)."""
    return range_service.range_history(db, slot_id, limit)


@router.put("", response_model=list[CallRangeRead])
def save_many(data: CallRangeBulk, db: Session = Depends(get_db), admin_id: str = Depends(require_admin)):
    """여러 칸 범위를 한 번에 저장 (전부 성공하거나 전부 실패). 저장만 하고 적용은 POST /call-ranges/apply."""
    return range_service.save_ranges(db, [(r.slot_id, r) for r in data.ranges], admin_id)


@router.put("/{slot_id}", response_model=CallRangeRead)
def save_one(data: CallRangeUpsert, slot_id: str = SlotPath, db: Session = Depends(get_db),
             admin_id: str = Depends(require_admin)):
    """칸 1개 범위 저장(새로 지정·수정). 같은 자료실의 다른 칸과 범위가 겹치면 422."""
    return range_service.save_ranges(db, [(slot_id, data)], admin_id)[0]


@router.delete("/{slot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_one(slot_id: str = SlotPath, db: Session = Depends(get_db), admin_id: str = Depends(require_admin)):
    range_service.delete_range(db, slot_id, admin_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/apply", response_model=ApplyResult)
def apply(dry_run: bool = Query(default=False, description="true면 결과만 계산하고 저장하지 않음"),
          db: Session = Depends(get_db), admin_id: str = Depends(require_admin)):
    """범위표대로 복본의 등록 위치를 다시 계산한다.

    - 관리자가 직접 지정한 등록 위치는 그대로 둔다.
    - 위치가 바뀐 복본은 등록 위치 변경 이력에 남는다.
    - 로봇이 확인한 적 없는 복본은 현재 물리 위치도 그 칸으로 '추정'한다.
    """
    return range_service.apply_ranges(db, admin_id, dry_run)
