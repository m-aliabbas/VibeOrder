"""Host fencing (spec 02: on host replacement, fence the old host before enabling writes; A19).

Writes to a branch are refused unless this host's HOST_ID is the branch's writable host. Reads stay available so
staff can reconcile physical records against the restored state.
"""
import re

from django.conf import settings
from django.http import JsonResponse

BRANCH_WRITE = re.compile(r"^/api/v1/branches/(?P<branch>[0-9a-f-]{36})/")
SAFE = {"GET", "HEAD", "OPTIONS"}


class HostFenceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in SAFE:
            m = BRANCH_WRITE.match(request.path)
            if m and not request.path.endswith(("/sessions", "/sessions/logout")):
                from apps.core.models import Branch
                writable = Branch.objects.filter(pk=m["branch"]).values_list("writable_host", flat=True).first()
                if writable and writable != settings.HOST_ID:
                    return JsonResponse({
                        "code": "host_fenced", "field_errors": {}, "current_revision": None,
                        "message": f"This host ({settings.HOST_ID}) is fenced; branch writes go to {writable}.",
                        "correlation_id": getattr(request, "correlation_id", ""),
                    }, status=503)
        return self.get_response(request)
