from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.core.commands import run_command
from apps.core.scope import branch_for, require

from . import services


@api_view(["GET"])
def levels(request, branch_id):
    branch = branch_for(request, branch_id)
    require(request, "stock.view")
    return Response(services.levels(branch))


@api_view(["GET"])
def movements(request, branch_id):
    branch = branch_for(request, branch_id)
    require(request, "stock.view")
    return Response(services.movements(branch, request.query_params.get("item")))


def _cmd(name, fn):
    @api_view(["POST"])
    def view(request, branch_id):
        branch_for(request, branch_id)
        require(request, "stock.view")
        return run_command(request, branch_id, f"stock.{name}", request.data, lambda ctx: fn(ctx, request.data))
    view.__name__ = name
    return view


receive = _cmd("receive", services.receive)
loss = _cmd("loss", services.record_loss)
returns = _cmd("return", services.record_return)
count = _cmd("count", services.count)
transfer = _cmd("transfer", services.send_transfer)


@api_view(["POST"])
def receive_transfer(request, branch_id, transfer_id):
    branch_for(request, branch_id)
    require(request, "stock.view")
    return run_command(request, branch_id, "stock.transfer_in", {"transfer": str(transfer_id)},
                       lambda ctx: services.receive_transfer(ctx, transfer_id))
