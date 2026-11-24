"""프론트 연동 테스트용 스캔 세션 만들기 (AI 인식 결과 화면 확인용).

실행 (프로젝트 폴더에서):
    .venv\\Scripts\\python -m scripts.fe_test_session            # 서가 F-01(칸 2개)에 새 세션 1건 생성

만드는 것
- 서가 F-01 (칸 F-01-01, F-01-02), 로봇 R001이 없으면 만든다. 실행할 때마다 새 세션을 만든다.
- 실제 대출실 장서 15권으로 사진 3장 분량의 관측 17건 (겹친 사진 중복 2건 포함)을 저장한다.
  화면에서 볼 상태를 모두 넣었다: 정상, 겹친 사진 중복, 엉뚱한 책, 순서 오류, 복본 미확정(후보만), 인식 실패,
  글자 조각 매칭.
- 사진은 책등·라벨을 그린 SVG (실제 촬영 사진이 아님). bbox는 이 그림의 픽셀 좌표와 정확히 맞는다.
  파일은 IMAGE_ROOT/fe-test/<세션ID>/ 에 저장되고 GET /observations/{id}/image 로 나온다.
- 로봇 촬영 종료·AI 분석 완료까지 처리해서 흐름 검사 결과(sequence_status)·알림까지 생긴 상태로 만든다.
"""
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from app.core.config import get_settings
from app.core.enums import ScanSessionStatus
from app.db.session import SessionLocal
from app.models import BookCopy, Robot, Shelf
from app.schemas.observation import ObservationCreate
from app.schemas.scan_session import ScanSessionCreate, ScanSessionFinish
from app.schemas.shelf import ShelfCreate
from app.services import scan_session as scan_service
from app.services import shelf as shelf_service
from app.services.observation import receive_observation
from app.services.shelf_order import check_shelf_order
from app.services.verification import Thresholds, check_unobserved

ROBOT, SHELF = "R001", "F-01"
WIDTH, HEIGHT, FLOOR = 1600, 1000, 960
KST = timezone(timedelta(hours=9))

# 책 1권: (등록번호, AI가 보내는 글자, OCR 신뢰도, 그림에 쓸 라벨)
#   글자: {"call": 청구기호, "title": 서명} 또는 {"fragments": [...]}. 등록번호는 보내지 않는다 (매칭은 백엔드).
#   등록번호 None = 그림만 있고 장서에 없는 책 (인식 실패)
BOOKS = {
    "a": ("EM0211328", {"call": "811.36 김658ㅎ c.2", "title": "화요일의 키스"}, 0.93),
    "b": ("EM0242152", {"call": "811.36 김658향 c.2", "title": "향나무 베개를 베고 자는 잠"}, 0.91),
    "c": ("EM0223422", {"call": "811.36 김659ㄴ2 C,2", "title": "능수엄마"}, 0.88),            # 쉼표 오인식
    "d": ("EM0193608", {"call": "811.36 김66ㄴ c.2", "title": "남장여자"}, 0.92),
    "e": ("EM0338008", {"call": "811.36 김66냉", "title": "냉면"}, 0.95),
    "f": ("EM0305793", {"call": "811.36 김66느 v.1", "title": "늘푸른 소나무"}, 0.9),
    "g": ("EM0305795", {"call": "811.36 김66느 v.2", "title": "늘푸른 소나무"}, 0.9),
    "h": ("EM0305797", {"call": "811.36 김66느 v.3", "title": "늘푸른 소나무"}, 0.89),
    "i": ("EM0226652", {"call": "811.36 김66돌 c.2", "title": "돌풍전후"}, 0.9),
    "j": ("EM0223782", {"fragments": ["로망에의동물학적접근", "811.36", "김66로", "c.2"]}, 0.84),   # 조각 매칭
    "k": ("EM0325167", {"call": "811.36 김66마3", "title": "마당 깊은 집"}, 0.92),
    "l": ("EM0331548", {"call": "330 B787m박", "title": "도덕경제학"}, 0.94),                 # 엉뚱한 책
    "m": ("EM0185520", {"call": "811.36 김66사 v.1 c.2", "title": "사랑하고 사랑한다"}, 0.9),  # 순서 바뀜
    "n": ("EM0238585", {"call": "811.36 김66부 c.2", "title": "부부의 초상"}, 0.9),
    "o": ("EM0396159", {"call": "811.36 김66여", "title": "영화가 된 근대소설"}, 0.86),         # 복본 2권 → 후보만
    "p": (None, {"fragments": ["ㅁ", "8"]}, 0.21),                                            # 라벨 가려짐
    "q": ("EM0200429", {"fragments": ["왕자의체통", "811,36", "김66왕", "C.2"]}, 0.83),
}
# 사진: (칸, 촬영 분, 왼쪽부터 책). p2 앞 두 권은 p1 끝 두 권과 겹쳐 찍힘
PHOTOS = [
    ("F-01-01", 0, "abcdef"),
    ("F-01-01", 1, "efghi"),
    ("F-01-02", 2, "jklmnopq"),
]
COLORS = ["#7b2d26", "#1f4e79", "#2e6b3a", "#6b4f2a", "#4b3b6b", "#8a6d1d", "#2f5d62", "#7a3e5d"]


def ensure_fixtures(db) -> None:
    if db.get(Robot, ROBOT) is None:
        db.add(Robot(id=ROBOT, name="테스트 로봇"))
        db.commit()
    if db.get(Shelf, SHELF) is None:
        shelf_service.create_shelf(db, ShelfCreate(id=SHELF, name="프론트 연동 테스트 서가", slot_count=2))


