"""OCR로 읽은 청구기호·서명 → DB의 복본(등록번호) 찾기 (백엔드 매칭).

단계
1. 정확히 일치: 읽은 청구기호(정규화)가 복본 1권과 같으면 확정 (복본 기호 c.N까지 읽은 경우)
2. 같은 책: 복본 기호(c.N)를 못 읽었거나 없으면, 그 책의 복본들을 후보로 (복본이 1권뿐이면 확정)
3. 비슷한 청구기호: OCR 오타가 있으면 같은 분류번호 안에서 비슷한 청구기호를 후보로 (서명이 있으면 함께 비교)
4. 서명만 읽은 경우: 비슷한 서명의 책을 후보로
후보만 있으면 백엔드는 복본을 확정하지 않는다 (흐름 검사는 후보가 모두 같은 책이면 정상 동작).

정규화: 줄바꿈·여러 칸 공백, 분류번호의 O→0·l/I/|→1·쉼표→점, 'c ,2'/'C.2'→'c.2', 'V 1'→'v.1', 대소문자 무시.
[가정] 점수 기준(MIN_SIMILARITY 등)은 실제 OCR 결과로 조정한다.
"""
import re
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher, get_close_matches

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Book, BookCopy

MIN_SIMILARITY = 0.80      # 비슷한 청구기호로 인정하는 최소 유사도
MIN_TITLE_SIMILARITY = 0.75
MAX_CANDIDATES = 20

_PREFIX = re.compile(r"^([A-Za-z]{1,2})\s+(?=[0-9OoIl|]{3})")
_CLASS = re.compile(r"^([0-9OoIl|]{1,3})\s*[.,·]?\s*([0-9OoIl|]*)")
_COPY = re.compile(r"(?<![A-Za-z가-힣])[cC]\s*[.,]*\s*(\d+)\b")
_VOLUME = re.compile(r"(?<![A-Za-z가-힣])[vV]\s*[.,]*\s*(\d+)")
_DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"})


