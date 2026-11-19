"""Pre-flight check for a branch host. Run before go-live and after any change to the machine.

    manage.py branch_doctor            # PASS / WARN / FAIL per check, exit 1 on any FAIL
    manage.py branch_doctor --printers # also open a TCP connection to each network printer
"""
import os
import shutil
import socket
import subprocess

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = "Check that this host is ready to run a branch (config, database, backups, printers)."

    def add_arguments(self, parser):
        parser.add_argument("--printers", action="store_true", help="test TCP connections to network printers")

    def handle(self, *args, **o):
        self.fails = 0
        prod = not settings.DEBUG
        self.item("DEBUG is off", prod, "set DJANGO_DEBUG=0 in branch.env", warn_only=True)
        self.item("Secret key set", settings.SECRET_KEY != "dev-only-insecure-key-change-me",
                   "set DJANGO_SECRET_KEY (50+ random characters)", warn_only=not prod)
        self.item("Backup encryption key set", bool(settings.BACKUP_KEY),
                   "set VIBEORDER_BACKUP_KEY (generate: manage.py branch_doctor shows how) and keep a copy OFF this "
                   "machine — backups cannot be restored without it", warn_only=not prod)
        self.item("Allowed hosts restricted", settings.ALLOWED_HOSTS != ["*"],
                   "set DJANGO_ALLOWED_HOSTS to the branch host name / IP", warn_only=True)
        self.item("Host id set", settings.HOST_ID != "dev-host", "set VIBEORDER_HOST_ID (e.g. b1-counter-pc)",
                   warn_only=not prod)

        try:
            connection.ensure_connection()
            self.item("Database reachable", True)
            plan = MigrationExecutor(connection).migration_plan(MigrationExecutor(connection).loader.graph.leaf_nodes())
            self.item("Migrations applied", not plan, f"{len(plan)} pending: run manage.py migrate")
        except Exception as exc:  # noqa: BLE001
            self.item("Database reachable", False, str(exc)[:200])
            return self.done()

        from apps.core.models import Branch
        for b in Branch.objects.all():
            fenced = b.writable_host and b.writable_host != settings.HOST_ID
            self.item(f"Branch {b.code} writable from this host", not fenced,
                       f"writable host is '{b.writable_host}'. Isolate it, then manage.py take_over", warn_only=True)

        from apps.ops.services import pg_tool
        dump = pg_tool("pg_dump")
        found = shutil.which(dump) or (os.path.isfile(dump) and dump)
        version = ""
        if found:
            r = subprocess.run([dump, "--version"], capture_output=True, text=True)
            version = r.stdout.strip()
        self.item(f"pg_dump available {version}", bool(found) and bool(version),
                   "install PostgreSQL client tools or set VIBEORDER_PG_BIN")
        for label, d in (("Backup folder", settings.BACKUP_DIR), ("Log folder", settings.LOG_DIR),
                         ("Run folder", settings.RUN_DIR), ("Print spool folder", settings.PRINT_SPOOL_DIR)):
            try:
                d.mkdir(parents=True, exist_ok=True)
                probe = d / ".write-test"
                probe.write_text("ok")
                probe.unlink()
                self.item(f"{label} writable ({d})", True)
            except OSError as exc:
                self.item(f"{label} writable ({d})", False, str(exc))

        from apps.ops.services import backup_status, read_state
        bs = backup_status()
        self.item("Recent backup", not bs["stale"],
                   "no backup in the last "
                   f"{settings.BACKUP_MAX_AGE_MINUTES} min — is the VibeOrder Branch service running?", warn_only=True)
        self.item("Restore drill passed", bs["last_drill_ok"] is True,
                   "run manage.py backup_branch --drill", warn_only=True)
        sv = read_state()
        self.item("Branch supervisor running", sv is not None and not sv["stale"],
                   "start the service (deploy/windows/install-service.ps1 or systemd unit)", warn_only=True)

        from apps.kitchen.models import Printer
        for p in Printer.objects.filter(is_active=True).select_related("branch"):
            if p.transport != "tcp":
                self.item(f"Printer '{p.name}' ({p.branch.code}) is a spool-file fake", False,
                           "configure the real printer (transport tcp, address host:9100) before go-live",
                           warn_only=True)
            elif o["printers"]:
                host, _, port = p.address.partition(":")
                try:
                    with socket.create_connection((host, int(port or 9100)), timeout=3):
                        self.item(f"Printer '{p.name}' reachable at {p.address}", True)
                except OSError as exc:
                    self.item(f"Printer '{p.name}' reachable at {p.address}", False, str(exc))
        return self.done()

    def item(self, label, passed, fix="", warn_only=False):
        if passed:
            self.stdout.write(self.style.SUCCESS("PASS ") + label)
        elif warn_only:
            self.stdout.write(self.style.WARNING("WARN ") + f"{label} — {fix}")
        else:
            self.fails += 1
            self.stdout.write(self.style.ERROR("FAIL ") + f"{label} — {fix}")

    def done(self):
        if not settings.BACKUP_KEY:
            from cryptography.fernet import Fernet
            self.stdout.write(f"\nA new backup key you can use: VIBEORDER_BACKUP_KEY={Fernet.generate_key().decode()}")
        self.stdout.write(f"\n{'NOT READY' if self.fails else 'OK'}: {self.fails} failure(s)")
        if self.fails:
            raise SystemExit(1)
