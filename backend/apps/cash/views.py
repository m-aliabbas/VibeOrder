from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.core.commands import run_command
from apps.core.errors import NotFound
from apps.core.scope import branch_for, require

from . import services
from .models import DrawerShift


@api_view(["GET", "POST"])
def shifts(request, branch_id):
    branch = branch_for(request, branch_id)
    if request.method == "GET":
        require(request, "shift.view")
        shift = services.current_shift(branch, request.query_params.get("drawer_code", "main"))
        if shift is None:
            return Response({"shift": None})
        # Blind count: expected cash is not revealed until the count is submitted.
        reveal = shift.status != "open" or request.user.has("shift.approve_variance")
        return Response({"shift": services.serialize_shift(shift, reveal_expected=reveal)})
    return run_command(request, branch_id, "shift.open", request.data, lambda ctx: services.open_shift(ctx, request.data))


@api_view(["GET"])
def shift_detail(request, branch_id, shift_id):
    branch = branch_for(request, branch_id)
    require(request, "shift.view")
    shift = DrawerShift.objects.filter(branch=branch, pk=shift_id).first()
    if shift is None:
        raise NotFound("Shift not found")
    reveal = shift.status != "open" or request.user.has("shift.approve_variance")
    return Response(services.serialize_shift(shift, reveal_expected=reveal))


def _shift_cmd(intent, fn):
    @api_view(["POST"])
    def view(request, branch_id, shift_id):
        branch_for(request, branch_id)
        require(request, "shift.view")
        return run_command(request, branch_id, intent, {"shift": str(shift_id), **request.data},
                           lambda ctx: fn(ctx, shift_id, request.data))
    return view


count_shift = _shift_cmd("shift.count", services.count_shift)
reopen_shift = _shift_cmd("shift.reopen", services.reopen_shift)
close_shift = _shift_cmd("shift.close", services.close_shift)
movement = _shift_cmd("drawer.movement", services.drawer_movement)


@api_view(["POST"])
def tenders(request, branch_id, check_id):
    branch_for(request, branch_id)
    require(request, "tender.take")
    return run_command(request, branch_id, "tender.take", {"check": str(check_id), **request.data},
                       lambda ctx: services.take_tender(ctx, check_id, request.data))


@api_view(["POST"])
def close_check(request, branch_id, check_id):
    branch_for(request, branch_id)
    require(request, "check.close")
    return run_command(request, branch_id, "check.close", {"check": str(check_id), **request.data},
                       lambda ctx: services.close_check(ctx, check_id, request.data))


@api_view(["POST"])
def refunds(request, branch_id, tender_id):
    from . import refunds as svc
    branch_for(request, branch_id)
    require(request, "tender.take")  # manager identity is checked inside (operator or manager PIN)
    return run_command(request, branch_id, "refund.create", {"tender": str(tender_id), **request.data},
                       lambda ctx: svc.create_refund(ctx, tender_id, request.data))


@api_view(["POST"])
def resolve_refund(request, branch_id, refund_id):
    from . import refunds as svc
    branch_for(request, branch_id)
    require(request, "tender.take")
    return run_command(request, branch_id, "refund.resolve", {"refund": str(refund_id), **request.data},
                       lambda ctx: svc.resolve_refund(ctx, refund_id, request.data))


@api_view(["POST"])
def payer_receipt(request, branch_id, tender_id):
    from apps.core.errors import NotFound
    from apps.kitchen.services import enqueue_payer_receipt, serialize_job

    from .models import Tender
    branch_for(request, branch_id)
    require(request, "receipt.print")

    def handler(ctx):
        t = Tender.objects.filter(branch=ctx.branch, pk=tender_id, status="succeeded").select_related("check_ref").first()
        if t is None:
            raise NotFound("Payment not found")
        job = enqueue_payer_receipt(ctx, t)
        ctx.audit("receipt.payer", t, None, t.revision, data={"job": str(job.pk)})
        return serialize_job(job)

    return run_command(request, branch_id, "receipt.payer", {"tender": str(tender_id)}, handler)


INVOICE_SALT = "vibeorder.invoice"


@api_view(["GET"])
def invoice_link(request, branch_id, check_id):
    """Signed, shareable link to a printable customer invoice (no staff session needed to open it)."""
    from django.core import signing

    from apps.orders.models import Check
    branch = branch_for(request, branch_id)
    require(request, "check.view")
    if not Check.objects.filter(branch=branch, pk=check_id).exists():
        raise NotFound("Check not found")
    token = signing.dumps({"c": str(check_id)}, salt=INVOICE_SALT, compress=True)
    return Response({"url": request.build_absolute_uri(f"/invoice/{token}"), "path": f"/invoice/{token}"})


def invoice_page(request, token):
    from django.core import signing
    from django.http import Http404
    from django.shortcuts import render

    from apps.orders.models import Check
    from apps.orders.services import check_amounts
    try:
        data = signing.loads(token, salt=INVOICE_SALT, max_age=60 * 60 * 24 * 90)
    except signing.BadSignature:
        raise Http404("Invoice link invalid or expired")
    c = Check.objects.filter(pk=data["c"]).select_related("branch__tenant", "table", "opened_by", "closed_by").first()
    if c is None:
        raise Http404("Invoice not found")
    a = check_amounts(c)
    lines = [ln for ln in c.lines.filter(status="active").order_by("line_no")]
    tenders = list(c.tenders.filter(status="succeeded").order_by("created_at"))
    refunds = list(c.refunds.filter(status="succeeded").order_by("created_at"))
    discounts = list(c.discounts.filter(status="active").select_related("promotion"))
    d = c.deliveries.order_by("-attempt").first()
    tz = c.branch.tz
    return render(request, "invoice.html", {
        "c": c, "a": a, "lines": lines, "tenders": tenders, "refunds": refunds, "discounts": discounts,
        "delivery": d, "branch": c.branch, "tenant": c.branch.tenant,
        "issued": timezone.localtime(c.closed_at or timezone.now(), tz),
        "final": c.status == "closed", "currency": c.branch.currency,
    })
