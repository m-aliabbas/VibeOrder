"""Corrections after send: remove / modify a sent line, kitchen remake, void, move and merge (spec 01 §Corrections,
spec 03 §State rules). Every command locks the check, checks the observed revision, records a CheckAdjustment and
sends the affected stations a change ticket that stays visible until acknowledged."""
from datetime import timedelta

from django.db.models import F
from django.utils import timezone

from apps.core.commands import next_sequence
from apps.core.errors import Conflict, DomainError, NotFound
from apps.core.scope import resolve_approver

from apps.stock.services import consume_item, reverse_item

from .discounts import recompute_if_discounted
from .models import Check, CheckAdjustment, CheckDiscount, CheckLine, CheckRound, DiningTable, Quote
from .services import _items_for_hash, check_amounts, lock_check, pricer_for, validate_expected_revision

COOKED = {"ready", "packed", "served"}
LIVE = {"held", "queued", "preparing"}


# ---------------------------------------------------------------- plain-language deltas

def _parse_kitchen_lines(lines):
    """'RIGHT half: Chicken Fajita + Extra cheese, Olives' -> {'RIGHT half': ('Chicken Fajita', {...})}."""
    out = {}
    for raw in lines or []:
        key, sep, text = raw.partition(":")
        if not sep:
            key, text = "", raw
        text = text.strip()
        if text.startswith("+ "):
            base, adds = "", text[2:]
        else:
            base, _, adds = text.partition(" + ")
        prev_base, prev_adds = out.get(key.strip(), ("", set()))
        names = {a.strip() for a in adds.split(",") if a.strip()}
        if key == "":
            names = {text}
            base = ""
        out[key.strip()] = (base or prev_base, prev_adds | names)
    return out


def describe_component_delta(old, new):
    """Human-readable difference between two kitchen components (same station)."""
    parts = []
    if (old or {}).get("label") != (new or {}).get("label"):
        parts.append(f"{(old or {}).get('label', '-')} -> {(new or {}).get('label', '-')}")
    a, b = _parse_kitchen_lines((old or {}).get("lines")), _parse_kitchen_lines((new or {}).get("lines"))
    for key in list(dict.fromkeys(list(a) + list(b))):
        ob, oa = a.get(key, ("", set()))
        nb, na = b.get(key, ("", set()))
        bits = []
        if ob != nb:
            bits.append(f"{ob or 'none'} -> {nb or 'none'}")
        if oa - na:
            bits.append("remove " + ", ".join(sorted(oa - na)))
        if na - oa:
            bits.append("add " + ", ".join(sorted(na - oa)))
        if bits:
            parts.append((f"{key}: " if key else "") + "; ".join(bits))
    if (old or {}).get("notes", "") != (new or {}).get("notes", ""):
        parts.append(f"note: {(new or {}).get('notes') or 'removed'}")
    return ", ".join(parts) or "no kitchen change"


def check_place(check):
    return f"Table {check.table.label}" if check.table_id else f"#{check.token}"


def line_ref(check, line):
    """'Table 6, pizza 1' - ordinal among the check's lines of the same kind, by line number."""
    kind = (line.components[0].get("kind") if len(line.components) == 1 else "deal") if line.components else "item"
    noun = {"pizza": "pizza", "deal": "deal"}.get(kind, line.name.lower())
    same = [ln.pk for ln in check.lines.filter(status="active").order_by("line_no")
            if ((ln.components[0].get("kind") if len(ln.components) == 1 else "deal") if ln.components else "item") == kind
            and (kind in ("pizza", "deal") or ln.product_code == line.product_code)]
    n = same.index(line.pk) + 1 if line.pk in same else len(same) + 1
    return f"{check_place(check)}, {noun} {n}"


# ---------------------------------------------------------------- kitchen helpers

def _station_map(branch):
    from apps.core.models import Station
    stations = {s.code: s for s in Station.objects.filter(branch=branch)}
    fallback = next((s for s in stations.values() if s.is_packing), None) or next(iter(stations.values()))
    return stations, fallback


def _new_ticket(ctx, check, rnd, station, kind, note=""):
    from apps.kitchen.models import ProductionTicket
    seq = next_sequence(ctx.branch.pk, "ticket", check.business_date)
    return ProductionTicket.objects.create(
        branch=ctx.branch, station=station, check_ref=check, round=rnd, seq=seq, kind=kind, note=note[:300],
        due_at=timezone.now() + timedelta(minutes=10),
    )


