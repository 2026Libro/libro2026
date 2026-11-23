"""칸별 청구기호 범위표와 등록 위치 계산.

- 범위표: 칸마다 [시작, 끝] 청구기호. 같은 자료실 안에서 범위가 겹치면 안 된다.
- 적용(apply): 해당 자료실 복본마다 청구기호가 드는 칸을 찾아 등록 위치(book_locations)를 정한다.
  · 관리자가 직접 지정한 위치(source=MANUAL)는 덮어쓰지 않는다.
  · 범위표로 정했던 위치가 이제 어느 범위에도 없으면 등록 위치를 지운다.
  · 위치가 바뀌면 book_location_history에 남긴다.
  · 로봇이 확인한 적 없는 복본은 현재 물리 위치도 그 칸으로 '추정'해 둔다. 확인된(CONFIRMED) 위치는 그대로.
- 범위표를 저장해도 자동 적용하지 않는다. 여러 칸을 고친 뒤 한 번에 적용한다.
"""
from bisect import bisect_right
from dataclasses import dataclass

from sqlalchemy import bindparam, delete, insert, select, update
from sqlalchemy.orm import Session

from app.core.call_number import sort_key, upper_key
from app.core.enums import LocationCertainty, PhysicalLocationType, RegisteredLocationSource
from app.core.errors import AppError
from app.db.base import utcnow
from app.models import BookCopy, BookLocation, BookLocationHistory, ShelfSlot, SlotCallRange, SlotCallRangeHistory
from app.schemas.call_range import ApplyResult, CallRangeUpsert, LocateResult
from app.schemas.common import ErrorCode
from app.services.library_import import CAMPUS_PREFIX

APPLY_REASON = "청구기호 범위표 적용"


def normalize_room(room: str) -> str:
    """'대출실' → '고양캠퍼스 대출실' (복본의 room 값과 맞춘다)."""
    room = room.strip()
    return room if room.startswith(CAMPUS_PREFIX) else CAMPUS_PREFIX + room


def _invalid(message: str, details: list | None = None) -> AppError:
    return AppError(ErrorCode.VALIDATION_ERROR, message, status_code=422, details=details)


@dataclass(frozen=True)
class _Span:
    slot_id: str
    start: tuple
    end: tuple


def _span(slot_id: str, start: str, end: str) -> _Span:
    span = _Span(slot_id, sort_key(start), upper_key(end))
    if span.start > span.end:
        raise _invalid(f"{slot_id}: 시작 청구기호 '{start}'가 끝 '{end}'보다 뒤입니다.")
    return span


def _check_overlaps(spans: list[_Span]) -> None:
    ordered = sorted(spans, key=lambda s: s.start)
    clashes = [f"{a.slot_id} ↔ {b.slot_id}" for a, b in zip(ordered, ordered[1:]) if b.start <= a.end]
    if clashes:
        raise _invalid("같은 자료실 안에서 칸 범위가 겹칩니다.",
                       details=[{"field": "ranges", "message": c} for c in clashes])


# ---------- 범위표 저장·조회 ----------

def list_ranges(db: Session, room: str | None = None, shelf_id: str | None = None) -> list[SlotCallRange]:
    stmt = select(SlotCallRange)
    if room:
        stmt = stmt.where(SlotCallRange.room == normalize_room(room))
    if shelf_id:
        stmt = stmt.join(ShelfSlot, ShelfSlot.id == SlotCallRange.slot_id).where(ShelfSlot.shelf_id == shelf_id)
    return sorted(db.scalars(stmt), key=lambda r: (r.room, sort_key(r.start_call_number)))


def save_ranges(db: Session, items: list[tuple[str, CallRangeUpsert]], admin_id: str) -> list[SlotCallRange]:
    """여러 칸 범위를 저장한다. 하나라도 문제가 있으면 아무것도 저장하지 않는다. commit은 여기서 한다."""
    slot_ids = [slot_id for slot_id, _ in items]
    if len(slot_ids) != len(set(slot_ids)):
        raise _invalid("같은 칸이 두 번 들어 있습니다.")
    missing = sorted(set(slot_ids) - set(db.scalars(select(ShelfSlot.id).where(ShelfSlot.id.in_(slot_ids)))))
    if missing:
        raise AppError(ErrorCode.NOT_FOUND, f"없는 칸입니다: {', '.join(missing[:10])}", status_code=404)

    existing = {r.slot_id: r for r in db.scalars(select(SlotCallRange))}
    after: dict[str, tuple[str, _Span]] = {
        r.slot_id: (r.room, _span(r.slot_id, r.start_call_number, r.end_call_number)) for r in existing.values()}
    for slot_id, data in items:
        after[slot_id] = (normalize_room(data.room), _span(slot_id, data.start_call_number, data.end_call_number))
    for room in {room for room, _ in after.values()}:
        _check_overlaps([span for r, span in after.values() if r == room])

    saved = []
    for slot_id, data in items:
        room = normalize_room(data.room)
        row = existing.get(slot_id)
        old = (row.start_call_number, row.end_call_number, row.room) if row else (None, None, None)
        new = (data.start_call_number.strip(), data.end_call_number.strip(), room)
        if row is None:
            row = SlotCallRange(slot_id=slot_id)
            db.add(row)
        elif old == new and row.note == data.note:
            saved.append(row)
            continue
        row.room, row.start_call_number, row.end_call_number = room, new[0], new[1]
        row.note, row.updated_by = data.note, admin_id
        if old != new:
            db.add(SlotCallRangeHistory(slot_id=slot_id, room=room, old_start=old[0], old_end=old[1],
                                        new_start=new[0], new_end=new[1], changed_by=admin_id))
        saved.append(row)
    db.commit()
    return saved


