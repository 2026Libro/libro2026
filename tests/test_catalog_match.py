"""OCR 글자 → 복본 매칭 (백엔드 매칭)과 관측 API 연결."""
import pytest

from app.services.catalog_match import CatalogIndex, normalize_call_number
from tests.test_shelf_order import CALLS, observe, order_check, shelf_env  # noqa: F401  shelf_env는 fixture


@pytest.mark.parametrize("text, expected", [
    ("811.16 김14당 c.2", "811.16 김14당 c.2"),
    ("811. l6  김14당\nC ,2", "811.16 김14당 c.2"),
    ("8ll.16 김14당", "811.16 김14당"),
    ("823.914 T455r이 V 1", "823.914 t455r이 v.1"),
    ("OO5.1 K12a", "005.1 k12a"),
])
def test_normalize(text, expected):
    assert normalize_call_number(text) == expected


INDEX = CatalogIndex([
    ("EM1", "811.16 김14당", 1, "바람의 시"), ("EM2", "811.16 김14당 c.2", 1, "바람의 시"),
    ("EM3", "811.16 김14울", 2, "울음의 시"),
    ("EM4", "330.01 B896e", 3, "Economics : an introduction"),
    ("EM5", "811.16 박1ㄱ", 4, "가을 노래"),
])


@pytest.mark.parametrize("call, title, method, copy_id, first_candidate", [
    ("811.16 김14당 c.2", None, "EXACT", "EM2", "EM2"),               # 복본 기호까지 읽음
    ("811.16 김14당", None, "SAME_BOOK", None, "EM1"),                # 복본 기호 없음 → 같은 책 후보
    ("811.16 김14울", None, "EXACT", "EM3", "EM3"),                    # 1권뿐인 책
    ("811.16 김14울 c.3", None, "SAME_BOOK", "EM3", "EM3"),            # 없는 복본 번호 → 그 책
    ("811.16 김l4울", None, "SIMILAR", None, "EM3"),                   # OCR 오타 → 확정하지 않음
    ("330.01 B869e", None, "SIMILAR", None, "EM4"),
    ("811.16 김l4당 c.2", None, "SIMILAR", None, "EM2"),               # 오타여도 읽은 c.2를 앞으로
    (None, "울음의 시", "TITLE", None, "EM3"),                         # 서명만 읽음
    ("999 없음", None, "NONE", None, None),
])
def test_match(call, title, method, copy_id, first_candidate):
    result = INDEX.match(call, title)
    assert (result.method, result.book_copy_id) == (method, copy_id)
    assert (result.candidates[0][0] if result.candidates else None) == first_candidate


# ---------- 관측 API: 글자만 보내면 백엔드가 매칭 ----------

def test_observation_with_text_only_is_matched(client, shelf_env):
    exact = observe(client, shelf_env, 1, call_number_text="811.16 김54ㄱ c.2")
    assert (exact["reported_book_copy_id"], exact["match_method"]) == ("EM9800002", "EXACT")

    same = observe(client, shelf_env, 2, call_number_text="811.16 김542ㄴ")                     # 1권뿐 → 확정
    typo = observe(client, shelf_env, 3, call_number_text="811.16 김5ㄷ", title_text="책 811.16 김55ㄷ")
    observe(client, shelf_env, 4, call_number_text="823 Al2b")                                 # 오타 + 엉뚱한 책
    observe(client, shelf_env, 5, call_number_text="811.16 박1ㄱ")
    none = observe(client, shelf_env, 6, call_number_text="999 모름")
    assert same["match_method"] == "EXACT" and same["reported_book_copy_id"] == "EM9800003"
    assert typo["match_method"] == "SIMILAR" and typo["candidates"][0]["book_copy_id"] == "EM9800004"
    assert (none["match_method"], none["candidates"]) == ("NONE", [])

    books = order_check(client, shelf_env)["images"][0]["books"]
    assert [b["status"] for b in books] == ["IN_ORDER", "IN_ORDER", "IN_ORDER", "OUT_OF_PLACE", "IN_ORDER",
                                            "UNIDENTIFIED"]
    assert books[2]["call_number"] == "811.16 김55ㄷ"          # 오타였지만 1등 후보가 확실해 흐름 판단에 씀
    assert books[3]["call_number"] == CALLS["EM9800005"]


def test_ai_matched_payload_is_not_rematched(client, shelf_env):
    obs = observe(client, shelf_env, 1, "EM9800001", call_number_text="811.16 박1ㄱ")
    assert (obs["reported_book_copy_id"], obs["match_method"]) == ("EM9800001", None)
