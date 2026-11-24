"""도서 API. 조회는 인증 없이, 등록·수정은 관리자 토큰이 필요하다."""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.session import get_db
from app.schemas.book import BookCreate, BookDetail, BookRead, BookUpdate, CopyCreate, CopyRead
from app.schemas.common import Page
from app.services import book as book_service

router = APIRouter(prefix="/books", tags=["books"])


@router.post("", response_model=BookRead, status_code=status.HTTP_201_CREATED)
def create_book(data: BookCreate, db: Session = Depends(get_db), _admin: str = Depends(require_admin)):
    """도서(서지 정보) 등록. 실제 책 한 권 한 권은 복본으로 따로 등록한다."""
    return book_service.create_book(db, data)


@router.get("", response_model=Page[BookRead])
def list_books(
    q: str | None = Query(default=None, max_length=100, description="제목·저자·ISBN·청구기호 검색어"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = book_service.list_books(db, q, limit, offset)
    return Page[BookRead](items=items, total=total, limit=limit, offset=offset)


@router.get("/{book_id}", response_model=BookDetail)
def get_book(book_id: int, db: Session = Depends(get_db)):
    """도서 상세 + 복본 목록 (복본별 대출 상태, 등록 위치, 현재 물리 위치)."""
    book = book_service.get_book(db, book_id)
    return BookDetail(
        **BookRead.model_validate(book).model_dump(),
        copies=[CopyRead.from_model(c) for c in sorted(book.copies, key=lambda c: c.id)],
    )


@router.put("/{book_id}", response_model=BookRead)
def update_book(book_id: int, data: BookUpdate, db: Session = Depends(get_db), _admin: str = Depends(require_admin)):
    return book_service.update_book(db, book_id, data)


@router.post("/{book_id}/copies", response_model=CopyRead, status_code=status.HTTP_201_CREATED)
def create_copy(book_id: int, data: CopyCreate, db: Session = Depends(get_db), admin_id: str = Depends(require_admin)):
    """복본 등록. slot_id를 주면 등록 위치가 지정되고, 물리 위치는 그 칸으로 '추정'된다."""
    return CopyRead.from_model(book_service.create_copy(db, book_id, data, admin_id))
