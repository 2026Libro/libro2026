"""7단계 로봇 상태·작업 관리 테스트."""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.enums import DeviceType, RobotTaskStatus, RobotTaskType
from app.db.base import utcnow
from app.main import app
from app.models import Robot, RobotTask, ScanSession, VerificationResult
from app.services.device import issue_api_key
from app.services.robot_task import mark_unresponsive
from tests.conftest import ADMIN_HEADERS, TEST_ADMIN_TOKEN

NOW_ISO = "2026-10-29T12:00:00+09:00"


@pytest.fixture
def env(client, db):
    for robot_id in ("R900", "R901"):
        assert client.post("/robots", json={"id": robot_id}, headers=ADMIN_HEADERS).status_code == 201
    client.post("/shelves", json={"id": "T-01", "slot_count": 3}, headers=ADMIN_HEADERS)
    return {
        "robot": {"X-API-Key": issue_api_key(db, DeviceType.ROBOT, "R900")},
        "other": {"X-API-Key": issue_api_key(db, DeviceType.ROBOT, "R901")},
        "ai": {"X-API-Key": issue_api_key(db, DeviceType.AI, "ai-test")},
    }


def create(client, task_type="MOVE_TO_SHELF", **fields) -> dict:
    res = client.post("/tasks", json={"task_type": task_type, "robot_id": "R900", "shelf_id": "T-01", **fields},
                      headers=ADMIN_HEADERS)
    assert res.status_code == 201, res.text
    return res.json()


def ack(client, env, task_id, accepted=True, key=None, who="robot"):
    return client.post(f"/tasks/{task_id}/ack", headers=env[who], json={
        "accepted": accepted, "idempotency_key": key or str(uuid.uuid4()), "reported_at": NOW_ISO})


def result(client, env, task_id, status, key=None, who="robot", **extra):
    return client.post(f"/tasks/{task_id}/result", headers=env[who], json={
        "status": status, "idempotency_key": key or str(uuid.uuid4()), "reported_at": NOW_ISO, **extra})


def next_task(client, env, robot_id="R900", who="robot"):
    return client.get(f"/robots/{robot_id}/tasks/next", headers=env[who])


def make_robot_silent(db, robot_id="R900", seconds=120):
    db.get(Robot, robot_id).last_seen_at = utcnow() - timedelta(seconds=seconds)
    db.commit()


# ---------- 로봇 등록·상태 ----------

def test_register_robot_requires_admin_and_unique(client, env):
    assert client.post("/robots", json={"id": "R902"}).status_code == 401
    assert client.post("/robots", json={"id": "R900"}, headers=ADMIN_HEADERS).status_code == 409


def test_new_robot_is_offline_until_it_reports(client, env):
    assert client.get("/robots/R900").json()["connection_status"] == "OFFLINE"


def test_status_report(client, env):
    res = client.post("/robots/R900/status", headers=env["robot"], json={
        "battery_percent": 87, "current_location": {"shelf_id": "T-01"}, "reported_state": "IDLE",
        "reported_at": NOW_ISO})
    assert res.status_code == 200
    robot = res.json()["robot"]
    assert (robot["connection_status"], robot["battery_percent"]) == ("ONLINE", 87)
    assert robot["last_seen_at"] is not None


def test_robot_cannot_report_for_another_robot(client, env):
    res = client.post("/robots/R901/status", headers=env["robot"], json={"reported_at": NOW_ISO})
    assert res.status_code == 403


# ---------- 작업 생성 ----------

def test_create_task_is_pending_with_event(client, env):
    task = create(client)
    assert task["status"] == "PENDING"
    assert [e["event_type"] for e in task["events"]] == ["CREATED"]


