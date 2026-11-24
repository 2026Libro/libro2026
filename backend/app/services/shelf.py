"""서가·칸 관리 로직 (BE-003)."""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError
from app.models import Book, BookCopy, BookLocation, Shelf, ShelfSlot
from app.schemas.common import ErrorCode
from app.schemas.shelf import RegisteredCopy, ShelfCreate, ShelfDetail, SlotDetail


def _already_exists(shelf_id: str) -> AppError:
    return AppError(ErrorCode.ALREADY_EXISTS, f"서가 '{shelf_id}'가 이미 있습니다.", status_code=409)


def create_shelf(db: Session, data: ShelfCreate) -> Shelf:
    if db.get(Shelf, data.id):
        raise _already_exists(data.id)

    shelf = Shelf(id=data.id, zone=data.id.split("-")[0], name=data.name, description=data.description)
    shelf.slots = [ShelfSlot(id=f"{data.id}-{n:02d}", position=n) for n in range(1, data.slot_count + 1)]
    db.add(shelf)
    try:
        db.commit()
    except IntegrityError:  # 동시에 같은 서가를 만든 경우
        db.rollback()
        raise _already_exists(data.id)
    return shelf


def list_shelves(db: Session) -> list[Shelf]:
    return list(db.scalars(select(Shelf).options(selectinload(Shelf.slots)).order_by(Shelf.id)))


def get_shelf(db: Session, shelf_id: str) -> Shelf:
    shelf = db.get(Shelf, shelf_id, options=[selectinload(Shelf.slots)])
    if shelf is None:
        raise AppError(ErrorCode.NOT_FOUND, f"서가 '{shelf_id}'를 찾을 수 없습니다.", status_code=404)
    return shelf


def get_shelf_detail(db: Session, shelf_id: str) -> ShelfDetail:
    """서가의 칸별 등록 도서 목록 (등록 위치 기준이며, 실제 관측 결과와 다를 수 있다)."""
    shelf = get_shelf(db, shelf_id)
    rows = db.execute(
        select(BookLocation.slot_id, BookCopy.id, Book.id, Book.title, BookCopy.loan_status)
        .join(BookCopy, BookCopy.id == BookLocation.book_copy_id)
        .join(Book, Book.id == BookCopy.book_id)
        .join(ShelfSlot, ShelfSlot.id == BookLocation.slot_id)
        .where(ShelfSlot.shelf_id == shelf_id)
        .order_by(BookCopy.id)
    ).all()

    by_slot: dict[str, list[RegisteredCopy]] = {}
    for slot_id, copy_id, book_id, title, loan_status in rows:
        by_slot.setdefault(slot_id, []).append(
            RegisteredCopy(book_copy_id=copy_id, book_id=book_id, title=title, loan_status=loan_status)
        )

    return ShelfDetail(
        id=shelf.id,
        zone=shelf.zone,
        name=shelf.name,
        description=shelf.description,
        slots=[
            SlotDetail(id=s.id, position=s.position, registered_copies=by_slot.get(s.id, []))
            for s in shelf.slots
        ],
    )
