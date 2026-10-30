"""로봇 작업 관리와 상태 머신 (BE-010).

안전 원칙
- 승인 대기(PENDING_APPROVAL) 작업은 로봇에 전달하지 않는다. 로봇이 받아 가는 것은 PENDING뿐이다.
- 수락(ack)과 완료(result)를 구분한다. 수락하지 않은 작업의 결과 보고는 거부한다.
- 통신이 끊기면 진행 중 작업을 FAILED가 아니라 UNKNOWN으로 둔다.
- UNKNOWN 작업은 자동으로 다시 PENDING으로 돌리거나 재전송하지 않는다. 로봇이 실제 결과를 보고해야 끝난다.
- 같은 보고가 재전송되면(idempotency_key 동일) 한 번만 처리한다.
- 재관측 작업이 실패·취소되거나, 완료 후에도 AI 결과가 오지 않으면 재관측 대기에 멈춰 두지 않고 관리자 검토로 넘긴다.
"""
import secrets
from datetime import UTC, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.enums import (
    AdminActionType,
    EventSource,
    RobotConnectionStatus,
    RobotTaskStatus,
    RobotTaskType,
    ScanSessionStatus,
    WorkflowStatus,
)
from app.core.errors import AppError
from app.db.base import utcnow
from app.models import AdminAction, Observation, Robot, RobotTask, ScanSession, Shelf, TaskEvent, VerificationResult
from app.models.robot import ACTIVE_TASK_STATUSES
from app.schemas.common import ErrorCode
from app.schemas.robot_task import RobotTaskCommand, TaskAck, TaskCreate, TaskResultReport
from app.services import robot as robot_service


S = RobotTaskStatus
TERMINAL = (S.COMPLETED, S.FAILED, S.CANCELLED)
RUNNING = (S.ACCEPTED, S.IN_PROGRESS)

# 로봇 보고(result)로 허용되는 상태 변경. 여기에 없는 변경은 거부한다.
ROBOT_TRANSITIONS: dict[RobotTaskStatus, set[RobotTaskStatus]] = {
    S.ACCEPTED: {S.IN_PROGRESS, S.COMPLETED, S.FAILED, S.CANCELLED},
    S.IN_PROGRESS: {S.IN_PROGRESS, S.COMPLETED, S.FAILED, S.CANCELLED},
    S.UNKNOWN: {S.IN_PROGRESS, S.COMPLETED, S.FAILED, S.CANCELLED},  # 통신 복구 후 실제 결과 확인
}


def _new_task_id() -> str:
    return f"TASK-{utcnow():%Y%m%d%H%M%S}-{secrets.token_hex(3)}"


def _invalid_state(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_STATE, message, status_code=409)


def _add_event(db: Session, task: RobotTask, event_type: str, source: EventSource, from_status: RobotTaskStatus | None,
               to_status: RobotTaskStatus | None, payload: dict | None = None, idempotency_key: str | None = None,
               occurred_at=None) -> None:
    db.add(TaskEvent(task_id=task.id, event_type=event_type, source=source, from_status=from_status,
                     to_status=to_status, payload=payload, idempotency_key=idempotency_key,
                     occurred_at=occurred_at or utcnow()))


def _new_task(db: Session, task_type: RobotTaskType, robot_id: str, created_by: str, **fields) -> RobotTask:
    task = RobotTask(id=_new_task_id(), task_type=task_type, status=S.PENDING, robot_id=robot_id,
                     created_by=created_by, **fields)
    db.add(task)
    db.flush()
    _add_event(db, task, "CREATED", EventSource.ADMIN if created_by != "system" else EventSource.BACKEND, None, S.PENDING)
    return task


# ---------- 조회 ----------

def get_task(db: Session, task_id: str, lock: bool = False) -> RobotTask:
    task = db.get(RobotTask, task_id, with_for_update=lock)
    if task is None:
        raise AppError(ErrorCode.NOT_FOUND, f"작업 '{task_id}'를 찾을 수 없습니다.", status_code=404)
    return task


def get_events(db: Session, task_id: str) -> list[TaskEvent]:
    return list(db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.id)))


