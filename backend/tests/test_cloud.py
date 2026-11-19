"""M4: replication (A15), online intake + reconciliation (A16), scope (A17), per-branch releases (A20),
prepaid callbacks (A23)."""
import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.cloud.branch_sync import TransportError, import_offer, pull_inbox, sync_once, upload
from apps.cloud.models import CloudEvent, DailyProjection, InboxCommand, OnlineOrder, SyncException
from apps.cloud.payments import sign
from apps.kitchen.models import ProductionTicket, TicketItem
from apps.orders.models import Check

from .conftest import MIXED_PIZZA, Staff, add_round

COLA = {"product": "cola-500", "quantity": 1}


class CloudLink:
    def __init__(self, token, fail_ack=False, down=False):
        self.c = APIClient()
        self.token = token
        self.fail_ack = fail_ack
        self.down = down

    def _check(self, r):
        if self.down or r.status_code >= 500:
            raise TransportError("unreachable")
        assert r.status_code < 400, r.content
        return r.json()

    def post(self, path, body):
        if self.down or (self.fail_ack and path == "ack"):
            raise TransportError("connection reset")
        return self._check(self.c.post(f"/api/v1/cloud/sync/{path}", body, format="json",
                                       HTTP_AUTHORIZATION=f"Branch {self.token}"))

    def get(self, path):
        if self.down:
            raise TransportError("unreachable")
        return self._check(self.c.get(f"/api/v1/cloud/sync/{path}", HTTP_AUTHORIZATION=f"Branch {self.token}"))


def tx(demo, code="B1", **kw):
    return CloudLink(demo["cloud_tokens"][code], **kw)


guest = APIClient()


def place(code="B1", items=None, service="pickup", payment="cod", key=None, **extra):
    items = items or [dict(COLA)]
    q = guest.post(f"/api/v1/store/demo/{code}/quotes", {"items": items, "service_type": service, **extra},
                   format="json")
    assert q.status_code == 200, q.content
    body = {"items": items, "service_type": service, "payment": payment, "customer_name": "Guest",
            "phone": "03001234567", "expected_total_minor": q.json()["total_minor"], **extra}
    h = {"HTTP_IDEMPOTENCY_KEY": str(key or uuid.uuid4())}
    return guest.post(f"/api/v1/store/demo/{code}/orders", body, format="json", **h)


def closed_sale(cashier, items):
    cid = cashier.post("checks", {"service_type": "counter"}).json()["check_id"]
    add_round(cashier, cid, items)
    if cashier.get("shifts").json()["shift"] is None:
        cashier.post("shifts", {"opening_float_minor": 0})
    d = cashier.get(f"checks/{cid}").json()
    t = cashier.post(f"checks/{cid}/tenders", {"method": "cash", "amount_minor": d["balance_minor"],
                                                "expected_revision": d["revision"]}).json()
    assert cashier.post(f"checks/{cid}/close", {"expected_revision": t["revision"]}).status_code == 200
    return cid


def test_a15_retransmitted_batch_counted_once_and_gaps_wait(demo, cashier):
    b = demo["branch"]
    closed_sale(cashier, [MIXED_PIZZA])
    t = tx(demo)
    from apps.core.models import SyncOutbox
    events = [{"event_id": str(e.event_id), "stream_position": e.stream_position, "event_type": e.event_type,
               "aggregate_type": e.aggregate_type, "aggregate_id": str(e.aggregate_id), "revision": e.revision,
               "payload": e.payload} for e in SyncOutbox.objects.filter(branch=b).order_by("stream_position")]
    n = len(events)
    # Out of order: the tail first → nothing applied, gap reported
    r = t.post("events", {"events": events[2:]})
    assert r["acked_position"] == 0 and r["missing"] == [[1, 2]]
    assert not DailyProjection.objects.exists()
    r = t.post("events", {"events": events[:2]})
    assert r["acked_position"] == n
    # Cloud retransmission of the whole batch: projection unchanged
    t.post("events", {"events": events})
    p = DailyProjection.objects.get(branch=b)
    assert p.checks_closed == 1 and p.sales_minor == 190000 and p.tenders == {"cash": 190000}
    assert CloudEvent.objects.count() == n
    # Branch uploader acks contiguous positions only
    assert upload(b, t)["acked_position"] == n
    assert not SyncOutbox.objects.filter(branch=b, acked_at__isnull=True).exists()
    # A conflicting event at an existing position is quarantined
    bad = {**events[0], "event_id": str(uuid.uuid4()), "stream_position": n + 1}
    t.post("events", {"events": [bad]})
    bad2 = {**events[0], "event_id": str(uuid.uuid4()), "stream_position": n + 1}
    r = t.post("events", {"events": [bad2]})
    assert SyncException.objects.filter(stream_position=n + 1).exists() and r["acked_position"] == n + 1


