"""개발용 샘플 서가·도서 데이터 생성.

실행 (프로젝트 폴더에서):
    .venv\\Scripts\\python -m scripts.seed_sample_data

이미 있는 로봇·서가·도서·복본은 건너뛰므로 여러 번 실행해도 된다.
기획서 8장 AI 예시(A-03-02 칸, BOOK-125/BOOK-126 '파이썬 프로그래밍')와 맞춰 두었다.
"""
from sqlalchemy import select

from app.core.enums import LoanStatus
from app.db.session import SessionLocal
from app.models import Book, BookCopy, Robot, Shelf
from app.schemas.book import BookCreate, CopyCreate
from app.schemas.shelf import ShelfCreate
from app.services import book as book_service
from app.services import shelf as shelf_service

SEED_ADMIN = "seed"

# [가정] 로봇 등록 API는 7단계에서 만든다. 그 전까지 스캔 세션 테스트용으로 1대 넣어 둔다.
ROBOTS = [("R001", "도서관리 로봇 1호")]

SHELVES = [("A-01", 5), ("A-02", 5), ("A-03", 5), ("B-01", 5)]

# (서지 정보, [(복본 ID, 등록 칸, 대출 상태)])
BOOKS = [
    ({"isbn": "9791100000011", "title": "파이썬 프로그래밍", "author": "김파이", "call_number": "005.133-김42"},
     [("BOOK-125", "A-03-02", LoanStatus.AVAILABLE),
      ("BOOK-126", "A-03-02", LoanStatus.AVAILABLE)]),  # 같은 제목 복본 2권
    ({"isbn": "9791100000028", "title": "자료구조와 알고리즘", "author": "이알고", "call_number": "005.73-이69"},
     [("BOOK-127", "A-03-03", LoanStatus.AVAILABLE)]),
    ({"isbn": "9791100000035", "title": "데이터베이스 개론", "author": "박디비", "call_number": "005.74-박22"},
     [("BOOK-128", "A-03-04", LoanStatus.CHECKED_OUT)]),  # 대출 중 → 미관측이어도 정상
    ({"isbn": "9791100000042", "title": "로봇공학 입문", "author": "최로봇", "call_number": "629.892-최56"},
     [("BOOK-201", "B-01-01", LoanStatus.AVAILABLE),
      ("BOOK-202", "B-01-01", LoanStatus.AVAILABLE)]),
    ({"isbn": "9791100000059", "title": "컴퓨터 비전", "author": "정비전", "call_number": "006.37-정49"},
     [("BOOK-203", "B-01-02", LoanStatus.AVAILABLE)]),
    ({"isbn": "9791100000066", "title": "운영체제", "author": "한커널", "call_number": "005.43-한38"},
     [("BOOK-130", "A-03-05", LoanStatus.AVAILABLE)]),  # 모의 AI 시나리오에서 관측되지 않음 → 미관측 예시
    ({"isbn": None, "title": "도서관 운영 매뉴얼", "author": None, "call_number": "020-도53"},  # ISBN 없는 자료
     [("BOOK-101", "A-01-01", LoanStatus.AVAILABLE)]),
]


def main() -> None:
    created = {"robots": 0, "shelves": 0, "books": 0, "copies": 0}
    with SessionLocal() as db:
        for robot_id, name in ROBOTS:
            if db.get(Robot, robot_id) is None:
                db.add(Robot(id=robot_id, name=name))
                db.commit()
                created["robots"] += 1

        for shelf_id, slot_count in SHELVES:
            if db.get(Shelf, shelf_id) is None:
                shelf_service.create_shelf(db, ShelfCreate(id=shelf_id, slot_count=slot_count))
                created["shelves"] += 1

        for book_data, copies in BOOKS:
            book = db.scalar(select(Book).where(Book.title == book_data["title"]))
            if book is None:
                book = book_service.create_book(db, BookCreate(**book_data))
                created["books"] += 1
            for copy_id, slot_id, loan_status in copies:
                if db.get(BookCopy, copy_id) is None:
                    book_service.create_copy(
                        db, book.id, CopyCreate(id=copy_id, slot_id=slot_id, loan_status=loan_status), SEED_ADMIN
                    )
                    created["copies"] += 1

    print(f"생성 완료: 로봇 {created['robots']}대, 서가 {created['shelves']}개, 도서 {created['books']}개, 복본 {created['copies']}개 (이미 있던 것은 건너뜀)")


if __name__ == "__main__":
    main()
