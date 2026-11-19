"""Phone customers, repeat orders, packing handover, deliveries and the rider cash ledger."""
import re
from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone

from apps.core.errors import Conflict, DomainError, NotFound, StaleRevision
from apps.core.models import Membership, Staff
from apps.core.scope import resolve_approver

from .models import (Address, Customer, Delivery, DeliveryZone, RemittanceAllocation, RiderCollection,
                     RiderRemittance)

TAKEAWAY = ("counter", "pickup", "delivery")
DONE = ("ready", "packed", "served")


def normalize_phone(raw):
    digits = re.sub(r"\D", "", str(raw or ""))
    if digits.startswith("92") and len(digits) == 12:
        digits = "0" + digits[2:]
    return digits[:20]


# ---------------------------------------------------------------- customers

def upsert_customer(branch, phone, name=""):
    phone = normalize_phone(phone)
    if len(phone) < 7:
        return None
    c = Customer.objects.select_for_update().filter(tenant=branch.tenant, phone=phone).first()
    if c is None:
        c = Customer.objects.create(tenant=branch.tenant, phone=phone, name=str(name)[:80])
    elif name and name != c.name:
        c.name = str(name)[:80]
        c.revision += 1
        c.save(update_fields=["name", "revision", "updated_at"])
    return c


def serialize_customer(c, branch, with_addresses=True):
    data = {"id": str(c.pk), "phone_masked": c.masked_phone, "name": c.name, "last_order_at": c.last_order_at,
            "orders": c.checks.filter(branch__tenant=branch.tenant).exclude(status__in=["voided", "merged"]).count()}
    if with_addresses:
        data["addresses"] = [{"id": str(a.pk), "text": a.text, "landmark": a.landmark,
                              "zone": {"id": str(a.zone_id), "name": a.zone.name} if a.zone_id else None}
                             for a in c.addresses.select_related("zone").order_by("-last_used_at", "-created_at")[:5]]
    return data


def lookup_customers(branch, q):
    digits = normalize_phone(q)
    if len(digits) < 4:
        raise DomainError("Enter at least 4 digits of the phone number", field_errors={"q": "too_short"})
    qs = Customer.objects.filter(tenant=branch.tenant).filter(phone__contains=digits).order_by("-last_order_at")[:10]
    return {"customers": [serialize_customer(c, branch) for c in qs]}


def repeat_order(branch, customer_id):
    """Rebuild the customer's last order at current availability and price, highlighting what changed."""
    from apps.catalog.pricing import PricingError
    from apps.orders.models import Check
    from apps.orders.services import pricer_for

    c = Customer.objects.filter(tenant=branch.tenant, pk=customer_id).first()
    if c is None:
        raise NotFound("Customer not found")
    last = (Check.objects.filter(customer=c, branch=branch).exclude(status__in=["voided", "merged"])
            .order_by("-opened_at").first())
    if last is None:
        return {"customer": serialize_customer(c, branch), "check": None, "items": []}
    pricer = pricer_for(branch)
    items = []
    for ln in last.lines.filter(status="active").exclude(product_code="delivery-fee").order_by("line_no"):
        spec = {**ln.spec, "client_line_id": f"repeat-{ln.line_no}", "quantity": ln.quantity}
        entry = {"spec": spec, "name": ln.name, "quantity": ln.quantity, "old_unit_minor": ln.unit_minor}
        try:
            priced = pricer.price_line(spec, 0)
            entry["new_unit_minor"] = priced.unit_minor
            entry["status"] = "ok" if priced.unit_minor == ln.unit_minor else "price_changed"
            entry["name"] = priced.name
        except PricingError as e:
            entry["status"] = "unavailable"
            entry["message"] = e.message
            entry["new_unit_minor"] = None
        items.append(entry)
    return {"customer": serialize_customer(c, branch),
            "check": {"id": str(last.pk), "token": last.token, "business_date": last.business_date,
                      "service_type": last.service_type},
            "delivery": _last_delivery_address(last), "items": items,
            "changes": sum(1 for i in items if i["status"] != "ok")}


