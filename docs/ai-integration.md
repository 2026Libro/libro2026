# AI 연동 안내 (백엔드 → AI 팀)

백엔드는 책등 OCR 결과로 **엉뚱한 책(청구기호 흐름에서 튀는 책)**을 찾습니다.

## 0. 기본 흐름 (2026-11-23 변경) — 사진은 백엔드를 거칩니다

```
로봇 ──사진──▶ 백엔드(사진 저장) ──사진──▶ AI OCR 서버
                                ◀── 책별 글자 조각 + 신뢰도 + 박스 좌표 ──
              백엔드: 매칭 → 흐름 검사 → 관리자 화면
```

AI는 **OCR만** 하면 됩니다. 매칭·판정·세션 관리는 백엔드가 합니다. 아래 둘 중 하나로 연결합니다.

### 0-1. (권장) AI가 OCR 서버(API)를 띄우고 백엔드가 호출 [가정: 형식 협의 필요]

**요청 (백엔드 → AI)** `POST {AI_OCR_URL}`
- 본문: 사진 파일 그대로 (`Content-Type: image/jpeg` 또는 `image/png`)
- 쿼리: `image_id`, `scan_session_id`, `slot_id` (참고용)
- 헤더: `X-API-Key` (AI 쪽에서 키가 필요하면 BE에 알려 주세요)
- 제한 시간 120초 (사진 1장 약 30초 기준)

**응답 (AI → 백엔드)** `200`, JSON

```json
{
  "books": [
    {
      "order": 1,
      "bbox": [345, 190, 45, 470],
      "detection_confidence": 0.97,
      "fragments": [
        {"text": "2013한국인터넷백서", "confidence": 1.0},
        {"text": "004,50951", "confidence": 0.938},
        {"text": "평55번", "confidence": 0.67}
      ]
    }
  ]
}
```

| 필드 | 필수 | 설명 |
|---|---|---|
| `books` | ✔ | 책(책등 그룹) 목록. 지금 출력의 `ID 001`, `ID 002` … 하나가 1권 |
| `order` | | 사진 안 왼쪽부터 1, 2, 3… 없으면 `bbox`의 x 좌표로 백엔드가 정함 |
| `bbox` | 화면 표시에 필요 | 원본 픽셀 `[x, y, width, height]`, 왼쪽 위 원점. 책등 그룹 전체를 감싸는 박스 |
| `detection_confidence` | | 책등 탐지 신뢰도 0~1 (없으면 0.9로 봄) |
| `fragments` | ✔ | 읽은 글자 조각과 신뢰도 **0~1** (93.8% → 0.938). 순서·오타 상관없음. 문자열만 보내도 됨 |

- 지금 텍스트 출력(`ID 001 - 2013한국인터넷백서(100.0%), …`)에 **박스 좌표만 더해 JSON으로** 주면 됩니다.
- 오류면 4xx/5xx로 응답해 주세요. 백엔드는 그 사진을 실패로 표시하고 관리자가 다시 요청할 수 있습니다.

### 0-2. AI가 결과를 직접 보내기 (OCR 서버를 띄우기 어려울 때)

백엔드 설정에 `AI_OCR_URL`을 비워 두면 백엔드가 AI를 부르지 않습니다. 사진 목록을 보고 결과를 보내 주세요.
- 처리할 사진: `GET /scan-sessions/{scan_session_id}/images` → `ocr_status`가 `PENDING`인 사진, 파일은 `GET /scan-images/{id}/file`
- 결과 보내기: `POST /scan-images/{id}/ocr-result` (헤더 `X-API-Key: <AI 키>`), 본문은 위 응답 JSON과 같음. 사진 1장당 1번

> 두 방식 모두 로봇이 촬영 종료를 보고하고 사진 OCR이 모두 끝나면 **백엔드가 분석 완료를 자동 처리**합니다 (아래 3절 신호 불필요).
> 아래 1~3절의 `POST /observations` 방식(AI가 책 1권씩 보내기)도 계속 쓸 수 있습니다.


## 1. 준비물 (BE가 전달)

| 항목 | 내용 |
|---|---|
| 서버 주소 | `http://<BE PC 주소>:8000` (같은 PC면 `http://127.0.0.1:8000`). API 문서: `/docs` |
| AI API 키 | 요청 헤더 `X-API-Key: <키>` (BE가 따로 전달) |
| 매칭용 도서 목록 (선택) | `LIBRO2026_AI_catalog_대출실.csv` (대출실 72,299권, 청구기호 순, 2026-11-23 갱신). 매칭은 백엔드가 하므로 참고용 |
| 테스트 세션 ID | `SCAN-…` (BE가 만들어 전달. 서가 `L-01`, 칸 `L-01-01` ~ `L-01-10`, 로봇 `R001`) |