def normalize_call_number(text: str) -> str:
    """비교용 청구기호. '811. l6  김14당\\nC ,2' → '811.16 김14당 c.2'"""
    t = re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()
    prefix = ""
    if m := _PREFIX.match(t):                     # 별치기호 (참고도서 R 등)
        prefix, t = m.group(1).upper() + " ", t[m.end():]
    if m := _CLASS.match(t):
        whole, frac = m.group(1).translate(_DIGIT_FIX), m.group(2).translate(_DIGIT_FIX)
        t = (f"{whole}.{frac}" if frac else whole) + t[m.end():]
    t = prefix + t
    t = _COPY.sub(lambda m: f" c.{m.group(1)}", t)
    t = _VOLUME.sub(lambda m: f" v.{m.group(1)}", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def _without_copy(key: str) -> str:
    return re.sub(r"\s+c\.\d+$", "", key)


def _has_copy_mark(key: str) -> bool:
    return bool(re.search(r"\sc\.\d+$", key))


def _class_key(key: str) -> str:
    """'r 004.603 권65ㅇ' → 'r 004.603', '811.16 김54ㄱ' → '811.16' (별치기호 + 분류번호)"""
    parts = key.split(" ")
    return " ".join(parts[:2]) if parts and parts[0].isalpha() and len(parts) > 1 else parts[0]


def _compact(key: str) -> str:
    return key.replace(" ", "")


@dataclass
class MatchResult:
    method: str                                   # EXACT / SAME_BOOK / SIMILAR / TITLE / NONE
    book_copy_id: str | None = None               # 확정한 복본 (없으면 후보만)
    score: float = 0.0                            # 확정 신뢰도 (identification confidence로 쓴다)
    candidates: list[tuple[str, float]] = field(default_factory=list)
    matched_call_number: str | None = None


class CatalogIndex:
    """복본 청구기호·서명 색인 (메모리). 7만 권 기준 수 초 안에 만든다."""

    def __init__(self, rows: list[tuple[str, str, int, str]]):
        self.copy_call: dict[str, str] = {}
        self.copy_book: dict[str, int] = {}
        self.by_key: dict[str, list[str]] = defaultdict(list)       # 정규화 청구기호 → 복본들
        self.by_base: dict[str, list[str]] = defaultdict(list)      # c.N 뗀 값 → 복본들
        self.by_class: dict[str, set[str]] = defaultdict(set)       # 분류번호 → base 목록
        self.book_title: dict[int, str] = {}
        self.book_copies: dict[int, list[str]] = defaultdict(list)
        for copy_id, call_number, book_id, title in rows:
            key = normalize_call_number(call_number)
            base = _without_copy(key)
            self.copy_call[copy_id] = call_number
            self.copy_book[copy_id] = book_id
            self.by_key[key].append(copy_id)
            self.by_base[base].append(copy_id)
            self.by_class[_class_key(base)].add(base)
            self.book_title[book_id] = title
            self.book_copies[book_id].append(copy_id)
        for ids in (*self.by_base.values(), *self.book_copies.values()):
            ids.sort()
        self._compact_bases = {_compact(b): b for b in self.by_base}
        self._titles = defaultdict(list)
        for book_id, title in self.book_title.items():
            self._titles[_title_key(title)].append(book_id)

    def __len__(self) -> int:
        return len(self.copy_call)

    def _title_score(self, copy_id: str, title: str | None) -> float | None:
        if not title:
            return None
        return SequenceMatcher(None, _title_key(title), _title_key(self.book_title[self.copy_book[copy_id]])).ratio()

    def match(self, call_number: str | None, title: str | None = None) -> MatchResult:
        key = normalize_call_number(call_number) if call_number else ""
        if key:
            base = _without_copy(key)
            exact = self.by_key.get(key, [])
            if _has_copy_mark(key) and exact:          # 복본 기호까지 읽었고 그대로 있음
                if len(exact) == 1:
                    return MatchResult("EXACT", exact[0], 0.99, [(exact[0], 0.99)], self.copy_call[exact[0]])
                return MatchResult("EXACT", None, 0.0, [(c, 0.9) for c in exact[:MAX_CANDIDATES]],
                                   self.copy_call[exact[0]])   # 도서관 데이터에 같은 청구기호가 2권 (드묾)
            if copies := self.by_base.get(base):
                # 복본 기호가 없거나 못 읽음: 복본이 1권뿐이면 확정, 여러 권이면 같은 책 후보
                if len(copies) == 1:
                    method = "EXACT" if exact else "SAME_BOOK"
                    return MatchResult(method, copies[0], 0.95, [(copies[0], 0.95)], self.copy_call[copies[0]])
                return MatchResult("SAME_BOOK", None, 0.0, [(c, 0.9) for c in copies[:MAX_CANDIDATES]],
                                   self.copy_call[copies[0]])
            similar = self._similar(base, title, key if _has_copy_mark(key) else None)
            if similar:
                return similar
        if title:
            return self._by_title(title)
        return MatchResult("NONE")

    def _similar(self, base: str, title: str | None, key_with_copy: str | None = None) -> MatchResult | None:
        pool = self.by_class.get(_class_key(base))
        if pool:
            target = _compact(base)
            scored = [(SequenceMatcher(None, target, _compact(b)).ratio(), b) for b in pool]
        else:   # 분류번호 자체를 잘못 읽음 → 전체에서 찾기
            close = get_close_matches(_compact(base), self._compact_bases, n=5, cutoff=MIN_SIMILARITY)
            scored = [(SequenceMatcher(None, _compact(base), c).ratio(), self._compact_bases[c]) for c in close]
        scored = sorted((s for s in scored if s[0] >= MIN_SIMILARITY), reverse=True)[:5]
        if not scored:
            return None
        candidates = []
        for sim, b in scored:
            for copy_id in self.by_base[b]:
                t = self._title_score(copy_id, title)
                score = sim if t is None else 0.7 * sim + 0.3 * t
                if key_with_copy and normalize_call_number(self.copy_call[copy_id]).endswith(
                        key_with_copy.rsplit(" ", 1)[-1]):
                    score += 0.03   # OCR이 읽은 복본 기호(c.N)와 같은 복본을 앞에
                candidates.append((copy_id, round(score * 0.9, 3)))   # 비슷함 ≠ 같음 → 확정하지 않는 점수
        candidates.sort(key=lambda c: (-c[1], c[0]))
        best = candidates[0][0]
        return MatchResult("SIMILAR", None, 0.0, candidates[:MAX_CANDIDATES], self.copy_call[best])

    def _by_title(self, title: str) -> MatchResult:
        close = get_close_matches(_title_key(title), self._titles, n=3, cutoff=MIN_TITLE_SIMILARITY)
        candidates = []
        for t in close:
            score = round(SequenceMatcher(None, _title_key(title), t).ratio() * 0.8, 3)
            for book_id in self._titles[t]:
                candidates += [(c, score) for c in self.book_copies[book_id]]
        if not candidates:
            return MatchResult("NONE")
        candidates.sort(key=lambda c: (-c[1], c[0]))
        return MatchResult("TITLE", None, 0.0, candidates[:MAX_CANDIDATES], self.copy_call[candidates[0][0]])


def _title_key(title: str) -> str:
    """서명 비교용: 부제(' : ' 뒤)·괄호·공백·기호 제거."""
    t = (title or "").split(" : ")[0].split(" = ")[0]
    t = re.sub(r"\([^)]*\)", "", t)
    return re.sub(r"[\s\W_]+", "", t).lower()


# ---------- DB에서 색인 만들기 (프로세스 안에서 재사용) ----------

_cache: dict[str, object] = {}


def get_index(db: Session) -> CatalogIndex:
    """복본 수·최근 수정 시각이 바뀌었으면 다시 만든다."""
    version = db.execute(select(func.count(BookCopy.id), func.max(BookCopy.updated_at))
                         .where(BookCopy.call_number.is_not(None))).one()
    if _cache.get("version") != tuple(version):
        rows = db.execute(select(BookCopy.id, BookCopy.call_number, BookCopy.book_id, Book.title)
                          .join(Book, Book.id == BookCopy.book_id)
                          .where(BookCopy.call_number.is_not(None))).all()
        _cache.update(version=tuple(version), index=CatalogIndex([tuple(r) for r in rows]))
    return _cache["index"]   # type: ignore[return-value]
