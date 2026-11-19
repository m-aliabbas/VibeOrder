from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.core.commands import run_command
from apps.core.errors import NotFound
from apps.core.scope import branch_for, require

from . import corrections, services
from .models import Check


@api_view(["GET"])
def floor(request, branch_id):
    branch = branch_for(request, branch_id)
    require(request, "floor.view")
    return Response(services.floor_snapshot(branch))


@api_view(["GET", "POST"])
def checks(request, branch_id):
    branch = branch_for(request, branch_id)
    if request.method == "GET":
        require(request, "check.view")
        status = request.query_params.get("status", "open")
        qs = Check.objects.filter(branch=branch, status=status).select_related("table", "opened_by", "branch") \
            .prefetch_related("lines").order_by("-opened_at")[:200]
        return Response({"checks": [services.serialize_check(c, detail=False) for c in qs]})
    require(request, "check.open")
    return run_command(request, branch_id, "check.open", request.data, lambda ctx: services.open_check(ctx, request.data))


@api_view(["GET"])
def check_detail(request, branch_id, check_id):
    branch = branch_for(request, branch_id)
    require(request, "check.view")
    check = Check.objects.filter(branch=branch, pk=check_id).select_related("table", "opened_by", "branch").first()
    if check is None:
        raise NotFound("Check not found")
    return Response(services.serialize_check(check))


@api_view(["POST"])
def quotes(request, branch_id):
    branch_for(request, branch_id)
    require(request, "quote")
    return Response(services.create_quote(request.user, request.data), status=201)


@api_view(["POST"])
def rounds(request, branch_id, check_id):
    branch_for(request, branch_id)
    require(request, "check.append")
    return run_command(request, branch_id, "check.append_round", {"check": str(check_id), **request.data},
                       lambda ctx: services.append_round(ctx, check_id, request.data))


@api_view(["POST"])
def fire(request, branch_id, check_id):
    branch_for(request, branch_id)
    require(request, "check.append")
    return run_command(request, branch_id, "check.fire", {"check": str(check_id), **request.data},
                       lambda ctx: services.fire_held(ctx, check_id, request.data))


@api_view(["POST"])
def request_bill(request, branch_id, check_id):
    branch_for(request, branch_id)
    require(request, "check.request_bill")
    return run_command(request, branch_id, "check.request_bill", {"check": str(check_id)},
                       lambda ctx: services.request_bill(ctx, check_id, request.data))


@api_view(["POST"])
def amendments(request, branch_id, check_id):
    branch_for(request, branch_id)
    require(request, "check.amend")
    return run_command(request, branch_id, "check.amend", {"check": str(check_id), **request.data},
                       lambda ctx: corrections.amend(ctx, check_id, request.data))


@api_view(["POST"])
def void(request, branch_id, check_id):
    branch_for(request, branch_id)
    require(request, "check.view")  # approval checked inside (manager or manager PIN)
    return run_command(request, branch_id, "check.void", {"check": str(check_id), **request.data},
                       lambda ctx: corrections.void_check(ctx, check_id, request.data))


@api_view(["POST"])
def move(request, branch_id, check_id):
    branch_for(request, branch_id)
    require(request, "check.move")
    return run_command(request, branch_id, "check.move", {"check": str(check_id), **request.data},
                       lambda ctx: corrections.move_check(ctx, check_id, request.data))


@api_view(["POST"])
def merge(request, branch_id, check_id):
    branch_for(request, branch_id)
    require(request, "check.move")
    return run_command(request, branch_id, "check.merge", {"check": str(check_id), **request.data},
                       lambda ctx: corrections.merge_checks(ctx, check_id, request.data))


@api_view(["GET", "POST"])
def discounts(request, branch_id, check_id):
    branch = branch_for(request, branch_id)
    require(request, "check.view")
    if request.method == "GET":
        from .discounts import promotions_for
        return Response({"promotions": promotions_for(branch), "limit_bp": branch.discount_limit_bp})
    from .discounts import add_discount
    return run_command(request, branch_id, "check.discount", {"check": str(check_id), **request.data},
                       lambda ctx: add_discount(ctx, check_id, request.data))


@api_view(["POST"])
def remove_discount(request, branch_id, check_id, discount_id):
    branch_for(request, branch_id)
    require(request, "check.view")
    from .discounts import remove_discount as rm
    return run_command(request, branch_id, "check.discount_remove",
                       {"check": str(check_id), "discount": str(discount_id), **request.data},
                       lambda ctx: rm(ctx, check_id, discount_id, request.data))
