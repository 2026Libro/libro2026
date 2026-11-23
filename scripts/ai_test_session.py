"""AI 연동 테스트용 스캔 세션 도우미 (로봇 없이 AI만 붙여 볼 때).

실행 (프로젝트 폴더에서):
    .venv\\Scripts\\python -m scripts.ai_test_session start                 # 테스트 서가 L-01(칸 10개) + 세션 생성
    .venv\\Scripts\\python -m scripts.ai_test_session start --shelf L-02 --slots 5
    .venv\\Scripts\\python -m scripts.ai_test_session finish SCAN-…        # 로봇 대신 촬영 종료(COMPLETED) 보고
    .venv\\Scripts\\python -m scripts.ai_test_session report SCAN-…        # 받은 관측·흐름 검사 결과 요약

순서: start → (AI가 관측 전송) → finish → (AI가 analysis-complete 전송) → report
- 로봇 R001이 없으면 만든다. 서가가 없으면 칸 수만큼 만든다 (기존 서가는 그대로).
- finish는 로봇 API 키 없이 DB에서 직접 처리한다 (개발용).
"""
import argparse
import sys
from collections import Counter

from sqlalchemy import select

from app.core.config import get_settings
from app.core.enums import ScanSessionStatus
from app.db.session import SessionLocal
from app.models import Observation, Robot, Shelf, VerificationResult
from app.schemas.scan_session import ScanSessionCreate, ScanSessionFinish
from app.schemas.shelf import ShelfCreate
from app.services import scan_session as scan_service
from app.services import shelf as shelf_service
from app.services.shelf_order import check_shelf_order

ROBOT = "R001"


def start(shelf_id: str, slots: int) -> None:
    with SessionLocal() as db:
        if db.get(Robot, ROBOT) is None:
            db.add(Robot(id=ROBOT, name="테스트 로봇"))
            db.commit()
        if db.get(Shelf, shelf_id) is None:
            shelf_service.create_shelf(db, ShelfCreate(id=shelf_id, name="AI 연동 테스트 서가", slot_count=slots))
            print(f"서가 {shelf_id} 생성 (칸 {shelf_id}-01 ~ {shelf_id}-{slots:02d})")
        session = scan_service.create_session(db, ScanSessionCreate(robot_id=ROBOT, shelf_id=shelf_id))
    print(f"DB: {get_settings().db_name}")
    print(f"scan_session_id = {session.id}")
    print(f"robot_id = {ROBOT}, shelf_id = {shelf_id}, slot_id = {shelf_id}-01 ~")


def finish(session_id: str) -> None:
    with SessionLocal() as db:
        result = scan_service.finish_session(db, session_id, ScanSessionFinish(status=ScanSessionStatus.COMPLETED),
                                             robot_id=ROBOT)
    print(f"{session_id}: {result.status} — 이제 AI가 POST /scan-sessions/{session_id}/analysis-complete 를 보내면 됩니다")


def report(session_id: str) -> None:
    with SessionLocal() as db:
        observations = list(db.scalars(select(Observation).where(Observation.scan_session_id == session_id)))
        results = list(db.scalars(select(VerificationResult).where(VerificationResult.scan_session_id == session_id)))
        print(f"관측 {len(observations)}건 (order 있음 {sum(o.order is not None for o in observations)}, "
              f"bbox 있음 {sum(o.bbox is not None for o in observations)})")
        print("식별:", dict(Counter(str(r.identification_status) for r in results if r.observation_id)))
        shelf = check_shelf_order(db, session_id)
    print(f"흐름 검사: 엉뚱한 책 {shelf.out_of_place_count} / 순서 오류 {shelf.out_of_order_count}")
    for slot in shelf.slots:
        for image in slot.images:
            print(f"  [{slot.slot_id}] {image.image_ref}")
            for b in image.books:
                mark = {"OUT_OF_PLACE": "  ← 엉뚱한 책", "OUT_OF_ORDER": "  ← 순서 오류"}.get(b.status, "")
                print(f"    {b.order:>3}. {b.call_number or '(알 수 없음)':<28} {b.book_copy_id or '-':<10} "
                      f"{b.status}{mark}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("start")
    p.add_argument("--shelf", default="L-01")
    p.add_argument("--slots", type=int, default=10)
    sub.add_parser("finish").add_argument("session_id")
    sub.add_parser("report").add_argument("session_id")
    args = parser.parse_args()
    if args.command == "start":
        start(args.shelf, args.slots)
    elif args.command == "finish":
        finish(args.session_id)
    else:
        report(args.session_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
