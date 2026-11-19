"""M3 fulfillment: phone customers + repeat order (A10), packing checklist (A13), delivery and rider ledger (A14),
full golden close (spec 04) and the family deal delivery fixture (A12: 2,420)."""
from apps.cash.models import DrawerMovement
from apps.fulfillment.models import RiderCollection
from apps.kitchen.models import TicketItem

from .conftest import MIXED_PIZZA, add_round

APPROVAL = {"username": "zara", "pin": "9999"}
FAMILY_DEAL = {"client_line_id": "deal-1", "product": "family-deal", "quantity": 1, "selections": [
    {"slot": "pizza", "product": "mixed-pizza", "variant": "large", "crust": "stuffed",
     "halves": {"left": "tikka", "right": "fajita"}, "options": [{"id": "extra-cheese", "side": "right"}]},
    {"slot": "drink", "product": "cola-1500", "variant": "regular"}]}


def zone(staff, code="gulberg"):
    return next(z for z in staff.get("zones").json()["zones"] if z["code"] == code)


def open_delivery(staff, payment="cod", phone="0300-1234567", name="Ali", address="House 12, Street 4, Gulberg III"):
    r = staff.post("checks", {"service_type": "delivery", "customer_phone": phone, "customer_name": name,
                              "address_text": address, "zone_id": zone(staff)["id"], "payment": payment})
    assert r.status_code == 201, r.content
    return r.json()


def cook_all(cook, check_id, upto="ready"):
    for st in cook.get("bootstrap").json()["stations"]:
        for it in cook.get(f"stations/{st['id']}/queue").json()["items"]:
            if it["check"]["id"] == check_id:
                cook.post(f"ticket-items/{it['id']}/transitions", {"to": upto, "expected_revision": it["revision"]})


def delivery_of(staff, check_id):
    return staff.get(f"checks/{check_id}").json()["delivery"]


def move(staff, delivery, to, **extra):
    return staff.post(f"deliveries/{delivery['id']}/transitions",
                      {"to": to, "expected_revision": delivery["revision"], **extra})


def rider_id(staff, username="imran"):
    return next(r["id"] for r in staff.get("riders").json()["riders"] if r["name"].lower().startswith(username))


def test_a12_family_deal_delivery_total_2420(demo, cashier):
    chk = open_delivery(cashier)
    _, r = add_round(cashier, chk["check_id"], [FAMILY_DEAL])
    assert r.status_code == 201, r.content
    d = cashier.get(f"checks/{chk['check_id']}").json()
    assert d["total_minor"] == 242000
    pack = cashier.get("packing").json()["orders"][0]
    labels = sorted(c["label"] for c in pack["components"])
    assert len(labels) == 2 and any("PIZZA" in x for x in labels)  # one pizza + one drink


def test_a13_packing_needs_all_components_or_override(demo, cashier, cook, staff_factory):
    packer = staff_factory("faisal", "6666")
    r = cashier.post("checks", {"service_type": "pickup", "customer_name": "Sara", "customer_phone": "03215550000"})
    cid = r.json()["check_id"]
    add_round(cashier, cid, [MIXED_PIZZA, {"client_line_id": "d", "product": "cola-500", "quantity": 1}])
    pizza = next(s for s in cook.get("bootstrap").json()["stations"] if s["code"] == "pizza")
    it = cook.get(f"stations/{pizza['id']}/queue").json()["items"][0]
    cook.post(f"ticket-items/{it['id']}/transitions", {"to": "ready", "expected_revision": it["revision"]})
    order = next(o for o in packer.get("packing").json()["orders"] if o["id"] == cid)
    assert [m["label"] for m in order["missing"]] == ["COLA 500ML"]
    r = packer.post(f"checks/{cid}/handover", {})
    assert r.status_code == 422 and r.json()["code"] == "pack_incomplete" and "COLA" in r.json()["missing"][0]
    r = packer.post(f"checks/{cid}/handover", {"override_reason": "guest in a hurry, no drink"})
    assert r.status_code == 403 and r.json()["code"] == "approval_required"
    r = packer.post(f"checks/{cid}/handover", {"override_reason": "guest in a hurry", "approval": APPROVAL})
    assert r.status_code == 200, r.content
    assert r.json()["handed_over_at"] and TicketItem.objects.filter(status="packed").count() == 1
    assert packer.post(f"checks/{cid}/handover", {}).json()["code"] == "already_handed_over"


