"""조각 글자 OCR → 복본 찾기.

실제 OCR은 책 1권에서 글자 조각 여러 개를 낸다 (라벨 줄마다, 제목 일부, 띠지 숫자 등).
    ['2013한국인터넷백서', '000', '004,50951', '평55번']
여기서 분류번호·저자기호·연도·복본 기호·제목 후보를 골라내고, 도서관 장서와 항목별로 비교해 점수를 낸다.

점수 (0~1): 분류번호 0.35 + 제목 0.35 + 연도 0.12 + 저자기호 0.10 + 복본 기호 0.08
  (읽지 못한 항목은 빼고 나머지 가중치로 다시 나눈다)
- 1등이 MIN_CONFIRM 이상이고 2등(다른 복본)보다 MIN_MARGIN 이상 높으면 확정, 아니면 후보만.
- [가정] 가중치·기준은 실제 사진 결과로 조정한다.
- '000', '00'처럼 라벨 띠의 분류 구분 숫자로 보이는 조각은 다른 분류번호 조각이 있으면 무시한다.
"""
import re
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher, get_close_matches

from app.core.call_number import split_prefix
from app.services.catalog_match import MatchResult

W_CLASS, W_TITLE, W_YEAR, W_AUTHOR, W_COPY = 0.35, 0.35, 0.12, 0.10, 0.08
MIN_CONFIRM = 0.70
MIN_MARGIN = 0.06
MIN_CLASS_SIMILARITY = 0.75
MAX_CANDIDATES = 10

_FIX = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1", "ㅣ": "1", ",": ".", "·": "."})
_CLASS_RE = re.compile(r"^\d{3}(?:\.\d+)?$")
_COPY_RE = re.compile(r"^[cC][.,]?\s*(\d{1,2})$")
_YEAR_RE = re.compile(r"^((?:19|20)\d{2})(.*)$")
_YEAR_ANY = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_SPACED_CLASS = re.compile(r"^(\d{3})\s+(\d{1,6})$")
_BAND = {"0", "00", "000"}


def _clean(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text or "").lower()


def _title_key(text: str) -> str:
    t = (text or "").split(" : ")[0].split(" = ")[0]
    return _clean(re.sub(r"\([^)]*\)", "", t))


def _title_keys(title: str) -> list[str]:
    """비교할 서명들: 본서명(괄호 뺌), 괄호 포함 본서명, '=' 뒤 병렬서명(영문 등)."""
    main = (title or "").split(" : ")[0]
    parts = [p.split(" : ")[0] for p in (title or "").split(" = ")]
    keys = [_title_key(title), _clean(main.split(" = ")[0])] + [_clean(re.sub(r"\([^)]*\)", "", p)) for p in parts[1:]]
    return [k for k in dict.fromkeys(keys) if len(k) >= 2]


@dataclass
class Fragments:
    classes: list[str] = field(default_factory=list)
    years: list[str] = field(default_factory=list)
    fuzzy_years: list[str] = field(default_factory=list)   # '1006'처럼 연도일 수 있는 4자리
    copy_no: int | None = None
    titles: list[str] = field(default_factory=list)
    marks: list[str] = field(default_factory=list)      # 저자기호처럼 보이는 짧은 조각


def parse_fragments(texts: list[str]) -> Fragments:
    f = Fragments()
    for raw in texts:
        text = (raw or "").strip()
        if not text:
            continue
        if m := _SPACED_CLASS.match(text.translate(_FIX)):
            text = f"{m.group(1)}.{m.group(2)}"           # '004 602' → '004.602'
        fixed = text.translate(_FIX).replace(" ", "")
        if m := _COPY_RE.match(text.replace(" ", "")):
            f.copy_no = int(m.group(1))
        elif _CLASS_RE.match(fixed):
            f.classes.append(fixed)
        elif m := _YEAR_RE.match(_lead_fix(text)):
            f.years.append(m.group(1))
            if len(rest := _title_key(m.group(2))) >= 2:
                f.titles.append(rest)                   # '2013한국인터넷백서' → 연도 + 제목
        elif len(key := _title_key(text)) >= 4 and re.search(r"[가-힣]{2}|[A-Za-z]{4}", text):
            f.titles.append(key)
            f.years += _YEAR_ANY.findall(text)               # 'Web Korea 2010 Annual'
            if m := re.search(r"((?:19|20)\d{2})$", text.replace(" ", "")):
                f.years.append(m.group(1))                   # '한국ISP편람2006'
        elif re.fullmatch(r"[12]\d{3}", fixed):
            f.fuzzy_years.append(fixed)
            f.marks.append(text.lower())
        elif re.search(r"\d", text) and len(text) <= 8:
            f.marks.append(text.lower())
    # '000'·'00'은 라벨 위 분류 띠 숫자로 보고 버린다 (실제 분류번호는 소수점 이하가 함께 읽힌다)
    f.classes = [c for c in f.classes if c not in _BAND]
    # 소수점 있는 분류번호가 있으면, 소수점 없는 세 자리 숫자('016')는 저자기호 숫자로 본다
    if any("." in c for c in f.classes):
        f.marks += [c for c in f.classes if "." not in c]
        f.classes = [c for c in f.classes if "." in c]
    return f


