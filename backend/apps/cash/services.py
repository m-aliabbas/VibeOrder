"""Tenders, check settlement and drawer reconciliation (spec 04 §Shift and rider ledger)."""
from django.db.models import Sum
from django.utils import timezone

from apps.core.errors import Conflict, DomainError, NotFound, StaleRevision
from apps.core.scope import resolve_approver
from apps.orders.services import check_amounts, lock_check, serialize_check, validate_expected_revision

from .models import DrawerMovement, DrawerShift, Tender, TenderAllocation


def _int(data, key, required=True, minimum=None):
    v = data.get(key)
    if v is None:
        if required:
            raise DomainError(f"{key} is required", field_errors={key: "required"})
        return None
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise DomainError(f"{key} must be an integer (minor units)", field_errors={key: "invalid"})
    if minimum is not None and v < minimum:
        raise DomainError(f"{key} must be at least {minimum}", field_errors={key: "too_small"})
    return v


def serialize_shift(shift, reveal_expected=True):
    data = {
        "id": str(shift.pk), "revision": shift.revision, "status": shift.status, "drawer_code": shift.drawer_code,
        "business_date": shift.business_date, "opened_by": shift.opened_by.display_name, "opened_at": shift.opened_at,
        "opening_float_minor": shift.opening_float_minor, "closed_at": shift.closed_at,
    }
    if reveal_expected:
        data.update({
            "expected_minor": shift.expected_minor if shift.status == "closed" else expected_drawer(shift),
            "counted_minor": shift.counted_minor, "variance_minor": shift.variance_minor,
            "close_reason": shift.close_reason,
            "approved_by": shift.approved_by.display_name if shift.approved_by_id else None,
            "summary": shift_summary(shift),
        })
    return data


def expected_drawer(shift):
    """Opening float + every physical drawer movement (cash sales are net of change)."""
    moved = shift.movements.aggregate(s=Sum("amount_minor"))["s"] or 0
    return shift.opening_float_minor + moved


def shift_summary(shift):
    by_kind = {k: 0 for k, _ in DrawerMovement.KINDS}
    for m in shift.movements.values("kind").annotate(s=Sum("amount_minor")):
        by_kind[m["kind"]] = m["s"]
    tenders = {}
    for t in shift.tenders.filter(status="succeeded").values("method").annotate(s=Sum("amount_minor")):
        tenders[t["method"]] = t["s"]
    # Card tenders taken while this shift was open (not tied to the drawer)
    from .models import Tender as T
    card = T.objects.filter(branch=shift.branch, method="external_card", status="succeeded",
                            created_at__gte=shift.opened_at)
    if shift.closed_at:
        card = card.filter(created_at__lte=shift.closed_at)
    tenders["external_card"] = card.aggregate(s=Sum("amount_minor"))["s"] or 0
    from .models import Refund
    card_ref = Refund.objects.filter(branch=shift.branch, method="external_card", status="succeeded",
                                     created_at__gte=shift.opened_at)
    if shift.closed_at:
        card_ref = card_ref.filter(created_at__lte=shift.closed_at)
    refunds = {"cash": -(by_kind.get("cash_refund") or 0),
               "external_card": card_ref.aggregate(s=Sum("amount_minor"))["s"] or 0,
               "pending": Refund.objects.filter(branch=shift.branch, status="pending").count()}
    from apps.orders.models import Check
    from apps.fulfillment.services import rider_balances
    open_checks = Check.objects.filter(branch=shift.branch, status="open")
    cod = T.objects.filter(branch=shift.branch, method="rider_cod", status="succeeded", created_at__gte=shift.opened_at)
    if shift.closed_at:
        cod = cod.filter(created_at__lte=shift.closed_at)
    tenders["rider_cod"] = cod.aggregate(s=Sum("amount_minor"))["s"] or 0
    riders = [r for r in rider_balances(shift.branch)["riders"] if r["outstanding_minor"] or r["active_deliveries"]]
    return {
        "movements": by_kind,
        "tenders": tenders,
        "refunds": refunds,
        "riders": {"outstanding_minor": sum(r["outstanding_minor"] for r in riders),
                   "remitted_minor": by_kind.get("remittance") or 0,
                   "by_rider": [{"id": r["id"], "name": r["name"], "outstanding_minor": r["outstanding_minor"],
                                 "collections": r["collections"]} for r in riders]},
        "open_checks": [{"id": str(c.pk), "token": c.token, "balance_minor": check_amounts(c)["balance_minor"]}
                        for c in open_checks.prefetch_related("lines")],
    }


