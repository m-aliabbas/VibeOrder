"""Purchase orders, reorder suggestions and count sheets (M5 purchasing + counts).

Purchase orders never move stock themselves: goods enter the ledger only when received against the order (one
PurchaseReceipt per delivery, through the same `receive` path as an ad-hoc receipt), so partial deliveries and
"closed short" orders are explicit. Quantities are stored in base units; the API takes purchase units by default
(`unit: "base"` for base units), exactly like ad-hoc receipts.

Count sheets let staff count many items in one session while service continues: each line stores the theoretical
on-hand at the moment it was counted, and approval posts `counted − expected` per line as a count adjustment
(manager, `stock.adjust`). Lines entered again are re-snapshotted. Approval is idempotent per sheet.
"""
import math
from decimal import Decimal

from django.utils import timezone

from apps.core.commands import next_sequence
from apps.core.errors import Conflict, DomainError, NotFound, StaleRevision
from apps.core.scope import resolve_approver

from .models import (PurchaseOrder, PurchaseOrderLine, PurchaseReceipt, StockCount, StockCountLine, StockItem,
                     StockLevel, Supplier)
from .services import _item, _level, _qty, post, receive

OPEN_PO = ("draft", "sent", "partial")


def _q(d):
    """Decimal → plain string without trailing zeros (for messages)."""
    d = Decimal(d).normalize()
    return f"{d:f}"


def _date(v):
    if not v:
        return None
    from django.utils.dateparse import parse_date
    try:
        d = parse_date(str(v))
    except ValueError:
        d = None
    if d is None:
        raise DomainError("Use a date like 2026-11-21", field_errors={"expected_on": "invalid"})
    return d


def _check_revision(obj, data):
    if data.get("expected_revision") is not None and int(data["expected_revision"]) != obj.revision:
        raise StaleRevision(f"{obj.number} was changed on another device", current_revision=obj.revision)


# ---------------------------------------------------------------- suppliers

def suppliers(branch):
    return {"suppliers": [{"id": str(s.pk), "name": s.name, "phone": s.phone, "note": s.note}
                          for s in Supplier.objects.filter(tenant=branch.tenant, is_active=True).order_by("name")]}


def save_supplier(ctx, data, supplier_id=None):
    if supplier_id:
        s = Supplier.objects.select_for_update().filter(tenant=ctx.branch.tenant, pk=supplier_id).first()
        if s is None:
            raise NotFound("Supplier not found")
    else:
        s = Supplier(tenant=ctx.branch.tenant)
    name = str(data.get("name", s.name or "")).strip()
    if not name:
        raise DomainError("Name is required", field_errors={"name": "required"})
    s.name, s.phone = name[:80], str(data.get("phone", s.phone or ""))[:30]
    s.note = str(data.get("note", s.note or ""))[:200]
    if "active" in data:
        s.is_active = bool(data["active"])
    if supplier_id:
        s.revision += 1
    s.save()
    ctx.audit("stock.supplier_saved", s, None, s.revision, data={"name": s.name})
    return (200 if supplier_id else 201), {"id": str(s.pk), "name": s.name, "phone": s.phone, "note": s.note}


def _supplier(ctx, value):
    if not value:
        return None
    s = Supplier.objects.filter(tenant=ctx.branch.tenant, pk=value).first() if _is_uuid(value) else None
    if s is None:
        s, _ = Supplier.objects.get_or_create(tenant=ctx.branch.tenant, name=str(value)[:80])
    return s


def _is_uuid(v):
    import uuid
    try:
        uuid.UUID(str(v))
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- reorder suggestions

def on_order(branch):
    """item id -> base units ordered but not yet received on open purchase orders."""
    out = {}
    for ln in PurchaseOrderLine.objects.filter(order__branch=branch, order__status__in=["sent", "partial"]):
        out[ln.item_id] = out.get(ln.item_id, Decimal(0)) + max(ln.quantity - ln.received, Decimal(0))
    return out


