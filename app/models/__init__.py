"""ORM 모델 모음.

새 모델 파일을 만들면 여기서 import 해야 Alembic autogenerate가 테이블을 인식한다.
"""
from app.db.base import Base
from app.models.admin import AdminAction, Alert
from app.models.book import Book, BookCopy, BookLocation, BookLocationHistory
from app.models.device import DeviceCredential
from app.models.observation import Observation, ObservationCandidate, ScanImage, ScanSession, VerificationResult
from app.models.robot import BasketItem, Robot, RobotTask, TaskEvent
from app.models.shelf import Shelf, ShelfSlot, SlotCallRange, SlotCallRangeHistory

__all__ = [
    "Base",
    "AdminAction",
    "Alert",
    "BasketItem",
    "Book",
    "BookCopy",
    "BookLocation",
    "BookLocationHistory",
    "DeviceCredential",
    "Observation",
    "ObservationCandidate",
    "Robot",
    "RobotTask",
    "ScanImage",
    "ScanSession",
    "Shelf",
    "ShelfSlot",
    "SlotCallRange",
    "SlotCallRangeHistory",
    "TaskEvent",
    "VerificationResult",
]
