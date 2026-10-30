"""AI·로봇 장치 API 키 발급 (BE-017).

키 원문은 발급할 때 한 번만 돌려주고, DB에는 SHA-256 해시만 저장한다.
(무작위로 만든 긴 키라서 비밀번호용 느린 해시가 아니어도 안전하다.)
"""
import hashlib
import secrets

from sqlalchemy.orm import Session

from app.core.enums import DeviceType
from app.core.errors import AppError
from app.models import DeviceCredential, Robot
from app.schemas.common import ErrorCode


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def issue_api_key(db: Session, device_type: DeviceType, device_id: str) -> str:
    """새 API 키를 발급하고 원문을 돌려준다."""
    if device_type is DeviceType.ROBOT and db.get(Robot, device_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, f"로봇 '{device_id}'가 등록되어 있지 않습니다.", status_code=404)

    api_key = f"lrk_{secrets.token_urlsafe(32)}"
    db.add(DeviceCredential(device_type=device_type, device_id=device_id, key_hash=hash_api_key(api_key)))
    db.commit()
    return api_key