def current_shift(branch, drawer_code="main"):
    return DrawerShift.objects.filter(branch=branch, drawer_code=drawer_code).exclude(status="closed").first()


def open_shift(ctx, data):
    if not ctx.principal.has("drawer.open"):
        raise DomainError("Your role cannot open a drawer", code="permission_denied", http_status=403)
    drawer_code = str(data.get("drawer_code") or "main")[:20]
    float_minor = _int(data, "opening_float_minor", minimum=0)
    if current_shift(ctx.branch, drawer_code):
        raise Conflict("This drawer already has an active shift", code="drawer_active")
    shift = DrawerShift.objects.create(branch=ctx.branch, drawer_code=drawer_code, business_date=ctx.branch.business_date(),
                                       opened_by=ctx.staff, opening_float_minor=float_minor)
    ctx.audit("shift.open", shift, None, 1, data={"float_minor": float_minor})
    ctx.emit("shift.opened", shift, {"shift_id": shift.pk, "float_minor": float_minor, "drawer": drawer_code})
    return serialize_shift(shift)


def _lock_shift(ctx, shift_id):
    shift = DrawerShift.objects.select_for_update().filter(branch=ctx.branch, pk=shift_id).first()
    if shift is None:
        raise NotFound("Shift not found")
    return shift


def take_tender(ctx, check_id, data):
    if not ctx.principal.has("tender.take"):
        raise DomainError("Your role cannot take payments", code="permission_denied", http_status=403)
    method = data.get("method")
    if method not in ("cash", "external_card"):
        raise DomainError("Unknown payment method", field_errors={"method": "invalid"})
    amount = _int(data, "amount_minor", minimum=1)
    check = lock_check(ctx.branch, check_id)
    if check.status != "open":
        raise Conflict("Check is already closed", code="check_closed", current_revision=check.revision)
    # Stale tender must have zero side effects: validate before any posting.
    validate_expected_revision(check, data)
    balance = check_amounts(check)["balance_minor"]
    if amount > balance:
        raise DomainError("Amount exceeds the balance due", code="overpayment", field_errors={"amount_minor": "exceeds_balance"},
                          extra={"balance_minor": balance})

    shift = None
    received, change = None, 0
    if method == "cash":
        shift = DrawerShift.objects.select_for_update().filter(branch=ctx.branch, drawer_code=data.get("drawer_code", "main")) \
            .exclude(status="closed").first()
        if shift is None:
            raise Conflict("Open a drawer shift before taking cash", code="no_open_drawer")
        if shift.status != "open":
            raise Conflict("Drawer is being counted; use another drawer", code="drawer_counting")
        received = _int(data, "cash_received_minor", required=False, minimum=1) or amount
        if received < amount:
            raise DomainError("Cash received is less than the amount", field_errors={"cash_received_minor": "too_small"})
        change = received - amount
    else:
        ref = str(data.get("external_ref", "")).strip()
        if not ref:
            raise DomainError("Enter the terminal reference", field_errors={"external_ref": "required"})

    # Allocations: explicit per line, or fill lines in order up to their remaining charge.
    lines = list(check.lines.filter(status="active").order_by("line_no"))
    paid_by_line = {
        r["line_id"]: r["s"] for r in TenderAllocation.objects.filter(
            tender__check_ref=check, tender__status="succeeded").values("line_id").annotate(s=Sum("amount_minor"))
    }
    remaining = {ln.pk: ln.charge_minor - paid_by_line.get(ln.pk, 0) for ln in lines}
    allocations = []
    if data.get("allocations"):
        ids = {str(k): k for k in remaining}
        for i, a in enumerate(data["allocations"]):
            lid = ids.get(str(a.get("line_id")))
            amt = _int(a, "amount_minor", minimum=1)
            if lid is None:
                raise DomainError("Allocation line not on this check", field_errors={f"allocations[{i}].line_id": "unknown"})
            if amt > remaining[lid]:
                raise DomainError("Allocation exceeds the line's unpaid amount", field_errors={f"allocations[{i}].amount_minor": "exceeds"})
            remaining[lid] -= amt
            allocations.append((lid, amt))
        if sum(a for _, a in allocations) != amount:
            raise DomainError("Allocations must add up to the amount", field_errors={"allocations": "sum_mismatch"})
    else:
        left = amount
        for ln in lines:
            take = min(left, remaining[ln.pk])
            if take > 0:
                allocations.append((ln.pk, take))
                left -= take
            if left == 0:
                break

    bdate = ctx.branch.business_date()
    tender = Tender.objects.create(
        branch=ctx.branch, check_ref=check, shift=shift, method=method, status="succeeded", amount_minor=amount,
        cash_received_minor=received, change_minor=change, external_ref=str(data.get("external_ref", ""))[:80],
        currency=ctx.branch.currency, staff=ctx.staff, business_date=bdate,
    )
    for lid, amt in allocations:
        TenderAllocation.objects.create(tender=tender, line_id=lid, amount_minor=amt)
    if method == "cash":
        # Net applied cash (received minus change) is the drawer effect.
        DrawerMovement.objects.create(shift=shift, kind="cash_sale", amount_minor=amount, tender=tender,
                                      staff=ctx.staff, business_date=bdate)
    prev = check.revision
    check.revision += 1
    check.save(update_fields=["revision", "updated_at"])
    new_balance = balance - amount
    ctx.audit("tender.succeeded", check, prev, check.revision,
              data={"tender": str(tender.pk), "method": method, "amount_minor": amount, "change_minor": change})
    ctx.emit("tender.succeeded", tender, {"check_id": check.pk, "tender_id": tender.pk, "method": method,
                                          "amount_minor": amount, "business_date": bdate})
    return {"tender_id": str(tender.pk), "status": tender.status, "applied_minor": amount, "change_minor": change,
            "cash_received_minor": received, "balance_minor": new_balance, "revision": check.revision}


