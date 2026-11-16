"""청구기호 정렬 규칙, 관측의 bbox·순번, 같은 칸 안 순서 검사."""
import pytest

from app.core.call_number import out_of_order, sort_key
from app.core.enums import DeviceType
from app.models import Robot
from app.services.device import issue_api_key
from tests.conftest import ADMIN_HEADERS

# ---------- 1. 청구기호 정렬 ----------

ORDERED = [
    "005.1 K12a",
    "005.133 김54ㄱ",
    "005.2 가12ㄴ",            # 소수: 005.133 < 005.2
    "811.16 김54ㄱ",
    "811.16 김54ㄱ c.2",       # 복본 기호는 맨 마지막
    "811.16 김542ㄴ",          # 저자번호는 소수처럼: 54 < 542 < 55
    "811.16 김55ㄷ",
    "811.16 박1ㄱ",
    "823.914 T455r이 v.1",
    "823.914 T455r이 v.1 c.2",
    "823.914 T455r이 v.2",
    "823.914 T455r이 v.10",    # 권차는 크기 순: 2 < 10
    "823.914 T455s김",
]


def test_sort_order():
    assert sorted(ORDERED[::-1], key=sort_key) == ORDERED


def test_messy_copy_and_volume_marks_read_the_same():
    assert sort_key("811.16 김54ㄱ C.2") == sort_key("811.16 김54ㄱ c,2") == sort_key("811.16 김54ㄱ c.2")
    assert sort_key("823.914 T455r이 v,1") == sort_key("823.914 T455r이 v.1")


@pytest.mark.parametrize("shelf, wrong", [
    (ORDERED[:6], set()),
    (["811.16 김54ㄱ", "811.16 김542ㄴ", "823 A12b", "811.16 김55ㄷ", "811.16 박1ㄱ"], {2}),   # 한 권만 튐
    (["811.16 박1ㄱ", "811.16 김54ㄱ", "811.16 김542ㄴ", "811.16 김55ㄷ"], {0}),               # 맨 앞 책
    (["005.1 K12a", "005.1 K12a"], set()),                                                   # 같은 청구기호
    ([], set()),
])
def test_out_of_order(shelf, wrong):
    assert out_of_order(shelf) == wrong


# ---------- 2. 관측 bbox·순번 ----------

CALLS = {"EM9800001": "811.16 김54ㄱ", "EM9800002": "811.16 김54ㄱ c.2", "EM9800003": "811.16 김542ㄴ",
         "EM9800004": "811.16 김55ㄷ", "EM9800005": "823 A12b"}


@pytest.fixture
def shelf_env(client, db):
    db.add(Robot(id="R980"))
    db.commit()
    client.post("/shelves", json={"id": "S-01", "slot_count": 2}, headers=ADMIN_HEADERS)
    book_ids = {}
    for copy_id, call in CALLS.items():
        base = call.removesuffix(" c.2")
        if base not in book_ids:
            book_ids[base] = client.post("/books", json={"title": f"책 {base}", "call_number": base},
                                         headers=ADMIN_HEADERS).json()["id"]
        res = client.post(f"/books/{book_ids[base]}/copies", headers=ADMIN_HEADERS,
                          json={"id": copy_id, "slot_id": "S-01-01", "call_number": call, "room": "고양캠퍼스 대출실"})
        assert res.status_code == 201, res.text
    session_id = client.post("/scan-sessions", json={"robot_id": "R980", "shelf_id": "S-01"},
                             headers=ADMIN_HEADERS).json()["id"]
    return {"session_id": session_id, "ai": {"X-API-Key": issue_api_key(db, DeviceType.AI, "ai-order")}}


_n = iter(range(1, 10_000))


def observe(client, env, order, copy_id=None, candidates=(), image="img/s01-01.jpg", bbox="auto", slot="S-01-01",
            reobservation_of=None, expect=201, **extra):
    data = {
        "observation_id": f"OBS-ORD-{next(_n):04d}", "scan_session_id": env["session_id"], "robot_id": "R980",
        "shelf_id": "S-01", "slot_id": slot, "detected_text": "x", "book_copy_id": copy_id,
        "confidence": {"detection": 0.95, "ocr": 0.9, "identification": 0.9},
        "candidates": [{"book_copy_id": c, "score": s} for c, s in candidates],
        "reobservation_of": reobservation_of, "image_ref": image, "order": order,
        "bbox": [(order or 1) * 100, 0, 80, 600] if bbox == "auto" else bbox, "image_width": 1920, "image_height": 1080,
        "captured_at": "2026-11-16T10:00:00+09:00", **extra,
    }
    res = client.post("/observations", headers=env["ai"], json=data)
    assert res.status_code == expect, res.text
    return res.json()


