"""2단계 공통 데이터 규격 테스트 (DB 없이 실행된다)."""
import copy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.enums import LocationVerdict, RobotTaskType, WorkflowStatus
from app.core.errors import AppError, register_exception_handlers
from app.schemas.common import ErrorCode
from app.schemas.observation import ObservationCreate
from app.schemas.robot_task import RobotTaskCommand

# 기획서 8장 예시 그대로
SAMPLE_OBSERVATION = {
    "observation_id": "OBS-001",
    "scan_session_id": "SCAN-001",
    "robot_id": "R001",
    "shelf_id": "A-03",
    "slot_id": "A-03-02",
    "detected_text": "파이썬 프로그래밍",
    "book_copy_id": None,
    "confidence": {"detection": 0.98, "ocr": 0.91, "identification": 0.75},
    "candidates": [
        {"book_copy_id": "BOOK-125", "score": 0.75},
        {"book_copy_id": "BOOK-126", "score": 0.72},
    ],
    "reobservation_of": None,
    "image_ref": "images/OBS-001.jpg",
    "captured_at": "2026-10-29T11:30:00+09:00",
}


def obs(**changes) -> dict:
    data = copy.deepcopy(SAMPLE_OBSERVATION)
    data.update(changes)
    return data


# ---------- 상태 코드 ----------

def test_review_required_is_workflow_not_location_verdict():
    assert "REVIEW_REQUIRED" in WorkflowStatus.__members__
    assert "REVIEW_REQUIRED" not in LocationVerdict.__members__


# ---------- AI 관측 데이터 ----------

def test_sample_observation_is_valid():
    o = ObservationCreate.model_validate(SAMPLE_OBSERVATION)
    assert o.book_copy_id is None  # 복본 미확정 상태 유지
    assert len(o.candidates) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"confidence": {"detection": 1.2, "ocr": 0.9, "identification": 0.7}},  # 범위 초과
        {"captured_at": "2026-10-29T11:30:00"},                                   # 시간대 없음
        {"slot_id": "B-01-02"},                                                   # 다른 서가의 칸
        {"shelf_id": "A03"},                                                      # ID 형식 오류
        {"candidates": [{"book_copy_id": "BOOK-1", "score": 0.5}] * 2},           # 후보 중복
        {"reobservation_of": "OBS-001"},                                          # 자기 자신 참조
        {"unknown_field": 1},                                                     # 정의되지 않은 필드
    ],
)
def test_invalid_observation_rejected(changes):
    with pytest.raises(ValidationError):
        ObservationCreate.model_validate(obs(**changes))


def test_observation_requires_confidence():
    data = obs()
    del data["confidence"]
    with pytest.raises(ValidationError):
        ObservationCreate.model_validate(data)


# ---------- 로봇 작업 명령 ----------

def task(**fields) -> dict:
    return {"task_id": "TASK-001", "robot_id": "R001", "issued_at": "2026-10-29T12:00:00+09:00", **fields}


def test_retrieve_command_valid():
    cmd = RobotTaskCommand.model_validate(
        task(task_type="RETRIEVE_MISPLACED_BOOK", shelf_id="A-03", slot_id="A-03-02", book_copy_id="BOOK-125")
    )
    assert cmd.task_type is RobotTaskType.RETRIEVE_MISPLACED_BOOK


@pytest.mark.parametrize(
    "fields",
    [
        {"task_type": "RETRIEVE_MISPLACED_BOOK", "shelf_id": "A-03", "slot_id": "A-03-02"},  # 대상 도서 없음
        {"task_type": "SCAN_SHELF", "shelf_id": "A-03"},                                    # 스캔 세션 없음
        {"task_type": "REOBSERVE_BOOK", "shelf_id": "A-03", "slot_id": "A-03-02"},          # 원 관측 없음
        {"task_type": "CANCEL_TASK"},                                                       # 취소 대상 없음
        {"task_type": "CANCEL_TASK", "target_task_id": "TASK-001"},                         # 자기 자신 취소
        {"task_type": "MOVE_TO_SHELF", "shelf_id": "A-03", "slot_id": "B-01-01"},           # 서가·칸 불일치
        {"task_type": "FLY_AWAY", "shelf_id": "A-03"},                                      # 없는 작업 종류
    ],
)
def test_invalid_task_command_rejected(fields):
    with pytest.raises(ValidationError):
        RobotTaskCommand.model_validate(task(**fields))


# ---------- 공통 오류 응답 ----------

@pytest.fixture
def error_client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/echo")
    def echo(body: ObservationCreate):
        return {"ok": True}

    @app.get("/conflict")
    def conflict():
        raise AppError(ErrorCode.DUPLICATE_REQUEST, "이미 수신한 관측입니다.", status_code=409)

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret-db-host:3306")

    return TestClient(app, raise_server_exceptions=False)


def test_validation_error_format(error_client):
    res = error_client.post("/echo", json=obs(confidence={"detection": 2, "ocr": 0.9, "identification": 0.7}))
    assert res.status_code == 422
    err = res.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert any(d["field"] == "confidence.detection" for d in err["details"])


def test_app_error_format(error_client):
    res = error_client.get("/conflict")
    assert res.status_code == 409
    assert res.json() == {"error": {"code": "DUPLICATE_REQUEST", "message": "이미 수신한 관측입니다.", "details": None}}


def test_not_found_uses_common_format(error_client):
    res = error_client.get("/no-such-path")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_unhandled_error_hides_internal_details(error_client):
    res = error_client.get("/boom")
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret-db-host" not in res.text
