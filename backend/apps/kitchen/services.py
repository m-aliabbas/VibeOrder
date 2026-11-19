"""Production routing, kitchen queue, ticket rendering and the durable print job lifecycle."""
import logging
import socket
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.commands import next_sequence
from apps.core.errors import Conflict, DomainError, NotFound, StaleRevision
from apps.core.models import Station

from apps.stock.services import consume_item

from .models import PrintJob, Printer, ProductionTicket, TicketItem

log = logging.getLogger("vibeorder.print")

DEFAULT_PREP_MINUTES = {"pizza": 15, "fryer": 8, "packing": 2}


# ---------------------------------------------------------------- dispatch

def dispatch_lines(ctx, check, rnd, lines, kind="original"):
    """Route each fired component to its station. Creates tickets, items and print jobs in the caller's txn."""
    stations = {s.code: s for s in Station.objects.filter(branch=ctx.branch)}
    fallback = next((s for s in stations.values() if s.is_packing), None) or next(iter(stations.values()))
    grouped = {}
    for ln in lines:
        for idx, comp in enumerate(ln.components):
            st = stations.get(comp.get("station")) or fallback
            grouped.setdefault(st.code, (st, []))[1].append((ln, idx, comp))
    now = timezone.now()
    tickets = []
    for code in sorted(grouped):
        st, entries = grouped[code]
        seq = next_sequence(ctx.branch.pk, "ticket", check.business_date)
        ticket = ProductionTicket.objects.create(
            branch=ctx.branch, station=st, check_ref=check, round=rnd, seq=seq, kind=kind,
            due_at=now + timedelta(minutes=DEFAULT_PREP_MINUTES.get(code, 10)),
        )
        for ln, idx, comp in entries:
            ti = TicketItem.objects.create(
                ticket=ticket, line=ln, component_index=idx, label=comp["label"], detail=comp.get("lines", []),
                notes=comp.get("notes", ""), slot_name=comp.get("slot_name", ""), quantity=ln.quantity,
                status="queued",
            )
            consume_item(ctx.branch, ti, ctx.staff)  # recipe consumption at first fire (A21)
        tickets.append(ticket)
        enqueue_ticket_print(ctx.branch, ticket)
    return tickets


def ticket_header(check):
    where = f"TABLE {check.table.label}" if check.table_id else check.get_service_type_display().upper()
    return f"#{check.token}  {where}"


def wrap(text, width, indent=""):
    words, out, cur = text.split(), [], indent
    for w in words:
        if len(cur) + len(w) + (1 if cur.strip() else 0) > width:
            out.append(cur.rstrip())
            cur = indent + "  " + w
        else:
            cur = (cur + " " + w) if cur.strip() else cur + w
    if cur.strip():
        out.append(cur.rstrip())
    return out


def render_ticket(ticket, width=48):
    check = ticket.check_ref
    tz = check.branch.tz
    lines = [
        "=" * width,
        f"{ticket.station.name.upper()}  TICKET {ticket.seq}".center(width),
        ("** FIRE HELD COURSE **" if ticket.kind == "fire" else f"** {ticket.kind.upper()} **" if ticket.kind != "original" else "").center(width),
        ticket_header(check),
        f"Round {ticket.round.number}  Guests {check.guest_count}  By {ticket.round.added_by.display_name}",
        f"Sent {timezone.localtime(ticket.created_at, tz):%H:%M}   DUE {timezone.localtime(ticket.due_at, tz):%H:%M}",
        "-" * width,
    ]
    if ticket.note:
        lines += wrap(f">> {ticket.note}", width) + ["-" * width]
    for item in ticket.items.order_by("line__line_no", "component_index"):
        if item.change_note and item.change_note != ticket.note:
            lines += wrap(f">> {item.change_note}", width)
        prefix = "CANCEL " if item.status == "cancelled" else ""
        lines += wrap(f"{prefix}{item.quantity} x {item.label}", width)
        if item.slot_name:
            lines += wrap(f"[{item.slot_name}]", width, "   ")
        for d in item.detail:
            lines += wrap(d, width, "   ")
        if item.notes:
            lines += wrap(f"NOTE: {item.notes}", width, "   ")
    lines += ["-" * width, f"Ticket {ticket.id}"[:width], "=" * width, "", ""]
    return "\n".join(ln for ln in lines if ln is not None)