def delete_range(db: Session, slot_id: str, admin_id: str) -> None:
    row = db.get(SlotCallRange, slot_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, f"칸 '{slot_id}'에는 범위가 없습니다.", status_code=404)
    db.add(SlotCallRangeHistory(slot_id=slot_id, room=row.room, old_start=row.start_call_number,
                                old_end=row.end_call_number, changed_by=admin_id))
    db.delete(row)
    db.commit()


def range_history(db: Session, slot_id: str | None, limit: int) -> list[SlotCallRangeHistory]:
    stmt = select(SlotCallRangeHistory)
    if slot_id:
        stmt = stmt.where(SlotCallRangeHistory.slot_id == slot_id)
    return list(db.scalars(stmt.order_by(SlotCallRangeHistory.id.desc()).limit(limit)))


# ---------- 칸 찾기 ----------

class _Index:
    """자료실별로 시작 청구기호 순 정렬해 두고 이진 탐색한다."""

    def __init__(self, ranges: list[SlotCallRange]):
        by_room: dict[str, list[_Span]] = {}
        for r in ranges:
            by_room.setdefault(r.room, []).append(_span(r.slot_id, r.start_call_number, r.end_call_number))
        self.spans = {room: sorted(spans, key=lambda s: s.start) for room, spans in by_room.items()}
        self.starts = {room: [s.start for s in spans] for room, spans in self.spans.items()}

    def find(self, room: str | None, call_number: str) -> str | None:
        spans = self.spans.get(room or "")
        if not spans:
            return None
        key = sort_key(call_number)
        i = bisect_right(self.starts[room], key) - 1
        return spans[i].slot_id if i >= 0 and key <= spans[i].end else None


def locate(db: Session, call_number: str, room: str) -> LocateResult:
    room = normalize_room(room)
    index = _Index(list(db.scalars(select(SlotCallRange).where(SlotCallRange.room == room))))
    return LocateResult(call_number=call_number, room=room, slot_id=index.find(room, call_number))


# ---------- 적용 ----------

def apply_ranges(db: Session, admin_id: str, dry_run: bool = False) -> ApplyResult:
    """범위표대로 등록 위치를 다시 계산한다. 범위표가 있는 자료실의 복본만 대상이다."""
    ranges = list(db.scalars(select(SlotCallRange)))
    index = _Index(ranges)
    rooms = list(index.spans)
    counts = dict(assigned=0, moved=0, unchanged=0, removed=0, kept_manual=0, uncovered=0, no_call_number=0)
    if not rooms:
        return ApplyResult(dry_run=dry_run, **counts)

    rows = db.execute(
        select(BookCopy.id, BookCopy.call_number, BookCopy.room, BookCopy.physical_location_type,
               BookCopy.physical_location_certainty, BookCopy.physical_slot_id,
               BookLocation.slot_id, BookLocation.source)
        .outerjoin(BookLocation, BookLocation.book_copy_id == BookCopy.id)
        .where(BookCopy.room.in_(rooms))
    ).all()

    new_locations, moved, removed, history, physical = [], [], [], [], []
    now = utcnow()
    for (copy_id, call_number, room, ptype, pcert, pslot, current, source) in rows:
        if current and source == RegisteredLocationSource.MANUAL:
            counts["kept_manual"] += 1
            continue
        if not call_number:
            counts["no_call_number"] += 1
            continue
        target = index.find(room, call_number)
        if target == current:
            counts["unchanged" if target else "uncovered"] += 1
            continue
        if target is None:                       # 범위표로 정했던 칸이 사라짐
            counts["removed"] += 1
            removed.append(copy_id)
        elif current is None:
            counts["assigned"] += 1
            new_locations.append({"book_copy_id": copy_id, "slot_id": target,
                                  "source": RegisteredLocationSource.CALL_RANGE, "created_at": now, "updated_at": now})
        else:
            counts["moved"] += 1
            moved.append({"cid": copy_id, "slot": target})
        history.append({"book_copy_id": copy_id, "from_slot_id": current, "to_slot_id": target,
                        "changed_by": admin_id, "reason": APPLY_REASON, "changed_at": now})
        # 로봇이 확인하지 않은 위치만 새 칸으로 추정한다
        estimated = ptype == PhysicalLocationType.UNKNOWN or (
            ptype == PhysicalLocationType.SHELF and pcert == LocationCertainty.ESTIMATED and pslot == current)
        if estimated:
            physical.append({"cid": copy_id, "ptype": PhysicalLocationType.SHELF if target else
                             PhysicalLocationType.UNKNOWN, "pslot": target})

    if not dry_run:
        # 7만 권 단위라 ORM 객체 대신 테이블에 직접 일괄 실행한다
        locations, copies = BookLocation.__table__, BookCopy.__table__
        if new_locations:
            db.execute(insert(BookLocation), new_locations)
        if moved:
            db.execute(update(locations).where(locations.c.book_copy_id == bindparam("cid"))
                       .values(slot_id=bindparam("slot"), source=RegisteredLocationSource.CALL_RANGE,
                               updated_at=now), moved)
        if removed:
            db.execute(delete(locations).where(locations.c.book_copy_id.in_(removed)))
        if history:
            db.execute(insert(BookLocationHistory), history)
        if physical:
            db.execute(update(copies).where(copies.c.id == bindparam("cid"))
                       .values(physical_location_type=bindparam("ptype"), physical_slot_id=bindparam("pslot"),
                               physical_location_certainty=LocationCertainty.ESTIMATED, updated_at=now), physical)
        db.commit()
        db.expire_all()   # 직접 실행한 변경을 세션의 객체에도 반영
    return ApplyResult(dry_run=dry_run, **counts)
