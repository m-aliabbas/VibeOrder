"""Stock (A21 + M5 purchasing): consumption at first fire, idempotent repeat, remake consumes again, cancel after
cooking creates no positive stock, packaging at packing, receipts with moving average, transfers, counts."""
from decimal import Decimal

from apps.kitchen.models import TicketItem
from apps.stock.models import StockItem, StockLevel, StockMovement
from apps.stock.services import consume_item

from .conftest import MIXED_PIZZA, add_round, open_table

APPROVAL = {"username": "zara", "pin": "9999"}


def on_hand(branch, code):
    return StockLevel.objects.get(branch=branch, item__code=code).on_hand


def test_a21_consumption_once_remake_and_cancel(demo, waiter, cook):
    b = demo["branch"]
    cheese0, dough0 = on_hand(b, "cheese"), on_hand(b, "dough")
    chk = open_table(waiter, 6)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA])
    # Mixed large: base cheese 120 + right-half extra cheese 20 = 140 g; dough 250 + stuffed 50 once
    assert cheese0 - on_hand(b, "cheese") == 140
    assert dough0 - on_hand(b, "dough") == 300
    assert on_hand(b, "chicken-tikka") == 10000 - 50 and on_hand(b, "chicken-fajita") == 10000 - 50
    item = TicketItem.objects.get()
    # Repeat fire event: nothing new
    assert consume_item(b, item) == []
    assert cheese0 - on_hand(b, "cheese") == 140
    # Cook it, then remake: the remake consumes its own ingredients
    pizza = next(s for s in cook.get("bootstrap").json()["stations"] if s["code"] == "pizza")
    it = cook.get(f"stations/{pizza['id']}/queue").json()["items"][0]
    cook.post(f"ticket-items/{it['id']}/transitions", {"to": "ready", "expected_revision": it["revision"]})
    d = waiter.get(f"checks/{chk['check_id']}").json()
    line = d["rounds"][0]["lines"][0]
    r = waiter.post(f"checks/{chk['check_id']}/amendments", {"action": "remake", "line_id": line["id"],
                                                             "reason": "burnt", "expected_revision": d["revision"],
                                                             "approval": APPROVAL})
    assert r.status_code == 200
    assert cheese0 - on_hand(b, "cheese") == 280
    # Cancel after cooking (manager remove): financial reversal only, no positive stock
    it = cook.get(f"stations/{pizza['id']}/queue").json()["items"][0]
    cook.post(f"ticket-items/{it['id']}/transitions", {"to": "ready", "expected_revision": it["revision"]})
    d = waiter.get(f"checks/{chk['check_id']}").json()
    r = waiter.post(f"checks/{chk['check_id']}/amendments", {"action": "remove", "line_id": line["id"],
                                                             "reason": "guest left", "expected_revision": d["revision"],
                                                             "approval": APPROVAL})
    assert r.status_code == 200, r.content
    assert cheese0 - on_hand(b, "cheese") == 280
    assert not StockMovement.objects.filter(kind="reversal").exists()


def test_cancel_before_preparation_reverses_and_packaging_at_handover(demo, cashier, cook):
    b = demo["branch"]
    r = cashier.post("checks", {"service_type": "pickup", "customer_name": "Z", "customer_phone": "03009998887"})
    cid = r.json()["check_id"]
    add_round(cashier, cid, [MIXED_PIZZA, {"client_line_id": "f", "product": "fries", "quantity": 1}])
    potato0 = on_hand(b, "potato")
    d = cashier.get(f"checks/{cid}").json()
    fries = next(ln for ln in d["rounds"][0]["lines"] if ln["product"] == "fries")
    r = cashier.post(f"checks/{cid}/amendments", {"action": "remove", "line_id": fries["id"], "reason": "changed mind",
                                                  "expected_revision": d["revision"]})
    assert r.status_code == 200
    assert on_hand(b, "potato") == potato0 + 200  # compensating reversal of an item never started
    for st in cook.get("bootstrap").json()["stations"]:
        for it in cook.get(f"stations/{st['id']}/queue").json()["items"]:
            cook.post(f"ticket-items/{it['id']}/transitions", {"to": "ready", "expected_revision": it["revision"]})
    boxes = on_hand(b, "box-large")
    assert cashier.post(f"checks/{cid}/handover", {}).status_code == 200
    assert on_hand(b, "box-large") == boxes - 1


def test_receipt_moving_average_count_transfer_and_loss(demo, manager, staff_factory):
    b, b2 = demo["branch"], demo["branch2"]
    # 2 x 5 kg cheese at Rs 13,000 per bag → 10,000 g booked
    r = manager.post("stock/receipts", {"supplier": "Dairy Co", "reference": "INV-77",
                                        "lines": [{"item": "cheese", "quantity": 2, "cost_minor": 2600000}]})
    assert r.status_code == 201, r.content
    assert r.json()["lines"][0]["quantity"] == "10000.000" or Decimal(r.json()["lines"][0]["quantity"]) == 10000
    lvl = StockLevel.objects.get(branch=b, item__code="cheese")
    assert lvl.on_hand == 30000
    # (20,000 g at 240 minor/g) + (10,000 g at 260) → 246.6667 minor per gram
    assert round(lvl.avg_cost_minor, 4) == Decimal("246.6667")
    # Loss needs a reason; count posts the variance
    assert manager.post("stock/losses", {"item": "cheese", "quantity": 500}).status_code == 422
    assert manager.post("stock/losses", {"item": "cheese", "quantity": 500, "reason": "fridge failure"}).status_code == 201
    r = manager.post("stock/counts", {"lines": [{"item": "cheese", "counted": 29400}], "reason": "weekly count"})
    assert r.status_code == 201 and Decimal(str(r.json()["lines"][0]["variance"])) == Decimal(-100)
    # Transfer B1 → B2 (received by B2 manager)
    r = manager.post("stock/transfers", {"to_branch_id": str(b2.pk), "lines": [{"item": "cheese", "quantity": 1000}]})
    assert r.status_code == 201, r.content
    nadia = staff_factory_b2(b2)
    r = nadia.post(f"stock/transfers/{r.json()['transfer_id']}/receive")
    assert r.status_code == 200
    assert on_hand(b2, "cheese") == 20000 + 1000 and on_hand(b, "cheese") == 29400 - 1000
    # Cashier cannot see stock
    cashier = staff_factory("hamza", "3333", "cashier")
    assert cashier.get("stock").status_code == 403
    levels = manager.get("stock").json()["items"]
    assert any(i["code"] == "cheese" for i in levels)
    assert StockItem.objects.filter(tenant=b.tenant).count() == len(levels)


def staff_factory_b2(b2):
    from .conftest import Staff
    return Staff(b2, "nadia", "7777")
