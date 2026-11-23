"""청구기호 흐름 검사: sequence_status에 DUPLICATE(겹쳐 찍힌 사진에서 다시 나온 책) 추가

Revision ID: a7d3e9c2b418
Revises: f2c9d1e7b345
Create Date: 2026-11-23 12:00:00

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a7d3e9c2b418'
down_revision: Union[str, Sequence[str], None] = 'f2c9d1e7b345'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 상태 코드 CHECK 제약 변경은 autogenerate가 감지하지 못하므로 직접 작성했다.
OLD = ("IN_ORDER", "OUT_OF_ORDER", "OUT_OF_PLACE", "UNIDENTIFIED")
NEW = OLD + ("DUPLICATE",)


def _replace_check(values: tuple[str, ...]) -> None:
    op.drop_constraint("ck_verification_results_sequence_status", "verification_results", type_="check")
    op.create_check_constraint("ck_verification_results_sequence_status", "verification_results",
                               "sequence_status IN (" + ", ".join(f"'{v}'" for v in values) + ")")


def upgrade() -> None:
    """Upgrade schema."""
    _replace_check(NEW)


def downgrade() -> None:
    """Downgrade schema. (DUPLICATE가 기록된 결과가 남아 있으면 실패하므로 먼저 비워야 한다)"""
    _replace_check(OLD)
