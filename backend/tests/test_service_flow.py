"""Simple service flow: waiter order → kitchen ready → waiter picks up and serves; invoice; restaurant menu."""
from apps.kitchen.models import TicketItem

from .conftest import add_round, open_table

KARAHI = {"client_line_id": "k1", "product": "mutton-karahi", "variant": "half", "quantity": 1}
NAAN = {"client_line_id": "n1", "product": "naan", "quantity": 4}


def test_waiter_sees_ready_dishes_and_serves_them(demo, waiter, cook, cashier):
    chk = open_table(waiter, 5)
    _, r = add_round(waiter, chk["check_id"], [KARAHI, NAAN])
    assert r.status_code == 201, r.content
    stations = {s["code"]: s["id"] for s in cook.get("bootstrap").json()["stations"]}
    karahi = cook.get(f"stations/{stations['kitchen']}/queue").json()["items"]
    naan = cook.get(f"stations/{stations['tandoor']}/queue").json()["items"]
    assert [i["label"] for i in karahi] == ["HALF KG MUTTON KARAHI"] and naan[0]["quantity"] == 4
    assert waiter.get("pickups").json()["count"] == 0
    cook.post(f"ticket-items/{karahi[0]['id']}/transitions", {"to": "ready", "expected_revision": karahi[0]["revision"]})
    p = waiter.get("pickups").json()
    assert p["count"] == 1 and p["tables"][0]["table"] == "5"
    floor = waiter.get("floor").json()
    t5 = next(t for a in floor["areas"] for t in a["tables"] if t["label"] == "5")
    assert t5["check"]["ready_to_serve"] == 1
    # Not-ready dish cannot be served
    r = waiter.post("pickups/serve", {"item_ids": [naan[0]["id"]]})
    assert r.status_code == 409 and r.json()["code"] == "not_ready"
    r = waiter.post("pickups/serve", {"item_ids": [p["tables"][0]["items"][0]["id"]]})
    assert r.status_code == 200
    assert TicketItem.objects.get(pk=karahi[0]["id"]).status == "served"
    assert waiter.get("pickups").json()["count"] == 0
    assert cook.get("pickups").status_code == 403


def test_invoice_link_renders_paid_invoice(demo, waiter, cashier):
    from rest_framework.test import APIClient
    chk = open_table(waiter, 4)
    add_round(waiter, chk["check_id"], [KARAHI, NAAN])
    link = cashier.get(f"checks/{chk['check_id']}/invoice").json()
    page = APIClient().get(link["path"]).content.decode()
    assert "Pro-forma bill" in page and "Mutton Karahi" in page and "2,560" in page  # 2,400 + 4 x 40
    cashier.post("shifts", {"opening_float_minor": 0})
    d = cashier.get(f"checks/{chk['check_id']}").json()
    t = cashier.post(f"checks/{chk['check_id']}/tenders", {"method": "cash", "amount_minor": 256000,
                                                           "cash_received_minor": 300000,
                                                           "expected_revision": d["revision"]}).json()
    cashier.post(f"checks/{chk['check_id']}/close", {"expected_revision": t["revision"]})
    page = APIClient().get(link["path"]).content.decode()
    assert "Tax invoice" in page and "PAID" in page and "B1-" in page and "440" in page  # change 440
    assert APIClient().get(link["path"] + "x").status_code == 404


def test_recipe_consumes_mutton(demo, waiter):
    from apps.stock.models import StockLevel
    b = demo["branch"]
    before = StockLevel.objects.get(branch=b, item__code="mutton").on_hand
    chk = open_table(waiter, 3)
    add_round(waiter, chk["check_id"], [{**KARAHI, "variant": "full", "quantity": 2}])
    assert before - StockLevel.objects.get(branch=b, item__code="mutton").on_hand == 2000
