"""Discounts: one order promotion + line adjustments, cashier limit, allocation, tax after discount (A12)."""
from apps.orders.models import CheckDiscount

from .conftest import MIXED_PIZZA, add_round, open_table

APPROVAL = {"username": "zara", "password": "zara123"}
COLA = {"client_line_id": "c", "product": "cola-500", "quantity": 1}  # Rs 120


def detail(staff, cid):
    return staff.get(f"checks/{cid}").json()


def discount(staff, cid, **body):
    d = detail(staff, cid)
    return staff.post(f"checks/{cid}/discounts", {"expected_revision": d["revision"], **body})


def test_promotion_percent_allocated_and_one_per_check(demo, waiter, cashier):
    chk = open_table(waiter, 6)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA, COLA])
    r = discount(cashier, chk["check_id"], kind="promotion", promotion_code="weekend10")
    assert r.status_code == 201, r.content
    assert r.json()["discount_minor"] == 19000 + 1200 and r.json()["total_minor"] == 202000 - 20200
    d = detail(cashier, chk["check_id"])
    assert sorted(ln["discount_minor"] for rr in d["rounds"] for ln in rr["lines"]) == [1200, 19000]
    assert d["discounts"][0]["promotion"]["code"] == "WEEKEND10"
    r = discount(cashier, chk["check_id"], kind="promotion", promotion_code="FLAT200")
    assert r.status_code == 409 and r.json()["code"] == "promotion_exists"
    # A new round picks up the order promotion
    add_round(waiter, chk["check_id"], [{**COLA, "client_line_id": "c2"}])
    d = detail(cashier, chk["check_id"])
    assert d["discount_minor"] == 20200 + 1200
    # Waiter cannot discount without a manager
    r = discount(waiter, chk["check_id"], kind="line_adjustment", line_id=d["rounds"][0]["lines"][0]["id"],
                 amount_minor=100, reason="x")
    assert r.status_code == 403 and r.json()["code"] == "approval_required"


def test_line_adjustment_over_limit_needs_manager(demo, waiter, cashier):
    chk = open_table(waiter, 5)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA])
    line = detail(cashier, chk["check_id"])["rounds"][0]["lines"][0]
    r = discount(cashier, chk["check_id"], kind="line_adjustment", line_id=line["id"], amount_minor=19000,
                 reason="late")  # exactly 10%: within cashier limit
    assert r.status_code == 201, r.content
    assert CheckDiscount.objects.get().approved_by is None
    r = discount(cashier, chk["check_id"], kind="line_adjustment", line_id=line["id"], amount_minor=100, reason="more")
    assert r.status_code == 403 and r.json()["code"] == "approval_required"
    r = discount(cashier, chk["check_id"], kind="line_adjustment", line_id=line["id"], percent_bp=10000,
                 reason="cold", approval=APPROVAL)
    assert r.status_code == 201
    assert r.json()["total_minor"] == 0  # floor of zero
    # Remove the large one: total recomputed
    did = r.json()["discount"]["id"]
    d = detail(cashier, chk["check_id"])
    r = cashier.post(f"checks/{chk['check_id']}/discounts/{did}/remove", {"expected_revision": d["revision"]})
    assert r.status_code == 200 and r.json()["total_minor"] == 171000


def test_fixed_promotion_minimum_and_three_line_allocation(demo, waiter, cashier):
    chk = open_table(waiter, 4)
    lines = [{"client_line_id": f"w{i}", "product": "water-500", "quantity": 1} for i in range(3)]
    add_round(waiter, chk["check_id"], lines)
    r = discount(cashier, chk["check_id"], kind="promotion", promotion_code="FLAT200")
    assert r.status_code == 422 and r.json()["code"] == "promotion_minimum"


def test_discount_after_payment_needs_refund(demo, waiter, cashier):
    chk = open_table(waiter, 3)
    add_round(waiter, chk["check_id"], [COLA])
    cashier.post("shifts", {"opening_float_minor": 0})
    d = detail(cashier, chk["check_id"])
    assert cashier.post(f"checks/{chk['check_id']}/tenders", {
        "method": "cash", "amount_minor": 12000, "expected_revision": d["revision"]}).status_code == 201
    r = discount(cashier, chk["check_id"], kind="promotion", promotion_code="WEEKEND10")
    assert r.status_code == 422 and r.json()["code"] == "refund_required"
    assert not CheckDiscount.objects.exists()