def _last_delivery_address(check):
    d = check.deliveries.order_by("-attempt").first()
    if d is None:
        return None
    return {"address_text": d.address_text, "landmark": d.landmark, "zone_id": str(d.zone_id),
            "needs_confirmation": True}


# ---------------------------------------------------------------- check opening helpers

def add_fee_line(ctx, check, amount_minor, label):
    """Delivery fee as a non-kitchen charge line (round 0) so payments/refunds can allocate to it."""
    from apps.orders.models import CheckLine, CheckRound
    rnd, _ = CheckRound.objects.get_or_create(check_ref=check, number=0, defaults={
        "added_by": ctx.staff, "device": ctx.device, "command_key": ctx.key})
    last_no = check.lines.order_by("-line_no").values_list("line_no", flat=True).first() or 0
    return CheckLine.objects.create(
        check_ref=check, round=rnd, line_no=last_no + 1, client_line_id="delivery-fee",
        menu_release=ctx.branch.active_release, product_code="delivery-fee", name=label, variant="",
        quantity=1, unit_minor=amount_minor, total_minor=amount_minor, currency=ctx.branch.currency,
        spec={}, breakdown=[{"rule": "delivery.fee", "label": label, "amount_minor": amount_minor}],
        components=[], fire_state="fired",
    )


def prepare_takeaway(ctx, check, data):
    """Called by open_check for counter/pickup/delivery: customer, promised time and delivery attempt."""
    now = timezone.now()
    phone, name = data.get("customer_phone"), str(data.get("customer_name", "")).strip()
    customer = upsert_customer(ctx.branch, phone, name) if phone else None
    if check.service_type == "delivery" and customer is None:
        raise DomainError("A delivery needs the customer's phone number", field_errors={"customer_phone": "required"})
    requested = data.get("requested_at")
    if requested:
        from django.utils.dateparse import parse_datetime
        requested = parse_datetime(str(requested))
        if requested is None:
            raise DomainError("requested_at must be an RFC 3339 time", field_errors={"requested_at": "invalid"})
    check.customer = customer
    check.requested_at = requested
    check.notes = str(data.get("notes", ""))[:300]
    if check.service_type == "delivery":
        zone = DeliveryZone.objects.filter(branch=ctx.branch, pk=data.get("zone_id"), is_active=True).first()
        if zone is None:
            raise DomainError("Choose a delivery zone", field_errors={"zone_id": "required"})
        text = str(data.get("address_text", "")).strip()
        if len(text) < 5:
            raise DomainError("Confirm the delivery address", field_errors={"address_text": "required"})
        payment = data.get("payment", "cod")
        if payment not in ("cod", "prepaid"):
            raise DomainError("payment must be cod or prepaid", field_errors={"payment": "invalid"})
        check.promised_at = max(requested or now, now + timedelta(minutes=zone.eta_minutes))
        check.save()
        landmark = str(data.get("landmark", ""))[:120]
        addr = customer.addresses.filter(text=text).first()
        if addr is None:
            addr = Address.objects.create(customer=customer, text=text[:250], landmark=landmark, zone=zone)
        addr.last_used_at, addr.zone, addr.landmark = now, zone, landmark or addr.landmark
        addr.save()
        if zone.fee_minor:
            add_fee_line(ctx, check, zone.fee_minor, f"Delivery fee ({zone.name})")
        Delivery.objects.create(
            branch=ctx.branch, check_ref=check, payment=payment, customer_name=name or customer.name,
            phone=customer.phone, address_text=text[:250], landmark=landmark, zone=zone, fee_minor=zone.fee_minor,
            promised_at=check.promised_at,
        )
    else:
        check.promised_at = max(requested or now, now + timedelta(minutes=ctx.branch.pickup_minutes))
        check.save()
    if customer:
        customer.last_order_at = now
        customer.save(update_fields=["last_order_at", "updated_at"])


# ---------------------------------------------------------------- packing

def _current_items(check):
    from apps.kitchen.models import TicketItem
    return list(TicketItem.objects.filter(line__check_ref=check, line__status="active").exclude(status="cancelled")
                .exclude(remakes__isnull=False).select_related("ticket__station", "line")
                .order_by("line__line_no", "component_index"))


