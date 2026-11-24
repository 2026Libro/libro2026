# 2026Libro

AI 기반 자율주행 도서 인출 및 관리 로봇 프로젝트입니다.

| 역할 | 디렉터리 | 장기 브랜치 |
|---|---|---|
| AI | `ai/` | `ai` |
| Backend | `backend/` | `be` |
| Frontend | `frontend/` | `fe` |
| Hardware | `hardware/` | `hw` |
| 공통 API·인터페이스 | `docs/` | 관련 역할 브랜치 |

개발 전 [협업 정책](CONTRIBUTING.md)을 확인하세요. 모든 기능 작업은 Issue를 만들고 최신 `main`에서 `feature/#번호-기능명`을 생성하여 진행합니다.

```bash
git switch main
git pull --ff-only origin main
git switch -c 'feature/#1-paddleocr'
```

기능 PR은 `main`으로 보냅니다. 최소 1명의 다른 팀원 승인과 필수 검사 통과 후 병합합니다.
