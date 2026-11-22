"""Ledgers for one branch, in one shape so the app table and the A4 print page render the same data.

    {kind, title, branch, from, to, filters, summary: [{label, value, money}],
     sections: [{title, subtitle, columns: [{key, label, align, money}], rows: [...], totals: {...}}]}

Kinds: cash (drawer shifts with running balance), sales (every closed bill), supplier (purchases vs payments),
customer (orders vs payments), stock (item quantities in / out). Money is integer minor units.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.core.errors import NotFound

KINDS = {"cash": "Cash ledger", "sales": "Sales ledger", "supplier": "Supplier ledger",
         "customer": "Customer ledger", "stock": "Stock ledger"}
MAX_ROWS = 5000


def col(key, label, money=False, align=None):
    return {"key": key, "label": label, "money": money, "align": align or ("right" if money else "left")}


def _local(dt, tz):
    return timezone.localtime(dt, tz).strftime("%d-%m-%Y %H:%M") if dt else ""


def build(kind, branch, d_from, d_to, params):
    if kind not in KINDS:
        raise NotFound("Unknown ledger")
    data = {"cash": cash, "sales": sales, "supplier": supplier, "customer": customer, "stock": stock}[kind](
        branch, d_from, d_to, params)
    return {"kind": kind, "title": KINDS[kind], "branch": {"name": branch.name, "business": branch.tenant.name,
                                                             "address": branch.address, "phone": branch.phone,
                                                             "tax_number": branch.tax_number},
            "from": d_from, "to": d_to, "generated_at": _local(timezone.now(), branch.tz), **data}


# ---------------------------------------------------------------- cash

def cash(branch, d_from, d_to, params):
    from apps.cash.models import DrawerShift
    tz = branch.tz
    labels = {"cash_sale": "Cash sale", "cash_refund": "Refund", "paid_in": "Paid in", "paid_out": "Paid out",
              "drop": "Cash drop", "remittance": "Rider cash"}
    cols = [col("time", "Time"), col("type", "Type"), col("ref", "Reference"), col("by", "By"),
            col("in", "Cash in", True), col("out", "Cash out", True), col("balance", "Balance", True)]
    sections, total_in, total_out = [], 0, 0
    shifts = DrawerShift.objects.filter(branch=branch, business_date__range=(d_from, d_to)).select_related(
        "opened_by", "closed_by").order_by("opened_at")
    for sh in shifts:
        bal = sh.opening_float_minor
        rows = [{"time": _local(sh.opened_at, tz), "type": "Opening float", "ref": "", "by": sh.opened_by.display_name,
                 "in": sh.opening_float_minor, "out": None, "balance": bal}]
        s_in = s_out = 0
        for m in sh.movements.select_related("staff", "tender__check_ref", "refund__check_ref").order_by("id"):
            bal += m.amount_minor
            chk = (m.tender.check_ref if m.tender_id else m.refund.check_ref if m.refund_id else None)
            ref = (chk.receipt_no or f"#{chk.token}") if chk else m.reason
            if m.amount_minor >= 0:
                s_in += m.amount_minor
            else:
                s_out += -m.amount_minor
            rows.append({"time": _local(m.created_at, tz), "type": labels.get(m.kind, m.kind), "ref": ref[:60],
                         "by": m.staff.display_name, "in": m.amount_minor if m.amount_minor >= 0 else None,
                         "out": -m.amount_minor if m.amount_minor < 0 else None, "balance": bal})
        total_in += s_in
        total_out += s_out
        status = {"open": "OPEN", "counting": "COUNTING", "closed": "CLOSED"}[sh.status]
        sub = f"Opened by {sh.opened_by.display_name} · {status}"
        if sh.status == "closed":
            sub += (f" · Expected {sh.expected_minor / 100:,.0f} · Counted {sh.counted_minor / 100:,.0f}"
                    f" · Variance {sh.variance_minor / 100:+,.0f}")
            if sh.close_reason:
                sub += f" ({sh.close_reason[:60]})"
        sections.append({"title": f"Drawer {sh.drawer_code} · business day {sh.business_date:%d-%m-%Y}",
                         "subtitle": sub, "columns": cols, "rows": rows,
                         "totals": {"type": "Shift total", "in": s_in + sh.opening_float_minor, "out": s_out,
                                    "balance": bal}})
    return {"summary": [{"label": "Shifts", "value": len(sections)},
                        {"label": "Cash in (excl. float)", "value": total_in, "money": True},
                        {"label": "Cash out", "value": total_out, "money": True},
                        {"label": "Net cash movement", "value": total_in - total_out, "money": True}],
            "sections": sections}


# ---------------------------------------------------------------- sales

def sales(branch, d_from, d_to, params):
    from apps.orders.models import Check
    tz = branch.tz
    methods = {"cash": "Cash", "external_card": "Card", "rider_cod": "COD", "online": "Online"}
    types = {"dine_in": "Dine-in", "counter": "Takeaway", "pickup": "Pickup", "delivery": "Delivery"}
    qs = (Check.objects.filter(branch=branch, status="closed", business_date__range=(d_from, d_to))
          .select_related("closed_by", "table").order_by("closed_at")
          .annotate(sub=Sum("lines__total_minor", filter=Q(lines__status="active")),
                    disc=Sum("lines__discount_minor", filter=Q(lines__status="active")),
                    tax=Sum("lines__tax_minor", filter=Q(lines__status="active"))))
    rows, totals, by_method = [], defaultdict(int), defaultdict(int)
    checks = list(qs[:MAX_ROWS])
    tenders = defaultdict(list)
    from apps.cash.models import Tender
    for t in Tender.objects.filter(check_ref__in=checks, status="succeeded").values("check_ref_id", "method",
                                                                                     "amount_minor"):
        tenders[t["check_ref_id"]].append(t)
        by_method[methods.get(t["method"], t["method"])] += t["amount_minor"]
    for c in checks:
        sub, disc, tax = c.sub or 0, c.disc or 0, c.tax or 0
        total = sub - disc + tax
        pay = ", ".join(sorted({methods.get(t["method"], t["method"]) for t in tenders[c.pk]}))
        rows.append({"receipt": c.receipt_no or f"#{c.token}", "time": _local(c.closed_at, tz),
                     "order": f"#{c.token}" + (f" T{c.table.label}" if c.table_id else ""),
                     "type": "Online" if c.channel == "web" else types.get(c.service_type, c.service_type),
                     "cashier": c.closed_by.display_name if c.closed_by_id else "",
                     "subtotal": sub, "discount": disc or None, "tax": tax or None, "total": total, "payment": pay})
        totals["subtotal"] += sub
        totals["discount"] += disc
        totals["tax"] += tax
        totals["total"] += total
    cols = [col("receipt", "Receipt"), col("time", "Date / time"), col("order", "Order"), col("type", "Type"),
            col("cashier", "Cashier"), col("subtotal", "Items", True), col("discount", "Discount", True),
            col("tax", "Tax", True), col("total", "Total", True), col("payment", "Paid by")]
    voids = Check.objects.filter(branch=branch, status="voided", business_date__range=(d_from, d_to)).count()
    summary = [{"label": "Bills", "value": len(rows)}, {"label": "Net sales", "value": totals["total"], "money": True},
               {"label": "Discounts", "value": totals["discount"], "money": True}]
    summary += [{"label": f"Paid {k}", "value": v, "money": True} for k, v in sorted(by_method.items())]
    summary.append({"label": "Voided bills", "value": voids})
    return {"summary": summary, "truncated": qs.count() > MAX_ROWS,
            "sections": [{"title": "Closed bills", "subtitle": "Sales by business day, net of line discounts",
                          "columns": cols, "rows": rows,
                          "totals": {"receipt": "Total", **{k: totals[k] for k in ("subtotal", "discount", "tax",
                                                                                     "total")}}}]}


# ---------------------------------------------------------------- supplier

def supplier(branch, d_from, d_to, params):
    from apps.stock.models import PurchaseReceipt, Supplier, SupplierPayment
    from apps.stock.purchasing import supplier_balance
    tz = branch.tz
    wanted = params.get("supplier")
    sups = Supplier.objects.filter(tenant=branch.tenant).order_by("name")
    if wanted:
        sups = sups.filter(pk=wanted)
        if not sups.exists():
            raise NotFound("Supplier not found")
    cols = [col("date", "Date"), col("type", "Entry"), col("ref", "Reference"), col("by", "By"),
            col("debit", "Purchased", True), col("credit", "Paid", True), col("balance", "Balance owed", True)]
    sections, summary_rows, total_owed = [], [], 0
    for s in sups:
        opening = supplier_balance(branch, s, before=d_from)
        entries = []
        for r in PurchaseReceipt.objects.filter(branch=branch, supplier=s, business_date__range=(d_from, d_to)) \
                .select_related("staff", "purchase_order"):
            ref = r.reference or (r.purchase_order.number if r.purchase_order_id else "")
            entries.append((r.created_at, {"date": _local(r.created_at, tz), "type": "Goods received", "ref": ref,
                                           "by": r.staff.display_name, "debit": r.total_minor, "credit": None}))
        for p in SupplierPayment.objects.filter(branch=branch, supplier=s, business_date__range=(d_from, d_to)) \
                .select_related("staff"):
            entries.append((p.created_at, {"date": _local(p.created_at, tz),
                                           "type": f"Payment ({p.get_method_display()})",
                                           "ref": " · ".join(x for x in (p.reference, p.note) if x),
                                           "by": p.staff.display_name, "debit": None, "credit": p.amount_minor}))
        if not entries and not opening and not wanted:
            continue
        bal = opening
        rows = [{"date": f"{d_from:%d-%m-%Y}", "type": "Opening balance", "ref": "", "by": "", "debit": None,
                 "credit": None, "balance": opening}]
        bought = paid = 0
        for _, e in sorted(entries, key=lambda x: x[0]):
            bal += (e["debit"] or 0) - (e["credit"] or 0)
            bought += e["debit"] or 0
            paid += e["credit"] or 0
            rows.append({**e, "balance": bal})
        total_owed += bal
        summary_rows.append({"id": str(s.pk), "supplier": s.name, "phone": s.phone, "opening": opening, "bought": bought,
                             "paid": paid, "closing": bal})
        sections.append({"title": s.name, "subtitle": s.phone or "", "columns": cols, "rows": rows,
                         "totals": {"type": "Closing balance", "debit": bought, "credit": paid, "balance": bal}})
    if not wanted and len(summary_rows) > 1:
        sections.insert(0, {"title": "All suppliers", "subtitle": "Balance owed at the end of the period",
                            "columns": [col("supplier", "Supplier"), col("phone", "Phone"),
                                        col("opening", "Opening", True), col("bought", "Purchased", True),
                                        col("paid", "Paid", True), col("closing", "Owed", True)],
                            "rows": summary_rows,
                            "totals": {"supplier": "Total", "bought": sum(r["bought"] for r in summary_rows),
                                       "paid": sum(r["paid"] for r in summary_rows), "closing": total_owed}})
    return {"summary": [{"label": "Suppliers", "value": len(summary_rows)},
                        {"label": "Purchased", "value": sum(r["bought"] for r in summary_rows), "money": True},
                        {"label": "Paid", "value": sum(r["paid"] for r in summary_rows), "money": True},
                        {"label": "Total owed", "value": total_owed, "money": True}],
            "sections": sections}


# ---------------------------------------------------------------- customer

def customer(branch, d_from, d_to, params):
    from apps.cash.models import Refund, Tender
    from apps.fulfillment.models import Customer
    from apps.orders.models import Check
    tz = branch.tz
    wanted = params.get("customer")
    base = Check.objects.filter(branch=branch, customer__isnull=False, business_date__range=(d_from, d_to)).exclude(
        status__in=["voided", "merged"])
    if wanted:
        cust = Customer.objects.filter(tenant=branch.tenant, pk=wanted).first()
        if cust is None:
            raise NotFound("Customer not found")
        rows, bal, billed_t, paid_t = [], 0, 0, 0
        checks = base.filter(customer=cust).order_by("opened_at").annotate(
            billed=Sum("lines__total_minor", filter=Q(lines__status="active")),
            disc=Sum("lines__discount_minor", filter=Q(lines__status="active")),
            tax=Sum("lines__tax_minor", filter=Q(lines__status="active")))
        for c in checks:
            billed = (c.billed or 0) - (c.disc or 0) + (c.tax or 0)
            paid = Tender.objects.filter(check_ref=c, status="succeeded").aggregate(s=Sum("amount_minor"))["s"] or 0
            refunded = Refund.objects.filter(check_ref=c, status="succeeded").aggregate(s=Sum("amount_minor"))["s"] or 0
            bal += billed - paid + refunded
            billed_t += billed
            paid_t += paid - refunded
            rows.append({"date": _local(c.opened_at, tz), "bill": c.receipt_no or f"#{c.token}",
                         "type": c.get_service_type_display(), "status": c.status, "billed": billed,
                         "paid": paid or None, "refunded": refunded or None, "balance": bal})
        cols = [col("date", "Date"), col("bill", "Bill"), col("type", "Type"), col("status", "Status"),
                col("billed", "Billed", True), col("paid", "Paid", True), col("refunded", "Refunded", True),
                col("balance", "Due", True)]
        return {"summary": [{"label": "Orders", "value": len(rows)}, {"label": "Billed", "value": billed_t, "money": True},
                            {"label": "Paid", "value": paid_t, "money": True},
                            {"label": "Due", "value": bal, "money": True}],
                "sections": [{"title": cust.name or cust.phone, "subtitle": cust.phone, "columns": cols, "rows": rows,
                              "totals": {"bill": "Total", "billed": billed_t, "balance": bal}}]}
    agg = (base.values("customer_id", "customer__name", "customer__phone")
           .annotate(orders=Count("id", distinct=True),
                     billed=Sum("lines__total_minor", filter=Q(lines__status="active")),
                     disc=Sum("lines__discount_minor", filter=Q(lines__status="active")))
           .order_by("-billed"))
    paid = {r["check_ref__customer_id"]: r["s"] for r in Tender.objects.filter(
        check_ref__in=base, status="succeeded").values("check_ref__customer_id").annotate(s=Sum("amount_minor"))}
    refunded = {r["check_ref__customer_id"]: r["s"] for r in Refund.objects.filter(
        check_ref__in=base, status="succeeded").values("check_ref__customer_id").annotate(s=Sum("amount_minor"))}
    rows = []
    for r in agg[:MAX_ROWS]:
        billed = (r["billed"] or 0) - (r["disc"] or 0)
        p = paid.get(r["customer_id"], 0) - refunded.get(r["customer_id"], 0)
        rows.append({"id": str(r["customer_id"]), "customer": r["customer__name"] or "(no name)",
                     "phone": r["customer__phone"], "orders": r["orders"], "billed": billed, "paid": p,
                     "due": billed - p})
    cols = [col("customer", "Customer"), col("phone", "Phone"), col("orders", "Orders", align="right"),
            col("billed", "Billed", True), col("paid", "Paid", True), col("due", "Due", True)]
    return {"summary": [{"label": "Customers", "value": len(rows)},
                        {"label": "Billed", "value": sum(r["billed"] for r in rows), "money": True},
                        {"label": "Due", "value": sum(r["due"] for r in rows), "money": True}],
            "sections": [{"title": "Customers with orders", "subtitle": "Takeaway / delivery customers by phone",
                          "columns": cols, "rows": rows,
                          "totals": {"customer": "Total", "orders": sum(r["orders"] for r in rows),
                                     "billed": sum(r["billed"] for r in rows), "paid": sum(r["paid"] for r in rows),
                                     "due": sum(r["due"] for r in rows)}}]}


# ---------------------------------------------------------------- stock

def _q(v):
    v = Decimal(v or 0).normalize()
    return format(v, "f") if v == v.to_integral() else format(v.quantize(Decimal("0.001")), "f")


def stock(branch, d_from, d_to, params):
    from apps.stock.models import StockItem, StockLevel, StockMovement
    tz = branch.tz
    wanted = params.get("item")
    labels = dict(StockMovement.KINDS)
    if wanted:
        item = StockItem.objects.filter(tenant=branch.tenant, pk=wanted).first()
        if item is None:
            raise NotFound("Stock item not found")
        mv = StockMovement.objects.filter(branch=branch, item=item)
        bal = mv.filter(business_date__lt=d_from).aggregate(s=Sum("quantity"))["s"] or Decimal(0)
        rows = [{"date": f"{d_from:%d-%m-%Y}", "type": "Opening", "ref": "", "by": "", "in": None, "out": None,
                 "balance": _q(bal), "value": None}]
        qin = qout = Decimal(0)
        for m in mv.filter(business_date__range=(d_from, d_to)).select_related("staff").order_by("created_at", "id")[:MAX_ROWS]:
            bal += m.quantity
            qin += max(m.quantity, 0)
            qout += max(-m.quantity, 0)
            value = int(abs(m.quantity) * m.unit_cost_minor) if m.unit_cost_minor is not None else None
            rows.append({"date": _local(m.created_at, tz), "type": labels.get(m.kind, m.kind),
                         "ref": m.reason[:50] or m.source_ref[:30], "by": m.staff.display_name if m.staff_id else "",
                         "in": _q(m.quantity) if m.quantity > 0 else None,
                         "out": _q(-m.quantity) if m.quantity < 0 else None, "balance": _q(bal), "value": value})
        cols = [col("date", "Date"), col("type", "Movement"), col("ref", "Reference"), col("by", "By"),
                col("in", f"In ({item.base_unit})", align="right"), col("out", f"Out ({item.base_unit})", align="right"),
                col("balance", "Balance", align="right"), col("value", "Value", True)]
        return {"summary": [{"label": "In", "value": f"{_q(qin)} {item.base_unit}"},
                            {"label": "Out", "value": f"{_q(qout)} {item.base_unit}"},
                            {"label": "Closing", "value": f"{_q(bal)} {item.base_unit}"}],
                "sections": [{"title": item.name, "subtitle": f"Unit: {item.base_unit}", "columns": cols, "rows": rows,
                              "totals": {"type": "Closing", "in": _q(qin), "out": _q(qout), "balance": _q(bal)}}]}
    rows, total_value = [], 0
    levels = {lv.item_id: lv for lv in StockLevel.objects.filter(branch=branch)}
    for item in StockItem.objects.filter(tenant=branch.tenant).order_by("name"):
        mv = StockMovement.objects.filter(branch=branch, item=item)
        opening = mv.filter(business_date__lt=d_from).aggregate(s=Sum("quantity"))["s"] or Decimal(0)
        period = mv.filter(business_date__range=(d_from, d_to))
        qin = period.filter(quantity__gt=0).aggregate(s=Sum("quantity"))["s"] or Decimal(0)
        qout = -(period.filter(quantity__lt=0).aggregate(s=Sum("quantity"))["s"] or Decimal(0))
        closing = opening + qin - qout
        lv = levels.get(item.pk)
        avg = getattr(lv, "avg_cost_minor", None) if lv else None
        value = int(closing * avg) if avg is not None and closing > 0 else None
        total_value += value or 0
        if not (opening or qin or qout):
            continue
        rows.append({"id": str(item.pk), "item": item.name, "unit": item.base_unit, "opening": _q(opening),
                     "in": _q(qin), "out": _q(qout), "closing": _q(closing), "value": value})
    cols = [col("item", "Item"), col("unit", "Unit"), col("opening", "Opening", align="right"),
            col("in", "In", align="right"), col("out", "Out", align="right"), col("closing", "Closing", align="right"),
            col("value", "Value", True)]
    return {"summary": [{"label": "Items", "value": len(rows)},
                        {"label": "Stock value", "value": total_value, "money": True}],
            "sections": [{"title": "Stock movement summary", "subtitle": "Quantities in base units; value at moving "
                          "average cost", "columns": cols, "rows": rows,
                          "totals": {"item": "Total", "value": total_value}}]}

