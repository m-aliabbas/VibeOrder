"""Refunds against a successful tender (spec 03 §Tender, spec 04 §Payments, acceptance A11).

Rules:
- Requires the original successful tender, amount, reason and a manager identity (the operator if a manager,
  otherwise manager username + PIN). Card refunds need the terminal reference; without one the outcome is
  unknown and the refund stays *pending*, still reserving refundable balance until resolved.
- The tender row is locked: succeeded + pending refunds can never exceed the tender amount, even when two
  refunds are posted at the same moment.
- Three separate facts: money (Refund + cash drawer movement), revenue reversed (RefundAllocation per line,
  from the original payment attribution) and goods disposition (``goods``). A closed check stays closed.
"""
from django.db.models import Sum
from django.utils import timezone

from apps.core.errors import Conflict, DomainError, NotFound
from apps.core.scope import resolve_approver

from .models import DrawerMovement, DrawerShift, Refund, RefundAllocation, Tender, TenderAllocation

RESERVING = ("pending", "succeeded")


def _int(v, field):
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise DomainError(f"{field} must be an integer (minor units)", field_errors={field: "invalid"})
    if v < 1:
        raise DomainError(f"{field} must be positive", field_errors={field: "too_small"})
    return v


def refunded_by_line(tender):
    return {r["line_id"]: r["s"] for r in RefundAllocation.objects.filter(
        refund__tender=tender, refund__status__in=RESERVING).values("line_id").annotate(s=Sum("amount_minor"))}


def refundable(tender):
    used = tender.refunds.filter(status__in=RESERVING).aggregate(s=Sum("amount_minor"))["s"] or 0
    return tender.amount_minor - used


def serialize_refund(r):
    return {
        "id": str(r.pk), "tender_id": str(r.tender_id), "check_id": str(r.check_ref_id), "status": r.status,
        "method": r.method, "amount_minor": r.amount_minor, "reason": r.reason, "goods": r.goods,
        "external_ref": r.external_ref, "staff": r.staff.display_name, "approved_by": r.approved_by.display_name,
        "business_date": r.business_date, "created_at": r.created_at, "resolved_at": r.resolved_at,
        "allocations": [{"line_id": str(a.line_id), "amount_minor": a.amount_minor} for a in r.allocations.all()],
    }


def create_refund(ctx, tender_id, data):
    amount = _int(data.get("amount_minor"), "amount_minor")
    reason = str(data.get("reason", "")).strip()[:200]
    if not reason:
        raise DomainError("Give a reason for the refund", field_errors={"reason": "required"})
    goods = data.get("goods", "not_applicable")
    if goods not in dict(Refund.GOODS):
        raise DomainError("Unknown goods disposition", field_errors={"goods": "invalid"})

    # Lock order matches tendering (check, then tender): concurrent refunds serialize here (A11).
    check_id = Tender.objects.filter(branch=ctx.branch, pk=tender_id).values_list("check_ref_id", flat=True).first()
    if check_id is None:
        raise NotFound("Payment not found")
    from apps.orders.services import lock_check
    check = lock_check(ctx.branch, check_id)
    tender = Tender.objects.select_for_update().get(pk=tender_id)
    tender.check_ref = check
    if tender.status != "succeeded":
        raise Conflict("Only a successful payment can be refunded", code="tender_not_refundable")
    if data.get("expected_revision") is not None and int(data["expected_revision"]) != tender.check_ref.revision:
        from apps.core.errors import StaleRevision
        raise StaleRevision("The check changed. Review it again.", current_revision=tender.check_ref.revision)
    left = refundable(tender)
    if amount > left:
        raise DomainError("Amount exceeds what is still refundable on this payment", code="refund_exceeds",
                          field_errors={"amount_minor": "exceeds_refundable"}, extra={"refundable_minor": left})
    approver = resolve_approver(ctx.principal, data, "refund.approve")

    # Revenue reversal by line, capped at what this tender paid for each line less earlier refunds.
    paid = {a.line_id: a.amount_minor for a in TenderAllocation.objects.filter(tender=tender)}
    done = refunded_by_line(tender)
    room = {lid: amt - done.get(lid, 0) for lid, amt in paid.items()}
    allocations = []
    if data.get("items"):
        ids = {str(k): k for k in room}
        for i, it in enumerate(data["items"]):
            lid = ids.get(str(it.get("line_id")))
            amt = _int(it.get("amount_minor"), f"items[{i}].amount_minor")
            if lid is None:
                raise DomainError("Line was not paid by this payment", field_errors={f"items[{i}].line_id": "unknown"})
            if amt > room[lid]:
                raise DomainError("Refund exceeds what was paid for this line",
                                  field_errors={f"items[{i}].amount_minor": "exceeds"})
            room[lid] -= amt
            allocations.append((lid, amt))
        if sum(a for _, a in allocations) != amount:
            raise DomainError("Item amounts must add up to the refund", field_errors={"items": "sum_mismatch"})
    else:
        remaining = amount
        for lid in sorted(room, key=lambda k: str(k)):
            take = min(remaining, room[lid])
            if take > 0:
                allocations.append((lid, take))
                remaining -= take
        if remaining:
            raise DomainError("Payment has no unrefunded lines left", code="refund_exceeds")

    shift, status, ref = None, "succeeded", str(data.get("external_ref", "")).strip()[:80]
    if tender.method in ("cash", "rider_cod"):
        shift = DrawerShift.objects.select_for_update().filter(
            branch=ctx.branch, drawer_code=data.get("drawer_code", "main")).exclude(status="closed").first()
        if shift is None:
            raise Conflict("Open a drawer to pay out a cash refund", code="no_open_drawer")
        if shift.status != "open":
            raise Conflict("Drawer is being counted", code="drawer_counting")
    elif not ref:
        # Terminal refund attempted but no confirmation: never guess, reserve and reconcile later.
        status = "pending"

    bdate = ctx.branch.business_date()
    refund = Refund.objects.create(
        branch=ctx.branch, tender=tender, check_ref=tender.check_ref, method=tender.method, status=status,
        amount_minor=amount, currency=tender.currency, reason=reason, goods=goods, external_ref=ref, shift=shift,
        staff=ctx.staff, approved_by=approver, business_date=bdate,
        resolved_at=timezone.now() if status == "succeeded" else None,
    )
    for lid, amt in allocations:
        RefundAllocation.objects.create(refund=refund, line_id=lid, amount_minor=amt)
    if shift is not None:
        DrawerMovement.objects.create(shift=shift, kind="cash_refund", amount_minor=-amount, tender=tender,
                                      refund=refund, staff=ctx.staff, approved_by=approver, reason=reason,
                                      business_date=bdate)
    prev = check.revision
    check.revision += 1
    check.save(update_fields=["revision", "updated_at"])
    ctx.audit(f"refund.{status}", check, prev, check.revision, reason=reason, approved_by=approver,
              data={"refund": str(refund.pk), "tender": str(tender.pk), "amount_minor": amount})
    ctx.audit(f"refund.{status}", refund, None, refund.revision, reason=reason, approved_by=approver,
              data={"tender": str(tender.pk), "check": str(check.pk), "amount_minor": amount, "goods": goods})
    ctx.emit(f"refund.{status}", refund, {"refund_id": refund.pk, "tender_id": tender.pk, "check_id": check.pk,
                                          "amount_minor": amount, "method": tender.method, "business_date": bdate,
                                          "sale_business_date": tender.business_date})
    if status == "succeeded":
        _print_slip(ctx, refund)
    return {**serialize_refund(refund), "refundable_minor": refundable(tender), "revision": check.revision}


