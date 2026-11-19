from django.core.management.base import BaseCommand

from apps.ops.services import backup, restore_drill


class Command(BaseCommand):
    help = "Encrypted backup of the branch database (incl. unreplayed outbox). --drill also verifies by restoring."

    def add_arguments(self, parser):
        parser.add_argument("--drill", action="store_true")

    def handle(self, *args, **opts):
        rec = backup()
        self.stdout.write(f"backup ok={rec.ok} {rec.path} {rec.size_bytes} bytes outbox_pending={rec.outbox_pending}")
        if opts["drill"] and rec.ok:
            d = restore_drill(rec)
            self.stdout.write(f"restore drill ok={d.ok} {d.detail}")
