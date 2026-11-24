"""도서관 장서 CSV(LIBRO2026_goyang_books.csv) → books·book_copies 가져오기.

- 자료실(소장위치)을 골라 일부만 넣을 수 있다. 예: 대출실 먼저, 보존서고는 나중에.
- 여러 번 실행해도 된다. 도서는 서지관리번호, 복본은 등록번호로 찾아서 있으면 고치고 없으면 넣는다.
- 등록 위치(칸)·현재 물리 위치는 건드리지 않는다. 서가·칸 위치는 도서관 데이터에 없다.
- 이미 있는 복본의 대출 상태는 바꾸지 않는다 (CSV는 수집 시점 기록이라 시스템 상태가 더 최신일 수 있음).
"""
import csv
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from app.core.enums import LoanStatus
from app.core.ids import BOOK_COPY_ID_PATTERN
from app.db.base import utcnow
from app.models import Book, BookCopy

CAMPUS_PREFIX = "고양캠퍼스 "
CHUNK = 1000
_COPY_MARK = re.compile(r"\s+c\.\s*\d+\s*$")
_COPY_ID = re.compile(BOOK_COPY_ID_PATTERN)


def loan_status_of(text: str) -> LoanStatus:
    """도서관 대출상태 문구 → 시스템 대출 상태. 대출불가·열람불가 등은 UNKNOWN."""
    text = (text or "").strip()
    if text.startswith("대출가능"):
        return LoanStatus.AVAILABLE
    if text.startswith("대출중"):
        return LoanStatus.CHECKED_OUT
    return LoanStatus.UNKNOWN


def base_call_number(call_number: str) -> str:
    """복본 기호(c.N)를 뗀 청구기호. '811.16 정565ㅎ c.6' → '811.16 정565ㅎ'"""
    return _COPY_MARK.sub("", call_number).strip()


def _isbn(text: str) -> str | None:
    value = re.sub(r"[^0-9Xx]", "", text or "").upper()
    return value if len(value) in (10, 13) else None


def _text(value: str, limit: int) -> str | None:
    value = (value or "").strip()
    return value[:limit] or None


def room_matches(location: str, rooms: Iterable[str]) -> bool:
    """'대출실' 또는 '고양캠퍼스 대출실' 모두 받는다. 정확히 같아야 한다 ('보존서고' ≠ '보존서고(대형도서)')."""
    return any(location in (r, CAMPUS_PREFIX + r) for r in rooms)


@dataclass
class ImportResult:
    rows_read: int = 0
    rows_selected: int = 0
    books_created: int = 0
    books_updated: int = 0
    copies_created: int = 0
    copies_updated: int = 0
    copies_unchanged: int = 0
    skipped: list[str] = field(default_factory=list)   # 형식 오류·충돌로 넣지 않은 행 (사유 포함)
    by_room: Counter = field(default_factory=Counter)


