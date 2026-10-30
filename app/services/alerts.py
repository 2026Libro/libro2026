"""관리자 알림 (BE-013) — 생성·자동 해결·확인·해결.

알림은 개별 기능 코드에서 직접 만들지 않고, DB에 저장되기 직전(before_flush)에
상태 변화를 보고 한 곳에서 만든다. 그래서 어떤 경로로 '관리자 검토'가 되든 알림이 빠지지 않는다.

| 변화                                          | 알림                              |
|-----------------------------------------------|-----------------------------------|
| 검증 결과 → REVIEW_REQUIRED                   | 원인별 (오배가 확정·의심, 인식 실패, 미관측, 기타) |
| 작업 생성 → PENDING_APPROVAL (인출 요청)      | RETRIEVAL_APPROVAL_REQUIRED       |
| 작업 → FAILED                                 | TASK_FAILED                       |
| 작업 → UNKNOWN                                | TASK_RESULT_UNKNOWN               |
| 로봇 ONLINE → OFFLINE                         | COMMUNICATION_ERROR               |

원인이 해소되면 (검증 결과가 검토 상태를 벗어남, 승인 대기·결과 불명에서 벗어남, 로봇 재연결)
열려 있는 알림을 RESOLVED로 자동 처리한다 (acknowledged_by="system").
"""
from sqlalchemy import event, func, inspect, select
from sqlalchemy.orm import Session

from app.core.enums import (
    AdminActionType,
    AlertStatus,
    AlertType,
    IdentificationStatus,
    LocationVerdict,
    ObservationStatus,
    RobotConnectionStatus,
    RobotTaskStatus,
    WorkflowStatus,
)
from app.core.errors import AppError
from app.db.base import utcnow
from app.models import AdminAction, Alert, Robot, RobotTask, VerificationResult
from app.schemas.common import ErrorCode

OPEN_STATUSES = (AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED)


# ---------- 상태 변화 감지 ----------

def _changed(obj, attr: str):
    """이번 저장에서 attr 값이 바뀌었으면 (이전 값, 새 값), 아니면 None."""
    history = inspect(obj).attrs[attr].history
    if not history.added:
        return None
    new = history.added[0]
    old = history.deleted[0] if history.deleted else None
    return None if old == new else (old, new)


def _add(session: Session, alert_type: AlertType, message: str, **target) -> None:
    session.add(Alert(alert_type=alert_type, message=message[:500], **target))


def _auto_resolve(session: Session, *conditions) -> None:
    now = utcnow()
    for alert in session.scalars(select(Alert).where(Alert.status.in_(OPEN_STATUSES), *conditions)):
        alert.status = AlertStatus.RESOLVED
        alert.acknowledged_by = alert.acknowledged_by or "system"
        alert.acknowledged_at = alert.acknowledged_at or now


def _verification_alert(v: VerificationResult) -> tuple[AlertType, str]:
    copy = v.book_copy_id or "복본 미확정"
    note = f" — {v.note}" if v.note else ""
    if v.observation_status is ObservationStatus.NOT_OBSERVED:
        return AlertType.NOT_OBSERVED, f"{copy}: 스캔 완료 범위에서 관측되지 않음 (등록 위치 {v.registered_slot_id}, 분실 처리 아님)"
    if v.location_verdict is LocationVerdict.MISPLACED_CONFIRMED:
        return AlertType.MISPLACED_CONFIRMED, f"{copy}: 오배가 확정 (등록 {v.registered_slot_id} / 관측 {v.observed_slot_id}){note}"
    if v.location_verdict is LocationVerdict.MISPLACED_SUSPECTED:
        return AlertType.MISPLACED_SUSPECTED, f"{copy}: 오배가 의심 (관측 {v.observed_slot_id}){note}"
    if v.identification_status in (IdentificationStatus.FAILED, IdentificationStatus.UNCERTAIN):
        return (AlertType.RECOGNITION_FAILED,
                f"{v.observed_slot_id}: 인식 {v.identification_status} (재관측 {v.reobservation_count}회){note}")
    return AlertType.REVIEW_REQUIRED, f"{copy} @ {v.observed_slot_id or v.registered_slot_id}: 확인 필요{note}"


def _on_verification(session: Session, v: VerificationResult) -> None:
    if not (change := _changed(v, "workflow_status")):
        return
    old, new = change
    if new is WorkflowStatus.REVIEW_REQUIRED:
        alert_type, message = _verification_alert(v)
        _add(session, alert_type, message, verification=v, book_copy_id=v.book_copy_id)
    elif old is WorkflowStatus.REVIEW_REQUIRED and v.id is not None:
        _auto_resolve(session, Alert.verification_id == v.id)


