"""모든 ORM 모델의 부모 클래스(Base)와 공통 컬럼 도구.

Alembic은 Base.metadata를 보고 테이블 변경사항을 감지한다.
"""
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, MetaData
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 제약조건 이름 규칙. 이름이 고정되어야 Alembic이 나중에 수정·삭제할 수 있다.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# 시각은 모두 UTC로 저장한다 (MySQL DATETIME은 시간대를 저장하지 않음). 마이크로초까지 저장.
UtcDateTime = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def enum_type(enum_cls: type[StrEnum]) -> Enum:
    """상태 코드 컬럼 타입. VARCHAR + CHECK 제약으로 정의된 값만 저장되게 한다.

    MySQL 네이티브 ENUM을 쓰지 않으므로 값 추가 시 마이그레이션이 단순하다.
    """
    return Enum(
        enum_cls,
        name=None,  # 제약 이름을 NAMING_CONVENTION(ck_테이블_컬럼)으로 만들어 테이블 간 이름 충돌을 막는다
        native_enum=False,
        create_constraint=True,
        length=32,
        validate_strings=True,
        values_callable=lambda cls: [m.value for m in cls],
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)
