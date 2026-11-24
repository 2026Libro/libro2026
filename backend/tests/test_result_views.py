"""프론트 결과 화면용 조회: 대표 배지, 제목, 대체 결과, 칸 필터, 관측 이미지."""
import pytest

from app.core.config import get_settings
from app.core.enums import IdentificationStatus as I
from app.core.enums import LocationVerdict as L
from app.core.enums import ObservationStatus as O
from app.core.enums import ResultBadge as B
from app.main import app
from app.models import VerificationResult
from app.services.verification import result_badge
from tests.test_verification import _analysis_complete, _finish, env, payload, send  # noqa: F401  env는 fixture


# ---------- 1. 대표 배지 (DB 없이) ----------

@pytest.mark.parametrize("obs_status, ident, verdict, expected", [
    (O.OBSERVED, I.IDENTIFIED, L.MISPLACED_CONFIRMED, B.MISPLACED_CONFIRMED),
    (O.OBSERVED, I.IDENTIFIED, L.MISPLACED_SUSPECTED, B.MISPLACED_SUSPECTED),
    (O.OBSERVED, I.CANDIDATE_ONLY, L.MISPLACED_SUSPECTED, B.MISPLACED_SUSPECTED),
    (O.NOT_OBSERVED, None, L.UNDETERMINED, B.NOT_OBSERVED),
    (O.SCAN_INCOMPLETE, None, L.UNDETERMINED, B.SCAN_INCOMPLETE),
    (O.OBSERVED, I.FAILED, L.UNDETERMINED, B.RECOGNITION_ISSUE),
    (O.OBSERVED, I.UNCERTAIN, L.UNDETERMINED, B.RECOGNITION_ISSUE),
    (O.OBSERVED, I.UNREGISTERED, L.UNDETERMINED, B.RECOGNITION_ISSUE),
    (O.OBSERVED, I.CANDIDATE_ONLY, L.UNDETERMINED, B.LOCATION_UNDETERMINED),  # 후보가 여러 칸에 걸침
    (O.OBSERVED, I.IDENTIFIED, L.UNDETERMINED, B.LOCATION_UNDETERMINED),      # 등록 위치 없는 복본
    (O.OBSERVED, I.IDENTIFIED, L.NORMAL, B.NORMAL),
    (O.OBSERVED, I.CANDIDATE_ONLY, L.NORMAL, B.NORMAL),                      # 후보 모두 이 칸 소속
])
def test_result_badge(obs_status, ident, verdict, expected):
    row = VerificationResult(observation_status=obs_status, identification_status=ident, location_verdict=verdict)
    assert result_badge(row) == expected


# ---------- 2. 목록 응답 값 ----------

def test_title_and_badge_in_response(client, env):
    v = send(client, env, "T-01-03", copy_id="BOOK-900127")
    assert (v["title"], v["badge"], v["superseded_by"]) == ("테스트 도서", "NORMAL", None)

    unknown = send(client, env, "T-01-01", text=None, ocr=0.1)
    assert (unknown["title"], unknown["badge"]) == (None, "RECOGNITION_ISSUE")


def test_candidates_across_slots_is_location_undetermined(client, env):
    v = send(client, env, "T-01-02", candidates=[("BOOK-900125", 0.75), ("BOOK-900127", 0.7)])
    assert (v["identification_status"], v["location_verdict"], v["badge"]) == \
        ("CANDIDATE_ONLY", "UNDETERMINED", "LOCATION_UNDETERMINED")


def test_reobservation_marks_previous_as_superseded(client, env):
    first = send(client, env, "T-01-03", copy_id="BOOK-900201")
    second = send(client, env, "T-01-03", copy_id="BOOK-900201", reobservation_of=first["observation_id"])

    assert client.get(f"/verifications/{first['id']}").json()["superseded_by"] == second["id"]
    assert second["superseded_by"] is None

    params = {"scan_session_id": env["session_id"], "latest_only": "true"}
    ids = [v["id"] for v in client.get("/verifications", params=params).json()["items"]]
    assert second["id"] in ids and first["id"] not in ids


