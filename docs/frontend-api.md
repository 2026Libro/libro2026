# 프론트엔드 연동 API 명세 (조회 중심)

대상 화면: **서가 → 칸 → 스캔 세션 → 결과 상세**

- 응답 예시는 개발 서버(로컬 시드 데이터)에서 실제로 받은 값입니다. 긴 목록은 일부만 실었습니다.
- **[구현됨]** 지금 바로 호출할 수 있는 API입니다.
- **[제안]** 아직 없는 API이며, 합의되면 백엔드에서 추가합니다.
- **[가정]** 값은 구현돼 있지만 팀 합의 전이라 바뀔 수 있습니다.
- 확정이 필요한 항목은 마지막 장 "결정 필요 목록"에 모았습니다.

---

## 1. 공통 규칙

| 항목 | 내용 |
|---|---|
| Base URL | `http://<서버>:8000` (로컬: `http://127.0.0.1:8000`, Swagger: `/docs`) |
| CORS | 기본 허용: `localhost:3000`, `localhost:5173` (`127.0.0.1` 포함). 다른 주소는 `.env`의 `CORS_ORIGINS`에 추가 |
| 인증 | **조회(GET)는 인증 없음.** 쓰기(생성·취소·수정)는 헤더 `X-Admin-Token: <토큰>` 필요. [가정] 로그인 방식이 정해지면 교체 |
| 시간 | 모두 **UTC**, ISO 8601 + `Z` (예: `2026-10-30T03:29:58Z`). 화면 표시는 프론트에서 KST로 변환 |
| 목록 응답 | `{ "items": [...], "total": 53, "limit": 20, "offset": 0 }`. `limit`는 최대 100 |
| 상태 코드 값 | 모두 대문자 문자열 (예: `"MISPLACED_SUSPECTED"`) |
| null | 값이 없거나 판정할 수 없을 때는 필드를 빼지 않고 `null`로 보냄 |

