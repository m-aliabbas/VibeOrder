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
