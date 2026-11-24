"""AI·로봇 장치의 API 키 (BE-017).

API 키 원문은 저장하지 않고 해시값만 저장한다. 원문은 발급 시 한 번만 보여준다.
"""
from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import DeviceType
from app.db.base import Base, UtcDateTime, enum_type, utcnow


class DeviceCredential(Base):
    __tablename__ = "device_credentials"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_type: Mapped[DeviceType] = mapped_column(enum_type(DeviceType))
    device_id: Mapped[str] = mapped_column(String(64), index=True)  # 로봇이면 R001, AI면 노드 이름 [가정]
    key_hash: Mapped[str] = mapped_column(String(128), unique=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
