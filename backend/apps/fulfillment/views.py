from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.core.commands import run_command
from apps.core.errors import Forbidden
from apps.core.scope import branch_for, require

from . import services
from .models import DeliveryZone


@api_view(["GET"])
def zones(request, branch_id):
    branch = branch_for(request, branch_id)
    require(request, "check.view")
    return Response({"zones": [{"id": str(z.pk), "code": z.code, "name": z.name, "fee_minor": z.fee_minor,
                                "eta_minutes": z.eta_minutes}
                               for z in DeliveryZone.objects.filter(branch=branch, is_active=True).order_by("name")]})


@api_view(["GET"])
def customers(request, branch_id):
    branch = branch_for(request, branch_id)
    require(request, "customer.lookup")
    return Response(services.lookup_customers(branch, request.query_params.get("q", "")))


@api_view(["GET"])
def repeat(request, branch_id, customer_id):
    branch = branch_for(request, branch_id)
    require(request, "customer.lookup")
    return Response(services.repeat_order(branch, customer_id))


@api_view(["GET"])
def packing(request, branch_id):
    branch = branch_for(request, branch_id)
    require(request, "pack.verify")
    return Response(services.packing_queue(branch))


@api_view(["POST"])
def handover(request, branch_id, check_id):
    branch_for(request, branch_id)
    require(request, "pack.verify")
    return run_command(request, branch_id, "check.handover", {"check": str(check_id), **request.data},
                       lambda ctx: services.handover(ctx, check_id, request.data))


@api_view(["GET"])
def deliveries(request, branch_id):
    branch = branch_for(request, branch_id)
    if request.user.has("delivery.manage"):
        return Response(services.deliveries(branch, active=request.query_params.get("scope") != "today"))
    if request.user.has("delivery.view_own"):
        # A rider sees only their assigned, active jobs (contact details only while active).
        return Response(services.deliveries(branch, rider=request.user.staff))
    raise Forbidden("Your role cannot view deliveries", code="permission_denied")


@api_view(["POST"])
def delivery_transition(request, branch_id, delivery_id):
    branch_for(request, branch_id)
    require(request, "delivery.manage")
    return run_command(request, branch_id, "delivery.transition", {"delivery": str(delivery_id), **request.data},
                       lambda ctx: services.transition_delivery(ctx, delivery_id, request.data))


@api_view(["POST"])
def delivery_retry(request, branch_id, delivery_id):
    branch_for(request, branch_id)
    require(request, "delivery.manage")
    return run_command(request, branch_id, "delivery.retry", {"delivery": str(delivery_id), **request.data},
                       lambda ctx: services.retry_delivery(ctx, delivery_id, request.data))


@api_view(["GET"])
def riders(request, branch_id):
    branch = branch_for(request, branch_id)
    require(request, "rider.view")
    return Response(services.rider_balances(branch))


@api_view(["POST"])
def remittances(request, branch_id, rider_id):
    branch_for(request, branch_id)
    require(request, "shift.view")
    return run_command(request, branch_id, "rider.remittance", {"rider": str(rider_id), **request.data},
                       lambda ctx: services.remit(ctx, rider_id, request.data))
