from __future__ import annotations


class OpenApiError(Exception):
    """Error response from the NEXON Open API (`{"error": {"name": ..., "message": ...}}`)."""

    def __init__(self, status: int, code: str | None, message: str | None, path: str = ""):
        self.status = status
        self.code = code
        self.message = message
        self.path = path
        super().__init__(f"{path} → {status} {code or ''} {message or ''}".strip())


class RateLimitError(OpenApiError):
    """429 OPENAPI00007: 호출량 초과 (재시도 후에도 계속될 때)."""


class DataNotReadyError(OpenApiError):
    """400 OPENAPI00009: 데이터 준비 중. 잠시 뒤 다시 시도."""


class MaintenanceError(OpenApiError):
    """400 OPENAPI00010 게임 점검 중 / 503 OPENAPI00011 API 점검 중. 수집 중단."""


class NotFoundError(OpenApiError):
    """존재하지 않는 닉네임 등 잘못된 식별자·파라미터 (OPENAPI00003 / OPENAPI00004)."""


class BudgetExceededError(Exception):
    """일일 호출 예산 또는 실행당 예산을 모두 사용함. 요청을 보내지 않고 중단."""


RATE_LIMIT = "OPENAPI00007"
DATA_NOT_READY = "OPENAPI00009"
GAME_MAINTENANCE = "OPENAPI00010"
API_MAINTENANCE = "OPENAPI00011"
NOT_FOUND_CODES = ("OPENAPI00003", "OPENAPI00004")