def read_csv(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _group(rows: list[dict], rooms: list[str] | None, result: ImportResult) -> dict[str, list[dict]]:
    """행 검사·정리 후 서지관리번호별로 묶는다."""
    by_bib: dict[str, list[dict]] = defaultdict(list)
    seen: set[str] = set()
    for row in rows:
        result.rows_read += 1
        location = (row.get("소장위치") or "").strip()
        if rooms and not room_matches(location, rooms):
            continue
        copy_id = (row.get("등록번호") or "").strip().upper()   # 원본에 소문자 em… 이 4건 있음
        bib_id = (row.get("서지관리번호") or "").strip()
        call_number = (row.get("청구기호") or "").strip()
        title = (row.get("서명") or "").strip()
        problem = None
        if not _COPY_ID.match(copy_id):
            problem = "등록번호 형식 오류"
        elif not bib_id or not title or not call_number:
            problem = "필수 값(서지관리번호·서명·청구기호) 없음"
        elif copy_id in seen:
            problem = "CSV 안에 같은 등록번호가 또 있음"
        if problem:
            result.skipped.append(f"{copy_id or '(빈 값)'}: {problem}")
            continue
        seen.add(copy_id)
        result.rows_selected += 1
        result.by_room[location] += 1
        by_bib[bib_id].append({**row, "등록번호": copy_id, "청구기호": call_number, "서명": title,
                               "소장위치": location})
    return by_bib


def _book_values(bib_id: str, copies: list[dict]) -> dict:
    first = min(copies, key=lambda r: r["등록번호"])
    # 대표 청구기호: 복본 기호를 뗀 값 중 가장 많은 것 (같으면 사전순 앞)
    bases = Counter(base_call_number(r["청구기호"]) for r in copies)
    call_number = min(bases, key=lambda b: (-bases[b], b))
    return {
        "library_bib_id": bib_id,
        "title": first["서명"][:500],
        "author": _text(first.get("저자명"), 300),
        "publisher": _text(first.get("출판사"), 200),
        "publication_year": _text(first.get("발행연도"), 10),
        "isbn": _isbn(first.get("ISBN")),
        "call_number": call_number[:100],
    }


_BOOK_FIELDS = ("title", "author", "publisher", "publication_year", "isbn", "call_number")


def import_catalog(db: Session, rows: list[dict], rooms: list[str] | None = None) -> ImportResult:
    """행을 DB에 반영한다. commit은 호출한 쪽이 한다 (dry-run이면 rollback)."""
    result = ImportResult()
    by_bib = _group(rows, rooms, result)
    bib_ids = sorted(by_bib, key=lambda b: (len(b), b))

    for start in range(0, len(bib_ids), CHUNK):
        chunk = bib_ids[start:start + CHUNK]
        wanted = {b: _book_values(b, by_bib[b]) for b in chunk}

        # 1. 도서: 없으면 넣고, 있으면 바뀐 값만 고친다
        existing = {b.library_bib_id: b for b in db.scalars(select(Book).where(Book.library_bib_id.in_(chunk)))}
        new_books = [v for b, v in wanted.items() if b not in existing]
        if new_books:
            now = utcnow()
            db.execute(insert(Book), [{**v, "created_at": now, "updated_at": now} for v in new_books])
            result.books_created += len(new_books)
        for b, book in existing.items():
            changes = {f: wanted[b][f] for f in _BOOK_FIELDS if getattr(book, f) != wanted[b][f]}
            if changes:
                db.execute(update(Book).where(Book.id == book.id).values(**changes, updated_at=utcnow()))
                result.books_updated += 1
        book_id = dict(db.execute(select(Book.library_bib_id, Book.id).where(Book.library_bib_id.in_(chunk))).all())

        # 2. 복본
        copy_rows = [r for b in chunk for r in by_bib[b]]
        copy_ids = [r["등록번호"] for r in copy_rows]
        current = {c.id: c for c in db.execute(
            select(BookCopy.id, BookCopy.book_id, BookCopy.call_number, BookCopy.room)
            .where(BookCopy.id.in_(copy_ids))).all()}
        new_copies = []
        now = utcnow()
        for r in copy_rows:
            target_book = book_id[r["서지관리번호"]]
            values = {"call_number": r["청구기호"][:100], "room": r["소장위치"][:50]}
            old = current.get(r["등록번호"])
            if old is None:
                new_copies.append({"id": r["등록번호"], "book_id": target_book,
                                   "loan_status": loan_status_of(r.get("대출상태")), **values,
                                   "created_at": now, "updated_at": now})
            elif old.book_id != target_book:
                result.skipped.append(f"{r['등록번호']}: 이미 다른 도서({old.book_id})의 복본으로 등록되어 있음")
            elif (old.call_number, old.room) != (values["call_number"], values["room"]):
                db.execute(update(BookCopy).where(BookCopy.id == r["등록번호"]).values(**values, updated_at=now))
                result.copies_updated += 1
            else:
                result.copies_unchanged += 1
        if new_copies:
            db.execute(insert(BookCopy), new_copies)
            result.copies_created += len(new_copies)
        db.flush()
    return result
