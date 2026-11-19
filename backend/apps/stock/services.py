"""Recipe consumption and the stock ledger (spec 04 §Stock and margin, A21; M5 purchasing).

- Consumption is posted once per production item at its first fire (unique source ``ti:<item id>``); a repeated
  fire event finds the existing movement and posts nothing.
- A remake is a new production item and consumes its own ingredients.
- Cancelling an item that never started (queued) posts a compensating reversal of its own consumption. Cancelling
  cooked food or refunding money never creates positive stock; a physical, approved return is a separate movement.
- Packaging is posted at packing handover for takeaway orders (``pk:<item id>``).
- Cost: moving weighted average per branch on receipts; each consumption snapshots the current average.
"""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.errors import Conflict, DomainError, NotFound
from apps.core.scope import resolve_approver

from .models import (PurchaseReceipt, Recipe, StockItem, StockLevel, StockMovement, StockTransfer, Supplier)

HALF = Decimal("0.5")


# ---------------------------------------------------------------- recipe resolution

def _recipe(tenant, kind, code, size):
    rs = {r.size_code: r for r in Recipe.objects.filter(tenant=tenant, target_kind=kind, target_code=code,
                                                       is_active=True).prefetch_related("components__item")}
    return rs.get(size or "") or rs.get("")


def requirements(tenant, spec, product_kinds):
    """[(recipe, factor)] for one physical component spec. product_kinds: code -> kind (from the menu)."""
    out, missing = [], []
    product = spec.get("product")
    size = spec.get("variant") or ""
    kind = product_kinds.get(product, "item")

    def add(k, code, factor):
        r = _recipe(tenant, k, code, size)
        if r is None:
            missing.append(f"{k}:{code}:{size}")
        else:
            out.append((r, factor))

    if kind in ("pizza", "mixed_pizza"):
        add("base", "pizza", Decimal(1))
        if kind == "mixed_pizza":
            halves = spec.get("halves") or {}
            add("product", halves.get("left"), HALF)
            add("product", halves.get("right"), HALF)
        else:
            add("product", product, Decimal(1))
        if spec.get("crust"):
            r = _recipe(tenant, "option", spec["crust"], size)
            if r:
                out.append((r, Decimal(1)))
        for o in spec.get("options") or []:
            r = _recipe(tenant, "option", o.get("id"), size)
            if r:
                out.append((r, Decimal(1) if o.get("side", "whole") == "whole" else HALF))
            else:
                missing.append(f"option:{o.get('id')}:{size}")
    else:
        add("product", product, Decimal(1))
    return out, missing


def _component_spec(line, index):
    spec = line.spec or {}
    if spec.get("selections") is not None:
        sels = spec["selections"]
        return sels[index] if index < len(sels) else {}
    return spec


def _product_kinds(line):
    from apps.orders.services import menu_for
    menu = menu_for(line.menu_release)
    return {code: p["kind"] for code, p in menu.products.items()}


# ---------------------------------------------------------------- ledger primitives

def _level(branch, item):
    lvl = StockLevel.objects.select_for_update().filter(branch=branch, item=item).first()
    if lvl is None:
        try:
            with transaction.atomic():
                lvl = StockLevel.objects.create(branch=branch, item=item)
        except IntegrityError:
            pass
        lvl = StockLevel.objects.select_for_update().get(branch=branch, item=item)
    return lvl


def post(branch, item, kind, quantity, source_ref, *, recipe=None, reverses=None, staff=None, approved_by=None,
         reason="", unit_cost=None):
    """Post one movement idempotently. Returns (movement, created)."""
    existing = StockMovement.objects.filter(branch=branch, kind=kind, source_ref=source_ref, item=item).first()
    if existing:
        return existing, False
    lvl = _level(branch, item)
    if unit_cost is not None and quantity > 0 and kind in ("receipt", "transfer_in"):
        # Moving weighted average on inbound stock with a cost.
        on_hand = max(lvl.on_hand, Decimal(0))
        old_cost = lvl.avg_cost_minor if lvl.avg_cost_minor is not None else Decimal(unit_cost)
        total = on_hand + quantity
        lvl.avg_cost_minor = ((on_hand * old_cost) + (quantity * Decimal(unit_cost))) / total if total else Decimal(unit_cost)
    cost = unit_cost if unit_cost is not None else lvl.avg_cost_minor
    try:
        with transaction.atomic():
            m = StockMovement.objects.create(
                branch=branch, item=item, kind=kind, quantity=quantity, unit_cost_minor=cost, source_ref=source_ref,
                recipe=recipe, reverses=reverses, staff=staff, approved_by=approved_by, reason=reason[:200],
                business_date=branch.business_date(),
            )
    except IntegrityError:  # concurrent identical posting won
        return StockMovement.objects.get(branch=branch, kind=kind, source_ref=source_ref, item=item), False
    lvl.on_hand += quantity
    lvl.save()
    return m, True


