"""도서관 장서 CSV 가져오기 (자료실 선택, 재실행 안전, 등록번호 ID)."""
import pytest
from sqlalchemy import select

from app.core.enums import LoanStatus
from app.models import Book, BookCopy
from app.services.library_import import base_call_number, import_catalog, loan_status_of, room_matches

LEND = "고양캠퍼스 대출실"
KEEP = "고양캠퍼스 보존서고"


def row(copy_id, bib, call, room=LEND, title="테스트 장서", isbn="8970658262", status="대출가능", **extra):
    return {"서지관리번호": bib, "등록번호": copy_id, "청구기호": call, "서명": title, "소장위치": room,
            "저자명": "홍길동", "ISBN": isbn, "대출상태": status, "출판사": "출판사", "발행연도": "1994", **extra}


ROWS = [
    row("EM9900001", "T-9001", "823.914 T455r이 v.1"),
    row("EM9900002", "T-9001", "823.914 T455r이 v.1 c.2"),
    row("EM9900003", "T-9001", "823.914 T455r이 v.2 c.2", room=KEEP),       # 같은 도서의 다른 권은 보존서고
    row("EM9900004", "T-9002", "811.16 정565ㅎ", title="다른 책", status="대출중 ( 1명, 예약중)"),
    row("WM9900005", "T-9003", "005.1 K12a", room=KEEP, isbn=""),
]


def _copy(db, copy_id):
    return db.get(BookCopy, copy_id)


@pytest.mark.parametrize("text, expected", [
    ("대출가능", LoanStatus.AVAILABLE), ("대출중", LoanStatus.CHECKED_OUT),
    ("대출중 ( 1명, 예약중)", LoanStatus.CHECKED_OUT), ("대출불가", LoanStatus.UNKNOWN),
    ("대출불가/열람불가", LoanStatus.UNKNOWN), ("", LoanStatus.UNKNOWN),
])
def test_loan_status_mapping(text, expected):
    assert loan_status_of(text) == expected


def test_helpers():
    assert base_call_number("811.16 정565ㅎ c.6") == "811.16 정565ㅎ"
    assert base_call_number("823.914 T455r이 v.1") == "823.914 T455r이 v.1"
    assert room_matches(LEND, ["대출실"]) and room_matches(LEND, [LEND])
    assert not room_matches("고양캠퍼스 보존서고(대형도서)", ["보존서고"])


def test_import_only_selected_room(db):
    result = import_catalog(db, ROWS, ["대출실"])
    assert (result.rows_selected, result.books_created, result.copies_created) == (3, 2, 3)
    assert _copy(db, "EM9900003") is None and _copy(db, "WM9900005") is None

    copy = _copy(db, "EM9900002")
    assert (copy.call_number, copy.room, copy.loan_status) == ("823.914 T455r이 v.1 c.2", LEND, LoanStatus.AVAILABLE)
    assert _copy(db, "EM9900004").loan_status == LoanStatus.CHECKED_OUT

    book = db.scalar(select(Book).where(Book.library_bib_id == "T-9001"))
    assert (book.title, book.isbn, book.publication_year, book.call_number) == \
        ("테스트 장서", "8970658262", "1994", "823.914 T455r이 v.1")


def test_add_other_room_later_attaches_to_same_book(db):
    import_catalog(db, ROWS, ["대출실"])
    result = import_catalog(db, ROWS, ["보존서고"])
    assert (result.books_created, result.copies_created, result.copies_unchanged) == (1, 2, 0)

    book = db.scalar(select(Book).where(Book.library_bib_id == "T-9001"))
    assert sorted(c.id for c in book.copies) == ["EM9900001", "EM9900002", "EM9900003"]
    assert _copy(db, "WM9900005").book.isbn is None


def test_rerun_is_idempotent_and_keeps_loan_status(db):
    import_catalog(db, ROWS)
    _copy(db, "EM9900001").loan_status = LoanStatus.IN_TRANSIT   # 시스템에서 바뀐 상태
    db.flush()

    result = import_catalog(db, ROWS)
    assert (result.books_created, result.copies_created, result.copies_updated, result.copies_unchanged) == (0, 0, 0, 5)
    assert _copy(db, "EM9900001").loan_status == LoanStatus.IN_TRANSIT


def test_rerun_updates_changed_values(db):
    import_catalog(db, ROWS)
    changed = [row("EM9900001", "T-9001", "823.914 T455r이 v.1", room=KEEP, title="고친 제목")]
    result = import_catalog(db, changed)
    assert (result.books_updated, result.copies_updated) == (1, 1)
    assert _copy(db, "EM9900001").room == KEEP
    assert db.scalar(select(Book.title).where(Book.library_bib_id == "T-9001")) == "고친 제목"


def test_bad_rows_are_skipped(db):
    rows = [row("em9900011", "T-9011", "001 A1"),                  # 소문자 → 대문자로 받음
            row("BAD-1", "T-9012", "001 A2"),                      # 형식 오류
            row("EM9900013", "T-9013", ""),                        # 청구기호 없음
            row("EM9900011", "T-9011", "001 A1")]                  # CSV 안 중복
    result = import_catalog(db, rows)
    assert result.copies_created == 1 and _copy(db, "EM9900011") is not None
    assert len(result.skipped) == 3


def test_same_isbn_on_different_books(db):
    rows = [row("EM9900021", "T-9021", "001 B1"), row("EM9900022", "T-9022", "001 B2", title="2판")]
    assert import_catalog(db, rows).books_created == 2


def test_imported_copy_visible_through_api(client, db):
    import_catalog(db, ROWS, ["대출실"])
    db.commit()
    res = client.get("/copies/EM9900002")
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["call_number"], body["room"], body["registered_slot_id"]) == ("823.914 T455r이 v.1 c.2", LEND, None)
    assert client.get("/books", params={"q": "테스트 장서"}).json()["items"][0]["library_bib_id"] == "T-9001"


def test_registration_number_accepted_as_copy_id(client):
    assert client.get("/copies/EM0174130").status_code == 404           # 형식은 통과, 데이터만 없음
    assert client.get("/copies/em-1").status_code == 422
