"""Encrypted backups, restore drills, host fencing and the recovery reconciliation report (spec 02, A19).

Backup = pg_dump (custom format) of the branch database — which includes the unreplayed outbox and configuration —
encrypted with Fernet (AES-128-CBC + HMAC) before it touches disk. A restore drill restores the newest backup into a
scratch database and compares key counts, so "last verified backup" is shown, not assumed.
"""
import base64
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import connection
from django.utils import timezone

from .models import BackupRecord

KEY_TABLES = ["orders_check", "cash_tender", "cash_drawermovement", "core_syncoutbox", "kitchen_printjob",
              "fulfillment_ridercollection", "stock_stockmovement"]


def pg_tool(name):
    """Full path of a PostgreSQL client tool when VIBEORDER_PG_BIN is set (Windows services have no useful PATH)."""
    if settings.PG_BIN_DIR:
        exe = Path(settings.PG_BIN_DIR) / (name + (".exe" if os.name == "nt" else ""))
        return str(exe)
    return name


def prune_backups(keep=None, keep_daily=None):
    """Keep the newest `keep` backups plus the first backup of each of the last `keep_daily` days. Deletes the
    files and marks their records; returns the number removed."""
    keep = settings.BACKUP_KEEP if keep is None else keep
    keep_daily = settings.BACKUP_KEEP_DAILY if keep_daily is None else keep_daily
    recs = list(BackupRecord.objects.filter(kind="backup", ok=True).exclude(path="").order_by("-created_at"))
    keepers = {r.pk for r in recs[:keep]}
    days = {}
    for r in reversed(recs):  # oldest first → first backup of each day
        days.setdefault(timezone.localtime(r.created_at).date(), r)
    for d in sorted(days, reverse=True)[:keep_daily]:
        keepers.add(days[d].pk)
    removed = 0
    for r in recs:
        if r.pk in keepers:
            continue
        try:
            Path(r.path).unlink(missing_ok=True)
        except OSError:
            continue
        r.detail = {**(r.detail or {}), "pruned_at": timezone.now().isoformat()}
        r.path = ""
        r.save(update_fields=["path", "detail"])
        removed += 1
    return removed


def _fernet():
    key = settings.BACKUP_KEY
    if not key:
        key = base64.urlsafe_b64encode(hashlib.sha256(("backup:" + settings.SECRET_KEY).encode()).digest())
    return Fernet(key)


def _db():
    db = connection.settings_dict
    env = {**os.environ}
    if db.get("PASSWORD"):
        env["PGPASSWORD"] = db["PASSWORD"]
    args = []
    if db.get("HOST"):
        args += ["-h", db["HOST"]]
    if db.get("PORT"):
        args += ["-p", str(db["PORT"])]
    if db.get("USER"):
        args += ["-U", db["USER"]]
    return db["NAME"], args, env


def backup():
    from apps.core.models import SyncOutbox
    name, args, env = _db()
    out_dir = Path(settings.BACKUP_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    pending = SyncOutbox.objects.filter(acked_at__isnull=True).count()
    dump = subprocess.run([pg_tool("pg_dump"), "-Fc", *args, name], capture_output=True, env=env, check=False)
    if dump.returncode != 0:
        rec = BackupRecord.objects.create(host_id=settings.HOST_ID, path="", ok=False,
                                          detail={"error": dump.stderr.decode()[-300:]})
        return rec
    blob = _fernet().encrypt(dump.stdout)
    path = out_dir / f"vibeorder-{settings.HOST_ID}-{timezone.now():%Y%m%dT%H%M%S%f}.vob"
    path.write_bytes(blob)
    try:
        os.chmod(path, 0o600)
    except OSError:  # Windows: rely on the service account's folder ACL
        pass
    rec = BackupRecord.objects.create(host_id=settings.HOST_ID, path=str(path), size_bytes=len(blob),
                                      sha256=hashlib.sha256(blob).hexdigest(), outbox_pending=pending,
                                      detail={"database": name, "plain_bytes": len(dump.stdout)})
    return rec


def restore(path, target_db, drop_existing=False):
    """Decrypt a backup and restore it into target_db (never the live database)."""
    name, args, env = _db()
    if target_db == name:
        raise ValueError("Refusing to restore over the live database; restore to a new database and switch hosts")
    blob = Path(path).read_bytes()
    plain = _fernet().decrypt(blob)
    if drop_existing:
        subprocess.run([pg_tool("dropdb"), "--if-exists", *args, target_db], env=env, check=True, capture_output=True)
    subprocess.run([pg_tool("createdb"), *args, target_db], env=env, check=True, capture_output=True)
    with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as f:
        f.write(plain)
        tmp = f.name
    try:
        r = subprocess.run([pg_tool("pg_restore"), "--no-owner", "-d", target_db, *args, tmp], env=env, capture_output=True)
    finally:
        os.unlink(tmp)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode()[-500:])
    return target_db


def _counts(dbname, args, env, tables):
    sql = " UNION ALL ".join(f"SELECT '{t}', count(*) FROM {t}" for t in tables)
    r = subprocess.run([pg_tool("psql"), "-At", "-F", "\t", *args, "-d", dbname, "-c", sql], env=env, capture_output=True,
                       check=True)
    return {line.split("\t")[0]: int(line.split("\t")[1]) for line in r.stdout.decode().splitlines() if line}