# ---------------------------------------------------------------- production hooks

def consume_item(branch, ticket_item, staff=None):
    """Post ingredient consumption for one production item at first fire. Idempotent per item."""
    line = ticket_item.line
    spec = _component_spec(line, ticket_item.component_index)
    if not spec:
        return []
    reqs, _missing = requirements(branch.tenant, spec, _product_kinds(line))
    posted = []
    totals = {}
    for recipe, factor in reqs:
        for c in recipe.components.all():
            key = c.item_id
            q = c.quantity * factor * ticket_item.quantity
            if key in totals:
                totals[key][1] += q
            else:
                totals[key] = [c.item, q, recipe]
    for item, qty, recipe in totals.values():
        m, created = post(branch, item, "consumption", -qty, f"ti:{ticket_item.pk}", recipe=recipe, staff=staff)
        if created:
            posted.append(m)
    return posted


def _reversed(m):
    from django.db.models import Sum
    return StockMovement.objects.filter(reverses=m).aggregate(s=Sum("quantity"))["s"] or Decimal(0)


def reverse_item(branch, ticket_item, reason, staff=None):
    """Compensate what is left of an item's original consumption (never started, or replaced)."""
    out = []
    for m in StockMovement.objects.filter(branch=branch, kind="consumption", source_ref=f"ti:{ticket_item.pk}"):
        remaining = -m.quantity - _reversed(m)
        if remaining <= 0:
            continue
        r, created = post(branch, m.item, "reversal", remaining, f"rv:{ticket_item.pk}", recipe=m.recipe,
                          reverses=m, staff=staff, reason=reason, unit_cost=m.unit_cost_minor)
        if created:
            out.append(r)
    return out


def split_consumption(branch, item, twin, share, staff=None):
    """Line split: move `share` of an item's consumption to its twin item (no net stock change)."""
    for m in StockMovement.objects.filter(branch=branch, kind="consumption", source_ref=f"ti:{item.pk}"):
        q = (-m.quantity * share).quantize(Decimal("0.001"))
        if q <= 0:
            continue
        post(branch, m.item, "reversal", q, f"sp:{item.pk}:{twin.pk}", recipe=m.recipe, reverses=m, staff=staff,
             reason="line split", unit_cost=m.unit_cost_minor)
        post(branch, m.item, "consumption", -q, f"ti:{twin.pk}", recipe=m.recipe, staff=staff,
             unit_cost=m.unit_cost_minor)


def post_packaging(ctx, check, items):
    kinds = {}
    for it in items:
        line = it.line
        spec = _component_spec(line, it.component_index)
        if not spec:
            continue
        if line.menu_release_id not in kinds:
            kinds[line.menu_release_id] = _product_kinds(line)
        kind = kinds[line.menu_release_id].get(spec.get("product"), "item")
        target = "pizza" if kind in ("pizza", "mixed_pizza") else spec.get("product")
        r = _recipe(ctx.branch.tenant, "packaging", target, spec.get("variant") or "")
        if r is None:
            continue
        for c in r.components.all():
            post(ctx.branch, c.item, "packaging", -(c.quantity * it.quantity), f"pk:{it.pk}", recipe=r, staff=ctx.staff)


# ---------------------------------------------------------------- reads

