from datetime import timedelta

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from apps.catalog.models import BranchAvailability
from apps.catalog.pricing import Menu, Pricer
from apps.core.commands import canonical_hash, next_sequence
from apps.core.errors import Conflict, DomainError, NotFound, StaleRevision

from .models import Check, CheckLine, CheckRound, DiningTable, Quote

_MENU_CACHE: dict = {}


def menu_for(release) -> Menu:
    """Published releases are immutable, so their loaded form can be cached by id."""
    key = (release.pk, release.revision)
    if key not in _MENU_CACHE:
        _MENU_CACHE[key] = Menu.load(release)
    return _MENU_CACHE[key]


def pricer_for(branch) -> Pricer:
    if branch.active_release is None:
        raise DomainError("No menu release active at this branch", code="no_menu", http_status=503)
    return Pricer(menu_for(branch.active_release), BranchAvailability.unavailable_codes(branch))


# ---------------------------------------------------------------- reads

def check_amounts(check):
    lines = [ln for ln in check.lines.all() if ln.status == "active"]
    charges = sum(ln.charge_minor for ln in lines)
    paid = check.tenders.filter(status="succeeded").aggregate(s=Sum("amount_minor"))["s"] or 0
    refunded = check.refunds.filter(status="succeeded").aggregate(s=Sum("amount_minor"))["s"] or 0
    pending_refunds = check.refunds.filter(status="pending").aggregate(s=Sum("amount_minor"))["s"] or 0
    return {
        # A refund is a credit paired with money back: the bill does not reopen (balance unchanged).
        "refunded_minor": refunded,
        "pending_refund_minor": pending_refunds,
        "net_paid_minor": paid - refunded,
        "subtotal_minor": sum(ln.total_minor for ln in lines),
        "discount_minor": sum(ln.discount_minor for ln in lines),
        "tax_minor": sum(ln.tax_minor for ln in lines),
        "total_minor": charges,
        "paid_minor": paid,
        "balance_minor": charges - paid,
    }


def table_label(check):
    return check.table.label if check.table_id else None


def serialize_check(check, detail=True):
    data = {
        "id": str(check.pk), "revision": check.revision, "status": check.status,
        "service_type": check.service_type, "token": check.token,
        "table": {"id": str(check.table.pk), "label": check.table.label} if check.table_id else None,
        "guest_count": check.guest_count, "business_date": check.business_date,
        "customer_name": check.customer_name, "customer_phone": check.customer_phone,
        "opened_by": check.opened_by.display_name, "opened_at": check.opened_at,
        "closed_at": check.closed_at, "receipt_no": check.receipt_no, "currency": check.branch.currency,
        "channel": check.channel, "promised_at": check.promised_at, "handed_over_at": check.handed_over_at,
        "customer_id": str(check.customer_id) if check.customer_id else None, "notes": check.notes,
        **check_amounts(check),
    }
    if not detail:
        return data
    rounds = []
    tickets_by_round = {}
    for t in check.tickets.select_related("station").all():
        tickets_by_round.setdefault(t.round_id, []).append(t)
    for r in check.rounds.select_related("added_by").order_by("number"):
        rounds.append({
            "id": str(r.pk), "number": r.number, "added_by": r.added_by.display_name, "created_at": r.created_at,
            "tickets": [{"id": str(t.pk), "seq": t.seq, "station": t.station.code, "kind": t.kind}
                        for t in tickets_by_round.get(r.pk, [])],
            "lines": [serialize_line(ln) for ln in r.lines.all()],
        })
    data["rounds"] = rounds
    from apps.cash.refunds import refundable, serialize_refund
    data["tenders"] = [
        {"id": str(t.pk), "method": t.method, "status": t.status, "amount_minor": t.amount_minor,
         "cash_received_minor": t.cash_received_minor, "change_minor": t.change_minor,
         "external_ref": t.external_ref, "created_at": t.created_at,
         "refundable_minor": refundable(t) if t.status == "succeeded" else 0,
         "lines": [{"line_id": str(a.line_id), "amount_minor": a.amount_minor} for a in t.allocations.all()]}
        for t in check.tenders.order_by("created_at")
    ]
    data["refunds"] = [serialize_refund(r) for r in
                       check.refunds.select_related("staff", "approved_by").prefetch_related("allocations")
                       .order_by("created_at")]
    data["adjustments"] = [
        {"id": str(a.pk), "kind": a.kind, "description": a.description, "reason": a.reason,
         "amount_delta_minor": a.amount_delta_minor, "discarded": a.discarded, "staff": a.staff.display_name,
         "approved_by": a.approved_by.display_name if a.approved_by_id else None, "created_at": a.created_at,
         "line_id": str(a.line_id) if a.line_id else None, "new_line_id": str(a.new_line_id) if a.new_line_id else None}
        for a in check.adjustments.select_related("staff", "approved_by").order_by("created_at")
    ]
    from .discounts import serialize_discount
    data["discounts"] = [serialize_discount(d) for d in
                         check.discounts.select_related("promotion", "staff", "approved_by").order_by("created_at")]
    d = check.deliveries.select_related("zone", "rider").order_by("-attempt").first()
    if d is not None:
        from apps.fulfillment.services import serialize_delivery
        data["delivery"] = serialize_delivery(d)
    from apps.kitchen.services import serialize_job
    data["print_jobs"] = [serialize_job(j) for j in check.printjob_set.select_related(
        "printer", "ticket__station", "check_ref", "requested_by").order_by("created_at")]
    data["void_reason"] = check.void_reason
    data["merged_into"] = str(check.merged_into_id) if check.merged_into_id else None
    data["server_time"] = timezone.now()
    return data


