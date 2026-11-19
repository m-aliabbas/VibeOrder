"""M1 vertical slice over HTTP: pair, login, open check, quote, append, kitchen, print, cash, close."""
from pathlib import Path

from apps.core.models import AuditEntry, SyncOutbox
from apps.kitchen.models import PrintJob
from apps.kitchen.services import process_one

from .conftest import MIXED_PIZZA, add_round, open_table


def open_drawer(cashier, float_minor=500000):
    r = cashier.post("shifts", {"opening_float_minor": float_minor})
    assert r.status_code == 201, r.content
    return r.json()


def test_full_dine_in_flow(demo, waiter, cashier, cook, settings):
    boot = waiter.get("bootstrap").json()
    assert boot["roles"] == ["waiter"] and boot["menu_release"]["version"] == 1

    chk = open_table(waiter, 6)
    q, r = add_round(waiter, chk["check_id"], [MIXED_PIZZA, {"client_line_id": "L2", "product": "cola-500", "quantity": 2}])
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["round_total_minor"] == 190000 + 24000
    assert body["revision"] == 2 and len(body["ticket_ids"]) == 2  # pizza + packing

    # Kitchen sees only its station
    pizza_station = next(s for s in boot["stations"] if s["code"] == "pizza")
    queue = cook.get(f"stations/{pizza_station['id']}/queue").json()
    assert len(queue["items"]) == 1
    item = queue["items"][0]
    assert item["check"]["table"] == "6" and "LEFT half: Chicken Tikka" in item["detail"]
    r = cook.post(f"ticket-items/{item['id']}/transitions", {"to": "preparing", "expected_revision": item["revision"]})
    assert r.status_code == 200, r.content
    r = cook.post(f"ticket-items/{item['id']}/transitions", {"to": "ready", "expected_revision": r.json()["revision"]})
    assert r.json()["status"] == "ready"

    # Kitchen ticket printed through spooler fake
    assert process_one() is not None
    job = PrintJob.objects.get(kind="kitchen")
    assert job.status == "handed_to_spooler"
    text = Path(job.spooler_ref).read_text()
    assert "TABLE 6" in text and "RIGHT half: Chicken Fajita + Extra cheese" in text

    # Cashier settles in cash with change
    open_drawer(cashier)
    detail = cashier.get(f"checks/{chk['check_id']}").json()
    assert detail["balance_minor"] == 214000
    r = cashier.post(f"checks/{chk['check_id']}/tenders", {
        "method": "cash", "amount_minor": 214000, "cash_received_minor": 250000, "expected_revision": detail["revision"]})
    assert r.status_code == 201, r.content
    t = r.json()
    assert t["change_minor"] == 36000 and t["balance_minor"] == 0
    r = cashier.post(f"checks/{chk['check_id']}/close", {"expected_revision": t["revision"]})
    assert r.status_code == 200, r.content
    assert r.json()["receipt_no"] == "B1-000001"
    # Table released
    floor = waiter.get("floor").json()
    t6 = [t for a in floor["areas"] for t in a["tables"] if t["label"] == "6"][0]
    assert not t6["occupied"]
    # Every step audited and exported in stream order
    assert AuditEntry.objects.filter(action="check.close").count() == 1
    positions = list(SyncOutbox.objects.order_by("stream_position").values_list("stream_position", flat=True))
    assert positions == list(range(1, len(positions) + 1))


def test_cash_change_fixture_and_split_tender(demo, waiter, cashier):
    """1,900 bill: 1,000 cash + 900 external card -> drawer +1,000; and 2,000 received for 1,900 -> change 100."""
    open_drawer(cashier, 0)
    chk = open_table(waiter, 1)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA])
    rev = cashier.get(f"checks/{chk['check_id']}").json()["revision"]
    r = cashier.post(f"checks/{chk['check_id']}/tenders", {"method": "cash", "amount_minor": 100000, "expected_revision": rev})
    assert r.json()["balance_minor"] == 90000
    r = cashier.post(f"checks/{chk['check_id']}/tenders", {"method": "external_card", "amount_minor": 90000,
                                                           "external_ref": "TERM-123", "expected_revision": r.json()["revision"]})
    assert r.status_code == 201 and r.json()["balance_minor"] == 0
    shift = cashier.get("shifts").json()["shift"]
    assert shift is not None

    chk2 = open_table(waiter, 2)
    add_round(waiter, chk2["check_id"], [MIXED_PIZZA])
    rev = cashier.get(f"checks/{chk2['check_id']}").json()["revision"]
    r = cashier.post(f"checks/{chk2['check_id']}/tenders", {"method": "cash", "amount_minor": 190000,
                                                            "cash_received_minor": 200000, "expected_revision": rev})
    assert r.json()["change_minor"] == 10000
    from apps.cash.models import DrawerShift
    from apps.cash.services import expected_drawer
    assert expected_drawer(DrawerShift.objects.get()) == 100000 + 190000


def test_overpayment_and_card_without_reference_rejected(demo, waiter, cashier):
    open_drawer(cashier)
    chk = open_table(waiter, 3)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA])
    rev = cashier.get(f"checks/{chk['check_id']}").json()["revision"]
    r = cashier.post(f"checks/{chk['check_id']}/tenders", {"method": "cash", "amount_minor": 999999, "expected_revision": rev})
    assert r.status_code == 422 and r.json()["code"] == "overpayment"
    r = cashier.post(f"checks/{chk['check_id']}/tenders", {"method": "external_card", "amount_minor": 1000, "expected_revision": rev})
    assert r.status_code == 422


def test_waiter_cannot_take_payment(demo, waiter):
    chk = open_table(waiter, 4)
    r = waiter.post(f"checks/{chk['check_id']}/tenders", {"method": "cash", "amount_minor": 100, "expected_revision": 1})
    assert r.status_code == 403


def test_held_course_fires_only_when_released(demo, waiter, cook):
    chk = open_table(waiter, 5)
    held = {**MIXED_PIZZA, "client_line_id": "H1", "fire": "hold"}
    _, r = add_round(waiter, chk["check_id"], [held])
    assert r.json()["ticket_ids"] == [] and r.json()["production_status"] == "held"
    boot = cook.get("bootstrap").json()
    pizza = next(s for s in boot["stations"] if s["code"] == "pizza")
    assert cook.get(f"stations/{pizza['id']}/queue").json()["items"] == []
    line_id = r.json()["accepted_lines"][0]
    r = waiter.post(f"checks/{chk['check_id']}/fire", {"line_ids": [line_id]})
    assert r.status_code == 200, r.content
    assert len(cook.get(f"stations/{pizza['id']}/queue").json()["items"]) == 1


def test_kds_page_served(client, demo):
    r = client.get("/kds/")
    assert r.status_code == 200 and b"VibeOrder Kitchen" in r.content


def test_health_endpoints(client, db):
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/readyz").json()["database"] == "ok"
