"""
Middleware for request correlation ID tracking and structured logging context.
"""

import uuid
from collections.abc import Callable

import structlog
from django.http import HttpRequest, HttpResponse


class CorrelationIdMiddleware:
    """
    Intercepts HTTP requests to assign a correlation ID (from X-Correlation-ID
    header or a newly generated UUID4) and binds it to structlog's contextvars.
    """

    HEADER_NAME: str = "HTTP_X_CORRELATION_ID"
    RESPONSE_HEADER: str = "X-Correlation-ID"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        structlog.contextvars.clear_contextvars()

        correlation_id = request.META.get(self.HEADER_NAME) or str(uuid.uuid4())
        request.correlation_id = correlation_id  # type: ignore[attr-defined]

        structlog.contextvars.bind_contextvars(
            correlation_id=correlation_id,
            method=request.method,
            path=request.path,
        )

        response = self.get_response(request)
        response[self.RESPONSE_HEADER] = correlation_id
        return response
