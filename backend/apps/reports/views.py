from django.http import HttpResponse
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.core.scope import branch_for, require

from . import services


@api_view(["GET"])
def report(request, branch_id, name):
    """Branch manager reports, scoped to the session's branch only."""
    branch = branch_for(request, branch_id)
    require(request, "reports.view")
    d_from, d_to = services.parse_range(request.query_params)
    if request.query_params.get("export") == "csv":
        resp = HttpResponse(services.as_csv(name, [branch], d_from, d_to, branch.tenant), content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="{branch.code}-{name}-{d_from}-{d_to}.csv"'
        return resp
    return Response(services.as_json(name, [branch], d_from, d_to, branch.tenant))