def resolve_refund(ctx, refund_id, data):
    """Reconcile a pending card refund once the terminal/provider outcome is known."""
    refund = Refund.objects.select_for_update().filter(branch=ctx.branch, pk=refund_id).first()
    if refund is None:
        raise NotFound("Refund not found")
    if refund.status != "pending":
        raise Conflict("Refund is already resolved", code="refund_resolved")
    outcome = data.get("status")
    if outcome not in ("succeeded", "failed"):
        raise DomainError("status must be succeeded or failed", field_errors={"status": "invalid"})
    ref = str(data.get("external_ref", "")).strip()[:80]
    if outcome == "succeeded" and not ref:
        raise DomainError("Enter the terminal refund reference", field_errors={"external_ref": "required"})
    approver = resolve_approver(ctx.principal, data, "refund.approve")
    prev = refund.revision
    refund.status = outcome
    refund.external_ref = ref or refund.external_ref
    refund.resolved_at = timezone.now()
    refund.resolved_by = approver
    refund.revision += 1
    refund.save()
    ctx.audit(f"refund.resolve.{outcome}", refund, prev, refund.revision, approved_by=approver,
              data={"external_ref": ref})
    ctx.emit(f"refund.{outcome}", refund, {"refund_id": refund.pk, "tender_id": refund.tender_id,
                                           "amount_minor": refund.amount_minor})
    if outcome == "succeeded":
        _print_slip(ctx, refund)
    return 200, {**serialize_refund(refund), "refundable_minor": refundable(refund.tender)}


def _print_slip(ctx, refund):
    from apps.kitchen.models import PrintJob
    from apps.kitchen.services import money, printer_for, ticket_header

    printer = printer_for(ctx.branch, "receipt")
    if printer is None:
        return None
    w = printer.width_chars
    check = refund.check_ref
    tz = ctx.branch.tz
    lines = ["=" * w, ctx.branch.name.center(w), "REFUND".center(w),
             (f"Original receipt {check.receipt_no}" if check.receipt_no else f"Check #{check.token}").center(w),
             ticket_header(check), f"{timezone.localtime(timezone.now(), tz):%Y-%m-%d %H:%M}", "-" * w]

    def row(k, v):
        lines.append(k + v.rjust(w - len(k)))

    row(f"Refund {refund.get_method_display()}", money(refund.amount_minor, refund.currency))
    if refund.external_ref:
        row("Terminal ref", refund.external_ref)
    row("Reason", refund.reason[: w - 8])
    row("Approved by", refund.approved_by.display_name)
    lines += ["=" * w, "", ""]
    return PrintJob.objects.create(branch=ctx.branch, printer=printer, kind="refund", check_ref=check,
                                   content="\n".join(lines))
