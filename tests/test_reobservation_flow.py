"""재관측 대기 상태가 멈추지 않는지 테스트 (작업 실패·취소, AI 결과 누락, 늦게 도착한 결과)."""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.enums import DeviceType, RobotTaskType
from app.db.base import utcnow
from app.models import Robot, RobotTask
from app.services.device import issue_api_key
from app.services.robot_task import expire_missing_reobservation_results, mark_unresponsive
from tests.conftest import ADMIN_HEADERS

NOW_ISO = "2026-10-29T12:00:00+09:00"
WAIT = 300


@pytest.fixture
def env(client, db):
    client.post("/robots", json={"id": "R900"}, headers=ADMIN_HEADERS)
    for shelf_id in ("T-01", "T-02"):
        client.post("/shelves", json={"id": shelf_id, "slot_count": 3}, headers=ADMIN_HEADERS)
    book = client.post("/books", json={"title": "테스트 도서"}, headers=ADMIN_HEADERS).json()
    client.post(f"/books/{book['id']}/copies", json={"id": "BOOK-900201", "slot_id": "T-02-01"}, headers=ADMIN_HEADERS)
    scan = client.post("/tasks", json={"task_type": "SCAN_SHELF", "robot_id": "R900", "shelf_id": "T-01"},
                       headers=ADMIN_HEADERS).json()
    e = {
        "session_id": scan["scan_session_id"],
        "robot": {"X-API-Key": issue_api_key(db, DeviceType.ROBOT, "R900")},
        "ai": {"X-API-Key": issue_api_key(db, DeviceType.AI, "ai-test")},
    }
    # 첫 관측: 인식 실패 → 재관측 대기 + 재관측 작업 자동 생성
    send_observation(client, e, "OBS-F-001", text=None, ocr=0.1)
    e["task_id"] = db.scalar(select(RobotTask.id).where(RobotTask.task_type == RobotTaskType.REOBSERVE_BOOK,
                                                        RobotTask.reobservation_of == "OBS-F-001"))
    return e


def send_observation(client, env, obs_id, reobservation_of=None, text="테스트 도서", ocr=0.9, copy_id=None):
    res = client.post("/observations", headers=env["ai"], json={
        "observation_id": obs_id, "scan_session_id": env["session_id"], "robot_id": "R900", "shelf_id": "T-01",
        "slot_id": "T-01-02", "detected_text": text, "book_copy_id": copy_id,
        "confidence": {"detection": 0.9, "ocr": ocr, "identification": 0.9}, "candidates": [],
        "reobservation_of": reobservation_of, "image_ref": None, "captured_at": NOW_ISO})
    assert res.status_code == 201, res.text


def verification_of(client, obs_id) -> dict:
    return client.get("/verifications", params={"observation_id": obs_id}).json()["items"][0]


def robot_call(client, env, path, **body):
    return client.post(path, headers=env["robot"],
                       json={"idempotency_key": str(uuid.uuid4()), "reported_at": NOW_ISO, **body})


def complete_task(client, env, db, finished_seconds_ago=0):
    robot_call(client, env, f"/tasks/{env['task_id']}/ack", accepted=True)
    robot_call(client, env, f"/tasks/{env['task_id']}/result", status="COMPLETED")
    if finished_seconds_ago:
        db.get(RobotTask, env["task_id"]).finished_at = utcnow() - timedelta(seconds=finished_seconds_ago)
        db.commit()


def test_starts_in_reobservation_pending(client, env):
    assert env["task_id"] is not None
    assert verification_of(client, "OBS-F-001")["workflow_status"] == "REOBSERVATION_PENDING"


# ---------- 작업이 실패·거절·취소되면 즉시 관리자 검토 ----------

def test_failed_reobservation_task_goes_to_review(client, env):
    robot_call(client, env, f"/tasks/{env['task_id']}/ack", accepted=True)
    robot_call(client, env, f"/tasks/{env['task_id']}/result", status="FAILED", message="카메라 오류")
    v = verification_of(client, "OBS-F-001")
    assert v["workflow_status"] == "REVIEW_REQUIRED"
    assert "FAILED" in v["note"]


def test_rejected_reobservation_task_goes_to_review(client, env):
    robot_call(client, env, f"/tasks/{env['task_id']}/ack", accepted=False, reason="배터리 부족")
    assert verification_of(client, "OBS-F-001")["workflow_status"] == "REVIEW_REQUIRED"


def test_cancelled_reobservation_task_goes_to_review(client, env):
    client.post(f"/tasks/{env['task_id']}/cancel", headers=ADMIN_HEADERS)
    assert verification_of(client, "OBS-F-001")["workflow_status"] == "REVIEW_REQUIRED"


def test_unknown_reobservation_task_keeps_waiting(client, db, env):
    robot_call(client, env, f"/tasks/{env['task_id']}/ack", accepted=True)
    db.get(Robot, "R900").last_seen_at = utcnow() - timedelta(seconds=120)
    db.commit()
    mark_unresponsive(db, timeout_seconds=60)
    # 로봇이 실제로 찍었는지 모르므로 판단을 미룬다 (로봇이 돌아와 결과를 보고하면 처리)
    assert verification_of(client, "OBS-F-001")["workflow_status"] == "REOBSERVATION_PENDING"


# ---------- 촬영은 완료했는데 AI 결과가 안 옴 ----------

def test_missing_ai_result_goes_to_review_after_wait(client, db, env):
    complete_task(client, env, db, finished_seconds_ago=WAIT + 1)
    assert expire_missing_reobservation_results(db, WAIT) == [env["task_id"]]

    v = verification_of(client, "OBS-F-001")
    assert v["workflow_status"] == "REVIEW_REQUIRED"
    assert "AI 결과 없음" in v["note"]
    events = client.get(f"/tasks/{env['task_id']}").json()["events"]
    assert events[-1]["event_type"] == "REOBSERVATION_RESULT_MISSING"
    assert expire_missing_reobservation_results(db, WAIT) == []  # 한 번만 처리


def test_not_expired_before_wait(client, db, env):
    complete_task(client, env, db, finished_seconds_ago=WAIT - 10)
    assert expire_missing_reobservation_results(db, WAIT) == []
    assert verification_of(client, "OBS-F-001")["workflow_status"] == "REOBSERVATION_PENDING"


def test_not_expired_when_ai_result_arrived(client, db, env):
    complete_task(client, env, db, finished_seconds_ago=WAIT + 1)
    send_observation(client, env, "OBS-F-002", reobservation_of="OBS-F-001", copy_id="BOOK-900201")
    assert expire_missing_reobservation_results(db, WAIT) == []
    assert verification_of(client, "OBS-F-001")["workflow_status"] == "CLOSED"


def test_late_ai_result_after_review_is_still_processed(client, db, env):
    complete_task(client, env, db, finished_seconds_ago=WAIT + 1)
    expire_missing_reobservation_results(db, WAIT)

    send_observation(client, env, "OBS-F-002", reobservation_of="OBS-F-001", copy_id="BOOK-900201")
    late = verification_of(client, "OBS-F-002")
    assert late["location_verdict"] == "MISPLACED_SUSPECTED"   # 늦게 왔어도 버리지 않고 판정
    assert late["reobservation_count"] == 1
    assert verification_of(client, "OBS-F-001")["workflow_status"] == "CLOSED"
