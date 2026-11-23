"""조각 글자 OCR 매칭 — 2026-11-16 참고도서실 실제 사진(1116t1.png) OCR 결과 기준."""
import pytest

from app.core.call_number import class_division, out_of_order, sort_key, split_prefix
from app.services.catalog_match import normalize_call_number
from app.services.fragment_match import FragmentIndex, match_image, parse_fragments

# ---------- 청구기호 규칙: 별치기호, 한글 → 영문 ----------

def test_prefix_and_hangul_before_latin():
    assert split_prefix("R 004.603 권65ㅇ c.2") == ("R", ["004.603", "권65ㅇ", "c.2"])
    assert class_division("R 004.603 권65ㅇ") == "004"
    shelf = ["R 004.603 권65ㅇ", "R 004.603 김94ㅇ", "R 004.603 김94ㅇ c.2", "R 004.603 정45ㅇ",
             "R 004.603 홍73ㅇ c.2", "R 004.603 C579d워", "R 004.603 C956i이", "R 004.60951 전52ㅎ 2010"]
    assert sorted(shelf[::-1], key=sort_key) == shelf          # 실제 서가 순서 (한글 저자 다음 영문 저자)
    assert out_of_order(shelf) == set()
    assert sort_key("811.16 김54ㄱ") < sort_key("R 004.603 권65ㅇ")  # 일반 자료와 별치 자료는 따로 모인다
    assert normalize_call_number("r 004,603 권65ㅇ C.2") == "r 004.603 권65ㅇ c.2"


# ---------- 조각 해석 ----------

def test_parse_fragments():
    f = parse_fragments(["그0ㅣ3국가정보보호백서", "NIS", "R", "000", "005.8", "3127", "2013", "C.2"])
    assert (f.classes, f.copy_no) == (["005.8"], 2)
    assert "2013" in f.years and "국가정보보호백서" in f.titles
    f = parse_fragments(["한국-NOO", "000", "004 602", "016", "2004"])
    assert (f.classes, f.years) == (["004.602"], ["2004"])
    f = parse_fragments(["t16", "004.602", "1006"])
    assert f.fuzzy_years == ["1006"]                         # 2006을 잘못 읽었을 수 있음
    assert parse_fragments(["Web Korea 2010 Annual"]).years == ["2010"]


# ---------- 매칭 ----------

ROWS = [  # (등록번호, 청구기호, 도서, 서명, 자료실) — 실제 장서 일부
    ("EM0236766", "R 004.50951 방55한 2013", 1, "한국인터넷백서. 2012-2015,2018-", "참고"),
    ("EM0222981", "R 004.50951 방55한 2012-1", 1, "한국인터넷백서. 2012-2015,2018-", "참고"),
    ("EM0172207", "R 004.602 한16i 2004", 2, "한국 ISP 편람 = Korea ISP directory. 2001, 2004", "보존"),
    ("EM0167776", "R 004.602 한16i 2006", 2, "한국 ISP 편람 = Korea ISP directory. 2001, 2004", "참고"),
    ("EM0097636", "R 004.603 권65ㅇ", 3, "인터넷 통신어휘 사전 = Dictionary of internet words", "참고"),
    ("EM0046213", "R 004.603 김94ㅇ", 4, "인터넷용어사전 = ENGLISH-KOREAN DICTIONARY OF INTERNET", "참고"),
    ("EM0046224", "R 004.603 김94ㅇ c.2", 4, "인터넷용어사전 = ENGLISH-KOREAN DICTIONARY OF INTERNET", "참고"),
    ("EM0046212", "R 004.603 정45ㅇ", 5, "(최신)인터넷용어사전", "참고"),
    ("EM0046214", "R 004.603 박95ㅇ", 6, "인터넷용어사전", "보존"),
    ("EM0214429", "R 004.603 홍73ㅇ c.5", 7, "인터넷 용어사전 = Internet English-Koreandictionary", "참고"),
    ("EM0155856", "R 004.603 C579d워", 8, "인터네트워킹 용어 및 약어 사전", "참고"),
    ("EM0046211", "R 004.603 C956i이", 9, "인터넷용어사전", "참고"),
    ("EM0211649", "R 004.60951 전52ㅎ 2010", 10, "한국인터넷백서 = Korea internet white paper. 2000-20", "참고"),
    ("EM0059307", "R 004.60951 전52ㅎ 2000", 10, "한국인터넷백서 = Korea internet white paper. 2000-20", "보존"),
    ("EM0213327", "R 005.72 웹64ㅇ", 11, "웹코리아 2010 연감 = Web Korea 2010 annual", "참고"),
    ("EM0310902", "005.72 웹64w 2005", 12, "Web Korea 2005 annual", "보존"),
    ("EM0236755", "R 005.8 국12ㄱ 2013 c.2", 13, "국가정보보호백서. 2006-", "참고"),
    ("EM0222556", "R 005.8 국12ㄱ 2012", 13, "국가정보보호백서. 2006-", "참고"),
    ("EM0091285", "R 005.8 정45ㄱ 2002", 14, "개인정보보호백서. 2002-2003", "보존"),
    ("EM0076658", "004.03 월12ㅋ", 15, "컴퓨터 용어사전", "보존"),
]
INDEX = FragmentIndex(ROWS)


