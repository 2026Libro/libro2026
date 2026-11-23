"""서가 청구기호 흐름 검사 — '엉뚱한 책'과 '순서 오류' 찾기.

정확한 칸 위치(범위표)가 없어도, 도서관은 청구기호 순으로 책을 꽂으므로 흐름에서 튀는 책을 찾을 수 있다.

1. 서가 한 줄 만들기: 칸 순서(A-01-01 → 02 …) → 칸 안 사진 순서(촬영 시각) → 사진 안 왼쪽부터 순번(order).
   [가정] 칸 번호 순서 = 책이 이어지는 순서. 한 칸에 사진이 여러 장이면 촬영 순서대로 왼쪽→오른쪽.
2. 가장 길게 순서가 맞는 책들(최장 비감소 부분수열)을 '제자리'로 본다. 나머지가 이탈.
3. 이탈한 책 중 제자리인 앞·뒤 책과 분류번호 앞 3자리(KDC 강목)가 모두 다르면 OUT_OF_PLACE(엉뚱한 책),
   같은 분류 근처면 OUT_OF_ORDER(순서만 어긋남).
4. 비교 청구기호: 식별된 복본의 청구기호. 복본 미확정이면 1등 후보 책의 청구기호(c.N 뗀 값)를 쓰되,
   다른 책 후보와 점수 차가 CANDIDATE_MARGIN 미만이면 UNIDENTIFIED로 두고 흐름 계산에서 뺀다.
5. 재관측으로 대체된 관측은 빼고 최신 관측만 쓴다. image_ref·order가 없는 관측은 위치를 몰라 제외.

조회 API는 요청 때마다 계산한다. AI 분석 완료 시점에는 결과를 검증 결과(sequence_status)에 기록하고,
엉뚱한 책은 관리자 검토(REVIEW_REQUIRED)로 넘겨 알림(OUT_OF_PLACE)이 생긴다.
"""
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased, selectinload

from app.core.call_number import class_division, out_of_order
from app.core.enums import ShelfOrderStatus, WorkflowStatus
from app.models import Book, BookCopy, Observation, ShelfSlot, VerificationResult
from app.schemas.verification import ShelfOrderCheck, SlotOrderBook, SlotOrderCheck, SlotOrderImage
from app.services.library_import import base_call_number
from app.services.scan_session import get_session

OUT_OF_PLACE_DIGITS = 3   # [가정] 분류번호 앞 몇 자리가 다르면 '엉뚱한 책'으로 볼지 (3 = KDC 강목)
CANDIDATE_MARGIN = 0.05   # [가정] 후보가 여러 책일 때 1등이 다른 책보다 이만큼 높으면 1등 책으로 흐름 판단


@dataclass
class _Entry:
    obs: Observation
    book: SlotOrderBook
    neighbors: list[str] = field(default_factory=list)


def _latest_observations(db: Session, session_id: str) -> list[Observation]:
    newer = aliased(Observation)
    return list(db.scalars(
        select(Observation)
        .where(Observation.scan_session_id == session_id,
               Observation.image_ref.is_not(None), Observation.order.is_not(None),
               ~select(newer.id).where(newer.reobservation_of == Observation.id).exists())
        .options(selectinload(Observation.candidates))
    ))


def _reference(obs: Observation, identified: str | None, copies: dict[str, BookCopy]) -> tuple[str | None, str | None]:
    """(비교할 청구기호, 제목)"""
    if identified and (copy := copies.get(identified)) and copy.call_number:
        return copy.call_number, copy.book.title
    ranked = sorted((c for c in obs.candidates if c.book_copy_id in copies), key=lambda c: (c.rank, c.book_copy_id))
    if not ranked or not copies[ranked[0].book_copy_id].call_number:
        return None, None
    top = copies[ranked[0].book_copy_id]
    rival = next((c for c in ranked if copies[c.book_copy_id].book_id != top.book_id), None)
    # 후보가 모두 같은 책이거나, 1등 책이 다른 책보다 확실히 점수가 높으면 1등 책 기준으로 본다
    if rival is None or ranked[0].score - rival.score >= CANDIDATE_MARGIN:
        return base_call_number(top.call_number), top.book.title
    return None, None


