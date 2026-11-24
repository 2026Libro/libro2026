"""로봇 등록·상태 관리 (BE-009)."""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import RobotConnectionStatus, RobotTaskStatus
from app.core.errors import AppError
from app.db.base import utcnow
from app.models import Robot, RobotTask
from app.schemas.common import ErrorCode
from app.schemas.robot import RobotCreate, RobotRead, RobotStatusReport, RobotStatusResponse


def is_online(robot: Robot, now: datetime, timeout_seconds: int) -> bool:
    return robot.last_seen_at is not None and now - robot.last_seen_at <= timedelta(seconds=timeout_seconds)


def to_read(robot: Robot, timeout_seconds: int) -> RobotRead:
    online = is_online(robot, utcnow(), timeout_seconds)
    return RobotRead(
        id=robot.id, name=robot.name,
        connection_status=RobotConnectionStatus.ONLINE if online else RobotConnectionStatus.OFFLINE,
        battery_percent=robot.battery_percent, current_location=robot.current_location,
        reported_state=robot.reported_state, last_seen_at=robot.last_seen_at,
    )


def get_robot(db: Session, robot_id: str) -> Robot:
    robot = db.get(Robot, robot_id)
    if robot is None:
        raise AppError(ErrorCode.NOT_FOUND, f"로봇 '{robot_id}'를 찾을 수 없습니다.", status_code=404)
    return robot


def create_robot(db: Session, data: RobotCreate) -> Robot:
    if db.get(Robot, data.id):
        raise AppError(ErrorCode.ALREADY_EXISTS, f"로봇 '{data.id}'가 이미 있습니다.", status_code=409)
    robot = Robot(id=data.id, name=data.name)
    db.add(robot)
    db.commit()
    return robot


def list_robots(db: Session) -> list[Robot]:
    return list(db.scalars(select(Robot).order_by(Robot.id)))


def touch(robot: Robot) -> None:
    """로봇에게서 요청이 오면 통신 중인 것으로 기록한다 (상태 보고뿐 아니라 작업 조회·보고도 포함)."""
    robot.last_seen_at = utcnow()
    robot.connection_status = RobotConnectionStatus.ONLINE


def report_status(db: Session, robot_id: str, data: RobotStatusReport, timeout_seconds: int) -> RobotStatusResponse:
    robot = get_robot(db, robot_id)
    robot.battery_percent = data.battery_percent
    robot.current_location = data.current_location
    robot.reported_state = data.reported_state
    touch(robot)
    db.commit()

    unknown = db.scalars(select(RobotTask.id).where(
        RobotTask.robot_id == robot_id, RobotTask.status == RobotTaskStatus.UNKNOWN,
    ).order_by(RobotTask.created_at))
    return RobotStatusResponse(robot=to_read(robot, timeout_seconds), tasks_needing_result=list(unknown))
