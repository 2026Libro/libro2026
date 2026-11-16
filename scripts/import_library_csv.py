"""도서관 장서 CSV를 DB(books·book_copies)에 넣는다.

실행 (프로젝트 폴더에서):
    .venv\\Scripts\\python -m scripts.import_library_csv --room 대출실 --dry-run   # 미리보기 (저장 안 함)
    .venv\\Scripts\\python -m scripts.import_library_csv --room 대출실             # 대출실만 넣기
    .venv\\Scripts\\python -m scripts.import_library_csv --room 보존서고 --room "보존서고(대형도서)"  # 나중에 추가

- 여러 번 실행해도 된다 (도서는 서지관리번호, 복본은 등록번호 기준으로 있으면 고치고 없으면 넣음).
- 기존 샘플 데이터(BOOK-125 등)·등록 위치·물리 위치는 건드리지 않는다.
- .env의 DB_NAME(기본 library_robot) DB에 넣는다.
"""
import argparse
import sys
import time

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.library_import import import_catalog, read_csv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", default="LIBRO2026_goyang_books.csv", help="장서 CSV 경로")
    parser.add_argument("--room", action="append",
                        help="넣을 자료실 (여러 번 지정 가능, '고양캠퍼스 ' 생략 가능). 생략하면 전체")
    parser.add_argument("--dry-run", action="store_true", help="결과만 보여 주고 저장하지 않음")
    args = parser.parse_args()

    started = time.monotonic()
    rows = read_csv(args.csv)
    print(f"DB: {get_settings().db_name} / CSV: {args.csv} ({len(rows):,}행) / 자료실: {args.room or '전체'}")

    with SessionLocal() as db:
        result = import_catalog(db, rows, args.room)
        if args.dry_run:
            db.rollback()
        else:
            db.commit()

    print(f"선택된 행: {result.rows_selected:,}")
    for room, count in result.by_room.most_common():
        print(f"  {room}: {count:,}권")
    print(f"도서: 새로 {result.books_created:,} / 수정 {result.books_updated:,}")
    print(f"복본: 새로 {result.copies_created:,} / 수정 {result.copies_updated:,} / 변경 없음 {result.copies_unchanged:,}")
    print(f"건너뜀: {len(result.skipped):,}")
    for line in result.skipped[:20]:
        print(f"  {line}")
    print(f"{'[미리보기] 저장하지 않았습니다' if args.dry_run else '저장 완료'} ({time.monotonic() - started:.1f}초)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