def list_tasks(db: Session, robot_id: str | None, status: RobotTaskStatus | None, task_type: RobotTaskType | None,
               limit: int, offset: int) -> tuple[list[RobotTask], int]:
    stmt = select(RobotTask)
    if robot_id:
        stmt = stmt.where(RobotTask.robot_id == robot_id)
    if status:
        stmt = stmt.where(RobotTask.status == status)
    if task_type:
        stmt = stmt.where(RobotTask.task_type == task_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(stmt.order_by(RobotTask.created_at.desc(), RobotTask.id).limit(limit).offset(offset)))
    return items, total


# ---------- 생성 ----------

def create_task(db: Session, data: TaskCreate, admin_id: str) -> RobotTask:
    robot_service.get_robot(db, data.robot_id)

    if data.task_type is RobotTaskType.REOBSERVE_BOOK:
        verification = db.get(VerificationResult, data.verification_id)
        if verification is None:
            raise AppError(ErrorCode.NOT_FOUND, f"검증 결과 {data.verification_id}를 찾을 수 없습니다.", status_code=404)
        if verification.workflow_status is not WorkflowStatus.REOBSERVATION_PENDING or verification.observation_id is None:
            raise _invalid_state("재관측 대기(REOBSERVATION_PENDING) 상태인 관측 검증 결과만 재관측할 수 있습니다.")
        task = create_reobservation_task(db, verification, data.robot_id, created_by=admin_id)
        db.add(AdminAction(admin_id=admin_id, action_type=AdminActionType.REQUEST_REOBSERVATION, task_id=task.id,
                           book_copy_id=verification.book_copy_id, comment=f"검증 결과 {verification.id}"))
    else:
        if db.get(Shelf, data.shelf_id) is None:
            raise AppError(ErrorCode.NOT_FOUND, f"서가 '{data.shelf_id}'를 찾을 수 없습니다.", status_code=404)
        fields = {"shelf_id": data.shelf_id}
        if data.task_type is RobotTaskType.SCAN_SHELF:
            # [가정] 작업 생성 시점에 스캔 세션을 만든다. 로봇·AI는 작업의 scan_session_id를 관측에 넣는다.
            session = ScanSession(id=f"SCAN-{utcnow():%Y%m%d%H%M%S}-{secrets.token_hex(3)}",
                                  robot_id=data.robot_id, shelf_id=data.shelf_id)
            db.add(session)
            db.flush()
            fields["scan_session_id"] = session.id
        task = _new_task(db, data.task_type, data.robot_id, created_by=admin_id, **fields)

    db.commit()
    return task


def create_reobservation_task(db: Session, verification: VerificationResult, robot_id: str,
                              created_by: str) -> RobotTask:
    """재관측 작업 생성 (commit은 호출한 쪽에서). 같은 관측에 진행 중인 재관측 작업이 있으면 거부한다."""
    obs = db.get(Observation, verification.observation_id)
    duplicate = db.scalar(select(RobotTask.id).where(
        RobotTask.task_type == RobotTaskType.REOBSERVE_BOOK,
        RobotTask.reobservation_of == obs.id,
        RobotTask.status.in_(ACTIVE_TASK_STATUSES),
    ))
    if duplicate:
        raise _invalid_state(f"이 관측에 대한 재관측 작업 '{duplicate}'가 이미 진행 중입니다.")
    # [가정] 재관측 결과는 원래 관측과 같은 스캔 세션으로 보낸다 (AI가 관측에 넣을 scan_session_id를 명령에 포함).
    return _new_task(db, RobotTaskType.REOBSERVE_BOOK, robot_id, created_by=created_by,
                     shelf_id=obs.shelf_id, slot_id=obs.slot_id, book_copy_id=verification.book_copy_id,
                     scan_session_id=obs.scan_session_id, reobservation_of=obs.id)


# ---------- 로봇: 작업 가져가기 ----------

