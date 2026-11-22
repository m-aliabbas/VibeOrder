from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.core.commands import run_command
from apps.core.scope import branch_for, require

from . import services
from .models import PrintJob


@api_view(["GET"])
def station_queue(request, branch_id, station_id):
    branch = branch_for(request, branch_id)
    require(request, "station.view")
    return Response(services.station_queue(branch, station_id, request.query_params.get("include_done") == "1"))


@api_view(["POST"])
def transition(request, branch_id, item_id):
    branch_for(request, branch_id)
    require(request, "ticket.transition")
    return run_command(request, branch_id, "ticket_item.transition", {"item": str(item_id), **request.data},
                       lambda ctx: services.transition_item(ctx, item_id, request.data))


@api_view(["POST"])
def acknowledge(request, branch_id, ticket_id):
    branch_for(request, branch_id)
    require(request, "ticket.acknowledge")
    return run_command(request, branch_id, "ticket.acknowledge", {"ticket": str(ticket_id)},
                       lambda ctx: services.acknowledge_ticket(ctx, ticket_id))


@api_view(["GET"])
def devices_health(request, branch_id):
    branch = branch_for(request, branch_id)
    return Response(services.device_health(branch))


@api_view(["GET"])
def print_jobs(request, branch_id):
    branch = branch_for(request, branch_id)
    return Response(services.job_log(branch, request.query_params))


@api_view(["GET"])
def print_job_detail(request, branch_id, job_id):
    branch = branch_for(request, branch_id)
    return Response(services.job_detail(branch, job_id))


@api_view(["POST"])
def reprint(request, branch_id, job_id):
    branch_for(request, branch_id)
    require(request, "print.reprint")
    return run_command(request, branch_id, "print.reprint", {"job": str(job_id), **request.data},
                       lambda ctx: services.reprint(ctx, job_id, request.data))


def kds_page(request):
    return render(request, "kds.html")


@api_view(["GET"])
def pickups(request, branch_id):
    branch = branch_for(request, branch_id)
    require(request, "ticket.serve")
    return Response(services.pickups(branch))


@api_view(["POST"])
def serve(request, branch_id):
    branch_for(request, branch_id)
    require(request, "ticket.serve")
    return run_command(request, branch_id, "ticket.serve", request.data, lambda ctx: services.serve_items(ctx, request.data))


# ---------------------------------------------------------------- printer setup (manager)

def _printer_row(p):
    return {"id": str(p.pk), "name": p.name, "purpose": p.purpose, "transport": p.transport, "address": p.address,
            "station_id": str(p.station_id) if p.station_id else None,
            "station": p.station.name if p.station_id else None, "width_chars": p.width_chars,
            "is_active": p.is_active, "last_error": p.last_error, "last_ok_at": p.last_ok_at}


def _receipt_settings(branch):
    return {"business_name": branch.tenant.name, "branch_name": branch.name, "address": branch.address,
            "phone": branch.phone, "tax_number": branch.tax_number, "receipt_footer": branch.receipt_footer}


@api_view(["GET", "POST"])
def printers(request, branch_id):
    """GET: printers, the printers installed on this PC (for USB) and stations. POST: add a printer."""
    from .escpos import list_system_printers
    from .models import Printer

    branch = branch_for(request, branch_id)
    require(request, "printer.manage")
    if request.method == "GET":
        return Response({
            "printers": [_printer_row(p) for p in Printer.objects.filter(branch=branch).select_related("station")
                         .order_by("purpose", "name")],
            "installed": list_system_printers(),
            "stations": [{"id": str(s.pk), "name": s.name} for s in branch.stations.order_by("code")],
            "transports": [{"code": c, "name": n} for c, n in Printer.TRANSPORTS],
            "receipt": _receipt_settings(branch),
        })
    return run_command(request, branch_id, "printer.create", request.data,
                       lambda ctx: _save_printer(ctx, None, request.data))


