"""모든 ORM 모델의 부모 클래스(Base).

Alembic은 Base.metadata를 보고 테이블 변경사항을 감지한다.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
