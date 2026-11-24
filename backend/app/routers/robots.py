"""로봇 API. 등록은 관리자, 상태 보고·작업 가져가기는 해당 로봇의 API 키로만 할 수 있다."""
from fastapi import APIRouter, Depends, Path, Response, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.enums import DeviceType
from app.core.errors import AppError
from app.core.ids import ROBOT_ID_PATTERN
from app.core.security import require_admin, require_device
from app.db.session import get_db
from app.models import DeviceCredential
from app.schemas.common import ErrorCode
from app.schemas.robot import RobotCreate, RobotRead, RobotStatusReport, RobotStatusResponse
from app.schemas.robot_task import RobotTaskCommand
from app.services import robot as robot_service
from app.services import robot_task as task_service

router = APIRouter(prefix="/robots", tags=["robots"])

RobotIdPath = Path(pattern=ROBOT_ID_PATTERN, examples=["R001"])


def _require_same_robot(robot_id: str, cred: DeviceCredential) -> None:
    if cred.device_id != robot_id:
        raise AppError(ErrorCode.FORBIDDEN, f"로봇 '{cred.device_id}'의 키로 '{robot_id}'를 대신할 수 없습니다.",
                       status_code=403)


@router.post("", response_model=RobotRead, status_code=status.HTTP_201_CREATED)
def create_robot(data: RobotCreate, db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
                 _admin: str = Depends(require_admin)):
    return robot_service.to_read(robot_service.create_robot(db, data), settings.robot_offline_after_seconds)


@router.get("", response_model=list[RobotRead])
def list_robots(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    return [robot_service.to_read(r, settings.robot_offline_after_seconds) for r in robot_service.list_robots(db)]


@router.get("/{robot_id}", response_model=RobotRead)
def get_robot(robot_id: str = RobotIdPath, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    return robot_service.to_read(robot_service.get_robot(db, robot_id), settings.robot_offline_after_seconds)


@router.post("/{robot_id}/status", response_model=RobotStatusResponse)
def report_status(
    data: RobotStatusReport,
    robot_id: str = RobotIdPath,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    cred: DeviceCredential = Depends(require_device(DeviceType.ROBOT)),
):
    """로봇 상태 보고 (주기적으로). 응답의 tasks_needing_result에 작업이 있으면 그 결과를 먼저 보고해야 한다."""
    _require_same_robot(robot_id, cred)
    return robot_service.report_status(db, robot_id, data, settings.robot_offline_after_seconds)


@router.get(
    "/{robot_id}/tasks/next",
    response_model=RobotTaskCommand,
    responses={204: {"description": "지금 할 작업이 없음 (또는 진행 중·결과 불명 작업이 있어 새 작업을 주지 않음)"}},
)
def next_task(
    robot_id: str = RobotIdPath,
    db: Session = Depends(get_db),
    cred: DeviceCredential = Depends(require_device(DeviceType.ROBOT)),
):
    """로봇이 다음 작업을 가져간다. 가져가기만으로는 상태가 바뀌지 않으며, ack로 수락해야 한다."""
    _require_same_robot(robot_id, cred)
    task = task_service.next_task(db, robot_id)
    if task is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return task_service.to_command(task)
