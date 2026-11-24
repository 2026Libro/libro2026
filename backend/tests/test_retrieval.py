"""8단계 오배가 도서 인출·바구니 보관·직원 회수 테스트."""
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


def use_settings(**changes):
    s = get_settings().model_copy(update={"admin_tokens": f"tester:{TEST_ADMIN_TOKEN}", **changes})
    app.dependency_overrides[get_settings] = lambda: s


@pytest.fixture
def env(client, db):
    """BOOK-900201(등록 T-02-01)이 T-01-03에서 두 번 관측되어 오배가 확정된 상태."""
    use_settings(auto_reobservation=False)  # 재관측 작업이 끼어들지 않게 (재관측 결과는 직접 보냄)
    client.post("/robots", json={"id": "R900"}, headers=ADMIN_HEADERS)
    for shelf_id in ("T-01", "T-02"):
        client.post("/shelves", json={"id": shelf_id, "slot_count": 3}, headers=ADMIN_HEADERS)
    book = client.post("/books", json={"title": "테스트 도서"}, headers=ADMIN_HEADERS).json()
    for copy_id, slot in (("BOOK-900201", "T-02-01"), ("BOOK-900202", "T-02-02")):
        client.post(f"/books/{book['id']}/copies", json={"id": copy_id, "slot_id": slot}, headers=ADMIN_HEADERS)
    session = client.post("/tasks", json={"task_type": "SCAN_SHELF", "robot_id": "R900", "shelf_id": "T-01"},
                          headers=ADMIN_HEADERS).json()
    e = {
        "session_id": session["scan_session_id"], "scan_task": session["id"],
        "robot": {"X-API-Key": issue_api_key(db, DeviceType.ROBOT, "R900")},
        "ai": {"X-API-Key": issue_api_key(db, DeviceType.AI, "ai-test")},
    }
    # 스캔 작업은 끝내 둔다 (로봇이 바쁜 상태가 아니도록)
    robot(client, e, f"/tasks/{e['scan_task']}/ack", accepted=True)
    robot(client, e, f"/tasks/{e['scan_task']}/result", status="COMPLETED")
    return e


def observe(client, env, copy_id="BOOK-900201", slot="T-01-03", reobservation_of=None, captured=NOW_ISO) -> dict:
    obs_id = f"OBS-RT-{next(_seq):04d}"
    res = client.post("/observations", headers=env["ai"], json={
        "observation_id": obs_id, "scan_session_id": env["session_id"], "robot_id": "R900", "shelf_id": "T-01",
        "slot_id": slot, "detected_text": "테스트 도서", "book_copy_id": copy_id,
        "confidence": {"detection": 0.95, "ocr": 0.9, "identification": 0.9}, "candidates": [],
        "reobservation_of": reobservation_of, "image_ref": None, "captured_at": captured})
    assert res.status_code == 201, res.text
    return client.get("/verifications", params={"observation_id": obs_id}).json()["items"][0]


def confirm_misplaced(client, env) -> dict:
    first = observe(client, env)
    return observe(client, env, reobservation_of=first["observation_id"])


def robot(client, env, path, **body):
    return client.post(path, headers=env["robot"],
                       json={"idempotency_key": str(uuid.uuid4()), "reported_at": NOW_ISO, **body})


def retrieval_tasks(db, copy_id="BOOK-900201") -> list[RobotTask]:
    return list(db.scalars(select(RobotTask).where(RobotTask.task_type == RobotTaskType.RETRIEVE_MISPLACED_BOOK,
                                                   RobotTask.book_copy_id == copy_id).order_by(RobotTask.created_at)))


def copy_state(client, copy_id="BOOK-900201") -> dict:
    return client.get(f"/copies/{copy_id}").json()


@pytest.fixture
def approved(client, db, env) -> str:
    confirm_misplaced(client, env)
    task_id = retrieval_tasks(db)[0].id
    assert client.post(f"/tasks/{task_id}/approve", headers=ADMIN_HEADERS).status_code == 200
    return task_id


# ---------- 인출 요청 생성 ----------

def test_confirmed_misplacement_creates_request_waiting_for_approval(client, db, env):
    v = confirm_misplaced(client, env)
    assert v["location_verdict"] == "MISPLACED_CONFIRMED"
    [task] = retrieval_tasks(db)
    assert (task.status, task.created_by, task.slot_id, task.verification_id) == ("PENDING_APPROVAL", "system", "T-01-03", v["id"])
    assert "승인 대기" in client.get(f"/verifications/{v['id']}").json()["note"]


