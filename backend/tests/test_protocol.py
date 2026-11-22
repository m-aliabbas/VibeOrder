"""Write protocol and acceptance cases A01-A05, A08, A10, A17, A18."""
import threading
import uuid

import pytest
from django.db import connection

from apps.catalog.models import BranchAvailability
from apps.kitchen.models import PrintJob, ProductionTicket
from apps.kitchen.services import PrintTransportError, process_one, recover_stale_jobs
from apps.orders.models import Check, CheckLine

from .conftest import MIXED_PIZZA, Staff, add_round, open_table, table_id


def test_a03_retry_returns_original_result(demo, waiter):
    chk = open_table(waiter, 6)
    key = uuid.uuid4()
    q, r1 = add_round(waiter, chk["check_id"], [MIXED_PIZZA], key=key)
    assert r1.status_code == 201
    r2 = waiter.post(f"checks/{chk['check_id']}/rounds",
                     {"quote_id": q["quote_id"], "menu_release": q["menu_release"], "items": [MIXED_PIZZA]}, key=key)
    assert r2.status_code == 201 and r2.json() == r1.json()
    assert r2["Idempotent-Replay"] == "true"
    assert CheckLine.objects.count() == 1 and ProductionTicket.objects.count() == 1


def test_a04_reused_key_with_changed_topping_conflicts(demo, waiter):
    chk = open_table(waiter, 6)
    key = uuid.uuid4()
    add_round(waiter, chk["check_id"], [MIXED_PIZZA], key=key)
    changed = {**MIXED_PIZZA, "options": [{"id": "olives", "side": "right"}]}
    q2, r = add_round(waiter, chk["check_id"], [changed], key=key)
    assert r.status_code == 409 and r.json()["code"] == "idempotency_mismatch"
    assert CheckLine.objects.count() == 1
    r = waiter.post(f"checks/{chk['check_id']}/rounds", {"quote_id": q2["quote_id"], "menu_release": q2["menu_release"], "items": [changed]})
    assert r.status_code == 201


def test_same_key_other_device_conflicts(demo, waiter, waiter2):
    chk = open_table(waiter, 6)
    key = uuid.uuid4()
    q, _ = add_round(waiter, chk["check_id"], [MIXED_PIZZA], key=key)
    r = waiter2.post(f"checks/{chk['check_id']}/rounds", {"quote_id": q["quote_id"], "menu_release": q["menu_release"], "items": [MIXED_PIZZA]}, key=key)
    assert r.status_code == 409


def test_a05_stale_tender_has_no_side_effects(demo, waiter, cashier):
    from apps.cash.models import DrawerMovement, Tender
    cashier.post("shifts", {"opening_float_minor": 0})
    chk = open_table(waiter, 6)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA])
    seen = cashier.get(f"checks/{chk['check_id']}").json()
    add_round(waiter, chk["check_id"], [{"client_line_id": "dessert", "product": "water-500"}])  # append wins
    r = cashier.post(f"checks/{chk['check_id']}/tenders", {"method": "cash", "amount_minor": seen["balance_minor"],
                                                           "expected_revision": seen["revision"]})
    assert r.status_code == 409 and r.json()["current_revision"] == seen["revision"] + 1
    assert Tender.objects.count() == 0 and DrawerMovement.objects.count() == 0
    # close wins -> append refused
    fresh = cashier.get(f"checks/{chk['check_id']}").json()
    r = cashier.post(f"checks/{chk['check_id']}/tenders", {"method": "cash", "amount_minor": fresh["balance_minor"],
                                                           "expected_revision": fresh["revision"]})
    cashier.post(f"checks/{chk['check_id']}/close", {"expected_revision": r.json()["revision"]})
    _, r = add_round(waiter, chk["check_id"], [MIXED_PIZZA])
    assert r.status_code == 409 and r.json()["code"] == "check_closed"


def test_occupied_table_returns_existing_check(demo, waiter, waiter2):
    chk = open_table(waiter, 7)
    r = waiter2.post("checks", {"service_type": "dine_in", "table_id": table_id(7), "guest_count": 2})
    assert r.status_code == 409 and r.json()["check_id"] == chk["check_id"]


def test_quote_mismatch_and_expired(demo, waiter, settings):
    chk = open_table(waiter, 6)
    q = waiter.post("quotes", {"items": [MIXED_PIZZA]}, key=False).json()
    other = {**MIXED_PIZZA, "crust": "thin"}
    r = waiter.post(f"checks/{chk['check_id']}/rounds", {"quote_id": q["quote_id"], "menu_release": q["menu_release"], "items": [other]})
    assert r.status_code == 422 and r.json()["code"] == "quote_mismatch"


