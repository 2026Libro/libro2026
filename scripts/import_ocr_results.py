"""도서관에서 찍은 사진의 OCR 결과 파일 → DB 매칭 → 청구기호 흐름 검사 → 엉뚱한 책 보고서.

실행 (프로젝트 폴더에서):
    .venv\\Scripts\\python -m scripts.import_ocr_results ocr.csv
    .venv\\Scripts\\python -m scripts.import_ocr_results ocr.json --shelf P-01 --report report.csv
    .venv\\Scripts\\python -m scripts.import_ocr_results ocr.csv --separate     # 사진끼리 이어지지 않을 때

입력: CSV 또는 JSON(객체 배열). 책 1권 = 1행. 열 이름은 아래 중 아무거나 (대소문자 무시):
    사진   : image, image_name, file, 사진, 이미지                (필수)
    순서   : order, 순서, 순번                                     (없으면 x 좌표로 왼쪽부터 매김)
    청구기호: call_number, call_number_text, 청구기호, ocr          (청구기호나 서명 중 하나는 필수)
    서명   : title, title_text, 서명, 제목
    신뢰도 : confidence, ocr_confidence, 신뢰도                      (없으면 0.9)
    좌표   : bbox("[x,y,w,h]") 또는 x, y, w(width), h(height)
    칸     : slot, slot_id, 칸                                     (없으면 사진 이름 순으로 칸 01, 02 …)

- 기본: 사진을 이름 순으로 이어 붙여 서가 한 줄로 본다 (사진들이 서가 순서대로 찍힌 경우).
- --separate: 사진마다 따로 검사한다 (사진끼리 이어지지 않는 경우).
- 결과는 개발 DB에 스캔 세션으로 저장되어 API(/scan-sessions/{id}/order-check 등)로도 볼 수 있다.
"""
import argparse
import csv
import json
import re
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import get_settings
from app.core.enums import ScanSessionStatus
from app.db.session import SessionLocal
from app.models import Observation, Robot, Shelf, ShelfSlot
from app.schemas.observation import ObservationCreate
from app.schemas.scan_session import ScanSessionCreate, ScanSessionFinish
from app.services import observation as observation_service
from app.services import scan_session as scan_service
from app.services.shelf_order import check_shelf_order
from app.services.verification import Thresholds, check_unobserved

ROBOT = "R001"
KST = timezone(timedelta(hours=9))
ALIASES = {
    "image": ("image", "image_name", "file", "filename", "사진", "이미지"),
    "order": ("order", "순서", "순번"),
    "call_number": ("call_number", "call_number_text", "callnumber", "청구기호", "ocr", "text"),
    "title": ("title", "title_text", "서명", "제목"),
    "confidence": ("confidence", "ocr_confidence", "conf", "신뢰도"),
    "bbox": ("bbox", "box"),
    "x": ("x",), "y": ("y",), "w": ("w", "width"), "h": ("h", "height"),
    "slot": ("slot", "slot_id", "칸"),
}


