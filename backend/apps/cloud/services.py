"""Cloud side: event ingestion + owner projection, inbox/ack for online offers, storefront and tracking.

Invariants
- Events are deduplicated by event_id and applied strictly in stream order; the cursor advances only over
  contiguous positions and stops at a gap (missing ranges are reported back for replay). A conflicting event
  (same position, different id) is quarantined as a SyncException and blocks the cursor (A15).
- An online order is offered to exactly one branch. Past the deadline without an acknowledgment it becomes
  "reconciling" — never re-offered or cancelled on timeout alone. A late ack converges on the branch's committed
  outcome (A16).
"""
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.core.errors import Conflict, DomainError, NotFound

from .models import CloudBranch, CloudEvent, DailyProjection, InboxCommand, OnlineOrder, SyncException


# ---------------------------------------------------------------- ingestion

def _projection(branch, business_date):
    d = parse_date(str(business_date)) if business_date else None
    d = d or branch.business_date()
    row, _ = DailyProjection.objects.select_for_update().get_or_create(branch=branch, business_date=d)
    return row


def apply_event(cb, ev):
    p = ev.payload
    t = ev.event_type
    branch = cb.branch
    if t == "check.closed":
        row = _projection(branch, p.get("business_date"))
        row.checks_closed += 1
        row.sales_minor += int(p.get("total_minor", 0))
        if p.get("channel") == "web":
            row.web_orders += 1
    elif t == "tender.succeeded":
        row = _projection(branch, p.get("business_date"))
        m = p.get("method", "other")
        row.tenders = {**row.tenders, m: row.tenders.get(m, 0) + int(p.get("amount_minor", 0))}
    elif t == "refund.succeeded":
        row = _projection(branch, p.get("business_date"))
        row.refunds_minor += int(p.get("amount_minor", 0))
    elif t == "check.voided":
        row = _projection(branch, p.get("business_date"))
        row.voids += 1
    else:
        row = None
    if row is not None:
        row.last_position = ev.stream_position
        row.save()
    # Guest tracking progress for web orders (branch is authoritative; cloud only projects).
    check_id = p.get("check_id")
    progress = {"check.handed_over": "ready", "delivery.dispatched": "out_for_delivery",
                "delivery.delivered": "delivered", "delivery.failed": "delivery_failed",
                "check.closed": None, "check.voided": "cancelled"}.get(t, "")
    if check_id and progress != "":
        qs = OnlineOrder.objects.filter(branch=branch, branch_check_id=check_id)
        if progress is None:
            qs.filter(service_type="pickup", progress="ready").update(progress="collected")
        else:
            qs.update(progress=progress)


def ingest(cb, events):
    """Store a batch, then advance the contiguous cursor. Returns the ack and any missing ranges."""
    now = timezone.now()
    with transaction.atomic():
        cb = CloudBranch.objects.select_for_update().get(pk=cb.pk)
        received = []
        for e in events:
            try:
                pos = int(e["stream_position"])
                eid = e["event_id"]
            except (KeyError, TypeError, ValueError):
                SyncException.objects.create(branch=cb.branch, error=f"malformed event {str(e)[:200]}")
                continue
            received.append(pos)
            if CloudEvent.objects.filter(pk=eid).exists():
                continue  # retransmission: already stored
            clash = CloudEvent.objects.filter(branch=cb.branch, stream_position=pos).first()
            if clash is not None:
                SyncException.objects.get_or_create(branch=cb.branch, event_id=eid, stream_position=pos, defaults={
                    "error": f"position {pos} already holds event {clash.event_id}"})
                continue
            CloudEvent.objects.create(
                event_id=eid, branch=cb.branch, stream_position=pos, schema_version=e.get("schema_version", 1),
                event_type=e.get("event_type", ""), aggregate_type=e.get("aggregate_type", ""),
                aggregate_id=e.get("aggregate_id"), revision=e.get("revision", 1), payload=e.get("payload") or {},
            )
        blocked = SyncException.objects.filter(branch=cb.branch, resolved_at__isnull=True,
                                               stream_position__isnull=False).order_by("stream_position").first()
        while True:
            nxt = cb.acked_position + 1
            if blocked and blocked.stream_position == nxt:
                break
            ev = CloudEvent.objects.filter(branch=cb.branch, stream_position=nxt, applied_at__isnull=True).first()
            if ev is None:
                if CloudEvent.objects.filter(branch=cb.branch, stream_position=nxt).exists():
                    cb.acked_position = nxt  # already applied earlier
                    continue
                break
            apply_event(cb, ev)
            ev.applied_at = now
            ev.save(update_fields=["applied_at"])
            cb.acked_position = nxt
        missing = []
        top = max(received) if received else cb.acked_position
        have = set(CloudEvent.objects.filter(branch=cb.branch, stream_position__gt=cb.acked_position,
                                             stream_position__lte=top).values_list("stream_position", flat=True))
        start = None
        for pos in range(cb.acked_position + 1, top + 1):
            if pos not in have and start is None:
                start = pos
            if pos in have and start is not None:
                missing.append([start, pos - 1])
                start = None
        if start is not None:
            missing.append([start, top])
        cb.heartbeat_at = now
        if events:
            cb.last_event_at = now
        cb.save(update_fields=["acked_position", "heartbeat_at", "last_event_at", "updated_at"])
    return {"acked_position": cb.acked_position, "missing": missing, "server_time": now}


