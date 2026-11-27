"""실무자가 이해할 수 있는 한국어 오류 응답."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError


class AppError(Exception):
    def __init__(self, status_code: int, message: str, detail: object | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.detail = detail


def not_found(what: str) -> AppError:
    return AppError(404, f"{what}을(를) 찾을 수 없습니다.")


_FIELD_MSG = {
    "missing": "필수 값입니다",
    "string_too_short": "값이 너무 짧습니다",
    "string_too_long": "값이 너무 깁니다",
    "int_parsing": "정수여야 합니다",
    "float_parsing": "숫자여야 합니다",
    "datetime_parsing": "날짜/시각 형식이 올바르지 않습니다 (예: 2026-01-31T09:00)",
    "datetime_from_date_parsing": "날짜/시각 형식이 올바르지 않습니다 (예: 2026-01-31T09:00)",
    "enum": "허용되지 않은 값입니다",
    "greater_than_equal": "값이 너무 작습니다",
    "less_than_equal": "값이 너무 큽니다",
    "greater_than": "값이 너무 작습니다",
    "value_error": "값이 올바르지 않습니다",
}


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        body: dict = {"message": exc.message}
        if exc.detail is not None:
            body["detail"] = exc.detail
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        fields = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path"))
            msg = _FIELD_MSG.get(err.get("type", ""), None)
            if err.get("type") == "value_error":
                msg = str(err.get("ctx", {}).get("error", "")) or msg
            fields.append({"field": loc, "message": msg or err.get("msg", "값이 올바르지 않습니다")})
        return JSONResponse(
            status_code=422, content={"message": "입력값이 올바르지 않습니다.", "detail": fields}
        )

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError):
        text = str(exc.orig).lower()
        if "unique" in text or "duplicate" in text:
            msg = "같은 값이 이미 등록되어 있습니다."
        elif "foreign key" in text:
            msg = "다른 데이터에서 참조 중이거나, 참조하는 데이터가 없습니다. 삭제 대신 비활성화를 사용하세요."
        else:
            msg = "데이터 제약 조건에 맞지 않습니다."
        return JSONResponse(status_code=409, content={"message": msg})