### 오류 응답 (모든 API 공통)

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "서가 'A-09'를 찾을 수 없습니다.",
    "details": null
  }
}
```

| HTTP | code | 의미 |
|---|---|---|
| 401 | `UNAUTHORIZED` | 관리자 토큰 없음·불일치 |
| 403 | `FORBIDDEN` | 권한 없음 |
| 404 | `NOT_FOUND` | 대상 없음 |
| 409 | `ALREADY_EXISTS` / `DUPLICATE_REQUEST` / `INVALID_STATE` | 중복, 또는 현재 상태에서 불가능한 요청 (예: 이미 끝난 작업 취소) |
| 422 | `VALIDATION_ERROR` | 요청 형식 오류. `details[].field`에 문제 필드 |
| 500 | `INTERNAL_ERROR` | 서버 오류 |

---

## 2. ID 규칙

[가정] 형식은 `app/core/ids.py`의 정규식 하나로 관리합니다. 바뀌면 그 파일만 고칩니다.

| 대상 | 형식 | 예시 | 비고 |
|---|---|---|---|
| 서가 | `^[A-Z]-\d{2}$` | `A-03` | 구역 A의 3번 서가. `zone`은 앞 글자 |
| 칸 | `^[A-Z]-\d{2}-\d{2}$` | `A-03-02` | 서가 ID + 칸 번호. `position`은 1부터 |
| 도서(서지) | 정수 | `30` | 같은 책 제목 단위 |
| 복본 | `^BOOK-\d+$` | `BOOK-125` | **실제 책 한 권.** 위치·판정은 모두 복본 기준 |
| 스캔 세션 | `SCAN-…` | `SCAN-20261030032958-e5ff26` | 백엔드가 생성 |
| 관측 | `OBS-…` | `OBS-20261030032958-e5ff26-003` | AI가 생성. 책등 1개를 1번 본 기록 |
| 검증 결과 | 정수 | `1238` | 판정 1건. 결과 상세 화면의 기준 ID |
| 로봇 / 작업 | `R001` / `TASK-…` | | |

> **화면 라우팅 키 권장:** 서가 `shelf_id`, 칸 `slot_id`, 세션 `scan_session_id`, 결과 상세 `verification_id`(정수)

---

## 3. 화면별 API

### 3-1. 서가 목록 — `GET /shelves` [구현됨]

```json
[
  {
    "id": "A-03", "zone": "A", "name": null, "description": null,
    "slots": [
      { "id": "A-03-01", "position": 1 },
      { "id": "A-03-02", "position": 2 }
    ]
  }
]
```

페이지 없이 배열 전체를 반환합니다.

### 3-2. 서가 상세(칸별 등록 도서) — `GET /shelves/{shelf_id}` [구현됨]

```json
{
  "id": "A-03", "zone": "A", "name": null, "description": null,
  "slots": [
    { "id": "A-03-01", "position": 1, "registered_copies": [] },
    { "id": "A-03-02", "position": 2, "registered_copies": [
      { "book_copy_id": "BOOK-125", "book_id": 30, "title": "파이썬 프로그래밍", "loan_status": "AVAILABLE" },
      { "book_copy_id": "BOOK-126", "book_id": 30, "title": "파이썬 프로그래밍", "loan_status": "AVAILABLE" }
    ]},
    { "id": "A-03-04", "position": 4, "registered_copies": [
      { "book_copy_id": "BOOK-128", "book_id": 32, "title": "데이터베이스 개론", "loan_status": "CHECKED_OUT" }
    ]}
  ]
}
```

> ⚠ 이 목록은 **등록 위치 기준(원래 꽂혀 있어야 할 책)** 입니다. 스캔에서 실제로 보인 책이 아닙니다. 칸 화면에서 "등록 도서"와 "스캔 결과"를 구분해서 표시해 주세요.

### 3-3. 스캔 세션 목록 — `GET /scan-sessions?shelf_id=A-03&limit=20&offset=0` [구현됨]

최신순입니다. `shelf_id`를 빼면 전체 세션을 반환합니다.

```json
{
  "items": [
    {
      "id": "SCAN-20261030032958-e5ff26",
      "robot_id": "R001",
      "shelf_id": "A-03",
      "status": "COMPLETED",
      "started_at": "2026-10-30T03:29:58.373894Z",
      "finished_at": "2026-10-30T03:29:58.478725Z",
      "observation_count": 5
    }
  ],
  "total": 11, "limit": 20, "offset": 0
}
```

세션 1개는 서가 1개 전체를 대상으로 합니다. 칸 단위 세션은 없습니다.

### 3-4. 스캔 세션 상세 — `GET /scan-sessions/{session_id}` [구현됨]

목록의 항목 1개와 같은 형식입니다. 진행률과 집계는 들어 있지 않습니다(→ 6장).

### 3-5. 세션 결과 목록 — `GET /verifications?scan_session_id=…&slot_id=…&latest_only=true&limit=100` [구현됨]

`id` 내림차순입니다.

- **`slot_id`**: 칸 필터입니다. 관측된 결과는 관측 칸(`observed_slot_id`), 미관측 결과는 등록 칸(`registered_slot_id`) 기준으로 거릅니다.
- **`latest_only=true`**: 대체된 결과(`superseded_by`가 있는 행)를 뺍니다. **화면에서는 항상 true를 권장합니다.**
- 그 밖의 필터: `identification_status`, `location_verdict`, `observation_status`, `workflow_status`, `book_copy_id`, `observation_id`

응답의 각 항목에는 아래 세 필드가 함께 옵니다.

- `title`: 도서 제목. 복본 미확정이면 null
- `badge`: 화면 대표 판정 (→ 4-2절)
- `superseded_by`: 이 결과를 대체한 결과의 id. null이면 최신 결과

```json
{
  "items": [
    {
      "id": 1238,
      "scan_session_id": "SCAN-20261030032958-e5ff26",
      "observation_id": "OBS-20261030032958-e5ff26-003",
      "book_copy_id": "BOOK-201",
      "identification_status": "IDENTIFIED",
      "location_verdict": "MISPLACED_SUSPECTED",
      "observation_status": "OBSERVED",
      "workflow_status": "REVIEW_REQUIRED",
      "registered_slot_id": "B-01-01",
      "observed_slot_id": "A-03-05",
      "reobservation_count": 0,
      "note": "재관측 작업 TASK-20261030032958-ac3f25 완료 후 300초 동안 AI 결과 없음",
      "created_at": "2026-10-30T03:29:58.442421Z",
      "updated_at": "2026-10-30T03:34:59.449684Z"
    }
  ],
  "total": 6, "limit": 100, "offset": 0
}
```

**같은 세션의 결과 6건을 유형별로 정리하면 다음과 같습니다(실제 데이터).**

| observation_status | identification_status | location_verdict | workflow_status | 화면 의미 |
|---|---|---|---|---|
| OBSERVED | IDENTIFIED | NORMAL | null | 제자리 |
| OBSERVED | CANDIDATE_ONLY | NORMAL | null | 복본은 못 정했지만 후보가 모두 이 칸 소속 → 제자리 |
| OBSERVED | IDENTIFIED | MISPLACED_SUSPECTED | REVIEW_REQUIRED | 오배가 의심 |
| OBSERVED | UNCERTAIN | UNDETERMINED | REVIEW_REQUIRED | 인식 불확실 |
| OBSERVED | FAILED | UNDETERMINED | REVIEW_REQUIRED | 인식 실패 |
| NOT_OBSERVED | null | UNDETERMINED | REVIEW_REQUIRED | 등록돼 있는데 스캔에서 안 보임 |


### 3-6. 결과 상세 — 지금은 API 3개를 조합해야 합니다 [구현됨]

1. `GET /verifications/{verification_id}`: 판정 (위 항목과 같은 형식)
2. `GET /observations/{observation_id}`: 신뢰도, 후보, 이미지, 읽은 글자 (미관측이면 `observation_id`가 null이므로 호출하지 않음)
3. `GET /copies/{book_copy_id}`: 제목, 대출 상태, 등록·물리 위치 (복본 미확정이면 호출하지 않음)

**관측 예시 (`GET /observations/OBS-20261030032958-e5ff26-003`)**

```json
{
  "id": "OBS-20261030032958-e5ff26-003",
  "scan_session_id": "SCAN-20261030032958-e5ff26",
  "robot_id": "R001",
  "shelf_id": "A-03",
  "slot_id": "A-03-05",
  "detected_text": "로봇공학 입문",
  "reported_book_copy_id": "BOOK-201",
  "detection_confidence": 0.97,
  "ocr_confidence": 0.9,
  "identification_confidence": 0.88,
  "candidates": [
    { "book_copy_id": "BOOK-201", "score": 0.88, "rank": 1 },
    { "book_copy_id": "BOOK-202", "score": 0.85, "rank": 2 }
  ],
  "reobservation_of": null,
  "image_ref": "images/OBS-20261030032958-e5ff26-003.jpg",
  "image_url": "/observations/OBS-20261030032958-e5ff26-003/image",
  "captured_at": "2026-10-30T03:30:01Z",
  "received_at": "2026-10-30T03:29:58.438139Z"
}
```

**관측 이미지 — `GET /observations/{observation_id}/image` [구현됨]**

- 응답은 이미지 파일 자체입니다(jpeg/png). `<img src="{서버주소}{image_url}">`로 그대로 쓰면 됩니다.
- `image_url`이 null이면 이미지가 없는 관측입니다.
- 파일이 서버에 없으면 404(`NOT_FOUND`)가 옵니다. 대체 이미지를 보여 주세요.
- [가정] 파일은 서버의 `IMAGE_ROOT` 폴더에서 `image_ref` 경로로 찾습니다. AI가 실제로 파일을 어디에 둘지는 AI 담당자와 정하는 중이라, 그전까지는 대부분 404입니다.

**복본 예시 (`GET /copies/BOOK-130`)**

```json
{
  "id": "BOOK-130",
  "book_id": 384,
  "title": "운영체제",
  "loan_status": "AVAILABLE",
  "registered_slot_id": "A-03-05",
  "physical_location_type": "SHELF",
  "physical_slot_id": "A-03-05",
  "physical_robot_id": null,
  "physical_location_certainty": "ESTIMATED",
  "physical_location_confirmed_at": null
}
```

> 결과 목록에는 `title`이 들어 있으므로 목록 화면에서는 `/copies`를 부를 필요가 없습니다. 상세 화면에서만 부르세요.

### 3-7. 대시보드 요약 — `GET /dashboard/summary` [구현됨]

집계 기준은 6-3절에 있습니다. 응답 일부입니다.

```json
{
  "generated_at": "2026-11-10T06:49:47.309701Z",
  "books": 7,
  "copies": { "total": 9, "normal": 2, "misplaced_suspected": 1, "misplaced_confirmed": 0,
              "not_observed": 1, "undetermined": 0, "never_verified": 5 },
  "loans": { "available": 8, "checked_out": 1, "in_transit": 0, "unknown": 0 },
  "workflow": { "reobservation_pending": 6, "review_required": 21, "approved": 0 },
  "open_alerts": 10,
  "last_scan_at": "2026-10-30T03:29:58Z"
}
```

### 3-8. 실시간 갱신 — `ws://<서버>/ws/events?token=<관리자 토큰>` [구현됨, 형식은 가정]

