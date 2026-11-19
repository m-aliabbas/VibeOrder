"""Discounts (spec 04 step 5): one eligible order promotion plus manager-authorized explicit line adjustments,
floor of zero, applied before tax. The order promotion is allocated to eligible lines proportionally to their
amount (after line adjustments) by largest remainder with stable line-ID tie breaks.

Line discount/tax snapshots on CheckLine are recomputed from the active CheckDiscount rows whenever the set of
lines or discounts changes, under the check lock. A recomputation that would leave a line (or the check) below
what was already paid for it is refused with ``refund_required``.
"""
from decimal import Decimal

from django.db.models import Sum

from apps.catalog.models import Promotion
from apps.catalog.pricing import allocate, round_half_up
from apps.core.errors import Conflict, DomainError, NotFound
from apps.core.scope import resolve_approver

from .models import CheckDiscount, CheckLine
from .services import check_amounts, lock_check, validate_expected_revision

NON_DISCOUNTABLE = {"delivery-fee"}


def _eligible(line):
    return line.status == "active" and line.product_code not in NON_DISCOUNTABLE


def apply_discounts(check):
    """Recompute line discount and tax from active discounts. Returns {discount_id: applied_minor}."""
    lines = list(check.lines.select_for_update().filter(status="active").select_related("menu_release")
                 .order_by("line_no"))
    discounts = list(check.discounts.filter(status="active").select_related("promotion").order_by("created_at"))
    by_line = {}
    applied = {}
    for d in discounts:
        if d.kind != "line_adjustment":
            continue
        ln = next((x for x in lines if x.pk == d.line_id), None)
        if ln is None:  # line was removed/changed: the adjustment no longer applies
            d.status = "removed"
            d.applied_minor = 0
            d.save(update_fields=["status", "applied_minor", "updated_at"])
            continue
        room = ln.total_minor - by_line.get(ln.pk, 0)
        want = d.amount_minor if d.amount_minor is not None else round_half_up(
            Decimal(ln.total_minor) * Decimal(d.percent_bp or 0) / Decimal(10000))
        take = max(0, min(want, room))
        by_line[ln.pk] = by_line.get(ln.pk, 0) + take
        applied[d.pk] = take

    promo = next((d for d in discounts if d.kind == "promotion"), None)
    promo_alloc = {}
    if promo is not None:
        weights = {str(ln.pk): ln.total_minor - by_line.get(ln.pk, 0) for ln in lines if _eligible(ln)}
        base = sum(weights.values())
        p = promo.promotion
        if p.kind == "percent":
            amount = round_half_up(Decimal(base) * Decimal(p.value) / Decimal(10000))
        else:
            amount = p.value
        if p.max_discount_minor is not None:
            amount = min(amount, p.max_discount_minor)
        if base < p.min_subtotal_minor:
            amount = 0
        amount = max(0, min(amount, base))
        promo_alloc = allocate(amount, weights) if weights else {}
        applied[promo.pk] = amount

    for ln in lines:
        disc = by_line.get(ln.pk, 0) + promo_alloc.get(str(ln.pk), 0)
        rate = Decimal(ln.menu_release.tax_rate_bp) / Decimal(10000)
        tax = round_half_up(Decimal(ln.total_minor - disc) * rate)
        if (disc, tax) != (ln.discount_minor, ln.tax_minor):
            ln.discount_minor, ln.tax_minor = disc, tax
            ln.revision += 1
            ln.save(update_fields=["discount_minor", "tax_minor", "revision", "updated_at"])
    for d in discounts:
        if d.pk in applied and d.applied_minor != applied[d.pk]:
            d.applied_minor = applied[d.pk]
            d.save(update_fields=["applied_minor", "updated_at"])
    _ensure_not_below_paid(check, lines)
    return applied


def _ensure_not_below_paid(check, lines):
    from apps.cash.models import TenderAllocation
    paid = {r["line_id"]: r["s"] for r in TenderAllocation.objects.filter(
        tender__check_ref=check, tender__status="succeeded").values("line_id").annotate(s=Sum("amount_minor"))}
    for ln in lines:
        if paid.get(ln.pk, 0) > ln.charge_minor:
            raise DomainError(f"{ln.name} is already paid; refund before discounting it", code="refund_required",
                              extra={"line_id": str(ln.pk)})
    a = check_amounts(check)
    if a["balance_minor"] < 0:
        raise DomainError("Payments already exceed the discounted total; refund first", code="refund_required")


def recompute_if_discounted(check):
    if check.discounts.filter(status="active").exists():
        apply_discounts(check)


def serialize_discount(d):
    return {
        "id": str(d.pk), "kind": d.kind, "status": d.status, "reason": d.reason,
        "promotion": {"code": d.promotion.code, "name": d.promotion.name} if d.promotion_id else None,
        "line_id": str(d.line_id) if d.line_id else None, "percent_bp": d.percent_bp, "amount_minor": d.amount_minor,
        "applied_minor": d.applied_minor, "staff": d.staff.display_name,
        "approved_by": d.approved_by.display_name if d.approved_by_id else None, "created_at": d.created_at,
    }


def promotions_for(branch, channel="staff"):
    out = []
    for p in Promotion.objects.filter(tenant=branch.tenant, is_active=True).order_by("code"):
        if p.channels and channel not in p.channels:
            continue
        out.append({"code": p.code, "name": p.name, "kind": p.kind, "value": p.value,
                    "min_subtotal_minor": p.min_subtotal_minor, "max_discount_minor": p.max_discount_minor,
                    "requires_approval": p.requires_approval})
    return out


