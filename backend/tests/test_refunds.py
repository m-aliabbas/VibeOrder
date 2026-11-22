"""Refunds (A11), refund effects on the drawer, void after refund, and the golden close drawer figures."""
import pytest

from apps.cash.models import DrawerMovement, Refund
from apps.kitchen.models import PrintJob
from apps.orders.models import Check

from .conftest import MIXED_PIZZA, Staff, add_round, open_table
from .test_protocol import _run_parallel

APPROVAL = {"username": "zara", "password": "zara123"}
COLA = {"client_line_id": "c", "product": "cola-500", "quantity": 1}  # Rs 120


def paid_check(waiter, cashier, table=6, items=None, method="cash", close=True):
    chk = open_table(waiter, table)
    _, r = add_round(waiter, chk["check_id"], items or [MIXED_PIZZA])
    assert r.status_code == 201, r.content
    if cashier.get("shifts").json()["shift"] is None:
        assert cashier.post("shifts", {"opening_float_minor": 500000}).status_code == 201
    d = cashier.get(f"checks/{chk['check_id']}").json()
    body = {"method": method, "amount_minor": d["balance_minor"], "expected_revision": d["revision"]}
    if method == "external_card":
        body["external_ref"] = "TERM-1"
    t = cashier.post(f"checks/{chk['check_id']}/tenders", body)
    assert t.status_code == 201, t.content
    if close:
        assert cashier.post(f"checks/{chk['check_id']}/close", {"expected_revision": t.json()["revision"]}).status_code == 200
    return chk["check_id"], t.json()["tender_id"]


def refund(staff, tender_id, amount, **extra):
    return staff.post(f"tenders/{tender_id}/refunds", {"amount_minor": amount, "reason": "cold pizza", **extra})


def test_cash_refund_on_closed_check_needs_manager_and_hits_drawer(demo, waiter, cashier):
    cid, tid = paid_check(waiter, cashier)
    r = refund(cashier, tid, 30000)
    assert r.status_code == 403 and r.json()["code"] == "approval_required"
    r = refund(cashier, tid, 30000, approval=APPROVAL, goods="not_returned")
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["status"] == "succeeded" and body["approved_by"].startswith("Zara") and body["refundable_minor"] == 160000
    assert body["allocations"] == [{"line_id": body["allocations"][0]["line_id"], "amount_minor": 30000}]
    d = cashier.get(f"checks/{cid}").json()
    assert d["status"] == "closed" and d["balance_minor"] == 0  # closed check stays closed, bill does not reopen
    assert d["refunded_minor"] == 30000 and d["net_paid_minor"] == 160000
    assert d["tenders"][0]["refundable_minor"] == 160000 and len(d["refunds"]) == 1
    m = DrawerMovement.objects.get(kind="cash_refund")
    assert m.amount_minor == -30000 and m.approved_by.username == "zara"
    assert PrintJob.objects.filter(kind="refund").count() == 1
    # Over-refund rejected with the remaining refundable amount
    r = refund(cashier, tid, 160001, approval=APPROVAL)
    assert r.status_code == 422 and r.json()["code"] == "refund_exceeds" and r.json()["refundable_minor"] == 160000


def test_refund_by_item_capped_at_line_payment(demo, waiter, cashier, manager):
    cid, tid = paid_check(waiter, cashier, items=[MIXED_PIZZA, COLA])
    d = cashier.get(f"checks/{cid}").json()
    cola = next(ln for r in d["rounds"] for ln in r["lines"] if ln["product"] == "cola-500")
    r = refund(manager, tid, 12001, items=[{"line_id": cola["id"], "amount_minor": 12001}])
    assert r.status_code == 422 and r.json()["field_errors"] == {"items[0].amount_minor": "exceeds"}
    r = refund(manager, tid, 12000, items=[{"line_id": cola["id"], "amount_minor": 12000}], goods="discarded")
    assert r.status_code == 201, r.content
    assert r.json()["goods"] == "discarded"
    r = refund(manager, tid, 1, items=[{"line_id": cola["id"], "amount_minor": 1}])
    assert r.status_code == 422


