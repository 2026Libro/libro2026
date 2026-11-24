"""칸별 청구기호 범위표: 저장·검사, 칸 찾기, 등록 위치 적용, 오배치 판정 연결."""
import pytest

from app.core.call_number import sort_key, upper_key
from app.core.enums import DeviceType
from app.models import Robot
from app.services.device import issue_api_key
from tests.conftest import ADMIN_HEADERS

ROOM = "고양캠퍼스 대출실"


# ---------- 1. 범위 끝 규칙 ----------

@pytest.mark.parametrize("end, inside, outside", [
    ("811.16 김55ㄷ", ["811.16 김55ㄷ", "811.16 김55ㄷ c.3", "811.16 김55ㄷ v.2"], ["811.16 김56ㄱ"]),
    ("823.914 T455r이 v.1", ["823.914 T455r이 v.1 c.2"], ["823.914 T455r이 v.2"]),
    ("811.16 김55ㄷ c.2", ["811.16 김55ㄷ c.2"], ["811.16 김55ㄷ c.3"]),
    ("811.16", ["811.16 하99ㅎ"], ["811.17 가1ㄱ"]),
])
def test_upper_key(end, inside, outside):
    assert all(sort_key(c) <= upper_key(end) for c in inside)
    assert all(sort_key(c) > upper_key(end) for c in outside)


# ---------- 준비 ----------

COPIES = {  # 복본 ID: 청구기호
    "EM9700001": "811.16 가12ㄱ",
    "EM9700002": "811.16 김54ㄱ",
    "EM9700003": "811.16 김54ㄱ c.2",
    "EM9700004": "811.16 김55ㄷ",
    "EM9700005": "811.16 박1ㄱ",
    "EM9700006": "813.6 이12ㄱ",          # 어느 범위에도 없음
}


@pytest.fixture
def lib(client, db):
    client.post("/shelves", json={"id": "R-01", "slot_count": 3}, headers=ADMIN_HEADERS)
    for copy_id, call in COPIES.items():
        book = client.post("/books", json={"title": f"책 {call}"}, headers=ADMIN_HEADERS).json()
        res = client.post(f"/books/{book['id']}/copies", headers=ADMIN_HEADERS,
                          json={"id": copy_id, "call_number": call, "room": ROOM})
        assert res.status_code == 201, res.text
    return client


def put(client, slot_id, start, end, room="대출실", expect=200):
    res = client.put(f"/call-ranges/{slot_id}", headers=ADMIN_HEADERS,
                     json={"room": room, "start_call_number": start, "end_call_number": end})
    assert res.status_code == expect, res.text
    return res.json()


def apply(client, dry_run=False):
    res = client.post("/call-ranges/apply", params={"dry_run": dry_run}, headers=ADMIN_HEADERS)
    assert res.status_code == 200, res.text
    return res.json()


def slot_of(client, copy_id):
    return client.get(f"/copies/{copy_id}").json()["registered_slot_id"]


# ---------- 2. 저장·검사 ----------

def test_save_and_list(lib):
    saved = put(lib, "R-01-02", "811.16 김55ㄱ", "811.16 하99ㅎ")
    put(lib, "R-01-01", "811.16 가1ㄱ", "811.16 김54ㄱ")
    assert (saved["room"], saved["updated_by"]) == (ROOM, "tester")
    assert [r["slot_id"] for r in lib.get("/call-ranges", params={"room": "대출실"}).json()] == ["R-01-01", "R-01-02"]
    assert [r["slot_id"] for r in lib.get("/call-ranges", params={"shelf_id": "R-02"}).json()] == []


@pytest.mark.parametrize("start, end, expect", [
    ("811.16 김54ㄱ", "811.16 하99ㅎ", 422),     # R-01-01 끝(김54ㄱ의 모든 복본)과 겹침
    ("811.16 박1ㄱ", "811.16 가1ㄱ", 422),       # 시작이 끝보다 뒤
    ("811.16 김54ㄱ c.3", "811.16 하99ㅎ", 422),  # c.3도 '김54ㄱ 전체'에 포함되어 겹침
    ("811.16 김54ㄴ", "811.16 하99ㅎ", 200),
])
def test_overlap_and_order_checks(lib, start, end, expect):
    put(lib, "R-01-01", "811.16 가1ㄱ", "811.16 김54ㄱ")
    put(lib, "R-01-02", start, end, expect=expect)


def test_same_range_in_other_room_is_fine(lib):
    put(lib, "R-01-01", "811.16 가1ㄱ", "811.16 김54ㄱ")
    put(lib, "R-01-02", "811.16 가1ㄱ", "811.16 김54ㄱ", room="보존서고")


def test_bulk_is_all_or_nothing(lib):
    body = {"ranges": [
        {"slot_id": "R-01-01", "room": "대출실", "start_call_number": "811.16 가1ㄱ", "end_call_number": "811.16 김54ㄱ"},
        {"slot_id": "R-01-02", "room": "대출실", "start_call_number": "811.16 김5ㄱ", "end_call_number": "811.16 하1ㄱ"},
    ]}
    assert lib.put("/call-ranges", json=body, headers=ADMIN_HEADERS).status_code == 422
    assert lib.get("/call-ranges").json() == []
    body["ranges"][1]["start_call_number"] = "811.16 김55ㄱ"
    assert len(lib.put("/call-ranges", json=body, headers=ADMIN_HEADERS).json()) == 2


def test_errors(lib):
    put(lib, "R-09-01", "1 A", "2 B", expect=404)
    put(lib, "R-01", "1 A", "2 B", expect=422)
    assert lib.put("/call-ranges/R-01-01", json={"room": "x", "start_call_number": "1",
                                                 "end_call_number": "2"}).status_code == 401
    assert lib.delete("/call-ranges/R-01-01", headers=ADMIN_HEADERS).status_code == 404


