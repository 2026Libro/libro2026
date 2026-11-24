"""청구기호 정렬 규칙 (서가 배열 순서).

청구기호 예: '823.914 T455r이 2011 v.1 c.2', 'R 004.603 권65ㅇ c.2'
  0. 별치기호   R           : 참고도서(R) 등 따로 꽂는 자료 표시. 없으면 일반 자료. 같은 별치기호끼리 모인다
  1. 분류번호   823.914     : 숫자 크기 순 (KDC/DDC 소수)
  2. 저자기호   T455r이     : 글자는 문자 순(영문 → 한글 자모·음절), 숫자는 소수처럼 비교 (김54 < 김542 < 김55)
  3. 나머지     2011, v.1   : 연도·판·권차 순, 숫자 부분은 크기 순
  4. 복본 기호  c.2         : 맨 마지막. 없으면 1권째(c.1)

저자기호의 글자는 한글이 영문보다 앞이다 (2026-11-16 참고도서실 사진으로 확인).
[가정] 그 밖의 배가 규칙(별치기호 종류 등)은 현장 확인 후 조정한다.
도서관 데이터의 오타(c,2 / C.2 / v,.1 등)도 같은 의미로 읽는다.
"""
import re
from decimal import Decimal, InvalidOperation

_COPY = re.compile(r"^c[.,]*\s*(\d+)$", re.IGNORECASE)
_VOLUME = re.compile(r"^v[.,]*\s*(.+)$", re.IGNORECASE)
_PARTS = re.compile(r"\d+|[^\d]+")


def _class_number(token: str) -> Decimal:
    try:
        return Decimal(token)
    except InvalidOperation:
        return Decimal("Infinity")


def _letter(ch: str) -> tuple[int, str]:
    """한글(음절·자모)이 영문보다 앞 (현장 사진 확인: 004.603 서가에서 '홍73ㅇ' 다음에 'C579d')."""
    return (0 if "가" <= ch <= "힣" or "ㄱ" <= ch <= "ㅣ" else 1, ch.upper())


def _mark(token: str) -> tuple:
    """저자기호 등: 숫자 덩어리는 소수 비교(문자열 그대로 비교하면 소수 순서가 됨), 글자는 한글 → 영문 순."""
    return tuple((0, p) if p.isdigit() else (1, tuple(_letter(ch) for ch in p)) for p in _PARTS.findall(token))


def _natural(token: str) -> tuple:
    """연도·권차 등: 숫자 덩어리는 크기 순."""
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p.upper()) for p in _PARTS.findall(token))


def split_prefix(call_number: str) -> tuple[str, list[str]]:
    """('R', ['004.603', '권65ㅇ', 'c.2']). 첫 토큰이 숫자로 시작하지 않으면 별치기호로 본다."""
    tokens = call_number.split()
    if tokens and not tokens[0][0].isdigit() and len(tokens) > 1 and tokens[1][0].isdigit():
        return tokens[0].upper(), tokens[1:]
    return "", tokens


def sort_key(call_number: str) -> tuple:
    """청구기호를 서가 배열 순서로 비교할 수 있는 값으로 바꾼다."""
    prefix, tokens = split_prefix(call_number)
    if not tokens:
        return (prefix, Decimal("Infinity"), (), (), (), 1)
    copy_no = 1
    volume: tuple = ()
    extra: list[tuple] = []
    for token in tokens[2:]:
        if m := _COPY.match(token):
            copy_no = int(m.group(1))
        elif m := _VOLUME.match(token):
            volume = _natural(m.group(1))
        else:
            extra.append(_natural(token))
    author = _mark(tokens[1]) if len(tokens) > 1 else ()
    return (prefix, _class_number(tokens[0]), author, tuple(extra), volume, copy_no)


def class_division(call_number: str, digits: int = 3) -> str:
    """분류번호 앞자리 (기본 3자리 = KDC 강목). '811.16 김54ㄱ' → '811', 'R 004.603 권65ㅇ' → '004'"""
    _, tokens = split_prefix(call_number)
    head = tokens[0] if tokens else ""
    return head.split(".")[0][:digits]


_MAX = ((2,),)   # 어떤 _natural/_mark 값보다 큰 값


def upper_key(call_number: str) -> tuple:
    """범위 '끝'으로 쓸 때의 비교값. 적지 않은 뒷부분은 모두 포함한다.

    끝이 '811.16 김55ㄷ'이면 '811.16 김55ㄷ c.3'·'811.16 김55ㄷ v.2'도 범위 안이다.
    끝이 '823.914 T455r이 v.1'이면 v.1의 모든 복본까지 포함하고 v.2는 제외한다.
    """
    prefix, cls, author, extra, volume, copy_no = sort_key(call_number)
    _, all_tokens = split_prefix(call_number)
    if len(all_tokens) < 2:                     # 분류번호만 적으면 그 분류 전체
        return (prefix, cls, _MAX, (_MAX,), _MAX, float("inf"))
    tokens = all_tokens[2:]
    has_copy = any(_COPY.match(t) for t in tokens)
    has_volume = any(_VOLUME.match(t) for t in tokens)
    if not has_copy:
        copy_no = float("inf")
        if not has_volume:
            volume = _MAX
            if not extra:
                extra = (_MAX,)
    return (prefix, cls, author, extra, volume, copy_no)


def out_of_order(call_numbers: list[str]) -> set[int]:
    """왼쪽→오른쪽 순서의 청구기호 목록에서 순서를 벗어난 책의 위치(0부터)를 돌려준다.

    가장 길게 순서가 맞는 책들(최장 비감소 부분수열)을 '제자리'로 보고, 나머지를 순서 이탈로 본다.
    책 한 권이 잘못 꽂히면 그 한 권만 표시된다.
    """
    keys = [sort_key(c) for c in call_numbers]
    n = len(keys)
    best = [1] * n
    prev = [-1] * n
    for i in range(n):
        for j in range(i):
            if keys[j] <= keys[i] and best[j] + 1 > best[i]:
                best[i], prev[i] = best[j] + 1, j
    keep: set[int] = set()
    # 같은 길이면 더 오른쪽에서 끝나는 쪽을 고른다: [김54, 김542, 823, 김55]에서 튀는 823을 이탈로 본다.
    i = max(range(n), key=lambda k: (best[k], k)) if n else -1
    while i != -1:
        keep.add(i)
        i = prev[i]
    return set(range(n)) - keep
