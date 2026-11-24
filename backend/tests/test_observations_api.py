"""5단계 스캔 세션·AI 관측 API 테스트 (실제 MySQL 사용, 테스트 후 롤백)."""
import pytest
from sqlalchemy import func, select

from app.core.enums import DeviceType
from app.core.errors import AppError
from app.db.base import utcnow
from app.models import DeviceCredential, Observation, Robot
from app.services.device import issue_api_key
from tests.conftest import ADMIN_HEADERS


@pytest.fixture
def env(client, db):
    """서가 T-01(3칸), 로봇 R900·R901, 같은 제목 복본 2권, 장치 키, 스캔 세션 1개."""
    db.add_all([Robot(id="R900"), Robot(id="R901")])
    db.commit()
    client.post("/shelves", json={"id": "T-01", "slot_count": 3}, headers=ADMIN_HEADERS)
    book = client.post("/books", json={"title": "테스트 파이썬"}, headers=ADMIN_HEADERS).json()
    for copy_id in ("BOOK-900125", "BOOK-900126"):
        client.post(f"/books/{book['id']}/copies", json={"id": copy_id, "slot_id": "T-01-02"}, headers=ADMIN_HEADERS)

    session = client.post("/scan-sessions", json={"robot_id": "R900", "shelf_id": "T-01"}, headers=ADMIN_HEADERS)
    assert session.status_code == 201
    return {
        "session_id": session.json()["id"],
        "ai": {"X-API-Key": issue_api_key(db, DeviceType.AI, "ai-test")},
        "robot": {"X-API-Key": issue_api_key(db, DeviceType.ROBOT, "R900")},
        "other_robot": {"X-API-Key": issue_api_key(db, DeviceType.ROBOT, "R901")},
    }


def observation(env, **changes) -> dict:
    data = {
        "observation_id": "OBS-T-001",
        "scan_session_id": env["session_id"],
        "robot_id": "R900",
        "shelf_id": "T-01",
        "slot_id": "T-01-02",
        "detected_text": "테스트 파이썬",
        "book_copy_id": None,
        "confidence": {"detection": 0.98, "ocr": 0.91, "identification": 0.75},
        "candidates": [
            {"book_copy_id": "BOOK-900126", "score": 0.72},
            {"book_copy_id": "BOOK-900125", "score": 0.75},
        ],
        "reobservation_of": None,
        "image_ref": "images/OBS-T-001.jpg",
        "captured_at": "2026-10-29T11:30:00+09:00",
    }
    data.update(changes)
    return data


# ---------- 장치 API 키 ----------

@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "lrk_wrong"}])
def test_observation_requires_api_key(client, env, headers):
    res = client.post("/observations", json=observation(env), headers=headers)
    assert res.status_code == 401


def test_robot_key_cannot_send_observation(client, env):
    res = client.post("/observations", json=observation(env), headers=env["robot"])
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"


def test_revoked_key_rejected(client, db, env):
    cred = db.scalar(select(DeviceCredential).where(DeviceCredential.device_id == "ai-test"))
    cred.revoked_at = utcnow()
    db.commit()
    assert client.post("/observations", json=observation(env), headers=env["ai"]).status_code == 401


def test_api_key_is_stored_only_as_hash(db, env):
    raw_key = env["ai"]["X-API-Key"]
    hashes = db.scalars(select(DeviceCredential.key_hash)).all()
    assert raw_key not in hashes


def test_robot_api_key_requires_registered_robot(client, db):
    with pytest.raises(AppError):
        issue_api_key(db, DeviceType.ROBOT, "R999")


# ---------- 관측 수신 ----------

def test_receive_observation(client, env):
    res = client.post("/observations", json=observation(env), headers=env["ai"])
    assert res.status_code == 201
    body = res.json()
    assert body["reported_book_copy_id"] is None  # 복본을 임의로 확정하지 않음
    assert [(c["book_copy_id"], c["rank"]) for c in body["candidates"]] == [("BOOK-900125", 1), ("BOOK-900126", 2)]
    assert body["captured_at"] == "2026-10-29T02:30:00Z"  # +09:00 → UTC 변환


def test_identical_resend_is_not_saved_twice(client, db, env):
    assert client.post("/observations", json=observation(env), headers=env["ai"]).status_code == 201
    res = client.post("/observations", json=observation(env), headers=env["ai"])
    assert res.status_code == 200
    assert db.scalar(select(func.count()).where(Observation.id == "OBS-T-001")) == 1


