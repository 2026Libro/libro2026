"""테스트 공통 준비물.

db     : 실제 MySQL에 연결하되, 테스트가 끝나면 전부 롤백되는 세션 (DB에 데이터가 남지 않음)
client : 위 db 세션과 테스트용 관리자 토큰을 쓰는 API 클라이언트
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import engine, get_db
from app.main import app

TEST_ADMIN_TOKEN = "test-admin-token"
ADMIN_HEADERS = {"X-Admin-Token": TEST_ADMIN_TOKEN}


@pytest.fixture
def db():
    with engine.connect() as conn:
        tx = conn.begin()
        # 서비스 코드의 commit()은 SAVEPOINT 해제로만 처리되고, 마지막에 바깥 트랜잭션을 롤백한다.
        session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
        try:
            yield session
        finally:
            session.close()
            tx.rollback()


@pytest.fixture
def client(db):
    test_settings = get_settings().model_copy(update={"admin_tokens": f"tester:{TEST_ADMIN_TOKEN}"})
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_settings] = lambda: test_settings
    yield TestClient(app)
    app.dependency_overrides.clear()
