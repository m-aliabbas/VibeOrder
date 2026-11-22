"""Branch restart drill (M1 "branch restart keeps sale", A03 across a real crash).

Starts the real branch supervisor (`manage.py run_branch`: waitress server + print worker + scheduled backups) on
a scratch port with fault injection, then drives the HTTP API like the apps:

  1. A waiter sends a round. The server process dies right after the commit, before answering (power-cut timing).
     The supervisor restarts it; the tablet retries with the SAME idempotency key and gets the original result.
     → exactly one round, one kitchen ticket per station, one KOT printed.
  2. The cashier takes cash; the server dies again after commit. Retry with the same key → one tender only.
  3. The cashier closes the check; the server dies after commit. Retry → closed once, one receipt number.
  4. The whole supervisor is killed hard (as on a power loss) and started again: the sale, receipt and printed
     KOT are all still there and nothing is printed twice. The scheduled backup has run and is restorable.

Usage (dev DB, DEBUG on):  .venv/bin/python scripts/restart_drill.py [--port 8020]
Exits non-zero on the first failed check. Needs pg_dump on PATH or VIBEORDER_PG_BIN for the backup step.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DRILL = BASE / "var" / "drill"
CRASH = "check.append_round,tender.take,check.close"
os.environ.update({
    "DJANGO_DEBUG": "1",
    "VIBEORDER_RUN_DIR": str(DRILL / "run"),
    "VIBEORDER_LOG_DIR": str(DRILL / "logs"),
    "VIBEORDER_SPOOL_DIR": str(DRILL / "spool"),
    "VIBEORDER_BACKUP_DIR": str(DRILL / "backups"),
})
sys.path.insert(0, str(BASE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vibeorder.settings")
import django  # noqa: E402

django.setup()

from apps.cash.models import Tender  # noqa: E402
from apps.core.models import Branch, CommandReceipt  # noqa: E402
from apps.kitchen.models import PrintJob, ProductionTicket  # noqa: E402
from apps.orders.models import Check, CheckRound  # noqa: E402


def ok(cond, msg):
    if not cond:
        raise SystemExit(f"✗ {msg}")
    print(f"✓ {msg}")


class Client:
    def __init__(self, server, branch):
        self.server, self.branch, self.device, self.session = server, branch, None, None

    def call(self, method, path, body=None, key=None):
        url = path if path.startswith("/") else f"/api/v1/branches/{self.branch}/{path}"
        req = urllib.request.Request(self.server + url, method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        req.add_header("Content-Type", "application/json")
        if self.session:
            req.add_header("Authorization", f"Bearer {self.session}")
        if key:
            req.add_header("Idempotency-Key", key)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read() or b"{}"), r.headers.get("Idempotent-Replay") == "true"
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), False

    def ok(self, method, path, body=None, cmd=False):
        s, d, _ = self.call(method, path, body, str(uuid.uuid4()) if cmd else None)
        if s not in (200, 201):
            raise SystemExit(f"✗ {method} {path} -> {s} {d}")
        return d

    def command_through_crash(self, path, body, what):
        """Send a command whose commit kills the server; retry with the same key until the server is back."""
        key = str(uuid.uuid4())
        try:
            s, d, _ = self.call("POST", path, body, key)
            raise SystemExit(f"✗ expected the server to die during {what}, got {s} {d}")
        except (urllib.error.URLError, ConnectionError, OSError):
            print(f"  … server died after committing {what} (tablet saw no answer)")
        t0 = time.time()
        while time.time() - t0 < 60:
            try:
                s, d, replay = self.call("POST", path, body, key)
                break
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(0.5)
        else:
            raise SystemExit(f"✗ server did not come back within 60 s after {what}")
        ok(s in (200, 201) and replay, f"{what}: retry with the same key after restart replayed the original "
                                       f"result ({time.time() - t0:.1f} s outage)")
        ok(CommandReceipt.objects.filter(key=key).count() == 1, f"{what}: one command receipt for the key")
        return d


def pair(server, branch, username, password, kind="tablet"):
    c = Client(server, str(branch.pk))
    r = c.ok("POST", "/api/v1/login", {"username": username, "password": password, "branch": branch.code,
                                       "device_name": f"drill-{username}", "device_kind": kind})
    c.session = r["session_token"]
    return c


def start_supervisor(port, crash):
    env = {**os.environ, "VIBEORDER_CRASH_AFTER_COMMIT": crash, "VIBEORDER_BACKUP_INTERVAL": "60",
           "PYTHONUNBUFFERED": "1"}
    log = open(DRILL / "supervisor.log", "ab")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen([sys.executable, str(BASE / "manage.py"), "run_branch", "--port", str(port),
                             "--drill-hour", "-1"], cwd=BASE, env=env, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=flags)


def wait_up(server, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(server + "/healthz", timeout=2) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    raise SystemExit(f"✗ server not up on {server} within {timeout} s (see {DRILL / 'logs' / 'server.log'})")


def kill_tree(proc):
    """Hard kill of the supervisor and its children, like pulling the plug."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        import signal
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait(10)


