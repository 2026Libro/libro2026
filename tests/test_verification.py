"""6단계 도서 식별·위치 검증·미관측 판정 테스트."""
from datetime import datetime

import pytest

from app.core.config import get_settings
from app.core.enums import DeviceType, IdentificationStatus
from app.main import app
from app.models import BookCopy, Observation, ObservationCandidate, Robot
from app.services.device import issue_api_key
from app.services.verification import Thresholds, identify
from tests.conftest import ADMIN_HEADERS

TH = Thresholds(min_detection=0.5, min_ocr=0.6, min_identification=0.8, max_reobservations=2)


# ---------- 1. 식별 규칙 (DB 없이) ----------

def _obs(copy_id=None, candidates=(), text="제목", det=0.95, ocr=0.9, ident=0.9) -> Observation:
    return Observation(
        reported_book_copy_id=copy_id, detected_text=text,
        detection_confidence=det, ocr_confidence=ocr, identification_confidence=ident,
        candidates=[ObservationCandidate(book_copy_id=c, score=0.99) for c in candidates],
    )


@pytest.mark.parametrize("obs, expected", [
    (_obs(copy_id="BOOK-1"), IdentificationStatus.IDENTIFIED),
    (_obs(copy_id="BOOK-1", ident=0.79), IdentificationStatus.UNCERTAIN),          # 식별 신뢰도 부족
    (_obs(copy_id="BOOK-1", ocr=0.5), IdentificationStatus.UNCERTAIN),             # OCR 신뢰도 부족
    (_obs(candidates=["BOOK-1"]), IdentificationStatus.CANDIDATE_ONLY),            # 후보 점수 0.99여도 확정 안 함
    (_obs(candidates=["BOOK-1"], ocr=0.3), IdentificationStatus.UNCERTAIN),
    (_obs(), IdentificationStatus.UNREGISTERED),                                   # 잘 읽었는데 후보 없음
    (_obs(text=None, ocr=0.1), IdentificationStatus.FAILED),
    (_obs(copy_id="BOOK-1", det=0.3), IdentificationStatus.FAILED),                # 탐지 자체가 불확실
])
def test_identify_rules(obs, expected):
    assert identify(obs, TH) is expected


# ---------- 2. API 흐름 ----------

@pytest.fixture
def env(client, db):
    """
    T-01-01: BOOK-900128 (대출 중)
    T-01-02: BOOK-900125, BOOK-900126 (같은 제목 복본)
    T-01-03: BOOK-900127
    T-02-01: BOOK-900201 (다른 서가)
    """
    db.add(Robot(id="R900"))
    db.commit()
    for shelf_id, n in (("T-01", 3), ("T-02", 2)):
        client.post("/shelves", json={"id": shelf_id, "slot_count": n}, headers=ADMIN_HEADERS)
    book = client.post("/books", json={"title": "테스트 도서"}, headers=ADMIN_HEADERS).json()
    for copy_id, slot_id, loan in [("BOOK-900128", "T-01-01", "CHECKED_OUT"), ("BOOK-900125", "T-01-02", "AVAILABLE"),
                                   ("BOOK-900126", "T-01-02", "AVAILABLE"), ("BOOK-900127", "T-01-03", "AVAILABLE"),
                                   ("BOOK-900201", "T-02-01", "AVAILABLE")]:
        client.post(f"/books/{book['id']}/copies", json={"id": copy_id, "slot_id": slot_id, "loan_status": loan},
                    headers=ADMIN_HEADERS)
    session_id = client.post("/scan-sessions", json={"robot_id": "R900", "shelf_id": "T-01"},
                             headers=ADMIN_HEADERS).json()["id"]
    return {
        "session_id": session_id,
        "ai": {"X-API-Key": issue_api_key(db, DeviceType.AI, "ai-test")},
        "robot": {"X-API-Key": issue_api_key(db, DeviceType.ROBOT, "R900")},
    }


_counter = iter(range(1, 10_000))


def payload(env, slot_id, copy_id=None, candidates=(), text="테스트 도서", ocr=0.9, ident=0.9,
            reobservation_of=None, captured_at="2026-10-29T11:30:00+09:00", obs_id=None) -> dict:
    return {
        "observation_id": obs_id or f"OBS-V-{next(_counter):04d}", "scan_session_id": env["session_id"],
        "robot_id": "R900", "shelf_id": "T-01", "slot_id": slot_id, "detected_text": text, "book_copy_id": copy_id,
        "confidence": {"detection": 0.95, "ocr": ocr, "identification": ident},
        "candidates": [{"book_copy_id": c, "score": s} for c, s in candidates],
        "reobservation_of": reobservation_of, "image_ref": None, "captured_at": captured_at,
    }


def send(client, env, slot_id, **kwargs) -> dict:
    """관측을 보내고 그 관측의 검증 결과를 돌려준다."""
    data = payload(env, slot_id, **kwargs)
    res = client.post("/observations", headers=env["ai"], json=data)
    assert res.status_code == 201, res.text
    page = client.get("/verifications", params={"scan_session_id": env["session_id"], "limit": 100}).json()
    return next(v for v in page["items"] if v["observation_id"] == data["observation_id"])


