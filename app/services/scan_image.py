"""로봇 사진 → AI OCR → 관측.

1. save_image: 로봇이 올린 사진을 IMAGE_ROOT/scans/<세션>/<사진ID>.<확장자>에 저장하고 ScanImage를 만든다.
2. run_ocr: (AI_OCR_URL이 있으면 백그라운드에서) AI에 사진을 보내 결과를 받는다.
   AI_OCR_URL이 없으면 AI가 POST /scan-images/{id}/ocr-result로 결과를 보낸다 (apply_ocr_result).
3. apply_ocr_result: 책마다 관측을 만든다. 글자 조각 매칭·검증은 기존 관측 수신(receive_observation)과 같다.
4. complete_if_ready: 로봇이 촬영 종료를 보고했고 사진이 모두 DONE이면 분석 완료(미관측 판정·흐름 검사)를 한다.
   AI가 analysis-complete를 따로 보낼 필요가 없다.
"""
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.enums import OcrStatus, ScanSessionStatus
from app.core.errors import AppError
from app.db.base import utcnow
from app.models import ScanImage, ScanSession, ShelfSlot
from app.schemas.common import ErrorCode
from app.schemas.observation import ObservationCreate
from app.schemas.scan_image import OcrResult
from app.services import ai_ocr
from app.services.observation import receive_observation
from app.services.verification import Thresholds, check_unobserved

log = logging.getLogger(__name__)
CONTENT_TYPES = {"image/jpeg": "jpg", "image/png": "png"}


