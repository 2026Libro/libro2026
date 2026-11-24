"""모의 AI: 실제 AI 대신 A-03 서가 관측 결과를 백엔드로 보낸다.

먼저 서버를 실행해 둔 상태에서 (다른 터미널, 프로젝트 폴더에서):
    .venv\\Scripts\\python -m scripts.mock_ai --ai-key <AI 키> [--robot-key <로봇 R001 키>]

- --session-id 를 주지 않으면 .env의 관리자 토큰으로 스캔 세션(R001, A-03)을 새로 만든다.
- --robot-key 를 주면 마지막에 로봇 R001 입장에서 촬영 종료(COMPLETED)를 보고하고,
  이어서 AI 입장에서 분석 완료를 알려 미관측 판정까지 실행한다.
- 마지막에 이 세션의 검증 결과(식별·위치 판정·처리 흐름)를 표로 출력한다.
- --last (또는 같은 --session-id)로 다시 실행하면 같은 observation_id가 재전송되므로 중복 방지(200 응답)를 확인할 수 있다.
- --reobserve : 로봇이 완료한 재관측 작업(REOBSERVE_BOOK)마다 재관측 분석 결과를 보낸다.
  (순서: mock_ai → mock_robot → mock_ai --reobserve)

시나리오는 scripts/seed_sample_data.py 의 샘플 데이터를 기준으로 한다.
"""
import argparse
from datetime import datetime, timedelta, timezone

import httpx

from app.core.config import get_settings

KST = timezone(timedelta(hours=9))

# (설명, 칸, 읽은 글자, AI가 확정한 복본, 신뢰도(탐지, OCR, 식별), 후보[(복본, 점수)])
SCENARIOS = [
    ("정상 위치: 자료구조와 알고리즘", "A-03-03", "자료구조와 알고리즘", "BOOK-127", (0.99, 0.95, 0.93),
     [("BOOK-127", 0.93)]),
    ("같은 제목 복본 2권 → 복본 미확정", "A-03-02", "파이썬 프로그래밍", None, (0.98, 0.91, 0.75),
     [("BOOK-125", 0.75), ("BOOK-126", 0.72)]),
    ("다른 서가 책 (등록 위치 B-01-01)", "A-03-05", "로봇공학 입문", "BOOK-201", (0.97, 0.90, 0.88),
     [("BOOK-201", 0.88), ("BOOK-202", 0.85)]),
    ("OCR 신뢰도 낮음", "A-03-04", "데이터베?", None, (0.93, 0.42, 0.38),
     [("BOOK-128", 0.38)]),
    ("인식 실패 (글자 못 읽음)", "A-03-01", None, None, (0.88, 0.05, 0.0),
     []),
]

# 재관측 결과 (칸별). 여기에 없는 칸은 다시 인식 실패로 보낸다.
REOBSERVE_RESULTS = {
    "A-03-05": ("같은 책 재확인 → 오배가 확정", "로봇공학 입문", "BOOK-201", (0.98, 0.93, 0.91), [("BOOK-201", 0.91)]),
    "A-03-04": ("선명하게 다시 읽음", "데이터베이스 개론", "BOOK-128", (0.97, 0.92, 0.90), [("BOOK-128", 0.90)]),
}
REOBSERVE_FAILED = ("다시 찍어도 인식 실패", None, None, (0.85, 0.05, 0.0), [])


def main() -> None:
    parser = argparse.ArgumentParser(description="모의 AI 관측 전송")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--ai-key", required=True, help="scripts.create_device_key --type AI 로 발급한 키")
    parser.add_argument("--robot-key", help="R001 로봇 키 (주면 촬영 종료 + 분석 완료 → 미관측 판정까지 실행)")
    parser.add_argument("--session-id", help="기존 스캔 세션 ID (없으면 새로 생성)")
    parser.add_argument("--last", action="store_true", help="가장 최근 스캔 세션으로 다시 전송 (중복 방지 확인용)")
    parser.add_argument("--reobserve", action="store_true", help="완료된 재관측 작업에 대한 재관측 결과 전송")
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=10) as http:
        if args.reobserve:
            _send_reobservations(http, args.ai_key)
            return

        session_id = args.session_id or (_latest_session(http) if args.last else _create_session(http))
        print(f"스캔 세션: {session_id}\n")

        suffix = session_id.removeprefix("SCAN-")
        # 촬영 시각을 세션 시작 시각 기준으로 정해야 재실행 시 완전히 같은 내용이 전송된다.
        session = http.get(f"/scan-sessions/{session_id}")
        if session.status_code == 404:
            raise SystemExit(f"스캔 세션 '{session_id}'가 없습니다. 이전 실행 결과 첫 줄의 'SCAN-...' 값을 그대로 넣으세요.")
        session.raise_for_status()
        started_at = datetime.fromisoformat(session.json()["started_at"])
        base_time = started_at.astimezone(KST).replace(microsecond=0)
        for i, (title, slot_id, text, copy_id, (det, ocr, ident), candidates) in enumerate(SCENARIOS, start=1):
            obs_id = f"OBS-{suffix}-{i:03d}"
            payload = {
                "observation_id": obs_id,
                "scan_session_id": session_id,
                "robot_id": "R001",
                "shelf_id": "A-03",
                "slot_id": slot_id,
                "detected_text": text,
                "book_copy_id": copy_id,
                "confidence": {"detection": det, "ocr": ocr, "identification": ident},
                "candidates": [{"book_copy_id": c, "score": s} for c, s in candidates],
                "reobservation_of": None,
                "image_ref": f"images/{obs_id}.jpg",
                "captured_at": (base_time + timedelta(seconds=i)).isoformat(),
            }
            res = http.post("/observations", json=payload, headers={"X-API-Key": args.ai_key})
            result = {201: "저장됨", 200: "이미 있음(재전송)"}.get(res.status_code, res.text)
            print(f"[{res.status_code}] {obs_id} {title}: {result}")

        if args.robot_key:
            res = http.post(f"/scan-sessions/{session_id}/finish", json={"status": "COMPLETED"},
                            headers={"X-API-Key": args.robot_key})
            print(f"\n로봇 촬영 종료 보고 [{res.status_code}]: {res.json().get('status', res.text)}")

            res = http.post(f"/scan-sessions/{session_id}/analysis-complete", headers={"X-API-Key": args.ai_key})
            if res.status_code == 200:
                r = res.json()
                print(f"AI 분석 완료 → 미관측 판정: 등록 {r['registered_copy_count']}권 중 "
                      f"미관측 {r['unobserved_copy_count']}권 (관리자 검토 {r['review_required_count']}권)")
            else:
                print(f"AI 분석 완료 보고 [{res.status_code}]: {res.json()['error']['message']}")

        _print_verifications(http, session_id)


