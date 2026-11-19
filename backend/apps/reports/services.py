"""Branch and owner reports (spec 04 §Report definitions). Every report is computed for an explicit scope
(branches the caller may see, business-day range) and every CSV carries that scope in its header (A17)."""
import csv
import io
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.core.errors import DomainError, NotFound

REPORTS = ["sales", "collections", "voids_approvals", "riders", "late_orders", "service_speed",
           "product_contribution", "till"]


def parse_range(params, default_days=0):
    today = timezone.now().date()
    d_to = parse_date(params.get("to", "")) or today
    d_from = parse_date(params.get("from", "")) or (d_to - timedelta(days=default_days))
    if d_from > d_to:
        raise DomainError("from must be on or before to", field_errors={"from": "invalid"})
    if (d_to - d_from).days > 366:
        raise DomainError("Range is limited to one year", field_errors={"from": "too_wide"})
    return d_from, d_to


def _p(values, q):
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 1)


def sales(branch, d_from, d_to):
    from apps.cash.models import RefundAllocation
    from apps.orders.models import CheckLine
    rows = defaultdict(lambda: {"checks": 0, "gross_items_minor": 0, "discounts_minor": 0, "tax_minor": 0,
                                "delivery_fees_minor": 0, "returns_minor": 0})
    lines = CheckLine.objects.filter(check_ref__branch=branch, check_ref__status="closed", status="active",
                                     check_ref__business_date__range=(d_from, d_to)).values(
        "check_ref__business_date", "product_code", "total_minor", "discount_minor", "tax_minor", "check_ref_id")
    seen = defaultdict(set)
    for ln in lines:
        r = rows[ln["check_ref__business_date"]]
        seen[ln["check_ref__business_date"]].add(ln["check_ref_id"])
        if ln["product_code"] == "delivery-fee":
            r["delivery_fees_minor"] += ln["total_minor"]
        else:
            r["gross_items_minor"] += ln["total_minor"]
            r["discounts_minor"] += ln["discount_minor"]
        r["tax_minor"] += ln["tax_minor"]
    # Returns are posted on the refund's business date and reference the original sale line.
    for a in RefundAllocation.objects.filter(refund__branch=branch, refund__status="succeeded",
                                             refund__business_date__range=(d_from, d_to)).exclude(
            line__product_code="delivery-fee").values("refund__business_date").annotate(s=Sum("amount_minor")):
        rows[a["refund__business_date"]]["returns_minor"] += a["s"]
    out = []
    for d in sorted(rows):
        r = rows[d]
        r["checks"] = len(seen[d])
        r["net_merchandise_minor"] = r["gross_items_minor"] - r["discounts_minor"] - r["returns_minor"]
        out.append({"business_date": d, **r})
    return out


def collections(branch, d_from, d_to):
    from apps.cash.models import Refund, Tender
    rows = defaultdict(lambda: {"gross_minor": 0, "refunds_minor": 0, "count": 0})
    for t in Tender.objects.filter(branch=branch, status="succeeded", business_date__range=(d_from, d_to)).values(
            "business_date", "method", "check_ref__channel").annotate(s=Sum("amount_minor")):
        r = rows[(t["business_date"], t["method"], t["check_ref__channel"])]
        r["gross_minor"] += t["s"]
    for t in Tender.objects.filter(branch=branch, status="succeeded", business_date__range=(d_from, d_to)).values(
            "business_date", "method", "check_ref__channel"):
        rows[(t["business_date"], t["method"], t["check_ref__channel"])]["count"] += 1
    for f in Refund.objects.filter(branch=branch, status="succeeded", business_date__range=(d_from, d_to)).values(
            "business_date", "method", "check_ref__channel").annotate(s=Sum("amount_minor")):
        rows[(f["business_date"], f["method"], f["check_ref__channel"])]["refunds_minor"] += f["s"]
    return [{"business_date": k[0], "method": k[1], "channel": k[2], **v,
             "net_minor": v["gross_minor"] - v["refunds_minor"]} for k, v in sorted(rows.items())]


