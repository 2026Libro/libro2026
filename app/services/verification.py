"""도서 식별 + 위치 검증 + 미관측 판정 (BE-005, BE-006, BE-007, BE-008).

안전 원칙
- 후보 점수만으로 복본을 확정하지 않는다. AI가 book_copy_id를 보냈고 신뢰도가 기준 이상일 때만 IDENTIFIED.
- 첫 관측에서는 오배가를 '의심'까지만 한다. 재관측에서 같은 결과가 나와야 MISPLACED_CONFIRMED.
- 미관측은 분실이 아니다. 대출 중·인출된 책은 검토 대상에서 뺀다.
- 재관측 대기 판정이 나오면 (auto_reobservation 설정 시) 촬영한 로봇에 REOBSERVE_BOOK 작업을 자동 생성한다.
  재관측 횟수가 한도를 넘으면 REVIEW_REQUIRED가 되므로 무한 반복되지 않는다.
- 오배가 확정이면 (auto_retrieval_request 설정 시) 인출 요청을 PENDING_APPROVAL로 만든다. 승인은 관리자가 한다.
- 관리자 알림 생성은 9단계에서 이 결과를 보고 한다.
"""
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings
from app.core.enums import (
    IdentificationStatus,
    LoanStatus,
    LocationCertainty,
    LocationVerdict,
    ObservationStatus,
    PhysicalLocationType,
    ScanSessionStatus,
    WorkflowStatus,
)
from app.core.errors import AppError
from app.db.base import utcnow
from app.models import BookCopy, BookLocation, Observation, ObservationCandidate, ScanSession, ShelfSlot, VerificationResult
from app.schemas.common import ErrorCode
from app.schemas.verification import UnobservedCheckResult
from app.services import retrieval
from app.services.robot_task import create_reobservation_task

_MISPLACED = (LocationVerdict.MISPLACED_SUSPECTED, LocationVerdict.MISPLACED_CONFIRMED)
# 로봇이 들고 있는 상태 (그리퍼·바구니). 서가 관측으로 덮어쓰지 않는다.
_HELD_BY_ROBOT = (PhysicalLocationType.ROBOT_GRIPPER, PhysicalLocationType.BASKET)
# 서가에 없는 게 당연한 물리 위치 (인출되어 로봇·바구니·직원에게 있음). 미관측 검토에서 제외한다.
_OFF_SHELF = (*_HELD_BY_ROBOT, PhysicalLocationType.COLLECTED_BY_STAFF)


@dataclass(frozen=True)
class Thresholds:
    min_detection: float
    min_ocr: float
    min_identification: float
    max_reobservations: int
    auto_reobservation: bool = False
    auto_retrieval_request: bool = False

    @classmethod
    def from_settings(cls, s: Settings) -> "Thresholds":
        return cls(s.verify_min_detection, s.verify_min_ocr, s.verify_min_identification, s.verify_max_reobservations,
                   s.auto_reobservation, s.auto_retrieval_request)


# ---------- 1. 도서 식별 (DB 없이 판단하는 순수 함수) ----------

def identify(obs: Observation, th: Thresholds) -> IdentificationStatus:
    if obs.detection_confidence < th.min_detection:
        return IdentificationStatus.FAILED
    ocr_ok = obs.ocr_confidence >= th.min_ocr

    if obs.reported_book_copy_id:
        if ocr_ok and obs.identification_confidence >= th.min_identification:
            return IdentificationStatus.IDENTIFIED
        return IdentificationStatus.UNCERTAIN
    if obs.candidates:
        return IdentificationStatus.CANDIDATE_ONLY if ocr_ok else IdentificationStatus.UNCERTAIN
    if obs.detected_text and ocr_ok:
        return IdentificationStatus.UNREGISTERED  # 글자는 확실히 읽었는데 DB에 해당 도서가 없음
    return IdentificationStatus.FAILED


# ---------- 2. 관측 1건 검증 ----------

def _registered_slots(db: Session, copy_ids: list[str]) -> dict[str, str | None]:
    found = dict(db.execute(select(BookLocation.book_copy_id, BookLocation.slot_id)
                            .where(BookLocation.book_copy_id.in_(copy_ids))).all())
    return {c: found.get(c) for c in copy_ids}


def _update_physical_location(copy: BookCopy, obs: Observation, notes: list[str]) -> None:
    """확실히 식별된 관측은 '로봇 카메라로 확인한 위치'이므로 물리 위치를 확정으로 갱신한다."""
    if copy.physical_location_type in _HELD_BY_ROBOT:
        # 로봇이 들고 있어야 할 책이 서가에서 보였다면 기록이 어긋난 것. 자동으로 덮어쓰지 않는다.
        # (직원이 회수한 책은 다시 꽂아 둘 수 있으므로 서가 관측으로 갱신한다)
        notes.append(f"물리 위치가 {copy.physical_location_type}인데 서가에서 관측됨 (물리 위치는 갱신하지 않음)")
        return
    if copy.physical_location_confirmed_at and copy.physical_location_confirmed_at >= obs.captured_at:
        return  # 더 최근에 확인된 위치가 있음 (늦게 도착한 관측)
    copy.physical_location_type = PhysicalLocationType.SHELF
    copy.physical_slot_id = obs.slot_id
    copy.physical_robot_id = None
    copy.physical_location_certainty = LocationCertainty.CONFIRMED
    copy.physical_location_confirmed_at = obs.captured_at


