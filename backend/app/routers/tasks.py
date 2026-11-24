"""로봇 작업 API.

- 관리자: 생성, 인출 승인·거절, 취소, 조회
- 로봇  : 수락(ack), 결과 보고(result) — 자기 작업만
"""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.enums import DeviceType, RobotTaskStatus, RobotTaskType
from app.core.ids import ROBOT_ID_PATTERN
from app.core.security import require_admin, require_device
from app.db.session import get_db
from app.models import DeviceCredential, RobotTask
from app.schemas.common import Page
from app.schemas.robot_task import (
    TaskAck,
    TaskCreate,
    TaskDetail,
    TaskEventRead,
    TaskRead,
    TaskReject,
    TaskResultReport,
)
from app.services import retrieval as retrieval_service
from app.services import robot_task as task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _detail(db: Session, task: RobotTask) -> TaskDetail:
    events = [TaskEventRead.model_validate(e) for e in task_service.get_events(db, task.id)]
    return TaskDetail(**TaskRead.model_validate(task).model_dump(), events=events)


@router.post("", response_model=TaskDetail, status_code=status.HTTP_201_CREATED)
def create_task(data: TaskCreate, db: Session = Depends(get_db), admin_id: str = Depends(require_admin)):
    """작업 생성.

    - MOVE_TO_SHELF, SCAN_SHELF, REOBSERVE_BOOK: 바로 PENDING이 되어 로봇이 가져갈 수 있다.
    - RETRIEVE_MISPLACED_BOOK: 안전 조건 검사 후 PENDING_APPROVAL로 생성된다. 승인(/approve) 전에는 로봇에 전달되지 않는다.
    """
    return _detail(db, task_service.create_task(db, data, admin_id))


@router.get("", response_model=Page[TaskRead])
def list_tasks(
    robot_id: str | None = Query(default=None, pattern=ROBOT_ID_PATTERN),
    status: RobotTaskStatus | None = None,
    task_type: RobotTaskType | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = task_service.list_tasks(db, robot_id, status, task_type, limit, offset)
    return Page[TaskRead](items=items, total=total, limit=limit, offset=offset)


@router.get("/{task_id}", response_model=TaskDetail)
def get_task(task_id: str, db: Session = Depends(get_db)):
    """작업 상세 + 단계별 이벤트 이력."""
    return _detail(db, task_service.get_task(db, task_id))


@router.post("/{task_id}/ack", response_model=TaskDetail)
def acknowledge(task_id: str, data: TaskAck, db: Session = Depends(get_db),
                cred: DeviceCredential = Depends(require_device(DeviceType.ROBOT))):
    """로봇의 작업 수락/거절. 응답 status가 ACCEPTED일 때만 실행한다. 409면 실행하지 않는다."""
    return _detail(db, task_service.acknowledge(db, task_id, cred.device_id, data))


@router.post("/{task_id}/result", response_model=TaskDetail)
def report_result(task_id: str, data: TaskResultReport, db: Session = Depends(get_db),
                  cred: DeviceCredential = Depends(require_device(DeviceType.ROBOT))):
    """로봇의 진행·완료·실패·취소 보고. 통신 복구 후 UNKNOWN 작업의 실제 결과도 이것으로 보고한다."""
    return _detail(db, task_service.report_result(db, task_id, cred.device_id, data))


@router.post("/{task_id}/cancel", response_model=TaskDetail)
def cancel_task(task_id: str, db: Session = Depends(get_db), admin_id: str = Depends(require_admin)):
    """작업 취소. 로봇이 아직 수락하지 않았으면 바로 취소되고, 실행 중이면 로봇에 취소 명령(CANCEL_TASK)을 보낸다."""
    return _detail(db, task_service.cancel_task(db, task_id, admin_id))


@router.post("/{task_id}/approve", response_model=TaskDetail)
def approve_retrieval(task_id: str, db: Session = Depends(get_db), admin_id: str = Depends(require_admin)):
    """인출 승인. 안전 조건을 다시 검사하고, 통과하면 PENDING이 되어 로봇이 가져갈 수 있다."""
    return _detail(db, retrieval_service.approve(db, task_id, admin_id))


@router.post("/{task_id}/reject", response_model=TaskDetail)
def reject_retrieval(task_id: str, data: TaskReject, db: Session = Depends(get_db),
                     admin_id: str = Depends(require_admin)):
    """인출 거절 (사유 필수). 로봇으로 인출하지 않고 사람이 직접 처리하는 것으로 본다."""
    return _detail(db, retrieval_service.reject(db, task_id, admin_id, data.reason))