def _print(ctx, tickets):
    from apps.kitchen.services import enqueue_ticket_print
    for t in tickets:
        enqueue_ticket_print(ctx.branch, t)


def _ensure_payable(check, delta):
    a = check_amounts(check)
    if a["total_minor"] + delta < a["paid_minor"]:
        raise DomainError("Payments already exceed the corrected total; refund first", code="refund_required",
                          extra={"paid_minor": a["paid_minor"], "new_total_minor": a["total_minor"] + delta})


def _bump(check, *fields):
    prev = check.revision
    check.revision += 1
    check.save(update_fields=["revision", "updated_at", *fields])
    return prev


def _reason(data):
    reason = str(data.get("reason", "")).strip()
    if not reason:
        raise DomainError("Give a reason", field_errors={"reason": "required"})
    return reason[:200]


def _lock_line(check, data):
    line = CheckLine.objects.select_for_update().filter(check_ref=check, pk=data.get("line_id")).first()
    if line is None:
        raise NotFound("Line not found on this check")
    if line.status != "active":
        raise Conflict("Line was already corrected", code="line_not_active", current_revision=check.revision)
    return line


def _result(check, adj, tickets, extra=None):
    a = check_amounts(check)
    return 200, {"check_id": str(check.pk), "revision": check.revision, "adjustment_id": str(adj.pk) if adj else None,
                 "description": adj.description if adj else "", "ticket_ids": [str(t.pk) for t in tickets],
                 "total_minor": a["total_minor"], "balance_minor": a["balance_minor"], **(extra or {})}


# ---------------------------------------------------------------- amendments

def amend(ctx, check_id, data):
    action = data.get("action")
    if action not in ("remove", "modify", "remake", "split"):
        raise DomainError("action must be remove, modify, remake or split", field_errors={"action": "invalid"})
    reason = _reason(data)
    check = lock_check(ctx.branch, check_id)
    if check.status != "open":
        raise Conflict("Check is not open", code="check_closed", current_revision=check.revision)
    validate_expected_revision(check, data)
    line = _lock_line(check, data)
    from apps.kitchen.models import TicketItem
    items = list(TicketItem.objects.select_for_update().filter(line=line).exclude(status="cancelled")
                 .exclude(remakes__isnull=False).select_related("ticket__station").order_by("component_index", "created_at"))
    return {"remove": _remove, "modify": _modify, "remake": _remake, "split": _split}[action](
        ctx, check, line, items, reason, data)


