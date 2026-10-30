"""대시보드 요약 응답 형식. [가정] 프론트엔드 화면 구성에 맞춰 항목이 바뀔 수 있다."""
from pydantic import BaseModel

from app.schemas.common import UtcDatetime
from app.schemas.robot import RobotRead


class CopyStatusCounts(BaseModel):
    """복본별 '가장 최근 검증 결과' 기준 현황."""

    total: int
    normal: int
    misplaced_suspected: int
    misplaced_confirmed: int
    not_observed: int
    undetermined: int         # 최근 검증이 판정 불가
    never_verified: int       # 한 번도 검증된 적 없음 (아직 스캔 안 됨 또는 복본 미확정 관측만 있음)


class LoanCounts(BaseModel):
    available: int
    checked_out: int
    in_transit: int
    unknown: int


class PhysicalCounts(BaseModel):
    in_robot_gripper: int
    in_basket: int
    collected_by_staff: int


class WorkflowCounts(BaseModel):
    reobservation_pending: int
    review_required: int
    approved: int             # 인출 승인되어 진행 중


class TaskCounts(BaseModel):
    pending_approval: int
    pending: int
    accepted: int
    in_progress: int
    unknown: int


class DashboardSummary(BaseModel):
    generated_at: UtcDatetime
    books: int                       # 서지(제목) 수
    copies: CopyStatusCounts
    loans: LoanCounts
    physical: PhysicalCounts
    workflow: WorkflowCounts
    open_alerts: int
    open_alerts_by_type: dict[str, int]
    active_tasks: TaskCounts
    robots: list[RobotRead]
    basket_items: int                # 아직 회수하지 않은 바구니 도서
    last_scan_at: UtcDatetime | None