def test_a15_web_acceptance_replay_one_order_one_dispatch(demo):
    b = demo["branch"]
    t = tx(demo)
    sync_once(b, t)  # heartbeat: branch online
    r = place(items=[dict(MIXED_PIZZA, fire="now")])
    assert r.status_code == 201, r.content
    token = r.json()["token"]
    assert r.json()["status"] == "pending"
    res = pull_inbox(b, t)
    assert res["processed"][0]["status"] == "accepted"
    payload = InboxCommand.objects.get().payload
    # Branch replays the acceptance (e.g. re-delivered command): same result, no second order or dispatch
    again = import_offer(b, payload)
    assert again["check_id"] == res["processed"][0]["check_id"]
    assert Check.objects.filter(channel="web").count() == 1
    assert ProductionTicket.objects.count() == 1 and TicketItem.objects.count() == 1
    track = guest.get(f"/api/v1/track/{token}").json()
    assert track["status"] == "accepted" and track["order_number"].startswith("P")
    # The web check's events reach the owner projection exactly once
    sync_once(b, t)
    sync_once(b, t)
    assert Check.objects.get(channel="web").service_type == "pickup"


def test_a16_ack_lost_past_deadline_converges_on_acceptance(demo, settings):
    b = demo["branch"]
    sync_once(b, tx(demo))
    token = place().json()["token"]
    order = OnlineOrder.objects.get(token=token)
    # Branch accepts before the deadline, but the acknowledgment is lost
    res = pull_inbox(b, tx(demo, fail_ack=True))
    assert res["processed"][0]["status"] == "accepted"
    accepted_check = res["processed"][0]["check_id"]
    OnlineOrder.objects.filter(pk=order.pk).update(offer_deadline=timezone.now() - timedelta(seconds=1))
    t = guest.get(f"/api/v1/track/{token}").json()
    assert t["status"] == "checking"  # never cancelled or reassigned on timeout alone
    assert InboxCommand.objects.count() == 1
    # Next cycle: the cloud re-delivers, the branch replays the committed result, the ack goes through
    pull_inbox(b, tx(demo))
    t = guest.get(f"/api/v1/track/{token}").json()
    assert t["status"] == "accepted"
    assert OnlineOrder.objects.get(pk=order.pk).branch_check_id == uuid.UUID(accepted_check)
    assert Check.objects.filter(channel="web").count() == 1


def test_expired_offer_received_late_is_rejected(demo):
    b = demo["branch"]
    sync_once(b, tx(demo))
    token = place().json()["token"]
    cmd = InboxCommand.objects.get()
    payload = {**cmd.payload, "deadline": (timezone.now() - timedelta(seconds=5)).isoformat()}
    assert import_offer(b, payload)["status"] == "rejected"
    assert not Check.objects.filter(channel="web").exists()
    _ = token