메시지 형식은 `{"type": "verification.created", "at": "...Z", "data": {...}}`입니다. `data`에는 핵심 값만 담기므로, 자세한 내용은 REST로 다시 조회하세요.

스캔 화면에서 쓸 이벤트는 다음과 같습니다.

| type | 언제 | data의 주요 값 |
|---|---|---|
| `observation.created` | 관측 도착 | `id`, `scan_session_id`, `slot_id` |
| `verification.created` / `.updated` | 판정 생성 / 처리 흐름 변경 | `id`, 상태 4종, `observed_slot_id`, `registered_slot_id` |
| `task.updated` | 스캔 작업 상태 변경 | `id`, `task_type`, `status` |

30초마다 `ping`이 옵니다. 응답할 필요는 없습니다.

---

## 4. 판정 상태 코드

**판정은 축 4개로 나뉘어 있습니다.** 하나의 "상태" 값으로 합치지 말고, 화면에서는 아래 우선순위로 대표 배지 하나를 고르는 방식을 권장합니다.

### 4-1. 코드 정의

**observation_status: 스캔 범위 기준 관측 여부**

| 값 | 의미 |
|---|---|
| `OBSERVED` | 이번 스캔에서 보임 |
| `NOT_OBSERVED` | 스캔은 끝났는데 안 보임. **분실 아님**, 확인 필요 |
| `SCAN_INCOMPLETE` | 스캔이 중단돼 판단 불가 |

