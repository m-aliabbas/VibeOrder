"""Purchase orders (suggest → order → partial receive → close short) and count sheets (count while selling)."""
from decimal import Decimal

from apps.stock.models import PurchaseReceipt, StockLevel, StockMovement

from .conftest import add_round, open_table


def on_hand(branch, code):
    return StockLevel.objects.get(branch=branch, item__code=code).on_hand


def test_suggest_order_partial_receive_and_close_short(demo, manager, waiter):
    b = demo["branch"]
    # Mozzarella: 20,000 g opening, reorder 5,000 → lose 16,000 → 4,000 g left (low)
    manager.post("stock/losses", {"item": "cheese", "quantity": 16000, "reason": "fridge failure"})
    manager.post(f"stock/items/{_item_id(manager, 'cheese')}", {"par_level": 15000})
    s = manager.get("stock/suggestions").json()["suggestions"]
    cheese = next(x for x in s if x["item"] == "cheese")
    # need 15,000 − 4,000 = 11,000 g → 3 bags of 5 kg (rounded up) = 15,000 g
    assert cheese["suggested_packs"] == 3 and Decimal(cheese["suggested_quantity"]) == 15000
    # Waiter cannot order
    assert waiter.post("stock/purchase-orders", {"lines": [{"item": "cheese", "quantity": 3}]}).status_code == 403
    r = manager.post("stock/purchase-orders", {"supplier": "Dairy Co", "expected_on": "2026-11-21",
                                               "lines": [{"item": "cheese", "quantity": 3, "expected_cost_minor": 3900000},
                                                         {"item": "olives", "quantity": 2}]})
    assert r.status_code == 201, r.content
    po = r.json()
    assert po["number"] == "PO-B1-0001" and po["status"] == "draft"
    assert "Mozzarella: 3 × 5 kg bag" in po["message"] and "Deliver by" in po["message"]
    # No stock moves on ordering
    assert on_hand(b, "cheese") == 4000
    # Edit draft with a stale revision → 409; then send
    assert manager.post(f"stock/purchase-orders/{po['id']}", {"action": "send", "expected_revision": 9}).status_code == 409
    po = manager.post(f"stock/purchase-orders/{po['id']}", {"action": "send", "expected_revision": po["revision"]}).json()
    assert po["status"] == "sent"
    # Sent order counts as on-order → no more suggestion for cheese
    assert not any(x["item"] == "cheese" for x in manager.get("stock/suggestions").json()["suggestions"])
    # First delivery: 2 bags of cheese only
    cheese_line = next(ln for ln in po["lines"] if ln["item"] == "cheese")
    olives_line = next(ln for ln in po["lines"] if ln["item"] == "olives")
    r = manager.post(f"stock/purchase-orders/{po['id']}/receive", {
        "reference": "DN-55", "expected_revision": po["revision"],
        "lines": [{"line_id": cheese_line["id"], "quantity": 2, "cost_minor": 2600000}]})
    assert r.status_code == 201, r.content
    po = r.json()
    assert po["status"] == "partial" and on_hand(b, "cheese") == 14000
    assert PurchaseReceipt.objects.get(pk=po["receipt_id"]).purchase_order_id is not None
    assert Decimal(next(ln for ln in po["lines"] if ln["item"] == "cheese")["outstanding"]) == 5000
    # Cannot cancel once goods arrived; close short with a reason instead
    r = manager.post(f"stock/purchase-orders/{po['id']}", {"action": "cancel", "reason": "x"})
    assert r.status_code == 409 and r.json()["code"] == "has_receipts"
    r = manager.post(f"stock/purchase-orders/{po['id']}", {"action": "close", "reason": "supplier out of olives"})
    assert r.status_code == 200 and r.json()["status"] == "closed"
    # Closed order: nothing more can be received
    r = manager.post(f"stock/purchase-orders/{po['id']}/receive",
                     {"lines": [{"line_id": olives_line["id"], "quantity": 1}]})
    assert r.status_code == 409
    assert [o["number"] for o in manager.get("stock/purchase-orders", status="open").json()["orders"]] == []


def test_receive_whole_order_marks_received(demo, manager):
    po = manager.post("stock/purchase-orders", {"lines": [{"item": "potato", "quantity": 4}], "send": True}).json()
    ln = po["lines"][0]
    r = manager.post(f"stock/purchase-orders/{po['id']}/receive",
                     {"lines": [{"line_id": ln["id"], "quantity": 4, "cost_minor": 450000}]})
    assert r.status_code == 201 and r.json()["status"] == "received"
    assert Decimal(r.json()["lines"][0]["received"]) == 10000  # 4 × 2.5 kg bags


def test_count_sheet_while_selling_posts_variance_at_count_time(demo, manager, waiter, staff_factory):
    b = demo["branch"]
    r = manager.post("stock/count-sheets", {"items": ["potato", "oil", "cola-500"], "note": "Thursday close"})
    assert r.status_code == 201, r.content
    sc = r.json()
    assert sc["number"] == "CT-B1-0001" and sc["total"] == 3 and sc["counted"] == 0
    # Only one open count at a time
    assert manager.post("stock/count-sheets", {}).status_code == 409
    # Count potato: theoretical 20,000, physically 19,500
    sc = manager.post(f"stock/count-sheets/{sc['id']}/lines",
                      {"lines": [{"item": "potato", "counted": 19500}, {"item": "cola-500", "counted": 240}]}).json()
    potato = next(ln for ln in sc["lines"] if ln["item"] == "potato")
    assert Decimal(potato["expected"]) == 20000 and Decimal(potato["variance"]) == -500
    assert potato["variance_value_minor"] == -500 * 45
    # Service continues after counting: fries consume 200 g potato
    chk = open_table(waiter, 4)
    add_round(waiter, chk["check_id"], [{"client_line_id": "f", "product": "fries", "variant": "regular",
                                         "quantity": 1}])
    assert on_hand(b, "potato") == 19800
    # A non-manager cannot approve without a manager PIN
    clerk = staff_factory("hamza", "hamza123", "cashier")
    r = clerk.post(f"stock/count-sheets/{sc['id']}", {"action": "approve"})
    assert r.status_code == 403
    r = manager.post(f"stock/count-sheets/{sc['id']}", {"action": "approve", "expected_revision": sc["revision"]})
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "approved" and r.json()["approved_by"]
    # Variance posted relative to the snapshot (−500), not "set to 19,500" (which would wipe the 200 g sale)
    assert on_hand(b, "potato") == 19300
    assert on_hand(b, "cola-500") == 240  # counted = expected → no movement
    assert StockMovement.objects.filter(kind="count", item__code="potato").get().quantity == -500
    assert not StockMovement.objects.filter(kind="count", item__code="cola-500").exists()
    # Approved sheet is closed
    assert manager.post(f"stock/count-sheets/{sc['id']}/lines",
                        {"lines": [{"item": "oil", "counted": 1}]}).status_code == 409
    assert manager.get("stock/count-sheets").json()["counts"][0]["status"] == "approved"


def _item_id(staff, code):
    return next(i["id"] for i in staff.get("stock").json()["items"] if i["code"] == code)