def _split(ctx, check, line, items, reason, data):
    """Split a quantity-N line into two lines (k and N-k) with the same spec, so one pizza of two can be changed,
    removed or paid separately (spec 01: editing one pizza in a quantity of two requires splitting first).
    Kitchen progress is kept on both halves; no kitchen ticket is printed because nothing changes physically."""
    from decimal import Decimal

    from apps.catalog.pricing import round_half_up
    from apps.kitchen.models import TicketItem
    from apps.stock.services import split_consumption
    try:
        k = int(data.get("quantity"))
    except (TypeError, ValueError):
        raise DomainError("quantity to split off is required", field_errors={"quantity": "required"})
    n = line.quantity
    if n < 2 or not 1 <= k < n:
        raise DomainError(f"Split off between 1 and {n - 1}", field_errors={"quantity": "out_of_range"})
    if line.allocations.filter(tender__status="succeeded").exists():
        raise DomainError("This line is already (part) paid; split the payment instead", code="line_paid")
    if check.discounts.filter(status="active", kind="line_adjustment", line=line).exists():
        raise DomainError("Remove the item discount before splitting", code="line_discounted")
    rate = Decimal(line.menu_release.tax_rate_bp) / Decimal(10000)
    last_no = check.lines.order_by("-line_no").values_list("line_no", flat=True).first() or 0
    new_lines = []
    for i, q in enumerate((n - k, k)):
        total = line.unit_minor * q
        new_lines.append(CheckLine.objects.create(
            check_ref=check, round=line.round, line_no=last_no + i + 1, client_line_id=f"{line.client_line_id}/{i + 1}",
            menu_release=line.menu_release, product_code=line.product_code, name=line.name, variant=line.variant,
            quantity=q, unit_minor=line.unit_minor, total_minor=total, tax_minor=round_half_up(Decimal(total) * rate),
            currency=line.currency, spec=line.spec, breakdown=line.breakdown, components=line.components,
            fire_state=line.fire_state, amends=line,
        ))
    keep, off = new_lines
    old_charge = line.charge_minor
    line.status = "voided"
    line.revision += 1
    line.save(update_fields=["status", "revision", "updated_at"])
    for it in items:
        twin = TicketItem.objects.create(
            ticket=it.ticket, line=off, component_index=it.component_index, label=it.label, detail=it.detail,
            notes=it.notes, slot_name=it.slot_name, quantity=k, status=it.status, started_at=it.started_at,
            ready_at=it.ready_at, change_note=it.change_note,
        )
        split_consumption(ctx.branch, it, twin, Decimal(k) / Decimal(n), ctx.staff)
        it.line, it.quantity = keep, n - k
        it.revision += 1
        it.save(update_fields=["line", "quantity", "revision", "updated_at"])
    recompute_if_discounted(check)
    keep.refresh_from_db()
    off.refresh_from_db()
    delta = keep.charge_minor + off.charge_minor - old_charge  # tax rounding only
    _ensure_payable(check, 0)
    ref = line_ref(check, line)
    adj = CheckAdjustment.objects.create(
        check_ref=check, kind="split", line=line, new_line=off, reason=reason, staff=ctx.staff,
        description=f"{ref}: split {n} x {line.name} into {n - k} + {k}", amount_delta_minor=delta,
        data={"lines": [str(keep.pk), str(off.pk)]},
    )
    prev = _bump(check)
    ctx.audit("check.amend.split", check, prev, check.revision, reason=reason,
              data={"line": str(line.pk), "lines": [str(keep.pk), str(off.pk)], "quantity": k})
    ctx.emit("check.line_split", check, {"check_id": check.pk, "line_id": line.pk, "lines": [keep.pk, off.pk]})
    return _result(check, adj, [], {"line_ids": [str(keep.pk), str(off.pk)], "new_line_id": str(off.pk)})


def _remove(ctx, check, line, items, reason, data):
    cooked = [i for i in items if i.status in COOKED]
    approver = resolve_approver(ctx.principal, data, "check.amend_cooked") if cooked else None
    _ensure_payable(check, -line.charge_minor)
    ref = line_ref(check, line)
    line.status = "voided"
    line.revision += 1
    line.save(update_fields=["status", "revision", "updated_at"])
    tickets = _cancel_items(ctx, check, line.round, items, f"{ref}: CANCEL {line.quantity} x {line.name}")
    recompute_if_discounted(check)
    adj = CheckAdjustment.objects.create(
        check_ref=check, kind="remove", line=line, reason=reason, staff=ctx.staff, approved_by=approver,
        description=f"{ref}: removed {line.quantity} x {line.name}", amount_delta_minor=-line.charge_minor,
        discarded=bool(cooked), data={"cancelled_items": [str(i.pk) for i in items if i.status == "cancelled"],
                                      "cooked_items": [str(i.pk) for i in cooked]},
    )
    prev = _bump(check)
    ctx.audit("check.amend.remove", check, prev, check.revision, reason=reason,
              approved_by=approver, data={"line": str(line.pk), "delta_minor": adj.amount_delta_minor})
    ctx.emit("check.line_removed", check, {"check_id": check.pk, "line_id": line.pk, "delta_minor": adj.amount_delta_minor,
                                           "discarded": adj.discarded})
    _print(ctx, tickets)
    return _result(check, adj, tickets)


def _cancel_items(ctx, check, rnd, items, note):
    """Cancel live production items and tell each station on a change ticket. Cooked items are left as-is
    (physically discarded) but their station is still told not to serve them."""
    by_station = {}
    for it in items:
        by_station.setdefault(it.ticket.station_id, (it.ticket.station, []))[1].append(it)
    from apps.kitchen.models import TicketItem
    tickets = []
    for _, (station, its) in sorted(by_station.items(), key=lambda kv: kv[1][0].code):
        t = _new_ticket(ctx, check, rnd, station, "change", note)
        for it in its:
            was = it.status
            if was in LIVE:
                it.status = "cancelled"
                it.revision += 1
                it.save(update_fields=["status", "revision", "updated_at"])
                if was == "queued":  # never started: compensate its consumption; cooked food stays consumed
                    reverse_item(ctx.branch, it, note, ctx.staff)
            TicketItem.objects.create(
                ticket=t, line=it.line, component_index=it.component_index, label=it.label, detail=it.detail,
                notes=it.notes, slot_name=it.slot_name, quantity=it.quantity, status="cancelled",
                change_note=note if was in LIVE else f"{note} (already {was}: do not serve)",
            )
        tickets.append(t)
    return tickets