def printer_for(branch, purpose, station=None):
    qs = Printer.objects.filter(branch=branch, is_active=True, purpose=purpose)
    if station is not None:
        p = qs.filter(station=station).first()
        if p:
            return p
    return qs.filter(station__isnull=True).first() if station is not None else qs.first()


def enqueue_ticket_print(branch, ticket):
    printer = printer_for(branch, "kitchen", ticket.station)
    if printer is None:
        return None  # KDS-only station
    return PrintJob.objects.create(
        branch=branch, printer=printer, kind="kitchen", ticket=ticket, check_ref=ticket.check_ref,
        content=render_ticket(ticket, printer.width_chars),
    )


def money(minor, currency="PKR"):
    sign = "-" if minor < 0 else ""
    minor = abs(minor)
    rupees, paisa = divmod(minor, 100)
    return f"{sign}{currency} {rupees:,}" + (f".{paisa:02d}" if paisa else "")


def render_bill(check, width=48, title="BILL", receipt_no=None):
    from apps.orders.services import check_amounts

    tz = check.branch.tz
    a = check_amounts(check)
    out = ["=" * width, check.branch.name.center(width), title.center(width)]
    if receipt_no:
        out.append(f"Receipt {receipt_no}".center(width))
    out += [ticket_header(check),
            f"{timezone.localtime(timezone.now(), tz):%Y-%m-%d %H:%M}  Business day {check.business_date}", "-" * width]
    for ln in check.lines.filter(status="active").order_by("line_no"):
        amt = money(ln.total_minor, "")
        label = f"{ln.quantity} x {ln.name}"
        if len(label) + len(amt) + 1 > width:
            out += wrap(label, width)
            out.append(amt.rjust(width))
        else:
            out.append(label + amt.rjust(width - len(label)))
    out.append("-" * width)

    def row(k, v):
        out.append(k + money(v, check.branch.currency).rjust(width - len(k)))

    row("Subtotal", a["subtotal_minor"])
    if a["discount_minor"]:
        row("Discount", -a["discount_minor"])
    if a["tax_minor"]:
        row("Tax", a["tax_minor"])
    row("TOTAL", a["total_minor"])
    for t in check.tenders.filter(status="succeeded").order_by("created_at"):
        row(f"Paid {t.get_method_display()}", t.amount_minor)
        if t.method == "cash" and t.cash_received_minor:
            row("  Cash received", t.cash_received_minor)
            row("  Change", t.change_minor)
    row("Balance", a["balance_minor"])
    out += ["=" * width, "Thank you!".center(width), "", ""]
    return "\n".join(out)


def enqueue_bill(ctx, check):
    printer = printer_for(ctx.branch, "receipt")
    if printer is None:
        return None
    return PrintJob.objects.create(branch=ctx.branch, printer=printer, kind="bill", check_ref=check,
                                   content=render_bill(check, printer.width_chars, "BILL - NOT A RECEIPT"))


def enqueue_receipt(ctx, check):
    printer = printer_for(ctx.branch, "receipt")
    if printer is None:
        return None
    return PrintJob.objects.create(branch=ctx.branch, printer=printer, kind="receipt", check_ref=check,
                                   content=render_bill(check, printer.width_chars, "RECEIPT", check.receipt_no))