@pytest.mark.parametrize("fragments, expected", [
    (["2010한국인터넷백서", "000", "004.0095", "8520", "2010"], "EM0211649"),          # 분류번호 오인식
    (["그0ㅣ3국가정보보호백서", "NIS", "R", "000", "005.8", "3127", "2013", "C.2"], "EM0236755"),
    (["20ㅣ2국가정보보호백서", "000", "005.8"], "EM0222556"),
    (["한국ISP편람2006"], "EM0167776"),                                               # 제목 + 연도만
    (["Web Korea 2010 Annual", "000", "005.72", "8640"], "EM0213327"),                 # 영문 병렬서명
    (["인터네트워킹용어및약어사전", "00", "C579d", "004.603"], "EM0155856"),
    (["엄대넷용어사원", "43a4", "000", "C956401"], "EM0046211"),                       # 분류번호 없음, 제목 오타
])
def test_top_candidate(fragments, expected):
    result = INDEX.match(fragments)
    assert result.candidates[0][0] == expected


def test_confirm_only_when_clear():
    clear = INDEX.match(["그0ㅣ3국가정보보호백서", "005.8", "2013", "C.2"])
    assert clear.book_copy_id == "EM0236755"
    tie = INDEX.match(["인터넷 용이사진", "000", "004,603", "0940"])                    # 김94ㅇ c.1·c.2 구분 불가
    assert tie.book_copy_id is None
    assert {c for c, _ in tie.candidates[:2]} == {"EM0046213", "EM0046224"}
    assert INDEX.match(["???"]).method == "NONE"


def test_match_image_uses_neighbor_order_for_ties():
    books = [
        ["인터넷통신어휘사전", "000", "004.603", "권650"],     # 권65ㅇ
        ["인터넷 용이사진", "000", "004,603", "0940"],        # 김94ㅇ (c.1/c.2 동점)
        ["최신인터용어사전", "00", "004,60", "450"],           # 정45ㅇ
        ["넷", "터1", "용어사전", "000", "004,003", "8730"],  # 홍73ㅇ (004.03 월12ㅋ와 헷갈림)
        ["인터네트워킹용어및약어사전", "00", "C579d", "004.603"],
    ]
    results = match_image(INDEX, books)
    calls = [r.matched_call_number for r in results]
    assert calls[0] == "R 004.603 권65ㅇ" and calls[2] == "R 004.603 정45ㅇ"
    assert calls[3].startswith("R 004.603 홍73ㅇ") and calls[4] == "R 004.603 C579d워"
    assert out_of_order(calls) == set()
