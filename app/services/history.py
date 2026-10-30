"""통합 이력 조회 (BE-015): 관측·검증·작업 이벤트·관리자 처리·등록 위치 변경·알림을 시간순으로.

각 테이블에서 조건에 맞는 최신 limit건씩 가져와 합친 뒤 다시 최신순으로 limit건을 돌려준다.
"""
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import (
    AdminAction,
    Alert,
    BookLocationHistory,
    Observation,
    ObservationCandidate,
    RobotTask,
    TaskEvent,
    VerificationResult,
)
from app.schemas.admin import HistoryItem


def get_history(db: Session, book_copy_id: str | None, robot_id: str | None, task_id: str | None,
                limit: int) -> list[HistoryItem]:
    items: list[HistoryItem] = []
    collectors = [_observations, _verifications, _task_events, _admin_actions, _location_changes, _alerts]
    for collect in collectors:
        items += collect(db, book_copy_id, robot_id, task_id, limit)
    items.sort(key=lambda i: i.at, reverse=True)
    return items[:limit]


def _observations(db, book_copy_id, robot_id, task_id, limit):
    if task_id:
        return []
    stmt = select(Observation)
    if book_copy_id:
        as_candidate = select(ObservationCandidate.observation_id).where(ObservationCandidate.book_copy_id == book_copy_id)
        stmt = stmt.where(or_(Observation.reported_book_copy_id == book_copy_id, Observation.id.in_(as_candidate)))
    if robot_id:
        stmt = stmt.where(Observation.robot_id == robot_id)
    rows = db.scalars(stmt.order_by(Observation.captured_at.desc()).limit(limit))
    return [HistoryItem(
        at=o.captured_at, kind="OBSERVATION", ref_id=o.id, robot_id=o.robot_id, book_copy_id=o.reported_book_copy_id,
        summary=f"{o.slot_id} 관측: '{o.detected_text or '-'}' (OCR {o.ocr_confidence:.2f}, 식별 {o.identification_confidence:.2f})"
                + (f", 재관측 대상 {o.reobservation_of}" if o.reobservation_of else ""),
    ) for o in rows]


def _verifications(db, book_copy_id, robot_id, task_id, limit):
    stmt = select(VerificationResult, Observation.robot_id).outerjoin(
        Observation, Observation.id == VerificationResult.observation_id)
    if book_copy_id:
        stmt = stmt.where(VerificationResult.book_copy_id == book_copy_id)
    if robot_id:
        stmt = stmt.where(Observation.robot_id == robot_id)
    if task_id:
        stmt = stmt.where(VerificationResult.id == select(RobotTask.verification_id).where(RobotTask.id == task_id)
                          .scalar_subquery())
    rows = db.execute(stmt.order_by(VerificationResult.created_at.desc()).limit(limit))
    return [HistoryItem(
        at=v.created_at, kind="VERIFICATION", ref_id=str(v.id), book_copy_id=v.book_copy_id, robot_id=obs_robot,
        summary=f"검증: {v.identification_status or v.observation_status} / {v.location_verdict}"
                f" / {v.workflow_status or '처리 없음'} (등록 {v.registered_slot_id or '-'}, 관측 {v.observed_slot_id or '-'})",
    ) for v, obs_robot in rows]


def _task_events(db, book_copy_id, robot_id, task_id, limit):
    stmt = select(TaskEvent, RobotTask).join(RobotTask, RobotTask.id == TaskEvent.task_id)
    if book_copy_id:
        stmt = stmt.where(RobotTask.book_copy_id == book_copy_id)
    if robot_id:
        stmt = stmt.where(RobotTask.robot_id == robot_id)
    if task_id:
        stmt = stmt.where(TaskEvent.task_id == task_id)
    rows = db.execute(stmt.order_by(TaskEvent.occurred_at.desc(), TaskEvent.id.desc()).limit(limit))
    return [HistoryItem(
        at=e.occurred_at, kind="TASK_EVENT", ref_id=str(e.id), task_id=t.id, robot_id=t.robot_id,
        book_copy_id=t.book_copy_id,
        summary=f"{t.task_type} {e.event_type} ({e.source}): {e.from_status or '-'} → {e.to_status or '-'}",
    ) for e, t in rows]


def _admin_actions(db, book_copy_id, robot_id, task_id, limit):
    stmt = select(AdminAction, RobotTask.robot_id).outerjoin(RobotTask, RobotTask.id == AdminAction.task_id)
    if book_copy_id:
        stmt = stmt.where(AdminAction.book_copy_id == book_copy_id)
    if robot_id:
        stmt = stmt.where(RobotTask.robot_id == robot_id)
    if task_id:
        stmt = stmt.where(AdminAction.task_id == task_id)
    rows = db.execute(stmt.order_by(AdminAction.created_at.desc()).limit(limit))
    return [HistoryItem(
        at=a.created_at, kind="ADMIN_ACTION", ref_id=str(a.id), task_id=a.task_id, book_copy_id=a.book_copy_id,
        robot_id=task_robot, summary=f"관리자 {a.admin_id}: {a.action_type}" + (f" — {a.comment}" if a.comment else ""),
    ) for a, task_robot in rows]


def _location_changes(db, book_copy_id, robot_id, task_id, limit):
    if robot_id or task_id:
        return []
    stmt = select(BookLocationHistory)
    if book_copy_id:
        stmt = stmt.where(BookLocationHistory.book_copy_id == book_copy_id)
    rows = db.scalars(stmt.order_by(BookLocationHistory.changed_at.desc()).limit(limit))
    return [HistoryItem(
        at=h.changed_at, kind="LOCATION_CHANGE", ref_id=str(h.id), book_copy_id=h.book_copy_id,
        summary=f"등록 위치 변경 {h.from_slot_id or '(없음)'} → {h.to_slot_id} by {h.changed_by}"
                + (f": {h.reason}" if h.reason else ""),
    ) for h in rows]


def _alerts(db, book_copy_id, robot_id, task_id, limit):
    stmt = select(Alert)
    if book_copy_id:
        stmt = stmt.where(Alert.book_copy_id == book_copy_id)
    if robot_id:
        stmt = stmt.where(Alert.robot_id == robot_id)
    if task_id:
        stmt = stmt.where(Alert.task_id == task_id)
    rows = db.scalars(stmt.order_by(Alert.created_at.desc()).limit(limit))
    return [HistoryItem(
        at=a.created_at, kind="ALERT", ref_id=str(a.id), book_copy_id=a.book_copy_id, robot_id=a.robot_id,
        task_id=a.task_id, summary=f"알림 {a.alert_type} [{a.status}]: {a.message}",
    ) for a in rows]