def render_payer_receipt(tender, width=48):
    """Receipt for one payer: only the lines (or parts) this tender paid for (spec 04 split by item)."""
    check = tender.check_ref
    tz = check.branch.tz
    out = ["=" * width, check.branch.name.center(width), "PAYER RECEIPT".center(width),
           (f"Receipt {check.receipt_no}" if check.receipt_no else f"Check #{check.token}").center(width),
           ticket_header(check), f"{timezone.localtime(tender.created_at, tz):%Y-%m-%d %H:%M}", "-" * width]
    for a in tender.allocations.select_related("line").order_by("line__line_no"):
        ln = a.line
        label = f"{ln.quantity} x {ln.name}"
        amt = money(a.amount_minor, "")
        if a.amount_minor != ln.charge_minor:
            label += " (part)"
        if len(label) + len(amt) + 1 > width:
            out += wrap(label, width)
            out.append(amt.rjust(width))
        else:
            out.append(label + amt.rjust(width - len(label)))
    out.append("-" * width)

    def row(k, v):
        out.append(k + money(v, check.branch.currency).rjust(width - len(k)))

    row(f"Paid {tender.get_method_display()}"[:width - 14], tender.amount_minor)
    if tender.method == "cash" and tender.cash_received_minor:
        row("  Cash received", tender.cash_received_minor)
        row("  Change", tender.change_minor)
    if tender.external_ref:
        out.append(f"Ref {tender.external_ref}"[:width])
    out += [f"Payment {tender.pk}"[:width], "=" * width, "Thank you!".center(width), "", ""]
    return "\n".join(out)


def enqueue_payer_receipt(ctx, tender):
    printer = printer_for(ctx.branch, "receipt")
    if printer is None:
        raise DomainError("No receipt printer configured", code="no_printer")
    return PrintJob.objects.create(branch=ctx.branch, printer=printer, kind="payer_receipt",
                                   check_ref=tender.check_ref, content=render_payer_receipt(tender, printer.width_chars))


# ---------------------------------------------------------------- queue

def serialize_ticket_item(item):
    t = item.ticket
    check = t.check_ref
    return {
        "id": str(item.pk), "revision": item.revision, "status": item.status, "quantity": item.quantity,
        "label": item.label, "detail": item.detail, "notes": item.notes, "slot_name": item.slot_name,
        "line_id": str(item.line_id), "ticket": {"id": str(t.pk), "seq": t.seq, "kind": t.kind, "due_at": t.due_at,
                                                 "created_at": t.created_at, "round": t.round.number},
        "check": {"id": str(check.pk), "token": check.token, "service_type": check.service_type,
                  "table": check.table.label if check.table_id else None},
        "started_at": item.started_at, "ready_at": item.ready_at,
        "change_note": item.change_note, "is_remake": item.remake_of_id is not None,
    }


def serialize_change_ticket(t):
    check = t.check_ref
    return {
        "id": str(t.pk), "seq": t.seq, "kind": t.kind, "note": t.note, "created_at": t.created_at,
        "check": {"id": str(check.pk), "token": check.token, "service_type": check.service_type,
                  "table": check.table.label if check.table_id else None},
        "items": [{"id": str(i.pk), "label": i.label, "quantity": i.quantity, "status": i.status,
                   "detail": i.detail, "change_note": i.change_note} for i in t.items.order_by("component_index")],
    }


def acknowledge_ticket(ctx, ticket_id):
    t = ProductionTicket.objects.select_for_update().filter(branch=ctx.branch, pk=ticket_id).first()
    if t is None:
        raise NotFound("Ticket not found")
    if t.kind not in ("change", "remake"):
        raise DomainError("Only change tickets need acknowledgment", code="not_a_change")
    if t.acknowledged_at is None:
        prev = t.revision
        t.acknowledged_at = timezone.now()
        t.acknowledged_by = ctx.staff
        t.revision += 1
        t.save(update_fields=["acknowledged_at", "acknowledged_by", "revision", "updated_at"])
        ctx.audit("ticket.acknowledge", t, prev, t.revision)
        ctx.emit("production.change_acknowledged", t, {"ticket_id": t.pk, "check_id": t.check_ref_id})
    return 200, {"id": str(t.pk), "acknowledged_at": t.acknowledged_at,
                 "acknowledged_by": t.acknowledged_by.display_name}


