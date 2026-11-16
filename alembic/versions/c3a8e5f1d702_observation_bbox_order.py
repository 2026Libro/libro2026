"""관측에 이미지 안 위치 추가: bbox, 왼쪽부터 순번, 원본 이미지 크기

Revision ID: c3a8e5f1d702
Revises: b7d41a9c3e20
Create Date: 2026-11-16 16:10:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3a8e5f1d702'
down_revision: Union[str, Sequence[str], None] = 'b7d41a9c3e20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('observations', sa.Column('bbox', sa.JSON(), nullable=True))
    op.add_column('observations', sa.Column('order_in_image', sa.Integer(), nullable=True))
    op.add_column('observations', sa.Column('image_width', sa.Integer(), nullable=True))
    op.add_column('observations', sa.Column('image_height', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('observations', 'image_height')
    op.drop_column('observations', 'image_width')
    op.drop_column('observations', 'order_in_image')
    op.drop_column('observations', 'bbox')