def test_suspected_only_does_not_create_request(client, db, env):
    observe(client, env)
    assert retrieval_tasks(db) == []


def test_request_waiting_for_approval_is_never_delivered(client, db, env):
    confirm_misplaced(client, env)
    assert client.get("/robots/R900/tasks/next", headers=env["robot"]).status_code == 204


def test_auto_request_skipped_when_unsafe(client, db, env, loan_tracking):
    client.put("/copies/BOOK-900201/loan-status", json={"loan_status": "IN_TRANSIT"}, headers=ADMIN_HEADERS)
    v = confirm_misplaced(client, env)
    assert retrieval_tasks(db) == []
    assert "자동 생성 안 함" in client.get(f"/verifications/{v['id']}").json()["note"]


def test_admin_creates_request_when_auto_off(client, db, env):
    use_settings(auto_reobservation=False, auto_retrieval_request=False)
    v = confirm_misplaced(client, env)
    assert retrieval_tasks(db) == []
    res = client.post("/tasks", json={"task_type": "RETRIEVE_MISPLACED_BOOK", "robot_id": "R900",
                                      "verification_id": v["id"]}, headers=ADMIN_HEADERS)
    assert res.status_code == 201
    assert res.json()["status"] == "PENDING_APPROVAL"


def test_request_for_unconfirmed_verification_rejected(client, env):
    v = observe(client, env)  # 의심 단계
    res = client.post("/tasks", json={"task_type": "RETRIEVE_MISPLACED_BOOK", "robot_id": "R900",
                                      "verification_id": v["id"]}, headers=ADMIN_HEADERS)
    assert res.status_code == 409
    assert any("오배가 확정" in d["message"] for d in res.json()["error"]["details"])


def test_duplicate_request_rejected(client, db, env):
    v = confirm_misplaced(client, env)  # 자동 요청 1건 있음
    res = client.post("/tasks", json={"task_type": "RETRIEVE_MISPLACED_BOOK", "robot_id": "R900",
                                      "verification_id": v["id"]}, headers=ADMIN_HEADERS)
    assert res.status_code == 409


# ---------- 승인·거절 ----------

def test_approve(client, db, env, approved):
    task = client.get(f"/tasks/{approved}").json()
    assert (task["status"], task["approved_by"]) == ("PENDING", "tester")
    assert client.get(f"/verifications/{task['verification_id']}").json()["workflow_status"] == "APPROVED"
    action = db.scalar(select(AdminAction).where(AdminAction.task_id == approved))
    assert action.action_type == "APPROVE_RETRIEVAL"

    cmd = client.get("/robots/R900/tasks/next", headers=env["robot"]).json()
    assert (cmd["task_type"], cmd["book_copy_id"], cmd["slot_id"]) == ("RETRIEVE_MISPLACED_BOOK", "BOOK-900201", "T-01-03")


def test_approve_requires_admin_and_only_once(client, db, env, approved):
    assert client.post(f"/tasks/{approved}/approve").status_code == 401
    assert client.post(f"/tasks/{approved}/approve", headers=ADMIN_HEADERS).status_code == 409


def test_approve_only_retrieval_tasks(client, env):
    res = client.post(f"/tasks/{env['scan_task']}/approve", headers=ADMIN_HEADERS)
    assert res.status_code == 409


@pytest.mark.parametrize("change, expected_reason", [
    (("PUT", "/copies/BOOK-900201/loan-status", {"loan_status": "CHECKED_OUT"}), "대출 상태"),
    (("PUT", "/copies/BOOK-900201/registered-location", {"slot_id": "T-01-03", "reason": "여기로 변경"}), "인출 불필요"),
])
def test_approve_rechecks_conditions(client, db, env, change, expected_reason, loan_tracking):
    confirm_misplaced(client, env)
    task_id = retrieval_tasks(db)[0].id
    method, url, body = change
    client.request(method, url, json=body, headers=ADMIN_HEADERS)  # 요청 이후 상황 변화

    res = client.post(f"/tasks/{task_id}/approve", headers=ADMIN_HEADERS)
    assert res.status_code == 409
    assert any(expected_reason in d["message"] for d in res.json()["error"]["details"])
    assert client.get(f"/tasks/{task_id}").json()["status"] == "PENDING_APPROVAL"


