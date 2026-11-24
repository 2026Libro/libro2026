"""DB 변경 → 실시간 이벤트 (BE-016).

저장(flush) 시점에 바뀐 내용을 모아 두었다가, commit이 성공한 뒤에만 WebSocket으로 보낸다.
rollback된 변경은 보내지 않는다.

이벤트 형식 [가정: 프론트엔드와 협의 전]
    {"type": "task.updated", "at": "2026-10-30T03:00:00Z", "data": {"id": "TASK-...", "status": "ACCEPTED", ...}}
data에는 화면 갱신에 필요한 핵심 값만 담는다. 자세한 내용은 REST API로 다시 조회한다.

| type                    | 언제                                   |
|-------------------------|----------------------------------------|
| observation.created     | AI 관측 수신                           |
| verification.created    | 검증 결과 생성                         |
| verification.updated    | 처리 흐름(workflow_status) 변경        |
| task.created / updated  | 작업 생성 / 상태 변경                  |
| robot.created / updated | 로봇 등록 / 통신·배터리·상태·위치 변경 (마지막 통신 시각만 바뀐 경우는 제외) |
| alert.created / updated | 알림 생성 / 상태 변경                  |
| basket.created / updated| 바구니 보관 / 직원 회수                |
| copy.updated            | 대출 상태·물리 위치 변경               |
"""
from datetime import UTC
from enum import Enum

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.realtime import hub
from app.db.base import attribute_change, utcnow
from app.models import Alert, BasketItem, BookCopy, Observation, Robot, RobotTask, VerificationResult

_PENDING = "realtime_pending_events"

# 모델별: (이벤트 이름, 변경을 감지할 컬럼, 이벤트에 담을 컬럼)
_WATCH: dict[type, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    Observation: ("observation", (), ("id", "scan_session_id", "robot_id", "slot_id")),
    VerificationResult: ("verification", ("workflow_status",),
                         ("id", "book_copy_id", "identification_status", "location_verdict", "observation_status",
                          "workflow_status", "observed_slot_id", "registered_slot_id")),
    RobotTask: ("task", ("status",), ("id", "task_type", "status", "robot_id", "book_copy_id", "slot_id")),
    Robot: ("robot", ("connection_status", "battery_percent", "reported_state", "current_location"),
            ("id", "connection_status", "battery_percent", "reported_state", "current_location")),
    Alert: ("alert", ("status",), ("id", "alert_type", "status", "message", "book_copy_id", "robot_id", "task_id")),
    BasketItem: ("basket", ("collected_at",), ("id", "book_copy_id", "robot_id", "task_id", "collected_at")),
    BookCopy: ("copy", ("loan_status", "physical_location_type", "physical_slot_id"),
               ("id", "loan_status", "physical_location_type", "physical_slot_id", "physical_robot_id",
                "physical_location_certainty")),
}
_NO_CREATED_EVENT = {BookCopy}  # 복본 등록은 관리 작업이라 실시간으로 보낼 필요 없음
_NO_UPDATED_EVENT = {Observation}


def _jsonable(value):
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
    return value


def _event(name: str, action: str, obj, fields: tuple[str, ...]) -> dict:
    return {
        "type": f"{name}.{action}",
        "at": _jsonable(utcnow()),
        "data": {f: _jsonable(getattr(obj, f)) for f in fields},
    }


@event.listens_for(Session, "after_flush")
def _collect(session: Session, _flush_context) -> None:
    # after_flush 시점에는 새 객체에 id가 붙어 있고, 변경 이력(history)도 아직 남아 있다.
    pending = session.info.setdefault(_PENDING, [])
    for obj in session.new:
        if (spec := _WATCH.get(type(obj))) and type(obj) not in _NO_CREATED_EVENT:
            pending.append(_event(spec[0], "created", obj, spec[2]))
    for obj in session.dirty:
        spec = _WATCH.get(type(obj))
        if spec and type(obj) not in _NO_UPDATED_EVENT and any(attribute_change(obj, a) for a in spec[1]):
            pending.append(_event(spec[0], "updated", obj, spec[2]))


@event.listens_for(Session, "after_commit")
def _publish(session: Session) -> None:
    for e in session.info.pop(_PENDING, []):
        hub.publish(e)


@event.listens_for(Session, "after_rollback")
@event.listens_for(Session, "after_soft_rollback")
def _discard(session: Session, *_args) -> None:
    session.info.pop(_PENDING, None)
