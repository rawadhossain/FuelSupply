"""Normalized errors for calls to the Intelligence service.

Unlike the simulator, Intelligence uses one consistent envelope for every
error: `HTTPException(status, {"code": ..., "message": ...})`, which FastAPI
wraps as `{"detail": {"code": ..., "message": ...}}`. See intelligence/service.py.
"""

from __future__ import annotations


class IntelligenceError(Exception):
    """Base for all Intelligence-integration failures."""


class IntelligenceUnavailableError(IntelligenceError):
    """Network failure, timeout, or the service's own 503 MODEL_UNAVAILABLE.

    Per REQ-009a: Core must fall back to the heuristic allocator directly
    (`intelligence.heuristic.plan`, importable without the Intelligence
    container) rather than surface this as a hard failure to the operator.
    """

    def __init__(self, code: str, message: str, status_code: int | None = None) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(f"{code}: {message}")


class IntelligenceInvalidInputError(IntelligenceError):
    """The snapshot we sent was rejected (422 INVALID_SNAPSHOT) — a bug in our
    own snapshot-building, not a simulator or Intelligence problem. Per
    REQ-009b: reject and alert, never retry blindly."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(f"{code}: {message}")
