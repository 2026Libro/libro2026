# Backend

REST API, 데이터베이스, 스캔 세션·관측 데이터 관리, 도서 위치·상태 판정 및 파트 간 연동을 담당합니다.

FastAPI + MySQL(SQLAlchemy, Alembic). 로봇이 올린 서가 사진을 AI OCR에 보내고, 읽은 글자를 도서관 장서와 매칭한 뒤 청구기호 흐름에서 튀는 **엉뚱한 책**을 찾아 관리자 화면에 제공합니다.

```
로봇 ──사진──▶ 백엔드 ──▶ AI OCR ──▶ 백엔드 (매칭·흐름 검사) ──▶ 프론트 화면
```

## 실행 (이 폴더 `backend/`에서)

Python 3.13, MySQL 8 기준입니다.

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt     # macOS·Linux: .venv/bin/python
copy .env.example .env                                       # DB 비밀번호 등 실제 값으로 수정 (업로드 금지)
# MySQL root로 scripts/init_db.sql 1회 실행 (개발 DB·테스트 DB·계정 생성)
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- API 문서: `http://<서버>:8000/docs`
- 테스트: `.venv\Scripts\python -m pytest -q` (테스트 전용 DB `library_robot_test` 사용)
- 장치 API 키 발급: `.venv\Scripts\python -m scripts.create_device_key --type ROBOT --device-id R001` (AI는 `--type AI`)
- 장서 넣기: `.venv\Scripts\python -m scripts.import_library_csv` (크롤링 CSV는 저장소에 올리지 않음, 담당자에게 받기)

## 연동 문서 (`docs/`)

| 문서 | 대상 |
|---|---|
| [docs/frontend-api.md](docs/frontend-api.md) | 프론트: 화면별 조회 API, 상태 코드 |
| [docs/ai-integration.md](docs/ai-integration.md) | AI: OCR 서버 요청·응답 형식, 결과 전송 |
| [docs/robot-photo-upload.md](docs/robot-photo-upload.md) | 하드웨어: 사진 업로드, `scripts/robot_upload.py` |
| [docs/erd.md](docs/erd.md) | DB 구조 |

공통 API 명세를 저장소 최상위 `docs/`로 옮기는 것은 팀 협의 후 진행합니다.

## 폴더

| 폴더 | 내용 |
|---|---|
| `app/` | FastAPI 앱 (routers, services, models, schemas) |
| `alembic/` | DB 마이그레이션 |
| `scripts/` | 장서·범위표 가져오기, 키 발급, 테스트 세션, 로봇 업로드 도구 |
| `tests/` | pytest |
| `data/` | 로컬 데이터 (사진·OCR 결과, 업로드 금지) |