def test_a14_prepaid_cod_failed_and_remittance(demo, cashier, cook, manager):
    cashier.post("shifts", {"opening_float_minor": 0})
    rid = rider_id(cashier)
    # 1) prepaid by card at the counter
    pre = open_delivery(cashier, payment="prepaid", phone="03001111111")
    add_round(cashier, pre["check_id"], [{"client_line_id": "c", "product": "cola-500", "quantity": 1}])
    d = cashier.get(f"checks/{pre['check_id']}").json()
    cashier.post(f"checks/{pre['check_id']}/tenders", {"method": "external_card", "amount_minor": d["balance_minor"],
                                                        "external_ref": "T9", "expected_revision": d["revision"]})
    # 2) COD
    cod = open_delivery(cashier, phone="03002222222")
    add_round(cashier, cod["check_id"], [FAMILY_DEAL])
    # 3) COD that fails
    bad = open_delivery(cashier, phone="03003333333")
    add_round(cashier, bad["check_id"], [{"client_line_id": "w", "product": "water-500", "quantity": 1}])
    for c in (pre, cod, bad):
        cook_all(cook, c["check_id"])
        assert cashier.post(f"checks/{c['check_id']}/handover", {}).status_code == 200
        dl = delivery_of(cashier, c["check_id"])
        r = move(cashier, dl, "assigned", rider_id=rid)
        assert r.status_code == 200, r.content
        r = move(cashier, r.json(), "dispatched")
        assert r.status_code == 200, r.content
    # Prepaid delivered: no collection
    r = move(cashier, delivery_of(cashier, pre["check_id"]), "delivered")
    assert r.status_code == 200 and "collected_minor" not in r.json()
    # COD delivered: rider collects the balance (2,420) and the check closes
    dl = delivery_of(cashier, cod["check_id"])
    assert dl["amount_to_collect_minor"] == 242000
    r = move(cashier, dl, "delivered", collected_minor=242000)
    assert r.status_code == 200 and r.json()["collected_minor"] == 242000 and r.json()["receipt_no"]
    # Failed: needs reason + food decision; no reversal, check stays open
    dl = delivery_of(cashier, bad["check_id"])
    assert move(cashier, dl, "failed", reason="no answer").status_code == 422
    r = move(cashier, dl, "failed", reason="no answer", food_disposition="returned")
    assert r.status_code == 200 and r.json()["decision_required"]
    bd = cashier.get(f"checks/{bad['check_id']}").json()
    assert bd["status"] == "open" and bd["balance_minor"] == 8000 + 12000
    # Only collected COD is a liability
    assert RiderCollection.objects.count() == 1
    riders = cashier.get("riders").json()["riders"]
    imran = next(r for r in riders if r["id"] == rid)
    assert imran["outstanding_minor"] == 242000
    # Remittance: needs manager, cannot exceed, posts one drawer movement
    r = cashier.post(f"riders/{rid}/remittances", {"amount_minor": 200000})
    assert r.status_code == 403
    r = cashier.post(f"riders/{rid}/remittances", {"amount_minor": 300000, "approval": APPROVAL})
    assert r.status_code == 422 and r.json()["code"] == "remittance_exceeds"
    key = "5b0f7a3e-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
    r = cashier.post(f"riders/{rid}/remittances", {"amount_minor": 200000, "approval": APPROVAL}, key=key)
    assert r.status_code == 201 and r.json()["outstanding_minor"] == 42000
    r = cashier.post(f"riders/{rid}/remittances", {"amount_minor": 200000, "approval": APPROVAL}, key=key)
    assert r.status_code == 201 and r["Idempotent-Replay"] == "true"
    assert DrawerMovement.objects.filter(kind="remittance").count() == 1
    # Retry the failed delivery: new attempt, old one kept
    r = cashier.post(f"deliveries/{dl['id']}/retry", {"reason": "guest called back"})
    assert r.status_code == 201 and r.json()["attempt"] == 2
    assert cashier.get("deliveries").json()["deliveries"][0]["attempt"] == 2


def test_rider_sees_only_own_active_jobs(demo, cashier, cook, staff_factory):
    rider = staff_factory("imran", "5555")
    other = staff_factory("kashif", "5557")
    c = open_delivery(cashier)
    add_round(cashier, c["check_id"], [{"client_line_id": "w", "product": "water-500", "quantity": 1}])
    move(cashier, delivery_of(cashier, c["check_id"]), "assigned", rider_id=rider_id(cashier))
    mine = rider.get("deliveries").json()["deliveries"]
    assert len(mine) == 1 and mine[0]["address_text"].startswith("House 12")
    assert other.get("deliveries").json()["deliveries"] == []
    assert rider.get("riders").status_code == 403


