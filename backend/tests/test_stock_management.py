"""Stock item master, reorder levels, versioned recipe management, sold-out list and the alert centre."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.kitchen.models import PrintJob, ProductionTicket
from apps.stock.models import Recipe, StockLevel, StockMovement

from .conftest import MIXED_PIZZA, add_round, open_table


def alert_ids(staff):
    r = staff.get("alerts")
    assert r.status_code == 200, r.content
    return {a["id"]: a for a in r.json()["alerts"]}


def test_item_create_edit_reorder_level_and_unit_lock(demo, manager, waiter):
    r = manager.post("stock/items", {"code": "Garlic Sauce", "name": "Garlic sauce", "base_unit": "ml",
                                     "purchase_unit": "1 L tub", "purchase_factor": 1000, "reorder_level": 500})
    assert r.status_code == 201, r.content
    item = r.json()["item"]
    assert item["code"] == "garlic-sauce" and item["status"] == "out" and Decimal(item["reorder_level"]) == 500
    # Duplicate code and waiter permission
    assert manager.post("stock/items", {"code": "garlic-sauce", "name": "x", "base_unit": "ml"}).status_code == 409
    assert waiter.post("stock/items", {"code": "y", "name": "y", "base_unit": "g"}).status_code == 403
    # Receive 2 tubs → 2000 ml, above reorder → ok
    assert manager.post("stock/receipts", {"lines": [{"item": "garlic-sauce", "quantity": 2,
                                                      "cost_minor": 80000}]}).status_code == 201
    row = next(i for i in manager.get("stock").json()["items"] if i["code"] == "garlic-sauce")
    assert row["status"] == "ok" and Decimal(row["on_hand"]) == 2000
    # Edit with a stale revision conflicts; with the right one it saves; unit is locked after movements
    r = manager.post(f"stock/items/{item['id']}", {"name": "Garlic mayo", "expected_revision": 99})
    assert r.status_code == 409
    r = manager.post(f"stock/items/{item['id']}", {"name": "Garlic mayo", "reorder_level": 2500,
                                                   "expected_revision": item["revision"]})
    assert r.status_code == 200, r.content
    assert r.json()["item"]["name"] == "Garlic mayo" and r.json()["item"]["status"] == "low"
    r = manager.post(f"stock/items/{item['id']}", {"base_unit": "g"})
    assert r.status_code == 409 and r.json()["code"] == "unit_locked"


def test_recipe_new_version_changes_consumption_and_keeps_history(demo, manager, waiter):
    b = demo["branch"]
    data = manager.get("stock/recipes").json()
    fries = next(r for r in data["recipes"] if r["target_code"] == "fries" and r["size_code"] == "regular")
    assert fries["version"] == 1 and fries["cost_minor"] == 200 * 45 + 20 * 55  # avg cost per base unit
    assert fries["cost_missing"] is False and fries["label"] == "Fries"
    # Waiter (stock.view denied) cannot read or publish
    assert waiter.get("stock/recipes").status_code == 403
    # Old recipe consumed by an order placed before the change
    chk = open_table(waiter, 3)
    add_round(waiter, chk["check_id"], [{"client_line_id": "a", "product": "fries", "variant": "regular",
                                         "quantity": 1}])
    v1 = Recipe.objects.get(target_code="fries", size_code="regular", version=1)
    assert StockMovement.objects.filter(recipe=v1, item__code="potato", quantity=-200).exists()
    # Publish v2 (250 g potato, 25 ml oil); stale expected_version is refused
    body = {"target_kind": "product", "target_code": "fries", "size_code": "regular", "note": "bigger portion",
            "components": [{"item": "potato", "quantity": 250}, {"item": "oil", "quantity": 25}]}
    assert manager.post("stock/recipes", {**body, "expected_version": 3}).status_code == 409
    r = manager.post("stock/recipes", {**body, "expected_version": 1})
    assert r.status_code == 201, r.content
    assert r.json()["recipe"]["version"] == 2
    v1.refresh_from_db()
    assert not v1.is_active  # retired, not edited
    potato0 = StockLevel.objects.get(branch=b, item__code="potato").on_hand
    add_round(waiter, chk["check_id"], [{"client_line_id": "b", "product": "fries", "variant": "regular",
                                         "quantity": 1}])
    assert potato0 - StockLevel.objects.get(branch=b, item__code="potato").on_hand == 250
    hist = manager.get("stock/recipes", target_kind="product", target_code="fries", size_code="regular").json()
    assert [v["version"] for v in hist["versions"]] == [2, 1] and hist["versions"][0]["note"] == "bigger portion"
    # Duplicate ingredient refused
    r = manager.post("stock/recipes", {**body, "components": [{"item": "oil", "quantity": 1},
                                                              {"item": "oil", "quantity": 2}]})
    assert r.status_code == 422


def test_missing_recipe_listed_and_retire(demo, manager):
    from apps.orders.services import _MENU_CACHE
    _MENU_CACHE.clear()  # other tests add ad-hoc products to the shared demo release
    data = manager.get("stock/recipes").json()
    assert data["missing"] == []  # demo menu is fully costed
    targets = {(t["target_kind"], t["target_code"]) for t in data["targets"]}
    assert ("base", "pizza") in targets and ("option", "olives") in targets
    r = manager.post("stock/recipes", {"target_kind": "product", "target_code": "wings", "size_code": "",
                                       "components": [], "note": "supplier change"})
    assert r.status_code == 200 and r.json()["retired"]
    data = manager.get("stock/recipes").json()
    assert [m["target_code"] for m in data["missing"]] == ["wings"]
    alerts = alert_ids(manager)
    assert alerts["stock:recipes_missing"]["severity"] == "info"


def test_alerts_low_and_out_of_stock_offer_sellout(demo, manager, waiter, cashier):
    # Olives: 3,000 g opening, reorder 750 g. Lose 2,300 → 700 g (low), then the rest → out
    approval = {}
    r = manager.post("stock/losses", {"item": "olives", "quantity": 2300, "reason": "spoiled", **approval})
    assert r.status_code == 201, r.content
    a = alert_ids(manager)["stock:low:olives"]
    assert a["severity"] == "warning" and a["action"]["type"] == "sellout"
    assert [t["code"] for t in a["action"]["targets"]] == ["olives"]
    manager.post("stock/losses", {"item": "olives", "quantity": 700, "reason": "spoiled"})
    alerts = alert_ids(manager)
    assert "stock:low:olives" not in alerts and alerts["stock:out:olives"]["severity"] == "critical"
    # Mark the option sold out → it disappears from "still on sale" and the sold-out list shows who/why
    r = manager.post("availability", {"target_kind": "option", "target_code": "olives", "available": False,
                                      "reason": "out of olives"})
    assert r.status_code == 200
    assert alert_ids(manager)["stock:out:olives"]["action"]["type"] == "receive"
    so = manager.get("availability").json()["sold_out"]
    assert so[0]["target_code"] == "olives" and so[0]["reason"] == "out of olives" and so[0]["actor"]
    # Receiving clears the alert
    manager.post("stock/receipts", {"lines": [{"item": "olives", "quantity": 2, "cost_minor": 40000}]})
    assert not any(k.endswith(":olives") for k in alert_ids(manager))
    # Waiters see no stock alerts; cashier sees none either (no stock.view)
    assert all(not k.startswith("stock:") for k in alert_ids(cashier))
    assert alert_ids(waiter) == {}


def test_alerts_late_kot_unacked_change_and_print_problems(demo, waiter, cook, cashier, manager):
    chk = open_table(waiter, 5)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA])
    t = ProductionTicket.objects.get(kind="original")
    assert not any(k.startswith("kitchen:") for k in alert_ids(cook))
    ProductionTicket.objects.filter(pk=t.pk).update(due_at=timezone.now() - timedelta(minutes=12))
    a = alert_ids(cook)[f"kitchen:late:{t.pk}"]
    assert a["severity"] == "critical" and "KOT" in a["title"] and a["ref"]["minutes_late"] >= 12
    # Bump the item → alert clears
    pizza = next(s for s in cook.get("bootstrap").json()["stations"] if s["code"] == "pizza")
    it = cook.get(f"stations/{pizza['id']}/queue").json()["items"][0]
    cook.post(f"ticket-items/{it['id']}/transitions", {"to": "ready", "expected_revision": it["revision"]})
    assert f"kitchen:late:{t.pk}" not in alert_ids(cook)
    # Unacknowledged change ticket older than the grace period
    ProductionTicket.objects.filter(pk=t.pk).update(kind="change", acknowledged_at=None,
                                                    created_at=timezone.now() - timedelta(minutes=5))
    assert f"kitchen:unacked:{t.pk}" in alert_ids(cook)
    # Print: KOT with an unknown outcome → critical with a reprint action; a printed copy clears it
    job = PrintJob.objects.filter(kind="kitchen").first()
    PrintJob.objects.filter(pk=job.pk).update(status="outcome_unknown")
    a = alert_ids(cashier)[f"print:{job.pk}"]
    assert a["severity"] == "critical" and a["action"] == {"type": "reprint", "job": str(job.pk)}
    r = cashier.post(f"print-jobs/{job.pk}/reprints", {"reason": "not printed"})
    assert r.status_code in (200, 201), r.content
    PrintJob.objects.filter(original=job).update(status="handed_to_spooler")
    assert f"print:{job.pk}" not in alert_ids(cashier)
    # Queued jobs nobody picks up → worker alert
    PrintJob.objects.filter(pk=job.pk).update(status="queued", created_at=timezone.now() - timedelta(minutes=5))
    assert "print:stuck" in alert_ids(manager)
    # Waiters see none of this
    assert alert_ids(waiter) == {}