def station_queue(branch, station_id, include_done=False):
    station = Station.objects.filter(branch=branch, pk=station_id).first()
    if station is None:
        raise NotFound("Station not found")
    statuses = ["queued", "preparing"] + (["ready"] if include_done else [])
    items = (
        TicketItem.objects.filter(ticket__station=station, status__in=statuses)
        .select_related("ticket__check_ref__table", "ticket__round", "line")
        .order_by("ticket__due_at", "ticket__seq", "line__line_no", "component_index")
    )
    ready_recent = []
    if not include_done:
        cutoff = timezone.now() - timedelta(minutes=10)
        ready_recent = list(
            TicketItem.objects.filter(ticket__station=station, status="ready", ready_at__gte=cutoff)
            .select_related("ticket__check_ref__table", "ticket__round", "line").order_by("-ready_at")[:20]
        )
    return {
        "station": {"id": str(station.pk), "code": station.code, "name": station.name},
        "items": [serialize_ticket_item(i) for i in items],
        "recently_ready": [serialize_ticket_item(i) for i in ready_recent],
        "changes": [serialize_change_ticket(t) for t in
                    ProductionTicket.objects.filter(station=station, kind__in=["change", "remake"], acknowledged_at__isnull=True)
                    .select_related("check_ref__table").prefetch_related("items").order_by("created_at")[:50]],
        "server_time": timezone.now(),
    }


def transition_item(ctx, item_id, data):
    item = (
        TicketItem.objects.select_for_update().select_related("ticket__station").filter(
            ticket__branch=ctx.branch, pk=item_id).first()
    )
    if item is None:
        raise NotFound("Ticket item not found")
    to = data.get("to")
    expected = data.get("expected_revision")
    if expected is not None and int(expected) != item.revision:
        raise StaleRevision("Item changed on another screen", current_revision=item.revision)
    if to not in TicketItem.TRANSITIONS.get(item.status, set()):
        raise Conflict(f"Cannot move from {item.status} to {to}", code="invalid_transition",
                       current_revision=item.revision)
    if to == "cancelled":
        raise DomainError("Cancellation goes through a check amendment", code="use_amendment", http_status=422)
    now = timezone.now()
    prev = item.revision
    item.status = to
    item.revision += 1
    if to == "preparing":
        item.started_at = now
    if to == "ready":
        item.ready_at = now
        item.started_at = item.started_at or now
    item.save()
    ctx.audit(f"ticket_item.{to}", item, prev, item.revision)
    ctx.emit("production.item_transitioned", item, {"item_id": item.pk, "to": to, "check_id": item.ticket.check_ref_id})
    return 200, serialize_ticket_item(item)


# ---------------------------------------------------------------- print jobs

def serialize_job(job):
    return {
        "id": str(job.pk), "kind": job.kind, "status": job.status, "printer": job.printer.name,
        "attempts": job.attempts, "last_error": job.last_error, "spooler_ref": job.spooler_ref,
        "is_copy": job.is_copy, "original_id": str(job.original_id) if job.original_id else None,
        "ticket_seq": job.ticket.seq if job.ticket_id else None,
        "ticket_kind": job.ticket.kind if job.ticket_id else None,
        "station": job.ticket.station.code if job.ticket_id else None,
        "check_token": job.check_ref.token if job.check_ref_id else None,
        "check_id": str(job.check_ref_id) if job.check_ref_id else None,
        "receipt_no": job.check_ref.receipt_no if job.check_ref_id else "",
        "reason": job.reason, "requested_by": job.requested_by.display_name if job.requested_by_id else None,
        "created_at": job.created_at, "sent_at": job.sent_at,
        "next_action": {
            "outcome_unknown": "Check the printer for this ticket. If missing, reprint a COPY.",
            "failed_before_send": "Will retry automatically. Check printer power, paper and network.",
            "queued": "Waiting for the print worker.",
            "sending": "Sending now.",
            "handed_to_spooler": "",
        }[job.status],
    }


