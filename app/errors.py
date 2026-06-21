"""Contract error envelope: {"error": {"code", "message", "details"}}."""
from fastapi import Request
from fastapi.responses import JSONResponse


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


def err(status: int, code: str, message: str, details: dict | None = None) -> APIError:
    return APIError(status, code, message, details)