def allocate_to_lines(check, amount):
    """Fill active lines in order up to their unpaid charge (default payment attribution)."""
    lines = list(check.lines.filter(status="active").order_by("line_no"))
    paid_by_line = {
        r["line_id"]: r["s"] for r in TenderAllocation.objects.filter(
            tender__check_ref=check, tender__status="succeeded").values("line_id").annotate(s=Sum("amount_minor"))
    }
    out, left = [], amount
    for ln in lines:
        take = min(left, ln.charge_minor - paid_by_line.get(ln.pk, 0))
        if take > 0:
            out.append((ln.pk, take))
            left -= take
        if left == 0:
            break
    return out


def finalize_close(ctx, check):
    """Close a locked, fully paid check: receipt number, status, print. Returns the receipt number."""
    from apps.core.commands import next_sequence
    from apps.kitchen.services import enqueue_receipt

    amounts = check_amounts(check)
    if amounts["balance_minor"] != 0:
        raise DomainError("Balance must be zero to close", code="balance_due", extra={"balance_minor": amounts["balance_minor"]})
    if not check.lines.filter(status="active").exists():
        raise DomainError("Nothing to settle; void the empty check instead", code="empty_check")
    n = next_sequence(ctx.branch.pk, "receipt")
    check.receipt_no = f"{ctx.branch.code}-{n:06d}"
    check.status = "closed"
    check.closed_at = timezone.now()
    check.closed_by = ctx.staff
    prev = check.revision
    check.revision += 1
    check.save()
    check._receipt_job = enqueue_receipt(ctx, check)
    ctx.audit("check.close", check, prev, check.revision, data={"receipt_no": check.receipt_no, **amounts})
    ctx.emit("check.closed", check, {"check_id": check.pk, "receipt_no": check.receipt_no, **amounts,
                                     "business_date": check.business_date, "service_type": check.service_type,
                                     "channel": check.channel})
    return check.receipt_no


def close_check(ctx, check_id, data):
    if not ctx.principal.has("check.close"):
        raise DomainError("Your role cannot close checks", code="permission_denied", http_status=403)
    check = lock_check(ctx.branch, check_id)
    if check.status != "open":
        raise Conflict("Check is already closed", code="check_closed", current_revision=check.revision)
    validate_expected_revision(check, data)
    finalize_close(ctx, check)
    job = check._receipt_job
    return 200, {"check_id": str(check.pk), "receipt_no": check.receipt_no, "revision": check.revision,
                 "status": check.status, "print_job_id": str(job.pk) if job else None,
                 "receipt": serialize_check(check, detail=True)}