def _on_task(session: Session, task: RobotTask) -> None:
    if not (change := _changed(task, "status")):
        return
    old, new = change
    target = {"task": task, "robot_id": task.robot_id, "book_copy_id": task.book_copy_id}
    if new is RobotTaskStatus.PENDING_APPROVAL:
        _add(session, AlertType.RETRIEVAL_APPROVAL_REQUIRED,
             f"{task.book_copy_id} 인출 승인 요청 (위치 {task.slot_id}, 로봇 {task.robot_id})", **target)
    elif new is RobotTaskStatus.FAILED:
        _add(session, AlertType.TASK_FAILED,
             f"{task.task_type} 작업 실패 (로봇 {task.robot_id}): {task.result_message or '사유 없음'}", **target)
    elif new is RobotTaskStatus.UNKNOWN:
        _add(session, AlertType.TASK_RESULT_UNKNOWN,
             f"{task.task_type} 작업 결과 불명 (로봇 {task.robot_id} 통신 끊김). 자동 재전송하지 않음", **target)

    if old is RobotTaskStatus.PENDING_APPROVAL:
        _auto_resolve(session, Alert.task_id == task.id, Alert.alert_type == AlertType.RETRIEVAL_APPROVAL_REQUIRED)
    elif old is RobotTaskStatus.UNKNOWN:
        _auto_resolve(session, Alert.task_id == task.id, Alert.alert_type == AlertType.TASK_RESULT_UNKNOWN)


def _on_robot(session: Session, robot: Robot) -> None:
    if not (change := _changed(robot, "connection_status")):
        return
    old, new = change
    is_comm = (Alert.robot_id == robot.id, Alert.alert_type == AlertType.COMMUNICATION_ERROR)
    if old is RobotConnectionStatus.ONLINE and new is RobotConnectionStatus.OFFLINE:
        already_open = session.scalar(select(Alert.id).where(Alert.status.in_(OPEN_STATUSES), *is_comm))
        if not already_open:
            _add(session, AlertType.COMMUNICATION_ERROR, f"로봇 {robot.id} 통신 끊김 (마지막 통신 {robot.last_seen_at})",
                 robot_id=robot.id)
    elif new is RobotConnectionStatus.ONLINE:
        _auto_resolve(session, *is_comm)


@event.listens_for(Session, "before_flush")
def _create_alerts(session: Session, _flush_context, _instances) -> None:
    with session.no_autoflush:  # 여기서 하는 조회가 다시 flush를 일으키지 않도록
        for obj in [*session.new, *session.dirty]:
            if isinstance(obj, VerificationResult):
                _on_verification(session, obj)
            elif isinstance(obj, RobotTask):
                _on_task(session, obj)
            elif isinstance(obj, Robot):
                _on_robot(session, obj)


# ---------- 관리자: 조회·확인·해결 ----------

def list_alerts(db: Session, status: AlertStatus | None, alert_type: AlertType | None, open_only: bool,
                book_copy_id: str | None, robot_id: str | None, limit: int, offset: int) -> tuple[list[Alert], int]:
    stmt = select(Alert)
    if book_copy_id:
        stmt = stmt.where(Alert.book_copy_id == book_copy_id)
    if robot_id:
        stmt = stmt.where(Alert.robot_id == robot_id)
    if open_only:
        stmt = stmt.where(Alert.status.in_(OPEN_STATUSES))
    if status:
        stmt = stmt.where(Alert.status == status)
    if alert_type:
        stmt = stmt.where(Alert.alert_type == alert_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(stmt.order_by(Alert.created_at.desc(), Alert.id.desc()).limit(limit).offset(offset)))
    return items, total


def _get(db: Session, alert_id: int) -> Alert:
    alert = db.get(Alert, alert_id, with_for_update=True)
    if alert is None:
        raise AppError(ErrorCode.NOT_FOUND, f"알림 {alert_id}를 찾을 수 없습니다.", status_code=404)
    return alert


def confirm(db: Session, alert_id: int, admin_id: str, comment: str | None) -> Alert:
    """확인함 (보고 있음). 원인 처리는 별도이며, 처리가 끝나면 resolve 한다."""
    alert = _get(db, alert_id)
    if alert.status is not AlertStatus.OPEN:
        raise AppError(ErrorCode.INVALID_STATE, f"이미 {alert.status} 상태인 알림입니다.", status_code=409)
    alert.status, alert.acknowledged_by, alert.acknowledged_at = AlertStatus.ACKNOWLEDGED, admin_id, utcnow()
    db.add(AdminAction(admin_id=admin_id, action_type=AdminActionType.CONFIRM_ALERT, alert_id=alert.id,
                       task_id=alert.task_id, book_copy_id=alert.book_copy_id, comment=comment))
    db.commit()
    return alert


def resolve(db: Session, alert_id: int, admin_id: str, comment: str | None) -> Alert:
    """처리 완료. 연결된 검증 결과가 관리자 검토 상태면 함께 닫는다 (사람이 확인·조치했으므로)."""
    alert = _get(db, alert_id)
    if alert.status is AlertStatus.RESOLVED:
        raise AppError(ErrorCode.INVALID_STATE, "이미 해결된 알림입니다.", status_code=409)
    alert.status = AlertStatus.RESOLVED
    alert.acknowledged_by, alert.acknowledged_at = admin_id, utcnow()
    if alert.verification and alert.verification.workflow_status is WorkflowStatus.REVIEW_REQUIRED:
        v = alert.verification
        v.workflow_status = WorkflowStatus.CLOSED
        v.note = f"{v.note}; 관리자 처리 완료 ({admin_id})" if v.note else f"관리자 처리 완료 ({admin_id})"
    db.add(AdminAction(admin_id=admin_id, action_type=AdminActionType.RESOLVE_ALERT, alert_id=alert.id,
                       task_id=alert.task_id, book_copy_id=alert.book_copy_id, comment=comment))
    db.commit()
    return alert
