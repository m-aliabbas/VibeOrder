from django.core.management.base import BaseCommand

from apps.ops.services import restore


class Command(BaseCommand):
    help = "Restore an encrypted backup into a NEW database (spare host). Then point the host at it and run take_over."

    def add_arguments(self, parser):
        parser.add_argument("file")
        parser.add_argument("--target-db", required=True)

    def handle(self, *args, **opts):
        restore(opts["file"], opts["target_db"])
        self.stdout.write(f"restored into {opts['target_db']}. Isolate the old host, then: manage.py take_over "
                          f"--branch <code> --reason '...' on this host, and reconcile via GET /recovery.")
