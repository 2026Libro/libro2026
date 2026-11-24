# 2026Libro

AI 기반 자율주행 도서 인출 및 관리 로봇 프로젝트입니다.

| 역할 | 디렉터리 | 장기 브랜치 |
|---|---|---|
| AI | `ai/` | `ai` |
| Backend | `backend/` | `be` |
| Frontend | `frontend/` | `fe` |
| Hardware | `hardware/` | `hw` |
| 공통 API·인터페이스 | `docs/` | 관련 역할 브랜치 |

개발 전 [협업 정책](CONTRIBUTING.md)을 확인하세요.

Issue 생성 → 담당 브랜치에서 개발·Commit·Push → 담당 브랜치에서 main으로 PR → 리뷰 후 Merge 순서로 진행합니다.
새 feature 브랜치는 만들지 않습니다. main, ai, be, fe, hw는 삭제하지 않습니다.

AI 작업 시작 예시:

```bash
git fetch origin
git switch ai
git pull --ff-only origin ai
git merge origin/main
```

PR은 다른 팀원 최소 1명 승인 및 필수 검사 통과 후 merge commit 방식으로 병합합니다.