def copy_state(client, copy_id) -> dict:
    return client.get(f"/copies/{copy_id}").json()


def test_identified_at_registered_slot_is_normal(client, env):
    v = send(client, env, "T-01-03", copy_id="BOOK-900127")
    assert (v["identification_status"], v["location_verdict"], v["workflow_status"]) == ("IDENTIFIED", "NORMAL", None)

    copy = copy_state(client, "BOOK-900127")
    assert copy["physical_location_certainty"] == "CONFIRMED"   # 로봇 카메라로 확인했으므로 확정
    assert copy["physical_location_confirmed_at"] == "2026-10-29T02:30:00Z"


def test_identified_at_other_slot_is_only_suspected(client, env):
    v = send(client, env, "T-01-03", copy_id="BOOK-900201")
    assert v["location_verdict"] == "MISPLACED_SUSPECTED"       # 첫 관측에서는 확정하지 않음
    assert v["workflow_status"] == "REOBSERVATION_PENDING"
    assert (v["registered_slot_id"], v["observed_slot_id"]) == ("T-02-01", "T-01-03")

    copy = copy_state(client, "BOOK-900201")
    assert copy["registered_slot_id"] == "T-02-01"              # 등록 위치는 관측으로 바뀌지 않음
    assert copy["physical_slot_id"] == "T-01-03"                # 실제 있는 곳은 관측된 칸


def test_reobservation_confirms_misplacement(client, env):
    first = send(client, env, "T-01-03", copy_id="BOOK-900201")
    second = send(client, env, "T-01-03", copy_id="BOOK-900201", reobservation_of=first["observation_id"])
    assert second["location_verdict"] == "MISPLACED_CONFIRMED"
    assert second["workflow_status"] == "REVIEW_REQUIRED"
    assert second["reobservation_count"] == 1

    first_now = client.get(f"/verifications/{first['id']}").json()
    assert first_now["workflow_status"] == "CLOSED"             # 재관측 결과로 대체됨


def test_reobservation_with_different_result_is_not_confirmed(client, env):
    first = send(client, env, "T-01-03", copy_id="BOOK-900201")
    second = send(client, env, "T-01-03", candidates=[("BOOK-900201", 0.6)], ocr=0.4,
                  reobservation_of=first["observation_id"])
    assert second["location_verdict"] == "UNDETERMINED"
    assert second["workflow_status"] == "REOBSERVATION_PENDING"


def test_low_confidence_identification_not_trusted(client, env):
    v = send(client, env, "T-01-01", copy_id="BOOK-900201", ident=0.5)
    assert (v["identification_status"], v["location_verdict"]) == ("UNCERTAIN", "UNDETERMINED")
    assert v["book_copy_id"] is None
    assert copy_state(client, "BOOK-900201")["physical_location_certainty"] == "ESTIMATED"  # 갱신 안 함


def test_same_title_candidates_at_right_slot(client, env):
    v = send(client, env, "T-01-02", candidates=[("BOOK-900125", 0.75), ("BOOK-900126", 0.72)])
    assert (v["identification_status"], v["location_verdict"]) == ("CANDIDATE_ONLY", "NORMAL")
    assert v["book_copy_id"] is None                            # 복본은 확정하지 않음
    assert copy_state(client, "BOOK-900125")["physical_location_certainty"] == "ESTIMATED"


def test_single_high_score_candidate_still_not_identified(client, env):
    v = send(client, env, "T-01-03", candidates=[("BOOK-900127", 0.99)])
    assert v["identification_status"] == "CANDIDATE_ONLY"
    assert v["book_copy_id"] is None


def test_candidates_all_registered_elsewhere_suspected(client, env):
    v = send(client, env, "T-01-01", candidates=[("BOOK-900125", 0.8), ("BOOK-900126", 0.7)])
    assert v["location_verdict"] == "MISPLACED_SUSPECTED"
    assert v["workflow_status"] == "REOBSERVATION_PENDING"


def test_unregistered_book_goes_to_review(client, env):
    v = send(client, env, "T-01-01", text="DB에 없는 책")
    assert v["identification_status"] == "UNREGISTERED"
    assert v["workflow_status"] == "REVIEW_REQUIRED"


def test_repeated_failure_goes_to_review(client, env):
    v = send(client, env, "T-01-01", text=None, ocr=0.1)
    assert (v["identification_status"], v["workflow_status"]) == ("FAILED", "REOBSERVATION_PENDING")
    for expected in ("REOBSERVATION_PENDING", "REVIEW_REQUIRED"):  # 재관측 1회, 2회
        v = send(client, env, "T-01-01", text=None, ocr=0.1, reobservation_of=v["observation_id"])
        assert v["workflow_status"] == expected
    assert v["reobservation_count"] == 2


