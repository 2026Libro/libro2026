"""예외를 공통 오류 응답 형식(ErrorResponse)으로 바꿔 주는 처리기.

서비스 코드에서는 AppError를 raise하면 된다.
    raise AppError(ErrorCode.NOT_FOUND, "도서를 찾을 수 없습니다.", status_code=404)
"""
import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.common import ErrorCode, ErrorResponse

logger = logging.getLogger(__name__)

_HTTP_STATUS_TO_CODE = {
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.INVALID_STATE,
    422: ErrorCode.VALIDATION_ERROR,
}


class AppError(Exception):
    def __init__(self, code: ErrorCode, message: str, status_code: int = 400, details: list[dict] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def _json(status_code: int, body: ErrorResponse) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


async def _app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return _json(exc.status_code, ErrorResponse.build(exc.code, exc.message, exc.details))


async def _validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        # loc 예: ("body", "confidence", "ocr") -> "confidence.ocr"
        {"field": ".".join(str(p) for p in err["loc"] if p != "body") or None, "message": err["msg"]}
        for err in exc.errors()
    ]
    body = ErrorResponse.build(ErrorCode.VALIDATION_ERROR, "요청 데이터 형식이 올바르지 않습니다.", details)
    return _json(status.HTTP_422_UNPROCESSABLE_CONTENT, body)


async def _http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    return _json(exc.status_code, ErrorResponse.build(code, str(exc.detail)))


async def _stale_data_handler(_request: Request, _exc: StaleDataError) -> JSONResponse:
    # robot_tasks.version 불일치: 다른 요청이 같은 작업 상태를 먼저 바꿈 (BE-017 작업 상태 불일치 방지)
    body = ErrorResponse.build(ErrorCode.INVALID_STATE, "다른 요청이 먼저 상태를 변경했습니다. 다시 조회 후 시도하세요.")
    return _json(status.HTTP_409_CONFLICT, body)


async def _unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    # 내부 정보(DB 주소, 스택 등)는 응답에 넣지 않고 서버 로그에만 남긴다.
    logger.exception("Unhandled error", exc_info=exc)
    body = ErrorResponse.build(ErrorCode.INTERNAL_ERROR, "서버 내부 오류가 발생했습니다.")
    return _json(status.HTTP_500_INTERNAL_SERVER_ERROR, body)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(StaleDataError, _stale_data_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