def _result(check, d):
    a = check_amounts(check)
    return {"check_id": str(check.pk), "revision": check.revision, "discount": serialize_discount(d),
            "total_minor": a["total_minor"], "discount_minor": a["discount_minor"],
            "balance_minor": a["balance_minor"]}


def add_discount(ctx, check_id, data):
    if not ctx.principal.has("discount.apply") and not data.get("approval"):
        from apps.core.errors import Forbidden
        raise Forbidden("Manager approval required", code="approval_required")
    kind = data.get("kind")
    if kind not in ("promotion", "line_adjustment"):
        raise DomainError("kind must be promotion or line_adjustment", field_errors={"kind": "invalid"})
    reason = str(data.get("reason", "")).strip()[:200]
    check = lock_check(ctx.branch, check_id)
    if check.status != "open":
        raise Conflict("Check is not open", code="check_closed", current_revision=check.revision)
    validate_expected_revision(check, data)
    approver = None
    d = CheckDiscount(check_ref=check, kind=kind, staff=ctx.staff)
    if kind == "promotion":
        code = str(data.get("promotion_code", "")).strip().upper()
        promo = Promotion.objects.filter(tenant=ctx.branch.tenant, code=code, is_active=True).first()
        if promo is None:
            raise DomainError("Unknown promotion", field_errors={"promotion_code": "unknown"})
        if check.discounts.filter(status="active", kind="promotion").exists():
            raise Conflict("This check already has a promotion; remove it first", code="promotion_exists")
        base = sum(ln.total_minor for ln in check.lines.filter(status="active") if _eligible(ln))
        if base < promo.min_subtotal_minor:
            raise DomainError("Order is below the promotion minimum", code="promotion_minimum",
                              extra={"min_subtotal_minor": promo.min_subtotal_minor})
        if promo.requires_approval or not ctx.principal.has("discount.apply"):
            approver = resolve_approver(ctx.principal, data, "discount.override")
        d.promotion = promo
        d.reason = reason or promo.name
    else:
        if not reason:
            raise DomainError("Give a reason", field_errors={"reason": "required"})
        line = CheckLine.objects.filter(check_ref=check, pk=data.get("line_id"), status="active").first()
        if line is None:
            raise NotFound("Line not found on this check")
        if not _eligible(line):
            raise DomainError("This charge cannot be discounted", field_errors={"line_id": "not_discountable"})
        amount, pct = data.get("amount_minor"), data.get("percent_bp")
        if (amount is None) == (pct is None):
            raise DomainError("Give amount_minor or percent_bp", field_errors={"amount_minor": "required"})
        if amount is not None:
            amount = int(amount)
            if amount < 1 or amount > line.total_minor:
                raise DomainError("Adjustment must be between 1 and the line amount",
                                  field_errors={"amount_minor": "out_of_range"})
            share_bp = amount * 10000 // max(line.total_minor, 1)
        else:
            pct = int(pct)
            if pct < 1 or pct > 10000:
                raise DomainError("percent_bp must be 1-10000", field_errors={"percent_bp": "out_of_range"})
            share_bp = pct
        existing = check.discounts.filter(status="active", kind="line_adjustment", line=line).aggregate(
            s=Sum("applied_minor"))["s"] or 0
        share_bp += existing * 10000 // max(line.total_minor, 1)
        if share_bp > ctx.branch.discount_limit_bp or not ctx.principal.has("discount.apply"):
            approver = resolve_approver(ctx.principal, data, "discount.override")
        d.line, d.amount_minor, d.percent_bp, d.reason = line, amount, pct, reason
    d.approved_by = approver
    d.save()
    applied = apply_discounts(check)
    d.refresh_from_db()
    if applied.get(d.pk, 0) == 0:
        raise DomainError("Nothing left to discount", code="nothing_to_discount")
    prev = check.revision
    check.revision += 1
    check.save(update_fields=["revision", "updated_at"])
    ctx.audit(f"discount.{kind}", check, prev, check.revision, reason=d.reason, approved_by=approver,
              data={"discount": str(d.pk), "applied_minor": d.applied_minor})
    ctx.emit("check.discount_applied", check, {"check_id": check.pk, "discount_id": d.pk, "kind": kind,
                                               "applied_minor": d.applied_minor})
    return _result(check, d)


def remove_discount(ctx, check_id, discount_id, data):
    check = lock_check(ctx.branch, check_id)
    if check.status != "open":
        raise Conflict("Check is not open", code="check_closed", current_revision=check.revision)
    validate_expected_revision(check, data)
    d = check.discounts.select_for_update().filter(pk=discount_id, status="active").first()
    if d is None:
        raise NotFound("Discount not found")
    if not ctx.principal.has("discount.apply"):
        resolve_approver(ctx.principal, data, "discount.override")
    d.status = "removed"
    d.removed_by = ctx.staff
    d.applied_minor = 0
    d.revision += 1
    d.save()
    apply_discounts(check)
    prev = check.revision
    check.revision += 1
    check.save(update_fields=["revision", "updated_at"])
    ctx.audit("discount.remove", check, prev, check.revision, reason=str(data.get("reason", ""))[:200],
              data={"discount": str(d.pk)})
    ctx.emit("check.discount_removed", check, {"check_id": check.pk, "discount_id": d.pk})
    return 200, _result(check, d)