def test_a10_sellout_after_quote_rejected_and_snapshot_kept(demo, waiter):
    chk = open_table(waiter, 6)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA])
    from apps.core.fixtures_demo import build_release
    branch = demo["branch"]
    q = waiter.post("quotes", {"items": [MIXED_PIZZA]}, key=False).json()
    BranchAvailability.objects.create(branch=branch, target_kind="option", target_code="extra-cheese", available=False)
    r = waiter.post(f"checks/{chk['check_id']}/rounds", {"quote_id": q["quote_id"], "menu_release": q["menu_release"], "items": [MIXED_PIZZA]})
    assert r.status_code == 422 and r.json()["code"] == "sold_out"
    # New release mid-shift: existing line keeps its snapshot price
    rel2 = build_release(demo["tenant"], version=2, price_overrides={("fajita", "large"): 170000})
    branch.active_release = rel2
    branch.save()
    BranchAvailability.objects.all().delete()
    old_q = q
    r = waiter.post(f"checks/{chk['check_id']}/rounds", {"quote_id": old_q["quote_id"], "menu_release": old_q["menu_release"], "items": [MIXED_PIZZA]})
    assert r.status_code == 422 and r.json()["code"] == "menu_changed"
    q2 = waiter.post("quotes", {"items": [MIXED_PIZZA]}, key=False).json()
    assert q2["total_minor"] == 200000
    assert CheckLine.objects.get().unit_minor == 190000


def test_a17_foreign_branch_and_revoked_device(demo, waiter, manager):
    other = uuid.uuid4()
    r = waiter.client.get(f"/api/v1/branches/{other}/floor", **waiter.headers(False))
    assert r.status_code == 404
    chk = open_table(waiter, 6)
    from apps.core.models import Device
    dev = Device.objects.get(token_hash=Device.hash_token(waiter.device_key))
    r = manager.post(f"devices/{dev.pk}/revoke", key=False)
    assert r.status_code == 200
    assert waiter.get(f"checks/{chk['check_id']}").status_code == 401


def test_login_lockout(demo, settings):
    settings.LOGIN_MAX_ATTEMPTS = 3
    from rest_framework.test import APIClient
    c = APIClient()
    for _ in range(3):
        assert c.post("/api/v1/login", {"username": "bilal", "password": "wrong-pw"}, format="json").status_code == 401
    r = c.post("/api/v1/login", {"username": "bilal", "password": "bilal123"}, format="json")
    assert r.status_code == 429 and r.json()["code"] == "locked"
    assert c.post("/api/v1/login", {"username": "nobody", "password": "x"}, format="json").status_code == 401


def test_a18_counting_drawer_rejects_postings_and_variance_needs_manager(demo, waiter, cashier, staff_factory):
    s = cashier.post("shifts", {"opening_float_minor": 500000}).json()
    chk = open_table(waiter, 6)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA])
    # cashier cannot see expected before counting (blind)
    assert "expected_minor" not in cashier.get("shifts").json()["shift"]
    r = cashier.post(f"shifts/{s['id']}/count", {"counted_minor": 495000, "expected_revision": s["revision"]})
    assert r.status_code == 200 and r.json()["variance_minor"] == -5000
    rev = cashier.get(f"checks/{chk['check_id']}").json()["revision"]
    r = cashier.post(f"checks/{chk['check_id']}/tenders", {"method": "cash", "amount_minor": 190000, "expected_revision": rev})
    assert r.status_code == 409 and r.json()["code"] == "drawer_counting"
    shift_rev = r_rev = cashier.get("shifts").json()["shift"]["revision"]
    r = cashier.post(f"shifts/{s['id']}/close", {"expected_revision": shift_rev})
    assert r.status_code == 422  # reason required
    r = cashier.post(f"shifts/{s['id']}/close", {"expected_revision": shift_rev, "reason": "short"})
    assert r.status_code == 403 and r.json()["code"] == "approval_required"
    r = cashier.post(f"shifts/{s['id']}/close", {"expected_revision": shift_rev, "reason": "short",
                                                 "approval": {"username": "zara", "password": "zara123"}})
    assert r.status_code == 200 and r.json()["approved_by"] == "Zara (Manager)"
    r = cashier.post(f"shifts/{s['id']}/count", {"counted_minor": 1, "expected_revision": r.json()["revision"]})
    assert r.status_code == 409  # closed is immutable


def test_golden_close_drawer_part(demo, waiter, cashier, manager):
    """Float 5,000 + cash sales 8,300 - paid-out 500 = 12,800 (refund/rider parts land in M2/M3)."""
    s = cashier.post("shifts", {"opening_float_minor": 500000}).json()
    from apps.cash.models import DrawerMovement, DrawerShift
    from apps.cash.services import expected_drawer
    shift = DrawerShift.objects.get()
    DrawerMovement.objects.create(shift=shift, kind="cash_sale", amount_minor=830000, staff=shift.opened_by,
                                  business_date=shift.business_date)
    r = manager.post(f"shifts/{s['id']}/movements", {"kind": "paid_out", "amount_minor": 50000, "reason": "gas cylinder"})
    assert r.status_code == 201, r.content
    assert expected_drawer(shift) == 1280000