@pytest.mark.parametrize("payload, status", [
    ({"task_type": "RETRIEVE_MISPLACED_BOOK", "robot_id": "R900", "shelf_id": "T-01"}, 422),  # verification_id 필요
    ({"task_type": "CANCEL_TASK", "robot_id": "R900", "shelf_id": "T-01"}, 422),              # cancel API 사용
    ({"task_type": "MOVE_TO_SHELF", "robot_id": "R900"}, 422),                                # 서가 없음
    ({"task_type": "MOVE_TO_SHELF", "robot_id": "R999", "shelf_id": "T-01"}, 404),
    ({"task_type": "MOVE_TO_SHELF", "robot_id": "R900", "shelf_id": "Z-99"}, 404),
])
def test_invalid_task_rejected(client, env, payload, status):
    assert client.post("/tasks", json=payload, headers=ADMIN_HEADERS).status_code == status


def test_scan_task_creates_scan_session(client, db, env):
    task = create(client, "SCAN_SHELF")
    session = db.get(ScanSession, task["scan_session_id"])
    assert (session.robot_id, session.shelf_id, session.status) == ("R900", "T-01", "IN_PROGRESS")


# ---------- 작업 가져가기 ----------

def test_next_returns_command_without_changing_status(client, env):
    task = create(client)
    res = next_task(client, env)
    assert res.status_code == 200
    assert res.json()["task_id"] == task["id"]
    assert client.get(f"/tasks/{task['id']}").json()["status"] == "PENDING"  # ack 전까지 그대로


def test_next_empty_returns_204(client, env):
    assert next_task(client, env).status_code == 204


def test_robot_only_gets_own_tasks(client, env):
    create(client)
    assert next_task(client, env, "R901", who="other").status_code == 204
    assert next_task(client, env, "R900", who="other").status_code == 403


def test_pending_approval_task_never_delivered(client, db, env):
    db.add(RobotTask(id="TASK-T-APPROVAL", task_type=RobotTaskType.MOVE_TO_SHELF,
                     status=RobotTaskStatus.PENDING_APPROVAL, robot_id="R900", shelf_id="T-01", created_by="t"))
    db.commit()
    assert next_task(client, env).status_code == 204


def test_busy_robot_gets_no_new_task(client, env):
    first, _second = create(client), create(client)
    ack(client, env, first["id"])
    assert next_task(client, env).status_code == 204


# ---------- 수락·결과 ----------

def test_full_task_lifecycle(client, env):
    task_id = create(client)["id"]
    assert ack(client, env, task_id).json()["status"] == "ACCEPTED"
    assert result(client, env, task_id, "IN_PROGRESS", event_type="ARRIVED").json()["status"] == "IN_PROGRESS"
    done = result(client, env, task_id, "COMPLETED", message="ok").json()

    assert done["status"] == "COMPLETED"
    assert done["accepted_at"] and done["started_at"] and done["finished_at"]
    assert [e["event_type"] for e in done["events"]] == ["CREATED", "ACCEPTED", "ARRIVED", "COMPLETED"]


def test_result_before_ack_rejected(client, env):
    task_id = create(client)["id"]
    res = result(client, env, task_id, "COMPLETED")
    assert res.status_code == 409
    assert "ack" in res.json()["error"]["message"]


def test_robot_rejects_task(client, env):
    task_id = create(client)["id"]
    res = ack(client, env, task_id, accepted=False)
    assert res.json()["status"] == "FAILED"


def test_ack_replay_is_idempotent(client, env):
    task_id = create(client)["id"]
    ack(client, env, task_id, key="ack-key-0001")
    res = ack(client, env, task_id, key="ack-key-0001")                # 같은 보고 재전송
    assert res.status_code == 200
    assert [e["event_type"] for e in res.json()["events"]] == ["CREATED", "ACCEPTED"]
    assert ack(client, env, task_id).status_code == 409                # 다른 보고로 두 번 수락은 불가


def test_result_replay_is_idempotent(client, env):
    task_id = create(client)["id"]
    ack(client, env, task_id)
    result(client, env, task_id, "COMPLETED", key="res-key-0001")
    res = result(client, env, task_id, "COMPLETED", key="res-key-0001")
    assert res.status_code == 200
    assert len(res.json()["events"]) == 3