**identification_status: 어떤 책인지 식별 (OBSERVED일 때만, 미관측이면 null)**

| 값 | 의미 |
|---|---|
| `IDENTIFIED` | 복본까지 확정 (`book_copy_id` 있음) |
| `CANDIDATE_ONLY` | 후보는 있으나 복본 미확정 (`book_copy_id` = null) |
| `UNCERTAIN` | 신뢰도 부족 |
| `UNREGISTERED` | 글자는 읽었는데 DB에 없는 책 |
| `FAILED` | 인식 실패 |

**location_verdict: 위치가 맞는지**

| 값 | 의미 |
|---|---|
| `NORMAL` | 제자리 |
| `MISPLACED_SUSPECTED` | 오배가 의심 (첫 관측) |
| `MISPLACED_CONFIRMED` | 오배가 확정 (재관측에서도 같은 결과) |
| `UNDETERMINED` | 판정 불가 (식별 실패, 미관측 등) |

**workflow_status: 관리자 처리 흐름**

| 값 | 의미 |
|---|---|
| `null` | 처리할 일 없음 |
| `REOBSERVATION_PENDING` | 로봇이 다시 보러 가는 중 |
| `REVIEW_REQUIRED` | 관리자 확인 필요 |
| `APPROVED` | 인출 승인됨 |
| `CLOSED` | 종료 (처리 완료, 또는 재관측 결과로 대체됨) |

### 4-2. 대표 배지 `badge` [구현됨, 확정]

서버가 판정 4개 축으로 계산해 응답에 넣습니다. **프론트는 `badge`를 그대로 표시합니다.** 위에서부터 먼저 해당하는 값 하나가 됩니다.

| 순위 | badge | 조건 | 화면 문구 예 |
|---|---|---|---|
| 1 | `MISPLACED_CONFIRMED` | `location_verdict = MISPLACED_CONFIRMED` | 오배가 확정 |
| 2 | `MISPLACED_SUSPECTED` | `location_verdict = MISPLACED_SUSPECTED` (IDENTIFIED, 또는 CANDIDATE_ONLY인데 후보가 전부 다른 칸 소속) | 오배가 의심 |
| 3 | `OUT_OF_PLACE` | `sequence_status = OUT_OF_PLACE` (청구기호 흐름에서 분류가 다른 책) | 엉뚱한 책 |
| 4 | `NOT_OBSERVED` | `observation_status = NOT_OBSERVED` | 미관측 (분실 아님) |
| 5 | `SCAN_INCOMPLETE` | `observation_status = SCAN_INCOMPLETE` | 판단 보류 (스캔 중단) |
| 6 | `RECOGNITION_ISSUE` | `identification_status`가 FAILED / UNCERTAIN / UNREGISTERED | 인식 확인 필요 |
| 7 | `OUT_OF_ORDER` | `sequence_status = OUT_OF_ORDER` (같은 분류 안에서 순서만 어긋남) | 순서 오류 |
| 8 | `NORMAL` | `location_verdict = NORMAL`, 또는 등록 칸이 없어도 `sequence_status = IN_ORDER` | 정상 |
| 9 | `LOCATION_UNDETERMINED` | 위에 해당하지 않음 (아래 설명) | 위치 판정 보류 |

