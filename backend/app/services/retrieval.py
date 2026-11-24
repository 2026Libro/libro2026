"""오배가 도서 인출·바구니 보관·직원 회수 (BE-011, BE-012, BE-014).

안전 원칙
- 인출 작업은 PENDING_APPROVAL로만 만들어진다. 관리자 승인 전에는 로봇에 전달되지 않는다.
- 안전 조건은 요청 생성 시와 승인 시 두 번 검사한다 (그 사이 상황이 바뀔 수 있음).
- 같은 도서에 진행 중인 인출 작업은 하나뿐이다 (서비스 검사 + DB UNIQUE 제약).
- 물리 위치는 로봇이 보고한 단계(GRIPPED, STORED_IN_BASKET, COMPLETED)로만 '확정' 갱신한다.
- 실패·취소·결과 불명이면 마지막으로 확인된 물리 위치를 그대로 둔다. 자동 재전송하지 않는다.
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.enums import (
    AdminActionType,
    EventSource,
    IdentificationStatus,
    LoanStatus,
    LocationCertainty,
    LocationVerdict,
    PhysicalLocationType,
    RobotTaskStatus,
    RobotTaskType,
    WorkflowStatus,
)
from app.core.errors import AppError
from app.db.base import utcnow
from app.models import AdminAction, BasketItem, BookCopy, RobotTask, ShelfSlot, VerificationResult
from app.models.robot import ACTIVE_TASK_STATUSES
from app.schemas.common import ErrorCode
from app.services import robot_task as task_core

S = RobotTaskStatus
GRIPPED = "GRIPPED"                    # [가정] 로봇 담당자와 단계 이름 협의
STORED_IN_BASKET = "STORED_IN_BASKET"


def _note(v: VerificationResult, text: str) -> None:
    v.note = f"{v.note}; {text}" if v.note else text


# ---------- 안전 조건 ----------

def check_conditions(db: Session, verification: VerificationResult, exclude_task_id: str | None = None) -> list[str]:
    """인출해도 되는지 검사한다. 문제가 있으면 사유 목록을, 없으면 빈 목록을 돌려준다.

    exclude_task_id: 승인 시 재검사할 때 승인 대상 작업 자신은 '진행 중 인출'에서 뺀다.
    """
    problems = []
    if verification.location_verdict is not LocationVerdict.MISPLACED_CONFIRMED:
        problems.append(f"오배가 확정 상태가 아님 ({verification.location_verdict})")
    if verification.identification_status is not IdentificationStatus.IDENTIFIED or not verification.book_copy_id:
        problems.append("복본이 확정(IDENTIFIED)되지 않음")
        return problems

    copy = db.get(BookCopy, verification.book_copy_id, options=[selectinload(BookCopy.registered_location)])
    if get_settings().loan_status_reliable and copy.loan_status is not LoanStatus.AVAILABLE:
        problems.append(f"대출 상태가 {copy.loan_status}")
    if (copy.physical_location_type is not PhysicalLocationType.SHELF
            or copy.physical_slot_id != verification.observed_slot_id
            or copy.physical_location_certainty is not LocationCertainty.CONFIRMED):
        problems.append(f"마지막 확인 위치({copy.physical_location_type} {copy.physical_slot_id or ''})가 "
                        f"관측 칸({verification.observed_slot_id})과 다름")
    registered = copy.registered_location.slot_id if copy.registered_location else None
    if registered == verification.observed_slot_id:
        problems.append("등록 위치가 이미 관측 칸으로 변경됨 (인출 불필요)")

    active = db.scalar(select(RobotTask.id).where(
        RobotTask.task_type == RobotTaskType.RETRIEVE_MISPLACED_BOOK,
        RobotTask.book_copy_id == copy.id,
        RobotTask.status.in_(ACTIVE_TASK_STATUSES),
        RobotTask.id != (exclude_task_id or ""),
    ))
    if active:
        problems.append(f"이미 진행 중인 인출 작업 {active}")
    return problems


def _raise_if_unsafe(problems: list[str]) -> None:
    if problems:
        raise AppError(ErrorCode.INVALID_STATE, "인출 안전 조건을 만족하지 않습니다.", status_code=409,
                       details=[{"field": None, "message": p} for p in problems])


# ---------- 인출 요청 생성 ----------

def create_request(db: Session, verification: VerificationResult, robot_id: str, created_by: str) -> RobotTask:
    """인출 요청(PENDING_APPROVAL) 생성 (commit은 호출한 쪽에서)."""
    _raise_if_unsafe(check_conditions(db, verification))
    slot = db.get(ShelfSlot, verification.observed_slot_id)
    return task_core.new_task(
        db, RobotTaskType.RETRIEVE_MISPLACED_BOOK, robot_id, created_by=created_by, status=S.PENDING_APPROVAL,
        shelf_id=slot.shelf_id, slot_id=slot.id, book_copy_id=verification.book_copy_id,
        verification_id=verification.id,
    )


def try_create_request(db: Session, verification: VerificationResult, robot_id: str) -> RobotTask | None:
    """오배가 확정 시 자동 요청. 조건이 안 맞으면 만들지 않고 사유를 검증 결과 비고에 남긴다."""
    if problems := check_conditions(db, verification):
        _note(verification, "인출 요청 자동 생성 안 함: " + ", ".join(problems))
        return None
    task = create_request(db, verification, robot_id, created_by="system")
    _note(verification, f"인출 요청 {task.id} 생성 (관리자 승인 대기)")
    return task


def _commit_or_conflict(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:  # DB UNIQUE 제약: 같은 도서의 활성 인출 작업이 동시에 만들어진 경우
        db.rollback()
        raise AppError(ErrorCode.INVALID_STATE, "같은 도서에 진행 중인 인출 작업이 이미 있습니다.", status_code=409)


def create_request_by_admin(db: Session, verification_id: int, robot_id: str, admin_id: str) -> RobotTask:
    verification = _get_verification(db, verification_id)
    task = create_request(db, verification, robot_id, created_by=admin_id)
    _commit_or_conflict(db)
    return task


def _get_verification(db: Session, verification_id: int) -> VerificationResult:
    v = db.get(VerificationResult, verification_id)
    if v is None:
        raise AppError(ErrorCode.NOT_FOUND, f"검증 결과 {verification_id}를 찾을 수 없습니다.", status_code=404)
    return v


# ---------- 관리자 승인·거절 ----------

def _load_pending_approval(db: Session, task_id: str) -> RobotTask:
    task = task_core.get_task(db, task_id, lock=True)
    if task.task_type is not RobotTaskType.RETRIEVE_MISPLACED_BOOK:
        raise AppError(ErrorCode.INVALID_STATE, "승인·거절은 인출 작업에만 할 수 있습니다.", status_code=409)
    if task.status is not S.PENDING_APPROVAL:
        raise AppError(ErrorCode.INVALID_STATE, f"승인 대기 상태가 아닙니다 ({task.status}).", status_code=409)
    return task


def approve(db: Session, task_id: str, admin_id: str) -> RobotTask:
    task = _load_pending_approval(db, task_id)
    verification = _get_verification(db, task.verification_id)
    # 요청 이후 상황이 바뀌었을 수 있으므로 다시 검사한다 (예: 그 사이 대출됨, 다른 칸에서 관측됨).
    _raise_if_unsafe(check_conditions(db, verification, exclude_task_id=task.id))

    task.status, task.approved_by, task.approved_at = S.PENDING, admin_id, utcnow()
    verification.workflow_status = WorkflowStatus.APPROVED
    task_core.add_event(db, task, "APPROVED", EventSource.ADMIN, S.PENDING_APPROVAL, S.PENDING, {"admin_id": admin_id})
    db.add(AdminAction(admin_id=admin_id, action_type=AdminActionType.APPROVE_RETRIEVAL, task_id=task.id,
                       book_copy_id=task.book_copy_id))
    db.commit()
    return task


def reject(db: Session, task_id: str, admin_id: str, reason: str) -> RobotTask:
    """거절: 로봇으로 인출하지 않는다. 사람이 직접 처리하는 것으로 보고 검증 결과를 닫는다."""
    task = _load_pending_approval(db, task_id)
    task.status, task.finished_at, task.result_message = S.CANCELLED, utcnow(), f"관리자 거절: {reason}"
    verification = _get_verification(db, task.verification_id)
    verification.workflow_status = WorkflowStatus.CLOSED
    _note(verification, f"인출 거절 ({admin_id}): {reason}")
    task_core.add_event(db, task, "REJECTED_BY_ADMIN", EventSource.ADMIN, S.PENDING_APPROVAL, S.CANCELLED,
                         {"admin_id": admin_id, "reason": reason})
    db.add(AdminAction(admin_id=admin_id, action_type=AdminActionType.REJECT_RETRIEVAL, task_id=task.id,
                       book_copy_id=task.book_copy_id, comment=reason))
    db.commit()
    return task


# ---------- 로봇 보고 → 물리 위치 ----------

def _set_physical(copy: BookCopy, location: PhysicalLocationType, robot_id: str | None, when: datetime) -> None:
    copy.physical_location_type = location
    copy.physical_slot_id = None
    copy.physical_robot_id = robot_id
    copy.physical_location_certainty = LocationCertainty.CONFIRMED  # 로봇이 직접 보고한 위치
    copy.physical_location_confirmed_at = when


def on_robot_report(db: Session, task: RobotTask, event_type: str | None, when: datetime) -> None:
    """인출 작업의 로봇 보고를 물리 위치·바구니에 반영한다 (report_result에서 호출, commit은 호출한 쪽에서)."""
    if task.task_type is not RobotTaskType.RETRIEVE_MISPLACED_BOOK:
        return
    copy = db.get(BookCopy, task.book_copy_id)
    if event_type == GRIPPED:
        _set_physical(copy, PhysicalLocationType.ROBOT_GRIPPER, task.robot_id, when)
    # COMPLETED = 인출 + 바구니 보관까지 끝남. STORED_IN_BASKET 보고를 빠뜨렸어도 바구니에 기록한다.
    if event_type == STORED_IN_BASKET or task.status is S.COMPLETED:
        already = db.scalar(select(BasketItem).where(BasketItem.task_id == task.id))
        if already is None:
            db.add(BasketItem(book_copy_id=copy.id, robot_id=task.robot_id, task_id=task.id, stored_at=when))
            _set_physical(copy, PhysicalLocationType.BASKET, task.robot_id, when)


def on_task_finished(db: Session, task: RobotTask) -> None:
    """인출 작업이 실패·취소로 끝나면 관리자 검토로. 물리 위치는 마지막 확인 위치를 그대로 둔다."""
    if task.task_type is not RobotTaskType.RETRIEVE_MISPLACED_BOOK or task.status is S.COMPLETED:
        return
    verification = db.get(VerificationResult, task.verification_id)
    if verification is None or verification.workflow_status is WorkflowStatus.CLOSED:
        return  # 거절(reject)로 이미 닫힌 경우
    copy = db.get(BookCopy, task.book_copy_id)
    verification.workflow_status = WorkflowStatus.REVIEW_REQUIRED
    where = copy.physical_slot_id or copy.physical_robot_id or ""
    _note(verification, f"인출 작업 {task.id} {task.status}: 마지막 확인 위치 {copy.physical_location_type} {where}")


# ---------- 바구니·직원 회수 ----------

def list_basket(db: Session, robot_id: str | None, include_collected: bool) -> list[BasketItem]:
    stmt = select(BasketItem).order_by(BasketItem.stored_at.desc(), BasketItem.id.desc())
    if robot_id:
        stmt = stmt.where(BasketItem.robot_id == robot_id)
    if not include_collected:
        stmt = stmt.where(BasketItem.collected_at.is_(None))
    return list(db.scalars(stmt))


def collect(db: Session, item_id: int, admin_id: str) -> BasketItem:
    """직원이 바구니에서 책을 꺼냈음을 기록한다. 이후 제자리에 꽂으면 다음 스캔에서 위치가 확인된다."""
    item = db.get(BasketItem, item_id, with_for_update=True)
    if item is None:
        raise AppError(ErrorCode.NOT_FOUND, f"바구니 기록 {item_id}를 찾을 수 없습니다.", status_code=404)
    if item.collected_at:
        raise AppError(ErrorCode.INVALID_STATE, "이미 회수 처리된 도서입니다.", status_code=409)

    now = utcnow()
    item.collected_at, item.collected_by = now, admin_id
    _set_physical(db.get(BookCopy, item.book_copy_id), PhysicalLocationType.COLLECTED_BY_STAFF, None, now)

    task = db.get(RobotTask, item.task_id)
    if task.verification_id and (v := db.get(VerificationResult, task.verification_id)):
        v.workflow_status = WorkflowStatus.CLOSED
        _note(v, f"직원 회수 ({admin_id})")
    db.add(AdminAction(admin_id=admin_id, action_type=AdminActionType.MARK_COLLECTED, task_id=item.task_id,
                       book_copy_id=item.book_copy_id))
    db.commit()
    return item
