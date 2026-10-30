"""9단계 관리자 알림·처리 이력·통합 이력 테스트."""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.enums import DeviceType, RobotTaskType
from app.db.base import utcnow
from app.main import app
from app.models import AdminAction, Robot, RobotTask
from app.services.device import issue_api_key
from app.services.robot_task import mark_unresponsive
from tests.conftest import ADMIN_HEADERS, TEST_ADMIN_TOKEN

NOW_ISO = "2026-10-29T12:00:00+09:00"
_seq = iter(range(1, 10_000))


@pytest.fixture
def env(client, db):
    s = get_settings().model_copy(update={"admin_tokens": f"tester:{TEST_ADMIN_TOKEN}", "auto_reobservation": False})
    app.dependency_overrides[get_settings] = lambda: s
    client.post("/robots", json={"id": "R900"}, headers=ADMIN_HEADERS)
    for shelf_id in ("T-01", "T-02"):
        client.post("/shelves", json={"id": shelf_id, "slot_count": 3}, headers=ADMIN_HEADERS)
    book = client.post("/books", json={"title": "테스트 도서"}, headers=ADMIN_HEADERS).json()
    for copy_id, slot, loan in (("BOOK-900201", "T-02-01", "AVAILABLE"), ("BOOK-900101", "T-01-01", "AVAILABLE"),
                                ("BOOK-900102", "T-01-02", "CHECKED_OUT")):
        client.post(f"/books/{book['id']}/copies", json={"id": copy_id, "slot_id": slot, "loan_status": loan},
                    headers=ADMIN_HEADERS)
    scan = client.post("/tasks", json={"task_type": "SCAN_SHELF", "robot_id": "R900", "shelf_id": "T-01"},
                       headers=ADMIN_HEADERS).json()
    return {
        "session_id": scan["scan_session_id"], "scan_task": scan["id"],
        "robot": {"X-API-Key": issue_api_key(db, DeviceType.ROBOT, "R900")},
        "ai": {"X-API-Key": issue_api_key(db, DeviceType.AI, "ai-test")},
    }


def observe(client, env, copy_id=None, slot="T-01-03", reobservation_of=None, ocr=0.9, text="테스트 도서") -> dict:
    obs_id = f"OBS-AL-{next(_seq):04d}"
    assert client.post("/observations", headers=env["ai"], json={
        "observation_id": obs_id, "scan_session_id": env["session_id"], "robot_id": "R900", "shelf_id": "T-01",
        "slot_id": slot, "detected_text": text, "book_copy_id": copy_id,
        "confidence": {"detection": 0.95, "ocr": ocr, "identification": 0.9}, "candidates": [],
        "reobservation_of": reobservation_of, "image_ref": None, "captured_at": NOW_ISO}).status_code == 201
    return client.get("/verifications", params={"observation_id": obs_id}).json()["items"][0]


def robot(client, env, path, **body):
    return client.post(path, headers=env["robot"],
                       json={"idempotency_key": str(uuid.uuid4()), "reported_at": NOW_ISO, **body})


def alerts(client, **params) -> list[dict]:
    return client.get("/alerts", params={"limit": 100, **params}).json()["items"]


def alerts_for_robot(client) -> list[dict]:
    return alerts(client, robot_id="R900")


def silence_robot(db):
    db.get(Robot, "R900").last_seen_at = utcnow() - timedelta(seconds=120)
    db.commit()
    mark_unresponsive(db, timeout_seconds=60)


# ---------- 알림 생성 ----------

def test_reobservation_pending_creates_no_alert(client, env):
    v = observe(client, env, text=None, ocr=0.1)              # 재관측으로 해결 시도 중 → 아직 알림 없음
    assert v["workflow_status"] == "REOBSERVATION_PENDING"
    assert [a for a in alerts(client) if a["verification_id"] == v["id"]] == []


def test_repeated_recognition_failure_alert(client, env):
    v = observe(client, env, text=None, ocr=0.1)
    for _ in range(2):
        v = observe(client, env, text=None, ocr=0.1, reobservation_of=v["observation_id"])
    assert v["workflow_status"] == "REVIEW_REQUIRED"
    [alert] = [a for a in alerts(client, alert_type="RECOGNITION_FAILED") if a["verification_id"] == v["id"]]
    assert alert["status"] == "OPEN"
    assert "재관측 2회" in alert["message"]


