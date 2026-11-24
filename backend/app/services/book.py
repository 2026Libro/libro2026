"""도서·복본·등록 위치 관리 로직 (BE-002, BE-003)."""
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.enums import AdminActionType, LocationCertainty, PhysicalLocationType, RegisteredLocationSource
from app.core.errors import AppError
from app.models import AdminAction, Book, BookCopy, BookLocation, BookLocationHistory, ShelfSlot
from app.schemas.book import BookCreate, BookUpdate, CopyCreate, LoanStatusUpdate, RegisteredLocationUpdate
from app.schemas.common import ErrorCode


def _not_found(what: str, key) -> AppError:
    return AppError(ErrorCode.NOT_FOUND, f"{what} '{key}'를 찾을 수 없습니다.", status_code=404)


def _commit_or_conflict(db: Session, message: str) -> None:
    """UNIQUE 위반(동시 요청 등)을 409로 바꾼다."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(ErrorCode.ALREADY_EXISTS, message, status_code=409)


def _get_slot(db: Session, slot_id: str) -> ShelfSlot:
    slot = db.get(ShelfSlot, slot_id)
    if slot is None:
        raise _not_found("서가 칸", slot_id)
    return slot


# ---------- 도서(서지 정보) ----------

def create_book(db: Session, data: BookCreate) -> Book:
    # ISBN은 중복을 허용한다 (전집·판 차이로 서로 다른 도서가 같은 ISBN을 쓰는 경우가 있음).
    book = Book(**data.model_dump())
    db.add(book)
    db.commit()
    return book


def list_books(db: Session, q: str | None, limit: int, offset: int) -> tuple[list[Book], int]:
    """제목·저자·ISBN·청구기호 부분 검색."""
    stmt = select(Book)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(Book.title.like(pattern), Book.author.like(pattern),
                              Book.isbn.like(pattern), Book.call_number.like(pattern)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(stmt.order_by(Book.id).limit(limit).offset(offset)))
    return items, total


def get_book(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id, options=[
        selectinload(Book.copies).selectinload(BookCopy.registered_location),
    ])
    if book is None:
        raise _not_found("도서", book_id)
    return book


def update_book(db: Session, book_id: int, data: BookUpdate) -> Book:
    book = get_book(db, book_id)
    for field, value in data.model_dump().items():
        setattr(book, field, value)
    db.commit()
    return book


# ---------- 복본 ----------

def get_copy(db: Session, copy_id: str) -> BookCopy:
    copy = db.get(BookCopy, copy_id, options=[selectinload(BookCopy.book), selectinload(BookCopy.registered_location)])
    if copy is None:
        raise _not_found("복본", copy_id)
    return copy


def create_copy(db: Session, book_id: int, data: CopyCreate, admin_id: str) -> BookCopy:
    get_book(db, book_id)
    if db.get(BookCopy, data.id):
        raise AppError(ErrorCode.ALREADY_EXISTS, f"복본 '{data.id}'가 이미 있습니다.", status_code=409)

    copy = BookCopy(id=data.id, book_id=book_id, loan_status=data.loan_status,
                    call_number=data.call_number, room=data.room)
    if data.slot_id:
        _get_slot(db, data.slot_id)
        copy.registered_location = BookLocation(slot_id=data.slot_id)
        # 등록 위치에 꽂혀 있을 것으로 '추정'만 한다. 로봇이 확인하기 전에는 CONFIRMED가 아니다.
        copy.physical_location_type = PhysicalLocationType.SHELF
        copy.physical_slot_id = data.slot_id
        copy.physical_location_certainty = LocationCertainty.ESTIMATED
        db.add(BookLocationHistory(book_copy_id=data.id, from_slot_id=None, to_slot_id=data.slot_id,
                                   changed_by=admin_id, reason="복본 최초 등록"))
    db.add(copy)
    _commit_or_conflict(db, f"복본 '{data.id}'가 이미 있습니다.")
    return get_copy(db, data.id)


def update_loan_status(db: Session, copy_id: str, data: LoanStatusUpdate) -> BookCopy:
    copy = get_copy(db, copy_id)
    copy.loan_status = data.loan_status
    db.commit()
    return copy


def change_registered_location(db: Session, copy_id: str, data: RegisteredLocationUpdate, admin_id: str) -> BookCopy:
    """등록 위치(원래 있어야 할 칸) 변경. 관리자만 할 수 있고 이력이 남는다.

    현재 물리 위치는 바꾸지 않는다. 책이 실제로 옮겨졌는지는 로봇 관측으로 확인한다.
    """
    copy = get_copy(db, copy_id)
    _get_slot(db, data.slot_id)

    old_slot_id = copy.registered_location.slot_id if copy.registered_location else None
    if old_slot_id == data.slot_id:
        raise AppError(ErrorCode.INVALID_STATE, f"이미 등록 위치가 '{data.slot_id}'입니다.", status_code=409)

    if copy.registered_location:
        copy.registered_location.slot_id = data.slot_id
        # 관리자가 직접 정한 위치는 이후 범위표를 다시 적용해도 덮어쓰지 않는다
        copy.registered_location.source = RegisteredLocationSource.MANUAL
    else:
        copy.registered_location = BookLocation(slot_id=data.slot_id)

    db.add(BookLocationHistory(book_copy_id=copy_id, from_slot_id=old_slot_id, to_slot_id=data.slot_id,
                               changed_by=admin_id, reason=data.reason))
    db.add(AdminAction(admin_id=admin_id, action_type=AdminActionType.CHANGE_REGISTERED_LOCATION,
                       book_copy_id=copy_id, comment=f"{old_slot_id} -> {data.slot_id}: {data.reason}"))
    db.commit()
    return copy


def get_location_history(db: Session, copy_id: str) -> list[BookLocationHistory]:
    get_copy(db, copy_id)
    return list(db.scalars(
        select(BookLocationHistory)
        .where(BookLocationHistory.book_copy_id == copy_id)
        .order_by(BookLocationHistory.changed_at, BookLocationHistory.id)
    ))


def list_copies(db: Session, filters: dict, shelf_id: str | None, q: str | None,
                limit: int, offset: int) -> tuple[list[BookCopy], int]:
    """복본 목록. 등록 위치의 서가(shelf_id), 제목·복본 ID 검색(q), 대출 상태·물리 위치 필터."""
    stmt = select(BookCopy).join(Book, Book.id == BookCopy.book_id)
    for field, value in filters.items():
        if value is not None:
            stmt = stmt.where(getattr(BookCopy, field) == value)
    if shelf_id:
        stmt = (stmt.join(BookLocation, BookLocation.book_copy_id == BookCopy.id)
                .join(ShelfSlot, ShelfSlot.id == BookLocation.slot_id).where(ShelfSlot.shelf_id == shelf_id))
    if q:
        stmt = stmt.where(or_(Book.title.like(f"%{q}%"), BookCopy.id.like(f"%{q}%")))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(
        stmt.options(selectinload(BookCopy.book), selectinload(BookCopy.registered_location))
        .order_by(BookCopy.id).limit(limit).offset(offset)
    ))
    return items, total