# ---------------------------------------------------------------- inbox / ack

def sweep_offers(branch=None):
    """Offers past their deadline with no branch answer become 'reconciling' (never re-offered)."""
    qs = OnlineOrder.objects.filter(status="offered", offer_deadline__lt=timezone.now())
    if branch is not None:
        qs = qs.filter(branch=branch)
    return qs.update(status="reconciling", updated_at=timezone.now())


def inbox(cb):
    now = timezone.now()
    CloudBranch.objects.filter(pk=cb.pk).update(heartbeat_at=now)
    sweep_offers(cb.branch)
    cmds = list(InboxCommand.objects.filter(branch=cb.branch, acked_at__isnull=True).order_by("created_at")[:50])
    for c in cmds:
        InboxCommand.objects.filter(pk=c.pk).update(delivered_count=c.delivered_count + 1, last_delivered_at=now)
    return {"commands": [{"id": str(c.pk), "kind": c.kind, "payload": c.payload} for c in cmds], "server_time": now}


def ack(cb, data):
    with transaction.atomic():
        cmd = InboxCommand.objects.select_for_update().filter(branch=cb.branch, pk=data.get("command_id")).first()
        if cmd is None:
            raise NotFound("Command not found")
        result = data.get("result") or {}
        if cmd.acked_at is not None:
            if cmd.result != result:
                raise Conflict("Command already acknowledged with a different result", code="ack_mismatch")
            return {"command_id": str(cmd.pk), "status": "already_acked"}
        cmd.acked_at = timezone.now()
        cmd.result = result
        cmd.save()
        order = cmd.order
        if order is not None:
            order = OnlineOrder.objects.select_for_update().get(pk=order.pk)
            if result.get("status") == "accepted":
                # Converges on the branch's committed acceptance, even after the deadline (A16).
                order.status = "accepted"
                order.branch_token = result.get("token", "")[:12]
                order.branch_check_id = result.get("check_id")
                if result.get("promised_at"):
                    from django.utils.dateparse import parse_datetime
                    order.promised_at = parse_datetime(result["promised_at"])
                order.progress = "preparing"
            else:
                order.status = "rejected"
                order.reject_reason = str(result.get("reason", "rejected"))[:200]
                if order.payment == "prepaid":
                    from .payments import on_branch_rejected
                    on_branch_rejected(order)
            order.decided_at = timezone.now()
            order.revision += 1
            order.save()
    CloudBranch.objects.filter(pk=cb.pk).update(heartbeat_at=timezone.now())
    return {"command_id": str(cmd.pk), "status": "acked"}


# ---------------------------------------------------------------- storefront

