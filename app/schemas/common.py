"""모든 API가 공통으로 쓰는 오류 응답 형식.

예시:
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "요청 데이터 형식이 올바르지 않습니다.",
    "details": [{"field": "confidence.ocr", "message": "..."}]
  }
}
"""
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Generic, TypeVar

from pydantic import AfterValidator, BaseModel

T = TypeVar("T")

# DB에는 시간대 없는 UTC로 저장되므로, 응답에서는 UTC 표시("Z")를 붙여 내보낸다. 예: 2026-10-29T02:30:00Z
UtcDatetime = Annotated[datetime, AfterValidator(lambda d: d.replace(tzinfo=UTC) if d.tzinfo is None else d)]


class Page(BaseModel, Generic[T]):
    """목록 조회 공통 응답."""

    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"    # JSON 형식·값 오류 (422)
    UNAUTHORIZED = "UNAUTHORIZED"            # API 키 없음·불일치 (401)
    FORBIDDEN = "FORBIDDEN"                  # 권한 없음, 예: 관리자 승인 필요 (403)
    NOT_FOUND = "NOT_FOUND"                  # 대상 없음 (404)
    ALREADY_EXISTS = "ALREADY_EXISTS"        # 같은 ID·ISBN이 이미 등록됨 (409)
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"  # 이미 처리된 요청, 예: 같은 observation_id (409)
    INVALID_STATE = "INVALID_STATE"          # 현재 상태에서 불가능한 요청, 예: 완료된 작업 취소 (409)
    INTERNAL_ERROR = "INTERNAL_ERROR"        # 서버 내부 오류 (500)


class ErrorDetail(BaseModel):
    field: str | None = None
    message: str


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody

    @classmethod
    def build(cls, code: ErrorCode, message: str, details: list[dict[str, Any]] | None = None) -> "ErrorResponse":
        return cls(error=ErrorBody(code=code, message=message, details=details))
