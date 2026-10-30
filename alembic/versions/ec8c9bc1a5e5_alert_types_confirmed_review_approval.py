"""alert types: confirmed, review, approval

Revision ID: ec8c9bc1a5e5
Revises: 6f2e194c1bf8
Create Date: 2026-10-30 11:59:50.304874

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ec8c9bc1a5e5'
down_revision: Union[str, Sequence[str], None] = '6f2e194c1bf8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 상태 코드 CHECK 제약 변경은 autogenerate가 감지하지 못하므로 직접 작성했다.
OLD_TYPES = ("MISPLACED_SUSPECTED", "RECOGNITION_FAILED", "NOT_OBSERVED", "TASK_FAILED", "TASK_RESULT_UNKNOWN",
             "COMMUNICATION_ERROR")
NEW_TYPES = ("MISPLACED_SUSPECTED", "MISPLACED_CONFIRMED", "RECOGNITION_FAILED", "NOT_OBSERVED", "REVIEW_REQUIRED",
             "RETRIEVAL_APPROVAL_REQUIRED", "TASK_FAILED", "TASK_RESULT_UNKNOWN", "COMMUNICATION_ERROR")
NAME = "ck_alerts_alert_type"


def _replace_check(types: tuple[str, ...]) -> None:
    op.drop_constraint(NAME, "alerts", type_="check")
    op.create_check_constraint(NAME, "alerts", "alert_type IN (" + ", ".join(f"'{t}'" for t in types) + ")")


def upgrade() -> None:
    """Upgrade schema."""
    _replace_check(NEW_TYPES)


def downgrade() -> None:
    """Downgrade schema. (새 종류의 알림이 남아 있으면 실패하므로 먼저 지워야 한다)"""
    _replace_check(OLD_TYPES)