def layout(keys: str) -> list[tuple[str, list[int]]]:
    """책마다 bbox [x, y, width, height] (원본 픽셀, 왼쪽 위 기준)."""
    boxes, x = [], 60
    for n, key in enumerate(keys):
        w = 120 + (ord(key) * 37) % 60
        top = 90 + (ord(key) * 53) % 110
        boxes.append((key, [x, top, w, FLOOR - top]))
        x += w + 8
    return boxes


def label_lines(key: str) -> list[str]:
    copy_id, text, _ = BOOKS[key]
    call = text.get("call") or " ".join(text["fragments"][1:]) if copy_id else ""
    return call.replace(" ", "\n", 1).replace(" c", "\nc").replace(" v", "\nv").replace(" C", "\nC").split("\n")


def draw(keys: str, titles: dict[str, str]) -> str:
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
             f'viewBox="0 0 {WIDTH} {HEIGHT}">',
             f'<rect width="{WIDTH}" height="{HEIGHT}" fill="#d9cbb3"/>',
             f'<rect y="{FLOOR}" width="{WIDTH}" height="{HEIGHT - FLOOR}" fill="#8b6b45"/>',
             '<text x="20" y="40" font-size="22" fill="#5a4a33" font-family="sans-serif">'
             '테스트용 합성 이미지 (실제 촬영 아님)</text>']
    for key, (x, y, w, h) in layout(keys):
        color = COLORS[ord(key) % len(COLORS)]
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="{color}"/>')
        title = escape(titles.get(key, ""))
        parts.append(f'<text x="{x + w / 2}" y="{y + 30}" font-size="30" fill="#f5efe2" font-family="sans-serif" '
                     f'writing-mode="vertical-rl" letter-spacing="2">{title}</text>')
        ly = FLOOR - 190
        if key == "p":   # 라벨이 가려진 책
            parts.append(f'<rect x="{x + 10}" y="{ly}" width="{w - 20}" height="150" fill="#3a3a3a" opacity="0.85"/>')
            continue
        parts.append(f'<rect x="{x + 10}" y="{ly}" width="{w - 20}" height="150" rx="3" fill="#fbfaf5"/>')
        for n, line in enumerate(label_lines(key)):
            parts.append(f'<text x="{x + w / 2}" y="{ly + 36 + n * 34}" font-size="24" text-anchor="middle" '
                         f'fill="#111" font-family="monospace">{escape(line)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    settings = get_settings()
    th = Thresholds.from_settings(settings)
    with SessionLocal() as db:
        ensure_fixtures(db)
        titles = {}
        for key, (copy_id, text, _) in BOOKS.items():
            copy = db.get(BookCopy, copy_id) if copy_id else None
            if copy_id and copy is None:
                print(f"장서에 없는 등록번호 {copy_id}: 먼저 scripts.import_library_csv로 장서를 넣으세요.")
                return 1
            titles[key] = (copy.book.title.split(" : ")[0] if copy else "") or text.get("title", "")

        session = scan_service.create_session(db, ScanSessionCreate(robot_id=ROBOT, shelf_id=SHELF))
        sid = session.id
        start = datetime.now(KST).replace(second=0, microsecond=0)
        folder = Path(settings.image_root) / "fe-test" / sid
        folder.mkdir(parents=True, exist_ok=True)

        n = 0
        for photo_no, (slot_id, minute, keys) in enumerate(PHOTOS, start=1):
            image_ref = f"fe-test/{sid}/p{photo_no}.svg"
            (Path(settings.image_root) / image_ref).write_text(draw(keys, titles), encoding="utf-8")
            for order, (key, bbox) in enumerate(layout(keys), start=1):
                n += 1
                _, text, ocr = BOOKS[key]
                shown = text.get("call") or " / ".join(text["fragments"])
                data = ObservationCreate(
                    observation_id=f"OBS-{sid.removeprefix('SCAN-')}-{n:03d}", scan_session_id=sid,
                    robot_id=ROBOT, shelf_id=SHELF, slot_id=slot_id,
                    detected_text=f"{shown} / {text['title']}" if "title" in text else shown,
                    call_number_text=text.get("call"), title_text=text.get("title"),
                    text_fragments=text.get("fragments", []),
                    confidence={"detection": 0.62 if key == "p" else 0.96, "ocr": ocr},
                    image_ref=image_ref, order=order, bbox=bbox, image_width=WIDTH, image_height=HEIGHT,
                    captured_at=start + timedelta(minutes=minute, seconds=order))
                receive_observation(db, data, th)

        scan_service.finish_session(db, sid, ScanSessionFinish(status=ScanSessionStatus.COMPLETED), robot_id=ROBOT)
        check_unobserved(db, sid, settings.loan_status_reliable)
        shelf = check_shelf_order(db, sid)

    print(f"DB: {settings.db_name} / 이미지 폴더: {folder.resolve()}")
    print(f"scan_session_id = {sid}  (서가 {SHELF}, 칸 F-01-01·F-01-02, 관측 {n}건)")
    print(f"흐름 검사: 엉뚱한 책 {shelf.out_of_place_count} / 순서 오류 {shelf.out_of_order_count}")
    for slot in shelf.slots:
        for image in slot.images:
            print(f"  [{slot.slot_id}] {image.image_ref}")
            for b in image.books:
                print(f"    {b.order}. {b.call_number or '(알 수 없음)':<26} {b.book_copy_id or '-':<10} {b.status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
