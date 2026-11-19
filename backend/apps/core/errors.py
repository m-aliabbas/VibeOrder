"""Shared JSON error envelope: code, message, field_errors, current_revision, correlation_id."""
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_handler


class DomainError(Exception):
    http_status = 422
    code = "invalid"

    def __init__(self, message, *, code=None, field_errors=None, current_revision=None, http_status=None, extra=None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if http_status:
            self.http_status = http_status
        self.field_errors = field_errors or {}
        self.current_revision = current_revision
        self.extra = extra or {}


class Conflict(DomainError):
    http_status = 409
    code = "conflict"


class StaleRevision(Conflict):
    code = "stale_revision"


class Forbidden(DomainError):
    http_status = 403
    code = "forbidden"


class NotFound(DomainError):
    http_status = 404
    code = "not_found"


def envelope(request, code, message, http_status, field_errors=None, current_revision=None, extra=None):
    body = {
        "code": code,
        "message": message,
        "field_errors": field_errors or {},
        "current_revision": current_revision,
        "correlation_id": getattr(request, "correlation_id", "") if request else "",
    }
    if extra:
        body.update(extra)
    return Response(body, status=http_status)


def exception_handler(exc, context):
    request = context.get("request")
    if isinstance(exc, DomainError):
        return envelope(request, exc.code, exc.message, exc.http_status, exc.field_errors, exc.current_revision, exc.extra)
    response = drf_handler(exc, context)
    if response is None:
        return None
    code = getattr(exc, "default_code", "error")
    if isinstance(exc, APIException) and response.status_code == status.HTTP_400_BAD_REQUEST:
        return envelope(request, "validation_error", "Invalid request", 400, field_errors=response.data)
    detail = response.data.get("detail") if isinstance(response.data, dict) else str(response.data)
    if response.status_code == 404:
        code = "not_found"
    return envelope(request, code, str(detail), response.status_code)