def test_confirmed_misplacement_alerts_and_auto_resolve_on_approval(client, db, env):
    first = observe(client, env, copy_id="BOOK-900201")
    observe(client, env, copy_id="BOOK-900201", reobservation_of=first["observation_id"])
    copy_alerts = {a["alert_type"]: a for a in alerts(client, book_copy_id="BOOK-900201")}
    assert set(copy_alerts) == {"MISPLACED_CONFIRMED", "RETRIEVAL_APPROVAL_REQUIRED"}
    assert "등록 T-02-01 / 관측 T-01-03" in copy_alerts["MISPLACED_CONFIRMED"]["message"]

    task_id = copy_alerts["RETRIEVAL_APPROVAL_REQUIRED"]["task_id"]
    client.post(f"/tasks/{task_id}/approve", headers=ADMIN_HEADERS)
    after = alerts(client, book_copy_id="BOOK-900201")
    assert {(a["alert_type"], a["status"], a["acknowledged_by"]) for a in after} == {
        ("MISPLACED_CONFIRMED", "RESOLVED", "system"), ("RETRIEVAL_APPROVAL_REQUIRED", "RESOLVED", "system")}


def test_not_observed_alert_only_for_available_copy(client, env):
    robot(client, env, f"/tasks/{env['scan_task']}/ack", accepted=True)
    robot(client, env, f"/tasks/{env['scan_task']}/result", status="COMPLETED")
    client.post(f"/scan-sessions/{env['session_id']}/analysis-complete", headers=env["ai"])
    assert [a["alert_type"] for a in alerts(client, book_copy_id="BOOK-900101")] == ["NOT_OBSERVED"]
    assert alerts(client, book_copy_id="BOOK-900102") == []      # 대출 중 → 알림 없음


@pytest.mark.parametrize("how", ["robot_failed", "robot_rejected"])
def test_task_failed_alert(client, env, how):
    task_id = env["scan_task"]
    if how == "robot_rejected":
        robot(client, env, f"/tasks/{task_id}/ack", accepted=False, reason="배터리 부족")
    else:
        robot(client, env, f"/tasks/{task_id}/ack", accepted=True)
        robot(client, env, f"/tasks/{task_id}/result", status="FAILED", message="경로 막힘")
    [alert] = [a for a in alerts_for_robot(client) if a["alert_type"] == "TASK_FAILED"]
    assert alert["task_id"] == task_id


def test_unknown_task_alert_resolved_when_result_arrives(client, db, env):
    robot(client, env, f"/tasks/{env['scan_task']}/ack", accepted=True)
    silence_robot(db)
    [unknown] = [a for a in alerts_for_robot(client) if a["alert_type"] == "TASK_RESULT_UNKNOWN"]
    assert unknown["status"] == "OPEN"

    robot(client, env, f"/tasks/{env['scan_task']}/result", status="COMPLETED")
    assert client.get("/alerts", params={"alert_type": "TASK_RESULT_UNKNOWN", "robot_id": "R900"}).json()[
        "items"][0]["status"] == "RESOLVED"


def test_communication_error_alert_once_and_resolved_on_reconnect(client, db, env):
    client.post("/robots/R900/status", headers=env["robot"], json={"reported_at": NOW_ISO})  # ONLINE
    silence_robot(db)
    silence_robot(db)                                             # 계속 끊겨 있어도 알림은 1건
    comm = [a for a in alerts_for_robot(client) if a["alert_type"] == "COMMUNICATION_ERROR"]
    assert len(comm) == 1 and comm[0]["status"] == "OPEN"

    client.post("/robots/R900/status", headers=env["robot"], json={"reported_at": NOW_ISO})  # 재연결
    comm = [a for a in alerts_for_robot(client) if a["alert_type"] == "COMMUNICATION_ERROR"]
    assert comm[0]["status"] == "RESOLVED"


def test_unregistered_book_alert(client, env):
    v = observe(client, env, text="DB에 없는 책")
    [alert] = [a for a in alerts(client, alert_type="REVIEW_REQUIRED") if a["verification_id"] == v["id"]]
    assert "확인 필요" in alert["message"]


# ---------- 확인·해결 ----------

def _recognition_alert(client, env) -> dict:
    v = observe(client, env, text="DB에 없는 책")
    return next(a for a in alerts(client, alert_type="REVIEW_REQUIRED") if a["verification_id"] == v["id"])