def count_shift(ctx, shift_id, data):
    """Blind count: the cashier submits the counted amount before seeing expected or variance."""
    shift = _lock_shift(ctx, shift_id)
    if shift.status == "closed":
        raise Conflict("Shift is closed", code="shift_closed")
    if int(data.get("expected_revision", -1)) != shift.revision:
        raise StaleRevision("Shift changed", current_revision=shift.revision)
    counted = _int(data, "counted_minor", minimum=0)
    prev = shift.revision
    shift.status = "counting"
    shift.counted_minor = counted
    shift.expected_minor = expected_drawer(shift)
    shift.variance_minor = counted - shift.expected_minor
    shift.revision += 1
    shift.save()
    ctx.audit("shift.count", shift, prev, shift.revision, data={"counted_minor": counted,
                                                                 "variance_minor": shift.variance_minor})
    return 200, serialize_shift(shift)


def reopen_shift(ctx, shift_id, data):
    shift = _lock_shift(ctx, shift_id)
    if shift.status != "counting":
        raise Conflict("Only a counting shift can be reopened", code="invalid_transition")
    reason = str(data.get("reason", "")).strip()
    if not reason:
        raise DomainError("Give a reason", field_errors={"reason": "required"})
    prev = shift.revision
    shift.status = "open"
    shift.counted_minor = shift.expected_minor = shift.variance_minor = None
    shift.revision += 1
    shift.save()
    ctx.audit("shift.reopen", shift, prev, shift.revision, reason=reason)
    return 200, serialize_shift(shift)


def close_shift(ctx, shift_id, data):
    shift = _lock_shift(ctx, shift_id)
    if shift.status != "counting":
        raise Conflict("Count the drawer before closing", code="count_required")
    if int(data.get("expected_revision", -1)) != shift.revision:
        raise StaleRevision("Shift changed", current_revision=shift.revision)
    approver = None
    reason = str(data.get("reason", "")).strip()
    if shift.variance_minor:
        if not reason:
            raise DomainError("A variance needs a reason", field_errors={"reason": "required"})
        approver = resolve_approver(ctx.principal, data, "shift.approve_variance")
    prev = shift.revision
    shift.status = "closed"
    shift.closed_at = timezone.now()
    shift.closed_by = ctx.staff
    shift.approved_by = approver
    shift.close_reason = reason
    shift.revision += 1
    shift.save()
    ctx.audit("shift.close", shift, prev, shift.revision, reason=reason, approved_by=approver,
              data={"expected_minor": shift.expected_minor, "counted_minor": shift.counted_minor,
                    "variance_minor": shift.variance_minor})
    ctx.emit("shift.closed", shift, {"shift_id": shift.pk, "expected_minor": shift.expected_minor,
                                     "counted_minor": shift.counted_minor, "variance_minor": shift.variance_minor})
    return 200, serialize_shift(shift)


def drawer_movement(ctx, shift_id, data):
    """Paid in / paid out / cash drop. Requires manager approval."""
    kind = data.get("kind")
    if kind not in ("paid_in", "paid_out", "drop"):
        raise DomainError("kind must be paid_in, paid_out or drop", field_errors={"kind": "invalid"})
    amount = _int(data, "amount_minor", minimum=1)
    reason = str(data.get("reason", "")).strip()
    if not reason:
        raise DomainError("Give a reason", field_errors={"reason": "required"})
    shift = _lock_shift(ctx, shift_id)
    if shift.status != "open":
        raise Conflict("Drawer is not open for postings", code="drawer_counting" if shift.status == "counting" else "shift_closed")
    approver = resolve_approver(ctx.principal, data, "drawer.movement")
    signed = amount if kind == "paid_in" else -amount
    m = DrawerMovement.objects.create(shift=shift, kind=kind, amount_minor=signed, staff=ctx.staff, approved_by=approver,
                                      reason=reason[:200], business_date=ctx.branch.business_date())
    shift.revision += 1
    shift.save(update_fields=["revision", "updated_at"])
    ctx.audit(f"drawer.{kind}", shift, shift.revision - 1, shift.revision, reason=reason, approved_by=approver,
              data={"amount_minor": signed})
    return {"movement_id": m.pk, "amount_minor": signed, "revision": shift.revision}