def levels(branch):
    rows = []
    lv = {lv.item_id: lv for lv in StockLevel.objects.filter(branch=branch)}
    for item in StockItem.objects.filter(tenant=branch.tenant, is_active=True).order_by("name"):
        lvl = lv.get(item.pk)
        rows.append({"id": str(item.pk), "code": item.code, "name": item.name, "unit": item.base_unit,
                     "purchase_unit": item.purchase_unit, "purchase_factor": item.purchase_factor,
                     "on_hand": lvl.on_hand if lvl else Decimal(0),
                     "avg_cost_minor": lvl.avg_cost_minor if lvl else None,
                     "cost_missing": not lvl or lvl.avg_cost_minor is None})
    return {"items": rows, "server_time": timezone.now()}


def movements(branch, item_id=None, limit=100):
    qs = StockMovement.objects.filter(branch=branch).select_related("item", "staff", "approved_by")
    if item_id:
        qs = qs.filter(item_id=item_id)
    return {"movements": [{
        "id": m.pk, "item": m.item.code, "kind": m.kind, "quantity": m.quantity, "unit": m.item.base_unit,
        "unit_cost_minor": m.unit_cost_minor, "source_ref": m.source_ref, "reason": m.reason,
        "staff": m.staff.display_name if m.staff_id else None,
        "approved_by": m.approved_by.display_name if m.approved_by_id else None,
        "business_date": m.business_date, "created_at": m.created_at} for m in qs.order_by("-id")[:limit]]}


# ---------------------------------------------------------------- commands (M5)

def _item(branch, code_or_id):
    qs = StockItem.objects.filter(tenant=branch.tenant)
    item = qs.filter(code=code_or_id).first()
    if item is None:
        try:
            item = qs.filter(pk=code_or_id).first()
        except Exception:
            item = None
    if item is None:
        raise NotFound(f"Stock item {code_or_id} not found")
    return item


def _qty(v, field):
    try:
        q = Decimal(str(v))
    except Exception:
        raise DomainError("Quantity must be a number", field_errors={field: "invalid"})
    if q <= 0:
        raise DomainError("Quantity must be positive", field_errors={field: "too_small"})
    return q


def receive(ctx, data):
    """Purchase receipt: lines [{item, quantity (purchase units or base with unit='base'), cost_minor}]."""
    lines = data.get("lines") or []
    if not lines:
        raise DomainError("Add at least one line", field_errors={"lines": "empty"})
    supplier = None
    if data.get("supplier"):
        supplier, _ = Supplier.objects.get_or_create(tenant=ctx.branch.tenant, name=str(data["supplier"])[:80])
    rec = PurchaseReceipt.objects.create(branch=ctx.branch, supplier=supplier, reference=str(data.get("reference", ""))[:60],
                                         staff=ctx.staff, business_date=ctx.branch.business_date())
    total = 0
    out = []
    for i, ln in enumerate(lines):
        item = _item(ctx.branch, ln.get("item"))
        q = _qty(ln.get("quantity"), f"lines[{i}].quantity")
        base = q if ln.get("unit") == "base" else q * item.purchase_factor
        cost = int(ln.get("cost_minor") or 0)
        unit_cost = (Decimal(cost) / base) if cost else None
        m, _ = post(ctx.branch, item, "receipt", base, f"rc:{rec.pk}:{i}", staff=ctx.staff, unit_cost=unit_cost,
                    reason=rec.reference)
        total += cost
        out.append({"item": item.code, "quantity": base, "unit": item.base_unit})
    rec.total_minor = total
    rec.save(update_fields=["total_minor"])
    ctx.audit("stock.receive", rec, None, 1, data={"lines": len(out), "total_minor": total})
    ctx.emit("stock.received", rec, {"receipt_id": rec.pk, "total_minor": total})
    return {"receipt_id": str(rec.pk), "lines": out, "total_minor": total}


def record_loss(ctx, data):
    """Explicit loss for stock discarded outside tracked production (manager)."""
    item = _item(ctx.branch, data.get("item"))
    q = _qty(data.get("quantity"), "quantity")
    reason = str(data.get("reason", "")).strip()
    if not reason:
        raise DomainError("Give a reason", field_errors={"reason": "required"})
    approver = resolve_approver(ctx.principal, data, "stock.adjust")
    ref = f"ls:{ctx.key}"
    m, _ = post(ctx.branch, item, "loss", -q, ref, staff=ctx.staff, approved_by=approver, reason=reason)
    ctx.audit("stock.loss", item, None, None, reason=reason, approved_by=approver, data={"quantity": str(q)})
    return {"movement_id": m.pk, "item": item.code, "quantity": -q}