def _modify(ctx, check, line, items, reason, data):
    item = data.get("item")
    if not isinstance(item, dict):
        raise DomainError("item (the corrected line) is required", field_errors={"item": "required"})
    if any(i.status in COOKED for i in items):
        raise DomainError("Already cooked. Record a remake or remove the line instead.", code="already_cooked")
    quote = Quote.objects.filter(branch=ctx.branch, pk=data.get("quote_id")).first()
    if quote is None:
        raise DomainError("Quote not found; request a new quote", code="quote_missing", field_errors={"quote_id": "unknown"})
    if quote.expires_at <= timezone.now():
        raise DomainError("Quote expired; refresh prices", code="quote_expired", field_errors={"quote_id": "expired"})
    from apps.core.commands import canonical_hash
    if quote.items_hash != canonical_hash("quote", _items_for_hash([item])):
        raise DomainError("Item differs from the quote", code="quote_mismatch", field_errors={"item": "mismatch"})
    if ctx.branch.active_release_id != quote.release_id:
        raise DomainError("Menu changed since the quote; refresh prices", code="menu_changed")
    lines, totals = pricer_for(ctx.branch).price(_items_for_hash([item]))
    if totals["total_minor"] != quote.totals["total_minor"]:
        raise DomainError("Price changed since the quote", code="quote_stale")
    priced, per = lines[0], totals["per_line"][lines[0].client_line_id]
    new_charge = priced.total_minor - per["discount_minor"] + per["tax_minor"]
    delta = new_charge - line.charge_minor
    _ensure_payable(check, delta)
    ref = line_ref(check, line)

    last_no = check.lines.order_by("-line_no").values_list("line_no", flat=True).first() or 0
    new = CheckLine.objects.create(
        check_ref=check, round=line.round, line_no=last_no + 1, client_line_id=priced.client_line_id,
        menu_release=quote.release, product_code=priced.product, name=priced.name, variant=priced.variant,
        quantity=priced.quantity, unit_minor=priced.unit_minor, total_minor=priced.total_minor,
        discount_minor=per["discount_minor"], tax_minor=per["tax_minor"], currency=ctx.branch.currency,
        spec=priced.spec, breakdown=priced.breakdown, components=priced.components, fire_state=line.fire_state,
        amends=line,
    )
    line.status = "voided"
    line.revision += 1
    line.save(update_fields=["status", "revision", "updated_at"])

    descriptions, tickets = [], []
    if line.fire_state == "fired":
        tickets, descriptions = _reroute(ctx, check, line, new, items, ref)
    if line.quantity != new.quantity:
        descriptions.insert(0, f"{ref}: quantity {line.quantity} -> {new.quantity}")
    if not descriptions:
        descriptions = [f"{ref}: " + describe_component_delta(
            line.components[0] if line.components else None, new.components[0] if new.components else None)]
    description = " | ".join(descriptions)[:300]
    recompute_if_discounted(check)
    adj = CheckAdjustment.objects.create(
        check_ref=check, kind="modify", line=line, new_line=new, reason=reason, staff=ctx.staff,
        description=description, amount_delta_minor=delta,
    )
    prev = _bump(check)
    ctx.audit("check.amend.modify", check, prev, check.revision, reason=reason,
              data={"line": str(line.pk), "new_line": str(new.pk), "delta_minor": delta, "description": description})
    ctx.emit("check.line_modified", check, {"check_id": check.pk, "line_id": line.pk, "new_line_id": new.pk,
                                            "delta_minor": delta})
    _print(ctx, tickets)
    return _result(check, adj, tickets, {"new_line_id": str(new.pk)})


