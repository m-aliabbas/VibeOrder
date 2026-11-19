"""Branch supervisor: the one process a service manager starts on the branch host.

Runs and watches:
  server   manage.py serve           (waitress, behind the TLS proxy)
  print    manage.py print_worker    (durable print queue)
  sync-XX  manage.py sync_worker     (one per --sync branch code, when the cloud link is configured)
and every VIBEORDER_BACKUP_INTERVAL minutes takes an encrypted backup, prunes old ones, and once a day runs a
restore drill. A child that exits is restarted with exponential backoff (1 s → 60 s; reset after 60 s of uptime).
Each child's output goes to VIBEORDER_LOG_DIR/<name>.log. State is written to VIBEORDER_RUN_DIR/supervisor.json
so `GET devices/health` and the alert centre can show it. One supervisor per host (lock file).

Stop with Ctrl+C / SIGTERM (Windows: the scheduled task's "End" or CTRL_BREAK): children are terminated. Killing
them is safe by design: commands are transactional, print jobs are leased and recovered as outcome_unknown.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

MANAGE = str(Path(settings.BASE_DIR) / "manage.py")
MAX_LOG_BYTES = 10 * 1024 * 1024


class _Lock:
    """Exclusive, crash-safe host lock (released by the OS if the process dies)."""

    def __init__(self, path):
        self.path = Path(path)
        self.fh = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            return False
        return True


class Child:
    def __init__(self, name, argv):
        self.name, self.argv = name, argv
        self.proc = None
        self.log = None
        self.started_at = None
        self.restarts = 0
        self.recent_exits = []  # timestamps
        self.fails_in_row = 0
        self.next_start = 0.0
        self.last_exit_code = None
        self.last_exit_at = None

    def _open_log(self):
        settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = settings.LOG_DIR / f"{self.name}.log"
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            old = path.with_suffix(".log.1")
            old.unlink(missing_ok=True)
            path.rename(old)
        return open(path, "ab", buffering=0)

    def start(self):
        self.log = self._open_log()
        self.log.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} starting {' '.join(self.argv[1:])}\n".encode())
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        self.proc = subprocess.Popen(self.argv, stdout=self.log, stderr=subprocess.STDOUT, cwd=settings.BASE_DIR,
                                     env=env, creationflags=flags)
        self.started_at = time.time()

    def check(self, now):
        """Returns True when the child just exited."""
        if self.proc is None or self.proc.poll() is None:
            return False
        self.last_exit_code = self.proc.returncode
        self.last_exit_at = now
        self.recent_exits = [t for t in self.recent_exits if now - t < 600] + [now]
        uptime = now - (self.started_at or now)
        self.fails_in_row = 0 if uptime > 60 else self.fails_in_row + 1
        self.next_start = now + min(60, 2 ** self.fails_in_row) if self.fails_in_row else now + 1
        self.proc = None
        if self.log:
            self.log.write(f"=== exited code={self.last_exit_code} after {uptime:.0f}s\n".encode())
            self.log.close()
            self.log = None
        return True

    def stop(self, timeout=10):
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def state(self, now):
        running = self.proc is not None and self.proc.poll() is None
        return {
            "status": "running" if running else "restarting",
            "pid": self.proc.pid if running else None,
            "started_at": _iso(self.started_at) if running else None,
            "restarts": self.restarts,
            "exits_last_10min": len([t for t in self.recent_exits if now - t < 600]),
            "last_exit_code": self.last_exit_code,
            "last_exit_at": _iso(self.last_exit_at),
            "next_start_in": None if running else max(0, round(self.next_start - now)),
        }


def _iso(ts):
    return datetime.fromtimestamp(ts).astimezone().isoformat() if ts else None


class Command(BaseCommand):
    help = "Supervise the branch server, print worker and sync workers, and take scheduled backups."

    def add_arguments(self, parser):
        parser.add_argument("--host", default=os.environ.get("VIBEORDER_LISTEN_HOST", "127.0.0.1"))
        parser.add_argument("--port", default=os.environ.get("VIBEORDER_LISTEN_PORT", "8000"))
        parser.add_argument("--sync", action="append", default=None,
                            help="Branch code to sync with the cloud (repeatable). Default: VIBEORDER_SYNC_BRANCHES")
        parser.add_argument("--sync-dev", action="store_true", help="use seeded dev cloud credentials")
        parser.add_argument("--no-print", action="store_true")
        parser.add_argument("--no-backup", action="store_true")
        parser.add_argument("--drill-hour", type=int, default=int(os.environ.get("VIBEORDER_DRILL_HOUR", "4")),
                            help="Local hour for the daily restore drill (-1 = never)")
        parser.add_argument("--run-for", type=float, default=0, help="Stop after N seconds (tests)")

    def handle(self, *args, **o):
        lock = _Lock(settings.RUN_DIR / "supervisor.lock")
        if not lock.acquire():
            raise CommandError(f"Another branch supervisor is already running on this host ({lock.path})")
        py = sys.executable
        children = [Child("server", [py, MANAGE, "serve", "--host", o["host"], "--port", str(o["port"])])]
        if not o["no_print"]:
            children.append(Child("print", [py, MANAGE, "print_worker"]))
        sync = o["sync"] if o["sync"] is not None else [
            c for c in os.environ.get("VIBEORDER_SYNC_BRANCHES", "").split(",") if c]
        for code in sync:
            children.append(Child(f"sync-{code}", [py, MANAGE, "sync_worker", "--branch", code,
                                                   *(["--dev"] if o["sync_dev"] else [])]))
        self.stopping = False
        self.backup_state = {"last_at": None, "last_ok": None, "last_error": "", "pruned": 0, "last_drill_at": None,
                             "last_drill_ok": None, "running": False}
        for sig in [signal.SIGINT, signal.SIGTERM] + ([signal.SIGBREAK] if os.name == "nt" else []):
            signal.signal(sig, self._on_signal)
        started = time.time()
        self.stdout.write(f"branch supervisor pid={os.getpid()} host={settings.HOST_ID} children="
                          f"{', '.join(c.name for c in children)} backups={'off' if o['no_backup'] else 'on'}")
        self.stdout.flush()
        for c in children:
            c.start()
        next_backup = time.time() + 20  # first backup shortly after start, then every interval
        last_state = 0.0
        try:
            while not self.stopping:
                now = time.time()
                for c in children:
                    if c.check(now):
                        self.stdout.write(f"{c.name} exited code={c.last_exit_code}; restart in "
                                          f"{c.next_start - now:.0f}s")
                        self.stdout.flush()
                    if c.proc is None and now >= c.next_start:
                        c.restarts += 1
                        c.start()
                if not o["no_backup"] and now >= next_backup and not self.backup_state["running"]:
                    next_backup = now + settings.BACKUP_INTERVAL_MINUTES * 60
                    threading.Thread(target=self._backup, args=(o["drill_hour"],), daemon=True).start()
                if now - last_state >= 2:
                    self._write_state(children, started, now)
                    last_state = now
                if o["run_for"] and now - started >= o["run_for"]:
                    break
                time.sleep(0.5)
        finally:
            for c in children:
                c.stop()
            self._write_state(children, started, time.time(), stopped=True)
            self.stdout.write("branch supervisor stopped")

    def _on_signal(self, *_):
        self.stopping = True

    def _backup(self, drill_hour):
        from apps.ops.models import BackupRecord
        from apps.ops.services import backup, prune_backups, restore_drill
        st = self.backup_state
        st["running"] = True
        try:
            rec = backup()
            st["last_at"], st["last_ok"] = timezone.now().isoformat(), rec.ok
            st["last_error"] = "" if rec.ok else str((rec.detail or {}).get("error", ""))[:300]
            if rec.ok:
                st["pruned"] += prune_backups()
                local = timezone.localtime()
                today_drill = BackupRecord.objects.filter(
                    kind="restore_drill", created_at__gte=local.replace(hour=0, minute=0, second=0, microsecond=0)
                ).exists()
                if drill_hour >= 0 and local.hour >= drill_hour and not today_drill:
                    d = restore_drill(rec)
                    st["last_drill_at"], st["last_drill_ok"] = timezone.now().isoformat(), d.ok
        except Exception as exc:  # noqa: BLE001 - surfaced through supervisor.json and alerts
            st["last_at"], st["last_ok"], st["last_error"] = timezone.now().isoformat(), False, str(exc)[:300]
        finally:
            st["running"] = False
            connection.close()

    def _write_state(self, children, started, now, stopped=False):
        state = {
            "pid": os.getpid(), "host_id": settings.HOST_ID, "started_at": _iso(started), "updated_at": _iso(now),
            "stopped": stopped, "children": {c.name: c.state(now) for c in children},
            "backup": {k: v for k, v in self.backup_state.items()},
        }
        path = settings.RUN_DIR / "supervisor.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1))
        os.replace(tmp, path)