def test_idempotency_key_reused_on_other_task_rejected(client, env):
    a, b = create(client)["id"], create(client)["id"]
    ack(client, env, a, key="shared-key-01")
    assert ack(client, env, b, key="shared-key-01").status_code == 409


def test_finished_task_cannot_change(client, env):
    task_id = create(client)["id"]
    ack(client, env, task_id)
    result(client, env, task_id, "FAILED")
    assert result(client, env, task_id, "COMPLETED").status_code == 409


def test_other_robot_cannot_report(client, env):
    task_id = create(client)["id"]
    assert ack(client, env, task_id, who="other").status_code == 403


@pytest.mark.parametrize("final, session_status", [("COMPLETED", "COMPLETED"), ("FAILED", "INCOMPLETE")])
def test_scan_task_result_finishes_session(client, db, env, final, session_status):
    task = create(client, "SCAN_SHELF")
    ack(client, env, task["id"])
    result(client, env, task["id"], final)
    db.expire_all()
    assert db.get(ScanSession, task["scan_session_id"]).status == session_status


# ---------- 취소 ----------

def test_cancel_pending_task(client, env):
    task_id = create(client)["id"]
    next_task(client, env)                                             # 로봇이 이미 가져간 상태
    res = client.post(f"/tasks/{task_id}/cancel", headers=ADMIN_HEADERS)
    assert res.json()["status"] == "CANCELLED"
    assert ack(client, env, task_id).status_code == 409                # 취소된 작업은 수락 불가 → 로봇은 실행 안 함


def test_cancel_running_task_sends_cancel_command(client, env):
    task_id = create(client)["id"]
    ack(client, env, task_id)
    res = client.post(f"/tasks/{task_id}/cancel", headers=ADMIN_HEADERS)
    assert res.json()["status"] == "ACCEPTED"                          # 로봇이 멈췄는지 모르므로 그대로

    cmd = next_task(client, env).json()                                # 바쁜 로봇에게도 취소 명령은 전달
    assert (cmd["task_type"], cmd["target_task_id"]) == ("CANCEL_TASK", task_id)
    assert client.post(f"/tasks/{task_id}/cancel", headers=ADMIN_HEADERS).status_code == 409  # 중복 취소 명령

    ack(client, env, cmd["task_id"])
    result(client, env, task_id, "CANCELLED")                          # 로봇이 실제로 멈춘 뒤 보고
    result(client, env, cmd["task_id"], "COMPLETED")
    assert client.get(f"/tasks/{task_id}").json()["status"] == "CANCELLED"


def test_cancel_finished_task_rejected(client, env):
    task_id = create(client)["id"]
    ack(client, env, task_id)
    result(client, env, task_id, "COMPLETED")
    assert client.post(f"/tasks/{task_id}/cancel", headers=ADMIN_HEADERS).status_code == 409


def test_cancel_requires_admin(client, env):
    task_id = create(client)["id"]
    assert client.post(f"/tasks/{task_id}/cancel").status_code == 401


# ---------- 통신 끊김 → UNKNOWN ----------

def test_silent_robot_task_becomes_unknown_not_failed(client, db, env):
    running, waiting = create(client)["id"], create(client)["id"]
    ack(client, env, running)
    result(client, env, running, "IN_PROGRESS")
    make_robot_silent(db)

    assert mark_unresponsive(db, timeout_seconds=60) == [running]
    detail = client.get(f"/tasks/{running}").json()
    assert detail["status"] == "UNKNOWN"
    assert detail["events"][-1]["event_type"] == "MARKED_UNKNOWN"
    assert client.get(f"/tasks/{waiting}").json()["status"] == "PENDING"  # 아직 전달 안 된 작업은 그대로
    assert client.get("/robots/R900").json()["connection_status"] == "OFFLINE"


def test_responsive_robot_task_not_marked(client, db, env):
    task_id = create(client)["id"]
    ack(client, env, task_id)                                          # 방금 통신함
    assert mark_unresponsive(db, timeout_seconds=60) == []


