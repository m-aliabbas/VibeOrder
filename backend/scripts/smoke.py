"""Live end-to-end smoke test against a running branch server (dev data only).

Usage:  .venv/bin/python scripts/smoke.py [--server http://localhost:8000] [--dump-fixtures DIR]
Creates its own pairing codes via Django ORM, then drives the HTTP API exactly like the apps:
waiter opens a table and sends a half/half pizza, cook marks it ready, cashier opens a drawer,
takes cash with change and closes the check. Prints each step; exits non-zero on failure.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vibeorder.settings")
import django  # noqa: E402

django.setup()
from django.utils import timezone  # noqa: E402

from apps.core.models import Branch, PairingCode  # noqa: E402


class Client:
    def __init__(self, server, branch_id=None):
        self.server, self.branch, self.device, self.session = server.rstrip("/"), branch_id, None, None

    def call(self, method, path, body=None, key=None):
        url = path if path.startswith("/") else f"/api/v1/branches/{self.branch}/{path}"
        req = urllib.request.Request(self.server + url, method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        req.add_header("Content-Type", "application/json")
        if self.device:
            req.add_header("X-Device-Token", self.device)
        if self.session:
            req.add_header("Authorization", f"Bearer {self.session}")
        if key:
            req.add_header("Idempotency-Key", key)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def ok(self, method, path, body=None, cmd=False, expect=(200, 201)):
        status, data = self.call(method, path, body, str(uuid.uuid4()) if cmd else None)
        if status not in expect:
            raise SystemExit(f"FAIL {method} {path} -> {status} {data}")
        return data


def staff(server, branch, username, pin, kind="tablet"):
    pc = PairingCode.objects.create(branch=branch, code=PairingCode.new_code(), kind=kind,
                                    expires_at=timezone.now() + timedelta(minutes=5))
    c = Client(server, str(branch.pk))
    c.device = c.ok("POST", "/api/v1/pair", {"code": pc.code, "name": f"smoke-{username}"})["device_token"]
    c.session = c.ok("POST", "sessions", {"username": username, "pin": pin})["session_token"]
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--dump-fixtures")
    ap.add_argument("--skip-web", action="store_true", help="skip the storefront order (no sync worker running)")
    a = ap.parse_args()
    branch = Branch.objects.get(code="B1")
    w, ck, ca = staff(a.server, branch, "bilal", "1111"), staff(a.server, branch, "usman", "4444", "kds"), \
        staff(a.server, branch, "hamza", "3333", "cashier")
    print("✓ paired + signed in waiter, cook, cashier")
    if a.dump_fixtures:
        d = Path(a.dump_fixtures)
        d.mkdir(parents=True, exist_ok=True)
        (d / "menu.json").write_text(json.dumps(w.ok("GET", "menu"), indent=1, ensure_ascii=False))
        (d / "floor.json").write_text(json.dumps(w.ok("GET", "floor"), indent=1))
        (d / "bootstrap_waiter.json").write_text(json.dumps(w.ok("GET", "bootstrap"), indent=1))
        print(f"✓ fixtures written to {d}")
    floor = w.ok("GET", "floor")
    free = next(t for ar in floor["areas"] for t in ar["tables"] if not t["occupied"])
    chk = w.ok("POST", "checks", {"service_type": "dine_in", "table_id": free["id"], "guest_count": 2}, cmd=True)
    print(f"✓ opened table {free['label']} check #{chk['token']}")
    item = {"client_line_id": "smoke-1", "product": "mixed-pizza", "variant": "large", "quantity": 1, "crust": "stuffed",
            "halves": {"left": "tikka", "right": "fajita"}, "options": [{"id": "extra-cheese", "side": "right"}]}
    q = w.ok("POST", "quotes", {"items": [item]})
    assert q["total_minor"] == 190000, q
    key = str(uuid.uuid4())
    body = {"quote_id": q["quote_id"], "menu_release": q["menu_release"], "items": [item]}
    s1, r1 = w.call("POST", f"checks/{chk['check_id']}/rounds", body, key)
    s2, r2 = w.call("POST", f"checks/{chk['check_id']}/rounds", body, key)
    assert s1 == s2 == 201 and r1 == r2, (s1, s2)
    print(f"✓ round sent Rs {r1['round_total_minor'] // 100} (retry with same key replayed, no duplicate)")
    pizza = next(s for s in ck.ok("GET", "bootstrap")["stations"] if s["code"] == "pizza")
    items = [i for i in ck.ok("GET", f"stations/{pizza['id']}/queue")["items"] if i["check"]["id"] == chk["check_id"]]
    assert len(items) == 1, items
    ck.ok("POST", f"ticket-items/{items[0]['id']}/transitions", {"to": "preparing"}, cmd=True)
    # Correction after cook started: drop the right-half extra cheese (A09)
    detail = w.ok("GET", f"checks/{chk['check_id']}")
    line = detail["rounds"][0]["lines"][0]
    changed = {**item, "client_line_id": "smoke-1b", "options": []}
    q2 = w.ok("POST", "quotes", {"items": [changed]})
    amend = w.ok("POST", f"checks/{chk['check_id']}/amendments", {
        "action": "modify", "line_id": line["id"], "item": changed, "quote_id": q2["quote_id"],
        "reason": "guest changed mind", "expected_revision": detail["revision"]}, cmd=True)
    assert "RIGHT half: remove Extra cheese" in amend["description"] and amend["total_minor"] == 180000, amend
    queue = ck.ok("GET", f"stations/{pizza['id']}/queue")
    chg = [t for t in queue["changes"] if t["check"]["id"] == chk["check_id"]]
    assert len(chg) == 1, chg
    ck.ok("POST", f"tickets/{chg[0]['id']}/acknowledge", {}, cmd=True)
    items = [i for i in queue["items"] if i["check"]["id"] == chk["check_id"]]
    assert len(items) == 1 and items[0]["status"] == "preparing", items
    print(f"✓ amended after cook start: '{amend['description']}' → Rs 1,800; kitchen acknowledged")
    ck.ok("POST", f"ticket-items/{items[0]['id']}/transitions", {"to": "ready"}, cmd=True)
    print("✓ pizza station marked it ready")
    st, shift = ca.call("GET", "shifts")
    if shift.get("shift") is None:
        ca.ok("POST", "shifts", {"opening_float_minor": 500000}, cmd=True)
        print("✓ drawer opened with Rs 5,000 float")
    rev = ca.ok("GET", f"checks/{chk['check_id']}")["revision"]
    t = ca.ok("POST", f"checks/{chk['check_id']}/tenders", {"method": "cash", "amount_minor": 180000,
                                                             "cash_received_minor": 200000, "expected_revision": rev}, cmd=True)
    assert t["change_minor"] == 20000 and t["balance_minor"] == 0, t
    c = ca.ok("POST", f"checks/{chk['check_id']}/close", {"expected_revision": t["revision"]}, cmd=True)
    print(f"✓ cash 2,000 for 1,800 → change 200; closed receipt {c['receipt_no']}")
    mg = staff(a.server, branch, "zara", "9999")
    tid = t["tender_id"]
    s1, r1 = ca.call("POST", f"tenders/{tid}/refunds", {"amount_minor": 30000, "reason": "cold"}, str(uuid.uuid4()))
    assert s1 == 403 and r1["code"] == "approval_required", (s1, r1)
    rf = mg.ok("POST", f"tenders/{tid}/refunds", {"amount_minor": 30000, "reason": "cold", "goods": "not_returned"}, cmd=True)
    assert rf["status"] == "succeeded" and rf["refundable_minor"] == 150000, rf
    after = ca.ok("GET", f"checks/{chk['check_id']}")
    assert after["status"] == "closed" and after["refunded_minor"] == 30000 and after["balance_minor"] == 0, after
    print("✓ refund Rs 300 cash: cashier needed a manager; check stays closed; Rs 1,500 still refundable")
    # Delivery with cash on delivery → packing → rider → remittance into the drawer once (A14)
    zone = next(z for z in ca.ok("GET", "zones")["zones"] if z["name"] == "Gulberg")
    dchk = ca.ok("POST", "checks", {"service_type": "delivery", "customer_phone": "0300 555 0101",
                                    "customer_name": "Smoke Guest", "address_text": "House 1, Street 2, Gulberg",
                                    "zone_id": zone["id"], "payment": "cod"}, cmd=True)
    cola = {"client_line_id": "smoke-c", "product": "cola-500", "quantity": 1}
    qd = ca.ok("POST", "quotes", {"items": [cola]})
    ca.ok("POST", f"checks/{dchk['check_id']}/rounds", {"quote_id": qd["quote_id"], "items": [cola]}, cmd=True)
    pk = ca.ok("GET", "packing")
    order = next(o for o in pk["orders"] if o["id"] == dchk["check_id"])
    for comp in order["components"]:
        ca.ok("POST", f"ticket-items/{comp['id']}/transitions", {"to": "ready", "expected_revision": comp["revision"]},
              cmd=True)
    ca.ok("POST", f"checks/{dchk['check_id']}/handover", {}, cmd=True)
    rider = next(r for r in ca.ok("GET", "riders")["riders"] if r["name"].startswith("Imran"))
    dl = ca.ok("GET", f"checks/{dchk['check_id']}")["delivery"]
    for to, extra in (("assigned", {"rider_id": rider["id"]}), ("dispatched", {}),
                      ("delivered", {"collected_minor": dl["amount_to_collect_minor"]})):
        dl = ca.ok("POST", f"deliveries/{dl['id']}/transitions", {"to": to, "expected_revision": dl["revision"], **extra},
                   cmd=True)
    assert dl["collected_minor"] == 12000 + 12000 and dl["receipt_no"], dl
    owed = next(r for r in ca.ok("GET", "riders")["riders"] if r["id"] == rider["id"])["outstanding_minor"]
    rem = mg.ok("POST", f"riders/{rider['id']}/remittances", {"amount_minor": owed}, cmd=True)
    assert rem["outstanding_minor"] == 0, rem
    print(f"✓ delivery #{dchk['token']}: packed, dispatched, rider collected Rs 240 COD, remitted Rs {owed // 100}")
    # Web order through the storefront, accepted by the branch via the sync worker (A15/A16)
    if not a.skip_web:
        import time
        g = Client(a.server)
        item = {"product": "water-500", "quantity": 1}
        quote = g.ok("POST", "/api/v1/store/demo/B1/quotes", {"items": [item]})
        st, placed = g.call("POST", "/api/v1/store/demo/B1/orders", {
            "items": [item], "service_type": "pickup", "customer_name": "Smoke Web", "phone": "03001234567",
            "expected_total_minor": quote["total_minor"]}, str(uuid.uuid4()))
        if st == 503:
            raise SystemExit("FAIL storefront says branch offline: start `make sync` (sync worker) or pass --skip-web")
        assert st == 201, (st, placed)
        for _ in range(20):
            tr = g.ok("GET", f"/api/v1/track/{placed['token']}")
            if tr["status"] != "pending":
                break
            time.sleep(1)
        assert tr["status"] == "accepted", tr
        print(f"✓ web order {tr['reference']} accepted by the branch as #{tr['order_number']}")
    jobs = ca.ok("GET", "print-jobs")["jobs"]
    print(f"✓ print jobs: " + ", ".join(f"{j['kind']}:{j['status']}" for j in jobs[:4]))
    print("SMOKE OK")


if __name__ == "__main__":
    main()