def verify_observation(db: Session, obs: Observation, th: Thresholds) -> VerificationResult:
    """관측을 판정하고 결과 행을 추가한다 (commit은 호출한 쪽에서)."""
    status = identify(obs, th)
    notes: list[str] = []
    needs_review = False
    copy_id = None
    registered_slot = None
    verdict = LocationVerdict.UNDETERMINED

    previous = None
    if obs.reobservation_of:
        previous = db.scalar(select(VerificationResult).where(VerificationResult.observation_id == obs.reobservation_of))
    reobservation_count = previous.reobservation_count + 1 if previous else 0

    if status is IdentificationStatus.IDENTIFIED:
        copy_id = obs.reported_book_copy_id
        copy = db.get(BookCopy, copy_id, options=[selectinload(BookCopy.registered_location)])
        registered_slot = copy.registered_location.slot_id if copy.registered_location else None

        if registered_slot is None:
            notes.append("등록 위치가 없는 복본")
            needs_review = True
        elif registered_slot == obs.slot_id:
            verdict = LocationVerdict.NORMAL
        elif (previous and previous.location_verdict in _MISPLACED and previous.book_copy_id == copy_id
              and previous.observed_slot_id == obs.slot_id):
            verdict = LocationVerdict.MISPLACED_CONFIRMED  # 재관측에서도 같은 칸에서 같은 복본 확인
        else:
            verdict = LocationVerdict.MISPLACED_SUSPECTED

        if copy.loan_status is LoanStatus.CHECKED_OUT:
            notes.append("대출 중으로 등록되어 있으나 서가에서 관측됨")
            needs_review = True
        _update_physical_location(copy, obs, notes)

    elif status is IdentificationStatus.CANDIDATE_ONLY:
        slots = _registered_slots(db, [c.book_copy_id for c in obs.candidates])
        if all(s == obs.slot_id for s in slots.values()):
            verdict = LocationVerdict.NORMAL  # 어느 복본이든 이 칸이 맞는 자리
        elif all(s is not None and s != obs.slot_id for s in slots.values()):
            verdict = LocationVerdict.MISPLACED_SUSPECTED
        notes.append("복본 미확정: 후보 " + ", ".join(f"{c}({s})" for c, s in slots.items()))

    elif status is IdentificationStatus.UNREGISTERED:
        needs_review = True

    workflow = _decide_workflow(verdict, needs_review, reobservation_count, th)

    if previous and previous.workflow_status is not WorkflowStatus.CLOSED:
        previous.workflow_status = WorkflowStatus.CLOSED
        previous.note = _join(previous.note, f"재관측 {obs.id} 결과로 대체됨")

    _close_late_unobserved(db, obs)

    result = VerificationResult(
        scan_session_id=obs.scan_session_id,
        observation_id=obs.id,
        book_copy_id=copy_id,
        identification_status=status,
        location_verdict=verdict,
        observation_status=ObservationStatus.OBSERVED,
        workflow_status=workflow,
        registered_slot_id=registered_slot,
        observed_slot_id=obs.slot_id,
        reobservation_count=reobservation_count,
        note="; ".join(notes) or None,
    )
    db.add(result)
    db.flush()

    if workflow is WorkflowStatus.REOBSERVATION_PENDING and th.auto_reobservation:
        create_reobservation_task(db, result, obs.robot_id, created_by="system")
    if verdict is LocationVerdict.MISPLACED_CONFIRMED and th.auto_retrieval_request:
        retrieval.try_create_request(db, result, obs.robot_id)  # 승인 대기로만 생성됨
    return result


def _decide_workflow(verdict: LocationVerdict, needs_review: bool, reobservation_count: int,
                     th: Thresholds) -> WorkflowStatus | None:
    if verdict is LocationVerdict.NORMAL and not needs_review:
        return None
    if verdict is LocationVerdict.MISPLACED_CONFIRMED or needs_review:
        return WorkflowStatus.REVIEW_REQUIRED
    # 오배가 의심, 또는 인식이 불확실해 판정 불가 → 재관측. 반복해도 안 되면 관리자에게.
    if reobservation_count < th.max_reobservations:
        return WorkflowStatus.REOBSERVATION_PENDING
    return WorkflowStatus.REVIEW_REQUIRED


