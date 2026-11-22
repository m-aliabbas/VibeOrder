"""M2 corrections: amendments with kitchen change tickets (A09), remake, void, move and merge (A22)."""
from pathlib import Path

from apps.kitchen.models import PrintJob, TicketItem
from apps.kitchen.services import process_one
from apps.orders.models import Check, CheckAdjustment

from .conftest import MIXED_PIZZA, add_round, open_table, table_id

PIZZA_NO_CHEESE = {**MIXED_PIZZA, "client_line_id": "L-phone-1b", "options": []}


def pizza_station(staff):
    return next(s for s in staff.get("bootstrap").json()["stations"] if s["code"] == "pizza")["id"]


def check(staff, cid):
    return staff.get(f"checks/{cid}").json()


def active_line(detail, product="mixed-pizza"):
    return next(ln for r in detail["rounds"] for ln in r["lines"] if ln["status"] == "active" and ln["product"] == product)


def amend(staff, cid, payload):
    d = check(staff, cid)
    return staff.post(f"checks/{cid}/amendments", {"expected_revision": d["revision"], **payload})


def modify(staff, cid, line_id, item, reason="guest changed mind"):
    q = staff.post("quotes", {"items": [item]}, key=False).json()
    return amend(staff, cid, {"action": "modify", "line_id": line_id, "item": item, "quote_id": q["quote_id"],
                              "reason": reason})


def test_a09_amend_half_after_cook_starts_then_remake(demo, waiter, cook, manager):
    chk = open_table(waiter, 6)
    _, r = add_round(waiter, chk["check_id"], [MIXED_PIZZA])
    assert r.status_code == 201
    st = pizza_station(cook)
    item = cook.get(f"stations/{st}/queue").json()["items"][0]
    r = cook.post(f"ticket-items/{item['id']}/transitions", {"to": "preparing", "expected_revision": item["revision"]})
    assert r.status_code == 200

    line = active_line(check(waiter, chk["check_id"]))
    r = modify(waiter, chk["check_id"], line["id"], PIZZA_NO_CHEESE)
    assert r.status_code == 200, r.content
    body = r.json()
    assert "Table 6, pizza 1, RIGHT half: remove Extra cheese" in body["description"]
    assert body["total_minor"] == 190000 - 10000  # explicit charged change only: extra cheese large half removed
    q = cook.get(f"stations/{st}/queue").json()
    # One live pizza, still in preparation (progress kept), carrying the delta
    assert len(q["items"]) == 1 and q["items"][0]["status"] == "preparing"
    assert "remove Extra cheese" in q["items"][0]["change_note"]
    assert len(q["changes"]) == 1 and "RIGHT half: remove Extra cheese" in q["changes"][0]["note"]
    # Kitchen acknowledges; change leaves the change list
    r = cook.post(f"tickets/{q['changes'][0]['id']}/acknowledge")
    assert r.status_code == 200, r.content
    assert cook.get(f"stations/{st}/queue").json()["changes"] == []
    # Printed change ticket says what changed
    while process_one():
        pass
    change_job = PrintJob.objects.filter(ticket__kind="change").get()
    assert "RIGHT half: remove Extra cheese" in " ".join(Path(change_job.spooler_ref).read_text().split())

    # Original trail retained
    d = check(waiter, chk["check_id"])
    lines = [ln for rr in d["rounds"] for ln in rr["lines"]]
    assert [ln["status"] for ln in lines] == ["voided", "active"]
    assert lines[1]["amends_id"] == lines[0]["id"]

    # After cooking: modify refused, remake needs a manager and charges nothing
    item = cook.get(f"stations/{st}/queue").json()["items"][0]
    cook.post(f"ticket-items/{item['id']}/transitions", {"to": "ready", "expected_revision": item["revision"]})
    new_line = lines[1]
    assert modify(waiter, chk["check_id"], new_line["id"], MIXED_PIZZA).json()["code"] == "already_cooked"
    r = amend(waiter, chk["check_id"], {"action": "remake", "line_id": new_line["id"], "reason": "dropped"})
    assert r.status_code == 403 and r.json()["code"] == "approval_required"
    r = amend(waiter, chk["check_id"], {"action": "remake", "line_id": new_line["id"], "reason": "dropped",
                                        "approval": {"username": "zara", "password": "zara123"}})
    assert r.status_code == 200, r.content
    assert r.json()["total_minor"] == 180000  # payable unchanged by remake
    q = cook.get(f"stations/{st}/queue").json()
    assert len(q["items"]) == 1 and q["items"][0]["is_remake"] and q["items"][0]["status"] == "queued"
    adj = CheckAdjustment.objects.get(kind="remake")
    assert adj.amount_delta_minor == 0 and adj.discarded and adj.approved_by.username == "zara"