KOT_KINDS = {"kitchen"}
RECEIPT_KINDS = {"receipt", "bill", "refund", "payer_receipt"}


def job_log(branch, params):
    """Print log for KOT / receipt management: newest first, filterable by order, type, search and problems."""
    from django.db.models import Q
    qs = (PrintJob.objects.filter(branch=branch)
          .select_related("printer", "ticket__station", "check_ref", "requested_by").order_by("-created_at"))
    if params.get("check_id"):
        qs = qs.filter(check_ref_id=params["check_id"])
    group = params.get("type")
    if group == "kot":
        qs = qs.filter(kind__in=KOT_KINDS)
    elif group == "receipts":
        qs = qs.filter(kind__in=RECEIPT_KINDS)
    if params.get("problems") in ("1", "true"):
        qs = qs.filter(status__in=["outcome_unknown", "failed_before_send"])
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    q = (params.get("q") or "").strip().lstrip("#")
    if q:
        cond = Q(check_ref__token__iexact=q) | Q(check_ref__receipt_no__icontains=q)
        if q.isdigit():
            cond |= Q(ticket__seq=int(q), check_ref__business_date=branch.business_date())
        qs = qs.filter(cond)
    if not params.get("check_id") and not q:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(hours=36))
    return {"jobs": [serialize_job(j) for j in qs[:200]], "server_time": timezone.now()}


def job_detail(branch, job_id):
    job = PrintJob.objects.filter(branch=branch, pk=job_id).select_related(
        "printer", "ticket__station", "check_ref", "requested_by").first()
    if job is None:
        raise NotFound("Print job not found")
    copies = [serialize_job(c) for c in job.copies.select_related("printer", "ticket__station", "check_ref",
                                                                  "requested_by").order_by("created_at")]
    return {**serialize_job(job), "content": job.content, "copies": copies}


def reprint(ctx, job_id, data):
    original = PrintJob.objects.select_for_update().filter(branch=ctx.branch, pk=job_id).first()
    if original is None:
        raise NotFound("Print job not found")
    reason = str(data.get("reason", "")).strip()
    if not reason:
        raise DomainError("Give a reason for the reprint", field_errors={"reason": "required"})
    root = original.original or original
    header = f"*** COPY of job {root.pk} ***\nReprinted by {ctx.staff.display_name}: {reason}\n"
    job = PrintJob.objects.create(
        branch=ctx.branch, printer=root.printer, kind=root.kind, ticket=root.ticket, check_ref=root.check_ref,
        content=header + root.content, original=root, is_copy=True, requested_by=ctx.staff, reason=reason[:200],
    )
    ctx.audit("print.reprint", job, None, 1, reason=reason, data={"original": str(root.pk)})
    return serialize_job(job)


class PrintTransportError(Exception):
    """Raised when we know bytes did not leave this host."""


def send_to_printer(printer: Printer, job: PrintJob) -> str:
    if printer.transport == "spool_file":
        queue = printer.address or f"printer-{printer.pk}"
        d = Path(settings.PRINT_SPOOL_DIR) / queue
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{timezone.now():%Y%m%dT%H%M%S}-{job.pk}.txt"
        path.write_text(job.content, encoding="utf-8")
        return str(path)
    if printer.transport == "tcp":
        host, _, port = printer.address.partition(":")
        try:
            sock = socket.create_connection((host, int(port or 9100)), timeout=5)
        except OSError as exc:
            raise PrintTransportError(str(exc)) from exc
        with sock:
            # ESC @ init, text, feed and partial cut. Mixed-script rasterization is a pending item.
            payload = b"\x1b@" + job.content.encode("cp437", errors="replace") + b"\n\n\n\x1dV\x01"
            sock.sendall(payload)
        return f"tcp:{printer.address}"
    raise PrintTransportError(f"Unknown transport {printer.transport}")


