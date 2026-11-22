"""Manager dashboard: one call that returns everything the charts need for one branch.

Figures follow the report definitions (spec 04): sales are closed checks by business date, net of line discounts,
delivery fees shown apart, refunds on the refund's business date. Times are in the branch's timezone.
"""
from collections import defaultdict
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db.models import Count, F, Sum
from django.utils import timezone

from apps.core.errors import DomainError

from .services import _p


def _pct(now, before):
    if not before:
        return None
    return round((now - before) * 100 / before, 1)


def dashboard(branch, days=7):
    from apps.cash.models import RefundAllocation, Tender
    from apps.kitchen.models import ProductionTicket, TicketItem
    from apps.orders.models import Check, CheckLine

    if days not in (1, 7, 14, 30, 90):
        raise DomainError("days must be 1, 7, 14, 30 or 90", field_errors={"days": "invalid"})
    tz = ZoneInfo(branch.timezone)
    today = branch.business_date()
    d_from = today - timedelta(days=days - 1)
    prev_from, prev_to = d_from - timedelta(days=days), d_from - timedelta(days=1)

    lines = CheckLine.objects.filter(check_ref__branch=branch, check_ref__status="closed", status="active")
    item_lines = lines.exclude(product_code="delivery-fee")

    def totals(a, b):
        agg = item_lines.filter(check_ref__business_date__range=(a, b)).aggregate(
            g=Sum("total_minor"), d=Sum("discount_minor"))
        refunds = RefundAllocation.objects.filter(
            refund__branch=branch, refund__status="succeeded", refund__business_date__range=(a, b)).exclude(
            line__product_code="delivery-fee").aggregate(s=Sum("amount_minor"))["s"] or 0
        orders = Check.objects.filter(branch=branch, status="closed", business_date__range=(a, b)).count()
        net = (agg["g"] or 0) - (agg["d"] or 0) - refunds
        return {"net_minor": net, "orders": orders, "avg_minor": round(net / orders) if orders else 0,
                "discounts_minor": agg["d"] or 0, "refunds_minor": refunds}

    cur, prev = totals(d_from, today), totals(prev_from, prev_to)
    open_checks = Check.objects.filter(branch=branch, status="open")

    # ---- sales per business day (zero-filled so the line chart has every day)
    per_day = defaultdict(lambda: {"net_minor": 0, "orders": 0})
    for r in item_lines.filter(check_ref__business_date__range=(d_from, today)).values(
            "check_ref__business_date").annotate(g=Sum("total_minor"), d=Sum("discount_minor")):
        per_day[r["check_ref__business_date"]]["net_minor"] += (r["g"] or 0) - (r["d"] or 0)
    for r in Check.objects.filter(branch=branch, status="closed", business_date__range=(d_from, today)).values(
            "business_date").annotate(n=Count("id")):
        per_day[r["business_date"]]["orders"] = r["n"]
    for r in RefundAllocation.objects.filter(
            refund__branch=branch, refund__status="succeeded", refund__business_date__range=(d_from, today)).exclude(
            line__product_code="delivery-fee").values("refund__business_date").annotate(s=Sum("amount_minor")):
        per_day[r["refund__business_date"]]["net_minor"] -= r["s"]
    sales_by_day = [{"date": d_from + timedelta(days=i), **per_day[d_from + timedelta(days=i)]} for i in range(days)]

    # ---- orders by hour of day (when the order was taken), averaged over the range
    hours = [{"hour": h, "orders": 0, "net_minor": 0} for h in range(24)]
    checks_in_range = Check.objects.filter(branch=branch, business_date__range=(d_from, today)).exclude(
        status__in=["voided", "merged"])
    for opened_at in checks_in_range.values_list("opened_at", flat=True):
        hours[opened_at.astimezone(tz).hour]["orders"] += 1
    for r in item_lines.filter(check_ref__business_date__range=(d_from, today)).values(
            "check_ref__opened_at", "total_minor", "discount_minor"):
        hours[r["check_ref__opened_at"].astimezone(tz).hour]["net_minor"] += r["total_minor"] - r["discount_minor"]

    # ---- channel mix
    labels = dict(Check.SERVICE)
    mix = defaultdict(lambda: {"orders": 0, "net_minor": 0})
    for r in Check.objects.filter(branch=branch, status="closed", business_date__range=(d_from, today)).values(
            "service_type", "channel").annotate(n=Count("id")):
        key = "web" if r["channel"] == "web" else r["service_type"]
        mix[key]["orders"] += r["n"]
    for r in item_lines.filter(check_ref__business_date__range=(d_from, today)).values(
            "check_ref__service_type", "check_ref__channel").annotate(g=Sum("total_minor"), d=Sum("discount_minor")):
        key = "web" if r["check_ref__channel"] == "web" else r["check_ref__service_type"]
        mix[key]["net_minor"] += (r["g"] or 0) - (r["d"] or 0)
    labels["web"] = "Online"
    channels = sorted([{"code": k, "name": labels.get(k, k), **v} for k, v in mix.items()],
                      key=lambda x: -x["net_minor"])

    # ---- payments
    methods = dict(Tender.METHODS)
    methods.update({"external_card": "Card", "rider_cod": "Cash on delivery", "online": "Online"})
    payments = [{"method": r["method"], "name": methods.get(r["method"], r["method"]), "amount_minor": r["s"],
                 "count": r["n"]}
                for r in Tender.objects.filter(branch=branch, status="succeeded", business_date__range=(d_from, today))
                .values("method").annotate(s=Sum("amount_minor"), n=Count("id")).order_by("-s")]

    # ---- best sellers
    top = [{"name": r["name"], "quantity": r["q"], "net_minor": (r["g"] or 0) - (r["d"] or 0)}
           for r in item_lines.filter(check_ref__business_date__range=(d_from, today))
           .values("name").annotate(q=Sum("quantity"), g=Sum("total_minor"), d=Sum("discount_minor"))
           .order_by("-q", "name")[:8]]

    # ---- staff (who took the orders)
    staff = [{"name": r["check_ref__opened_by__display_name"], "orders": r["n"],
              "net_minor": (r["g"] or 0) - (r["d"] or 0)}
             for r in item_lines.filter(check_ref__business_date__range=(d_from, today))
             .values("check_ref__opened_by__display_name")
             .annotate(n=Count("check_ref", distinct=True), g=Sum("total_minor"), d=Sum("discount_minor"))
             .order_by("-g")[:6]]

    # ---- kitchen speed: minutes from ticket sent to item ready
    mins = [(r - c).total_seconds() / 60 for r, c in TicketItem.objects.filter(
        ticket__branch=branch, ready_at__isnull=False, ticket__check_ref__business_date__range=(d_from, today))
        .values_list("ready_at", "ticket__created_at")[:5000]]
    late_now = ProductionTicket.objects.filter(branch=branch, due_at__lt=timezone.now(),
                                               items__status__in=["queued", "preparing"]).distinct().count()
    in_kitchen = TicketItem.objects.filter(ticket__branch=branch, status__in=["queued", "preparing"]).count()

    return {
        "branch": {"id": str(branch.pk), "name": branch.name, "currency": branch.currency},
        "range": {"from": d_from, "to": today, "days": days},
        "kpis": {
            "net_minor": cur["net_minor"], "net_change_pct": _pct(cur["net_minor"], prev["net_minor"]),
            "orders": cur["orders"], "orders_change_pct": _pct(cur["orders"], prev["orders"]),
            "avg_minor": cur["avg_minor"], "avg_change_pct": _pct(cur["avg_minor"], prev["avg_minor"]),
            "discounts_minor": cur["discounts_minor"], "refunds_minor": cur["refunds_minor"],
            "open_checks": open_checks.count(),
            "open_value_minor": CheckLine.objects.filter(check_ref__in=open_checks, status="active").aggregate(
                s=Sum(F("total_minor") - F("discount_minor")))["s"] or 0,
            "in_kitchen": in_kitchen, "late_tickets": late_now,
            "kitchen_median_min": _p(mins, 0.5), "kitchen_p90_min": _p(mins, 0.9),
        },
        "sales_by_day": sales_by_day,
        "by_hour": hours,
        "channels": channels,
        "payments": payments,
        "top_items": top,
        "staff": staff,
        "server_time": timezone.now(),
    }