def serialize_line(ln):
    statuses = sorted({ti.status for ti in ln.ticket_items.all()})
    return {
        "id": str(ln.pk), "line_no": ln.line_no, "client_line_id": ln.client_line_id, "product": ln.product_code,
        "name": ln.name, "variant": ln.variant, "quantity": ln.quantity, "unit_minor": ln.unit_minor,
        "total_minor": ln.total_minor, "tax_minor": ln.tax_minor, "discount_minor": ln.discount_minor,
        "spec": ln.spec, "breakdown": ln.breakdown, "components": ln.components,
        "fire_state": ln.fire_state, "status": ln.status, "production_status": statuses,
        "amends_id": str(ln.amends_id) if ln.amends_id else None, "charge_minor": ln.charge_minor,
    }


def floor_snapshot(branch):
    open_checks = {c.table_id: c for c in Check.objects.filter(branch=branch, status="open", table__isnull=False)
                   .select_related("opened_by")}
    tables = DiningTable.objects.filter(branch=branch, is_active=True).select_related("area").order_by("area__sort", "sort", "label")
    areas = {}
    for t in tables:
        a = areas.setdefault(t.area_id, {"id": str(t.area.pk), "code": t.area.code, "name": t.area.name, "tables": []})
        c = open_checks.get(t.pk)
        entry = {"id": str(t.pk), "label": t.label, "seats": t.seats, "occupied": c is not None, "check": None}
        if c:
            amounts = check_amounts(c)
            entry["check"] = {"id": str(c.pk), "token": c.token, "revision": c.revision, "guest_count": c.guest_count,
                              "waiter": c.opened_by.display_name, "opened_at": c.opened_at,
                              "total_minor": amounts["total_minor"], "balance_minor": amounts["balance_minor"],
                              "bill_requested": c.bill_requested_at is not None}
        a["tables"].append(entry)
    return {"areas": list(areas.values()), "server_time": timezone.now()}


# ---------------------------------------------------------------- commands

def lock_check(branch, check_id) -> Check:
    check = Check.objects.select_for_update().filter(branch=branch, pk=check_id).first()
    if check is None:
        raise NotFound("Check not found")
    return check


