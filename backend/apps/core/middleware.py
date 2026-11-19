import uuid


class CorrelationIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        cid = request.headers.get("X-Correlation-Id") or uuid.uuid4().hex
        request.correlation_id = cid[:64]
        response = self.get_response(request)
        response["X-Correlation-Id"] = request.correlation_id
        return response