def test_remove_queued_line_cancels_production(demo, waiter, cook):
    chk = open_table(waiter, 3)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA, {"client_line_id": "c", "product": "cola-500", "quantity": 2}])
    line = active_line(check(waiter, chk["check_id"]), "cola-500")
    r = amend(waiter, chk["check_id"], {"action": "remove", "line_id": line["id"], "reason": "not wanted"})
    assert r.status_code == 200, r.content
    assert r.json()["total_minor"] == 190000
    packing = next(s for s in cook.get("bootstrap").json()["stations"] if s["code"] == "packing")["id"]
    q = cook.get(f"stations/{packing}/queue").json()
    assert q["items"] == [] and "CANCEL 2 x" in q["changes"][0]["note"]
    # Removing again is a conflict, not a double reversal
    r = amend(waiter, chk["check_id"], {"action": "remove", "line_id": line["id"], "reason": "again"})
    assert r.status_code == 409 and r.json()["code"] == "line_not_active"


def test_remove_cooked_line_needs_manager_and_records_discard(demo, waiter, cook):
    chk = open_table(waiter, 4)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA])
    st = pizza_station(cook)
    item = cook.get(f"stations/{st}/queue").json()["items"][0]
    cook.post(f"ticket-items/{item['id']}/transitions", {"to": "ready", "expected_revision": item["revision"]})
    line = active_line(check(waiter, chk["check_id"]))
    r = amend(waiter, chk["check_id"], {"action": "remove", "line_id": line["id"], "reason": "complaint"})
    assert r.json()["code"] == "approval_required"
    r = amend(waiter, chk["check_id"], {"action": "remove", "line_id": line["id"], "reason": "complaint",
                                        "approval": {"username": "zara", "password": "zara123"}})
    assert r.status_code == 200 and r.json()["total_minor"] == 0
    assert CheckAdjustment.objects.get(kind="remove").discarded


def test_stale_amendment_has_no_side_effects(demo, waiter):
    chk = open_table(waiter, 5)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA])
    line = active_line(check(waiter, chk["check_id"]))
    r = waiter.post(f"checks/{chk['check_id']}/amendments",
                    {"expected_revision": 1, "action": "remove", "line_id": line["id"], "reason": "x"})
    assert r.status_code == 409 and r.json()["code"] == "stale_revision"
    assert CheckAdjustment.objects.count() == 0


def test_void_requires_manager_and_food_resolution(demo, waiter, cook, manager):
    chk = open_table(waiter, 7)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA])
    st = pizza_station(cook)
    item = cook.get(f"stations/{st}/queue").json()["items"][0]
    cook.post(f"ticket-items/{item['id']}/transitions", {"to": "preparing", "expected_revision": item["revision"]})
    d = check(waiter, chk["check_id"])
    r = waiter.post(f"checks/{chk['check_id']}/void", {"expected_revision": d["revision"], "reason": "walkout"})
    assert r.status_code == 403
    r = manager.post(f"checks/{chk['check_id']}/void", {"expected_revision": d["revision"], "reason": "walkout"})
    assert r.status_code == 422 and r.json()["code"] == "food_resolution_required"
    r = manager.post(f"checks/{chk['check_id']}/void", {"expected_revision": d["revision"], "reason": "walkout",
                                                        "food_resolution": "discard"})
    assert r.status_code == 200, r.content
    assert Check.objects.get(pk=chk["check_id"]).status == "voided"
    q = cook.get(f"stations/{st}/queue").json()
    assert q["items"] == [] and "VOID CHECK" in q["changes"][0]["note"]
    # Table free again
    assert open_table(waiter, 7)["check_id"] != chk["check_id"]


