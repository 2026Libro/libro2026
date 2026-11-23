"""AI 매칭용 도서 목록 CSV 만들기 (DB 기준이라 AI가 보내는 등록번호가 백엔드와 항상 맞는다).

실행 (프로젝트 폴더에서):
    .venv\\Scripts\\python -m scripts.export_ai_catalog                       # 대출실 → LIBRO2026_AI_catalog_대출실.csv
    .venv\\Scripts\\python -m scripts.export_ai_catalog --room 보존서고 --out catalog.csv

열: 등록번호(book_copy_id), 청구기호(책등 라벨, c.N 포함), 대표청구기호(c.N 제외), 서명, 저자, 출판사, 발행연도, 자료실
청구기호 순으로 정렬한다 (서가에 꽂히는 순서).
"""
import argparse
import csv
import sys

from sqlalchemy import select

from app.core.call_number import sort_key
from app.db.session import SessionLocal
from app.models import Book, BookCopy
from app.services.call_range import normalize_room
from app.services.library_import import base_call_number

COLUMNS = ["등록번호", "청구기호", "대표청구기호", "서명", "저자", "출판사", "발행연도", "자료실"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--room", default="대출실")
    parser.add_argument("--out")
    args = parser.parse_args()
    room = normalize_room(args.room)
    out = args.out or f"LIBRO2026_AI_catalog_{args.room}.csv"

    with SessionLocal() as db:
        rows = db.execute(
            select(BookCopy.id, BookCopy.call_number, Book.title, Book.author, Book.publisher, Book.publication_year)
            .join(Book, Book.id == BookCopy.book_id).where(BookCopy.room == room, BookCopy.call_number.is_not(None))
        ).all()
    rows.sort(key=lambda r: (sort_key(r.call_number), r.id))
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for r in rows:
            writer.writerow([r.id, r.call_number, base_call_number(r.call_number), r.title, r.author or "",
                             r.publisher or "", r.publication_year or "", room])
    print(f"{out}: {len(rows):,}권 ({room})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