def test_card_refund_without_reference_stays_pending_and_reserves(demo, waiter, cashier, manager):
    cid, tid = paid_check(waiter, cashier, method="external_card")
    r = refund(manager, tid, 190000)
    assert r.status_code == 201 and r.json()["status"] == "pending" and r.json()["refundable_minor"] == 0
    pending = r.json()["id"]
    assert refund(manager, tid, 1).json()["code"] == "refund_exceeds"  # reserved while unknown
    d = cashier.get(f"checks/{cid}").json()
    assert d["pending_refund_minor"] == 190000 and d["refunded_minor"] == 0
    r = manager.post(f"refunds/{pending}/resolve", {"status": "failed"})
    assert r.status_code == 200 and r.json()["refundable_minor"] == 190000
    r = refund(manager, tid, 190000, external_ref="RF-77")
    assert r.status_code == 201 and r.json()["status"] == "succeeded"
    assert not DrawerMovement.objects.filter(kind="cash_refund").exists()  # card money never touches the drawer


def test_void_open_check_after_full_refund(demo, waiter, cashier, manager):
    cid, tid = paid_check(waiter, cashier, table=3, items=[COLA], close=False)
    d = manager.get(f"checks/{cid}").json()
    r = manager.post(f"checks/{cid}/void", {"expected_revision": d["revision"], "reason": "guest left"})
    assert r.json()["code"] == "refund_required"
    assert refund(manager, tid, 12000).status_code == 201
    d = manager.get(f"checks/{cid}").json()
    r = manager.post(f"checks/{cid}/void", {"expected_revision": d["revision"], "reason": "guest left"})
    assert r.status_code == 200, r.content
    assert Check.objects.get(pk=cid).status == "voided"


@pytest.mark.django_db(transaction=True)
def test_a11_two_simultaneous_refunds_stay_within_refundable(tmp_path, settings):
    from apps.core.fixtures_demo import seed
    settings.PRINT_SPOOL_DIR = tmp_path
    d = seed()
    w, c = Staff(d["branch"], "bilal", "bilal123"), Staff(d["branch"], "hamza", "hamza123", "cashier")
    m1, m2 = Staff(d["branch"], "zara", "zara123"), Staff(d["branch"], "zara", "zara123")
    _, tid = paid_check(w, c)
    r1, r2 = _run_parallel([lambda: refund(m1, tid, 120000), lambda: refund(m2, tid, 120000)])
    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [201, 422], (r1.content, r2.content)
    assert Refund.objects.filter(status="succeeded").count() == 1
    assert DrawerMovement.objects.filter(kind="cash_refund").count() == 1


def test_golden_close_drawer_without_rider(demo, waiter, cashier, manager):
    """Spec 04 golden close, rider part pending (M3): float 5,000 + cash 8,300 − refund 300 − paid-out 500
    = 12,500 (14,500 once the 2,000 rider remittance is posted). Card 4,000 is excluded."""
    from apps.catalog.models import Product, Variant
    from apps.orders.services import _MENU_CACHE
    rel = demo["branch"].active_release
    p = Product.objects.create(release=rel, category=Product.objects.filter(release=rel).first().category,
                               code="open-food", name="Open food", kind="item", station_code="packing")
    Variant.objects.create(product=p, code="8300", name="8300", price_minor=830000)
    Variant.objects.create(product=p, code="4000", name="4000", price_minor=400000)
    _MENU_CACHE.clear()
    _, cash_tid = paid_check(waiter, cashier, 1, [{"client_line_id": "a", "product": "open-food", "variant": "8300"}])
    paid_check(waiter, cashier, 2, [{"client_line_id": "b", "product": "open-food", "variant": "4000"}], "external_card")
    assert refund(cashier, cash_tid, 30000, approval=APPROVAL).status_code == 201
    shift = cashier.get("shifts").json()["shift"]
    r = cashier.post(f"shifts/{shift['id']}/movements", {"kind": "paid_out", "amount_minor": 50000,
                                                         "reason": "gas cylinder", "approval": APPROVAL})
    assert r.status_code == 201, r.content
    shift = cashier.get("shifts").json()["shift"]
    r = cashier.post(f"shifts/{shift['id']}/count", {"counted_minor": 1245000, "expected_revision": shift["revision"]})
    s = r.json()
    assert s["expected_minor"] == 1250000 and s["variance_minor"] == -5000
    assert s["summary"]["tenders"]["external_card"] == 400000 and s["summary"]["refunds"]["cash"] == 30000