def open_check(ctx, data):
    branch = ctx.branch
    service_type = data.get("service_type", "dine_in")
    if service_type not in dict(Check.SERVICE):
        raise DomainError("Unknown service type", field_errors={"service_type": "invalid"})
    table = None
    if service_type == "dine_in":
        table_id = data.get("table_id")
        if not table_id:
            raise DomainError("Choose a table", field_errors={"table_id": "required"})
        table = DiningTable.objects.select_for_update().filter(branch=branch, pk=table_id, is_active=True).first()
        if table is None:
            raise NotFound("Table not found")
        existing = Check.objects.filter(table=table, status="open").first()
        if existing:
            raise Conflict(f"Table {table.label} already has an open check", code="table_occupied",
                           extra={"check_id": str(existing.pk), "token": existing.token})
    guest_count = int(data.get("guest_count") or 1)
    if guest_count < 1 or guest_count > 99:
        raise DomainError("Guest count must be 1-99", field_errors={"guest_count": "invalid"})
    bdate = branch.business_date()
    prefix = {"dine_in": "D", "counter": "C", "pickup": "P", "delivery": "R"}[service_type]
    seq = next_sequence(branch.pk, "token", bdate)
    check = Check.objects.create(
        branch=branch, service_type=service_type, table=table, business_date=bdate,
        token=f"{prefix}{seq:03d}", guest_count=guest_count, opened_by=ctx.staff,
        customer_name=str(data.get("customer_name", ""))[:80], customer_phone=str(data.get("customer_phone", ""))[:30],
        channel=ctx.channel,
    )
    if service_type != "dine_in":
        from apps.fulfillment.services import prepare_takeaway
        prepare_takeaway(ctx, check, data)
    ctx.audit("check.open", check, None, check.revision, data={"service_type": service_type,
                                                                  "table": table.label if table else None})
    ctx.emit("check.opened", check, {"check_id": check.pk, "token": check.token, "service_type": service_type,
                                     "business_date": bdate})
    return {"check_id": str(check.pk), "token": check.token, "revision": check.revision,
            "table": table.label if table else None, "business_date": bdate, "promised_at": check.promised_at,
            "customer_id": str(check.customer_id) if check.customer_id else None}


def _items_for_hash(items):
    return [{k: v for k, v in it.items() if k != "fire"} for it in items]


def create_quote(principal, data):
    branch = principal.branch
    pricer = pricer_for(branch)
    items = data.get("items") or []
    if not isinstance(items, list):
        raise DomainError("items must be a list", field_errors={"items": "invalid"})
    lines, totals = pricer.price(_items_for_hash(items))
    if data.get("preview"):
        # Builder preview: priced by the server, nothing persisted, cannot be used to commit.
        return {"quote_id": None, "menu_release": str(branch.active_release_id), "currency": branch.currency,
                "lines": [ln.as_dict() for ln in lines], **{k: v for k, v in totals.items() if k != "per_line"}}
    q = Quote.objects.create(
        branch=branch, release=branch.active_release, service_type=data.get("service_type", "dine_in"),
        items_hash=canonical_hash("quote", _items_for_hash(items)), items=_items_for_hash(items),
        lines=[ln.as_dict() for ln in lines], totals=totals,
        expires_at=timezone.now() + timedelta(seconds=settings.QUOTE_TTL_SECONDS), created_by=principal.staff,
    )
    return {
        "quote_id": str(q.pk), "menu_release": str(q.release_id), "menu_version": q.release.version,
        "currency": branch.currency, "expires_at": q.expires_at, "lines": q.lines,
        **{k: v for k, v in totals.items() if k != "per_line"},
    }