@api_view(["POST"])
def printer_detail(request, branch_id, printer_id):
    branch_for(request, branch_id)
    require(request, "printer.manage")
    return run_command(request, branch_id, "printer.update", {"printer": str(printer_id), **request.data},
                       lambda ctx: _save_printer(ctx, printer_id, request.data))


def _save_printer(ctx, printer_id, data):
    from apps.core.errors import DomainError, NotFound
    from apps.core.models import Station

    from .models import Printer

    if printer_id:
        p = Printer.objects.select_for_update().filter(branch=ctx.branch, pk=printer_id).first()
        if p is None:
            raise NotFound("Printer not found")
    else:
        p = Printer(branch=ctx.branch)
    for f in ("name", "address"):
        if f in data:
            setattr(p, f, str(data[f]).strip()[:120])
    if "purpose" in data:
        if data["purpose"] not in ("kitchen", "receipt"):
            raise DomainError("Purpose is kitchen or receipt", field_errors={"purpose": "invalid"})
        p.purpose = data["purpose"]
    if "transport" in data:
        if data["transport"] not in dict(Printer.TRANSPORTS):
            raise DomainError("Unknown connection type", field_errors={"transport": "invalid"})
        p.transport = data["transport"]
    if "width_chars" in data:
        w = int(data["width_chars"] or 48)
        if w not in (32, 42, 48, 64):
            raise DomainError("Width is 32 (58 mm), 42 or 48 (80 mm) or 64", field_errors={"width_chars": "invalid"})
        p.width_chars = w
    if "is_active" in data:
        p.is_active = bool(data["is_active"])
    if "station_id" in data:
        sid = data["station_id"]
        p.station = Station.objects.filter(branch=ctx.branch, pk=sid).first() if sid else None
        if sid and p.station is None:
            raise DomainError("Station not found", field_errors={"station_id": "invalid"})
    if not p.name:
        raise DomainError("Give the printer a name", field_errors={"name": "required"})
    if not p.address:
        raise DomainError("Choose the printer (or its IP address)", field_errors={"address": "required"})
    if p.transport == "tcp" and ":" not in p.address:
        p.address += ":9100"
    p.save()
    ctx.audit("printer.update" if printer_id else "printer.create", p, data={"transport": p.transport,
                                                                             "address": p.address})
    return (200 if printer_id else 201), _printer_row(p)


@api_view(["POST"])
def printer_test(request, branch_id, printer_id):
    """Queue a test page through the normal print worker (so it proves the whole path)."""
    from .layouts import render_test_page
    from .models import Printer, PrintJob

    branch = branch_for(request, branch_id)
    require(request, "printer.manage")

    def handler(ctx):
        p = Printer.objects.filter(branch=branch, pk=printer_id).first()
        if p is None:
            from apps.core.errors import NotFound
            raise NotFound("Printer not found")
        job = PrintJob.objects.create(branch=branch, printer=p, kind="test", content=render_test_page(p, p.width_chars),
                                      requested_by=ctx.staff)
        ctx.audit("printer.test", p)
        return services.serialize_job(job)

    return run_command(request, branch_id, "printer.test", {"printer": str(printer_id)}, handler)


@api_view(["POST"])
def receipt_settings(request, branch_id):
    """Header and footer printed on bills and receipts."""
    from apps.core.models import Branch

    branch_for(request, branch_id)
    require(request, "printer.manage")

    def handler(ctx):
        b = Branch.objects.select_for_update().get(pk=ctx.branch.pk)
        fields = {"address": 200, "phone": 40, "tax_number": 40, "receipt_footer": 200}
        changed = []
        for f, n in fields.items():
            if f in request.data:
                changed.append(f)
                # update() only: saving a stale Branch would reset stream_position (outbox safety).
                Branch.objects.filter(pk=b.pk).update(**{f: str(request.data[f]).strip()[:n]})
        ctx.audit("branch.receipt_settings", b, data={"changed": changed})
        return 200, _receipt_settings(Branch.objects.get(pk=b.pk))

    return run_command(request, branch_id, "branch.receipt_settings", request.data, handler)
