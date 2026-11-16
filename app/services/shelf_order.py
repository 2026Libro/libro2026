"""같은 칸 안 청구기호 순서 검사.

서가 전체의 칸별 청구기호 범위표가 없어도, 한 사진 안에서 왼쪽→오른쪽 책들의 청구기호가
순서대로인지만 보면 잘못 꽂힌 책을 찾을 수 있다.

- 사진(image_ref)마다 따로 검사한다. order는 사진 안 순번이라 사진끼리는 이어지지 않는다.
- 재관측으로 대체된 관측은 빼고, 최신 관측만 쓴다.
- 비교 기준 청구기호: 식별된 복본의 청구기호. 복본은 몰라도 후보가 모두 같은 책(복본 기호만 다름)이면
  복본 기호를 뗀 청구기호로 비교한다. 그 밖에는 UNIDENTIFIED로 두고 검사에서 뺀다.
- 결과는 저장하지 않고 요청할 때마다 계산한다. [가정] 오배치 판정·관리자 알림과 연결할지는 팀과 협의.
"""
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased, selectinload

from app.core.call_number import out_of_order
from app.core.enums import ShelfOrderStatus
from app.models import Book, BookCopy, Observation, VerificationResult
from app.schemas.verification import SlotOrderBook, SlotOrderCheck, SlotOrderImage
from app.services.library_import import base_call_number
from app.services.scan_session import get_session


def _latest_observations(db: Session, session_id: str, slot_id: str) -> list[Observation]:
    newer = aliased(Observation)
    return list(db.scalars(
        select(Observation)
        .where(Observation.scan_session_id == session_id, Observation.slot_id == slot_id,
               Observation.image_ref.is_not(None), Observation.order.is_not(None),
               ~select(newer.id).where(newer.reobservation_of == Observation.id).exists())
        .options(selectinload(Observation.candidates))
    ))


def _reference(obs: Observation, identified: str | None, copies: dict[str, BookCopy]) -> tuple[str | None, str | None]:
    """(비교할 청구기호, 제목)"""
    if identified and (copy := copies.get(identified)) and copy.call_number:
        return copy.call_number, copy.book.title
    candidates = [copies[c.book_copy_id] for c in obs.candidates if c.book_copy_id in copies]
    bases = {base_call_number(c.call_number) for c in candidates if c.call_number}
    if candidates and len(bases) == 1 and all(c.call_number for c in candidates):
        return bases.pop(), candidates[0].book.title
    return None, None


def check_slot_order(db: Session, session_id: str, slot_id: str) -> SlotOrderCheck:
    get_session(db, session_id)
    observations = _latest_observations(db, session_id, slot_id)

    identified = dict(db.execute(
        select(VerificationResult.observation_id, VerificationResult.book_copy_id)
        .where(VerificationResult.observation_id.in_([o.id for o in observations]))).all())
    copy_ids = {c for c in identified.values() if c} | {c.book_copy_id for o in observations for c in o.candidates}
    copies = {c.id: c for c in db.scalars(
        select(BookCopy).where(BookCopy.id.in_(copy_ids)).options(selectinload(BookCopy.book).load_only(Book.title)))}

    by_image: dict[str, list[Observation]] = defaultdict(list)
    for obs in observations:
        by_image[obs.image_ref].append(obs)

    images = []
    for image_ref, group in sorted(by_image.items(), key=lambda kv: min(o.captured_at for o in kv[1])):
        group.sort(key=lambda o: (o.order, o.id))
        books = []
        for obs in group:
            call_number, title = _reference(obs, identified.get(obs.id), copies)
            books.append(SlotOrderBook(order=obs.order, observation_id=obs.id, book_copy_id=identified.get(obs.id),
                                       call_number=call_number, title=title, bbox=obs.bbox,
                                       status=ShelfOrderStatus.IN_ORDER if call_number else
                                       ShelfOrderStatus.UNIDENTIFIED))
        known = [b for b in books if b.call_number]
        for i in out_of_order([b.call_number for b in known]):
            known[i].status = ShelfOrderStatus.OUT_OF_ORDER
        first = group[0]
        images.append(SlotOrderImage(
            image_ref=image_ref, image_url=f"/observations/{first.id}/image",
            image_width=first.image_width, image_height=first.image_height,
            out_of_order_count=sum(b.status == ShelfOrderStatus.OUT_OF_ORDER for b in books), books=books))

    return SlotOrderCheck(scan_session_id=session_id, slot_id=slot_id,
                          out_of_order_count=sum(i.out_of_order_count for i in images), images=images)