def test_a08_print_crash_marks_outcome_unknown_and_copy_is_labelled(demo, waiter, manager, settings):
    chk = open_table(waiter, 6)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA])

    def crash_after_bytes(printer, job):
        raise RuntimeError("worker killed mid-send")

    process_one(send=crash_after_bytes)
    job = PrintJob.objects.get(kind="kitchen")
    assert job.status == "outcome_unknown"
    assert process_one() is None, "no automatic blind re-fire"
    r = manager.post(f"print-jobs/{job.pk}/reprints", {"reason": "ticket not on rail"})
    assert r.status_code == 201 and r.json()["is_copy"]
    process_one()
    copy = PrintJob.objects.get(is_copy=True)
    assert "*** COPY of job" in open(copy.spooler_ref).read()


def test_print_known_failure_retries_and_stale_lease_recovered(demo, waiter, settings):
    chk = open_table(waiter, 6)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA])

    def refuse(printer, job):
        raise PrintTransportError("connection refused")

    process_one(send=refuse)
    assert PrintJob.objects.get().status == "failed_before_send"
    assert process_one() is None, "waits out its backoff instead of hammering a broken printer"
    from django.utils import timezone as tz
    PrintJob.objects.update(lease_until=tz.now())  # backoff elapsed
    process_one()
    assert PrintJob.objects.get().status == "handed_to_spooler"
    # simulate worker died after leasing
    from django.utils import timezone
    from datetime import timedelta
    PrintJob.objects.update(status="sending", lease_until=timezone.now() - timedelta(seconds=1))
    assert recover_stale_jobs() == 1
    assert PrintJob.objects.get().status == "outcome_unknown"


# ------------------------------------------------------------ real concurrency (independent connections)

def _run_parallel(fns):
    results, errors = [None] * len(fns), []
    barrier = threading.Barrier(len(fns))

    def wrap(i, fn):
        try:
            barrier.wait()
            results[i] = fn()
        except Exception as e:  # pragma: no cover
            errors.append(e)
        finally:
            connection.close()

    threads = [threading.Thread(target=wrap, args=(i, f)) for i, f in enumerate(fns)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    return results


@pytest.mark.django_db(transaction=True)
def test_a01_two_waiters_open_same_table(tmp_path, settings):
    from apps.core.fixtures_demo import seed
    settings.PRINT_SPOOL_DIR = tmp_path
    d = seed()
    a, b = Staff(d["branch"], "bilal", "bilal123"), Staff(d["branch"], "sana", "sana123")
    payload = {"service_type": "dine_in", "table_id": table_id(6), "guest_count": 2}
    r1, r2 = _run_parallel([lambda: a.post("checks", payload), lambda: b.post("checks", payload)])
    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [201, 409]
    assert Check.objects.filter(status="open").count() == 1
    ids = {r.json().get("check_id") for r in (r1, r2)}
    assert len(ids) == 1, "both devices recover the same check id"


@pytest.mark.django_db(transaction=True)
def test_a02_concurrent_rounds_on_same_check(tmp_path, settings):
    from apps.core.fixtures_demo import seed
    settings.PRINT_SPOOL_DIR = tmp_path
    d = seed()
    a, b = Staff(d["branch"], "bilal", "bilal123"), Staff(d["branch"], "sana", "sana123")
    chk = open_table(a, 6)
    qa = a.post("quotes", {"items": [MIXED_PIZZA]}, key=False).json()
    item_b = {"client_line_id": "B1", "product": "fries", "variant": "regular"}
    qb = b.post("quotes", {"items": [item_b]}, key=False).json()
    url = f"checks/{chk['check_id']}/rounds"
    r1, r2 = _run_parallel([
        lambda: a.post(url, {"quote_id": qa["quote_id"], "menu_release": qa["menu_release"], "items": [MIXED_PIZZA]}),
        lambda: b.post(url, {"quote_id": qb["quote_id"], "menu_release": qb["menu_release"], "items": [item_b]}),
    ])
    assert r1.status_code == r2.status_code == 201
    assert sorted([r1.json()["revision"], r2.json()["revision"]]) == [2, 3]
    assert set(r1.json()["ticket_ids"]).isdisjoint(r2.json()["ticket_ids"])
    assert CheckLine.objects.count() == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_identical_retry_single_write(tmp_path, settings):
    from apps.core.fixtures_demo import seed
    settings.PRINT_SPOOL_DIR = tmp_path
    d = seed()
    a = Staff(d["branch"], "bilal", "bilal123")
    chk = open_table(a, 6)
    q = a.post("quotes", {"items": [MIXED_PIZZA]}, key=False).json()
    key = uuid.uuid4()
    body = {"quote_id": q["quote_id"], "menu_release": q["menu_release"], "items": [MIXED_PIZZA]}
    url = f"checks/{chk['check_id']}/rounds"
    r1, r2 = _run_parallel([lambda: a.post(url, body, key=key), lambda: a.post(url, body, key=key)])
    assert r1.status_code == r2.status_code == 201
    assert r1.json() == r2.json()
    assert CheckLine.objects.count() == 1