def restore_drill(record=None):
    """Restore the newest (or given) backup into a scratch DB and compare key counts with the backup's snapshot."""
    record = record or BackupRecord.objects.filter(kind="backup", ok=True).order_by("-created_at").first()
    if record is None:
        return BackupRecord.objects.create(kind="restore_drill", host_id=settings.HOST_ID, path="", ok=False,
                                           detail={"error": "no backup to restore"})
    name, args, env = _db()
    scratch = f"{name}_drill"
    started = timezone.now()
    try:
        restore(record.path, scratch, drop_existing=True)
        restored = _counts(scratch, args, env, KEY_TABLES)
        ok = all(v >= 0 for v in restored.values())
        detail = {"backup_id": record.pk, "restored_counts": restored, "seconds": (timezone.now() - started).seconds}
    except Exception as exc:  # noqa: BLE001 - recorded for the operator
        ok, detail = False, {"backup_id": record.pk, "error": str(exc)[:300]}
    finally:
        subprocess.run([pg_tool("dropdb"), "--if-exists", *args, scratch], env=env, capture_output=True)
    return BackupRecord.objects.create(kind="restore_drill", host_id=settings.HOST_ID, path=record.path, ok=ok,
                                       detail=detail)


def backup_status():
    last = BackupRecord.objects.filter(kind="backup", ok=True).order_by("-created_at").first()
    drill = BackupRecord.objects.filter(kind="restore_drill").order_by("-created_at").first()
    now = timezone.now()
    age = int((now - last.created_at).total_seconds() // 60) if last else None
    return {
        "last_backup_at": last.created_at if last else None, "age_minutes": age,
        "stale": age is None or age > settings.BACKUP_MAX_AGE_MINUTES,
        "last_drill_at": drill.created_at if drill else None, "last_drill_ok": drill.ok if drill else None,
        "host_id": settings.HOST_ID,
    }


def take_over(branch, reason, staff=None):
    """Make this host the only writable host for the branch. The old host must already be isolated."""
    from apps.core.models import AuditEntry
    prev = branch.writable_host
    branch.writable_host = settings.HOST_ID
    branch.fence_epoch += 1
    branch.revision += 1
    branch.save(update_fields=["writable_host", "fence_epoch", "revision", "updated_at"])
    AuditEntry.objects.create(branch=branch, staff=staff, action="host.take_over", aggregate_type="Branch",
                              aggregate_id=branch.pk, reason=reason,
                              data={"previous_host": prev, "host": settings.HOST_ID, "epoch": branch.fence_epoch})
    return {"writable_host": branch.writable_host, "fence_epoch": branch.fence_epoch, "previous_host": prev}


def reconciliation(branch):
    """What to compare against physical records before resuming after a restore (A19)."""
    from apps.cash.models import DrawerShift, Refund
    from apps.core.models import SyncOutbox
    from apps.fulfillment.services import rider_balances
    from apps.kitchen.models import PrintJob
    from apps.ops.models import OnlineImport
    from apps.orders.models import Check
    from apps.orders.services import check_amounts

    open_checks = Check.objects.filter(branch=branch, status="open").prefetch_related("lines")
    return {
        "host": {"this_host": settings.HOST_ID, "writable_host": branch.writable_host or "(any)",
                 "fence_epoch": branch.fence_epoch,
                 "writable_here": not branch.writable_host or branch.writable_host == settings.HOST_ID},
        "backup": backup_status(),
        "open_checks": [{"id": str(c.pk), "token": c.token, "service_type": c.service_type,
                         "balance_minor": check_amounts(c)["balance_minor"], "opened_at": c.opened_at}
                        for c in open_checks],
        "accepted_web_orders": [{"request_id": str(i.request_id), "token": i.result.get("token"),
                                 "ack_pending": i.ack_pending}
                                for i in OnlineImport.objects.filter(branch=branch, status="accepted")
                                .order_by("-created_at")[:50]],
        "pending_sync_events": SyncOutbox.objects.filter(branch=branch, acked_at__isnull=True).count(),
        "drawers": [{"id": str(s.pk), "status": s.status, "drawer_code": s.drawer_code, "opened_at": s.opened_at}
                    for s in DrawerShift.objects.filter(branch=branch).exclude(status="closed")],
        "print_exceptions": PrintJob.objects.filter(branch=branch,
                                                    status__in=["outcome_unknown", "failed_before_send"]).count(),
        "pending_refunds": Refund.objects.filter(branch=branch, status="pending").count(),
        "rider_outstanding_minor": sum(r["outstanding_minor"] for r in rider_balances(branch)["riders"]),
        "server_time": timezone.now(),
    }


def read_state(max_age_seconds=20):
    """Branch supervisor state (manage.py run_branch); None when no supervisor ever ran on this host (dev)."""
    import json
    from datetime import datetime
    path = settings.RUN_DIR / "supervisor.json"
    if not path.exists():
        return None
    try:
        st = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    updated = datetime.fromisoformat(st["updated_at"])
    st["stale"] = st.get("stopped") or (timezone.now() - updated).total_seconds() > max_age_seconds
    return st