def test_confirm_alert(client, db, env):
    alert = _recognition_alert(client, env)
    assert client.post(f"/alerts/{alert['id']}/confirm").status_code == 401
    res = client.post(f"/alerts/{alert['id']}/confirm", json={"comment": "확인 중"}, headers=ADMIN_HEADERS)
    assert (res.json()["status"], res.json()["acknowledged_by"]) == ("ACKNOWLEDGED", "tester")
    assert client.post(f"/alerts/{alert['id']}/confirm", headers=ADMIN_HEADERS).status_code == 409
    action = db.scalar(select(AdminAction).where(AdminAction.alert_id == alert["id"]))
    assert (action.action_type, action.comment) == ("CONFIRM_ALERT", "확인 중")


def test_resolve_alert_closes_verification(client, env):
    alert = _recognition_alert(client, env)
    res = client.post(f"/alerts/{alert['id']}/resolve", json={"comment": "신규 도서로 등록함"}, headers=ADMIN_HEADERS)
    assert res.json()["status"] == "RESOLVED"
    v = client.get(f"/verifications/{alert['verification_id']}").json()
    assert v["workflow_status"] == "CLOSED"
    assert "관리자 처리 완료" in v["note"]
    assert client.post(f"/alerts/{alert['id']}/resolve", headers=ADMIN_HEADERS).status_code == 409


def test_open_only_filter(client, env):
    alert = _recognition_alert(client, env)
    client.post(f"/alerts/{alert['id']}/resolve", headers=ADMIN_HEADERS)
    open_ids = [a["id"] for a in alerts(client, open_only=True)]
    assert alert["id"] not in open_ids


def test_admin_can_request_reobservation_from_review(client, db, env):
    alert = _recognition_alert(client, env)
    res = client.post("/tasks", json={"task_type": "REOBSERVE_BOOK", "robot_id": "R900",
                                      "verification_id": alert["verification_id"]}, headers=ADMIN_HEADERS)
    assert res.status_code == 201
    v = client.get(f"/verifications/{alert['verification_id']}").json()
    assert v["workflow_status"] == "REOBSERVATION_PENDING"
    same = next(a for a in alerts(client, alert_type="REVIEW_REQUIRED") if a["id"] == alert["id"])
    assert (same["status"], same["acknowledged_by"]) == ("RESOLVED", "system")  # 다시 찍어 보기로 했으므로 자동 해결
    assert db.scalar(select(AdminAction.action_type).where(AdminAction.task_id == res.json()["id"])) == \
        "REQUEST_REOBSERVATION"


# ---------- 관리자 처리 이력·통합 이력 ----------

def test_admin_actions_require_admin_and_filter(client, env):
    alert = _recognition_alert(client, env)
    client.post(f"/alerts/{alert['id']}/confirm", headers=ADMIN_HEADERS)
    assert client.get("/admin-actions").status_code == 401
    page = client.get("/admin-actions", params={"admin_id": "tester", "action_type": "CONFIRM_ALERT"},
                      headers=ADMIN_HEADERS).json()
    assert page["total"] >= 1
    assert all(a["action_type"] == "CONFIRM_ALERT" for a in page["items"])


def test_history_of_a_book(client, db, env):
    first = observe(client, env, copy_id="BOOK-900201")
    observe(client, env, copy_id="BOOK-900201", reobservation_of=first["observation_id"])
    task_id = db.scalar(select(RobotTask.id).where(RobotTask.task_type == RobotTaskType.RETRIEVE_MISPLACED_BOOK,
                                                   RobotTask.book_copy_id == "BOOK-900201"))
    client.post(f"/tasks/{task_id}/approve", headers=ADMIN_HEADERS)

    history = client.get("/history", params={"book_copy_id": "BOOK-900201"}).json()
    kinds = {h["kind"] for h in history}
    assert kinds == {"LOCATION_CHANGE", "OBSERVATION", "VERIFICATION", "TASK_EVENT", "ADMIN_ACTION", "ALERT"}
    assert all(h["book_copy_id"] in ("BOOK-900201", None) for h in history)
    assert [h["at"] for h in history] == sorted((h["at"] for h in history), reverse=True)


def test_history_of_a_task(client, env):
    robot(client, env, f"/tasks/{env['scan_task']}/ack", accepted=True)
    history = client.get("/history", params={"task_id": env["scan_task"]}).json()
    assert {h["kind"] for h in history} == {"TASK_EVENT"}
    assert len(history) == 2  # CREATED, ACCEPTED
