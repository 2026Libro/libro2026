"""외부(AI·로봇·프론트)와 주고받는 ID 형식 규칙.

[가정] 기획서 8장 예시(R001, A-03, A-03-02, BOOK-125, OBS-001, SCAN-001)를 기준으로 정했다.
팀원과 형식이 바뀌면 이 파일의 정규식만 고치면 된다.
"""
from typing import Annotated

from pydantic import StringConstraints

ROBOT_ID_PATTERN = r"^R\d{3,}$"                   # R001
SHELF_ID_PATTERN = r"^[A-Z]-\d{2}$"               # A-03 (구역-서가 번호)
SLOT_ID_PATTERN = r"^[A-Z]-\d{2}-\d{2}$"          # A-03-02 (서가 ID + 선반/칸 번호)
# 개별 복본 = 도서관 등록번호 (EM0174130, WM0012345). AI 팀과 합의 (2026-11-16).
# BOOK-125 형식은 기존 샘플 데이터·테스트 호환을 위해 함께 허용한다.
BOOK_COPY_ID_PATTERN = r"^(?:[A-Z]{2,4}\d{5,10}|BOOK-\d+)$"
OBSERVATION_ID_PATTERN = r"^OBS-[A-Za-z0-9_-]+$"  # OBS-001 (AI가 생성)
SCAN_SESSION_ID_PATTERN = r"^SCAN-[A-Za-z0-9_-]+$"
TASK_ID_PATTERN = r"^TASK-[A-Za-z0-9_-]+$"        # 백엔드가 생성

RobotId = Annotated[str, StringConstraints(pattern=ROBOT_ID_PATTERN)]
ShelfId = Annotated[str, StringConstraints(pattern=SHELF_ID_PATTERN)]
SlotId = Annotated[str, StringConstraints(pattern=SLOT_ID_PATTERN)]
BookCopyId = Annotated[str, StringConstraints(pattern=BOOK_COPY_ID_PATTERN)]
ObservationId = Annotated[str, StringConstraints(pattern=OBSERVATION_ID_PATTERN, max_length=64)]
ScanSessionId = Annotated[str, StringConstraints(pattern=SCAN_SESSION_ID_PATTERN, max_length=64)]
TaskId = Annotated[str, StringConstraints(pattern=TASK_ID_PATTERN, max_length=64)]


def slot_belongs_to_shelf(slot_id: str, shelf_id: str) -> bool:
    """A-03-02는 A-03 서가의 칸이다."""
    return slot_id.startswith(shelf_id + "-")