def packing_checklist(check):
    items = _current_items(check)
    comps = [{"id": str(i.pk), "label": i.label, "quantity": i.quantity, "station": i.ticket.station.code,
              "status": i.status, "slot_name": i.slot_name, "detail": i.detail, "revision": i.revision,
              "done": i.status in DONE} for i in items]
    for ln in check.lines.filter(status="active", fire_state="held"):
        comps.append({"id": None, "label": ln.name.upper(), "quantity": ln.quantity, "station": None,
                      "status": "held", "slot_name": "", "detail": [], "revision": 0, "done": False})
    return comps


def serialize_pack(check):
    from apps.orders.services import check_amounts
    comps = packing_checklist(check)
    now = timezone.now()
    d = check.deliveries.order_by("-attempt").first()
    return {
        "id": str(check.pk), "token": check.token, "revision": check.revision, "service_type": check.service_type,
        "status": check.status, "channel": check.channel, "customer_name": check.customer_name,
        "promised_at": check.promised_at, "late": bool(check.promised_at and check.promised_at < now),
        "components": comps, "missing": [c for c in comps if not c["done"]],
        "complete": all(c["done"] for c in comps) and bool(comps),
        "handed_over_at": check.handed_over_at, "balance_minor": check_amounts(check)["balance_minor"],
        "delivery": serialize_delivery(d) if d else None, "notes": check.notes,
    }


def packing_queue(branch):
    from apps.orders.models import Check
    qs = (Check.objects.filter(branch=branch, service_type__in=TAKEAWAY, handed_over_at__isnull=True,
                               status__in=["open", "closed"]).exclude(lines__isnull=True).distinct()
          .order_by("promised_at", "opened_at")[:100])
    return {"orders": [serialize_pack(c) for c in qs], "server_time": timezone.now()}


def handover(ctx, check_id, data):
    from apps.orders.services import lock_check
    from apps.stock.services import post_packaging

    check = lock_check(ctx.branch, check_id)
    if check.status not in ("open", "closed"):
        raise Conflict("Check is not active", code="check_closed")
    if check.handed_over_at:
        raise Conflict("Already handed over", code="already_handed_over")
    if data.get("expected_revision") is not None and int(data["expected_revision"]) != check.revision:
        raise StaleRevision("The order changed. Check the components again.", current_revision=check.revision)
    comps = packing_checklist(check)
    if not comps:
        raise DomainError("Nothing to hand over", code="empty_check")
    missing = [c for c in comps if not c["done"]]
    approver, override = None, ""
    if missing:
        override = str(data.get("override_reason", "")).strip()[:200]
        if not override:
            raise DomainError("Not all components are ready", code="pack_incomplete",
                              extra={"missing": [f"{c['quantity']} x {c['label']} ({c['status']})" for c in missing]})
        approver = resolve_approver(ctx.principal, data, "pack.override")
    from apps.kitchen.models import TicketItem
    now = timezone.now()
    items = list(TicketItem.objects.select_for_update().filter(pk__in=[c["id"] for c in comps if c["id"]]))
    for it in items:
        if it.status == "ready":
            it.status = "packed"
            it.revision += 1
            it.save(update_fields=["status", "revision", "updated_at"])
    if check.service_type != "dine_in":
        post_packaging(ctx, check, items)
    check.handed_over_at, check.handed_over_by, check.handover_override = now, ctx.staff, override
    prev = check.revision
    check.revision += 1
    check.save(update_fields=["handed_over_at", "handed_over_by", "handover_override", "revision", "updated_at"])
    ctx.audit("check.handover", check, prev, check.revision, reason=override, approved_by=approver,
              data={"missing": [c["label"] for c in missing]})
    ctx.emit("check.handed_over", check, {"check_id": check.pk, "override": bool(missing),
                                          "service_type": check.service_type})
    return 200, serialize_pack(check)


# ---------------------------------------------------------------- deliveries

