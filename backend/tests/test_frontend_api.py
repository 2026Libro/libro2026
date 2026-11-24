"""10단계 프론트엔드 연동 (CORS, 대시보드 요약, 복본 목록, 실시간 이벤트) 테스트."""
import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.enums import DeviceType
from app.core.realtime import hub
from app.models import Robot
from app.services.device import issue_api_key
from tests.conftest import ADMIN_HEADERS, TEST_ADMIN_TOKEN

NOW_ISO = "2026-10-29T12:00:00+09:00"


@pytest.fixture
def env(client, db):
    """T-01-01: BOOK-900101(정상 관측), T-01-02: BOOK-900102(대출 중, 미관측), T-02-01: BOOK-900201(T-01-03에서 관측)."""
    client.post("/robots", json={"id": "R900"}, headers=ADMIN_HEADERS)
    for shelf_id in ("T-01", "T-02"):
        client.post("/shelves", json={"id": shelf_id, "slot_count": 3}, headers=ADMIN_HEADERS)
    book = client.post("/books", json={"title": "프론트 테스트 도서"}, headers=ADMIN_HEADERS).json()
    for copy_id, slot, loan in (("BOOK-900101", "T-01-01", "AVAILABLE"), ("BOOK-900102", "T-01-02", "CHECKED_OUT"),
                                ("BOOK-900201", "T-02-01", "AVAILABLE")):
        client.post(f"/books/{book['id']}/copies", json={"id": copy_id, "slot_id": slot, "loan_status": loan},
                    headers=ADMIN_HEADERS)
    session = client.post("/scan-sessions", json={"robot_id": "R900", "shelf_id": "T-01"},
                          headers=ADMIN_HEADERS).json()["id"]
    return {"book_id": book["id"], "session_id": session, "ai": {"X-API-Key": issue_api_key(db, DeviceType.AI, "ai-t")}}


def observe(client, env, copy_id, slot):
    res = client.post("/observations", headers=env["ai"], json={
        "observation_id": f"OBS-FE-{uuid.uuid4().hex[:8]}", "scan_session_id": env["session_id"], "robot_id": "R900",
        "shelf_id": "T-01", "slot_id": slot, "detected_text": "프론트 테스트 도서", "book_copy_id": copy_id,
        "confidence": {"detection": 0.95, "ocr": 0.9, "identification": 0.9}, "candidates": [],
        "reobservation_of": None, "image_ref": None, "captured_at": NOW_ISO})
    assert res.status_code == 201


# ---------- CORS ----------

def test_cors_allows_frontend_dev_origin(client):
    res = client.options("/books", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
                                            "Access-Control-Request-Headers": "X-Admin-Token"})
    assert res.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "x-admin-token" in res.headers["access-control-allow-headers"].lower()


def test_cors_rejects_unknown_origin(client):
    res = client.get("/health", headers={"Origin": "http://evil.example.com"})
    assert "access-control-allow-origin" not in res.headers


# ---------- 대시보드 요약 ----------

def test_dashboard_summary(client, env):
    observe(client, env, "BOOK-900101", "T-01-01")   # 정상
    observe(client, env, "BOOK-900201", "T-01-03")   # 오배가 의심
    s = client.get("/dashboard/summary").json()

    assert s["books"] == 1
    assert s["copies"] == {"total": 3, "normal": 1, "misplaced_suspected": 1, "misplaced_confirmed": 0,
                           "not_observed": 0, "undetermined": 0, "never_verified": 1}
    assert s["loans"] == {"available": 2, "checked_out": 1, "in_transit": 0, "unknown": 0}
    assert s["workflow"]["reobservation_pending"] == 1
    assert s["active_tasks"]["pending"] == 1          # 자동 생성된 재관측 작업
    assert [r["id"] for r in s["robots"]] == ["R900"]
    assert s["last_scan_at"] is not None


def test_dashboard_counts_open_alerts(client, env):
    observe(client, env, None, "T-01-02")             # 후보 없이 글자만 → 미등록 → 관리자 검토 알림
    s = client.get("/dashboard/summary").json()
    assert s["open_alerts"] == 1
    assert s["open_alerts_by_type"] == {"REVIEW_REQUIRED": 1}


# ---------- 복본 목록 ----------

@pytest.mark.parametrize("params, expected", [
    ({}, ["BOOK-900101", "BOOK-900102", "BOOK-900201"]),
    ({"loan_status": "CHECKED_OUT"}, ["BOOK-900102"]),
    ({"shelf_id": "T-02"}, ["BOOK-900201"]),
    ({"q": "900201"}, ["BOOK-900201"]),
    ({"q": "프론트"}, ["BOOK-900101", "BOOK-900102", "BOOK-900201"]),
    ({"physical_location_type": "BASKET"}, []),
])
def test_list_copies_filters(client, env, params, expected):
    page = client.get("/copies", params=params).json()
    assert [c["id"] for c in page["items"]] == expected
    assert page["total"] == len(expected)


# ---------- 실시간 이벤트 ----------

@pytest.fixture
def published(monkeypatch) -> list[dict]:
    """hub.publish로 나가는 이벤트를 가로채 기록한다."""
    events: list[dict] = []
    monkeypatch.setattr(hub, "publish", events.append)
    return events


def test_events_sent_after_commit(client, env, published):
    observe(client, env, "BOOK-900201", "T-01-03")
    types = [e["type"] for e in published]
    assert "observation.created" in types
    assert "verification.created" in types
    assert "task.created" in types                    # 재관측 작업 자동 생성
    assert "copy.updated" in types                    # 물리 위치 갱신
    v = next(e for e in published if e["type"] == "verification.created")["data"]
    assert (v["book_copy_id"], v["location_verdict"], v["workflow_status"]) == \
        ("BOOK-900201", "MISPLACED_SUSPECTED", "REOBSERVATION_PENDING")
    assert published[0]["at"].endswith("Z")


def test_no_event_for_rolled_back_change(client, env, published):
    res = client.post("/books", json={"isbn": "123", "title": "x"}, headers=ADMIN_HEADERS)  # 형식 오류 → 저장 안 됨
    assert res.status_code == 422
    client.post("/robots", json={"id": "R900"}, headers=ADMIN_HEADERS)                     # 중복 → 409
    assert published == []


def test_heartbeat_only_change_is_not_an_event(client, db, published):
    db.add(Robot(id="R901"))
    db.commit()
    published.clear()
    key = {"X-API-Key": issue_api_key(db, DeviceType.ROBOT, "R901")}
    client.post("/robots/R901/status", headers=key, json={"battery_percent": 80, "reported_at": NOW_ISO})
    assert [e["type"] for e in published] == ["robot.updated"]      # ONLINE + 배터리 변경
    published.clear()
    client.get("/robots/R901/tasks/next", headers=key)              # 마지막 통신 시각만 바뀜
    assert published == []


def test_websocket_requires_admin_token(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/events?token=wrong") as ws:
            ws.receive_json()
    assert exc.value.code == 1008


def test_websocket_receives_events(client, env):
    with client.websocket_connect(f"/ws/events?token={TEST_ADMIN_TOKEN}") as ws:
        assert ws.receive_json()["type"] == "connected"
        client.post("/tasks", json={"task_type": "MOVE_TO_SHELF", "robot_id": "R900", "shelf_id": "T-02"},
                    headers=ADMIN_HEADERS)
        event = ws.receive_json()
        assert event["type"] == "task.created"
        assert (event["data"]["task_type"], event["data"]["status"]) == ("MOVE_TO_SHELF", "PENDING")
    assert hub.subscriber_count == 0                  # 연결을 닫으면 구독 해제
