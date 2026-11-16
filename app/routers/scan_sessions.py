"""스캔 세션 API."""
from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.core.enums import DeviceType
from app.core.ids import SHELF_ID_PATTERN, SLOT_ID_PATTERN
from app.core.security import require_admin, require_device
from app.db.session import get_db
from app.models import DeviceCredential
from app.schemas.common import Page
from app.schemas.scan_session import ScanSessionCreate, ScanSessionFinish, ScanSessionRead
from app.schemas.verification import SlotOrderCheck, UnobservedCheckResult
from app.services import scan_session as scan_service
from app.services import shelf_order as shelf_order_service
from app.services import verification as verification_service

router = APIRouter(prefix="/scan-sessions", tags=["scan-sessions"])


@router.post("", response_model=ScanSessionRead, status_code=status.HTTP_201_CREATED)
def create_session(data: ScanSessionCreate, db: Session = Depends(get_db), _admin: str = Depends(require_admin)):
    """서가 1개 촬영 세션 시작. 응답의 id를 로봇·AI가 관측 데이터에 scan_session_id로 넣는다."""
    return scan_service.create_session(db, data)


@router.post("/{session_id}/finish", response_model=ScanSessionRead)
def finish_session(
    session_id: str,
    data: ScanSessionFinish,
    db: Session = Depends(get_db),
    robot: DeviceCredential = Depends(require_device(DeviceType.ROBOT)),
):
    """로봇이 촬영 종료를 보고한다. COMPLETED여야 6단계에서 미관측 판정을 한다."""
    return scan_service.finish_session(db, session_id, data, robot_id=robot.device_id)


@router.post(
    "/{session_id}/analysis-complete",
    response_model=UnobservedCheckResult,
    dependencies=[Depends(require_device(DeviceType.AI))],
)
def analysis_complete(session_id: str, db: Session = Depends(get_db)):
    """AI가 이 세션의 관측을 모두 보냈음을 알린다 → 미관측 판정 (세션당 1회).

    [가정] AI 분석이 로봇 촬영보다 늦게 끝날 수 있어, 로봇의 촬영 종료(finish)가 아니라
    AI의 분석 완료 신호를 기준으로 미관측을 판정한다. AI 담당자와 협의 필요.
    로봇이 촬영을 끝내기(finish) 전에는 호출할 수 없다.
    """
    return verification_service.check_unobserved(db, session_id)


@router.get("", response_model=Page[ScanSessionRead])
def list_sessions(
    shelf_id: str | None = Query(default=None, pattern=SHELF_ID_PATTERN),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = scan_service.list_sessions(db, shelf_id, limit, offset)
    return Page[ScanSessionRead](items=items, total=total, limit=limit, offset=offset)


@router.get("/{session_id}", response_model=ScanSessionRead)
def get_session(session_id: str, db: Session = Depends(get_db)):
    return scan_service.get_session_read(db, session_id)


@router.get("/{session_id}/slots/{slot_id}/order-check", response_model=SlotOrderCheck)
def slot_order_check(session_id: str, slot_id: str = Path(pattern=SLOT_ID_PATTERN, examples=["A-01-03"]),
                     db: Session = Depends(get_db)):
    """같은 칸 안 청구기호 순서 검사. 사진마다 왼쪽→오른쪽(order) 순서로 청구기호가 맞는지 본다.

    서가·칸 범위표 없이 쓸 수 있다. 관측에 image_ref와 order가 있어야 검사 대상이 된다.
    순서를 벗어난 책은 OUT_OF_ORDER, 청구기호를 알 수 없는 책은 UNIDENTIFIED(검사 제외).
    """
    return shelf_order_service.check_slot_order(db, session_id, slot_id)
