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


@api_view(["GET"])
def dashboard(request, branch_id):
    """Everything the manager dashboard charts need, for this branch."""
    from .dashboard import dashboard as build

    branch = branch_for(request, branch_id)
    require(request, "reports.view")
    try:
        days = int(request.query_params.get("days", 7))
    except ValueError:
        days = 0
    return Response(build(branch, days))


LEDGER_SALT = "vibeorder.ledger"
LEDGER_PARAMS = ("supplier", "customer", "item")


@api_view(["GET"])
def ledger(request, branch_id, kind):
    """A ledger as JSON for the app, plus a short-lived signed link to the printable A4 page."""
    from django.core import signing

    from .ledgers import build

    branch = branch_for(request, branch_id)
    require(request, "reports.view")
    d_from, d_to = services.parse_range(request.query_params)
    params = {k: request.query_params[k] for k in LEDGER_PARAMS if request.query_params.get(k)}
    data = build(kind, branch, d_from, d_to, params)
    token = signing.dumps({"b": str(branch.pk), "k": kind, "f": str(d_from), "t": str(d_to), "p": params,
                           "by": request.user.staff.display_name}, salt=LEDGER_SALT, compress=True)
    return Response({**data, "print_path": f"/print/ledger/{token}",
                     "print_url": request.build_absolute_uri(f"/print/ledger/{token}")})


def ledger_print(request, token):
    """A4 print page. The link is signed and expires after a day; it shows only what the signer could see."""
    from django.core import signing
    from django.http import Http404
    from django.shortcuts import render
    from django.utils.dateparse import parse_date

    from apps.core.models import Branch

    from .ledgers import build
    try:
        d = signing.loads(token, salt=LEDGER_SALT, max_age=60 * 60 * 24)
    except signing.BadSignature:
        raise Http404("Print link invalid or expired. Open the ledger again in the app.")
    branch = Branch.objects.select_related("tenant").filter(pk=d["b"]).first()
    if branch is None:
        raise Http404("Branch not found")
    data = build(d["k"], branch, parse_date(d["f"]), parse_date(d["t"]), d.get("p") or {})
    for sec in data["sections"]:
        sec["table"] = [[(row.get(c["key"]), c) for c in sec["columns"]] for row in sec["rows"]]
        sec["foot"] = [(sec.get("totals", {}).get(c["key"]), c) for c in sec["columns"]]
    return render(request, "ledger.html", {"l": data, "printed_by": d.get("by", ""),
                                           "autoprint": request.GET.get("print") == "1"})