def record_return(ctx, data):
    """Approved physical return of unopened goods to stock (separate from any money refund)."""
    item = _item(ctx.branch, data.get("item"))
    q = _qty(data.get("quantity"), "quantity")
    reason = str(data.get("reason", "")).strip() or "physical return"
    approver = resolve_approver(ctx.principal, data, "stock.adjust")
    m, _ = post(ctx.branch, item, "return", q, f"rt:{ctx.key}", staff=ctx.staff, approved_by=approver, reason=reason)
    ctx.audit("stock.return", item, None, None, reason=reason, approved_by=approver, data={"quantity": str(q)})
    return {"movement_id": m.pk, "item": item.code, "quantity": q}


def count(ctx, data):
    """Physical count: posts the variance as a count adjustment, manager-approved."""
    lines = data.get("lines") or []
    if not lines:
        raise DomainError("Add counted items", field_errors={"lines": "empty"})
    approver = resolve_approver(ctx.principal, data, "stock.adjust")
    out = []
    for i, ln in enumerate(lines):
        item = _item(ctx.branch, ln.get("item"))
        try:
            counted = Decimal(str(ln.get("counted")))
        except Exception:
            raise DomainError("counted must be a number", field_errors={f"lines[{i}].counted": "invalid"})
        lvl = _level(ctx.branch, item)
        variance = counted - lvl.on_hand
        if variance:
            post(ctx.branch, item, "count", variance, f"ct:{ctx.key}:{i}", staff=ctx.staff, approved_by=approver,
                 reason=str(data.get("reason", "count"))[:200])
        out.append({"item": item.code, "counted": counted, "variance": variance})
    ctx.audit("stock.count", ctx.branch, None, None, approved_by=approver, data={"lines": len(out)})
    return {"lines": out}


def send_transfer(ctx, data):
    from apps.core.models import Branch
    to = Branch.objects.filter(tenant=ctx.branch.tenant, pk=data.get("to_branch_id")).exclude(pk=ctx.branch.pk).first()
    if to is None:
        raise NotFound("Destination branch not found")
    lines = []
    for i, ln in enumerate(data.get("lines") or []):
        item = _item(ctx.branch, ln.get("item"))
        lines.append({"item": item.code, "quantity": str(_qty(ln.get("quantity"), f"lines[{i}].quantity"))})
    if not lines:
        raise DomainError("Add items to transfer", field_errors={"lines": "empty"})
    tr = StockTransfer.objects.create(from_branch=ctx.branch, to_branch=to, lines=lines, sent_by=ctx.staff)
    for i, ln in enumerate(lines):
        item = _item(ctx.branch, ln["item"])
        post(ctx.branch, item, "transfer_out", -Decimal(ln["quantity"]), f"tr:{tr.pk}:{i}", staff=ctx.staff)
    ctx.audit("stock.transfer_out", tr, None, 1, data={"to": to.code, "lines": lines})
    ctx.emit("stock.transfer_sent", tr, {"transfer_id": tr.pk, "to_branch": to.pk})
    return {"transfer_id": str(tr.pk), "status": tr.status}


def receive_transfer(ctx, transfer_id):
    tr = StockTransfer.objects.select_for_update().filter(pk=transfer_id, to_branch=ctx.branch).first()
    if tr is None:
        raise NotFound("Transfer not found")
    if tr.status == "received":
        raise Conflict("Transfer already received", code="already_received")
    for i, ln in enumerate(tr.lines):
        item = _item(ctx.branch, ln["item"])
        out_m = StockMovement.objects.filter(branch=tr.from_branch, source_ref=f"tr:{tr.pk}:{i}").first()
        post(ctx.branch, item, "transfer_in", Decimal(ln["quantity"]), f"tr:{tr.pk}:{i}", staff=ctx.staff,
             unit_cost=out_m.unit_cost_minor if out_m else None)
    tr.status, tr.received_by, tr.received_at = "received", ctx.staff, timezone.now()
    tr.revision += 1
    tr.save()
    ctx.audit("stock.transfer_in", tr, tr.revision - 1, tr.revision)
    return 200, {"transfer_id": str(tr.pk), "status": tr.status}