`sequence_status`(청구기호 흐름 검사)는 AI가 분석 완료를 알릴 때 기록됩니다. 그 전에는 null입니다. 자세한 내용은 4-4절을 보세요.

**정상 조건**
- `location_verdict = NORMAL`이면 정상입니다. 아래 두 경우가 해당합니다.
  - `IDENTIFIED` + 등록 칸 = 관측 칸
  - `CANDIDATE_ONLY` + 후보가 **모두** 이 칸에 등록된 복본 (예: 같은 책 복본 2권이 같은 칸) → 어느 복본이든 제자리이므로 정상
- 정상이어도 `workflow_status`가 `REVIEW_REQUIRED`일 수 있습니다. 예를 들어 대출 중으로 등록된 책이 서가에서 보인 경우입니다. 이때는 배지는 정상, 처리 상태는 "검토 필요"로 따로 표시하세요.

**CANDIDATE_ONLY 처리**
- 후보가 모두 이 칸 소속이면 `NORMAL`입니다.
- 후보가 모두 다른 칸 소속이면 `MISPLACED_SUSPECTED`입니다.
- 후보가 이 칸과 다른 칸에 섞여 있으면 `LOCATION_UNDETERMINED`입니다.
- 어느 경우든 `book_copy_id`와 `title`은 null입니다. 후보 목록은 관측 API의 `candidates`를 보여 주세요.

**UNDETERMINED 처리** (`location_verdict = UNDETERMINED`)
- 인식 문제 때문이면 `RECOGNITION_ISSUE`입니다.
- 미관측이면 `NOT_OBSERVED` 또는 `SCAN_INCOMPLETE`입니다.
- 책은 알아봤는데 위치만 판정할 수 없으면 `LOCATION_UNDETERMINED`입니다. 후보가 섞여 있거나, 등록 위치가 없는 복본인 경우입니다.

배지와 별개로, `workflow_status`는 "처리 상태"로 따로 표시합니다(예: 재관측 중, 검토 필요, 완료).

### 4-4. 청구기호 흐름 검사 (엉뚱한 책 찾기) [구현됨]

도서관은 청구기호 순으로 책을 꽂습니다. 그래서 서가를 한 줄로 이어 보고, 흐름에서 튀는 책을 찾습니다. 칸별 정확한 위치(범위표)가 없어도 동작합니다.

- 서가 전체: `GET /scan-sessions/{session_id}/order-check`
- 칸 하나: `GET /scan-sessions/{session_id}/slots/{slot_id}/order-check` (판정은 서가 전체 흐름으로 하고, 이 칸의 책만 보여 줌)
- 순서: 칸 번호 순 → 칸 안 사진 순(촬영 시각) → 사진 안 왼쪽부터 `order`
- 관측에 `image_ref`와 `order`가 있어야 대상이 됩니다.

| status | 의미 |
|---|---|
| `IN_ORDER` | 흐름에 맞음 |
| `OUT_OF_ORDER` | 순서만 어긋남. 앞뒤 책과 같은 분류(분류번호 앞 3자리) 근처 |
| `OUT_OF_PLACE` | 엉뚱한 책. 흐름상 앞·뒤 책과 분류번호 앞 3자리가 다름. `neighbors`에 앞뒤 청구기호(판단 근거)가 옴 |
| `UNIDENTIFIED` | 청구기호를 알 수 없어 검사에서 뺌 |

```json
{"scan_session_id": "SCAN-…", "shelf_id": "A-01", "out_of_order_count": 1, "out_of_place_count": 1,
 "slots": [{"slot_id": "A-01-01", "out_of_order_count": 1, "out_of_place_count": 1,
   "images": [{"image_ref": "img/a.jpg", "image_url": "/observations/OBS-1/image", "image_width": 1920, "image_height": 1080,
     "books": [{"order": 4, "observation_id": "OBS-4", "book_copy_id": "EM0099999", "call_number": "330.9 박12ㄱ",
                "title": "…", "bbox": [400, 0, 80, 600], "status": "OUT_OF_PLACE",
                "neighbors": ["811.16 김55ㄷ", "811.16 박1ㄱ"]}]}]}]}
```