def voids_approvals(branch, d_from, d_to):
    from apps.core.models import AuditEntry
    tz = branch.tz
    start = timezone.make_aware(timezone.datetime.combine(d_from, branch.day_cutoff), tz)
    end = timezone.make_aware(timezone.datetime.combine(d_to + timedelta(days=1), branch.day_cutoff), tz)
    qs = (AuditEntry.objects.filter(branch=branch, created_at__gte=start, created_at__lt=end)
          .filter(approved_by__isnull=False).select_related("staff", "approved_by").order_by("created_at"))
    out = [{"at": a.created_at, "action": a.action, "operator": a.staff.display_name if a.staff_id else "",
            "approved_by": a.approved_by.display_name, "reason": a.reason, "aggregate": a.aggregate_type,
            "aggregate_id": str(a.aggregate_id),
            "amount_minor": (a.data or {}).get("amount_minor", (a.data or {}).get("total_minor", (a.data or {}).get(
                "delta_minor", "")))} for a in qs]
    return sorted(out, key=lambda r: r["at"])


def riders(branch, d_from=None, d_to=None):
    from apps.fulfillment.services import rider_balances
    out = []
    for r in rider_balances(branch)["riders"]:
        oldest = max((c["age_minutes"] for c in r["collections"]), default=0)
        out.append({"rider": r["name"], "outstanding_minor": r["outstanding_minor"],
                    "open_collections": len(r["collections"]), "oldest_age_minutes": oldest})
    return out


