"""기본 모드: 도서관 대출 정보를 실시간으로 알 수 없으므로 대출 상태·빈자리만으로 관리자 검토를 만들지 않는다.

(대출 상태를 믿는 모드는 loan_tracking fixture를 쓰는 기존 테스트에서 검증한다)
"""
from tests.conftest import ADMIN_HEADERS
from tests.test_verification import _analysis_complete, _finish, env, send  # noqa: F401  env는 fixture


def test_checked_out_copy_seen_on_shelf_is_not_reviewed(client, env):
    v = send(client, env, "T-01-01", copy_id="BOOK-900128")          # DB엔 대출 중, 실제로는 반납 후 정리됐을 수 있음
    assert (v["location_verdict"], v["workflow_status"]) == ("NORMAL", None)
    assert "대출 중으로 등록" in v["note"]
    assert client.get("/copies/BOOK-900128").json()["physical_location_certainty"] == "CONFIRMED"


def test_unobserved_books_are_only_recorded(client, env):
    _finish(client, env)
    res = _analysis_complete(client, env)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["unobserved_copy_count"] == 4 and body["review_required_count"] == 0

    rows = client.get("/verifications", params={"scan_session_id": env["session_id"], "limit": 100}).json()["items"]
    assert {(r["observation_status"], r["workflow_status"]) for r in rows} == {("NOT_OBSERVED", None)}
    assert client.get("/alerts", params={"alert_type": "NOT_OBSERVED"}, headers=ADMIN_HEADERS).json()["total"] == 0


def test_copy_without_registered_location_is_not_reviewed(client, env):
    book = client.post("/books", json={"title": "범위표 없는 책"}, headers=ADMIN_HEADERS).json()
    client.post(f"/books/{book['id']}/copies", json={"id": "EM9600001", "call_number": "811.16 김54ㄱ"},
                headers=ADMIN_HEADERS)
    v = send(client, env, "T-01-02", copy_id="EM9600001")
    assert (v["identification_status"], v["location_verdict"], v["workflow_status"]) == \
        ("IDENTIFIED", "UNDETERMINED", None)
    assert client.get("/alerts", headers=ADMIN_HEADERS).json()["total"] == 0


def test_same_book_candidates_without_location_need_no_reobservation(client, env):
    book = client.post("/books", json={"title": "복본 2권"}, headers=ADMIN_HEADERS).json()
    for copy_id, call in (("EM9600011", "811.16 김54ㄱ"), ("EM9600012", "811.16 김54ㄱ c.2")):
        client.post(f"/books/{book['id']}/copies", json={"id": copy_id, "call_number": call}, headers=ADMIN_HEADERS)
    v = send(client, env, "T-01-02", candidates=[("EM9600011", 0.7), ("EM9600012", 0.7)])   # c.2를 못 읽음
    assert (v["identification_status"], v["workflow_status"]) == ("CANDIDATE_ONLY", None)

    other = client.post("/books", json={"title": "다른 책"}, headers=ADMIN_HEADERS).json()
    client.post(f"/books/{other['id']}/copies", json={"id": "EM9600013", "call_number": "330.9 박12ㄱ"},
                headers=ADMIN_HEADERS)
    v = send(client, env, "T-01-02", candidates=[("EM9600011", 0.7), ("EM9600013", 0.7)])   # 서로 다른 책
    assert v["workflow_status"] == "REOBSERVATION_PENDING"                                 # 다시 보면 알 수 있음