def test_unknown_task_is_not_resent_and_blocks_new_tasks(client, db, env):
    task_id = create(client)["id"]
    ack(client, env, task_id)
    make_robot_silent(db)
    mark_unresponsive(db, timeout_seconds=60)
    create(client)

    assert next_task(client, env).status_code == 204                   # 자동 재전송도, 새 작업도 없음
    status = client.post("/robots/R900/status", headers=env["robot"], json={"reported_at": NOW_ISO}).json()
    assert status["tasks_needing_result"] == [task_id]                 # 결과를 먼저 보고하라고 알려 줌


def test_unknown_task_resolved_by_robot_result(client, db, env):
    task_id = create(client)["id"]
    ack(client, env, task_id)
    make_robot_silent(db)
    mark_unresponsive(db, timeout_seconds=60)

    done = result(client, env, task_id, "COMPLETED").json()
    assert done["status"] == "COMPLETED"
    assert done["events"][-1]["payload"]["recovered_from_unknown"] is True


# ---------- 재관측 작업 ----------

def _low_confidence_observation(client, env, db, obs_id="OBS-R-001"):
    session = create(client, "SCAN_SHELF")["scan_session_id"]
    res = client.post("/observations", headers=env["ai"], json={
        "observation_id": obs_id, "scan_session_id": session, "robot_id": "R900", "shelf_id": "T-01",
        "slot_id": "T-01-02", "detected_text": None, "book_copy_id": None,
        "confidence": {"detection": 0.9, "ocr": 0.1, "identification": 0.0}, "candidates": [],
        "reobservation_of": None, "image_ref": None, "captured_at": NOW_ISO})
    assert res.status_code == 201
    page = client.get("/verifications", params={"scan_session_id": session}).json()
    return session, page["items"][0]


def _reobserve_tasks(db, obs_id):
    return list(db.scalars(select(RobotTask).where(RobotTask.task_type == RobotTaskType.REOBSERVE_BOOK,
                                                   RobotTask.reobservation_of == obs_id)))


def test_reobservation_task_created_automatically(client, db, env):
    session, verification = _low_confidence_observation(client, env, db)
    assert verification["workflow_status"] == "REOBSERVATION_PENDING"

    [task] = _reobserve_tasks(db, "OBS-R-001")
    assert (task.created_by, task.status, task.slot_id, task.scan_session_id) == ("system", "PENDING", "T-01-02", session)
    # 이미 진행 중인 재관측이 있으므로 관리자가 같은 관측으로 또 만들 수 없다
    res = client.post("/tasks", json={"task_type": "REOBSERVE_BOOK", "robot_id": "R900",
                                      "verification_id": verification["id"]}, headers=ADMIN_HEADERS)
    assert res.status_code == 409


def test_admin_creates_reobservation_when_auto_off(client, db, env):
    off = get_settings().model_copy(update={"admin_tokens": f"tester:{TEST_ADMIN_TOKEN}", "auto_reobservation": False})
    app.dependency_overrides[get_settings] = lambda: off
    _session, verification = _low_confidence_observation(client, env, db)
    assert _reobserve_tasks(db, "OBS-R-001") == []

    res = client.post("/tasks", json={"task_type": "REOBSERVE_BOOK", "robot_id": "R900",
                                      "verification_id": verification["id"]}, headers=ADMIN_HEADERS)
    assert res.status_code == 201
    assert res.json()["reobservation_of"] == "OBS-R-001"


def test_reobservation_only_for_pending_verification(client, db, env):
    _session, verification = _low_confidence_observation(client, env, db)
    db.get(RobotTask, _reobserve_tasks(db, "OBS-R-001")[0].id).status = RobotTaskStatus.CANCELLED
    db.get(VerificationResult, verification["id"]).workflow_status = None
    db.commit()
    res = client.post("/tasks", json={"task_type": "REOBSERVE_BOOK", "robot_id": "R900",
                                      "verification_id": verification["id"]}, headers=ADMIN_HEADERS)
    assert res.status_code == 409
