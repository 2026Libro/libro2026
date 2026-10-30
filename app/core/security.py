"""인증 의존성.

- 관리자 API : 요청 헤더 `X-Admin-Token`
- AI·로봇 API: 요청 헤더 `X-API-Key` (scripts/create_device_key.py로 발급)

Swagger UI 오른쪽 위 [Authorize] 버튼에 값을 넣으면 모든 요청에 자동으로 붙는다.

[가정] 프론트엔드 로그인 방식(세션, JWT 등)이 정해지면 require_admin만 교체한다.
"""
import hmac
from collections.abc import Callable

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.enums import DeviceType
from app.core.errors import AppError
from app.db.base import utcnow
from app.db.session import get_db
from app.models import DeviceCredential
from app.schemas.common import ErrorCode
from app.services.device import hash_api_key

admin_token_header = APIKeyHeader(name="X-Admin-Token", auto_error=False, description="관리자 토큰")
device_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="AI·로봇 장치 API 키")


def require_admin(
    token: str | None = Security(admin_token_header),
    settings: Settings = Depends(get_settings),
) -> str:
    """관리자 토큰을 확인하고 관리자 ID를 돌려준다. 이력의 changed_by 등에 쓰인다."""
    if token:
        for known_token, admin_id in settings.admin_token_map.items():
            if hmac.compare_digest(known_token, token):  # 응답 시간 차이로 토큰을 추측하지 못하게 비교
                return admin_id
    raise AppError(ErrorCode.UNAUTHORIZED, "관리자 인증이 필요합니다.", status_code=401)


def require_device(*allowed: DeviceType) -> Callable[..., DeviceCredential]:
    """지정한 종류의 장치 API 키만 허용하는 의존성을 만든다. 예: Depends(require_device(DeviceType.AI))"""

    def dependency(
        api_key: str | None = Security(device_key_header),
        db: Session = Depends(get_db),
    ) -> DeviceCredential:
        cred = None
        if api_key:
            cred = db.scalar(select(DeviceCredential).where(
                DeviceCredential.key_hash == hash_api_key(api_key),
                DeviceCredential.is_active.is_(True),
                DeviceCredential.revoked_at.is_(None),
            ))
        if cred is None:
            raise AppError(ErrorCode.UNAUTHORIZED, "유효한 API 키가 필요합니다.", status_code=401)
        if cred.device_type not in allowed:
            raise AppError(ErrorCode.FORBIDDEN, f"{cred.device_type} 장치는 이 API를 호출할 수 없습니다.", status_code=403)

        cred.last_used_at = utcnow()
        db.commit()
        return cred

    return dependency
