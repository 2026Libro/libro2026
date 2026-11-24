"""AI·로봇 장치용 API 키 발급.

실행 (프로젝트 폴더에서):
    .venv\\Scripts\\python -m scripts.create_device_key --type AI --device-id ai-laptop-1
    .venv\\Scripts\\python -m scripts.create_device_key --type ROBOT --device-id R001

출력된 키는 다시 확인할 수 없으므로 바로 해당 팀원에게 전달한다. (DB에는 해시만 저장된다)
로봇 키의 device-id는 robots 테이블에 있는 로봇 ID여야 한다.
"""
import argparse

from app.core.enums import DeviceType
from app.db.session import SessionLocal
from app.services.device import issue_api_key


def main() -> None:
    parser = argparse.ArgumentParser(description="AI·로봇 장치 API 키 발급")
    parser.add_argument("--type", required=True, choices=[t.value for t in DeviceType])
    parser.add_argument("--device-id", required=True, help="로봇이면 R001 같은 로봇 ID, AI면 노드 이름")
    args = parser.parse_args()

    with SessionLocal() as db:
        api_key = issue_api_key(db, DeviceType(args.type), args.device_id)

    print(f"[{args.type}] {args.device_id} API 키 (이번에만 표시됨):")
    print(api_key)


if __name__ == "__main__":
    main()
