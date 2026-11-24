"""테스트 공통 준비물.

테스트는 개발 DB(library_robot)가 아니라 테스트 전용 DB(library_robot_test)에서 실행된다.
- 테스트 DB 이름은 .env의 TEST_DB_NAME으로 바꿀 수 있다 (기본: library_robot_test).
- 테스트 시작 시 Alembic 마이그레이션을 테스트 DB에 자동 적용한다.

db     : 테스트 DB에 연결하되, 테스트가 끝나면 전부 롤백되는 세션
client : 위 db 세션과 테스트용 관리자 토큰을 쓰는 API 클라이언트
"""
import os

from dotenv import dotenv_values

# app을 import하기 전에 DB 이름을 바꿔야 한다 (엔진이 import 시점에 만들어지므로).
_env = dotenv_values(".env")
DEV_DB_NAME = os.environ.get("DB_NAME") or _env.get("DB_NAME")
TEST_DB_NAME = os.environ.get("TEST_DB_NAME") or _env.get("TEST_DB_NAME") or "library_robot_test"
if TEST_DB_NAME == DEV_DB_NAME:
    raise RuntimeError(f"테스트 DB 이름({TEST_DB_NAME})이 개발 DB와 같습니다. 개발 데이터 보호를 위해 테스트를 중단합니다.")
os.environ["DB_NAME"] = TEST_DB_NAME

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.session import engine, get_db  # noqa: E402
from app.main import app  # noqa: E402

TEST_ADMIN_TOKEN = "test-admin-token"
ADMIN_HEADERS = {"X-Admin-Token": TEST_ADMIN_TOKEN}


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """테스트 DB에 최신 마이그레이션을 적용한다 (테스트 실행마다 1회)."""
    assert get_settings().db_name == TEST_DB_NAME
    try:
        with engine.connect():
            pass
    except OperationalError as exc:
        pytest.exit(f"테스트 DB '{TEST_DB_NAME}'에 접속할 수 없습니다. scripts/init_db.sql의 테스트 DB 부분을 "
                    f"root로 실행했는지 확인하세요. ({exc.orig})", returncode=1)
    command.upgrade(Config("alembic.ini"), "head")


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


@pytest.fixture
def loan_tracking(client, monkeypatch):
    """대출 상태를 믿는 모드(LOAN_STATUS_RELIABLE=true). 기본은 꺼져 있다 (도서관 대출 정보 연동 없음)."""
    settings = app.dependency_overrides[get_settings]().model_copy(update={"loan_status_reliable": True})
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr("app.services.retrieval.get_settings", lambda: settings)
    return settings
