"""4단계 도서·서가 API 테스트 (실제 MySQL 사용, 테스트 후 롤백)."""
import pytest
from sqlalchemy import select

from app.core.enums import AdminActionType
from app.models import AdminAction
from tests.conftest import ADMIN_HEADERS

BOOK = {"isbn": "9790000000001", "title": "테스트 파이썬", "author": "홍길동", "call_number": "005.133-T1"}


@pytest.fixture
def shelf(client):
    res = client.post("/shelves", json={"id": "T-01", "slot_count": 3}, headers=ADMIN_HEADERS)
    assert res.status_code == 201
    client.post("/shelves", json={"id": "T-02", "slot_count": 2}, headers=ADMIN_HEADERS)
    return res.json()


@pytest.fixture
def book(client):
    res = client.post("/books", json=BOOK, headers=ADMIN_HEADERS)
    assert res.status_code == 201
    return res.json()


# ---------- 관리자 인증 ----------

@pytest.mark.parametrize("headers", [{}, {"X-Admin-Token": "wrong"}])
def test_write_requires_admin_token(client, headers):
    res = client.post("/books", json=BOOK, headers=headers)
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


def test_read_does_not_require_token(client, book):
    assert client.get("/books").status_code == 200


# ---------- 서가 ----------

def test_create_shelf_creates_slots(shelf):
    assert shelf["zone"] == "T"
    assert [s["id"] for s in shelf["slots"]] == ["T-01-01", "T-01-02", "T-01-03"]


def test_duplicate_shelf_rejected(client, shelf):
    res = client.post("/shelves", json={"id": "T-01", "slot_count": 1}, headers=ADMIN_HEADERS)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "ALREADY_EXISTS"


def test_unknown_shelf_404(client):
    assert client.get("/shelves/Z-99").status_code == 404


# ---------- 도서 ----------

def test_book_crud(client, book):
    assert client.get(f"/books/{book['id']}").json()["title"] == "테스트 파이썬"

    updated = {**BOOK, "title": "테스트 파이썬 개정판"}
    res = client.put(f"/books/{book['id']}", json=updated, headers=ADMIN_HEADERS)
    assert res.status_code == 200
    assert res.json()["title"] == "테스트 파이썬 개정판"


def test_search_books(client, book):
    client.post("/books", json={"title": "테스트 자바"}, headers=ADMIN_HEADERS)
    page = client.get("/books", params={"q": "테스트 파이썬"}).json()
    assert page["total"] == 1
    assert page["items"][0]["id"] == book["id"]


def test_duplicate_isbn_allowed(client, book):
    """전집·판 차이로 서로 다른 도서가 같은 ISBN을 쓰는 경우가 있어 허용한다."""
    res = client.post("/books", json={**BOOK, "title": "다른 책"}, headers=ADMIN_HEADERS)
    assert res.status_code == 201
    assert res.json()["id"] != book["id"]


def test_invalid_isbn_rejected(client):
    res = client.post("/books", json={**BOOK, "isbn": "978-89-1234"}, headers=ADMIN_HEADERS)
    assert res.status_code == 422
    assert res.json()["error"]["details"][0]["field"] == "isbn"


def test_unknown_book_404(client):
    res = client.get("/books/999999999")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


# ---------- 복본 ----------

def test_same_title_copies_are_distinguished(client, shelf, book):
    for copy_id in ("BOOK-900125", "BOOK-900126"):
        res = client.post(f"/books/{book['id']}/copies", json={"id": copy_id, "slot_id": "T-01-02"}, headers=ADMIN_HEADERS)
        assert res.status_code == 201

    copies = client.get(f"/books/{book['id']}").json()["copies"]
    assert [c["id"] for c in copies] == ["BOOK-900125", "BOOK-900126"]

    slot = client.get("/shelves/T-01").json()["slots"][1]
    assert [c["book_copy_id"] for c in slot["registered_copies"]] == ["BOOK-900125", "BOOK-900126"]