def test_history_and_locate(lib):
    put(lib, "R-01-01", "811.16 가1ㄱ", "811.16 김54ㄱ")
    put(lib, "R-01-01", "811.16 가1ㄱ", "811.16 김60ㄱ")
    put(lib, "R-01-01", "811.16 가1ㄱ", "811.16 김60ㄱ")        # 같은 값 → 이력 안 남김
    assert lib.delete("/call-ranges/R-01-01", headers=ADMIN_HEADERS).status_code == 204
    rows = lib.get("/call-ranges/history", params={"slot_id": "R-01-01"}).json()
    assert [(r["old_end"], r["new_end"]) for r in rows] == [
        ("811.16 김60ㄱ", None), ("811.16 김54ㄱ", "811.16 김60ㄱ"), (None, "811.16 김54ㄱ")]

    put(lib, "R-01-02", "811.16 김55ㄱ", "811.16 하99ㅎ")
    found = lib.get("/call-ranges/locate", params={"call_number": "811.16 박1ㄱ c.2"}).json()
    assert (found["slot_id"], found["room"]) == ("R-01-02", ROOM)
    assert lib.get("/call-ranges/locate", params={"call_number": "900 A1"}).json()["slot_id"] is None


# ---------- 3. 적용 ----------

def ranges(client):
    put(client, "R-01-01", "811.16 가1ㄱ", "811.16 김54ㄱ")          # 001, 002, 003(c.2)
    put(client, "R-01-02", "811.16 김55ㄱ", "811.16 하99ㅎ")         # 004, 005


def test_apply_assigns_registered_location(lib):
    ranges(lib)
    assert apply(lib, dry_run=True)["assigned"] == 5
    assert slot_of(lib, "EM9700001") is None                          # 미리보기는 저장 안 함

    result = apply(lib)
    assert {k: result[k] for k in ("assigned", "unchanged", "uncovered")} == {"assigned": 5, "unchanged": 0,
                                                                              "uncovered": 1}
    assert [slot_of(lib, c) for c in COPIES] == ["R-01-01", "R-01-01", "R-01-01", "R-01-02", "R-01-02", None]
    copy = lib.get("/copies/EM9700003").json()
    assert (copy["physical_slot_id"], copy["physical_location_certainty"]) == ("R-01-01", "ESTIMATED")
    history = lib.get("/copies/EM9700003/location-history").json()
    assert (history[-1]["to_slot_id"], history[-1]["reason"]) == ("R-01-01", "청구기호 범위표 적용")

    assert apply(lib)["unchanged"] == 5                               # 다시 적용해도 그대로


def test_apply_moves_and_removes_after_range_change(lib):
    ranges(lib)
    apply(lib)
    put(lib, "R-01-01", "811.16 가1ㄱ", "811.16 가99ㅎ")               # 002, 003이 범위에서 빠짐
    put(lib, "R-01-03", "811.16 김1ㄱ", "811.16 김54ㄱ")               # → R-01-03으로
    lib.delete("/call-ranges/R-01-02", headers=ADMIN_HEADERS)         # 004, 005는 범위 없음
    result = apply(lib)
    assert (result["moved"], result["removed"], result["unchanged"]) == (2, 2, 1)
    assert slot_of(lib, "EM9700002") == "R-01-03"
    assert slot_of(lib, "EM9700004") is None
    assert lib.get("/copies/EM9700004").json()["physical_location_type"] == "UNKNOWN"


def test_manual_location_is_kept(lib):
    ranges(lib)
    apply(lib)
    res = lib.put("/copies/EM9700005/registered-location", headers=ADMIN_HEADERS,
                  json={"slot_id": "R-01-03", "reason": "별치 서가"})
    assert res.status_code == 200, res.text
    result = apply(lib)
    assert result["kept_manual"] == 1
    assert slot_of(lib, "EM9700005") == "R-01-03"


# ---------- 4. 오배치 판정과 연결 ----------

def test_range_location_drives_misplacement_verdict(lib, db):
    ranges(lib)
    apply(lib)
    db.add(Robot(id="R970"))
    db.commit()
    session_id = lib.post("/scan-sessions", json={"robot_id": "R970", "shelf_id": "R-01"},
                          headers=ADMIN_HEADERS).json()["id"]
    ai = {"X-API-Key": issue_api_key(db, DeviceType.AI, "ai-range")}

    def observe(slot_id, copy_id, obs_id):
        res = lib.post("/observations", headers=ai, json={
            "observation_id": obs_id, "scan_session_id": session_id, "robot_id": "R970", "shelf_id": "R-01",
            "slot_id": slot_id, "detected_text": "x", "book_copy_id": copy_id,
            "confidence": {"detection": 0.95, "ocr": 0.9, "identification": 0.9}, "candidates": [],
            "captured_at": "2026-11-16T10:00:00+09:00"})
        assert res.status_code == 201, res.text
        page = lib.get("/verifications", params={"scan_session_id": session_id, "limit": 100}).json()
        return next(v for v in page["items"] if v["observation_id"] == obs_id)

    assert observe("R-01-01", "EM9700002", "OBS-RNG-1")["location_verdict"] == "NORMAL"
    wrong = observe("R-01-01", "EM9700005", "OBS-RNG-2")              # 박1ㄱ은 R-01-02 칸 책
    assert (wrong["location_verdict"], wrong["registered_slot_id"]) == ("MISPLACED_SUSPECTED", "R-01-02")