def _reroute(ctx, check, old, new, items, ref):
    """Replace old production items with the corrected ones, keeping progress at the same station (a pizza
    already in preparation stays in preparation). One change ticket per affected station."""
    from apps.kitchen.models import TicketItem
    stations, fallback = _station_map(ctx.branch)
    old_by_idx = {i.component_index: i for i in items}
    plan = {}  # station code -> list of (old_item | None, new_idx | None)
    for idx, comp in enumerate(new.components):
        st = stations.get(comp.get("station")) or fallback
        prior = old_by_idx.pop(idx, None)
        if prior is not None and prior.ticket.station_id != st.pk:
            plan.setdefault(prior.ticket.station.code, (prior.ticket.station, []))[1].append((prior, None))
            prior = None
        plan.setdefault(st.code, (st, []))[1].append((prior, idx))
    for prior in old_by_idx.values():
        plan.setdefault(prior.ticket.station.code, (prior.ticket.station, []))[1].append((prior, None))

    tickets, descriptions = [], []
    for code in sorted(plan):
        st, entries = plan[code]
        notes = []
        for prior, idx in entries:
            if idx is None:
                notes.append(f"{ref}: CANCEL {prior.label}")
            else:
                old_comp = old.components[prior.component_index] if prior is not None else None
                d = describe_component_delta(old_comp, new.components[idx]) if old_comp else f"ADD {new.components[idx]['label']}"
                if old.quantity != new.quantity:
                    d = f"qty {old.quantity} -> {new.quantity}" + ("" if d == "no kitchen change" else f", {d}")
                notes.append(f"{ref}, {d}")
        note = " | ".join(notes)
        t = _new_ticket(ctx, check, old.round, st, "change", note)
        for (prior, idx), n in zip(entries, notes):
            status = "queued"
            started = None
            if prior is not None:
                status = prior.status if prior.status in ("queued", "preparing") else "queued"
                started = prior.started_at
                prior.status = "cancelled"
                prior.revision += 1
                prior.save(update_fields=["status", "revision", "updated_at"])
                reverse_item(ctx.branch, prior, f"replaced: {n}", ctx.staff)
            if idx is None:
                TicketItem.objects.create(ticket=t, line=old, component_index=prior.component_index, label=prior.label,
                                          detail=prior.detail, quantity=prior.quantity, status="cancelled", change_note=n)
            else:
                comp = new.components[idx]
                ti = TicketItem.objects.create(
                    ticket=t, line=new, component_index=idx, label=comp["label"], detail=comp.get("lines", []),
                    notes=comp.get("notes", ""), slot_name=comp.get("slot_name", ""), quantity=new.quantity,
                    status=status, started_at=started, change_note=n,
                )
                consume_item(ctx.branch, ti, ctx.staff)
        tickets.append(t)
        descriptions += notes
    return tickets, descriptions


def _remake(ctx, check, line, items, reason, data):
    started = [i for i in items if i.status in COOKED or i.status == "preparing"]
    if not started:
        raise DomainError("Nothing has been cooked yet; change or remove the line instead", code="not_cooked")
    approver = resolve_approver(ctx.principal, data, "production.remake")
    from apps.kitchen.models import TicketItem
    ref = line_ref(check, line)
    note = f"{ref}: REMAKE {line.quantity} x {line.name} ({reason})"
    by_station = {}
    for it in started:
        by_station.setdefault(it.ticket.station.code, (it.ticket.station, []))[1].append(it)
    tickets = []
    for code in sorted(by_station):
        st, its = by_station[code]
        t = _new_ticket(ctx, check, line.round, st, "remake", note)
        for it in its:
            ti = TicketItem.objects.create(
                ticket=t, line=line, component_index=it.component_index, label=it.label, detail=it.detail,
                notes=it.notes, slot_name=it.slot_name, quantity=it.quantity, status="queued", remake_of=it,
                change_note=note,
            )
            consume_item(ctx.branch, ti, ctx.staff)  # a remake consumes its own ingredients
        tickets.append(t)
    # Financial effect is zero: a kitchen remake alone never changes what the guest pays.
    adj = CheckAdjustment.objects.create(
        check_ref=check, kind="remake", line=line, reason=reason, staff=ctx.staff, approved_by=approver,
        description=note, amount_delta_minor=0, discarded=True,
        data={"discarded_items": [str(i.pk) for i in started]},
    )
    prev = _bump(check)
    ctx.audit("check.remake", check, prev, check.revision, reason=reason, approved_by=approver,
              data={"line": str(line.pk), "items": [str(i.pk) for i in started]})
    ctx.emit("production.remake", check, {"check_id": check.pk, "line_id": line.pk, "items": len(started)})
    _print(ctx, tickets)
    return _result(check, adj, tickets)


# ---------------------------------------------------------------- void / move / merge