def serialize_delivery(d, show_contact=True):
    from apps.orders.services import check_amounts
    check = d.check_ref
    active = d.status not in ("delivered", "failed")
    data = {
        "id": str(d.pk), "revision": d.revision, "check_id": str(d.check_ref_id), "token": check.token,
        "attempt": d.attempt, "status": d.status, "payment": d.payment, "zone": d.zone.name, "fee_minor": d.fee_minor,
        "promised_at": d.promised_at, "late": d.promised_at < (d.delivered_at or timezone.now()),
        "rider": {"id": str(d.rider_id), "name": d.rider.display_name} if d.rider_id else None,
        "assigned_at": d.assigned_at, "dispatched_at": d.dispatched_at, "delivered_at": d.delivered_at,
        "failed_at": d.failed_at, "failure_reason": d.failure_reason, "food_disposition": d.food_disposition,
        "decision_required": d.decision_required, "packed": check.handed_over_at is not None,
        "amount_to_collect_minor": check_amounts(check)["balance_minor"] if d.payment == "cod" else 0,
        "check_status": check.status,
    }
    if show_contact or active:
        data.update({"customer_name": d.customer_name, "phone": d.phone, "address_text": d.address_text,
                     "landmark": d.landmark})
    return data


def deliveries(branch, active=True, rider=None):
    qs = Delivery.objects.filter(branch=branch).select_related("check_ref", "zone", "rider")
    if active:
        qs = qs.exclude(status="delivered").exclude(status="failed", decision_required=False)
    else:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=1))
    if rider is not None:
        qs = qs.filter(rider=rider, status__in=["assigned", "dispatched"])
    return {"deliveries": [serialize_delivery(d, show_contact=rider is None) for d in qs.order_by("promised_at")],
            "server_time": timezone.now()}


def riders_at(branch):
    ids = Membership.objects.filter(branch=branch, role="rider", is_active=True).values_list("staff_id", flat=True)
    return Staff.objects.filter(pk__in=ids, is_active=True).order_by("display_name")


def _lock_delivery(ctx, delivery_id):
    d = (Delivery.objects.select_for_update(of=("self",)).filter(branch=ctx.branch, pk=delivery_id)
         .select_related("check_ref", "zone", "rider").first())
    if d is None:
        raise NotFound("Delivery not found")
    return d