def _close_late_unobserved(db: Session, obs: Observation) -> None:
    """미관측 판정 뒤에 늦게 도착한 관측이 그 책을 가리키면 미관측 결과를 닫는다."""
    copy_ids = {c.book_copy_id for c in obs.candidates}
    if obs.reported_book_copy_id:
        copy_ids.add(obs.reported_book_copy_id)
    if not copy_ids:
        return
    rows = db.scalars(select(VerificationResult).where(
        VerificationResult.scan_session_id == obs.scan_session_id,
        VerificationResult.observation_status == ObservationStatus.NOT_OBSERVED,
        VerificationResult.book_copy_id.in_(copy_ids),
        VerificationResult.workflow_status == WorkflowStatus.REVIEW_REQUIRED,
    ))
    for row in rows:
        row.workflow_status = WorkflowStatus.CLOSED
        row.note = _join(row.note, f"미관측 판정 후 관측 {obs.id}에서 확인됨")


def _join(note: str | None, extra: str) -> str:
    return f"{note}; {extra}" if note else extra


# ---------- 3. 미관측 판정 ----------

def check_unobserved(db: Session, session_id: str) -> UnobservedCheckResult:
    """AI가 이 세션의 분석을 모두 보냈다고 알렸을 때 1회 실행한다."""
    session = db.scalar(select(ScanSession).where(ScanSession.id == session_id).with_for_update())
    if session is None:
        raise AppError(ErrorCode.NOT_FOUND, f"스캔 세션 '{session_id}'를 찾을 수 없습니다.", status_code=404)
    if session.status is ScanSessionStatus.IN_PROGRESS:
        raise AppError(ErrorCode.INVALID_STATE, "로봇이 아직 촬영 중인 세션입니다.", status_code=409)
    if session.unobserved_checked_at:
        raise AppError(ErrorCode.INVALID_STATE, "이미 미관측 판정을 한 세션입니다.", status_code=409)

    completed = session.status is ScanSessionStatus.COMPLETED
    obs_status = ObservationStatus.NOT_OBSERVED if completed else ObservationStatus.SCAN_INCOMPLETE

    registered = list(db.scalars(
        select(BookCopy)
        .join(BookLocation, BookLocation.book_copy_id == BookCopy.id)
        .join(ShelfSlot, ShelfSlot.id == BookLocation.slot_id)
        .where(ShelfSlot.shelf_id == session.shelf_id)
        .options(selectinload(BookCopy.registered_location))
    ))
    # 이번 세션에서 언급된 복본: AI가 확정했거나 후보로라도 나온 것 (보수적으로 '보였을 수 있음'으로 취급)
    seen = set(db.scalars(select(Observation.reported_book_copy_id).where(
        Observation.scan_session_id == session_id, Observation.reported_book_copy_id.is_not(None))))
    seen |= set(db.scalars(select(ObservationCandidate.book_copy_id)
                           .join(Observation, Observation.id == ObservationCandidate.observation_id)
                           .where(Observation.scan_session_id == session_id)))

    unobserved = [c for c in registered if c.id not in seen]
    review_count = 0
    for copy in unobserved:
        workflow, note = _unobserved_workflow(copy, completed)
        review_count += workflow is WorkflowStatus.REVIEW_REQUIRED
        db.add(VerificationResult(
            scan_session_id=session_id,
            book_copy_id=copy.id,
            location_verdict=LocationVerdict.UNDETERMINED,
            observation_status=obs_status,
            workflow_status=workflow,
            registered_slot_id=copy.registered_location.slot_id,
            note=note,
        ))

    session.unobserved_checked_at = utcnow()
    db.commit()
    return UnobservedCheckResult(
        scan_session_id=session_id, observation_status=obs_status, registered_copy_count=len(registered),
        unobserved_copy_count=len(unobserved), review_required_count=review_count,
        checked_at=session.unobserved_checked_at,
    )


def _unobserved_workflow(copy: BookCopy, scan_completed: bool) -> tuple[WorkflowStatus | None, str]:
    if not scan_completed:
        return None, "스캔이 완료되지 않아 판정 보류"
    if copy.loan_status is LoanStatus.CHECKED_OUT:
        return None, "대출 중이므로 서가에 없는 것이 정상"
    if copy.loan_status is LoanStatus.IN_TRANSIT:
        return None, "이동 중(IN_TRANSIT)"
    if copy.physical_location_type in _OFF_SHELF:
        return None, f"인출되어 {copy.physical_location_type}에 있음"
    return WorkflowStatus.REVIEW_REQUIRED, "스캔 완료 범위에서 관측되지 않음 (분실 처리 아님, 관리자 확인 필요)"


# ---------- 4. 조회 ----------

def list_verifications(db: Session, filters: dict, limit: int, offset: int) -> tuple[list[VerificationResult], int]:
    stmt = select(VerificationResult)
    for field, value in filters.items():
        if value is not None:
            stmt = stmt.where(getattr(VerificationResult, field) == value)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(stmt.order_by(VerificationResult.id.desc()).limit(limit).offset(offset)))
    return items, total


def get_verification(db: Session, verification_id: int) -> VerificationResult:
    row = db.get(VerificationResult, verification_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, f"검증 결과 {verification_id}를 찾을 수 없습니다.", status_code=404)
    return row