def test_late_observation_supersedes_unobserved_row(client, env):
    _finish(client, env)
    _analysis_complete(client, env)
    late = send(client, env, "T-01-03", copy_id="BOOK-900127")

    params = {"scan_session_id": env["session_id"], "book_copy_id": "BOOK-900127", "limit": 100}
    rows = {r["observation_status"]: r for r in client.get("/verifications", params=params).json()["items"]}
    assert rows["NOT_OBSERVED"]["superseded_by"] == late["id"]

    latest = client.get("/verifications", params={**params, "latest_only": "true"}).json()
    assert [r["id"] for r in latest["items"]] == [late["id"]]
    assert latest["total"] == 1


def test_slot_filter_uses_observed_or_registered_slot(client, env):
    send(client, env, "T-01-03", copy_id="BOOK-900201")       # 다른 서가 책이 T-01-03에서 관측
    _finish(client, env)
    _analysis_complete(client, env)                            # 125·126(T-01-02), 128(T-01-01), 127(T-01-03) 미관측

    def slot(slot_id):
        page = client.get("/verifications", params={"scan_session_id": env["session_id"], "slot_id": slot_id,
                                                    "limit": 100}).json()
        return sorted(v["book_copy_id"] for v in page["items"])

    assert slot("T-01-03") == ["BOOK-900127", "BOOK-900201"]   # 관측 칸 + 이 칸에 등록된 미관측 책
    assert slot("T-01-02") == ["BOOK-900125", "BOOK-900126"]
    assert slot("T-02-01") == []                               # 201의 등록 칸이지만 관측은 T-01-03에서 됨


def test_slot_filter_rejects_bad_format(client):
    assert client.get("/verifications", params={"slot_id": "T-01"}).status_code == 422


# ---------- 3. 관측 이미지 ----------

@pytest.fixture
def image_root(tmp_path, client):
    """테스트용 이미지 폴더를 IMAGE_ROOT로 쓴다 (client fixture의 설정 덮어쓰기 뒤에 적용)."""
    from app.core.config import get_settings as dep
    base = app.dependency_overrides[dep]()
    app.dependency_overrides[dep] = lambda: base.model_copy(update={"image_root": str(tmp_path)})
    (tmp_path / "images").mkdir()
    return tmp_path


def _observe_with_image(client, env, image_ref):
    data = payload(env, "T-01-03", copy_id="BOOK-900127")
    data["image_ref"] = image_ref
    res = client.post("/observations", headers=env["ai"], json=data)
    assert res.status_code == 201, res.text
    return res.json()


def test_image_url_and_file(client, env, image_root):
    (image_root / "images" / "a.jpg").write_bytes(b"\xff\xd8\xff fake-jpeg")
    obs = _observe_with_image(client, env, "images/a.jpg")
    assert obs["image_url"] == f"/observations/{obs['id']}/image"

    res = client.get(obs["image_url"])
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"
    assert res.content == b"\xff\xd8\xff fake-jpeg"


def test_image_missing_file_is_404(client, env, image_root):
    obs = _observe_with_image(client, env, "images/none.jpg")
    res = client.get(obs["image_url"])
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_no_image_ref_has_no_url(client, env, image_root):
    obs = _observe_with_image(client, env, None)
    assert obs["image_url"] is None
    assert client.get(f"/observations/{obs['id']}/image").status_code == 404


def test_image_path_outside_root_is_rejected(client, env, image_root):
    secret = image_root.parent / "secret.txt"
    secret.write_text("secret")
    obs = _observe_with_image(client, env, "../secret.txt")
    assert client.get(obs["image_url"]).status_code == 404


def test_default_image_root_setting():
    assert get_settings().image_root  # 기본값이 비어 있지 않음