def transition_delivery(ctx, delivery_id, data):
    from apps.cash.models import Tender, TenderAllocation
    from apps.cash.services import allocate_to_lines, finalize_close
    from apps.orders.services import check_amounts, lock_check

    # Lock order: check, then delivery (same as tendering: check first).
    check_id = Delivery.objects.filter(branch=ctx.branch, pk=delivery_id).values_list("check_ref_id", flat=True).first()
    if check_id is None:
        raise NotFound("Delivery not found")
    check = lock_check(ctx.branch, check_id)
    d = _lock_delivery(ctx, delivery_id)
    d.check_ref = check
    to = data.get("to")
    if data.get("expected_revision") is not None and int(data["expected_revision"]) != d.revision:
        raise StaleRevision("Delivery changed on another screen", current_revision=d.revision)
    if to not in Delivery.TRANSITIONS.get(d.status, set()):
        raise Conflict(f"Cannot move delivery from {d.status} to {to}", code="invalid_transition",
                       current_revision=d.revision)
    now = timezone.now()
    extra = {}
    if to == "assigned":
        rider = riders_at(ctx.branch).filter(pk=data.get("rider_id")).first()
        if rider is None:
            raise DomainError("Choose a rider working at this branch", field_errors={"rider_id": "invalid"})
        d.rider, d.assigned_at = rider, now
    elif to == "awaiting_assignment":
        d.rider, d.assigned_at = None, None
    elif to == "dispatched":
        if check.handed_over_at is None:
            raise DomainError("Pack and hand over the order before dispatch", code="not_packed")
        if check.status == "voided":
            raise Conflict("Check was voided", code="check_closed")
        d.dispatched_at = now
    elif to == "delivered":
        balance = check_amounts(check)["balance_minor"]
        if d.payment == "prepaid" and balance > 0:
            raise DomainError("Prepaid order still has a balance; collect it or change to COD", code="balance_due",
                              extra={"balance_minor": balance})
        if balance > 0:
            collected = int(data.get("collected_minor", balance))
            if collected != balance:
                raise DomainError("Collected cash must equal the amount due", code="collection_mismatch",
                                  field_errors={"collected_minor": "mismatch"}, extra={"balance_minor": balance})
            bdate = ctx.branch.business_date()
            tender = Tender.objects.create(
                branch=ctx.branch, check_ref=check, method="rider_cod", status="succeeded", amount_minor=balance,
                currency=ctx.branch.currency, staff=ctx.staff, business_date=bdate,
                external_ref=f"rider:{d.rider.username}" if d.rider_id else "",
            )
            for lid, amt in allocate_to_lines(check, balance):
                TenderAllocation.objects.create(tender=tender, line_id=lid, amount_minor=amt)
            RiderCollection.objects.create(branch=ctx.branch, rider=d.rider, delivery=d, tender=tender,
                                           amount_minor=balance, business_date=bdate)
            ctx.emit("tender.succeeded", tender, {"check_id": check.pk, "tender_id": tender.pk, "method": "rider_cod",
                                                  "amount_minor": balance, "business_date": bdate})
            extra["collected_minor"] = balance
        d.delivered_at = now
        if check.status == "open":
            extra["receipt_no"] = finalize_close(ctx, check)
    elif to == "failed":
        reason = str(data.get("reason", "")).strip()[:200]
        food = data.get("food_disposition")
        if not reason:
            raise DomainError("Give the failure reason", field_errors={"reason": "required"})
        if food not in ("discarded", "returned", "redeliver"):
            raise DomainError("Say what happens to the food", field_errors={"food_disposition": "required"})
        # No sale reversal and no fresh stock here: refund/void/redelivery is an explicit later decision.
        d.failed_at, d.failure_reason, d.food_disposition, d.decision_required = now, reason, food, True
    prev = d.revision
    d.status = to
    d.revision += 1
    d.save()
    ctx.audit(f"delivery.{to}", d, prev, d.revision, reason=str(data.get("reason", ""))[:200],
              data={"rider": str(d.rider_id) if d.rider_id else None, **{k: v for k, v in extra.items()}})
    ctx.emit(f"delivery.{to}", d, {"delivery_id": d.pk, "check_id": check.pk, "attempt": d.attempt,
                                   "rider_id": d.rider_id, **extra})
    return 200, {**serialize_delivery(d), **extra}


def retry_delivery(ctx, delivery_id, data):
    from apps.orders.services import lock_check
    check_id = Delivery.objects.filter(branch=ctx.branch, pk=delivery_id).values_list("check_ref_id", flat=True).first()
    if check_id is None:
        raise NotFound("Delivery not found")
    check = lock_check(ctx.branch, check_id)
    d = _lock_delivery(ctx, delivery_id)
    if d.status != "failed":
        raise Conflict("Only a failed delivery can be retried", code="invalid_transition")
    if check.deliveries.exclude(status__in=["delivered", "failed"]).exists():
        raise Conflict("A delivery attempt is already active", code="delivery_active")
    if check.status == "voided":
        raise Conflict("Check was voided", code="check_closed")
    now = timezone.now()
    new = Delivery.objects.create(
        branch=ctx.branch, check_ref=check, attempt=d.attempt + 1, previous=d, payment=d.payment,
        customer_name=d.customer_name, phone=d.phone, address_text=str(data.get("address_text") or d.address_text)[:250],
        landmark=d.landmark, zone=d.zone, fee_minor=0, promised_at=now + timedelta(minutes=d.zone.eta_minutes),
    )
    d.decision_required = False
    d.revision += 1
    d.save(update_fields=["decision_required", "revision", "updated_at"])
    ctx.audit("delivery.retry", new, None, new.revision, reason=str(data.get("reason", ""))[:200],
              data={"previous": str(d.pk)})
    ctx.emit("delivery.retry", new, {"delivery_id": new.pk, "check_id": check.pk, "attempt": new.attempt})
    return serialize_delivery(new)


# ---------------------------------------------------------------- riders