def test_approve_rejected_if_book_seen_elsewhere_since(client, db, env):
    confirm_misplaced(client, env)
    task_id = retrieval_tasks(db)[0].id
    observe(client, env, slot="T-01-01", captured="2026-10-29T13:00:00+09:00")  # 그 사이 다른 칸에서 관측
    res = client.post(f"/tasks/{task_id}/approve", headers=ADMIN_HEADERS)
    assert res.status_code == 409


def test_reject(client, db, env):
    v = confirm_misplaced(client, env)
    task_id = retrieval_tasks(db)[0].id
    assert client.post(f"/tasks/{task_id}/reject", json={}, headers=ADMIN_HEADERS).status_code == 422  # 사유 필수

    res = client.post(f"/tasks/{task_id}/reject", json={"reason": "직접 옮기겠음"}, headers=ADMIN_HEADERS)
    assert res.json()["status"] == "CANCELLED"
    after = client.get(f"/verifications/{v['id']}").json()
    assert after["workflow_status"] == "CLOSED"
    assert "직접 옮기겠음" in after["note"]
    assert db.scalar(select(AdminAction.comment).where(AdminAction.action_type == "REJECT_RETRIEVAL")) == "직접 옮기겠음"


# ---------- 로봇 실행 → 물리 위치 ----------

def start(client, env, task_id):
    assert client.get("/robots/R900/tasks/next", headers=env["robot"]).json()["task_id"] == task_id
    robot(client, env, f"/tasks/{task_id}/ack", accepted=True)


def test_full_retrieval_and_collection(client, db, env, approved):
    start(client, env, approved)
    assert copy_state(client)["physical_slot_id"] == "T-01-03"           # 수락만으로는 위치 안 바뀜

    robot(client, env, f"/tasks/{approved}/result", status="IN_PROGRESS", event_type="GRIPPED")
    c = copy_state(client)
    assert (c["physical_location_type"], c["physical_robot_id"], c["physical_location_certainty"]) == \
        ("ROBOT_GRIPPER", "R900", "CONFIRMED")

    robot(client, env, f"/tasks/{approved}/result", status="IN_PROGRESS", event_type="STORED_IN_BASKET")
    assert copy_state(client)["physical_location_type"] == "BASKET"
    robot(client, env, f"/tasks/{approved}/result", status="COMPLETED")

    basket = client.get("/basket-items", params={"robot_id": "R900"}).json()
    assert [(b["book_copy_id"], b["task_id"]) for b in basket] == [("BOOK-900201", approved)]  # 중복 기록 없음
    assert client.get("/verifications", params={"book_copy_id": "BOOK-900201", "location_verdict":
                      "MISPLACED_CONFIRMED"}).json()["items"][0]["workflow_status"] == "APPROVED"

    res = client.post(f"/basket-items/{basket[0]['id']}/collect", headers=ADMIN_HEADERS)
    assert res.json()["collected_by"] == "tester"
    assert copy_state(client)["physical_location_type"] == "COLLECTED_BY_STAFF"
    assert client.get("/basket-items", params={"robot_id": "R900"}).json() == []
    assert len(client.get("/basket-items", params={"robot_id": "R900", "include_collected": True}).json()) == 1
    v = client.get(f"/tasks/{approved}").json()["verification_id"]
    assert client.get(f"/verifications/{v}").json()["workflow_status"] == "CLOSED"
    assert client.post(f"/basket-items/{basket[0]['id']}/collect", headers=ADMIN_HEADERS).status_code == 409


def test_completed_without_store_event_still_recorded_in_basket(client, env, approved):
    start(client, env, approved)
    robot(client, env, f"/tasks/{approved}/result", status="COMPLETED")
    assert copy_state(client)["physical_location_type"] == "BASKET"
    assert len(client.get("/basket-items", params={"robot_id": "R900"}).json()) == 1