def next_task(db: Session, robot_id: str) -> RobotTask | None:
    """로봇이 다음에 할 작업.

    1) 취소 명령이 있으면 가장 먼저 준다.
    2) 진행 중이거나 결과 불명(UNKNOWN)인 작업이 있으면 새 작업을 주지 않는다.
    3) 그 외에는 가장 오래된 PENDING 작업. (PENDING_APPROVAL은 절대 주지 않는다)
    가져가기만으로는 상태가 바뀌지 않는다. 로봇이 ack 해야 ACCEPTED가 된다.
    """
    robot = robot_service.get_robot(db, robot_id)
    robot_service.touch(robot)
    db.commit()

    mine = select(RobotTask).where(RobotTask.robot_id == robot_id).order_by(RobotTask.created_at, RobotTask.id)
    cancel = db.scalar(mine.where(RobotTask.task_type == RobotTaskType.CANCEL_TASK, RobotTask.status == S.PENDING))
    if cancel:
        return cancel
    busy = db.scalar(mine.where(RobotTask.task_type != RobotTaskType.CANCEL_TASK,
                                RobotTask.status.in_((*RUNNING, S.UNKNOWN))))
    if busy:
        return None
    return db.scalar(mine.where(RobotTask.status == S.PENDING))


def to_command(task: RobotTask) -> RobotTaskCommand:
    return RobotTaskCommand(
        task_id=task.id, task_type=task.task_type, robot_id=task.robot_id, shelf_id=task.shelf_id,
        slot_id=task.slot_id, book_copy_id=task.book_copy_id, scan_session_id=task.scan_session_id,
        reobservation_of=task.reobservation_of, target_task_id=task.target_task_id,
        issued_at=task.created_at.replace(tzinfo=UTC),
    )


# ---------- 로봇: 수락·결과 보고 ----------

def _load_for_robot_report(db: Session, task_id: str, robot_id: str) -> RobotTask:
    task = get_task(db, task_id, lock=True)  # 같은 작업에 대한 동시 보고를 순서대로 처리
    if task.robot_id != robot_id:
        raise AppError(ErrorCode.FORBIDDEN, f"이 작업은 로봇 '{task.robot_id}'의 작업입니다.", status_code=403)
    robot_service.touch(db.get(Robot, robot_id))
    return task


def _is_replay(db: Session, task: RobotTask, idempotency_key: str) -> bool:
    event = db.scalar(select(TaskEvent).where(TaskEvent.idempotency_key == idempotency_key))
    if event is None:
        return False
    if event.task_id != task.id:
        raise AppError(ErrorCode.DUPLICATE_REQUEST, "다른 작업에서 이미 사용한 idempotency_key입니다.", status_code=409)
    return True


def acknowledge(db: Session, task_id: str, robot_id: str, data: TaskAck) -> RobotTask:
    task = _load_for_robot_report(db, task_id, robot_id)
    if _is_replay(db, task, data.idempotency_key):
        db.commit()
        return task
    if task.status is not S.PENDING:
        # 예: 로봇이 가져간 뒤 관리자가 취소한 작업 → 로봇은 실행하면 안 된다.
        raise _invalid_state(f"현재 상태({task.status})에서는 작업을 수락할 수 없습니다. 작업을 실행하지 마세요.")

    now = utcnow()
    if data.accepted:
        task.status, task.accepted_at = S.ACCEPTED, now
        event_type = "ACCEPTED"
    else:
        task.status, task.finished_at, task.result_message = S.FAILED, now, f"로봇이 거절: {data.reason or '사유 없음'}"
        event_type = "REJECTED"
    _add_event(db, task, event_type, EventSource.ROBOT, S.PENDING, task.status, {"reason": data.reason},
               data.idempotency_key, _to_utc(data.reported_at))
    _on_task_finished(db, task)
    db.commit()
    return task