- 조회 API는 요청할 때마다 최신 관측으로 계산합니다.
- AI 분석 완료 시점에는 결과가 검증 결과의 `sequence_status`에 저장됩니다. `OUT_OF_PLACE`인 책은 다른 처리가 걸려 있지 않으면 `workflow_status = REVIEW_REQUIRED`가 되고, 관리자 알림 `OUT_OF_PLACE`가 생깁니다.
- 사진 위에 책 위치를 그릴 때는 `bbox`(원본 픽셀 `[x, y, width, height]`)와 `image_width`·`image_height`로 축소 비율을 맞추세요.

### 4-3. 대체된 결과 처리 규칙 [구현됨, 확정]

다음 두 경우에 이전 결과의 `superseded_by`에 새 결과의 id가 들어갑니다.

1. **재관측:** 로봇이 다시 보러 가서 새 결과가 나온 경우
2. **늦은 관측:** 미관측 판정 뒤에 늦게 도착한 관측이 같은 세션에서 그 책을 확정 또는 후보로 언급한 경우

화면 규칙
- 결과 목록과 칸 화면은 `latest_only=true`로 조회합니다. 그러면 책마다 최종 결과만 남습니다.
- 상세 화면에서 이력을 보여 주려면 `superseded_by`를 따라가면 됩니다(이전 결과 → 새 결과).
- `workflow_status = CLOSED`만으로는 "처리 완료"와 "대체됨"을 구분할 수 없습니다. 대체 여부는 항상 `superseded_by`로 판단하세요.
- `note`는 사람이 읽을 설명용입니다. 화면 로직의 조건으로 쓰지 마세요.

---

## 5. 신뢰도와 누락값 처리

### 5-1. 신뢰도

- 값은 모두 `0.0 ~ 1.0` 실수입니다. 화면에서는 % 변환을 권장합니다.
- 판정 기준값은 [가정]이며, AI 담당자와 협의해 `.env`에서 조정합니다.

| 필드 | 의미 | 기준값 | 기준 미달이면 |
|---|---|---|---|
| `detection_confidence` | 책등을 찾았는가 | 0.5 | `FAILED` |
| `ocr_confidence` | 글자를 제대로 읽었는가 | 0.6 | `UNCERTAIN` 등 |
| `identification_confidence` | AI가 고른 복본이 맞는가 | 0.8 | `UNCERTAIN` |
| `candidates[].score` | 후보별 점수 (`rank` 1이 최고) | — | 점수만으로는 확정하지 않음 |

- 기준값은 지금 API로 제공하지 않습니다. 프론트에서 기준값으로 색을 나누려면 설정 조회 API를 추가해야 합니다(→ 6장).
- 백엔드가 이미 내린 판정 결과(`identification_status`)를 그대로 쓰는 것을 권장합니다. 프론트가 신뢰도로 다시 판정하면 백엔드 판정과 어긋날 수 있습니다.

### 5-2. null이 오는 경우

| 필드 | null인 경우 | 화면 표시 권장 |
|---|---|---|
| `observation_id` | 미관측 결과 | 이미지·신뢰도 영역을 숨기고 "이번 스캔에서 관측되지 않음" 표시 |
| `book_copy_id` | 식별 실패·후보만 있음 | "도서 미확정"으로 표시. 후보 목록은 관측 API의 `candidates`에서 가져옴 |
| `identification_status` | 미관측 결과 | "—" |
| `registered_slot_id` | 복본 미확정, 또는 등록 위치가 없는 복본 | "등록 위치 없음/미확정" |
| `observed_slot_id` | 미관측 결과 | "—" |
| `detected_text` | 글자를 못 읽음 | "읽은 글자 없음" |
| `image_ref` | AI가 이미지를 안 보냄 | 이미지 자리에 대체 표시 |
| `finished_at` | 스캔 진행 중 | "진행 중" |
| `physical_location_confirmed_at` | 로봇이 아직 직접 확인 못 함 (`certainty = ESTIMATED`) | "추정" 표시 |

- 숫자 필드(신뢰도, 개수)는 null이 아니라 항상 숫자로 옵니다. 데이터가 없으면 `0`입니다.
- 예외적으로 미관측 결과에는 신뢰도 자체가 없습니다. 관측 API를 호출하지 않으므로 화면에 "—"로 표시하세요.

---