def suggestions(branch):
    """Items at or below their reorder level (counting stock already on order), with a quantity that brings them
    up to par, rounded up to whole purchase units."""
    levels = {lv.item_id: lv for lv in StockLevel.objects.filter(branch=branch)}
    ordered = on_order(branch)
    rows = []
    for item in StockItem.objects.filter(tenant=branch.tenant, is_active=True).order_by("name"):
        lvl = levels.get(item.pk)
        if lvl is None or lvl.reorder_level is None:
            continue
        on_hand, incoming = lvl.on_hand, ordered.get(item.pk, Decimal(0))
        if on_hand + incoming > lvl.reorder_level:
            continue
        par = lvl.par_level if lvl.par_level is not None else lvl.reorder_level * 2
        need = max(par - on_hand - incoming, Decimal(0))
        factor = item.purchase_factor or Decimal(1)
        packs = math.ceil(need / factor) if need > 0 else 0
        if packs == 0:
            continue
        qty = packs * factor
        rows.append({
            "item": item.code, "name": item.name, "unit": item.base_unit, "purchase_unit": item.purchase_unit,
            "purchase_factor": factor, "on_hand": on_hand, "on_order": incoming, "reorder_level": lvl.reorder_level,
            "par_level": par, "suggested_packs": packs, "suggested_quantity": qty,
            "estimated_cost_minor": int((qty * lvl.avg_cost_minor).quantize(Decimal(1)))
            if lvl.avg_cost_minor is not None else None,
        })
    return {"suggestions": rows}


# ---------------------------------------------------------------- purchase orders

def _po_lines(ctx, lines):
    out, seen = [], set()
    for i, ln in enumerate(lines or []):
        item = _item(ctx.branch, ln.get("item"))
        if item.pk in seen:
            raise DomainError(f"{item.name} is listed twice", field_errors={f"lines[{i}].item": "duplicate"})
        seen.add(item.pk)
        q = _qty(ln.get("quantity"), f"lines[{i}].quantity")
        base = q if ln.get("unit") == "base" else q * item.purchase_factor
        out.append((item, base, int(ln.get("expected_cost_minor") or 0)))
    if not out:
        raise DomainError("Add at least one item", field_errors={"lines": "empty"})
    return out


def serialize_po(po, with_message=True):
    lines = []
    total = 0
    for ln in po.lines.select_related("item").order_by("id"):
        f = ln.item.purchase_factor or Decimal(1)
        lines.append({
            "id": ln.pk, "item": ln.item.code, "name": ln.item.name, "unit": ln.item.base_unit,
            "purchase_unit": ln.item.purchase_unit, "purchase_factor": f, "quantity": ln.quantity,
            "packs": (ln.quantity / f).normalize(), "received": ln.received,
            "outstanding": max(ln.quantity - ln.received, Decimal(0)), "expected_cost_minor": ln.expected_cost_minor,
        })
        total += ln.expected_cost_minor
    out = {
        "id": str(po.pk), "number": po.number, "status": po.status, "revision": po.revision,
        "supplier": {"id": str(po.supplier.pk), "name": po.supplier.name, "phone": po.supplier.phone}
        if po.supplier_id else None,
        "expected_on": po.expected_on, "note": po.note, "created_by": po.created_by.display_name,
        "created_at": po.created_at, "sent_at": po.sent_at, "closed_reason": po.closed_reason,
        "expected_total_minor": total, "lines": lines,
        "receipts": [{"id": str(r.pk), "reference": r.reference, "total_minor": r.total_minor,
                      "staff": r.staff.display_name, "created_at": r.created_at}
                     for r in po.receipts.select_related("staff").order_by("created_at")],
    }
    if with_message:
        out["message"] = supplier_message(po, lines)
    return out


def supplier_message(po, lines=None):
    """Plain text the manager can send to the supplier (WhatsApp / SMS)."""
    lines = lines if lines is not None else serialize_po(po, with_message=False)["lines"]
    rows = [f"{po.branch.name}", f"Purchase order {po.number}"]
    if po.expected_on:
        rows.append(f"Deliver by {po.expected_on:%a %d %b}")
    rows.append("")
    for ln in lines:
        pu = ln["purchase_unit"]
        qty = f"{_q(ln['packs'])} × {pu}" if pu else f"{_q(ln['quantity'])} {ln['unit']}"
        rows.append(f"- {ln['name']}: {qty}")
    if po.note:
        rows += ["", po.note]
    if po.branch.address:
        rows += ["", po.branch.address]
    return "\n".join(rows)


