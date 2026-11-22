from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.core.commands import run_command
from apps.core.scope import branch_for, require

from . import management_services as mgmt
from . import purchasing as po
from . import services


@api_view(["GET"])
def levels(request, branch_id):
    branch = branch_for(request, branch_id)
    require(request, "stock.view")
    return Response(services.levels(branch, request.query_params.get("all") == "1"))


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


@api_view(["POST"])
def items(request, branch_id):
    branch_for(request, branch_id)
    require(request, "stock.manage")
    return run_command(request, branch_id, "stock.item_create", request.data,
                       lambda ctx: mgmt.save_item(ctx, request.data))


@api_view(["POST"])
def item_update(request, branch_id, item_id):
    branch_for(request, branch_id)
    require(request, "stock.manage")
    return run_command(request, branch_id, "stock.item_update", {"item": str(item_id), **request.data},
                       lambda ctx: mgmt.save_item(ctx, request.data, item_id))


@api_view(["GET", "POST"])
def recipes(request, branch_id):
    branch = branch_for(request, branch_id)
    require(request, "stock.view")
    if request.method == "GET":
        q = request.query_params
        if q.get("target_kind"):
            return Response(mgmt.recipe_history(branch, q["target_kind"], q.get("target_code", ""),
                                                q.get("size_code", "")))
        return Response(mgmt.recipes(branch, q.get("history") == "1"))
    require(request, "stock.manage")
    return run_command(request, branch_id, "stock.recipe_publish", request.data,
                       lambda ctx: mgmt.publish_recipe(ctx, request.data))


@api_view(["GET"])
def alerts(request, branch_id):
    from apps.ops.alerts import alerts_for
    branch = branch_for(request, branch_id)
    return Response(alerts_for(branch, request.user))


def _manage_cmd(intent, fn, perm="stock.manage"):
    """POST command helper for purchasing/count views: fn(ctx, data, **url_kwargs)."""
    @api_view(["POST"])
    def view(request, branch_id, **kw):
        branch_for(request, branch_id)
        require(request, perm)
        return run_command(request, branch_id, intent, {**{k: str(v) for k, v in kw.items()}, **request.data},
                           lambda ctx: fn(ctx, request.data, **kw))
    return view


@api_view(["GET", "POST"])
def suppliers(request, branch_id):
    branch = branch_for(request, branch_id)
    require(request, "stock.view")
    if request.method == "GET":
        return Response(po.suppliers(branch))
    require(request, "stock.manage")
    return run_command(request, branch_id, "stock.supplier_save", request.data,
                       lambda ctx: po.save_supplier(ctx, request.data, request.data.get("id")))


@api_view(["GET"])
def suggestions(request, branch_id):
    branch = branch_for(request, branch_id)
    require(request, "stock.view")
    return Response(po.suggestions(branch))


@api_view(["GET", "POST"])
def purchase_orders(request, branch_id):
    branch = branch_for(request, branch_id)
    require(request, "stock.view")
    if request.method == "GET":
        return Response(po.purchase_orders(branch, request.query_params.get("status")))
    require(request, "stock.manage")
    return run_command(request, branch_id, "stock.po_create", request.data, lambda ctx: po.create_po(ctx, request.data))


@api_view(["GET", "POST"])
def purchase_order(request, branch_id, po_id):
    branch = branch_for(request, branch_id)
    require(request, "stock.view")
    if request.method == "GET":
        return Response(po.po_detail(branch, po_id))
    require(request, "stock.manage")
    return run_command(request, branch_id, "stock.po_update", {"po": str(po_id), **request.data},
                       lambda ctx: po.update_po(ctx, po_id, request.data))


receive_po = _manage_cmd("stock.po_receive", lambda ctx, data, po_id: po.receive_po(ctx, po_id, data),
                         perm="stock.view")


@api_view(["GET", "POST"])
def count_sheets(request, branch_id):
    branch = branch_for(request, branch_id)
    require(request, "stock.view")
    if request.method == "GET":
        return Response(po.count_sheets(branch))
    return run_command(request, branch_id, "stock.count_start", request.data,
                       lambda ctx: po.start_count(ctx, request.data))


@api_view(["GET", "POST"])
def count_sheet(request, branch_id, count_id):
    branch = branch_for(request, branch_id)
    require(request, "stock.view")
    if request.method == "GET":
        return Response(po.count_detail(branch, count_id))
    return run_command(request, branch_id, "stock.count_finish", {"count": str(count_id), **request.data},
                       lambda ctx: po.finish_count(ctx, count_id, request.data))


enter_counts = _manage_cmd("stock.count_enter", lambda ctx, data, count_id: po.enter_counts(ctx, count_id, data),
                           perm="stock.view")


@api_view(["POST"])
def supplier_payment(request, branch_id, supplier_id):
    branch_for(request, branch_id)
    require(request, "supplier.pay")
    return run_command(request, branch_id, "supplier.payment", {"supplier": str(supplier_id), **request.data},
                       lambda ctx: po.pay_supplier(ctx, supplier_id, request.data))
