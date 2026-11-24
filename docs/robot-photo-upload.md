# 로봇 사진 업로드 안내 (백엔드 → 로봇 팀)

로봇은 칸 사진을 **백엔드에 올리기만** 하면 됩니다. 백엔드가 사진을 저장하고 AI OCR에 보내고, 결과로 엉뚱한 책을 찾습니다. 로봇이 AI와 직접 통신할 필요는 없습니다.

```
로봇 ──사진──▶ 백엔드 ──▶ AI OCR ──▶ 백엔드 (매칭·판정) ──▶ 관리자 화면
```

## 1. 가장 쉬운 방법 — `scripts/robot_upload.py` 한 줄 실행

이 파일 하나만 로봇 PC(라즈베리파이 등)에 복사하면 됩니다. 파이썬 3.9 이상, 추가 설치 없음.
명령 한 번으로 **세션 만들기 → 촬영·업로드 → 촬영 끝 신호**까지 합니다.

```bash
# 칸마다 촬영 명령을 실행하고 바로 올리기 ({file} 자리에 저장할 파일 경로가 들어감)
python robot_upload.py --server http://10.80.211.86:8000 --key <로봇키> --shelf R-01 \
    --capture "libcamera-still -n -o {file}" --slots R-01-01 R-01-02

# 이미 찍은 사진 올리기 (칸:파일, 왼쪽 사진부터)
python robot_upload.py --server http://10.80.211.86:8000 --key <로봇키> --shelf R-01 \
    R-01-01:shot1.jpg R-01-01:shot2.jpg R-01-02:shot3.jpg
```

- `--capture`에는 쓰시는 카메라 촬영 명령을 넣으면 됩니다 (`libcamera-still`, `fswebcam`, 직접 만든 스크립트 등).
- 중간에 실패하면 세션을 '중단(INCOMPLETE)'으로 닫습니다. 다시 실행하면 새 세션으로 찍습니다.
- 키는 `--key` 대신 환경변수 `ROBOT_API_KEY`로 줘도 됩니다.

## 1-1. 직접 구현할 때의 순서

1. 세션 만들기: `POST /scan-sessions`, 헤더 `X-API-Key: <로봇 키>`, 본문 `{"robot_id": "R001", "shelf_id": "R-01"}` → 응답의 `id`(`SCAN-…`)가 세션 ID. 로봇은 자기 세션만 만들 수 있습니다.
2. 칸마다 사진 올리기: `POST /scan-sessions/{scan_session_id}/images` (아래 2절)
3. 촬영이 끝나면: `POST /scan-sessions/{scan_session_id}/finish`, 본문 `{"status": "COMPLETED"}` (중간에 멈췄으면 `"INCOMPLETE"`)

AI 분석 완료 신호는 필요 없습니다. 사진 OCR이 모두 끝나면 백엔드가 알아서 마무리합니다.

## 2. 사진 올리기 — `POST /scan-sessions/{scan_session_id}/images`

| 항목 | 내용 |
|---|---|
| 헤더 | `X-API-Key: <로봇 API 키>`, `Content-Type: image/jpeg` 또는 `image/png` |
| 본문 | **사진 파일 그대로** (multipart 아님), 최대 20MB |
| 쿼리 `slot_id` | 필수. 찍은 칸 (예: `R-01-01`) |
| 쿼리 `captured_at` | 필수. 촬영 시각, 시간대 포함 (예: `2026-11-23T16:01:00+09:00`) |
| 쿼리 `image_id` | 선택. 로봇이 정하는 사진 ID (`IMG-` + 영문·숫자·`-`·`_` 40자 이내). 넣으면 **같은 요청을 다시 보내도 한 번만 저장**됩니다 (재전송 안전) |

```python
import requests
with open("R-01-01_001.jpg", "rb") as f:
    res = requests.post(
        f"{SERVER}/scan-sessions/{session_id}/images",
        params={"slot_id": "R-01-01", "captured_at": "2026-11-23T16:01:00+09:00", "image_id": "IMG-R001-0001"},
        data=f.read(),
        headers={"X-API-Key": ROBOT_KEY, "Content-Type": "image/jpeg"},
        timeout=30,
    )
print(res.status_code, res.json()["id"], res.json()["ocr_status"])
```

**응답**
- `202` 저장함 (OCR은 백엔드가 이어서 처리, 사진 1장 약 30초)
- `200` 같은 `image_id`로 이미 올린 사진 (다시 저장하지 않음)
- `401` 키 오류 / `403` 다른 로봇의 세션 / `404` 세션 없음 / `409` 이미 종료된 세션 / `413` 20MB 초과 / `415` JPEG·PNG 아님 / `422` 서가에 없는 칸, 읽을 수 없는 사진

## 3. 촬영 규칙

1. 한 칸을 여러 장으로 찍으면 **왼쪽부터 순서대로** 찍고 올려 주세요. 백엔드는 칸 번호 → 촬영 시각 순으로 사진을 이어 봅니다.
2. 사진끼리 겹쳐도 됩니다. 앞 사진 끝과 다음 사진 앞의 같은 책은 백엔드가 한 권으로 합칩니다.
3. 칸 번호 순서가 서가에 책이 이어지는 순서라고 봅니다 [가정].

## 4. 진행 상태 확인 (인증 없음)

`GET /scan-sessions/{scan_session_id}/images` → 사진별 `ocr_status`: `PENDING`(AI 대기) · `PROCESSING` · `DONE` · `FAILED`