def purchase_orders(branch, status=None):
    qs = PurchaseOrder.objects.filter(branch=branch).select_related("supplier", "created_by", "branch")
    if status == "open":
        qs = qs.filter(status__in=OPEN_PO)
    elif status:
        qs = qs.filter(status=status)
    return {"orders": [serialize_po(po, with_message=False) for po in qs.order_by("-created_at")[:100]]}


def _get_po(ctx, po_id):
    po = PurchaseOrder.objects.select_for_update().filter(branch=ctx.branch, pk=po_id).first()
    if po is None:
        raise NotFound("Purchase order not found")
    return po


def create_po(ctx, data):
    lines = _po_lines(ctx, data.get("lines"))
    n = next_sequence(ctx.branch.pk, "purchase_order")
    po = PurchaseOrder.objects.create(
        branch=ctx.branch, number=f"PO-{ctx.branch.code}-{n:04d}", supplier=_supplier(ctx, data.get("supplier")),
        expected_on=_date(data.get("expected_on")), note=str(data.get("note", ""))[:200], created_by=ctx.staff)
    PurchaseOrderLine.objects.bulk_create([PurchaseOrderLine(order=po, item=it, quantity=q, expected_cost_minor=c)
                                           for it, q, c in lines])
    if data.get("send"):
        po.status, po.sent_at = "sent", timezone.now()
        po.save(update_fields=["status", "sent_at"])
    ctx.audit("stock.po_created", po, None, po.revision, data={"lines": len(lines), "number": po.number})
    ctx.emit("stock.po_created", po, {"po_id": po.pk, "number": po.number})
    return 201, serialize_po(po)


def update_po(ctx, po_id, data):
    """Edit a draft (replace lines, supplier, date, note) or mark it sent."""
    po = _get_po(ctx, po_id)
    _check_revision(po, data)
    action = data.get("action", "edit")
    prev = po.revision
    if action == "edit":
        if po.status != "draft":
            raise Conflict("Only a draft can be edited; cancel and create a new order", code="not_draft")
        if "lines" in data:
            lines = _po_lines(ctx, data["lines"])
            po.lines.all().delete()
            PurchaseOrderLine.objects.bulk_create([PurchaseOrderLine(order=po, item=it, quantity=q,
                                                                     expected_cost_minor=c) for it, q, c in lines])
        if "supplier" in data:
            po.supplier = _supplier(ctx, data["supplier"])
        if "expected_on" in data:
            po.expected_on = _date(data["expected_on"])
        if "note" in data:
            po.note = str(data["note"] or "")[:200]
    elif action == "send":
        if po.status != "draft":
            raise Conflict("Order was already sent", code="already_sent")
        po.status, po.sent_at = "sent", timezone.now()
    elif action in ("cancel", "close"):
        reason = str(data.get("reason", "")).strip()
        if not reason:
            raise DomainError("Give a reason", field_errors={"reason": "required"})
        if action == "cancel" and po.status not in ("draft", "sent"):
            raise Conflict("Goods were already received; close it short instead", code="has_receipts")
        if action == "close" and po.status != "partial":
            raise Conflict("Only a partly received order can be closed short", code="not_partial")
        po.status, po.closed_reason = ("cancelled" if action == "cancel" else "closed"), reason[:200]
    else:
        raise DomainError("Unknown action", field_errors={"action": "invalid"})
    po.revision += 1
    po.save()
    ctx.audit(f"stock.po_{action}", po, prev, po.revision, reason=po.closed_reason)
    return 200, serialize_po(po)