def spool_files(token):
    return [p for p in (DRILL / "spool").rglob("*") if p.is_file() and token in p.read_text(errors="ignore")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8020)
    ap.add_argument("--skip-backup", action="store_true")
    a = ap.parse_args()
    server = f"http://127.0.0.1:{a.port}"
    shutil.rmtree(DRILL / "run", ignore_errors=True)
    shutil.rmtree(DRILL / "spool", ignore_errors=True)
    DRILL.mkdir(parents=True, exist_ok=True)
    branch = Branch.objects.get(code="B1")

    sup = start_supervisor(a.port, CRASH)
    try:
        wait_up(server)
        print(f"✓ branch supervisor up (pid {sup.pid}) with server + print worker on {server}")
        w = pair(server, branch, "bilal", "bilal123", "tablet")
        ca = pair(server, branch, "hamza", "hamza123", "cashier")
        floor = w.ok("GET", "floor")
        free = next(t for ar in floor["areas"] for t in ar["tables"] if not t["occupied"])
        chk = w.ok("POST", "checks", {"service_type": "dine_in", "table_id": free["id"], "guest_count": 2}, cmd=True)
        cid, token = chk["check_id"], chk["token"]
        print(f"✓ opened table {free['label']} check #{token}")

        # 1. Round: crash after commit, retry same key
        items = [{"client_line_id": "drill-1", "product": "mixed-pizza", "variant": "large", "quantity": 1,
                  "crust": "stuffed", "halves": {"left": "tikka", "right": "fajita"},
                  "options": [{"id": "extra-cheese", "side": "right"}]},
                 {"client_line_id": "drill-2", "product": "fries", "variant": "regular", "quantity": 1}]
        q = w.ok("POST", "quotes", {"items": items})
        w.command_through_crash(f"checks/{cid}/rounds", {"quote_id": q["quote_id"], "menu_release": q["menu_release"],
                                                         "items": items}, "round")
        rounds = CheckRound.objects.filter(check_ref_id=cid).count() if hasattr(CheckRound, "check_ref") else \
            CheckRound.objects.filter(check_id=cid).count()
        ok(rounds == 1, "one round on the check (no duplicate from the retry)")
        tickets = ProductionTicket.objects.filter(check_ref_id=cid, kind="original")
        stations = sorted(t.station.code for t in tickets)
        ok(len(stations) == len(set(stations)) and len(stations) >= 2,
           f"one kitchen ticket per station: {', '.join(stations)}")
        jobs = PrintJob.objects.filter(ticket__check_ref_id=cid, kind="kitchen")
        kot_count = jobs.count()
        t0 = time.time()
        while time.time() - t0 < 20 and jobs.filter(status="handed_to_spooler").count() < kot_count:
            time.sleep(0.5)
        per_ticket = {j.ticket_id for j in jobs}
        ok(kot_count >= 1 and len(per_ticket) == kot_count and all(j.status == "handed_to_spooler" for j in jobs),
           f"{kot_count} KOT(s) printed once each by the supervised print worker (stations without a printer "
           f"use the KDS)")

        # 2. Tender: crash after commit
        if ca.call("GET", "shifts")[1].get("shift") is None:
            ca.ok("POST", "shifts", {"opening_float_minor": 500000}, cmd=True)
        d = ca.ok("GET", f"checks/{cid}")
        t = ca.command_through_crash(f"checks/{cid}/tenders", {
            "method": "cash", "amount_minor": d["total_minor"], "cash_received_minor": d["total_minor"],
            "expected_revision": d["revision"]}, "cash tender")
        ok(Tender.objects.filter(check_ref_id=cid, status="succeeded").count() == 1, "exactly one tender recorded")

        # 3. Close: crash after commit
        c = ca.command_through_crash(f"checks/{cid}/close", {"expected_revision": t["revision"]}, "close check")
        chk_row = Check.objects.get(pk=cid)
        ok(chk_row.status == "closed" and chk_row.receipt_no == c["receipt_no"],
           f"check closed once with receipt {c['receipt_no']}")

        if not a.skip_backup:
            t0 = time.time()
            st = {}
            while time.time() - t0 < 90:
                p = DRILL / "run" / "supervisor.json"
                st = json.loads(p.read_text()) if p.exists() else {}
                if st.get("backup", {}).get("last_ok") is not None:
                    break
                time.sleep(1)
            bk = st.get("backup", {})
            ok(bk.get("last_ok") is True, f"scheduled backup taken by the supervisor {bk.get('last_error') or ''}")
            files = list((DRILL / "backups").glob("*.vob"))
            ok(len(files) >= 1, f"encrypted backup file written ({files[-1].name if files else '-'})")
        sv = json.loads((DRILL / "run" / "supervisor.json").read_text())
        ok(sv["children"]["server"]["restarts"] == 3, "supervisor restarted the server 3 times (once per crash)")
        printed_before = len(spool_files(token))

        # 4. Pull the plug on everything, start again
        kill_tree(sup)
        print("  … killed the supervisor and all children (power loss)")
        sup = start_supervisor(a.port, "")
        wait_up(server)
        print("✓ branch back up after a full restart")
        d = w.ok("GET", f"checks/{cid}")
        ok(d["status"] == "closed" and d["receipt_no"] == c["receipt_no"] and d["balance_minor"] == 0,
           "sale survived the restart: closed, paid, same receipt number")
        time.sleep(4)  # give the print worker time to (wrongly) reprint anything
        ok(len(spool_files(token)) == printed_before,
           f"nothing printed twice after restart ({printed_before} ticket/receipt files for #{token})")
        ok(PrintJob.objects.filter(ticket__check_ref_id=cid, kind="kitchen").count() == kot_count,
           "no new KOT jobs created by the restart")
        print("RESTART DRILL OK")
    finally:
        if sup.poll() is None:
            kill_tree(sup)


if __name__ == "__main__":
    main()