def test_storefront_pauses_on_stale_heartbeat_and_price_change(demo, settings):
    b = demo["branch"]
    r = place()
    assert r.status_code == 503 and r.json()["code"] == "branch_unavailable"  # never synced: offline
    sync_once(b, tx(demo))
    q = guest.post("/api/v1/store/demo/B1/quotes", {"items": [dict(COLA)]}, format="json").json()
    r = guest.post("/api/v1/store/demo/B1/orders", {"items": [dict(COLA)], "customer_name": "A",
                                                     "phone": "03001234567",
                                                     "expected_total_minor": q["total_minor"] - 1}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "price_changed" and r.json()["total_minor"] == 12000
    key = uuid.uuid4()
    a, c = place(key=key), place(key=key)
    assert a.status_code == 201 and c.status_code == 200 and a.json()["token"] == c.json()["token"]
    menu = guest.get("/api/v1/store/demo/B1/menu").json()
    assert "station" not in menu["categories"][0]["products"][0]


def test_a20_release_at_b1_b2_keeps_menu_and_local_sellout(demo):
    b1, b2 = demo["branch"], demo["branch2"]
    owner = owner_client("ayesha", "8888")
    rel = owner.get("/api/v1/owner/releases").json()["releases"][0]
    r = owner.post("/api/v1/owner/releases", {"base_release_id": rel["id"], "name": "Summer",
                                              "price_changes": [{"product": "cola-500", "variant": "regular",
                                                                 "price_minor": 15000}]}, format="json")
    assert r.status_code == 201, r.content
    new = r.json()
    p = owner.post(f"/api/v1/owner/releases/{new['id']}/preview", {"items": [dict(COLA)]}, format="json").json()
    assert p["total_minor"] == 15000
    r = owner.post(f"/api/v1/owner/releases/{new['id']}/publish", {"branch_ids": [str(b1.pk)]}, format="json")
    assert r.status_code == 200 and r.json()["activations"] == [{"branch": "B1", "release": 2, "changed": True}]
    # B2 manager sells out fries locally
    nadia = Staff(b2, "nadia", "7777")
    assert nadia.post("availability", {"target_kind": "product", "target_code": "fries", "available": False,
                                       "reason": "no potatoes"}).status_code == 200
    for code in ("B1", "B2"):
        sync_once(type(b1).objects.get(code=code), tx(demo, code))
    q1 = guest.post("/api/v1/store/demo/B1/quotes", {"items": [dict(COLA)]}, format="json").json()
    q2 = guest.post("/api/v1/store/demo/B2/quotes", {"items": [dict(COLA)]}, format="json").json()
    assert (q1["total_minor"], q1["menu_version"], q2["total_minor"], q2["menu_version"]) == (15000, 2, 12000, 1)
    fries = {"product": "fries", "variant": "regular", "quantity": 1}
    r = guest.post("/api/v1/store/demo/B2/quotes", {"items": [fries]}, format="json")
    assert r.status_code == 422 and r.json()["code"] == "sold_out"
    m1 = guest.get("/api/v1/store/demo/B1/menu").json()
    m2 = guest.get("/api/v1/store/demo/B2/menu").json()
    avail = lambda m: {p["code"]: p["available"] for c in m["categories"] for p in c["products"]}  # noqa: E731
    assert avail(m1)["fries"] and not avail(m2)["fries"]
    # B1 order (fries available there) is dispatched at B1 only
    r = place("B1", items=[fries])
    assert r.status_code == 201
    pull_inbox(b1, tx(demo, "B1"))
    pull_inbox(b2, tx(demo, "B2"))
    web = Check.objects.get(channel="web")
    assert web.branch_id == b1.pk and ProductionTicket.objects.filter(check_ref=web).get().station.branch_id == b1.pk
    # Existing B2 lines keep v1; overview shows per-branch release
    ov = owner.get("/api/v1/owner/overview").json()["branches"]
    assert {b["code"]: b["release"]["version"] for b in ov} == {"B1": 2, "B2": 1}


def owner_client(username, pin):
    c = APIClient()
    r = c.post("/api/v1/owner/sessions", {"tenant": "demo", "username": username, "pin": pin}, format="json")
    assert r.status_code == 201, r.content
    c.credentials(HTTP_AUTHORIZATION=f"Owner {r.json()['token']}")
    return c


def test_a17_scope_reports_exports_and_sync_revocation(demo, cashier, manager):
    b1, b2 = demo["branch"], demo["branch2"]
    closed_sale(cashier, [dict(COLA, client_line_id="c")])
    owner = owner_client("ayesha", "8888")
    r = owner.get("/api/v1/owner/reports/sales", {"export": "csv"})
    text = r.content.decode()
    assert r.status_code == 200 and "# branches: B1, B2" in text and "# currency: PKR" in text
    assert "net_merchandise_minor" in text
    # B2 manager sees B2 only; B1 is invisible (404), not forbidden
    nadia = owner_client("nadia", "7777")
    assert [b["code"] for b in nadia.get("/api/v1/owner/overview").json()["branches"]] == ["B2"]
    assert nadia.get("/api/v1/owner/reports/sales", {"branches": str(b1.pk)}).status_code == 404
    assert nadia.get("/api/v1/owner/reports/sales", {"branches": "B1"}).status_code == 404
    assert nadia.post("/api/v1/owner/releases", {}, format="json").status_code == 403  # manager cannot publish
    # Branch session cannot read another branch's checks, reports or exports
    other = Check.objects.filter(branch=b1).first()
    c = APIClient()
    h = cashier.headers(False)
    assert c.get(f"/api/v1/branches/{b2.pk}/checks/{other.pk}", **h).status_code == 404
    assert c.get(f"/api/v1/branches/{b2.pk}/reports/sales", **h).status_code == 404
    assert cashier.get("reports/sales").status_code == 403
    rep = manager.get("reports/sales", export="csv")
    assert rep.status_code == 200 and "# branches: B1\n" in rep.content.decode()
    j = manager.get("reports/collections").json()
    assert j["scope"]["branches"] == ["B1"] and j["rows"][0]["method"] == "cash"
    # Sync credential revocation takes effect on receipt
    from apps.cloud.models import CloudBranch
    CloudBranch.objects.filter(branch=b1).update(revoked_at=timezone.now())
    r = APIClient().post("/api/v1/cloud/sync/events", {"events": []}, format="json",
                         HTTP_AUTHORIZATION=f"Branch {demo['cloud_tokens']['B1']}")
    assert r.status_code == 401


def test_a23_prepaid_duplicate_and_late_callbacks(demo, settings):
    settings.ONLINE_PAYMENTS_ENABLED = True
    b = demo["branch"]
    sync_once(b, tx(demo))
    r = place(payment="prepaid")
    assert r.status_code == 201 and r.json()["status"] == "awaiting_payment"
    order = OnlineOrder.objects.get(token=r.json()["token"])
    assert not InboxCommand.objects.exists()  # no kitchen before payment
    pay = order.provider_payment

    def cb(cid, status="authorized", amount=None, ref=None, secret_ok=True):
        body = json.dumps({"callback_id": cid, "provider_ref": ref or pay.provider_ref, "status": status,
                           "amount_minor": amount if amount is not None else pay.amount_minor,
                           "currency": "PKR"}).encode()
        sig = sign(body) if secret_ok else "bad"
        return guest.post("/api/v1/payments/fakepay/webhook", body, content_type="application/json",
                          HTTP_X_SIGNATURE=sig)

    assert cb("cb1", secret_ok=False).status_code == 401
    assert cb("cb0", amount=1).json()["outcome"] == "rejected_mismatch"
    assert cb("cb1").json()["outcome"] == "authorized_offered"
    assert cb("cb1").json()["duplicate"] is True
    assert cb("cb2").json()["outcome"] == "already_processed"
    assert InboxCommand.objects.count() == 1
    res = pull_inbox(b, tx(demo))
    assert res["processed"][0]["status"] == "accepted"
    web = Check.objects.get(channel="web")
    assert web.tenders.get().method == "online" and web.tenders.get().external_ref == pay.provider_ref
    # Late success after the payment window: no fulfilment, refund required
    r = place(payment="prepaid")
    late = OnlineOrder.objects.get(token=r.json()["token"])
    type(pay).objects.filter(order=late).update(expires_at=timezone.now() - timedelta(seconds=1))
    out = cb("cb3", ref=late.provider_payment.provider_ref, amount=late.total_minor).json()
    assert out["outcome"] == "late_success_refund_required"
    late.refresh_from_db()
    assert late.status == "expired" and late.provider_payment.status == "refund_required"
    assert InboxCommand.objects.count() == 1


def test_uploader_backs_off_when_cloud_unreachable(demo, cashier):
    b = demo["branch"]
    closed_sale(cashier, [dict(COLA, client_line_id="c")])
    r = upload(b, tx(demo, down=True))
    assert "error" in r and r["retry_in"] > 0
    assert upload(b, tx(demo))["skipped"] == "backoff"
    from apps.ops.models import SyncState
    SyncState.objects.filter(branch=b).update(backoff_until=None)
    assert upload(b, tx(demo))["acked_position"] > 0
    h = cashier.get("devices/health").json()["cloud"]
    assert h["status"] == "ok" and h["pending_events"] == 0


@pytest.mark.parametrize("_", [0])
def test_a24_business_day_cutoff(demo, cashier, manager, _):
    from unittest import mock

    from django.utils import timezone as tz
    b = demo["branch"]
    real = tz.now()
    local = real.astimezone(b.tz)
    cutoff = (local + timedelta(minutes=60)).replace(second=0, microsecond=0)
    type(b).objects.filter(pk=b.pk).update(day_cutoff=cutoff.time())
    before, after = real + timedelta(minutes=30), real + timedelta(minutes=90)
    with mock.patch("django.utils.timezone.now", return_value=before):
        cid = closed_sale(cashier, [dict(COLA, client_line_id="c")])
        d1 = cashier.get(f"checks/{cid}").json()
    with mock.patch("django.utils.timezone.now", return_value=after):
        cid2 = closed_sale(cashier, [dict(COLA, client_line_id="c")])
        d2 = cashier.get(f"checks/{cid2}").json()
        r = cashier.post(f"tenders/{d1['tenders'][0]['id']}/refunds",
                         {"amount_minor": 12000, "reason": "flat", "approval": {"username": "zara", "pin": "9999"}})
        assert r.status_code == 201
    from datetime import date
    bd1, bd2 = date.fromisoformat(str(d1["business_date"])), date.fromisoformat(str(d2["business_date"]))
    assert bd2 - bd1 == timedelta(days=1)
    assert r.json()["business_date"] == str(bd2) or str(r.json()["business_date"]) == str(bd2)
    from apps.cash.models import Tender
    assert str(Tender.objects.get(check_ref_id=cid).business_date) == str(bd1)  # original sale day kept
    rows = manager.get("reports/sales", **{"from": str(bd1), "to": str(bd2)}).json()["rows"]
    by = {r_["business_date"]: r_ for r_ in rows}
    assert by[str(bd1)]["gross_items_minor"] == 12000 and by[str(bd1)]["returns_minor"] == 0
    assert by[str(bd2)]["returns_minor"] == 12000 and by[str(bd2)]["net_merchandise_minor"] == 0