def recover_stale_jobs():
    """A job left in 'sending' past its lease may or may not have printed: never re-fire blindly."""
    n = PrintJob.objects.filter(status="sending", lease_until__lt=timezone.now()).update(
        status="outcome_unknown", last_error="Worker stopped during transmission; verify paper before reprinting",
    )
    return n


def lease_next_job():
    with transaction.atomic():
        job = (
            PrintJob.objects.select_for_update(skip_locked=True)
            .filter(status__in=["queued", "failed_before_send"])
            .order_by("created_at").first()
        )
        if job is None:
            return None
        job.status = "sending"
        job.attempts += 1
        job.lease_until = timezone.now() + timedelta(seconds=settings.PRINT_LEASE_SECONDS)
        job.save(update_fields=["status", "attempts", "lease_until", "updated_at"])
        return job


def process_one(send=send_to_printer):
    job = lease_next_job()
    if job is None:
        return None
    printer = job.printer
    try:
        ref = send(printer, job)
    except PrintTransportError as exc:
        PrintJob.objects.filter(pk=job.pk).update(status="failed_before_send", last_error=str(exc)[:300])
        Printer.objects.filter(pk=printer.pk).update(last_error=str(exc)[:300])
        log.warning("print job %s failed before send: %s", job.pk, exc)
        return job.pk
    except Exception as exc:  # unknown point of failure: bytes may have left
        PrintJob.objects.filter(pk=job.pk).update(status="outcome_unknown", last_error=str(exc)[:300])
        log.exception("print job %s outcome unknown", job.pk)
        return job.pk
    now = timezone.now()
    PrintJob.objects.filter(pk=job.pk).update(status="handed_to_spooler", spooler_ref=ref[:500], sent_at=now,
                                              last_error="")
    Printer.objects.filter(pk=printer.pk).update(last_error="", last_ok_at=now)
    return job.pk


def device_health(branch):
    from apps.core.models import Device, SyncOutbox

    printers = []
    for p in Printer.objects.filter(branch=branch).select_related("station"):
        jobs = PrintJob.objects.filter(printer=p)
        printers.append({
            "id": str(p.pk), "name": p.name, "purpose": p.purpose, "transport": p.transport,
            "station": p.station.code if p.station_id else None, "active": p.is_active,
            "last_error": p.last_error, "last_ok_at": p.last_ok_at,
            "queued": jobs.filter(status__in=["queued", "sending", "failed_before_send"]).count(),
            "needs_attention": [serialize_job(j) for j in jobs.filter(status__in=["outcome_unknown", "failed_before_send"])
                                .select_related("printer", "ticket", "check_ref").order_by("-created_at")[:20]],
        })
    from apps.ops.models import SyncState
    from apps.ops.services import backup_status
    pending = SyncOutbox.objects.filter(branch=branch, acked_at__isnull=True)
    oldest = pending.order_by("created_at").first()
    now = timezone.now()
    st = SyncState.objects.filter(branch=branch).first()
    backlog_age = int((now - oldest.created_at).total_seconds()) if oldest else 0
    cloud_status = "not_configured" if st is None else (
        "ok" if st.last_success_at and (now - st.last_success_at).total_seconds() < 120 else "stale")
    return {
        "branch": {"status": "ok", "server_time": now, "host_id": settings.HOST_ID,
                   "writable": not branch.writable_host or branch.writable_host == settings.HOST_ID},
        "backup": backup_status(),
        "cloud": {"pending_events": pending.count(),
                  "oldest_pending_at": oldest.created_at if oldest else None, "backlog_age_seconds": backlog_age,
                  "last_success_at": st.last_success_at if st else None, "last_error": st.last_error if st else "",
                  "acked_position": st.acked_position if st else 0, "status": cloud_status},
        "printers": printers,
        "devices": [{"id": str(d.pk), "name": d.name, "kind": d.kind, "last_seen_at": d.last_seen_at,
                     "revoked": d.revoked_at is not None} for d in Device.objects.filter(branch=branch).order_by("name")],
    }