def _pick(row: dict, key: str):
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for name in ALIASES[key]:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def read_rows(path: Path) -> list[dict]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data["items"] if isinstance(data, dict) and "items" in data else data
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _bbox(row: dict) -> list[int] | None:
    if (raw := _pick(row, "bbox")) is not None:
        nums = raw if isinstance(raw, list) else [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", str(raw))]
        return [int(round(float(n))) for n in nums[:4]] if len(nums) >= 4 else None
    parts = [_pick(row, k) for k in ("x", "y", "w", "h")]
    return [int(round(float(p))) for p in parts] if all(p is not None for p in parts) else None


def parse(rows: list[dict]) -> dict[str, list[dict]]:
    """사진별로 묶고 왼쪽부터 순서를 정한다."""
    images: dict[str, list[dict]] = {}
    for i, row in enumerate(rows):
        image = _pick(row, "image")
        call, title = _pick(row, "call_number"), _pick(row, "title")
        if not image or not (call or title):
            print(f"  건너뜀 {i + 1}행: 사진 이름 또는 청구기호·서명이 없음")
            continue
        bbox = _bbox(row)
        order = _pick(row, "order")
        images.setdefault(str(image), []).append({
            "order": int(float(order)) if order is not None else None, "bbox": bbox,
            "x": bbox[0] if bbox else None, "call": str(call) if call else None, "title": str(title) if title else None,
            "conf": float(_pick(row, "confidence") or 0.9), "slot": _pick(row, "slot"), "row": i})
    for books in images.values():
        if any(b["order"] is None for b in books):
            if any(b["x"] is None for b in books):
                books.sort(key=lambda b: b["row"])       # 순서도 좌표도 없으면 파일에 적힌 순서
            else:
                books.sort(key=lambda b: b["x"])
            for n, b in enumerate(books, start=1):
                b["order"] = n
        books.sort(key=lambda b: b["order"])
    return dict(sorted(images.items()))


def _ensure_shelf(db, shelf_id: str, slot_count: int) -> None:
    if db.get(Robot, ROBOT) is None:
        db.add(Robot(id=ROBOT, name="OCR 일괄 처리"))
    if db.get(Shelf, shelf_id) is None:
        db.add(Shelf(id=shelf_id, zone=shelf_id.split("-")[0], name="도서관 사진 OCR"))
    have = {s.position for s in db.query(ShelfSlot).filter(ShelfSlot.shelf_id == shelf_id)}
    for pos in range(1, slot_count + 1):
        if pos not in have:
            db.add(ShelfSlot(id=f"{shelf_id}-{pos:02d}", shelf_id=shelf_id, position=pos))
    db.commit()


def run_session(db, shelf_id: str, images: dict[str, list[dict]], th: Thresholds, started: datetime) -> str:
    slots = {}
    for n, (image, books) in enumerate(images.items(), start=1):
        given = books[0]["slot"]
        slots[image] = str(given) if given else f"{shelf_id}-{n:02d}"
    _ensure_shelf(db, shelf_id, max(int(s.rsplit("-", 1)[1]) for s in slots.values()))
    session = scan_service.create_session(db, ScanSessionCreate(robot_id=ROBOT, shelf_id=shelf_id))
    count = 0
    for n, (image, books) in enumerate(images.items()):
        for b in books:
            count += 1
            data = ObservationCreate(
                observation_id=f"OBS-OCR-{session.id[-13:]}-{count:04d}", scan_session_id=session.id,
                robot_id=ROBOT, shelf_id=shelf_id, slot_id=slots[image],
                detected_text=" / ".join(t for t in (b["call"], b["title"]) if t)[:500],
                call_number_text=b["call"], title_text=b["title"],
                confidence={"detection": 0.95, "ocr": min(max(b["conf"], 0.0), 1.0)},
                image_ref=image, order=b["order"], bbox=b["bbox"],
                captured_at=started + timedelta(seconds=n * 60 + b["order"]))
            observation_service.receive_observation(db, data, th)
    scan_service.finish_session(db, session.id, ScanSessionFinish(status=ScanSessionStatus.COMPLETED), robot_id=ROBOT)
    check_unobserved(db, session.id)          # 흐름 검사 기록 + 엉뚱한 책 알림
    return session.id


def report(db, session_ids: list[str], out: Path) -> None:
    methods = dict(db.query(Observation.id, Observation.match_method)
                   .filter(Observation.scan_session_id.in_(session_ids)).all())
    texts = {oid: " ".join((text or "").split()) for oid, text in db.query(Observation.id, Observation.detected_text)
             .filter(Observation.scan_session_id.in_(session_ids)).all()}
    lines, totals = [], {"OUT_OF_PLACE": 0, "OUT_OF_ORDER": 0, "IN_ORDER": 0, "UNIDENTIFIED": 0}
    for sid in session_ids:
        for slot in check_shelf_order(db, sid).slots:
            for image in slot.images:
                print(f"\n[{image.image_ref}]  (세션 {sid}, 칸 {slot.slot_id})")
                for b in image.books:
                    totals[b.status] += 1
                    mark = {"OUT_OF_PLACE": "  ← 엉뚱한 책", "OUT_OF_ORDER": "  ← 순서 오류"}.get(b.status, "")
                    print(f"  {b.order:>3}. {texts[b.observation_id][:30]:<32} → {b.call_number or '(못 찾음)':<26} "
                          f"{methods[b.observation_id] or '':<9} {b.status}{mark}")
                    lines.append([image.image_ref, b.order, texts[b.observation_id], b.call_number or "",
                                  b.book_copy_id or "", b.title or "", methods[b.observation_id] or "", b.status,
                                  " / ".join(b.neighbors), sid])
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["사진", "순서", "OCR 글자", "매칭 청구기호", "등록번호", "서명", "매칭 방법", "판정", "앞뒤 책", "세션"])
        w.writerows(lines)
    print(f"\n엉뚱한 책 {totals['OUT_OF_PLACE']} / 순서 오류 {totals['OUT_OF_ORDER']} / 정상 {totals['IN_ORDER']} "
          f"/ 못 찾음 {totals['UNIDENTIFIED']}  → 보고서 {out}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path)
    parser.add_argument("--shelf", default="P-01", help="저장할 서가 ID (기본 P-01)")
    parser.add_argument("--separate", action="store_true", help="사진마다 따로 검사")
    parser.add_argument("--report", type=Path, help="보고서 CSV (기본: <입력 파일>_report.csv)")
    args = parser.parse_args()

    images = parse(read_rows(args.file))
    if not images:
        print("읽을 수 있는 행이 없습니다.")
        return 1
    print(f"DB: {get_settings().db_name} / 사진 {len(images)}장, 책 {sum(map(len, images.values()))}권")
    # 일괄 처리에서는 로봇 작업(재관측·인출 요청)을 만들지 않는다
    th = replace(Thresholds.from_settings(get_settings()), auto_reobservation=False, auto_retrieval_request=False)
    started = datetime.now(KST)
    with SessionLocal() as db:
        if args.separate:
            zone = args.shelf.split("-")[0]
            session_ids = [run_session(db, f"{zone}-{n:02d}", {image: books}, th, started)
                           for n, (image, books) in enumerate(images.items(), start=1)]
        else:
            session_ids = [run_session(db, args.shelf, images, th, started)]
        report(db, session_ids, args.report or args.file.with_name(args.file.stem + "_report.csv"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