def store_branches(slug):
    from apps.core.models import Tenant
    tenant = Tenant.objects.filter(slug=slug).first()
    if tenant is None:
        raise NotFound("Store not found")
    out = []
    for b in tenant.branches.order_by("code"):
        out.append({"code": b.code, "name": b.name, "address": b.address, "accepting": accepting(b)[0],
                    "status_message": accepting(b)[1]})
    return tenant, {"store": {"name": tenant.name, "slug": tenant.slug}, "branches": out}


def accepting(branch):
    if branch.online_paused:
        return False, "Not taking online orders right now"
    cb = getattr(branch, "cloud", None)
    if cb is None or cb.revoked_at:
        return False, "Online ordering is not available at this branch"
    if cb.heartbeat_at is None or (timezone.now() - cb.heartbeat_at).total_seconds() > settings.HEARTBEAT_STALE_SECONDS:
        return False, "Branch is temporarily offline"
    return True, "Open"


def _branch(slug, code):
    from apps.core.models import Branch
    b = Branch.objects.filter(tenant__slug=slug, code=code).select_related("tenant", "active_release").first()
    if b is None:
        raise NotFound("Branch not found")
    return b


def store_menu(slug, code):
    from apps.catalog.views import serialize_menu
    from apps.fulfillment.models import DeliveryZone
    b = _branch(slug, code)
    m = serialize_menu(b)
    for c in m["categories"]:
        for p in c["products"]:
            p.pop("station", None)
    ok, msg = accepting(b)
    m["branch"] = {"code": b.code, "name": b.name, "accepting": ok, "status_message": msg,
                   "pickup_minutes": b.pickup_minutes}
    m["zones"] = [{"code": z.code, "name": z.name, "fee_minor": z.fee_minor, "eta_minutes": z.eta_minutes}
                  for z in DeliveryZone.objects.filter(branch=b, is_active=True).order_by("name")]
    return m


def _price(branch, data):
    from apps.fulfillment.models import DeliveryZone
    from apps.orders.services import _items_for_hash, pricer_for
    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        raise DomainError("Your basket is empty", field_errors={"items": "empty"})
    for i, it in enumerate(items):
        it.setdefault("client_line_id", f"web-{i + 1}")
    service = data.get("service_type", "pickup")
    if service not in ("pickup", "delivery"):
        raise DomainError("Choose pickup or delivery", field_errors={"service_type": "invalid"})
    lines, totals = pricer_for(branch).price(_items_for_hash(items))
    fee, zone = 0, None
    if service == "delivery":
        zone = DeliveryZone.objects.filter(branch=branch, code=data.get("zone_code"), is_active=True).first()
        if zone is None:
            raise DomainError("We don't deliver to that area from this branch", field_errors={"zone_code": "invalid"})
        fee = zone.fee_minor
    total = totals["total_minor"] + fee
    return items, lines, totals, zone, fee, total


def store_quote(slug, code, data):
    b = _branch(slug, code)
    items, lines, totals, zone, fee, total = _price(b, data)
    return {"currency": b.currency, "lines": [{"name": ln.name, "quantity": ln.quantity, "total_minor": ln.total_minor,
                                               "breakdown": ln.breakdown} for ln in lines],
            "subtotal_minor": totals["subtotal_minor"], "tax_minor": totals["tax_minor"], "delivery_fee_minor": fee,
            "total_minor": total, "menu_version": b.active_release.version,
            "estimated_minutes": zone.eta_minutes if zone else b.pickup_minutes}


