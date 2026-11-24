"""로봇 사진 업로드·AI OCR 결과 API.

로봇 → POST /scan-sessions/{id}/images (사진 파일 그대로) → 백엔드가 저장하고 AI OCR에 보냄 (AI_OCR_URL 설정 시)
AI   → POST /scan-images/{id}/ocr-result (백엔드가 AI를 부르지 않는 경우, AI가 결과를 직접 보냄)
프론트 → GET /scan-sessions/{id}/images (사진별 OCR 진행 상태), GET /scan-images/{id}/file (사진)
"""

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import AwareDatetime
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_settings
from app.core.enums import DeviceType
from app.core.ids import SCAN_IMAGE_ID_PATTERN, SLOT_ID_PATTERN
from app.core.security import require_admin, require_device
from app.db.session import get_db, get_session_factory
from app.models import DeviceCredential
from app.schemas.scan_image import OcrResult, ScanImageRead
from app.services import scan_image as service
from app.services.observation import image_path
from app.services.scan_session import get_session

router = APIRouter(tags=["scan-images"])


@router.post(
    "/scan-sessions/{session_id}/images",
    response_model=ScanImageRead,
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra={"requestBody": {"required": True, "content": {
        "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
        "image/png": {"schema": {"type": "string", "format": "binary"}}}}},
    responses={200: {"description": "같은 image_id로 이미 올린 사진 (재전송, 다시 저장하지 않음)"}},
)
async def upload_image(
    session_id: str,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    slot_id: str = Query(pattern=SLOT_ID_PATTERN, description="사진을 찍은 칸"),
    captured_at: AwareDatetime = Query(description="촬영 시각, 시간대 필수 (예: 2026-11-23T16:01:00+09:00)"),
    image_id: str | None = Query(default=None, pattern=SCAN_IMAGE_ID_PATTERN,
                                 description="로봇이 정하는 사진 ID (IMG-…). 넣으면 재전송해도 한 번만 저장된다"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    session_factory=Depends(get_session_factory),
    robot: DeviceCredential = Depends(require_device(DeviceType.ROBOT)),
):
    """로봇이 칸 사진 1장을 올린다 (로봇 API 키 필요). 본문은 사진 파일 그대로 (Content-Type: image/jpeg·image/png).

    - 202: 저장함. AI_OCR_URL이 있으면 백엔드가 곧바로 AI OCR을 요청한다 (사진 1장 약 30초).
    - 한 칸을 여러 장으로 찍으면 왼쪽 사진부터 촬영 시각 순서로 올린다. 겹쳐 찍어도 된다 (중복은 백엔드가 합침).
    - 촬영을 다 끝내면 POST /scan-sessions/{id}/finish. 모든 사진의 OCR이 끝나면 백엔드가 분석 완료를 처리한다.
    """
    data = await request.body()
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    image, created = await run_in_threadpool(
        service.save_image, db, settings, session_id, robot.device_id, slot_id, captured_at, content_type, data,
        image_id)
    if not created:
        response.status_code = status.HTTP_200_OK
    elif settings.ai_ocr_url:
        background.add_task(service.run_ocr, session_factory, image.id, settings)
    return image


@router.get("/scan-sessions/{session_id}/images", response_model=list[ScanImageRead])
def list_images(session_id: str, db: Session = Depends(get_db)):
    """세션의 사진과 사진별 OCR 진행 상태 (칸 → 촬영 시각 순)."""
    get_session(db, session_id)
    return service.list_images(db, session_id)


@router.get("/scan-images/{image_id}", response_model=ScanImageRead)
def get_image(image_id: str, db: Session = Depends(get_db)):
    return service.get_image(db, image_id)


@router.get(
    "/scan-images/{image_id}/file",
    response_class=FileResponse,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}}, "description": "사진 파일"}},
)
def get_image_file(image_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    return FileResponse(image_path(service.get_image(db, image_id), settings.image_root))


@router.post("/scan-images/{image_id}/ocr-result", response_model=ScanImageRead,
             dependencies=[Depends(require_device(DeviceType.AI))])
def submit_ocr_result(image_id: str, result: OcrResult, db: Session = Depends(get_db),
                      settings: Settings = Depends(get_settings)):
    """AI가 OCR 결과를 직접 보낸다 (AI API 키 필요). 백엔드가 AI를 부르지 않는 경우(AI_OCR_URL 없음)에 쓴다.

    사진 1장당 1번. 책마다 관측이 만들어지고 매칭·흐름 검사가 이어진다.
    """
    return service.apply_ocr_result(db, service.get_image(db, image_id), result, settings)


@router.post("/scan-images/{image_id}/retry-ocr", response_model=ScanImageRead, status_code=status.HTTP_202_ACCEPTED)
def retry_ocr(image_id: str, background: BackgroundTasks, db: Session = Depends(get_db),
              settings: Settings = Depends(get_settings), session_factory=Depends(get_session_factory),
              _admin: str = Depends(require_admin)):
    """OCR에 실패한 사진을 AI에 다시 보낸다 (관리자)."""
    image = service.reset_for_retry(db, service.get_image(db, image_id), settings)
    background.add_task(service.run_ocr, session_factory, image.id, settings)
    return image