def rider_balances(branch):
    out = []
    now = timezone.now()
    for r in riders_at(branch):
        open_cols = list(RiderCollection.objects.filter(branch=branch, rider=r).select_related("delivery__check_ref")
                         .order_by("created_at"))
        open_cols = [c for c in open_cols if c.outstanding_minor > 0]
        out.append({
            "id": str(r.pk), "name": r.display_name,
            "outstanding_minor": sum(c.outstanding_minor for c in open_cols),
            "collections": [{"id": str(c.pk), "token": c.delivery.check_ref.token, "amount_minor": c.amount_minor,
                             "outstanding_minor": c.outstanding_minor, "created_at": c.created_at,
                             "age_minutes": int((now - c.created_at).total_seconds() // 60)} for c in open_cols],
            "active_deliveries": Delivery.objects.filter(branch=branch, rider=r,
                                                         status__in=["assigned", "dispatched"]).count(),
        })
    return {"riders": out, "server_time": now}


def remit(ctx, rider_id, data):
    from apps.cash.models import DrawerMovement, DrawerShift

    rider = riders_at(ctx.branch).filter(pk=rider_id).first()
    if rider is None:
        raise NotFound("Rider not found")
    try:
        amount = int(data.get("amount_minor"))
    except (TypeError, ValueError):
        raise DomainError("amount_minor is required", field_errors={"amount_minor": "required"})
    if amount < 1:
        raise DomainError("Amount must be positive", field_errors={"amount_minor": "too_small"})
    cols = RiderCollection.objects.select_for_update().filter(branch=ctx.branch, rider=rider).order_by("created_at")
    if data.get("collection_ids"):
        cols = cols.filter(pk__in=data["collection_ids"])
    cols = [c for c in cols if c.outstanding_minor > 0]
    outstanding = sum(c.outstanding_minor for c in cols)
    if amount > outstanding:
        raise DomainError("More than the rider owes", code="remittance_exceeds",
                          extra={"outstanding_minor": outstanding})
    approver = resolve_approver(ctx.principal, data, "rider.remittance")
    shift = DrawerShift.objects.select_for_update().filter(
        branch=ctx.branch, drawer_code=data.get("drawer_code", "main")).exclude(status="closed").first()
    if shift is None:
        raise Conflict("Open a drawer to receive rider cash", code="no_open_drawer")
    if shift.status != "open":
        raise Conflict("Drawer is being counted", code="drawer_counting")
    bdate = ctx.branch.business_date()
    note = str(data.get("note", ""))[:200]
    mv = DrawerMovement.objects.create(shift=shift, kind="remittance", amount_minor=amount, staff=ctx.staff,
                                       approved_by=approver, reason=f"Rider {rider.display_name}: {note}"[:200],
                                       business_date=bdate)
    rem = RiderRemittance.objects.create(branch=ctx.branch, rider=rider, amount_minor=amount, shift=shift, movement=mv,
                                         staff=ctx.staff, approved_by=approver, note=note, business_date=bdate)
    left = amount
    for c in cols:
        take = min(left, c.outstanding_minor)
        if take <= 0:
            break
        RemittanceAllocation.objects.create(remittance=rem, collection=c, amount_minor=take)
        c.remitted_minor += take
        c.revision += 1
        c.save(update_fields=["remitted_minor", "revision", "updated_at"])
        left -= take
    shift.revision += 1
    shift.save(update_fields=["revision", "updated_at"])
    remaining = outstanding - amount
    ctx.audit("rider.remittance", rem, None, 1, reason=note, approved_by=approver,
              data={"rider": str(rider.pk), "amount_minor": amount, "outstanding_after_minor": remaining})
    ctx.emit("rider.remitted", rem, {"remittance_id": rem.pk, "rider_id": rider.pk, "amount_minor": amount,
                                     "outstanding_minor": remaining, "business_date": bdate})
    return {"remittance_id": str(rem.pk), "amount_minor": amount, "outstanding_minor": remaining,
            "drawer_movement_id": mv.pk, "approved_by": approver.display_name}


def outstanding_total(branch):
    tot = RiderCollection.objects.filter(branch=branch).aggregate(a=Sum("amount_minor"), r=Sum("remitted_minor"))
    return (tot["a"] or 0) - (tot["r"] or 0)
