"""/health API 테스트.

- 응답 형식 테스트는 DB 없이 실행된다 (get_db를 가짜 세션으로 교체).
- test_real_database_connection은 .env의 실제 MySQL에 접속한다.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.db.session import engine, get_db
from app.main import app


class FakeSession:
    def __init__(self, fail: bool):
        self.fail = fail

    def execute(self, _statement):
        if self.fail:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))


@pytest.fixture
def client_with_db():
    def _make(fail: bool) -> TestClient:
        app.dependency_overrides[get_db] = lambda: FakeSession(fail)
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()


def test_health_ok(client_with_db):
    res = client_with_db(fail=False).get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "database": "ok", "database_error": None}


def test_health_db_down_returns_503(client_with_db):
    res = client_with_db(fail=True).get("/health")
    assert res.status_code == 503
    body = res.json()
    assert body["database"] == "error"
    assert body["database_error"] == "OperationalError"
    assert "connection refused" not in res.text  # 내부 오류 상세 노출 금지


def test_real_database_connection():
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