def test_new_copy_physical_location_is_only_estimated(client, shelf, book):
    copy = client.post(f"/books/{book['id']}/copies", json={"id": "BOOK-900125", "slot_id": "T-01-01"},
                       headers=ADMIN_HEADERS).json()
    assert copy["registered_slot_id"] == "T-01-01"
    assert copy["physical_location_type"] == "SHELF"
    assert copy["physical_location_certainty"] == "ESTIMATED"  # 로봇이 확인하지 않았으므로 확정 아님
    assert copy["physical_location_confirmed_at"] is None


def test_copy_without_location(client, book):
    copy = client.post(f"/books/{book['id']}/copies", json={"id": "BOOK-900125"}, headers=ADMIN_HEADERS).json()
    assert copy["registered_slot_id"] is None
    assert copy["physical_location_type"] == "UNKNOWN"


@pytest.mark.parametrize("payload, status", [
    ({"id": "BOOK-900125", "slot_id": "Z-99-01"}, 404),  # 없는 칸
    ({"id": "125"}, 422),                                # ID 형식 오류
])
def test_invalid_copy_rejected(client, shelf, book, payload, status):
    assert client.post(f"/books/{book['id']}/copies", json=payload, headers=ADMIN_HEADERS).status_code == status


def test_duplicate_copy_rejected(client, book):
    client.post(f"/books/{book['id']}/copies", json={"id": "BOOK-900125"}, headers=ADMIN_HEADERS)
    res = client.post(f"/books/{book['id']}/copies", json={"id": "BOOK-900125"}, headers=ADMIN_HEADERS)
    assert res.status_code == 409


def test_loan_status_update(client, book):
    client.post(f"/books/{book['id']}/copies", json={"id": "BOOK-900125"}, headers=ADMIN_HEADERS)
    res = client.put("/copies/BOOK-900125/loan-status", json={"loan_status": "CHECKED_OUT"}, headers=ADMIN_HEADERS)
    assert res.json()["loan_status"] == "CHECKED_OUT"

    res = client.put("/copies/BOOK-900125/loan-status", json={"loan_status": "LOST"}, headers=ADMIN_HEADERS)
    assert res.status_code == 422  # 정의되지 않은 상태


# ---------- 등록 위치 변경 ----------

def test_change_registered_location_records_history(client, db, shelf, book):
    client.post(f"/books/{book['id']}/copies", json={"id": "BOOK-900125", "slot_id": "T-01-01"}, headers=ADMIN_HEADERS)

    res = client.put("/copies/BOOK-900125/registered-location",
                     json={"slot_id": "T-02-01", "reason": "서가 재배치"}, headers=ADMIN_HEADERS)
    assert res.status_code == 200
    copy = res.json()
    assert copy["registered_slot_id"] == "T-02-01"
    assert copy["physical_slot_id"] == "T-01-01"  # 실제로 옮겨졌는지는 로봇이 확인해야 하므로 물리 위치는 그대로

    history = client.get("/copies/BOOK-900125/location-history").json()
    assert [(h["from_slot_id"], h["to_slot_id"]) for h in history] == [(None, "T-01-01"), ("T-01-01", "T-02-01")]
    assert history[1]["changed_by"] == "tester"
    assert history[1]["reason"] == "서가 재배치"

    action = db.scalar(select(AdminAction).where(AdminAction.book_copy_id == "BOOK-900125"))
    assert action.action_type == AdminActionType.CHANGE_REGISTERED_LOCATION
    assert action.admin_id == "tester"


def test_change_to_same_location_rejected(client, shelf, book):
    client.post(f"/books/{book['id']}/copies", json={"id": "BOOK-900125", "slot_id": "T-01-01"}, headers=ADMIN_HEADERS)
    res = client.put("/copies/BOOK-900125/registered-location",
                     json={"slot_id": "T-01-01", "reason": "x"}, headers=ADMIN_HEADERS)
    assert res.status_code == 409


def test_location_change_requires_reason(client, shelf, book):
    client.post(f"/books/{book['id']}/copies", json={"id": "BOOK-900125", "slot_id": "T-01-01"}, headers=ADMIN_HEADERS)
    res = client.put("/copies/BOOK-900125/registered-location", json={"slot_id": "T-02-01"}, headers=ADMIN_HEADERS)
    assert res.status_code == 422
