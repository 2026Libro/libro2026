"""AI 인식 결과 수신·저장 (BE-004).

새 관측은 저장과 동시에 식별·위치 검증(services/verification.py)을 거치며,
관측과 검증 결과는 한 트랜잭션으로 함께 저장된다.
"""
from datetime import UTC
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError
from app.models import BookCopy, Observation, ObservationCandidate, ShelfSlot
from app.schemas.common import ErrorCode
from app.schemas.observation import Candidate, ObservationCreate
from app.services.catalog_match import get_index
from app.services.scan_session import get_session
from app.services.verification import Thresholds, verify_observation


def _check_duplicate(existing: Observation, payload: dict) -> Observation:
    """같은 observation_id가 다시 왔을 때.

    - 내용이 같으면 네트워크 재전송으로 보고 기존 결과를 그대로 돌려준다 (새로 저장하지 않음).
    - 내용이 다르면 ID 충돌이므로 거부한다.
    """
    if existing.raw_payload == payload:
        return existing
    raise AppError(
        ErrorCode.DUPLICATE_REQUEST,
        f"observation_id '{existing.id}'로 다른 내용의 관측이 이미 저장되어 있습니다.",
        status_code=409,
    )


def _validate_references(db: Session, data: ObservationCreate) -> None:
    session = get_session(db, data.scan_session_id)
    # [가정] 세션이 이미 종료되었어도 관측을 받는다. AI 분석이 로봇 촬영보다 늦게 끝날 수 있기 때문이다.
    mismatched = [
        f"{field}: 세션은 '{expected}', 관측은 '{actual}'"
        for field, expected, actual in (
            ("robot_id", session.robot_id, data.robot_id),
            ("shelf_id", session.shelf_id, data.shelf_id),
        )
        if expected != actual
    ]
    if mismatched:
        raise AppError(ErrorCode.VALIDATION_ERROR, "관측 정보가 스캔 세션과 일치하지 않습니다.", status_code=422,
                       details=[{"field": m.split(":")[0], "message": m} for m in mismatched])

    if db.get(ShelfSlot, data.slot_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, f"서가 칸 '{data.slot_id}'를 찾을 수 없습니다.", status_code=404)

    copy_ids = {c.book_copy_id for c in data.candidates}
    if data.book_copy_id:
        copy_ids.add(data.book_copy_id)
    if copy_ids:
        found = set(db.scalars(select(BookCopy.id).where(BookCopy.id.in_(copy_ids))))
        if missing := sorted(copy_ids - found):
            raise AppError(ErrorCode.VALIDATION_ERROR, "등록되지 않은 복본 ID가 포함되어 있습니다.", status_code=422,
                           details=[{"field": "book_copy_id", "message": m} for m in missing])

    if data.reobservation_of and db.get(Observation, data.reobservation_of) is None:
        raise AppError(ErrorCode.NOT_FOUND, f"재관측 대상 관측 '{data.reobservation_of}'를 찾을 수 없습니다.",
                       status_code=404)


def _match_text(db: Session, data: ObservationCreate) -> tuple[ObservationCreate, str]:
    """AI가 읽은 글자만 보낸 경우: 백엔드가 DB에서 복본을 찾아 book_copy_id·후보·식별 신뢰도를 채운다.

    원본 요청(raw_payload)은 바꾸지 않는다. 재전송 비교도 원본 기준이다.
    """
    result = get_index(db).match(data.call_number_text, data.title_text)
    return data.model_copy(update={
        "book_copy_id": result.book_copy_id,
        "candidates": [Candidate(book_copy_id=c, score=min(s, 1.0)) for c, s in result.candidates],
        "confidence": data.confidence.model_copy(update={"identification": result.score}),
    }), result.method


def receive_observation(db: Session, data: ObservationCreate, th: Thresholds) -> tuple[Observation, bool]:
    """관측을 저장하고 검증한다. (관측, 새로 저장했는지)를 돌려준다. 재전송이면 다시 검증하지 않는다."""
    payload = data.model_dump(mode="json")
    if existing := db.get(Observation, data.observation_id):
        return _check_duplicate(existing, payload), False

    match_method = None
    if not data.book_copy_id and not data.candidates and (data.call_number_text or data.title_text):
        data, match_method = _match_text(db, data)

    _validate_references(db, data)

    ranked = sorted(data.candidates, key=lambda c: c.score, reverse=True)
    obs = Observation(
        id=data.observation_id,
        scan_session_id=data.scan_session_id,
        robot_id=data.robot_id,
        shelf_id=data.shelf_id,
        slot_id=data.slot_id,
        detected_text=data.detected_text,
        reported_book_copy_id=data.book_copy_id,
        match_method=match_method,
        detection_confidence=data.confidence.detection,
        ocr_confidence=data.confidence.ocr,
        identification_confidence=data.confidence.identification,
        reobservation_of=data.reobservation_of,
        image_ref=data.image_ref,
        bbox=data.bbox,
        order=data.order,
        image_width=data.image_width,
        image_height=data.image_height,
        captured_at=data.captured_at.astimezone(UTC).replace(tzinfo=None),
        raw_payload=payload,
        candidates=[
            ObservationCandidate(book_copy_id=c.book_copy_id, score=c.score, rank=i)
            for i, c in enumerate(ranked, start=1)
        ],
    )
    db.add(obs)
    try:
        db.flush()
    except IntegrityError:
        # 같은 관측이 동시에 두 번 들어와 다른 쪽이 먼저 저장한 경우
        db.rollback()
        if existing := db.get(Observation, data.observation_id):
            return _check_duplicate(existing, payload), False
        raise
    verify_observation(db, obs, th)
    db.commit()
    return obs, True


def get_observation(db: Session, observation_id: str) -> Observation:
    obs = db.get(Observation, observation_id, options=[selectinload(Observation.candidates)])
    if obs is None:
        raise AppError(ErrorCode.NOT_FOUND, f"관측 '{observation_id}'를 찾을 수 없습니다.", status_code=404)
    return obs


def image_path(obs: Observation, image_root: str) -> Path:
    """관측 이미지 파일 경로. 보관 폴더 밖을 가리키는 경로(../, 절대 경로)는 거부한다."""
    if not obs.image_ref:
        raise AppError(ErrorCode.NOT_FOUND, f"관측 '{obs.id}'에는 이미지가 없습니다.", status_code=404)
    root = Path(image_root).resolve()
    path = (root / obs.image_ref).resolve()
    if not path.is_relative_to(root):
        raise AppError(ErrorCode.NOT_FOUND, f"관측 '{obs.id}'의 이미지 경로가 올바르지 않습니다.", status_code=404)
    if not path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, f"관측 '{obs.id}'의 이미지 파일이 서버에 없습니다.", status_code=404)
    return path


def list_observations(
    db: Session, scan_session_id: str | None, shelf_id: str | None, slot_id: str | None, limit: int, offset: int
) -> tuple[list[Observation], int]:
    stmt = select(Observation)
    if scan_session_id:
        stmt = stmt.where(Observation.scan_session_id == scan_session_id)
    if shelf_id:
        stmt = stmt.where(Observation.shelf_id == shelf_id)
    if slot_id:
        stmt = stmt.where(Observation.slot_id == slot_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(
        stmt.options(selectinload(Observation.candidates))
        .order_by(Observation.captured_at.desc(), Observation.id)
        .limit(limit).offset(offset)
    ))
    return items, total