def late_orders(branch, d_from, d_to):
    from apps.orders.models import Check
    out = []
    for c in Check.objects.filter(branch=branch, business_date__range=(d_from, d_to), promised_at__isnull=False) \
            .exclude(status__in=["voided", "merged"]).prefetch_related("deliveries"):
        d = c.deliveries.filter(status="delivered").first()
        done = d.delivered_at if d else c.handed_over_at
        if done is None or done <= c.promised_at:
            if done is None and c.promised_at < timezone.now():
                out.append({"token": c.token, "business_date": c.business_date, "service_type": c.service_type,
                            "promised_at": c.promised_at, "completed_at": None,
                            "late_minutes": int((timezone.now() - c.promised_at).total_seconds() // 60),
                            "open": True})
            continue
        out.append({"token": c.token, "business_date": c.business_date, "service_type": c.service_type,
                    "promised_at": c.promised_at, "completed_at": done,
                    "late_minutes": int((done - c.promised_at).total_seconds() // 60), "open": False})
    return out


def service_speed(branch, d_from, d_to):
    from apps.kitchen.models import TicketItem
    from apps.orders.models import Check
    to_ready, to_hand, missing, n = [], [], 0, 0
    for c in Check.objects.filter(branch=branch, business_date__range=(d_from, d_to)).exclude(
            status__in=["voided", "merged"]):
        items = TicketItem.objects.filter(line__check_ref=c, line__status="active").exclude(status="cancelled") \
            .exclude(remakes__isnull=False)
        if not items.exists():
            continue
        n += 1
        ready = [i.ready_at for i in items]
        if any(r is None for r in ready):
            missing += 1
            continue
        accepted = c.rounds.order_by("created_at").values_list("created_at", flat=True).first() or c.opened_at
        r = max(ready)
        to_ready.append((r - accepted).total_seconds() / 60)
        d = c.deliveries.filter(status="delivered").first()
        hand = d.delivered_at if d else c.handed_over_at
        if hand:
            to_hand.append((hand - r).total_seconds() / 60)
    return [{"metric": "accepted_to_ready_minutes", "count": len(to_ready), "median": _p(to_ready, 0.5),
             "p90": _p(to_ready, 0.9)},
            {"metric": "ready_to_handoff_minutes", "count": len(to_hand), "median": _p(to_hand, 0.5),
             "p90": _p(to_hand, 0.9)},
            {"metric": "orders_considered", "count": n, "median": None, "p90": None},
            {"metric": "missing_ready_timestamps", "count": missing, "median": None, "p90": None}]


def product_contribution(branch, d_from, d_to):
    from apps.kitchen.models import TicketItem
    from apps.orders.models import CheckLine
    from apps.stock.models import StockMovement
    rev = defaultdict(lambda: {"quantity": 0, "net_revenue_minor": 0, "cost_minor": Decimal(0), "missing_cost": False,
                               "name": ""})
    lines = CheckLine.objects.filter(check_ref__branch=branch, check_ref__status="closed", status="active",
                                     check_ref__business_date__range=(d_from, d_to)).exclude(product_code="delivery-fee")
    line_product = {}
    for ln in lines:
        r = rev[ln.product_code]
        r["name"] = r["name"] or ln.name
        r["quantity"] += ln.quantity
        r["net_revenue_minor"] += ln.total_minor - ln.discount_minor
        line_product[ln.pk] = ln.product_code
    items = dict(TicketItem.objects.filter(line_id__in=list(line_product)).values_list("pk", "line_id"))
    refs = {f"ti:{k}": v for k, v in items.items()} | {f"rv:{k}": v for k, v in items.items()} | \
           {f"pk:{k}": v for k, v in items.items()}
    for m in StockMovement.objects.filter(branch=branch, source_ref__in=list(refs)):
        r = rev[line_product[refs[m.source_ref]]]
        if m.unit_cost_minor is None:
            r["missing_cost"] = True
        else:
            r["cost_minor"] += -m.quantity * m.unit_cost_minor
    with_items = {line_product[v] for v in items.values()}
    out = []
    for code, r in sorted(rev.items()):
        cost = int(r["cost_minor"].quantize(Decimal(1)))
        missing = r["missing_cost"] or code not in with_items
        margin = r["net_revenue_minor"] - cost
        out.append({"product": code, "name": r["name"], "quantity": r["quantity"],
                    "net_revenue_minor": r["net_revenue_minor"], "ingredient_cost_minor": None if missing else cost,
                    "contribution_minor": None if missing else margin,
                    "margin_pct": None if missing or not r["net_revenue_minor"]
                    else round(100 * margin / r["net_revenue_minor"], 1),
                    "missing_recipe_or_cost": missing})
    return out


def till(branch, d_from, d_to):
    from apps.cash.models import DrawerShift
    from apps.cash.services import expected_drawer
    out = []
    for s in DrawerShift.objects.filter(branch=branch, business_date__range=(d_from, d_to)).select_related(
            "opened_by", "approved_by").order_by("opened_at"):
        mv = {k: v for k, v in s.movements.values_list("kind").annotate(t=Sum("amount_minor"))}
        out.append({"shift_id": str(s.pk), "drawer": s.drawer_code, "business_date": s.business_date,
                    "status": s.status, "opening_float_minor": s.opening_float_minor,
                    "cash_sales_minor": mv.get("cash_sale", 0), "remittances_minor": mv.get("remittance", 0),
                    "cash_refunds_minor": -mv.get("cash_refund", 0), "paid_in_minor": mv.get("paid_in", 0),
                    "paid_out_minor": -mv.get("paid_out", 0), "drops_minor": -mv.get("drop", 0),
                    "expected_minor": s.expected_minor if s.status == "closed" else expected_drawer(s),
                    "counted_minor": s.counted_minor, "variance_minor": s.variance_minor,
                    "approved_by": s.approved_by.display_name if s.approved_by_id else None,
                    "reason": s.close_reason})
    return out


FUNCS = {"sales": sales, "collections": collections, "voids_approvals": voids_approvals, "riders": riders,
         "late_orders": late_orders, "service_speed": service_speed, "product_contribution": product_contribution,
         "till": till}


def run(name, branches, d_from, d_to):
    if name not in FUNCS:
        raise NotFound("Unknown report")
    rows = []
    for b in branches:
        for r in FUNCS[name](b, d_from, d_to):
            rows.append({"branch": b.code, **r})
    return rows


def as_json(name, branches, d_from, d_to, tenant):
    rows = run(name, branches, d_from, d_to)
    return {"report": name, "scope": scope(name, branches, d_from, d_to, tenant), "rows": rows}


def scope(name, branches, d_from, d_to, tenant):
    from apps.cloud.models import CloudBranch
    marks = {cb.branch_id: cb.heartbeat_at for cb in CloudBranch.objects.filter(branch__in=branches)}
    return {"tenant": tenant.name, "branches": [b.code for b in branches],
            "from_business_day": d_from, "to_business_day": d_to,
            "currency": sorted({b.currency for b in branches}), "generated_at": timezone.now(),
            "last_synced": {b.code: marks.get(b.pk) for b in branches}}


def as_csv(name, branches, d_from, d_to, tenant):
    rows = run(name, branches, d_from, d_to)
    sc = scope(name, branches, d_from, d_to, tenant)
    buf = io.StringIO()
    buf.write(f"# report: {name}\n# tenant: {sc['tenant']}\n# branches: {', '.join(sc['branches'])}\n"
              f"# business days: {d_from} to {d_to}\n# currency: {', '.join(sc['currency'])} (amounts in minor units)\n"
              f"# generated_at: {sc['generated_at'].isoformat()}\n")
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return buf.getvalue()

