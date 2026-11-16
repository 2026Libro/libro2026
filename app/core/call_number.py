"""청구기호 정렬 규칙 (서가 배열 순서).

청구기호 예: '823.914 T455r이 2011 v.1 c.2'
  1. 분류번호   823.914     : 숫자 크기 순 (KDC/DDC 소수)
  2. 저자기호   T455r이     : 글자는 문자 순(영문 → 한글 자모·음절), 숫자는 소수처럼 비교 (김54 < 김542 < 김55)
  3. 나머지     2011, v.1   : 연도·판·권차 순, 숫자 부분은 크기 순
  4. 복본 기호  c.2         : 맨 마지막. 없으면 1권째(c.1)

[가정] 중부대 도서관 실제 배가 규칙(별치기호, 영문·한글 저자 순서 등)은 현장 확인 후 조정한다.
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


def _mark(token: str) -> tuple:
    """저자기호 등: 숫자 덩어리는 소수 비교(문자열 그대로 비교하면 소수 순서가 됨), 글자는 대문자 기준 문자 순."""
    return tuple((0, p) if p.isdigit() else (1, p.upper()) for p in _PARTS.findall(token))


def _natural(token: str) -> tuple:
    """연도·권차 등: 숫자 덩어리는 크기 순."""
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p.upper()) for p in _PARTS.findall(token))


def sort_key(call_number: str) -> tuple:
    """청구기호를 서가 배열 순서로 비교할 수 있는 값으로 바꾼다."""
    tokens = call_number.split()
    if not tokens:
        return (Decimal("Infinity"),)
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
    return (_class_number(tokens[0]), author, tuple(extra), volume, copy_no)


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