def _lead_fix(text: str) -> str:
    """앞쪽 숫자 부분의 오인식만 고친다: '20ㅣ2국가…' → '2012국가…'"""
    m = re.match(r"^[0-9OoIlㅣ|그]{4}", text.replace(" ", ""))
    if not m:
        return text
    lead = m.group(0).translate(_FIX).replace("그", "2")
    return lead + text.replace(" ", "")[4:]


@dataclass
class _Copy:
    copy_id: str
    call_number: str
    book_id: int
    prefix: str
    class_no: str
    author: str
    extras: list[str]
    copy_no: int
    titles: list[str]
    room: str | None


class FragmentIndex:
    def __init__(self, rows: list[tuple]):
        """rows: (등록번호, 청구기호, 도서 id, 서명, 자료실)"""
        self.copies: list[_Copy] = []
        self.by_class: dict[str, list[_Copy]] = defaultdict(list)
        self.by_title: dict[str, list[_Copy]] = defaultdict(list)
        self.by_author_head: dict[str, list[_Copy]] = defaultdict(list)
        for copy_id, call, book_id, title, room in rows:
            prefix, tokens = split_prefix(call)
            if not tokens:
                continue
            copy_no, extras = 1, []
            for t in tokens[2:]:
                if m := re.match(r"^[cC][.,]*(\d+)$", t):
                    copy_no = int(m.group(1))
                else:
                    extras.append(t)
            c = _Copy(copy_id, call, book_id, prefix, tokens[0], tokens[1].lower() if len(tokens) > 1 else "",
                      extras, copy_no, _title_keys(title), room)
            self.copies.append(c)
            self.by_class[c.class_no].append(c)
            for key in c.titles:
                self.by_title[key].append(c)
            if c.author:
                self.by_author_head[c.author[0]].append(c)
        self._class_keys = list(self.by_class)
        self._title_keys = list(self.by_title)

    def __len__(self) -> int:
        return len(self.copies)

    def _pool(self, f: Fragments) -> dict[str, _Copy]:
        pool: dict[str, _Copy] = {}
        for cls in f.classes:
            for key in self._class_keys:
                if key == cls or SequenceMatcher(None, cls, key).ratio() >= MIN_CLASS_SIMILARITY:
                    for c in self.by_class[key]:
                        pool[c.copy_id] = c
        for title in f.titles:
            if len(title) < 3:
                continue
            keys = [k for k in self._title_keys if title in k or (len(k) >= 4 and k in title)]
            if not keys:   # 제목 오타 ('엄대넷용어사원') → 비슷한 제목
                keys = get_close_matches(title, self._title_keys, n=8, cutoff=0.6)
            for key in keys:
                for c in self.by_title[key]:
                    pool[c.copy_id] = c
        if not f.classes:   # 분류번호를 못 읽었으면 저자기호로도 찾는다 ('C956401' → 'c956i이')
            for mark in (m for m in f.marks if len(m) >= 4 and m[0].isalpha()):
                for c in self.by_author_head.get(mark[0], []):
                    if _author_similarity(mark, c.author) >= 0.6:
                        pool[c.copy_id] = c
        return pool

    def _score(self, c: _Copy, f: Fragments) -> float:
        cls = max((1.0 if x == c.class_no else SequenceMatcher(None, x, c.class_no).ratio() for x in f.classes),
                  default=0.0)
        title = max((_title_similarity(t, k) for t in f.titles for k in c.titles), default=0.0)
        year = _year_score(f, c.extras)
        author = max((_author_similarity(m, c.author) for m in f.marks), default=0.0) if c.author else 0.0
        copy = 1.0 if f.copy_no == c.copy_no else 0.0   # 복본 기호를 못 읽었으면 점수에 넣지 않는다
        weights = [(W_CLASS, cls, bool(f.classes)), (W_TITLE, title, bool(f.titles)), (W_YEAR, year, bool(f.years or f.fuzzy_years)),
                   (W_AUTHOR, author, bool(f.marks)), (W_COPY, copy, f.copy_no is not None)]
        used = sum(w for w, _, present in weights if present) or 1.0
        return sum(w * v for w, v, present in weights if present) / used

    def match(self, texts: list[str]) -> MatchResult:
        f = parse_fragments(texts)
        pool = self._pool(f)
        if not pool:
            return MatchResult("NONE")
        scored = sorted(((round(self._score(c, f), 3), c) for c in pool.values()),
                        key=lambda s: (-s[0], s[1].copy_id))[:MAX_CANDIDATES]
        best_score, best = scored[0]
        runner = scored[1][0] if len(scored) > 1 else 0.0
        candidates = [(c.copy_id, s) for s, c in scored]
        if best_score >= MIN_CONFIRM and best_score - runner >= MIN_MARGIN:
            return MatchResult("FRAGMENTS", best.copy_id, best_score, candidates, best.call_number)
        return MatchResult("FRAGMENTS", None, 0.0, candidates, best.call_number)