def _evaluate(db: Session, session_id: str) -> list[_Entry]:
    """서가 한 줄 순서로 정렬한 관측과 판정."""
    observations = _latest_observations(db, session_id)
    if not observations:
        return []
    identified = dict(db.execute(
        select(VerificationResult.observation_id, VerificationResult.book_copy_id)
        .where(VerificationResult.observation_id.in_([o.id for o in observations]))).all())
    copy_ids = {c for c in identified.values() if c} | {c.book_copy_id for o in observations for c in o.candidates}
    copies = {c.id: c for c in db.scalars(
        select(BookCopy).where(BookCopy.id.in_(copy_ids)).options(selectinload(BookCopy.book).load_only(Book.title)))}
    position = dict(db.execute(select(ShelfSlot.id, ShelfSlot.position)
                               .where(ShelfSlot.id.in_({o.slot_id for o in observations}))).all())
    image_start: dict[str, object] = {}
    for o in observations:
        image_start[o.image_ref] = min(image_start.get(o.image_ref, o.captured_at), o.captured_at)

    observations.sort(key=lambda o: (position.get(o.slot_id, 0), o.slot_id, image_start[o.image_ref], o.image_ref,
                                     o.order, o.id))
    entries = []
    for obs in observations:
        call_number, title = _reference(obs, identified.get(obs.id), copies)
        entries.append(_Entry(obs, SlotOrderBook(
            order=obs.order, observation_id=obs.id, book_copy_id=identified.get(obs.id), call_number=call_number,
            title=title, bbox=obs.bbox,
            status=ShelfOrderStatus.IN_ORDER if call_number else ShelfOrderStatus.UNIDENTIFIED)))

    known = [e for e in entries if e.book.call_number]
    wrong = out_of_order([e.book.call_number for e in known])
    kept = [i for i in range(len(known)) if i not in wrong]
    for i in wrong:
        before = max((k for k in kept if k < i), default=None)
        after = min((k for k in kept if k > i), default=None)
        neighbors = [known[k].book.call_number for k in (before, after) if k is not None]
        mine = class_division(known[i].book.call_number, OUT_OF_PLACE_DIGITS)
        far = bool(neighbors) and all(class_division(n, OUT_OF_PLACE_DIGITS) != mine for n in neighbors)
        known[i].book.status = ShelfOrderStatus.OUT_OF_PLACE if far else ShelfOrderStatus.OUT_OF_ORDER
        known[i].book.neighbors = neighbors
    return entries


def _count(books, status) -> int:
    return sum(b.status is status for b in books)


def _slot_result(session_id: str, slot_id: str, entries: list[_Entry]) -> SlotOrderCheck:
    by_image: dict[str, list[_Entry]] = defaultdict(list)
    for e in entries:
        by_image[e.obs.image_ref].append(e)   # entries가 이미 서가 순서라 사진 순서도 유지된다
    images = []
    for image_ref, group in by_image.items():
        books = [e.book for e in group]
        first = group[0].obs
        images.append(SlotOrderImage(
            image_ref=image_ref, image_url=f"/observations/{first.id}/image", image_width=first.image_width,
            image_height=first.image_height, out_of_order_count=_count(books, ShelfOrderStatus.OUT_OF_ORDER),
            out_of_place_count=_count(books, ShelfOrderStatus.OUT_OF_PLACE), books=books))
    return SlotOrderCheck(
        scan_session_id=session_id, slot_id=slot_id,
        out_of_order_count=sum(i.out_of_order_count for i in images),
        out_of_place_count=sum(i.out_of_place_count for i in images), images=images)


def check_slot_order(db: Session, session_id: str, slot_id: str) -> SlotOrderCheck:
    """칸 하나의 결과. 판정은 서가 전체 흐름으로 하고, 이 칸 책만 보여 준다."""
    get_session(db, session_id)
    return _slot_result(session_id, slot_id, [e for e in _evaluate(db, session_id) if e.obs.slot_id == slot_id])


def check_shelf_order(db: Session, session_id: str) -> ShelfOrderCheck:
    session = get_session(db, session_id)
    by_slot: dict[str, list[_Entry]] = defaultdict(list)
    for e in _evaluate(db, session_id):
        by_slot[e.obs.slot_id].append(e)
    slots = [_slot_result(session_id, slot_id, group) for slot_id, group in by_slot.items()]
    return ShelfOrderCheck(
        scan_session_id=session_id, shelf_id=session.shelf_id,
        out_of_order_count=sum(s.out_of_order_count for s in slots),
        out_of_place_count=sum(s.out_of_place_count for s in slots), slots=slots)


def record_sequence(db: Session, session_id: str) -> int:
    """AI 분석 완료 시점: 검증 결과에 흐름 판정을 기록하고, 엉뚱한 책은 관리자 검토로 넘긴다.

    이미 다른 처리(재관측 대기·검토·승인)가 걸린 결과의 workflow는 바꾸지 않는다. 엉뚱한 책 수를 돌려준다.
    commit은 호출한 쪽이 한다.
    """
    entries = {e.obs.id: e for e in _evaluate(db, session_id)}
    if not entries:
        return 0
    flagged = 0
    for v in db.scalars(select(VerificationResult).where(VerificationResult.observation_id.in_(list(entries)))):
        book = entries[v.observation_id].book
        v.sequence_status = book.status
        if book.status is ShelfOrderStatus.OUT_OF_PLACE:
            flagged += 1
            note = f"청구기호 흐름 이탈: {book.call_number} (앞뒤 {' / '.join(book.neighbors)})"
            v.note = f"{v.note}; {note}" if v.note else note
            if v.workflow_status is None:
                v.workflow_status = WorkflowStatus.REVIEW_REQUIRED
    return flagged
