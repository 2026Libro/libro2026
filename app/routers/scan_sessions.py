"""스캔 세션 API."""
from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.enums import DeviceType
from app.core.ids import SHELF_ID_PATTERN, SLOT_ID_PATTERN
from app.core.errors import AppError
from app.core.security import require_admin_or_robot, require_device
from app.db.session import get_db
from app.models import DeviceCredential
from app.schemas.common import ErrorCode, Page
from app.schemas.scan_session import ScanSessionCreate, ScanSessionFinish, ScanSessionRead
from app.schemas.verification import ShelfOrderCheck, SlotOrderCheck, UnobservedCheckResult
from app.services import scan_image as scan_image_service
from app.services import scan_session as scan_service
from app.services import shelf_order as shelf_order_service
from app.services import verification as verification_service

router = APIRouter(prefix="/scan-sessions", tags=["scan-sessions"])


@router.post("", response_model=ScanSessionRead, status_code=status.HTTP_201_CREATED)
def create_session(data: ScanSessionCreate, db: Session = Depends(get_db),
                   caller: tuple[str, str] = Depends(require_admin_or_robot)):
    """서가 1개 촬영 세션 시작 (관리자 토큰 또는 로봇 API 키). 응답의 id로 사진을 올린다.

    로봇 키로 만들 때는 robot_id가 키의 로봇과 같아야 한다 (자기 세션만 열 수 있음).
    """
    kind, who = caller
    if kind == "ROBOT" and data.robot_id != who:
        raise AppError(ErrorCode.FORBIDDEN, f"로봇 '{who}'의 키로 로봇 '{data.robot_id}'의 세션을 만들 수 없습니다.",
                       status_code=403)
    return scan_service.create_session(db, data)


@router.post("/{session_id}/finish", response_model=ScanSessionRead)
def finish_session(
    session_id: str,
    data: ScanSessionFinish,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    robot: DeviceCredential = Depends(require_device(DeviceType.ROBOT)),
):
    """로봇이 촬영 종료를 보고한다. COMPLETED여야 6단계에서 미관측 판정을 한다.

    사진을 백엔드에 올린 세션(POST /scan-sessions/{id}/images)은 사진 OCR이 모두 끝나 있으면 여기서 바로,
    아니면 마지막 사진 OCR이 끝날 때 백엔드가 분석 완료(미관측 판정·흐름 검사)를 처리한다.
    """
    result = scan_service.finish_session(db, session_id, data, robot_id=robot.device_id)
    scan_image_service.complete_if_ready(db, session_id, settings)
    return result


@router.post(
    "/{session_id}/analysis-complete",
    response_model=UnobservedCheckResult,
    dependencies=[Depends(require_device(DeviceType.AI))],
)
def analysis_complete(session_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    """AI가 이 세션의 관측을 모두 보냈음을 알린다 → 미관측 판정 (세션당 1회).

    [가정] AI 분석이 로봇 촬영보다 늦게 끝날 수 있어, 로봇의 촬영 종료(finish)가 아니라
    AI의 분석 완료 신호를 기준으로 미관측을 판정한다. AI 담당자와 협의 필요.
    로봇이 촬영을 끝내기(finish) 전에는 호출할 수 없다.
    """
    return verification_service.check_unobserved(db, session_id, settings.loan_status_reliable)


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


@router.get("/{session_id}/order-check", response_model=ShelfOrderCheck)
def shelf_order_check(session_id: str, db: Session = Depends(get_db)):
    """서가 전체 청구기호 흐름 검사. 칸 순서 → 사진 순서 → 사진 안 순번으로 이어 붙여 엉뚱한 책을 찾는다.

    - OUT_OF_PLACE: 엉뚱한 책 (흐름상 앞·뒤 책과 분류번호 앞 3자리가 다름)
    - OUT_OF_ORDER: 순서만 어긋남 (같은 분류 근처)
    - UNIDENTIFIED: 청구기호를 알 수 없어 검사 제외
    관측에 image_ref와 order가 있어야 검사 대상이 된다. 요청할 때마다 최신 관측으로 계산한다.
    """
    return shelf_order_service.check_shelf_order(db, session_id)


@router.get("/{session_id}/slots/{slot_id}/order-check", response_model=SlotOrderCheck)
def slot_order_check(session_id: str, slot_id: str = Path(pattern=SLOT_ID_PATTERN, examples=["A-01-03"]),
                     db: Session = Depends(get_db)):
    """칸 하나의 청구기호 흐름 검사 결과. 판정은 서가 전체 흐름으로 하고 이 칸의 책만 보여 준다."""
    return shelf_order_service.check_slot_order(db, session_id, slot_id)
