"""도서관 장서 가져오기: 서지관리번호·발행연도·복본 청구기호·자료실, ISBN 중복 허용

Revision ID: b7d41a9c3e20
Revises: ec8c9bc1a5e5
Create Date: 2026-11-16 15:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d41a9c3e20'
down_revision: Union[str, Sequence[str], None] = 'ec8c9bc1a5e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('books', sa.Column('library_bib_id', sa.String(length=20), nullable=True))
    op.add_column('books', sa.Column('publication_year', sa.String(length=10), nullable=True))
    op.create_unique_constraint(op.f('uq_books_library_bib_id'), 'books', ['library_bib_id'])
    # 서로 다른 도서가 같은 ISBN을 쓰는 경우가 있어 UNIQUE를 일반 인덱스로 바꾼다.
    op.drop_constraint(op.f('uq_books_isbn'), 'books', type_='unique')
    op.create_index(op.f('ix_books_isbn'), 'books', ['isbn'], unique=False)

    op.add_column('book_copies', sa.Column('call_number', sa.String(length=100), nullable=True))
    op.add_column('book_copies', sa.Column('room', sa.String(length=50), nullable=True))
    op.create_index(op.f('ix_book_copies_call_number'), 'book_copies', ['call_number'], unique=False)
    op.create_index(op.f('ix_book_copies_room'), 'book_copies', ['room'], unique=False)


def downgrade() -> None:
    """Downgrade schema. (ISBN이 중복된 행이 있으면 UNIQUE 복원이 실패한다)"""
    op.drop_index(op.f('ix_book_copies_room'), table_name='book_copies')
    op.drop_index(op.f('ix_book_copies_call_number'), table_name='book_copies')
    op.drop_column('book_copies', 'room')
    op.drop_column('book_copies', 'call_number')

    op.drop_index(op.f('ix_books_isbn'), table_name='books')
    op.create_unique_constraint(op.f('uq_books_isbn'), 'books', ['isbn'])
    op.drop_constraint(op.f('uq_books_library_bib_id'), 'books', type_='unique')
    op.drop_column('books', 'publication_year')
    op.drop_column('books', 'library_bib_id')