def append_round(ctx, check_id, data):
    from apps.kitchen.services import dispatch_lines

    branch = ctx.branch
    check = lock_check(branch, check_id)
    if check.status != "open":
        raise Conflict("Check is closed; start a new check", code="check_closed", current_revision=check.revision)
    items = data.get("items") or []
    quote = Quote.objects.filter(branch=branch, pk=data.get("quote_id")).select_related("release").first()
    if quote is None:
        raise DomainError("Quote not found; request a new quote", code="quote_missing", field_errors={"quote_id": "unknown"})
    if quote.expires_at <= timezone.now():
        raise DomainError("Quote expired; refresh prices", code="quote_expired", field_errors={"quote_id": "expired"})
    if quote.items_hash != canonical_hash("quote", _items_for_hash(items)):
        raise DomainError("Items differ from the quote", code="quote_mismatch", field_errors={"items": "mismatch"})
    if branch.active_release_id != quote.release_id or str(data.get("menu_release", quote.release_id)) != str(quote.release_id):
        raise DomainError("Menu changed since the quote; refresh prices", code="menu_changed")
    # Revalidate price and sellouts under the check lock.
    lines, totals = pricer_for(branch).price(_items_for_hash(items))
    if totals["total_minor"] != quote.totals["total_minor"]:
        raise DomainError("Price changed since the quote", code="quote_stale",
                          extra={"new_total_minor": totals["total_minor"]})

    number = check.round_count + 1
    rnd = CheckRound.objects.create(check_ref=check, number=number, added_by=ctx.staff, device=ctx.device,
                                    command_key=ctx.key, quote=quote)
    last_no = check.lines.order_by("-line_no").values_list("line_no", flat=True).first() or 0
    created, fired = [], []
    for i, (spec, priced) in enumerate(zip(items, lines)):
        fire = spec.get("fire", "now")
        if fire not in ("now", "hold"):
            raise DomainError("fire must be now or hold", field_errors={f"items[{i}].fire": "invalid"})
        per = totals["per_line"][priced.client_line_id]
        ln = CheckLine.objects.create(
            check_ref=check, round=rnd, line_no=last_no + i + 1, client_line_id=priced.client_line_id,
            menu_release=quote.release, product_code=priced.product, name=priced.name, variant=priced.variant,
            quantity=priced.quantity, unit_minor=priced.unit_minor, total_minor=priced.total_minor,
            discount_minor=per["discount_minor"], tax_minor=per["tax_minor"], currency=branch.currency,
            spec=priced.spec, breakdown=priced.breakdown, components=priced.components,
            fire_state="held" if fire == "hold" else "fired",
        )
        created.append(ln)
        if fire == "now":
            fired.append(ln)
    tickets = dispatch_lines(ctx, check, rnd, fired, kind="original") if fired else []
    from .discounts import recompute_if_discounted
    recompute_if_discounted(check)
    for ln in created:
        ln.refresh_from_db(fields=["discount_minor", "tax_minor"])
    prev = check.revision
    check.revision += 1
    check.round_count = number
    check.save(update_fields=["revision", "round_count", "updated_at"])
    round_total = sum(ln.charge_minor for ln in created)
    ctx.audit("check.append_round", check, prev, check.revision,
              data={"round": number, "lines": [str(ln.pk) for ln in created], "total_minor": round_total})
    ctx.emit("check.round_appended", check, {
        "check_id": check.pk, "round": number, "total_minor": round_total,
        "lines": [{"id": ln.pk, "product": ln.product_code, "qty": ln.quantity, "total_minor": ln.total_minor}
                  for ln in created],
    })
    return {
        "check_id": str(check.pk), "revision": check.revision, "round": number,
        "accepted_lines": [str(ln.pk) for ln in created],
        "line_ids": {ln.client_line_id: str(ln.pk) for ln in created},
        "ticket_ids": [str(t.pk) for t in tickets], "round_total_minor": round_total,
        "currency": branch.currency, "production_status": "queued" if fired else "held",
    }


def fire_held(ctx, check_id, data):
    from apps.kitchen.services import dispatch_lines

    check = lock_check(ctx.branch, check_id)
    if check.status != "open":
        raise Conflict("Check is closed", code="check_closed", current_revision=check.revision)
    ids = data.get("line_ids") or []
    lines = list(check.lines.select_for_update().filter(pk__in=ids, fire_state="held", status="active"))
    if not lines or len(lines) != len(set(ids)):
        raise DomainError("Choose held lines to fire", field_errors={"line_ids": "not_held"})
    by_round = {}
    for ln in lines:
        ln.fire_state = "fired"
        ln.revision += 1
        ln.save(update_fields=["fire_state", "revision", "updated_at"])
        by_round.setdefault(ln.round_id, []).append(ln)
    tickets = []
    for round_id, lns in by_round.items():
        tickets += dispatch_lines(ctx, check, lns[0].round, lns, kind="fire")
    prev = check.revision
    check.revision += 1
    check.save(update_fields=["revision", "updated_at"])
    ctx.audit("check.fire_held", check, prev, check.revision, data={"lines": [str(ln.pk) for ln in lines]})
    ctx.emit("check.lines_fired", check, {"check_id": check.pk, "lines": [ln.pk for ln in lines]})
    return 200, {"check_id": str(check.pk), "revision": check.revision, "ticket_ids": [str(t.pk) for t in tickets]}


def request_bill(ctx, check_id, data):
    from apps.kitchen.services import enqueue_bill

    check = lock_check(ctx.branch, check_id)
    if check.status != "open":
        raise Conflict("Check is closed", code="check_closed", current_revision=check.revision)
    check.bill_requested_at = timezone.now()
    prev = check.revision
    check.revision += 1
    check.save(update_fields=["bill_requested_at", "revision", "updated_at"])
    job = enqueue_bill(ctx, check)
    ctx.audit("check.request_bill", check, prev, check.revision)
    return 200, {"check_id": str(check.pk), "revision": check.revision, "print_job_id": str(job.pk) if job else None}


def validate_expected_revision(check, data):
    expected = data.get("expected_revision")
    if expected is None:
        raise DomainError("expected_revision is required", field_errors={"expected_revision": "required"})
    if int(expected) != check.revision:
        raise StaleRevision("The check changed. Review the new total.", current_revision=check.revision)