def void_check(ctx, check_id, data):
    reason = _reason(data)
    check = lock_check(ctx.branch, check_id)
    if check.status != "open":
        raise Conflict("Check is not open", code="check_closed", current_revision=check.revision)
    validate_expected_revision(check, data)
    approver = resolve_approver(ctx.principal, data, "check.void")
    a = check_amounts(check)
    if a["net_paid_minor"] != 0 or a["pending_refund_minor"]:
        raise DomainError("This check has payments. Refund them in full before voiding.", code="refund_required",
                          extra={"net_paid_minor": a["net_paid_minor"]})
    from apps.kitchen.models import TicketItem
    items = list(TicketItem.objects.select_for_update().filter(line__check_ref=check).exclude(status="cancelled")
                 .select_related("ticket__station", "line").order_by("created_at"))
    cooked = [i for i in items if i.status in COOKED or i.status == "preparing"]
    resolution = data.get("food_resolution")
    if cooked and resolution not in ("discard", "staff_meal", "keep"):
        raise DomainError("Food was already prepared. Say what happens to it.", code="food_resolution_required",
                          field_errors={"food_resolution": "required"},
                          extra={"prepared_items": [f"{i.quantity} x {i.label}" for i in cooked]})
    total = check_amounts(check)["total_minor"]
    rnd = check.rounds.order_by("-number").first()
    note = f"{check_place(check)}: VOID CHECK #{check.token} ({reason})"
    tickets = _cancel_items(ctx, check, rnd, items, note) if items and rnd else []
    check.lines.filter(status="active").update(status="voided", revision=F("revision") + 1)
    check.status = "voided"
    check.void_reason = reason
    check.closed_at = timezone.now()
    check.closed_by = ctx.staff
    adj = CheckAdjustment.objects.create(
        check_ref=check, kind="void", reason=reason, staff=ctx.staff, approved_by=approver,
        description=note, amount_delta_minor=-total, discarded=bool(cooked) and resolution == "discard",
        data={"food_resolution": resolution, "prepared_items": [str(i.pk) for i in cooked]},
    )
    prev = _bump(check, "status", "void_reason", "closed_at", "closed_by")
    ctx.audit("check.void", check, prev, check.revision, reason=reason, approved_by=approver,
              data={"total_minor": total, "food_resolution": resolution})
    ctx.emit("check.voided", check, {"check_id": check.pk, "total_minor": total, "food_resolution": resolution,
                                     "business_date": check.business_date})
    _print(ctx, tickets)
    return _result(check, adj, tickets)


def _notify_stations(ctx, check, note):
    """Tell every station still working on this check about a table change (KDS shows it until acknowledged)."""
    from apps.kitchen.models import TicketItem
    live = (TicketItem.objects.filter(line__check_ref=check, status__in=["queued", "preparing", "ready"])
            .select_related("ticket__station"))
    stations = {i.ticket.station.code: i.ticket.station for i in live}
    rnd = check.rounds.order_by("-number").first()
    tickets = [_new_ticket(ctx, check, rnd, stations[c], "change", note) for c in sorted(stations)] if rnd else []
    _print(ctx, tickets)
    return tickets


def move_check(ctx, check_id, data):
    check = lock_check(ctx.branch, check_id)
    if check.status != "open":
        raise Conflict("Check is not open", code="check_closed", current_revision=check.revision)
    validate_expected_revision(check, data)
    if check.service_type != "dine_in":
        raise DomainError("Only dine-in checks sit at a table", code="not_dine_in")
    table = DiningTable.objects.select_for_update().filter(branch=ctx.branch, pk=data.get("table_id"), is_active=True).first()
    if table is None:
        raise NotFound("Table not found")
    if table.pk == check.table_id:
        raise DomainError("Check is already at that table", field_errors={"table_id": "same_table"})
    existing = Check.objects.filter(table=table, status="open").first()
    if existing:
        raise Conflict(f"Table {table.label} is occupied", code="table_occupied",
                       extra={"check_id": str(existing.pk), "token": existing.token})
    old_label = check.table.label
    check.table = table
    note = f"MOVED #{check.token}: Table {old_label} -> Table {table.label}"
    adj = CheckAdjustment.objects.create(check_ref=check, kind="move", reason=str(data.get("reason", ""))[:200] or "move",
                                         staff=ctx.staff, description=note,
                                         data={"from": old_label, "to": table.label})
    prev = _bump(check, "table")
    tickets = _notify_stations(ctx, check, note)
    ctx.audit("check.move", check, prev, check.revision, data={"from": old_label, "to": table.label})
    ctx.emit("check.moved", check, {"check_id": check.pk, "from": old_label, "to": table.label})
    return _result(check, adj, tickets, {"table": table.label})