def _print_verifications(http: httpx.Client, session_id: str) -> None:
    res = http.get("/verifications", params={"scan_session_id": session_id, "limit": 100})
    res.raise_for_status()
    print("\n검증 결과:")
    print(f"  {'관측칸':8} {'복본':11} {'식별':15} {'위치 판정':20} {'처리 흐름':22} 비고")
    for v in sorted(res.json()["items"], key=lambda v: v["id"]):
        print(f"  {v['observed_slot_id'] or '-':8} {v['book_copy_id'] or '-':11} {v['identification_status'] or '-':15} "
              f"{v['location_verdict']:20} {v['workflow_status'] or '-':22} {v['note'] or ''}")


def _send_reobservations(http: httpx.Client, ai_key: str) -> None:
    res = http.get("/tasks", params={"task_type": "REOBSERVE_BOOK", "status": "COMPLETED", "limit": 100})
    res.raise_for_status()
    sent = 0
    for task in res.json()["items"]:
        original_id = task["reobservation_of"]
        pending = http.get("/verifications", params={"observation_id": original_id}).json()["items"]
        if not pending or pending[0]["workflow_status"] != "REOBSERVATION_PENDING":
            continue  # 이미 재관측 결과를 보냈거나 다른 처리가 끝남

        original = http.get(f"/observations/{original_id}").json()
        title, text, copy_id, (det, ocr, ident), candidates = REOBSERVE_RESULTS.get(original["slot_id"], REOBSERVE_FAILED)
        obs_id = f"{original_id}-R"
        captured = datetime.fromisoformat(task["finished_at"])  # 로봇이 재촬영을 마친 시각
        res = http.post("/observations", headers={"X-API-Key": ai_key}, json={
            "observation_id": obs_id,
            "scan_session_id": original["scan_session_id"],  # 원래 관측과 같은 세션
            "robot_id": original["robot_id"],
            "shelf_id": original["shelf_id"],
            "slot_id": original["slot_id"],
            "detected_text": text,
            "book_copy_id": copy_id,
            "confidence": {"detection": det, "ocr": ocr, "identification": ident},
            "candidates": [{"book_copy_id": c, "score": s} for c, s in candidates],
            "reobservation_of": original_id,
            "image_ref": f"images/{obs_id}.jpg",
            "captured_at": captured.astimezone(KST).isoformat(),
        })
        if res.status_code not in (200, 201):
            print(f"[{res.status_code}] {obs_id}: {res.text}")
            continue
        v = http.get("/verifications", params={"observation_id": obs_id}).json()["items"][0]
        print(f"[{res.status_code}] {original['slot_id']} {title}: "
              f"{v['identification_status']} / {v['location_verdict']} / {v['workflow_status'] or '-'}")
        sent += 1
    print(f"\n재관측 결과 {sent}건 전송." if sent else "보낼 재관측 결과가 없습니다. (mock_robot으로 재관측 작업을 먼저 완료하세요)")


def _latest_session(http: httpx.Client) -> str:
    res = http.get("/scan-sessions", params={"shelf_id": "A-03", "limit": 1})
    res.raise_for_status()
    items = res.json()["items"]
    if not items:
        raise SystemExit("A-03 서가의 스캔 세션이 아직 없습니다. --last 없이 먼저 한 번 실행하세요.")
    return items[0]["id"]


def _create_session(http: httpx.Client) -> str:
    tokens = list(get_settings().admin_token_map)
    if not tokens:
        raise SystemExit(".env에 ADMIN_TOKENS가 없습니다. --session-id를 직접 지정하세요.")
    res = http.post("/scan-sessions", json={"robot_id": "R001", "shelf_id": "A-03"},
                    headers={"X-Admin-Token": tokens[0]})
    res.raise_for_status()
    return res.json()["id"]


if __name__ == "__main__":
    main()
