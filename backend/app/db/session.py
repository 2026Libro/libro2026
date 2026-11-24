"""DB 엔진과 세션 생성.

라우터에서는 get_db()를 의존성(Depends)으로 받아 요청마다 세션을 열고 닫는다.
"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,  # 끊어진 커넥션을 사용 전에 감지
    pool_recycle=3600,   # MySQL wait_timeout 대비
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session_factory():
    """백그라운드 작업(요청이 끝난 뒤 실행)이 쓸 세션 생성기. 테스트에서는 테스트 세션으로 바꾼다."""
    return SessionLocal