def test_failure_keeps_last_confirmed_location(client, db, env, approved):
    start(client, env, approved)
    robot(client, env, f"/tasks/{approved}/result", status="IN_PROGRESS", event_type="GRIPPED")
    robot(client, env, f"/tasks/{approved}/result", status="FAILED", message="바구니에 넣지 못함")

    c = copy_state(client)
    assert (c["physical_location_type"], c["physical_robot_id"]) == ("ROBOT_GRIPPER", "R900")  # 마지막 확인 위치 유지
    v = client.get(f"/verifications/{client.get(f'/tasks/{approved}').json()['verification_id']}").json()
    assert v["workflow_status"] == "REVIEW_REQUIRED"
    assert "FAILED" in v["note"] and "ROBOT_GRIPPER" in v["note"]
    assert client.get("/robots/R900/tasks/next", headers=env["robot"]).status_code == 204  # 자동 재시도 없음
    assert client.get("/basket-items", params={"robot_id": "R900"}).json() == []


def test_failure_before_grip_keeps_shelf_location(client, env, approved):
    start(client, env, approved)
    robot(client, env, f"/tasks/{approved}/result", status="FAILED")
    c = copy_state(client)
    assert (c["physical_location_type"], c["physical_slot_id"]) == ("SHELF", "T-01-03")


def test_unknown_retrieval_not_resent_and_location_kept(client, db, env, approved):
    start(client, env, approved)
    robot(client, env, f"/tasks/{approved}/result", status="IN_PROGRESS", event_type="GRIPPED")
    db.get(Robot, "R900").last_seen_at = utcnow() - timedelta(seconds=120)
    db.commit()
    mark_unresponsive(db, timeout_seconds=60)

    assert client.get(f"/tasks/{approved}").json()["status"] == "UNKNOWN"
    assert copy_state(client)["physical_location_type"] == "ROBOT_GRIPPER"
    assert client.get("/robots/R900/tasks/next", headers=env["robot"]).status_code == 204
    # 결과 불명인 동안 같은 도서로 새 인출 요청도 만들 수 없다 (UNKNOWN도 활성 작업)
    v_id = client.get(f"/tasks/{approved}").json()["verification_id"]
    res = client.post("/tasks", json={"task_type": "RETRIEVE_MISPLACED_BOOK", "robot_id": "R900",
                                      "verification_id": v_id}, headers=ADMIN_HEADERS)
    assert res.status_code == 409


def test_cancelled_request_goes_to_review(client, db, env):
    v = confirm_misplaced(client, env)
    task_id = retrieval_tasks(db)[0].id
    client.post(f"/tasks/{task_id}/cancel", headers=ADMIN_HEADERS)
    assert client.get(f"/verifications/{v['id']}").json()["workflow_status"] == "REVIEW_REQUIRED"


# ---------- 회수 후 다시 꽂은 책 / 바구니에 있는 책의 관측 ----------

def test_reshelved_book_location_updated_by_next_scan(client, env, approved):
    start(client, env, approved)
    robot(client, env, f"/tasks/{approved}/result", status="COMPLETED")
    client.post(f"/basket-items/{client.get('/basket-items', params={'robot_id': 'R900'}).json()[0]['id']}/collect", headers=ADMIN_HEADERS)

    session = client.post("/scan-sessions", json={"robot_id": "R900", "shelf_id": "T-02"},
                          headers=ADMIN_HEADERS).json()["id"]
    res = client.post("/observations", headers=env["ai"], json={
        "observation_id": "OBS-RT-RESHELF", "scan_session_id": session, "robot_id": "R900", "shelf_id": "T-02",
        "slot_id": "T-02-01", "detected_text": "테스트 도서", "book_copy_id": "BOOK-900201",
        "confidence": {"detection": 0.95, "ocr": 0.9, "identification": 0.9}, "candidates": [],
        "reobservation_of": None, "image_ref": None, "captured_at": "2099-01-01T00:00:00+09:00"})
    assert res.status_code == 201
    c = copy_state(client)
    assert (c["physical_location_type"], c["physical_slot_id"], c["physical_location_certainty"]) == \
        ("SHELF", "T-02-01", "CONFIRMED")


def test_book_in_basket_seen_on_shelf_is_not_overwritten(client, env, approved):
    start(client, env, approved)
    robot(client, env, f"/tasks/{approved}/result", status="COMPLETED")
    v = observe(client, env, slot="T-01-01", captured="2099-01-01T00:00:00+09:00")
    assert copy_state(client)["physical_location_type"] == "BASKET"
    assert "갱신하지 않음" in v["note"]