### 매칭용 도서 목록 열
`등록번호, 청구기호, 대표청구기호, 서명, 저자, 출판사, 발행연도, 자료실`
- **등록번호**: 백엔드로 보낼 `book_copy_id` (예: `EM0421696`)
- **청구기호**: 책등 라벨 그대로 (복본 기호 포함, 예: `811.16 김14당 c.2`). 1권째는 `c.N`이 없습니다.
- **대표청구기호**: 복본 기호를 뗀 값. 같은 책의 복본끼리 같습니다.

## 2. 관측 1건 보내기 — `POST /observations`

책 1권 = 요청 1건입니다. **매칭은 백엔드가 합니다** (2026-11-23 합의). AI는 책등에서 읽은 글자만 보내면 됩니다.

### 2-1. 기본: 읽은 글자 조각만 보내기

```json
{
  "observation_id": "OBS-20261123-0001",
  "scan_session_id": "SCAN-20261123065512-a8c455",
  "robot_id": "R001",
  "shelf_id": "L-01",
  "slot_id": "L-01-01",
  "text_fragments": ["2013한국인터넷백서", "000", "004,50951", "방55한", "C.2"],
  "confidence": {"detection": 0.97, "ocr": 0.81},
  "image_ref": "2026-11-23/L-01-01_001.jpg",
  "order": 2,
  "bbox": [300, 40, 90, 900],
  "image_width": 1920,
  "image_height": 1080,
  "captured_at": "2026-11-23T16:01:00+09:00"
}
```

- `text_fragments`: OCR이 책 1권에서 읽은 글자 조각들 (최대 30개, 각 200자). **순서·오타·잘린 글자 상관없음.** 라벨 줄, 제목 일부, 띠지 숫자를 그대로 넣어 주세요. 백엔드가 분류번호·저자기호·연도·복본 기호(c.N)·제목을 골라 장서와 비교합니다.
- 라벨을 한 줄로 읽었다면 `call_number_text`(청구기호, 예: `811.16 김14당 c.2`)와 `title_text`(서명)로 보내도 됩니다. 이걸로 못 찾으면 `text_fragments`로 다시 찾습니다.
- 백엔드가 고른 결과는 응답의 `match_method`(`EXACT`·`SAME_BOOK`·`SIMILAR`·`TITLE`·`FRAGMENTS`·`NONE`), `reported_book_copy_id`(확정한 등록번호, 못 하면 null), `candidates`에 나옵니다.
- `confidence.identification`은 생략하세요 (백엔드 매칭 점수를 씁니다).

### 2-2. (선택) AI가 직접 등록번호를 고른 경우

`book_copy_id`나 `candidates`를 보내면 백엔드는 다시 매칭하지 않고 그 값을 씁니다.

```json
{
  "observation_id": "OBS-20261116-0001",
  "scan_session_id": "SCAN-20261116065512-a8c455",
  "robot_id": "R001",
  "shelf_id": "L-01",
  "slot_id": "L-01-01",
  "detected_text": "811.16 김14당 c.2 / 시집 제목",
  "book_copy_id": "EM0421697",
  "confidence": {"detection": 0.97, "ocr": 0.90, "identification": 0.92},
  "candidates": [{"book_copy_id": "EM0421697", "score": 0.92}],
  "image_ref": "2026-11-16/L-01-01_001.jpg",
  "order": 2,
  "bbox": [300, 40, 90, 900],
  "image_width": 1920,
  "image_height": 1080,
  "captured_at": "2026-11-16T16:01:00+09:00"
}
```

