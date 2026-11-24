"""대시보드 요약 (BE-016)."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import (
    AlertStatus,
    LoanStatus,
    LocationVerdict,
    ObservationStatus,
    PhysicalLocationType,
    RobotTaskStatus,
    WorkflowStatus,
)
from app.db.base import utcnow
from app.models import Alert, BasketItem, Book, BookCopy, RobotTask, ScanSession, VerificationResult
from app.schemas.dashboard import (
    CopyStatusCounts,
    DashboardSummary,
    LoanCounts,
    PhysicalCounts,
    TaskCounts,
    WorkflowCounts,
)
from app.services import robot as robot_service


def _count_by(db: Session, column, *where) -> dict:
    return dict(db.execute(select(column, func.count()).where(*where).group_by(column)).all())


def _copy_status(db: Session) -> CopyStatusCounts:
    """복본마다 가장 최근 검증 결과 1건을 골라 집계한다."""
    latest_ids = (select(func.max(VerificationResult.id))
                  .where(VerificationResult.book_copy_id.is_not(None))
                  .group_by(VerificationResult.book_copy_id))
    latest = db.execute(select(VerificationResult.location_verdict, VerificationResult.observation_status)
                        .where(VerificationResult.id.in_(latest_ids))).all()

    counts = {"normal": 0, "misplaced_suspected": 0, "misplaced_confirmed": 0, "not_observed": 0, "undetermined": 0}
    for verdict, obs_status in latest:
        if obs_status is ObservationStatus.NOT_OBSERVED:
            counts["not_observed"] += 1
        elif verdict is LocationVerdict.NORMAL:
            counts["normal"] += 1
        elif verdict is LocationVerdict.MISPLACED_SUSPECTED:
            counts["misplaced_suspected"] += 1
        elif verdict is LocationVerdict.MISPLACED_CONFIRMED:
            counts["misplaced_confirmed"] += 1
        else:
            counts["undetermined"] += 1  # SCAN_INCOMPLETE 포함

    total = db.scalar(select(func.count()).select_from(BookCopy))
    return CopyStatusCounts(total=total, never_verified=total - len(latest), **counts)


def get_summary(db: Session, offline_after_seconds: int) -> DashboardSummary:
    loans = _count_by(db, BookCopy.loan_status)
    physical = _count_by(db, BookCopy.physical_location_type)
    workflow = _count_by(db, VerificationResult.workflow_status)
    tasks = _count_by(db, RobotTask.status)
    alerts = _count_by(db, Alert.alert_type, Alert.status.in_((AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED)))

    return DashboardSummary(
        generated_at=utcnow(),
        books=db.scalar(select(func.count()).select_from(Book)),
        copies=_copy_status(db),
        loans=LoanCounts(
            available=loans.get(LoanStatus.AVAILABLE, 0), checked_out=loans.get(LoanStatus.CHECKED_OUT, 0),
            in_transit=loans.get(LoanStatus.IN_TRANSIT, 0), unknown=loans.get(LoanStatus.UNKNOWN, 0),
        ),
        physical=PhysicalCounts(
            in_robot_gripper=physical.get(PhysicalLocationType.ROBOT_GRIPPER, 0),
            in_basket=physical.get(PhysicalLocationType.BASKET, 0),
            collected_by_staff=physical.get(PhysicalLocationType.COLLECTED_BY_STAFF, 0),
        ),
        workflow=WorkflowCounts(
            reobservation_pending=workflow.get(WorkflowStatus.REOBSERVATION_PENDING, 0),
            review_required=workflow.get(WorkflowStatus.REVIEW_REQUIRED, 0),
            approved=workflow.get(WorkflowStatus.APPROVED, 0),
        ),
        open_alerts=sum(alerts.values()),
        open_alerts_by_type={str(k): v for k, v in alerts.items()},
        active_tasks=TaskCounts(
            pending_approval=tasks.get(RobotTaskStatus.PENDING_APPROVAL, 0), pending=tasks.get(RobotTaskStatus.PENDING, 0),
            accepted=tasks.get(RobotTaskStatus.ACCEPTED, 0), in_progress=tasks.get(RobotTaskStatus.IN_PROGRESS, 0),
            unknown=tasks.get(RobotTaskStatus.UNKNOWN, 0),
        ),
        robots=[robot_service.to_read(r, offline_after_seconds) for r in robot_service.list_robots(db)],
        basket_items=db.scalar(select(func.count()).select_from(BasketItem).where(BasketItem.collected_at.is_(None))),
        last_scan_at=db.scalar(select(func.max(ScanSession.started_at))),
    )
