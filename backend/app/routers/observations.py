"""AI 관측 결과 API."""
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.enums import DeviceType
from app.core.ids import SHELF_ID_PATTERN, SLOT_ID_PATTERN
from app.core.security import require_device
from app.db.session import get_db
from app.schemas.common import Page
from app.schemas.observation import ObservationCreate, ObservationRead
from app.services import observation as observation_service
from app.services.verification import Thresholds

router = APIRouter(prefix="/observations", tags=["observations"])


@router.post(
    "",
    response_model=ObservationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_device(DeviceType.AI))],
    responses={200: {"description": "같은 내용의 관측이 이미 저장되어 있음 (재전송). 새로 저장하지 않았다."}},
)
def receive_observation(
    data: ObservationCreate,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """AI 인식 결과 수신 (AI 장치 API 키 필요). 저장과 동시에 식별·위치 검증을 한다 (결과: GET /verifications).

    - 201: 새로 저장함
    - 200: 같은 observation_id·같은 내용이 이미 있음 (재전송으로 간주, 중복 저장 안 함)
    - 409: 같은 observation_id인데 내용이 다름
    """
    obs, created = observation_service.receive_observation(db, data, Thresholds.from_settings(settings))
    if not created:
        response.status_code = status.HTTP_200_OK
    return obs


@router.get("", response_model=Page[ObservationRead])
def list_observations(
    scan_session_id: str | None = Query(default=None),
    shelf_id: str | None = Query(default=None, pattern=SHELF_ID_PATTERN),
    slot_id: str | None = Query(default=None, pattern=SLOT_ID_PATTERN),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = observation_service.list_observations(db, scan_session_id, shelf_id, slot_id, limit, offset)
    return Page[ObservationRead](items=items, total=total, limit=limit, offset=offset)


@router.get("/{observation_id}", response_model=ObservationRead)
def get_observation(observation_id: str, db: Session = Depends(get_db)):
    return observation_service.get_observation(db, observation_id)


@router.get(
    "/{observation_id}/image",
    response_class=FileResponse,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}}, "description": "관측 이미지 파일"},
               404: {"description": "이미지가 없거나 서버에 파일이 없음"}},
)
def get_observation_image(observation_id: str, db: Session = Depends(get_db),
                          settings: Settings = Depends(get_settings)):
    """관측 이미지 파일. 응답의 image_url이 이 경로다. [가정] 파일은 IMAGE_ROOT 폴더에서 image_ref 경로로 찾는다."""
    obs = observation_service.get_observation(db, observation_id)
    return FileResponse(observation_service.image_path(obs, settings.image_root))