## 6. 백엔드 보완 제안 [제안: 아직 없음]

화면 흐름을 기준으로 지금 부족한 부분과 추가안입니다. 합의되면 이 순서대로 구현합니다.

### 6-1. 이미지 / 좌표

**현재 상태**
- 이미지 조회 API와 `image_url`은 반영했습니다(3-6절). 남은 것은 파일 보관 위치(AI 서버 공유 폴더인지, 백엔드 업로드인지)를 AI 담당자와 정하는 일입니다.
- **좌표는 AI와 합의해 반영했습니다.** 관측에 `bbox` `[x, y, width, height]`(원본 픽셀), `order`(사진 안 왼쪽부터 순번), `image_width`, `image_height`가 옵니다(4-4절). 아래 제안 중 좌표 부분은 완료입니다.

**제안**
- **좌표**
  - AI 데이터 규격에 `bbox: {x, y, width, height}`를 추가합니다. 단위는 **원본 이미지 픽셀**, 기준점은 왼쪽 위로 합니다.
  - 원본 이미지 크기 `image_width`, `image_height`도 함께 받아야 화면 축소 비율을 맞출 수 있습니다.
- 이미지와 좌표 모두 AI 쪽에서 값을 보내야 가능합니다. **AI 담당자 확인이 먼저**입니다.

### 6-2. 스캔 진행 상태

**현재 상태**
- 세션 `status`는 `IN_PROGRESS` / `COMPLETED` / `INCOMPLETE` 3가지입니다.
- 진행 정도는 `observation_count`(지금까지 받은 관측 수)만 있습니다. 칸별 진행률은 없습니다. 로봇이 칸 단위 진행 상황을 보내지 않기 때문입니다.
- "AI 분석 완료 → 미관측 판정 완료" 시점(`unobserved_checked_at`)은 DB에는 있지만 응답에 빠져 있습니다.
- 세션에서 연결된 스캔 작업(`task_id`)을 찾을 방법이 없습니다. 스캔을 취소하려면 작업 ID가 필요합니다.

**제안: 세션 응답에 아래 필드 추가**

```json
{
  "id": "SCAN-…",
  "status": "COMPLETED",
  "task_id": "TASK-…",
  "analysis_completed_at": "2026-10-30T03:30:10Z",
  "phase": "ANALYZED",
  "observation_count": 5
}
```

`phase`는 화면 표시용으로 아래와 같이 계산합니다.

| phase | 조건 | 화면 문구 예 |
|---|---|---|
| `SCANNING` | `status = IN_PROGRESS` | 촬영 중 |
| `ANALYZING` | 촬영 끝, AI 분석 완료 신호 전 | 분석 중 |
| `ANALYZED` | 미관측 판정까지 완료 | 완료 |
| `INCOMPLETE` | 스캔 중단 | 중단됨 (미관측 판정 안 함) |

칸별 진행률이 필요하면 로봇이 "현재 칸" 보고를 해야 합니다. **로봇 담당자와 협의가 필요**합니다.

### 6-3. 집계 기준

**현재 구현 (대시보드)**
- 복본마다 **가장 최근 검증 결과 1건**을 골라 셉니다.
- 미관측(`NOT_OBSERVED`)을 먼저 보고, 그다음 위치 판정 순서로 분류합니다.
- `never_verified`에는 검증 결과에 한 번도 기록된 적 없는 복본이 들어갑니다. 아직 스캔되지 않았거나, 후보로만 나와 복본이 확정되지 않은 경우입니다.
- 미관측으로 기록된 복본은 `not_observed`로 셉니다. `never_verified`에는 들어가지 않습니다.

**제안: 세션 단위 집계 `GET /scan-sessions/{id}/summary`**

```json
{
  "scan_session_id": "SCAN-…",
  "registered_copy_count": 5,
  "result_count": 6,
  "by_badge": { "NORMAL": 2, "MISPLACED_SUSPECTED": 1, "MISPLACED_CONFIRMED": 0,
                "NOT_OBSERVED": 1, "RECOGNITION_ISSUE": 2, "SCAN_INCOMPLETE": 0 },
  "by_workflow": { "REOBSERVATION_PENDING": 0, "REVIEW_REQUIRED": 4 },
  "by_slot": [
    { "slot_id": "A-03-02", "registered": 2, "normal": 1, "issues": 0 }
  ]
}
```