def _year_score(f: Fragments, extras: list[str]) -> float:
    years = [e[:4] for e in extras if re.match(r"(?:19|20)\d{2}", e)]
    if any(y in years for y in f.years):
        return 1.0
    for y in f.fuzzy_years:   # 한 자리만 틀린 연도 ('1006' ↔ '2006')
        if any(sum(a == b for a, b in zip(y, x)) >= 3 for x in years):
            return 0.6
    return 0.0


def _author_similarity(mark: str, author: str) -> float:
    """저자기호 비교. 한글·기호는 OCR이 자주 틀리므로 숫자만 따로도 비교한다 ('0940' ↔ '김94ㅇ')."""
    text = SequenceMatcher(None, mark, author).ratio()
    d1, d2 = re.sub(r"\D", "", mark), re.sub(r"\D", "", author)
    digits = SequenceMatcher(None, d1, d2).ratio() if d1 and d2 else 0.0
    return max(text, 0.9 * digits)


def _title_similarity(fragment: str, title: str) -> float:
    """조각이 제목의 일부일 수 있으므로, 조각이 제목에 얼마나 들어 있는지와 제목을 얼마나 덮는지를 함께 본다."""
    if not fragment or not title:
        return 0.0
    matched = sum(b.size for b in SequenceMatcher(None, fragment, title).get_matching_blocks())
    return 0.6 * matched / len(fragment) + 0.4 * matched / len(title)


# ---------- 사진 단위: 옆 책 순서로 동점 후보 고르기 ----------

ORDER_TIE = 0.04   # [가정] 이 점수 차 안의 후보끼리만 앞뒤 책 순서를 보고 고른다 (확실한 엉뚱한 책은 그대로)


def match_image(index: FragmentIndex, books: list[list[str]]) -> list[MatchResult]:
    """사진 한 장의 책들(왼쪽부터)을 한꺼번에 매칭한다.

    책마다 1등과 점수 차가 ORDER_TIE 이내인 후보들 중에서, 앞뒤 책과 청구기호 순서가 맞는 쪽을 고른다
    (동적 계획법: 순서가 맞는 이웃 쌍이 가장 많은 조합). 고른 후보를 후보 목록 맨 앞으로 옮긴다.
    점수가 확실히 높은 1등은 바꾸지 않으므로, 진짜로 잘못 꽂힌 책은 그대로 흐름 검사에서 걸린다.
    """
    from app.core.call_number import sort_key

    results = [index.match(texts) for texts in books]
    options: list[list[int]] = []
    for r in results:
        if not r.candidates:
            options.append([])
            continue
        top = r.candidates[0][1]
        options.append([i for i, (_, s) in enumerate(r.candidates) if top - s <= ORDER_TIE])

    call = {c.copy_id: c.call_number for c in index.copies}
    known = [i for i, opts in enumerate(options) if opts]
    if len(known) < 2:
        return results
    # best[k][j] = known[k] 책이 후보 j를 골랐을 때, 그 책까지 순서가 맞는 이웃 쌍 수 (최대)
    best: list[dict[int, tuple[int, int | None]]] = []
    for k, i in enumerate(known):
        row = {}
        for j in options[i]:
            key = sort_key(call[results[i].candidates[j][0]])
            if k == 0:
                row[j] = (0, None)
                continue
            prev_i = known[k - 1]
            choices = [(best[k - 1][pj][0] + (sort_key(call[results[prev_i].candidates[pj][0]]) <= key), pj)
                       for pj in options[prev_i]]
            row[j] = max(choices, key=lambda c: (c[0], -c[1]))
        best.append(row)
    j = max(best[-1], key=lambda x: (best[-1][x][0], -x))
    for k in range(len(known) - 1, -1, -1):
        i = known[k]
        if j:   # 순서상 더 맞는 후보를 맨 앞으로
            r = results[i]
            chosen = r.candidates[j]
            r.candidates = [chosen] + [c for n, c in enumerate(r.candidates) if n != j]
            r.matched_call_number = call[chosen[0]]
            if r.book_copy_id and r.book_copy_id != chosen[0]:
                r.book_copy_id, r.score = None, 0.0     # 확정했던 것과 다르면 확정을 풀고 후보로
        j = best[k][j][1] if best[k][j][1] is not None else 0
    return results


# ---------- DB에서 색인 만들기 (프로세스 안에서 재사용) ----------

_cache: dict[str, object] = {}


def get_fragment_index(db) -> FragmentIndex:
    """복본 수·최근 수정 시각이 바뀌었으면 다시 만든다 (25만 권 기준 수 초)."""
    from sqlalchemy import func, select

    from app.models import Book, BookCopy

    version = tuple(db.execute(select(func.count(BookCopy.id), func.max(BookCopy.updated_at))
                               .where(BookCopy.call_number.is_not(None))).one())
    if _cache.get("version") != version:
        rows = db.execute(select(BookCopy.id, BookCopy.call_number, BookCopy.book_id, Book.title, BookCopy.room)
                          .join(Book, Book.id == BookCopy.book_id)
                          .where(BookCopy.call_number.is_not(None))).all()
        _cache.update(version=version, index=FragmentIndex([tuple(r) for r in rows]))
    return _cache["index"]   # type: ignore[return-value]
