from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.health import HealthResponse
from app.services.health import check_database

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(response: Response, db: Session = Depends(get_db)) -> HealthResponse:
    """서버 및 DB 연결 상태 확인. DB 연결 실패 시 503을 반환한다."""
    db_error = check_database(db)
    if db_error:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="error", database="error", database_error=db_error)
    return HealthResponse(status="ok", database="ok")