- 재관측으로 **대체된 결과는 집계에서 빼고**, 최종 결과만 셉니다.
- 배지 분류는 4-2절 우선순위를 그대로 씁니다. 그래야 서버 집계와 화면 배지가 일치합니다.

### 6-4. 결과 화면용 조회 보강

이미 반영한 것: `slot_id` 필터, `latest_only`, `title`, `badge`, `superseded_by`, 이미지 조회 API (3장·4장 참고)

아직 남은 제안

| 제안 | 이유 |
|---|---|
| `GET /verifications/{id}/detail` | 판정·관측(신뢰도·후보·이미지)·복본 정보를 한 번에 |
| `GET /settings/thresholds` | 신뢰도 기준값을 화면에 표시해야 할 경우만 |

---

## 7. 스캔 시작 / 취소 / 수동 수정 — 지금 있는 것

**API는 일부 있습니다.** 이번 범위에 넣을지는 8장에서 정합니다.

| 기능 | API | 상태 | 비고 |
|---|---|---|---|
| 스캔 시작 | `POST /tasks` `{"task_type":"SCAN_SHELF","robot_id":"R001","shelf_id":"A-03"}` | [구현됨] | 관리자 토큰 필요. 세션이 자동 생성되고 응답의 `scan_session_id`로 화면 이동 |
| 스캔 취소 | `POST /tasks/{task_id}/cancel` | [구현됨] | 로봇이 수락 전이면 즉시 취소, 실행 중이면 로봇에 취소 명령. 세션은 `INCOMPLETE`가 되고 미관측 판정은 하지 않음 |
| 작업 상태 조회 | `GET /tasks/{task_id}`, `GET /tasks?task_type=SCAN_SHELF` | [구현됨] | 세션 → 작업 연결 필드는 6-2절 [제안] |
| 재관측 요청 | `POST /tasks` `{"task_type":"REOBSERVE_BOOK","robot_id":…,"verification_id":…}` | [구현됨] | |
| 등록 위치 변경 | `PUT /copies/{id}/registered-location` `{"slot_id":…,"reason":…}` | [구현됨] | 이력이 남음 |
| 알림 확인·처리 완료 | `POST /alerts/{id}/confirm`, `/resolve` | [구현됨] | 처리 완료하면 연결된 검토 결과도 닫힘 |
| **판정 결과 직접 수정** | — | **없음** | 예: "오배가 의심 → 정상으로 고침". 이력·사유 저장 방식을 정해야 해서 새로 설계해야 함 |

---

## 8. 결정 필요 목록

| # | 항목 | 백엔드 제안 | 확인할 사람 |
|---|---|---|---|
| 1 | ID 형식 | 2장 그대로 사용 (`A-03`, `A-03-02`, `BOOK-125`, 결과는 정수 ID) | 프론트·로봇·AI |
| 2 | 상태 코드 | 4개 축을 유지하고, 화면은 4-2절 우선순위로 대표 배지 계산 | 프론트 |
| 3 | 신뢰도 표시 | 백엔드 판정을 그대로 표시하고, 신뢰도는 % 수치로만 보여 줌 | 프론트·AI |
| 4 | 누락값 | 5-2절 표대로 (null 유지, 화면에서 대체 문구) | 프론트 |
| 5 | 이미지 | `GET /observations/{id}/image` 추가. 파일 보관 위치는 AI와 결정 | AI·백엔드 |
| 6 | 좌표 | `bbox` (원본 픽셀, 왼쪽 위 기준) + 원본 이미지 크기 | AI |
| 7 | 스캔 진행 상태 | 세션에 `phase`·`task_id`·`analysis_completed_at` 추가. 칸별 진행률은 로봇 보고가 생기면 | 로봇·백엔드 |
| 8 | 집계 기준 | 대체된 결과 제외, 배지 우선순위로 분류. 세션 요약 API 추가 | 프론트 |
| 9 | 이번 범위 | 아래 참고 | **팀 결정** |

**#9 이번 범위에 대한 백엔드 의견**
- **조회 4개 화면:** 이번 범위에 넣습니다.
- **스캔 시작·취소:** API가 이미 있으니 넣어도 됩니다. 버튼 2개와 작업 상태 표시만 붙이면 됩니다.
- **수동 수정:** 판정 결과를 고치는 API가 아직 없고, 이력·권한 설계가 필요합니다. 다음 단계로 미루는 것을 권장합니다.
- 다만 "등록 위치 변경"과 "알림 처리 완료"는 이미 있으니 필요하면 이번에 붙일 수 있습니다.