def test_golden_close_full(demo, waiter, cashier, cook, manager):
    """Spec 04: float 5,000 + counter cash 8,300 + rider remitted 2,000 − refund 300 − paid-out 500 = 14,500;
    counted 14,450 → −50; card 4,000 excluded; rider outstanding 420."""
    from apps.catalog.models import Product, Variant
    from apps.orders.services import _MENU_CACHE
    rel = demo["branch"].active_release
    p = Product.objects.create(release=rel, category=Product.objects.filter(release=rel).first().category,
                               code="open-food", name="Open food", kind="item", station_code="packing")
    for amt in (8300, 4000, 2300):
        Variant.objects.create(product=p, code=str(amt), name=str(amt), price_minor=amt * 100)
    _MENU_CACHE.clear()
    assert cashier.post("shifts", {"opening_float_minor": 500000}).status_code == 201

    def counter_sale(amount, method):
        cid = cashier.post("checks", {"service_type": "counter"}).json()["check_id"]
        add_round(cashier, cid, [{"client_line_id": "x", "product": "open-food", "variant": str(amount)}])
        d = cashier.get(f"checks/{cid}").json()
        body = {"method": method, "amount_minor": d["balance_minor"], "expected_revision": d["revision"]}
        if method == "external_card":
            body["external_ref"] = "T1"
        t = cashier.post(f"checks/{cid}/tenders", body).json()
        cashier.post(f"checks/{cid}/close", {"expected_revision": t["revision"]})
        return t["tender_id"]

    cash_tid = counter_sale(8300, "cash")
    counter_sale(4000, "external_card")
    # Rider collects 2,420 (2,300 food + 120 Gulberg fee), hands back 2,000
    c = open_delivery(cashier)
    add_round(cashier, c["check_id"], [{"client_line_id": "x", "product": "open-food", "variant": "2300"}])
    cook_all(cook, c["check_id"])
    cashier.post(f"checks/{c['check_id']}/handover", {})
    rid = rider_id(cashier)
    dl = move(cashier, delivery_of(cashier, c["check_id"]), "assigned", rider_id=rid).json()
    dl = move(cashier, dl, "dispatched").json()
    assert move(cashier, dl, "delivered", collected_minor=242000).status_code == 200
    assert cashier.post(f"riders/{rid}/remittances", {"amount_minor": 200000, "approval": APPROVAL}).status_code == 201
    assert cashier.post(f"tenders/{cash_tid}/refunds", {"amount_minor": 30000, "reason": "cold",
                                                        "approval": APPROVAL}).status_code == 201
    shift = cashier.get("shifts").json()["shift"]
    assert cashier.post(f"shifts/{shift['id']}/movements", {"kind": "paid_out", "amount_minor": 50000,
                                                            "reason": "gas", "approval": APPROVAL}).status_code == 201
    shift = cashier.get("shifts").json()["shift"]
    s = cashier.post(f"shifts/{shift['id']}/count", {"counted_minor": 1445000,
                                                     "expected_revision": shift["revision"]}).json()
    assert s["expected_minor"] == 1450000 and s["variance_minor"] == -5000
    assert s["summary"]["tenders"]["external_card"] == 400000
    assert s["summary"]["riders"]["outstanding_minor"] == 42000
    assert s["summary"]["riders"]["by_rider"][0]["collections"][0]["outstanding_minor"] == 42000


def test_a10_phone_lookup_and_repeat_highlights_changes(demo, cashier, manager):
    c = open_delivery(cashier, phone="0300 765 4321", name="Hina")
    add_round(cashier, c["check_id"], [MIXED_PIZZA, {"client_line_id": "f", "product": "fries", "quantity": 2}])
    r = cashier.get("customers", q="4321").json()["customers"]
    assert len(r) == 1 and r[0]["phone_masked"] == "0300••••321" and r[0]["name"] == "Hina"
    assert r[0]["addresses"][0]["text"].startswith("House 12")
    # Fries sell out, so the repeat highlights it
    manager.post("availability", {"target_kind": "product", "target_code": "fries", "available": False,
                                  "reason": "fryer down"})
    rep = cashier.get(f"customers/{r[0]['id']}/repeat").json()
    statuses = {i["name"]: i["status"] for i in rep["items"]}
    assert statuses["Large Half & Half Pizza"] == "ok" and statuses["Fries"] == "unavailable"
    assert rep["changes"] == 1 and rep["delivery"]["needs_confirmation"]
    assert all(i["spec"]["product"] != "delivery-fee" for i in rep["items"])
    # Lookup needs permission and 4 digits
    assert cashier.get("customers", q="12").status_code == 422
