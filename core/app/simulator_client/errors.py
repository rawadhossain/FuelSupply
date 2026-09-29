"""Normalized simulator-integration errors.

The simulator uses two distinct error envelope shapes (see CONTRACT-SIM-REST /
CONTRACT-SIM-ALLOC in docs/api-contracts.md, verified against a live
instance): `{"error": {...}}` for fault-injected 503s, and `{"detail": {...}}`
(or `{"detail": [...]}` for FastAPI's own pydantic validation) for documented
4xx business rejections. Callers should never need to inspect a raw envelope
shape themselves — only these exception types.
"""

from __future__ import annotations


class SimulatorError(Exception):
    """Base for all simulator-integration failures."""


class SimulatorUnavailableError(SimulatorError):
    """Network failure, timeout, or a fault-injected 503 (`unavailable`/`error_rate`).

    Not the caller's fault — this is the "backend dependency unavailable"
    resilience path (REQ-009d): retry/backoff and fall back to cached state,
    don't treat it as a bad request.
    """

    def __init__(self, code: str, message: str, status_code: int | None = None) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(f"{code}: {message}")


class SimulatorRequestError(SimulatorError):
    """A documented 4xx business-logic rejection (`{"detail": {"code": ..., "message": ...}}`),
    e.g. NOT_FOUND, ROUTE_MISMATCH, IDEMPOTENCY_KEY_MISMATCH. The request itself
    was invalid per the simulator's validation order, not a connectivity problem.
    """

    def __init__(self, code: str, message: str, status_code: int) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(f"{code}: {message}")


class SimulatorInvalidResponseError(SimulatorError):
    """The simulator returned 2xx but the body didn't match the documented schema.

    Per REQ-009b: reject the input and raise an alert, never silently pass
    unvalidated data downstream.
    """

    def __init__(self, endpoint: str, raw_body: object, validation_error: Exception) -> None:
        self.endpoint = endpoint
        self.raw_body = raw_body
        self.validation_error = validation_error
        super().__init__(f"Invalid response shape from {endpoint}: {validation_error}")
