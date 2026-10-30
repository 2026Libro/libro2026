"""ORM 모델 모음.

3단계(DB 설계)에서 books, shelves 등의 모델을 추가하면 여기서 import 해야
Alembic autogenerate가 테이블을 인식한다.
"""
from app.db.base import Base

__all__ = ["Base"]
