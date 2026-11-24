"""청구기호 흐름 검사: 검증 결과 sequence_status, 알림 종류 OUT_OF_PLACE(엉뚱한 책)

Revision ID: e4b7c2d9a613
Revises: d9f2b6a4c815
Create Date: 2026-11-16 17:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4b7c2d9a613'
down_revision: Union[str, Sequence[str], None] = 'd9f2b6a4c815'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 상태 코드 CHECK 제약 변경은 autogenerate가 감지하지 못하므로 직접 작성했다.
OLD_ALERT_TYPES = ("MISPLACED_SUSPECTED", "MISPLACED_CONFIRMED", "RECOGNITION_FAILED", "NOT_OBSERVED",
                   "REVIEW_REQUIRED", "RETRIEVAL_APPROVAL_REQUIRED", "TASK_FAILED", "TASK_RESULT_UNKNOWN",
                   "COMMUNICATION_ERROR")
NEW_ALERT_TYPES = OLD_ALERT_TYPES[:2] + ("OUT_OF_PLACE",) + OLD_ALERT_TYPES[2:]
SEQUENCE = ("IN_ORDER", "OUT_OF_ORDER", "OUT_OF_PLACE", "UNIDENTIFIED")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _replace_alert_check(types: tuple[str, ...]) -> None:
    op.drop_constraint("ck_alerts_alert_type", "alerts", type_="check")
    op.create_check_constraint("ck_alerts_alert_type", "alerts", _in("alert_type", types))


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('verification_results', sa.Column('sequence_status', sa.String(length=32), nullable=True))
    op.create_check_constraint('ck_verification_results_sequence_status', 'verification_results',
                               _in("sequence_status", SEQUENCE))
    _replace_alert_check(NEW_ALERT_TYPES)


def downgrade() -> None:
    """Downgrade schema. (OUT_OF_PLACE 알림이 남아 있으면 실패하므로 먼저 지워야 한다)"""
    _replace_alert_check(OLD_ALERT_TYPES)
    op.drop_constraint('ck_verification_results_sequence_status', 'verification_results', type_='check')
    op.drop_column('verification_results', 'sequence_status')