def test_a22_held_course_move_merge_then_split(demo, waiter, waiter2, cook, cashier, manager):
    # Table 6: pizza fired now, fries held
    a = open_table(waiter, 6)
    add_round(waiter, a["check_id"], [MIXED_PIZZA, {"client_line_id": "f", "product": "fries", "quantity": 1,
                                                    "fire": "hold"}])
    # Move table 6 -> 8; kitchen sees the new table
    d = check(waiter, a["check_id"])
    r = waiter.post(f"checks/{a['check_id']}/move", {"table_id": table_id(8), "expected_revision": d["revision"]})
    assert r.status_code == 200, r.content
    st = pizza_station(cook)
    q = cook.get(f"stations/{st}/queue").json()
    assert q["items"][0]["check"]["table"] == "8" and "Table 6 -> Table 8" in q["changes"][0]["note"]
    # Moving onto an occupied table conflicts
    b = open_table(waiter2, 2)
    add_round(waiter2, b["check_id"], [{"client_line_id": "c", "product": "cola-500", "quantity": 1}])
    d = check(waiter, a["check_id"])
    r = waiter.post(f"checks/{a['check_id']}/move", {"table_id": table_id(2), "expected_revision": d["revision"]})
    assert r.status_code == 409 and r.json()["code"] == "table_occupied"

    # Merge table 9 into table 8: waiter needs a manager
    da, db = check(waiter, a["check_id"]), check(waiter, b["check_id"])
    payload = {"source_check_id": b["check_id"], "expected_revision": da["revision"],
               "source_expected_revision": db["revision"], "reason": "family joined"}
    r = waiter.post(f"checks/{a['check_id']}/merge", payload)
    assert r.status_code == 403 and r.json()["code"] == "approval_required"
    r = waiter.post(f"checks/{a['check_id']}/merge", {**payload, "approval": {"username": "zara", "password": "zara123"}})
    assert r.status_code == 200, r.content
    merged = check(waiter, a["check_id"])
    assert Check.objects.get(pk=b["check_id"]).status == "merged"
    assert [r_["number"] for r_ in merged["rounds"]] == [1, 2]
    assert merged["total_minor"] == 190000 + 35000 + 12000
    floor = waiter.get("floor").json()
    t9 = [t for ar in floor["areas"] for t in ar["tables"] if t["label"] == "2"][0]
    assert not t9["occupied"]

    # Only the released course fires
    fries = active_line(merged, "fries")
    assert fries["fire_state"] == "held"
    r = waiter.post(f"checks/{a['check_id']}/fire", {"line_ids": [fries["id"]]})
    assert r.status_code == 200
    assert TicketItem.objects.filter(line_id=fries["id"]).count() == 1

    # Split by item: pizza paid by card, rest cash
    cashier.post("shifts", {"opening_float_minor": 500000})
    d = check(cashier, a["check_id"])
    pizza = active_line(d)
    r = cashier.post(f"checks/{a['check_id']}/tenders", {
        "method": "external_card", "amount_minor": pizza["charge_minor"], "external_ref": "T1",
        "allocations": [{"line_id": pizza["id"], "amount_minor": pizza["charge_minor"]}],
        "expected_revision": d["revision"]})
    assert r.status_code == 201, r.content
    r = cashier.post(f"checks/{a['check_id']}/tenders", {
        "method": "cash", "amount_minor": r.json()["balance_minor"], "cash_received_minor": 50000,
        "expected_revision": r.json()["revision"]})
    assert r.status_code == 201, r.content
    # After a payment, merging is refused
    c = open_table(waiter, 1)
    d = check(waiter, a["check_id"])
    r = manager.post(f"checks/{c['check_id']}/merge", {
        "source_check_id": a["check_id"], "expected_revision": check(waiter, c["check_id"])["revision"],
        "source_expected_revision": d["revision"], "reason": "x"})
    assert r.status_code == 422 and r.json()["code"] == "merge_after_payment"


