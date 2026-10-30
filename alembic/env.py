"""Alembic 마이그레이션 환경.

DB 접속 주소는 alembic.ini가 아니라 .env(app.core.config)에서 읽는다.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.core.config import get_settings
from app.models import Base  # 모든 모델이 import된 Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
database_url = get_settings().database_url


def run_migrations_offline() -> None:
    """DB 접속 없이 SQL 스크립트만 생성 (alembic upgrade head --sql)."""
    context.configure(
        url=database_url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """실제 DB에 접속해 마이그레이션 적용."""
    connectable = create_engine(database_url)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