def place_order(slug, code, data, client_key=None):
    b = _branch(slug, code)
    if client_key:
        existing = OnlineOrder.objects.filter(client_key=client_key).first()
        if existing:
            return 200, track(existing.token)
    ok, msg = accepting(b)
    if not ok:
        raise DomainError(msg, code="branch_unavailable", http_status=503)
    items, lines, totals, zone, fee, total = _price(b, data)
    expected = data.get("expected_total_minor")
    if expected is None or int(expected) != total:
        # Never promise a new total without the guest's consent.
        raise Conflict("Prices changed. Please review your total.", code="price_changed",
                       extra={"total_minor": total})
    name = str(data.get("customer_name", "")).strip()[:80]
    from apps.fulfillment.services import normalize_phone
    phone = normalize_phone(data.get("phone"))
    if not name or len(phone) < 10:
        raise DomainError("Enter your name and phone number", field_errors={"phone": "required"})
    service = data.get("service_type", "pickup")
    address = str(data.get("address_text", "")).strip()[:250]
    if service == "delivery" and len(address) < 5:
        raise DomainError("Enter the delivery address", field_errors={"address_text": "required"})
    payment = data.get("payment", "cod")
    if payment not in ("cod", "prepaid"):
        raise DomainError("Unknown payment option", field_errors={"payment": "invalid"})
    if payment == "prepaid" and not settings.ONLINE_PAYMENTS_ENABLED:
        raise DomainError("Online payment is not available yet; choose cash", code="payment_unavailable")
    from apps.core.commands import next_sequence
    ref = f"W{next_sequence(b.pk, 'web', b.business_date()):03d}"
    try:
        with transaction.atomic():
            order = OnlineOrder.objects.create(
                tenant=b.tenant, branch=b, token=OnlineOrder.new_token(), reference=ref,
                status="awaiting_payment" if payment == "prepaid" else "submitted", service_type=service,
                payment=payment, items=items, menu_release=b.active_release, total_minor=total,
                quote_lines=[{"name": ln.name, "quantity": ln.quantity, "total_minor": ln.total_minor} for ln in lines]
                + ([{"name": f"Delivery fee ({zone.name})", "quantity": 1, "total_minor": fee}] if fee else []),
                currency=b.currency, customer_name=name, phone=phone, address_text=address,
                zone_code=zone.code if zone else "", notes=str(data.get("notes", ""))[:300], client_key=client_key,
            )
            if payment == "prepaid":
                from .payments import start_payment
                start_payment(order)
            else:
                offer(order)
    except IntegrityError:
        existing = OnlineOrder.objects.get(client_key=client_key)
        return 200, track(existing.token)
    return 201, track(order.token)


def offer(order):
    """Offer to the chosen branch (one branch only) with an acceptance deadline."""
    now = timezone.now()
    order.status = "offered"
    order.offered_at = now
    order.offer_deadline = now + timedelta(seconds=settings.OFFER_TIMEOUT_SECONDS)
    order.save()
    InboxCommand.objects.create(branch=order.branch, kind="online_offer", order=order, payload={
        "request_id": str(order.pk), "reference": order.reference, "service_type": order.service_type,
        "payment": order.payment, "items": order.items, "menu_release": str(order.menu_release_id),
        "total_minor": order.total_minor, "customer_name": order.customer_name, "phone": order.phone,
        "address_text": order.address_text, "zone_code": order.zone_code, "notes": order.notes,
        "deadline": order.offer_deadline.isoformat(),
        "provider_ref": getattr(getattr(order, "provider_payment", None), "provider_ref", ""),
    })


GUEST_STATUS = {"awaiting_payment": "awaiting_payment", "submitted": "pending", "offered": "pending",
                "reconciling": "checking", "accepted": "accepted", "rejected": "rejected", "expired": "expired"}


def track(token):
    o = OnlineOrder.objects.filter(token=token).select_related("branch").first()
    if o is None:
        raise NotFound("Order not found")
    if o.status == "offered" and o.offer_deadline and o.offer_deadline < timezone.now():
        sweep_offers(o.branch)
        o.refresh_from_db()
    msg = {"pending": "Sent to the branch. Waiting for confirmation.",
           "checking": "Checking with the branch. Your order is not lost.",
           "accepted": "Confirmed by the branch.",
           "rejected": f"The branch could not take this order: {o.reject_reason}",
           "expired": "This order was not confirmed.",
           "awaiting_payment": "Waiting for payment confirmation."}[GUEST_STATUS[o.status]]
    pay = getattr(o, "provider_payment", None)
    return {"token": o.token, "reference": o.reference, "status": GUEST_STATUS[o.status], "message": msg,
            "progress": o.progress, "order_number": o.branch_token or None, "branch": o.branch.name,
            "service_type": o.service_type, "items": o.quote_lines, "total_minor": o.total_minor,
            "currency": o.currency, "promised_at": o.promised_at, "created_at": o.created_at,
            "payment": o.payment, "payment_status": pay.status if pay else None}