def report_result(db: Session, task_id: str, robot_id: str, data: TaskResultReport) -> RobotTask:
    task = _load_for_robot_report(db, task_id, robot_id)
    if _is_replay(db, task, data.idempotency_key):
        db.commit()
        return task

    from_status = task.status
    allowed = ROBOT_TRANSITIONS.get(from_status)
    if allowed is None:
        hint = " 먼저 ack로 작업을 수락해야 합니다." if from_status is S.PENDING else ""
        raise _invalid_state(f"현재 상태({from_status})에서는 결과를 보고할 수 없습니다.{hint}")
    if data.status not in allowed:
        raise _invalid_state(f"{from_status} → {data.status} 변경은 허용되지 않습니다.")

    now = utcnow()
    task.status = data.status
    if data.status is S.IN_PROGRESS and task.started_at is None:
        task.started_at = now
    if data.status in TERMINAL:
        task.finished_at = now
        task.result_message = data.message

    payload = {"message": data.message, **(data.payload or {})}
    if from_status is S.UNKNOWN:
        payload["recovered_from_unknown"] = True
    _add_event(db, task, data.event_type or data.status.value, EventSource.ROBOT, from_status, data.status,
               payload, data.idempotency_key, _to_utc(data.reported_at))
    _on_task_finished(db, task)
    db.commit()
    return task


def _on_task_finished(db: Session, task: RobotTask) -> None:
    """작업이 끝났을 때 연결된 데이터 후속 처리."""
    if task.status not in TERMINAL:
        return

    # 스캔 작업 → 스캔 세션 종료. 완료가 아니면 INCOMPLETE (미관측 판정 안 함)
    if task.task_type is RobotTaskType.SCAN_SHELF and task.scan_session_id:
        session = db.get(ScanSession, task.scan_session_id)
        if session.status is ScanSessionStatus.IN_PROGRESS:
            session.status = ScanSessionStatus.COMPLETED if task.status is S.COMPLETED else ScanSessionStatus.INCOMPLETE
            session.finished_at = utcnow()

    # 재관측 작업이 실패·거절·취소 → 재관측 대기에서 멈추지 않도록 관리자 검토로 넘긴다.
    # (완료된 경우는 AI 결과를 기다리며, 오지 않으면 expire_missing_reobservation_results가 처리)
    if task.task_type is RobotTaskType.REOBSERVE_BOOK and task.status is not S.COMPLETED:
        _send_reobservation_to_review(db, task, f"재관측 작업 {task.id}이(가) {task.status}로 끝남")


def _send_reobservation_to_review(db: Session, task: RobotTask, reason: str) -> bool:
    verification = db.scalar(select(VerificationResult).where(
        VerificationResult.observation_id == task.reobservation_of,
        VerificationResult.workflow_status == WorkflowStatus.REOBSERVATION_PENDING,
    ))
    if verification is None:
        return False  # 이미 재관측 결과가 왔거나 다른 처리가 된 경우
    verification.workflow_status = WorkflowStatus.REVIEW_REQUIRED
    verification.note = f"{verification.note}; {reason}" if verification.note else reason
    return True


def _to_utc(dt):
    return dt.astimezone(UTC).replace(tzinfo=None)


# ---------- 관리자: 취소 ----------

def cancel_task(db: Session, task_id: str, admin_id: str) -> RobotTask:
    """- 아직 로봇이 수락하지 않은 작업: 바로 CANCELLED
    - 수락·진행 중·결과 불명 작업: 로봇이 실행 중일 수 있으므로 상태를 직접 바꾸지 않고,
      로봇에게 CANCEL_TASK 명령을 보낸다. 로봇이 실제로 멈춘 뒤 원래 작업을 CANCELLED로 보고한다.
    """
    task = get_task(db, task_id, lock=True)
    from_status = task.status

    if from_status in (S.PENDING_APPROVAL, S.PENDING):
        task.status, task.finished_at, task.result_message = S.CANCELLED, utcnow(), f"관리자 취소 ({admin_id})"
        _add_event(db, task, "CANCELLED", EventSource.ADMIN, from_status, S.CANCELLED, {"admin_id": admin_id})
        _on_task_finished(db, task)
    elif from_status in (*RUNNING, S.UNKNOWN):
        if task.task_type is RobotTaskType.CANCEL_TASK:
            raise _invalid_state("취소 명령 자체는 취소할 수 없습니다.")
        existing = db.scalar(select(RobotTask.id).where(
            RobotTask.task_type == RobotTaskType.CANCEL_TASK, RobotTask.target_task_id == task.id,
            RobotTask.status.in_(ACTIVE_TASK_STATUSES),
        ))
        if existing:
            raise _invalid_state(f"이미 취소 명령 '{existing}'이 전달 대기 중입니다.")
        cancel = _new_task(db, RobotTaskType.CANCEL_TASK, task.robot_id, created_by=admin_id, target_task_id=task.id)
        _add_event(db, task, "CANCEL_REQUESTED", EventSource.ADMIN, from_status, from_status,
                   {"admin_id": admin_id, "cancel_task_id": cancel.id})
    else:
        raise _invalid_state(f"이미 끝난 작업입니다 ({from_status}).")

    db.add(AdminAction(admin_id=admin_id, action_type=AdminActionType.CANCEL_TASK, task_id=task.id,
                       book_copy_id=task.book_copy_id, comment=f"{from_status}에서 취소 요청"))
    db.commit()
    return task


