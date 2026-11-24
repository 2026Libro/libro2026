"""현장 조사한 칸별 청구기호 범위표(CSV)를 DB에 저장하고, 원하면 바로 적용한다.

CSV 열 (첫 줄은 제목, docs/call_range_template.csv 참고):
    칸ID, 자료실, 시작 청구기호, 끝 청구기호, 비고

실행 (프로젝트 폴더에서):
    .venv\\Scripts\\python -m scripts.import_call_ranges ranges.csv --dry-run                # 검사 + 적용 결과 미리보기
    .venv\\Scripts\\python -m scripts.import_call_ranges ranges.csv --create-slots --apply   # 저장 + 등록 위치 계산

--create-slots : CSV에 나온 서가·칸이 DB에 없으면 만든다 (A-01-05까지 있으면 A-01 서가에 칸 5개).
--apply        : 저장 뒤 범위표대로 등록 위치를 다시 계산한다 (관리자가 직접 지정한 위치는 그대로).
--dry-run      : 아무것도 저장하지 않고, 저장·적용했을 때의 결과만 보여 준다.
"""
import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import SessionLocal
from app.models import Shelf, ShelfSlot
from app.schemas.call_range import CallRangeUpsert
from app.services import call_range as range_service

ADMIN = "range-import"
COLUMNS = {"slot_id": ("칸ID", "slot_id"), "room": ("자료실", "room"),
           "start": ("시작 청구기호", "start_call_number"), "end": ("끝 청구기호", "end_call_number"),
           "note": ("비고", "note")}


def _get(row: dict, key: str) -> str:
    for name in COLUMNS[key]:
        if row.get(name):
            return row[name].strip()
    return ""


def read_ranges(path: Path) -> list[tuple[str, CallRangeUpsert]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if any((v or "").strip() for v in r.values())]
    return [(_get(r, "slot_id"), CallRangeUpsert(room=_get(r, "room"), start_call_number=_get(r, "start"),
                                                end_call_number=_get(r, "end"), note=_get(r, "note") or None))
            for r in rows]


def create_missing_slots(db, slot_ids: list[str]) -> list[str]:
    wanted: dict[str, int] = defaultdict(int)
    for slot_id in slot_ids:
        shelf_id, _, pos = slot_id.rpartition("-")
        wanted[shelf_id] = max(wanted[shelf_id], int(pos))
    created = []
    for shelf_id, last in sorted(wanted.items()):
        if db.get(Shelf, shelf_id) is None:
            db.add(Shelf(id=shelf_id, zone=shelf_id.split("-")[0]))
        have = set(db.scalars(select(ShelfSlot.position).where(ShelfSlot.shelf_id == shelf_id)))
        for pos in range(1, last + 1):
            if pos not in have:
                db.add(ShelfSlot(id=f"{shelf_id}-{pos:02d}", shelf_id=shelf_id, position=pos))
                created.append(f"{shelf_id}-{pos:02d}")
    db.flush()
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--create-slots", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    items = read_ranges(args.csv)
    print(f"DB: {get_settings().db_name} / 범위 {len(items)}개 ({args.csv})")
    with SessionLocal() as db:
        try:
            if args.create_slots:
                created = create_missing_slots(db, [s for s, _ in items])
                print(f"새로 만든 칸: {len(created)}개 {created[:10]}{' …' if len(created) > 10 else ''}")
            if args.dry_run:
                db.commit = db.flush          # 미리보기: 서비스의 commit을 flush로 바꿔 마지막에 되돌린다
            range_service.save_ranges(db, items, ADMIN)
            print("범위 저장: 문제 없음" if args.dry_run else "범위 저장 완료")
            if args.apply or args.dry_run:
                result = range_service.apply_ranges(db, ADMIN, dry_run=args.dry_run)
                print("적용 결과:", result.model_dump(exclude={"dry_run"}))
        except AppError as e:
            db.rollback()
            print(f"실패: {e.message}")
            for d in e.details or []:
                print(f"  {d}")
            return 1
        if args.dry_run:
            db.rollback()
            print("[미리보기] 저장하지 않았습니다")
        else:
            db.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