def test_checked_out_copy_seen_on_shelf_goes_to_review(client, env):
    v = send(client, env, "T-01-01", copy_id="BOOK-900128")
    assert v["location_verdict"] == "NORMAL"
    assert v["workflow_status"] == "REVIEW_REQUIRED"
    assert "대출 중" in v["note"]


def test_resend_does_not_verify_twice(client, env):
    data = payload(env, "T-01-03", copy_id="BOOK-900127")
    assert client.post("/observations", headers=env["ai"], json=data).status_code == 201
    assert client.post("/observations", headers=env["ai"], json=data).status_code == 200
    page = client.get("/verifications", params={"scan_session_id": env["session_id"]}).json()
    assert page["total"] == 1


def test_older_observation_does_not_overwrite_newer_physical_location(client, env):
    send(client, env, "T-01-03", copy_id="BOOK-900201", captured_at="2026-10-29T12:00:00+09:00")
    send(client, env, "T-01-01", copy_id="BOOK-900201", captured_at="2026-10-29T11:00:00+09:00")  # 늦게 도착한 옛 관측
    assert copy_state(client, "BOOK-900201")["physical_slot_id"] == "T-01-03"


def test_threshold_comes_from_settings(client, env):
    lenient = get_settings().model_copy(update={"admin_tokens": "tester:test-admin-token", "verify_min_identification": 0.7})
    app.dependency_overrides[get_settings] = lambda: lenient
    v = send(client, env, "T-01-03", copy_id="BOOK-900127", ident=0.75)
    assert v["identification_status"] == "IDENTIFIED"


# ---------- 3. 미관측 판정 ----------

def _finish(client, env, status="COMPLETED"):
    res = client.post(f"/scan-sessions/{env['session_id']}/finish", json={"status": status}, headers=env["robot"])
    assert res.status_code == 200


def _analysis_complete(client, env):
    return client.post(f"/scan-sessions/{env['session_id']}/analysis-complete", headers=env["ai"])


def test_unobserved_after_completed_scan(client, db, env):
    send(client, env, "T-01-03", copy_id="BOOK-900127")
    _finish(client, env)

    res = _analysis_complete(client, env)
    assert res.status_code == 200
    body = res.json()
    assert (body["registered_copy_count"], body["unobserved_copy_count"], body["review_required_count"]) == (4, 3, 2)

    rows = client.get("/verifications", params={"scan_session_id": env["session_id"],
                                                "observation_status": "NOT_OBSERVED"}).json()["items"]
    by_copy = {r["book_copy_id"]: r for r in rows}
    assert set(by_copy) == {"BOOK-900125", "BOOK-900126", "BOOK-900128"}
    assert by_copy["BOOK-900125"]["workflow_status"] == "REVIEW_REQUIRED"
    assert by_copy["BOOK-900128"]["workflow_status"] is None     # 대출 중 → 검토 불필요
    assert "분실 처리 아님" in by_copy["BOOK-900125"]["note"]

    # 미관측이어도 대출 상태·위치 정보는 자동으로 바꾸지 않는다 (분실 처리 금지)
    db.expire_all()
    assert db.get(BookCopy, "BOOK-900125").loan_status == "AVAILABLE"


def test_candidate_mention_counts_as_possibly_seen(client, env):
    send(client, env, "T-01-02", candidates=[("BOOK-900125", 0.75), ("BOOK-900126", 0.72)])
    _finish(client, env)
    body = _analysis_complete(client, env).json()
    assert body["unobserved_copy_count"] == 2  # 125·126은 후보로 언급됨 → 미관측 아님 (127, 128만)


def test_incomplete_scan_does_not_flag_unobserved(client, env):
    _finish(client, env, "INCOMPLETE")
    body = _analysis_complete(client, env).json()
    assert body["observation_status"] == "SCAN_INCOMPLETE"
    assert body["review_required_count"] == 0


@pytest.mark.parametrize("finish_first, expected", [(False, 409), (True, 200)])
def test_analysis_complete_requires_finished_scan(client, env, finish_first, expected):
    if finish_first:
        _finish(client, env)
    assert _analysis_complete(client, env).status_code == expected


def test_analysis_complete_only_once(client, env):
    _finish(client, env)
    _analysis_complete(client, env)
    assert _analysis_complete(client, env).status_code == 409


def test_analysis_complete_requires_ai_key(client, env):
    _finish(client, env)
    res = client.post(f"/scan-sessions/{env['session_id']}/analysis-complete", headers=env["robot"])
    assert res.status_code == 403


def test_late_observation_closes_unobserved_row(client, env):
    _finish(client, env)
    _analysis_complete(client, env)
    send(client, env, "T-01-03", copy_id="BOOK-900127")  # AI 결과가 뒤늦게 도착

    rows = client.get("/verifications", params={"book_copy_id": "BOOK-900127",
                                                "observation_status": "NOT_OBSERVED"}).json()["items"]
    assert rows[0]["workflow_status"] == "CLOSED"
    assert "확인됨" in rows[0]["note"]
