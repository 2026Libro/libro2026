from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


def check_database(db: Session) -> str | None:
    """DB에 SELECT 1을 실행한다. 정상이면 None, 실패하면 오류 종류를 반환한다."""
    try:
        db.execute(text("SELECT 1"))
        return None
    except SQLAlchemyError as exc:
        # 접속 정보 노출을 막기 위해 상세 메시지 대신 예외 클래스명만 돌려준다.
        return type(exc).__name__
