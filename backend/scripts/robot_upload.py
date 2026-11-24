"""로봇(하드웨어)용 촬영·업로드 도구 — 이 파일 하나만 로봇 PC(라즈베리파이 등)에 복사해서 실행한다.

파이썬 3.9 이상 기본 라이브러리만 쓴다 (pip 설치 필요 없음).

하는 일: 세션 만들기 → 칸 사진 업로드 → 촬영 끝 신호. 그다음 OCR·판정·화면 표시는 백엔드가 한다.

1) 이미 찍은 사진 올리기 (칸:파일, 왼쪽 사진부터 순서대로)
    python robot_upload.py --server http://10.80.211.86:8000 --key <로봇키> --shelf R-01 \\
        R-01-01:shot1.jpg R-01-01:shot2.jpg R-01-02:shot3.jpg

2) 칸마다 촬영 명령을 실행하고 바로 올리기 ({file} 자리에 저장할 파일 이름이 들어감)
    python robot_upload.py --server http://10.80.211.86:8000 --key <로봇키> --shelf R-01 \\
        --capture "libcamera-still -n -o {file}" --slots R-01-01 R-01-02

옵션: --robot R001 (기본), --key 대신 환경변수 ROBOT_API_KEY, --keep-open (촬영 끝 신호를 보내지 않음)
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def call(method, url, key, body=None, content_type="application/json", params=None, retries=3):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"X-API-Key": key, "Content-Type": content_type})
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return res.status, json.loads(res.read() or b"null")
        except urllib.error.HTTPError as exc:   # 4xx·5xx: 서버가 이유를 알려 줌
            detail = exc.read().decode("utf-8", "replace")
            if exc.code < 500 or attempt == retries:
                raise SystemExit(f"[오류] {method} {url} → {exc.code}\n{detail}")
        except (urllib.error.URLError, TimeoutError) as exc:   # 연결 실패: 잠시 후 다시
            if attempt == retries:
                raise SystemExit(f"[오류] 서버에 연결할 수 없습니다: {url} ({exc})")
        time.sleep(2 * attempt)


def capture(command: str, slot: str, n: int, folder: Path) -> Path:
    path = folder / f"{slot}_{n:03d}.jpg"
    # 명령을 먼저 나눈 뒤 {file}을 바꾼다 (경로의 공백·역슬래시가 깨지지 않게)
    cmd = [part.replace("{file}", str(path)) for part in shlex.split(command)]
    print(f"  촬영: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    if not path.is_file():
        raise SystemExit(f"[오류] 촬영 명령이 {path} 파일을 만들지 않았습니다.")
    return path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("photos", nargs="*", help="칸:사진파일 (예: R-01-01:shot1.jpg), 왼쪽 사진부터")
    p.add_argument("--server", required=True, help="백엔드 주소 (예: http://10.80.211.86:8000)")
    p.add_argument("--key", default=os.environ.get("ROBOT_API_KEY"), help="로봇 API 키 (또는 ROBOT_API_KEY)")
    p.add_argument("--robot", default="R001")
    p.add_argument("--shelf", required=True, help="찍는 서가 (예: R-01)")
    p.add_argument("--capture", help='촬영 명령. {file}에 저장 경로가 들어감 (예: "libcamera-still -n -o {file}")')
    p.add_argument("--slots", nargs="*", default=[], help="--capture를 쓸 때 찍을 칸 순서")
    p.add_argument("--out", default="captures", help="--capture로 찍은 사진을 둘 폴더")
    p.add_argument("--keep-open", action="store_true", help="촬영 끝 신호를 보내지 않음 (나중에 더 올릴 때)")
    args = p.parse_args()
    if not args.key:
        p.error("--key 또는 환경변수 ROBOT_API_KEY가 필요합니다.")
    if not args.photos and not (args.capture and args.slots):
        p.error("사진(칸:파일)을 주거나 --capture와 --slots를 함께 주세요.")
    server = args.server.rstrip("/")

    _, session = call("POST", f"{server}/scan-sessions", args.key, {"robot_id": args.robot, "shelf_id": args.shelf})
    sid = session["id"]
    print(f"세션 시작: {sid} (서가 {args.shelf}, 로봇 {args.robot})")

    try:
        upload_all(args, server, sid)
    except BaseException:
        # 중간에 실패하면 세션을 '중단(INCOMPLETE)'으로 닫는다 (촬영 중으로 남지 않게)
        call("POST", f"{server}/scan-sessions/{sid}/finish", args.key, {"status": "INCOMPLETE"}, retries=1)
        print(f"세션 {sid}를 중단(INCOMPLETE)으로 닫았습니다. 다시 실행하면 새 세션으로 찍습니다.")
        raise

    if not args.keep_open:
        call("POST", f"{server}/scan-sessions/{sid}/finish", args.key, {"status": "COMPLETED"})
        print("촬영 끝 신호 보냄. OCR이 끝나면 백엔드가 판정하고 관리자 화면에 나옵니다.")
    print(f"진행 상태: {server}/scan-sessions/{sid}/images")
    return 0


def upload_all(args, server: str, sid: str) -> None:
    jobs = []
    folder = Path(args.out) / sid
    if args.capture:
        folder.mkdir(parents=True, exist_ok=True)
        jobs = [(slot, None) for slot in args.slots]
    for item in args.photos:
        slot, sep, file = item.partition(":")
        if not sep or not Path(file).is_file():
            raise SystemExit(f"[오류] '{item}'은 칸:사진파일 형식이 아니거나 파일이 없습니다.")
        jobs.append((slot, Path(file)))

    for n, (slot, path) in enumerate(jobs, start=1):
        if path is None:
            path = capture(args.capture, slot, n, folder)
        ctype = TYPES.get(path.suffix.lower())
        if not ctype:
            raise SystemExit(f"[오류] {path}: JPEG·PNG만 올릴 수 있습니다.")
        captured = datetime.fromtimestamp(path.stat().st_mtime, KST).isoformat(timespec="seconds")
        image_id = f"IMG-{sid.removeprefix('SCAN-')}-{n:03d}"[:44]   # 재전송해도 한 번만 저장되는 ID
        status, image = call("POST", f"{server}/scan-sessions/{sid}/images", args.key, path.read_bytes(), ctype,
                             {"slot_id": slot, "captured_at": captured, "image_id": image_id})
        print(f"  업로드 {n}/{len(jobs)}: {slot} {path.name} → {image['id']} ({status}, {image['ocr_status']})")


if __name__ == "__main__":
    sys.exit(main())