def merge_checks(ctx, target_id, data):
    """Merge source check into the target (this URL's check). Pilot: only before any payment, manager permission."""
    source_id = data.get("source_check_id")
    if not source_id or str(source_id) == str(target_id):
        raise DomainError("Choose another check to merge", field_errors={"source_check_id": "invalid"})
    reason = _reason(data)
    # Lock both checks in a stable order so concurrent merges cannot deadlock.
    locked = {}
    for cid in sorted([str(target_id), str(source_id)]):
        locked[cid] = lock_check(ctx.branch, cid)
    target, source = locked[str(target_id)], locked[str(source_id)]
    for c in (target, source):
        if c.status != "open":
            raise Conflict(f"Check #{c.token} is not open", code="check_closed", current_revision=c.revision)
    validate_expected_revision(target, data)
    src_expected = data.get("source_expected_revision")
    if src_expected is None or int(src_expected) != source.revision:
        from apps.core.errors import StaleRevision
        raise StaleRevision(f"Check #{source.token} changed. Review it again.", current_revision=source.revision)
    approver = resolve_approver(ctx.principal, data, "check.merge")
    if target.tenders.exists() or source.tenders.exists():
        raise DomainError("Merging is only allowed before any payment", code="merge_after_payment")

    from apps.kitchen.models import ProductionTicket
    last_no = target.lines.order_by("-line_no").values_list("line_no", flat=True).first() or 0
    moved_lines = list(source.lines.order_by("line_no"))
    for i, ln in enumerate(moved_lines, start=1):
        CheckLine.objects.filter(pk=ln.pk).update(check_ref=target, line_no=last_no + i, revision=ln.revision + 1)
    round_map = {}
    for i, r in enumerate(source.rounds.order_by("number"), start=1):
        new_no = target.round_count + i
        round_map[r.number] = new_no
        CheckRound.objects.filter(pk=r.pk).update(check_ref=target, number=new_no, revision=r.revision + 1)
    ProductionTicket.objects.filter(check_ref=source).update(check_ref=target)
    from apps.kitchen.models import PrintJob
    PrintJob.objects.filter(check_ref=source).update(check_ref=target)

    src_place = check_place(source)
    note = f"MERGED #{source.token} ({src_place}) into #{target.token} ({check_place(target)})"
    target.round_count += len(round_map)
    target.guest_count = min(99, target.guest_count + source.guest_count)
    source.status = "merged"
    source.merged_into = target
    source.closed_at = timezone.now()
    source.closed_by = ctx.staff
    adj = CheckAdjustment.objects.create(
        check_ref=target, kind="merge", reason=reason, staff=ctx.staff, approved_by=approver, description=note,
        data={"source_check": str(source.pk), "source_token": source.token, "source_table": src_place,
              "lines": [str(ln.pk) for ln in moved_lines], "rounds": round_map},
    )
    CheckAdjustment.objects.create(check_ref=source, kind="merge", reason=reason, staff=ctx.staff,
                                   approved_by=approver, description=note, data={"target_check": str(target.pk)})
    # Line adjustments follow their lines; a source promotion lapses (one order promotion per check).
    CheckDiscount.objects.filter(check_ref=source, status="active", kind="line_adjustment").update(check_ref=target)
    CheckDiscount.objects.filter(check_ref=source, status="active", kind="promotion").update(status="removed")
    sprev = _bump(source, "status", "merged_into", "closed_at", "closed_by")
    recompute_if_discounted(target)
    prev = _bump(target, "round_count", "guest_count")
    tickets = _notify_stations(ctx, target, note)
    ctx.audit("check.merge", target, prev, target.revision, reason=reason, approved_by=approver,
              data={"source": str(source.pk)})
    ctx.audit("check.merged_away", source, sprev, source.revision, reason=reason, approved_by=approver,
              data={"target": str(target.pk)})
    ctx.emit("check.merged", target, {"check_id": target.pk, "source_check_id": source.pk,
                                      "lines": [ln.pk for ln in moved_lines]})
    return _result(target, adj, tickets, {"source_check_id": str(source.pk)})