def receive_po(ctx, po_id, data):
    """Receive a delivery against an order: lines [{line_id, quantity (purchase units, or unit=base), cost_minor}].
    Posts stock through the normal receipt path and links the receipt to the order."""
    po = _get_po(ctx, po_id)
    _check_revision(po, data)
    if po.status not in ("sent", "partial", "draft"):
        raise Conflict(f"{po.number} is {po.get_status_display().lower()}", code="po_closed")
    by_id = {ln.pk: ln for ln in po.lines.select_for_update().select_related("item")}
    rec_lines, updates = [], []
    for i, ln in enumerate(data.get("lines") or []):
        pol = by_id.get(int(ln.get("line_id") or 0))
        if pol is None:
            raise DomainError("Line is not on this order", field_errors={f"lines[{i}].line_id": "invalid"})
        q = Decimal(str(ln.get("quantity") or 0))
        if q < 0:
            raise DomainError("Quantity must be positive", field_errors={f"lines[{i}].quantity": "too_small"})
        if q == 0:
            continue
        base = q if ln.get("unit") == "base" else q * pol.item.purchase_factor
        rec_lines.append({"item": pol.item.code, "quantity": str(base), "unit": "base",
                          "cost_minor": int(ln.get("cost_minor") or 0)})
        updates.append((pol, base))
    if not rec_lines:
        raise DomainError("Enter what arrived", field_errors={"lines": "empty"})
    result = receive(ctx, {"supplier": po.supplier.name if po.supplier_id else "",
                           "reference": str(data.get("reference") or po.number)[:60], "lines": rec_lines})
    PurchaseReceipt.objects.filter(pk=result["receipt_id"]).update(purchase_order=po)
    for pol, base in updates:
        pol.received += base
        pol.save(update_fields=["received"])
    prev = po.revision
    complete = all(ln.received >= ln.quantity for ln in by_id.values())
    po.status = "received" if complete else "partial"
    if po.sent_at is None:
        po.sent_at = timezone.now()
    po.revision += 1
    po.save()
    ctx.audit("stock.po_received", po, prev, po.revision, data={"receipt": result["receipt_id"]})
    return 201, {**serialize_po(po), "receipt_id": result["receipt_id"]}


# ---------------------------------------------------------------- count sheets

def serialize_count(sc):
    lines, value, counted_n = [], Decimal(0), 0
    for ln in sc.lines.select_related("item", "counted_by").order_by("item__name"):
        variance = (ln.counted - ln.expected) if ln.counted is not None and ln.expected is not None else None
        v_value = (variance * ln.unit_cost_minor) if variance is not None and ln.unit_cost_minor is not None else None
        if ln.counted is not None:
            counted_n += 1
        if v_value is not None:
            value += v_value
        lines.append({
            "item": ln.item.code, "name": ln.item.name, "unit": ln.item.base_unit, "counted": ln.counted,
            "expected": ln.expected, "variance": variance,
            "variance_value_minor": int(v_value.quantize(Decimal(1))) if v_value is not None else None,
            "counted_by": ln.counted_by.display_name if ln.counted_by_id else None, "counted_at": ln.counted_at,
        })
    return {
        "id": str(sc.pk), "number": sc.number, "status": sc.status, "revision": sc.revision, "note": sc.note,
        "created_by": sc.created_by.display_name, "created_at": sc.created_at,
        "submitted_by": sc.submitted_by.display_name if sc.submitted_by_id else None,
        "approved_by": sc.approved_by.display_name if sc.approved_by_id else None, "approved_at": sc.approved_at,
        "lines": lines, "counted": counted_n, "total": len(lines),
        "variance_value_minor": int(value.quantize(Decimal(1))),
    }


def count_sheets(branch):
    qs = StockCount.objects.filter(branch=branch).select_related("created_by", "submitted_by", "approved_by")
    return {"counts": [{k: v for k, v in serialize_count(sc).items() if k != "lines"}
                       for sc in qs.order_by("-created_at")[:50]]}


def _get_count(ctx, count_id):
    sc = StockCount.objects.select_for_update().filter(branch=ctx.branch, pk=count_id).first()
    if sc is None:
        raise NotFound("Count sheet not found")
    return sc


def start_count(ctx, data):
    """Start a count sheet for the given item codes, or every active item."""
    if StockCount.objects.filter(branch=ctx.branch, status__in=["open", "submitted"]).exists():
        raise Conflict("Finish or cancel the open count first", code="count_open")
    codes = data.get("items")
    items = StockItem.objects.filter(tenant=ctx.branch.tenant, is_active=True)
    if codes:
        items = items.filter(code__in=codes)
    items = list(items)
    if not items:
        raise DomainError("No items to count", field_errors={"items": "empty"})
    n = next_sequence(ctx.branch.pk, "stock_count")
    sc = StockCount.objects.create(branch=ctx.branch, number=f"CT-{ctx.branch.code}-{n:04d}", created_by=ctx.staff,
                                   note=str(data.get("note", ""))[:200], business_date=ctx.branch.business_date())
    StockCountLine.objects.bulk_create([StockCountLine(count=sc, item=it) for it in items])
    ctx.audit("stock.count_started", sc, None, sc.revision, data={"items": len(items)})
    return 201, serialize_count(sc)