def test_bbox_and_order_saved(client, shelf_env):
    obs = observe(client, shelf_env, 1, "EM9800001")
    assert (obs["order"], obs["bbox"], obs["image_width"], obs["image_height"]) == (1, [100, 0, 80, 600], 1920, 1080)


@pytest.mark.parametrize("order, extra", [
    (1, {"bbox": [0, 0, 80]}),                  # 4개가 아님
    (1, {"bbox": [0, 0, 0, 100]}),              # 폭 0
    (1, {"bbox": [1900, 0, 80, 100]}),          # 이미지 밖
    (0, {}),                                    # 순번은 1부터
    (1, {"image": None}),                       # 위치만 있고 이미지 ID 없음
])
def test_bad_position_rejected(client, shelf_env, order, extra):
    observe(client, shelf_env, order, "EM9800001", expect=422, **extra)


def test_old_payload_without_position_still_works(client, shelf_env):
    obs = observe(client, shelf_env, None, "EM9800001", image=None, bbox=None)
    assert obs["order"] is None


# ---------- 3. 같은 칸 안 순서 검사 ----------

def order_check(client, env, slot="S-01-01"):
    res = client.get(f"/scan-sessions/{env['session_id']}/slots/{slot}/order-check")
    assert res.status_code == 200, res.text
    return res.json()


def test_order_check_flags_misplaced_book(client, shelf_env):
    for order, copy_id in enumerate(["EM9800001", "EM9800003", "EM9800005", "EM9800004"], start=1):
        observe(client, shelf_env, order, copy_id)
    result = order_check(client, shelf_env)
    assert result["out_of_order_count"] == 1
    [image] = result["images"]
    assert image["image_ref"] == "img/s01-01.jpg" and image["image_width"] == 1920
    assert [(b["order"], b["book_copy_id"], b["status"]) for b in image["books"]] == [
        (1, "EM9800001", "IN_ORDER"), (2, "EM9800003", "IN_ORDER"),
        (3, "EM9800005", "OUT_OF_ORDER"), (4, "EM9800004", "IN_ORDER")]
    assert image["books"][2]["call_number"] == "823 A12b"


def test_candidates_of_same_book_use_base_call_number(client, shelf_env):
    observe(client, shelf_env, 1, candidates=[("EM9800001", 0.7), ("EM9800002", 0.7)])   # 복본 구분 불가
    observe(client, shelf_env, 2, "EM9800004")
    observe(client, shelf_env, 3, candidates=[("EM9800001", 0.6), ("EM9800005", 0.6)])   # 서로 다른 책
    books = order_check(client, shelf_env)["images"][0]["books"]
    assert (books[0]["book_copy_id"], books[0]["call_number"], books[0]["status"]) == (None, "811.16 김54ㄱ", "IN_ORDER")
    assert books[2]["status"] == "UNIDENTIFIED"


def test_each_image_checked_separately_and_reobservation_replaces(client, shelf_env):
    observe(client, shelf_env, 1, "EM9800004", image="img/a.jpg")
    wrong = observe(client, shelf_env, 2, "EM9800001", image="img/a.jpg")      # 김55 뒤에 김54 → 순서 틀림
    observe(client, shelf_env, 1, "EM9800001", image="img/b.jpg")               # 다른 사진의 순번 1
    assert order_check(client, shelf_env)["out_of_order_count"] == 1

    observe(client, shelf_env, 2, "EM9800005", image="img/a.jpg", reobservation_of=wrong["id"])
    images = {i["image_ref"]: i for i in order_check(client, shelf_env)["images"]}
    assert [b["book_copy_id"] for b in images["img/a.jpg"]["books"]] == ["EM9800004", "EM9800005"]
    assert images["img/b.jpg"]["out_of_order_count"] == 0


def test_order_check_empty_slot_and_errors(client, shelf_env):
    assert order_check(client, shelf_env, "S-01-02") == {
        "scan_session_id": shelf_env["session_id"], "slot_id": "S-01-02", "out_of_order_count": 0, "images": []}
    assert client.get(f"/scan-sessions/{shelf_env['session_id']}/slots/S-01/order-check").status_code == 422
    assert client.get("/scan-sessions/SCAN-none/slots/S-01-01/order-check").status_code == 404