def test_payer_receipt_lists_only_that_payers_items(demo, waiter, cashier):
    chk = open_table(waiter, 6)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA, {"client_line_id": "f", "product": "fries", "quantity": 1}])
    cashier.post("shifts", {"opening_float_minor": 0})
    d = check(cashier, chk["check_id"])
    pizza = active_line(d)
    r = cashier.post(f"checks/{chk['check_id']}/tenders", {
        "method": "cash", "amount_minor": pizza["charge_minor"], "cash_received_minor": 200000,
        "allocations": [{"line_id": pizza["id"], "amount_minor": pizza["charge_minor"]}],
        "expected_revision": d["revision"]})
    assert r.status_code == 201
    j = cashier.post(f"tenders/{r.json()['tender_id']}/receipt")
    assert j.status_code == 201 and j.json()["kind"] == "payer_receipt"
    content = PrintJob.objects.get(kind="payer_receipt").content
    assert "Half & Half Pizza" in content and "Fries" not in content and "Change" in content
    assert waiter.post(f"tenders/{r.json()['tender_id']}/receipt").status_code == 403


def test_split_two_pizzas_then_change_one(demo, waiter, cook):
    from decimal import Decimal

    from apps.stock.models import StockLevel
    b = demo["branch"]
    cheese0 = StockLevel.objects.get(branch=b, item__code="cheese").on_hand
    chk = open_table(waiter, 6)
    add_round(waiter, chk["check_id"], [{**MIXED_PIZZA, "quantity": 2}])
    assert cheese0 - StockLevel.objects.get(branch=b, item__code="cheese").on_hand == 280
    st = pizza_station(cook)
    item = cook.get(f"stations/{st}/queue").json()["items"][0]
    assert item["quantity"] == 2
    cook.post(f"ticket-items/{item['id']}/transitions", {"to": "preparing", "expected_revision": item["revision"]})
    line = active_line(check(waiter, chk["check_id"]))
    r = amend(waiter, chk["check_id"], {"action": "split", "line_id": line["id"], "quantity": 3, "reason": "x"})
    assert r.status_code == 422
    r = amend(waiter, chk["check_id"], {"action": "split", "line_id": line["id"], "quantity": 1,
                                        "reason": "two guests"})
    assert r.status_code == 200, r.content
    assert r.json()["total_minor"] == 380000
    d = check(waiter, chk["check_id"])
    live = [ln for rr in d["rounds"] for ln in rr["lines"] if ln["status"] == "active"]
    assert sorted(ln["quantity"] for ln in live) == [1, 1]
    q = cook.get(f"stations/{st}/queue").json()
    assert [i["quantity"] for i in q["items"]] == [1, 1] and {i["status"] for i in q["items"]} == {"preparing"}
    assert q["changes"] == [] and not PrintJob.objects.filter(ticket__kind="change").exists()
    # Stock unchanged by the split; changing one pizza now reverses only that pizza's share
    assert cheese0 - StockLevel.objects.get(branch=b, item__code="cheese").on_hand == 280
    r = modify(waiter, chk["check_id"], live[1]["id"], PIZZA_NO_CHEESE)
    assert r.status_code == 200, r.content
    assert cheese0 - StockLevel.objects.get(branch=b, item__code="cheese").on_hand == Decimal(280 - 20)
    assert r.json()["total_minor"] == 190000 + 180000


def test_print_log_filters_and_shows_ticket_content(demo, waiter, cashier):
    chk = open_table(waiter, 6)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA])
    log = cashier.get("print-jobs", type="kot").json()["jobs"]
    assert len(log) == 1 and log[0]["kind"] == "kitchen" and log[0]["station"] == "pizza"
    seq = log[0]["ticket_seq"]
    assert cashier.get("print-jobs", q=str(seq)).json()["jobs"][0]["id"] == log[0]["id"]
    assert cashier.get("print-jobs", q=chk["token"]).json()["jobs"][0]["check_token"] == chk["token"]
    detail = cashier.get(f"print-jobs/{log[0]['id']}").json()
    assert "RIGHT half" in detail["content"] and detail["copies"] == []
    r = cashier.post(f"print-jobs/{log[0]['id']}/reprints", {"reason": "kitchen lost it"})
    assert r.status_code == 201 and r.json()["is_copy"]
    detail = cashier.get(f"print-jobs/{log[0]['id']}").json()
    assert len(detail["copies"]) == 1 and detail["copies"][0]["reason"] == "kitchen lost it"
    d = check(cashier, chk["check_id"])
    assert [j["kind"] for j in d["print_jobs"]] == ["kitchen", "kitchen"]
    assert cashier.get("print-jobs", type="receipts").json()["jobs"] == []