def enter_counts(ctx, count_id, data):
    """Record counted quantities [{item, counted}] (base units). Snapshots the theoretical on-hand now."""
    sc = _get_count(ctx, count_id)
    if sc.status != "open":
        raise Conflict("This count is no longer open", code="count_closed")
    by_item = {ln.item.code: ln for ln in sc.lines.select_related("item")}
    now = timezone.now()
    for i, row in enumerate(data.get("lines") or []):
        ln = by_item.get(str(row.get("item")))
        if ln is None:
            raise DomainError("Item is not on this count", field_errors={f"lines[{i}].item": "invalid"})
        if row.get("counted") in (None, ""):
            ln.counted = ln.expected = ln.counted_at = ln.counted_by = None
        else:
            try:
                c = Decimal(str(row["counted"]))
            except Exception:
                raise DomainError("Counted must be a number", field_errors={f"lines[{i}].counted": "invalid"})
            if c < 0:
                raise DomainError("Counted cannot be negative", field_errors={f"lines[{i}].counted": "too_small"})
            lvl = _level(ctx.branch, ln.item)
            ln.counted, ln.expected, ln.unit_cost_minor = c, lvl.on_hand, lvl.avg_cost_minor
            ln.counted_at, ln.counted_by = now, ctx.staff
        ln.save()
    sc.revision += 1
    sc.save(update_fields=["revision", "updated_at"])
    return 200, serialize_count(sc)


def finish_count(ctx, count_id, data):
    """action: submit (counter) | approve (manager: posts variances) | cancel."""
    sc = _get_count(ctx, count_id)
    _check_revision(sc, data)
    action = data.get("action")
    prev = sc.revision
    if action == "submit":
        if sc.status != "open":
            raise Conflict("Already submitted", code="count_closed")
        if not sc.lines.exclude(counted=None).exists():
            raise DomainError("Count at least one item", field_errors={"lines": "empty"})
        sc.status, sc.submitted_by, sc.submitted_at = "submitted", ctx.staff, timezone.now()
    elif action == "approve":
        if sc.status not in ("open", "submitted"):
            raise Conflict("This count is closed", code="count_closed")
        approver = resolve_approver(ctx.principal, data, "stock.adjust")
        posted = 0
        for ln in sc.lines.exclude(counted=None).select_related("item"):
            variance = ln.counted - ln.expected
            if variance:
                post(ctx.branch, ln.item, "count", variance, f"cs:{sc.pk}:{ln.item_id}", staff=ln.counted_by,
                     approved_by=approver, reason=f"count {sc.number}")
                posted += 1
        sc.status, sc.approved_by, sc.approved_at = "approved", approver, timezone.now()
        if sc.submitted_at is None:
            sc.submitted_by, sc.submitted_at = ctx.staff, sc.approved_at
        ctx.emit("stock.counted", sc, {"count_id": sc.pk, "adjusted": posted})
    elif action == "cancel":
        if sc.status not in ("open", "submitted"):
            raise Conflict("This count is closed", code="count_closed")
        sc.status = "cancelled"
    else:
        raise DomainError("Unknown action", field_errors={"action": "invalid"})
    sc.revision += 1
    sc.save()
    ctx.audit(f"stock.count_{action}", sc, prev, sc.revision,
              approved_by=sc.approved_by if action == "approve" else None)
    return 200, serialize_count(sc)


def count_detail(branch, count_id):
    sc = StockCount.objects.filter(branch=branch, pk=count_id).first()
    if sc is None:
        raise NotFound("Count sheet not found")
    return serialize_count(sc)


def po_detail(branch, po_id):
    po = PurchaseOrder.objects.filter(branch=branch, pk=po_id).select_related("supplier", "created_by", "branch").first()
    if po is None:
        raise NotFound("Purchase order not found")
    return serialize_po(po)

