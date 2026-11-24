"""3단계 DB 제약조건 테스트 (실제 MySQL 필요, alembic upgrade head 적용 후 실행).

db 픽스처(conftest.py)는 테스트가 끝나면 롤백되므로 DB에 데이터가 남지 않는다.
"""
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.enums import RobotTaskStatus, RobotTaskType
from app.models import BasketItem, Book, BookCopy, Observation, Robot, RobotTask, ScanSession, Shelf, ShelfSlot

NOW = datetime(2026, 10, 29, 2, 30)


@pytest.fixture(autouse=True)
def seed(db):
    _seed(db)


def _seed(s: Session) -> None:
    book = Book(title="테스트 도서")  # id를 직접 지정하면 롤백 후에도 AUTO_INCREMENT 값이 커지므로 자동 생성
    s.add_all([
        Shelf(id="T-01", zone="T"),
        ShelfSlot(id="T-01-01", shelf_id="T-01", position=1),
        book,
        Robot(id="R900"),
    ])
    s.flush()
    s.add_all([BookCopy(id="BOOK-900001", book_id=book.id), BookCopy(id="BOOK-900002", book_id=book.id)])
    s.flush()


def _retrieve_task(task_id: str, status: RobotTaskStatus, copy_id: str = "BOOK-900001") -> RobotTask:
    return RobotTask(
        id=task_id,
        task_type=RobotTaskType.RETRIEVE_MISPLACED_BOOK,
        status=status,
        robot_id="R900",
        shelf_id="T-01",
        slot_id="T-01-01",
        book_copy_id=copy_id,
        created_by="test",
    )


# ---------- 중복 인출 작업 방지 (원칙 12) ----------

@pytest.mark.parametrize("existing_status", [
    RobotTaskStatus.PENDING_APPROVAL,
    RobotTaskStatus.PENDING,
    RobotTaskStatus.ACCEPTED,
    RobotTaskStatus.IN_PROGRESS,
    RobotTaskStatus.UNKNOWN,  # 결과 불명이어도 새 인출 작업을 막아야 한다 (원칙 11)
])
def test_second_active_retrieval_for_same_copy_rejected(db, existing_status):
    db.add(_retrieve_task("TASK-T1", existing_status))
    db.flush()
    db.add(_retrieve_task("TASK-T2", RobotTaskStatus.PENDING_APPROVAL))
    with pytest.raises(IntegrityError):
        db.flush()


@pytest.mark.parametrize("finished_status", [
    RobotTaskStatus.COMPLETED,
    RobotTaskStatus.FAILED,
    RobotTaskStatus.CANCELLED,
])
def test_new_retrieval_allowed_after_previous_finished(db, finished_status):
    db.add(_retrieve_task("TASK-T1", finished_status))
    db.flush()
    db.add(_retrieve_task("TASK-T2", RobotTaskStatus.PENDING_APPROVAL))
    db.flush()


def test_active_retrievals_for_different_copies_allowed(db):
    db.add(_retrieve_task("TASK-T1", RobotTaskStatus.PENDING))
    db.add(_retrieve_task("TASK-T2", RobotTaskStatus.PENDING, copy_id="BOOK-900002"))
    db.flush()


def test_multiple_non_retrieval_tasks_allowed(db):
    for i in range(2):
        db.add(RobotTask(
            id=f"TASK-M{i}", task_type=RobotTaskType.MOVE_TO_SHELF, status=RobotTaskStatus.PENDING,
            robot_id="R900", shelf_id="T-01", book_copy_id="BOOK-900001", created_by="test",
        ))
    db.flush()


# ---------- 상태 코드 CHECK 제약 ----------

def test_db_rejects_undefined_status_value(db):
    # ORM 검증을 거치지 않고 SQL로 직접 넣어도 DB가 거부해야 한다.
    with pytest.raises(OperationalError):
        db.execute(text(
            "INSERT INTO robot_tasks (id, task_type, status, created_by, version, created_at, updated_at) "
            "VALUES ('TASK-X', 'SCAN_SHELF', 'DONE', 'test', 1, NOW(), NOW())"
        ))


# ---------- 작업 상태 동시 변경 방지 (BE-017) ----------

def test_stale_task_update_rejected(db):
    task = _retrieve_task("TASK-T1", RobotTaskStatus.PENDING)
    db.add(task)
    db.flush()

    # 다른 요청이 먼저 상태를 바꾼 상황
    db.execute(text("UPDATE robot_tasks SET status='ACCEPTED', version=version+1 WHERE id='TASK-T1'"))

    task.status = RobotTaskStatus.CANCELLED
    with pytest.raises(StaleDataError):
        db.flush()


# ---------- 중복 관측 / 바구니 ----------

def test_duplicate_observation_id_rejected(db):
    db.add(ScanSession(id="SCAN-T1", robot_id="R900", shelf_id="T-01"))
    db.flush()

    def make() -> Observation:
        return Observation(
            id="OBS-T1", scan_session_id="SCAN-T1", robot_id="R900", shelf_id="T-01", slot_id="T-01-01",
            detection_confidence=0.9, ocr_confidence=0.9, identification_confidence=0.9,
            captured_at=NOW, raw_payload={},
        )

    db.add(make())
    db.flush()
    db.expunge_all()
    db.add(make())
    with pytest.raises(IntegrityError):
        db.flush()


def test_same_copy_cannot_be_in_basket_twice_until_collected(db):
    db.add(_retrieve_task("TASK-T1", RobotTaskStatus.COMPLETED))
    db.flush()
    first = BasketItem(book_copy_id="BOOK-900001", robot_id="R900", task_id="TASK-T1", stored_at=NOW)
    db.add(first)
    db.flush()

    with pytest.raises(IntegrityError), db.begin_nested():  # 실패한 부분만 롤백
        db.add(BasketItem(book_copy_id="BOOK-900001", robot_id="R900", task_id="TASK-T1", stored_at=NOW))
        db.flush()

    # 직원이 회수한 뒤에는 다시 보관 기록을 만들 수 있다.
    first.collected_at = NOW
    db.flush()
    db.add(BasketItem(book_copy_id="BOOK-900001", robot_id="R900", task_id="TASK-T1", stored_at=NOW))
    db.flush()