def test_same_id_different_content_rejected(client, env):
    client.post("/observations", json=observation(env), headers=env["ai"])
    res = client.post("/observations", json=observation(env, slot_id="T-01-03"), headers=env["ai"])
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "DUPLICATE_REQUEST"


@pytest.mark.parametrize("changes, status", [
    ({"candidates": [{"book_copy_id": "BOOK-999999", "score": 0.5}]}, 422),  # 등록 안 된 복본
    ({"book_copy_id": "BOOK-999999"}, 422),
    ({"robot_id": "R901"}, 422),                                             # 세션의 로봇과 다름
    ({"scan_session_id": "SCAN-NOPE"}, 404),                                 # 없는 세션
    ({"reobservation_of": "OBS-NOPE"}, 404),                                 # 없는 원 관측
    ({"confidence": {"detection": 1.5, "ocr": 0.9, "identification": 0.7}}, 422),
])
def test_invalid_observation_rejected(client, env, changes, status):
    res = client.post("/observations", json=observation(env, **changes), headers=env["ai"])
    assert res.status_code == status


def test_slot_not_found(client, env):
    res = client.post("/observations", json=observation(env, slot_id="T-01-09"), headers=env["ai"])
    assert res.status_code == 404


def test_reobservation_links_to_original(client, env):
    client.post("/observations", json=observation(env), headers=env["ai"])
    res = client.post("/observations", json=observation(env, observation_id="OBS-T-002", reobservation_of="OBS-T-001"),
                      headers=env["ai"])
    assert res.status_code == 201
    assert res.json()["reobservation_of"] == "OBS-T-001"


def test_failed_recognition_is_stored(client, env):
    """OCR 실패여도 관측 기록은 남긴다 (6단계에서 FAILED/재관측 판단)."""
    res = client.post("/observations", json=observation(
        env, detected_text=None, candidates=[], confidence={"detection": 0.9, "ocr": 0.1, "identification": 0.0},
    ), headers=env["ai"])
    assert res.status_code == 201


def test_late_observation_after_session_finished_accepted(client, env):
    client.post(f"/scan-sessions/{env['session_id']}/finish", json={"status": "COMPLETED"}, headers=env["robot"])
    res = client.post("/observations", json=observation(env), headers=env["ai"])
    assert res.status_code == 201


def test_list_observations_by_session(client, env):
    client.post("/observations", json=observation(env), headers=env["ai"])
    client.post("/observations", json=observation(env, observation_id="OBS-T-002", slot_id="T-01-03"), headers=env["ai"])
    page = client.get("/observations", params={"scan_session_id": env["session_id"]}).json()
    assert page["total"] == 2
    assert client.get("/observations", params={"slot_id": "T-01-03"}).json()["total"] == 1
    assert client.get("/observations/OBS-T-001").json()["slot_id"] == "T-01-02"


# ---------- 스캔 세션 ----------

def test_create_session_requires_admin(client, env):
    res = client.post("/scan-sessions", json={"robot_id": "R900", "shelf_id": "T-01"})
    assert res.status_code == 401


@pytest.mark.parametrize("payload", [{"robot_id": "R999", "shelf_id": "T-01"}, {"robot_id": "R900", "shelf_id": "Z-99"}])
def test_create_session_unknown_robot_or_shelf(client, env, payload):
    assert client.post("/scan-sessions", json=payload, headers=ADMIN_HEADERS).status_code == 404


def test_finish_session(client, env):
    client.post("/observations", json=observation(env), headers=env["ai"])
    res = client.post(f"/scan-sessions/{env['session_id']}/finish", json={"status": "COMPLETED"}, headers=env["robot"])
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "COMPLETED"
    assert body["finished_at"] is not None
    assert body["observation_count"] == 1


def test_only_assigned_robot_can_finish_session(client, env):
    res = client.post(f"/scan-sessions/{env['session_id']}/finish", json={"status": "COMPLETED"},
                      headers=env["other_robot"])
    assert res.status_code == 403


def test_finish_session_twice_rejected(client, env):
    url = f"/scan-sessions/{env['session_id']}/finish"
    client.post(url, json={"status": "INCOMPLETE"}, headers=env["robot"])
    res = client.post(url, json={"status": "COMPLETED"}, headers=env["robot"])
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "INVALID_STATE"


def test_finish_status_must_be_terminal(client, env):
    res = client.post(f"/scan-sessions/{env['session_id']}/finish", json={"status": "IN_PROGRESS"},
                      headers=env["robot"])
    assert res.status_code == 422
