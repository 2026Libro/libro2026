"""Alembic 마이그레이션 환경.

DB 접속 주소는 alembic.ini가 아니라 .env(app.core.config)에서 읽는다.
"""
from logging.config import fileConfig

from alembic import context
import sqlalchemy as sa
from sqlalchemy import create_engine

from app.core.config import get_settings
from app.models import Base  # 모든 모델이 import된 Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
database_url = get_settings().database_url


def render_item(type_, obj, autogen_context):
    """상태 코드(Enum) 컬럼을 이름 없이 렌더링한다.

    Alembic은 기본적으로 Enum 클래스 이름(예: robottaskstatus)을 CHECK 제약 이름으로 쓰는데,
    MySQL은 제약 이름이 DB 전체에서 고유해야 하므로 같은 Enum을 여러 컬럼에 쓰면 충돌한다.
    이름을 빼면 Base의 NAMING_CONVENTION(ck_테이블_컬럼)이 적용된다.
    """
    if type_ == "type" and isinstance(obj, sa.Enum) and not obj.native_enum:
        values = ", ".join(repr(v) for v in obj.enums)
        return f"sa.Enum({values}, native_enum=False, create_constraint=True, length={obj.length})"
    return False


def run_migrations_offline() -> None:
    """DB 접속 없이 SQL 스크립트만 생성 (alembic upgrade head --sql)."""
    context.configure(
        url=database_url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        render_item=render_item,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """실제 DB에 접속해 마이그레이션 적용."""
    connectable = create_engine(database_url)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_item=render_item)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