# ---------- 통신 끊김 감시 ----------

def mark_unresponsive(db: Session, timeout_seconds: int) -> list[str]:
    """마지막 통신 후 timeout이 지난 로봇을 OFFLINE으로, 그 로봇의 진행 중 작업을 UNKNOWN으로 바꾼다.

    실패(FAILED)로 확정하지 않는다. 로봇이 돌아와 결과를 보고하면 그때 확정된다.
    """
    cutoff = utcnow() - timedelta(seconds=timeout_seconds)
    stale = or_(Robot.last_seen_at.is_(None), Robot.last_seen_at < cutoff)

    tasks = list(db.scalars(
        select(RobotTask).join(Robot, Robot.id == RobotTask.robot_id)
        .where(RobotTask.status.in_(RUNNING), stale)
        .with_for_update(of=RobotTask)
    ))
    for task in tasks:
        _add_event(db, task, "MARKED_UNKNOWN", EventSource.BACKEND, task.status, S.UNKNOWN,
                   {"reason": f"로봇 무응답 {timeout_seconds}초 초과"})
        task.status = S.UNKNOWN

    for robot in db.scalars(select(Robot).where(Robot.connection_status == RobotConnectionStatus.ONLINE, stale)):
        robot.connection_status = RobotConnectionStatus.OFFLINE

    try:
        db.commit()
    except StaleDataError:
        db.rollback()  # 그 사이 로봇이 보고해 상태가 바뀜 → 다음 검사에서 다시 판단
        return []
    return [t.id for t in tasks]


# ---------- 재관측 결과 누락 감시 ----------

def expire_missing_reobservation_results(db: Session, wait_seconds: int) -> list[str]:
    """로봇이 재관측 촬영을 완료했는데 wait_seconds가 지나도 AI 재관측 결과가 없으면 관리자 검토로 넘긴다.

    이후에 결과가 늦게 와도 버리지 않고 평소대로 판정한다 (verify_observation이 원래 결과를 CLOSED로 바꿈).
    """
    cutoff = utcnow() - timedelta(seconds=wait_seconds)
    result_arrived = select(Observation.id).where(Observation.reobservation_of == RobotTask.reobservation_of).exists()
    tasks = list(db.scalars(
        select(RobotTask)
        .join(VerificationResult, VerificationResult.observation_id == RobotTask.reobservation_of)
        .where(
            RobotTask.task_type == RobotTaskType.REOBSERVE_BOOK,
            RobotTask.status == S.COMPLETED,
            RobotTask.finished_at < cutoff,
            VerificationResult.workflow_status == WorkflowStatus.REOBSERVATION_PENDING,
            ~result_arrived,
        )
    ))
    expired = []
    for task in tasks:
        if _send_reobservation_to_review(db, task, f"재관측 작업 {task.id} 완료 후 {wait_seconds}초 동안 AI 결과 없음"):
            _add_event(db, task, "REOBSERVATION_RESULT_MISSING", EventSource.BACKEND, task.status, task.status,
                       {"wait_seconds": wait_seconds})
            expired.append(task.id)
    db.commit()
    return expired