| 필드 | 필수 | 설명 |
|---|---|---|
| `observation_id` | ✔ | AI가 만드는 고유 ID. `OBS-`로 시작. **같은 ID로 다시 보내면 중복 저장되지 않음** (재전송 안전) |
| `scan_session_id`, `robot_id`, `shelf_id`, `slot_id` | ✔ | 세션 정보와 맞아야 함. `slot_id`는 `shelf_id`의 칸 (`L-01-01`) |
| `book_copy_id` | | 확정한 등록번호. **확정 못 하면 null** (임의로 고르지 않기) |
| `candidates` | | 후보 등록번호와 점수 (최대 20개). 복본 기호(`c.2`)를 못 읽어 같은 책 복본 중 어느 것인지 모르면 후보로 모두 보내기 |
| `confidence` | ✔ | 0.0~1.0. 탐지·OCR·식별 신뢰도 |
| `image_ref` | 흐름 검사에 필요 | **이미지 ID**. 같은 사진에서 나온 책은 같은 값 |
| `order` | 흐름 검사에 필요 | 같은 사진 안에서 **왼쪽부터 1, 2, 3…** |
| `bbox` | | 원본 픽셀 `[x, y, width, height]` (왼쪽 위 기준) |
| `image_width`, `image_height` | | 원본 이미지 크기 |
| `captured_at` | ✔ | 촬영 시각, 시간대 필수 (`+09:00`) |
| `text_fragments` | 매칭에 필요 | 읽은 글자 조각 목록 (2-1 참고) |
| `call_number_text`, `title_text` | | 한 줄로 읽은 청구기호·서명 (있으면 먼저 사용) |
| `detected_text` | | OCR로 읽은 글자 기록용 (최대 500자, 매칭에는 안 씀) |

**응답**
- `201` 저장됨 / `200` 같은 내용 재전송 (중복 저장 안 함)
- `409` 같은 `observation_id`인데 내용이 다름 / `401` API 키 오류
- `422` 형식 오류: 응답 `error.details`에 어느 필드가 왜 틀렸는지 나옴 (예: 없는 등록번호, bbox가 이미지 밖, `order`만 있고 `image_ref` 없음)

## 3. 다 보냈으면 — `POST /scan-sessions/{scan_session_id}/analysis-complete`

이 세션의 관측을 모두 보냈다는 신호입니다. **세션당 1번**, 로봇이 촬영 종료를 보고한 뒤에 보냅니다 (테스트에서는 BE가 대신 종료 처리).
이때 백엔드가 서가 전체 흐름 검사를 기록하고, 엉뚱한 책은 관리자 알림으로 넘깁니다. 응답의 `out_of_place_count`가 엉뚱한 책 수입니다.

## 4. 결과 확인 (인증 없음)

- 서가 전체 흐름 검사: `GET /scan-sessions/{scan_session_id}/order-check`
- 칸 하나: `GET /scan-sessions/{scan_session_id}/slots/L-01-01/order-check`
- 책별 판정: `GET /verifications?scan_session_id=…&latest_only=true&limit=100`

흐름 검사 결과의 `status`
- `IN_ORDER` 정상
- `OUT_OF_ORDER` 같은 분류 안에서 순서만 어긋남
- `OUT_OF_PLACE` 엉뚱한 책 (앞뒤 책과 분류번호 앞 3자리가 다름, `neighbors`에 근거)
- `UNIDENTIFIED` 청구기호를 알 수 없어 검사 제외 (식별 실패, 후보가 서로 다른 책)
- `DUPLICATE` 겹쳐 찍은 사진에서 앞 사진과 같은 책 (한 권으로 보고 검사 제외, `duplicate_of`에 처음 본 관측 ID)

## 5. 흐름 검사가 잘 되려면

1. **`image_ref`와 `order`를 꼭 보내 주세요.** 없으면 위치를 몰라 흐름 검사에서 빠집니다.
2. 한 칸을 여러 장으로 찍으면 **왼쪽 사진부터 촬영 시각 순서**가 되게 해 주세요 (백엔드는 칸 번호 → 촬영 시각 → `order` 순으로 이어 봅니다).
   사진끼리 겹쳐도 됩니다. 앞 사진 끝과 다음 사진 앞에 같은 책이 나오면 백엔드가 한 권으로 합칩니다 (가장자리에 잘린 책 포함). 겹친 책도 빼지 말고 그대로 보내 주세요.
3. 복본을 구분 못 해도 괜찮습니다. 후보가 모두 같은 책이면 흐름 검사는 정상 동작합니다.
4. 같은 책을 다시 찍어 결과를 고칠 때는 새 `observation_id`로 보내고 `reobservation_of`에 이전 관측 ID를 넣어 주세요. 최신 결과로 대체됩니다.

## 6. 점검 결과 (BE에서 실제 HTTP로 확인, 2026-11-16)

대출실 실제 도서 10권을 위 형식으로 보냈습니다.
- 시집(811.16) 사이에 끼운 경제 책 `330.01 B896e`는 **엉뚱한 책**으로 판정되었고 관리자 알림이 생겼습니다.
- 자리만 바꾼 시집 2권은 **순서 오류**로 판정되었습니다.
- 나머지 8권은 정상이었습니다.
