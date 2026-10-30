"""모의 로봇: 실제 로봇 대신 R001로서 작업을 받아 처리한다.

먼저 서버를 실행해 둔 상태에서 (다른 터미널, 프로젝트 폴더에서):
    .venv\\Scripts\\python -m scripts.mock_robot --robot-key <R001 키>

동작: 상태 보고 → 결과 불명 작업이 있으면 결과 재보고 → 작업 가져가기 → 수락 → 진행 → 완료 를
할 작업이 없을 때까지 반복한다.

--mode fail   : 수락 후 실패(FAILED)로 보고
--mode vanish : 수락·진행 보고 후 아무것도 하지 않고 종료 (통신 끊김 흉내)
                서버 설정 ROBOT_OFFLINE_AFTER_SECONDS(기본 60초)가 지나면 작업이 UNKNOWN이 된다.
                그 뒤 --mode normal 로 다시 실행하면 UNKNOWN 작업의 결과를 보고해 복구한다.
"""
import argparse
import uuid
from datetime import datetime, timezone

import httpx

ROBOT_ID = "R001"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _report(http: httpx.Client, task_id: str, status: str, event_type: str | None = None, message: str | None = None):
    res = http.post(f"/tasks/{task_id}/result", json={
        "status": status, "event_type": event_type, "message": message,
        "idempotency_key": str(uuid.uuid4()), "reported_at": _now()})
    print(f"    결과 보고 {event_type or status} [{res.status_code}]")
    return res


def main() -> None:
    parser = argparse.ArgumentParser(description="모의 로봇 R001")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--robot-key", required=True, help="scripts.create_device_key --type ROBOT --device-id R001")
    parser.add_argument("--mode", choices=["normal", "fail", "vanish"], default="normal")
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=10, headers={"X-API-Key": args.robot_key}) as http:
        status = http.post(f"/robots/{ROBOT_ID}/status", json={
            "battery_percent": 92, "current_location": {"shelf_id": "A-03"}, "reported_state": "IDLE",
            "reported_at": _now()})
        status.raise_for_status()
        print("상태 보고 완료 (배터리 92%)")

        for task_id in status.json()["tasks_needing_result"]:
            # 실제 로봇이라면 내부 기록을 보고 실제 결과를 보고해야 한다. 모의 로봇은 '완료했었다'고 가정.
            print(f"결과 불명 작업 {task_id} → 통신 복구 후 실제 결과 보고")
            _report(http, task_id, "COMPLETED", message="통신 끊김 중 완료했음")

        handled = 0
        while True:
            res = http.get(f"/robots/{ROBOT_ID}/tasks/next")
            if res.status_code == 204:
                print(f"\n할 작업 없음. 처리한 작업 {handled}건.")
                return
            res.raise_for_status()
            cmd = res.json()
            target = cmd.get("slot_id") or cmd.get("shelf_id") or cmd.get("target_task_id")
            print(f"\n작업 {cmd['task_id']} {cmd['task_type']} ({target})")

            ack = http.post(f"/tasks/{cmd['task_id']}/ack", json={
                "accepted": True, "idempotency_key": str(uuid.uuid4()), "reported_at": _now()})
            if ack.status_code != 200:
                print(f"    수락 실패 [{ack.status_code}] → 실행하지 않음: {ack.json()['error']['message']}")
                continue
            print("    수락 [200] ACCEPTED")
            handled += 1

            if cmd["task_type"] == "CANCEL_TASK":
                _report(http, cmd["target_task_id"], "CANCELLED", message="관리자 요청으로 중단")
                _report(http, cmd["task_id"], "COMPLETED")
                continue

            _report(http, cmd["task_id"], "IN_PROGRESS", event_type="ARRIVED")
            if args.mode == "vanish":
                print("\n(통신 끊김 흉내: 결과를 보고하지 않고 종료)")
                return
            if args.mode == "fail":
                _report(http, cmd["task_id"], "FAILED", message="경로 막힘")
                continue
            if cmd["task_type"] in ("SCAN_SHELF", "REOBSERVE_BOOK"):
                print("    (촬영 완료. 실제로는 이 이미지를 AI가 분석해 /observations 로 보낸다)")
            _report(http, cmd["task_id"], "COMPLETED")


if __name__ == "__main__":
    main()