def image_size(data: bytes) -> tuple[int, int] | None:
    """PNG·JPEG 헤더에서 (가로, 세로)를 읽는다."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker, length = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
            if marker in (0xC0, 0xC1, 0xC2):
                return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
            i += 2 + length
    return None


def get_image(db: Session, image_id: str) -> ScanImage:
    image = db.get(ScanImage, image_id)
    if image is None:
        raise AppError(ErrorCode.NOT_FOUND, f"사진 '{image_id}'를 찾을 수 없습니다.", status_code=404)
    return image


def list_images(db: Session, session_id: str) -> list[ScanImage]:
    return list(db.scalars(select(ScanImage).where(ScanImage.scan_session_id == session_id)
                           .order_by(ScanImage.slot_id, ScanImage.captured_at, ScanImage.id)))


def save_image(db: Session, settings: Settings, session_id: str, robot_id: str, slot_id: str,
               captured_at: datetime, content_type: str, data: bytes, image_id: str | None) -> tuple[ScanImage, bool]:
    """(사진, 새로 저장했는지). 같은 image_id로 다시 올리면 기존 사진을 돌려준다 (로봇 재전송 안전)."""
    if image_id and (existing := db.get(ScanImage, image_id)):
        if existing.scan_session_id != session_id or existing.slot_id != slot_id:
            raise AppError(ErrorCode.DUPLICATE_REQUEST, f"image_id '{image_id}'가 다른 세션·칸 사진으로 이미 있습니다.",
                           status_code=409)
        return existing, False

    session = db.get(ScanSession, session_id)
    if session is None:
        raise AppError(ErrorCode.NOT_FOUND, f"스캔 세션 '{session_id}'를 찾을 수 없습니다.", status_code=404)
    if session.robot_id != robot_id:
        raise AppError(ErrorCode.FORBIDDEN, f"이 세션은 로봇 '{session.robot_id}'의 세션입니다.", status_code=403)
    if session.status is not ScanSessionStatus.IN_PROGRESS:
        raise AppError(ErrorCode.INVALID_STATE, f"이미 종료된 세션입니다 ({session.status}).", status_code=409)
    slot = db.get(ShelfSlot, slot_id)
    if slot is None or slot.shelf_id != session.shelf_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, f"칸 '{slot_id}'는 이 세션의 서가 '{session.shelf_id}'에 없습니다.",
                       status_code=422, details=[{"field": "slot_id", "message": "서가에 없는 칸"}])
    if content_type not in CONTENT_TYPES:
        raise AppError(ErrorCode.VALIDATION_ERROR, "사진은 image/jpeg 또는 image/png로 보내야 합니다.", status_code=415,
                       details=[{"field": "Content-Type", "message": content_type or "(없음)"}])
    if not data:
        raise AppError(ErrorCode.VALIDATION_ERROR, "사진 파일이 비어 있습니다.", status_code=422)
    if len(data) > settings.max_image_bytes:
        raise AppError(ErrorCode.VALIDATION_ERROR, f"사진이 너무 큽니다 (최대 {settings.max_image_bytes // 1024 // 1024}MB).",
                       status_code=413)
    size = image_size(data)
    if size is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "JPEG·PNG 파일로 읽을 수 없습니다.", status_code=422)

    image_id = image_id or f"IMG-{utcnow():%Y%m%d%H%M%S}-{secrets.token_hex(3)}"
    image_ref = f"scans/{session_id}/{image_id}.{CONTENT_TYPES[content_type]}"
    path = Path(settings.image_root) / image_ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    image = ScanImage(id=image_id, scan_session_id=session_id, slot_id=slot_id, image_ref=image_ref,
                      content_type=content_type, image_width=size[0], image_height=size[1],
                      captured_at=captured_at.astimezone(UTC).replace(tzinfo=None))
    db.add(image)
    db.commit()
    return image, True


def apply_ocr_result(db: Session, image: ScanImage, result: OcrResult, settings: Settings) -> ScanImage:
    """책마다 관측을 만든다. 한 사진의 결과는 한 번만 받는다."""
    if image.ocr_status is OcrStatus.DONE:
        raise AppError(ErrorCode.INVALID_STATE, f"사진 '{image.id}'의 OCR 결과를 이미 받았습니다.", status_code=409)
    books = list(result.books)
    if any(b.order is None for b in books):   # 순번이 없으면 박스 x 좌표(없으면 받은 순서)로 왼쪽부터
        books.sort(key=lambda b: b.bbox[0] if b.bbox else 0)
        orders = range(1, len(books) + 1)
    else:
        books.sort(key=lambda b: b.order)
        orders = [b.order for b in books]
    th = Thresholds.from_settings(settings)
    captured = image.captured_at.replace(tzinfo=UTC)
    for order, book in zip(orders, books):
        texts = book.texts()
        confs = [c for _, c in texts if c is not None]
        bbox = book.bbox
        if bbox and image.image_width and image.image_height:   # 사진 밖으로 나간 박스는 사진 안으로 자른다
            x, y = min(bbox[0], image.image_width - 1), min(bbox[1], image.image_height - 1)
            bbox = [x, y, max(1, min(bbox[2], image.image_width - x)), max(1, min(bbox[3], image.image_height - y))]
        data = ObservationCreate(
            observation_id=f"OBS-{image.id.removeprefix('IMG-')}-{order:03d}",
            scan_session_id=image.scan_session_id, robot_id=_robot_of(db, image), shelf_id=_shelf_of(db, image),
            slot_id=image.slot_id, detected_text=" / ".join(t for t, _ in texts)[:500] or None,
            text_fragments=[t for t, _ in texts],
            confidence={"detection": book.detection_confidence if book.detection_confidence is not None
                        else settings.ocr_default_detection,
                        "ocr": round(sum(confs) / len(confs), 3) if confs else (0.0 if not texts else 0.9)},
            image_ref=image.image_ref, order=order, bbox=bbox,
            image_width=image.image_width, image_height=image.image_height, captured_at=captured)
        receive_observation(db, data, th)
    image.ocr_status = OcrStatus.DONE
    image.ocr_error = None
    image.book_count = len(books)
    image.ocr_completed_at = utcnow()
    db.commit()
    complete_if_ready(db, image.scan_session_id, settings)
    return image


def _robot_of(db: Session, image: ScanImage) -> str:
    return db.get(ScanSession, image.scan_session_id).robot_id


def _shelf_of(db: Session, image: ScanImage) -> str:
    return db.get(ScanSession, image.scan_session_id).shelf_id


def run_ocr(session_factory: Callable, image_id: str, settings: Settings) -> None:
    """백그라운드 작업: AI에 사진을 보내고 결과를 적용한다. 실패하면 FAILED로 남긴다 (관리자가 다시 시도)."""
    with session_factory() as db:
        image = db.get(ScanImage, image_id)
        if image is None or image.ocr_status not in (OcrStatus.PENDING, OcrStatus.FAILED):
            return
        image.ocr_status = OcrStatus.PROCESSING
        image.ocr_error = None
        db.commit()
        try:
            raw = ai_ocr.request_ocr(settings, image, Path(settings.image_root) / image.image_ref)
            result = OcrResult.model_validate(raw)
        except (ai_ocr.OcrCallError, ValidationError, OSError) as exc:
            log.warning("OCR 실패 %s: %s", image_id, exc)
            image.ocr_status = OcrStatus.FAILED
            image.ocr_error = str(exc)[:500]
            db.commit()
            return
        try:
            apply_ocr_result(db, image, result, settings)
        except AppError as exc:
            db.rollback()
            image = db.get(ScanImage, image_id)
            image.ocr_status = OcrStatus.FAILED
            image.ocr_error = f"결과 저장 실패: {exc.message}"[:500]
            db.commit()


def complete_if_ready(db: Session, session_id: str, settings: Settings) -> bool:
    """촬영 종료 + 사진이 1장 이상이고 모두 DONE이면 분석 완료 처리. 처리했으면 True."""
    session = db.get(ScanSession, session_id)
    if session is None or session.status is ScanSessionStatus.IN_PROGRESS or session.unobserved_checked_at:
        return False
    total, done = db.execute(select(func.count(), func.sum(ScanImage.ocr_status == OcrStatus.DONE))
                             .where(ScanImage.scan_session_id == session_id)).one()
    if not total or int(done or 0) != total:
        return False
    try:
        check_unobserved(db, session_id, settings.loan_status_reliable)
    except AppError:   # 다른 요청이 먼저 처리함
        db.rollback()
        return False
    return True


def reset_for_retry(db: Session, image: ScanImage, settings: Settings) -> ScanImage:
    if not settings.ai_ocr_url:
        raise AppError(ErrorCode.INVALID_STATE, "AI_OCR_URL이 설정되지 않아 백엔드가 AI를 부를 수 없습니다.", status_code=409)
    if image.ocr_status is not OcrStatus.FAILED:
        raise AppError(ErrorCode.INVALID_STATE, f"실패한 사진만 다시 시도할 수 있습니다 ({image.ocr_status}).",
                       status_code=409)
    image.ocr_status = OcrStatus.PENDING
    db.commit()
    return image
