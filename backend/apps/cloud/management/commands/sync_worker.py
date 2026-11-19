"""Branch replication worker: upload outbox (contiguous acks, backoff) and import online offers from the inbox."""
import time

from django.core.management.base import BaseCommand

from apps.cloud.branch_sync import HttpTransport, sync_once
from apps.core.models import Branch


class Command(BaseCommand):
    help = "Run the branch ↔ cloud sync loop (VIBEORDER_CLOUD_URL, VIBEORDER_CLOUD_TOKEN or --dev for seeded tokens)"

    def add_arguments(self, parser):
        parser.add_argument("--branch", default="B1")
        parser.add_argument("--interval", type=float, default=2.0)
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--dev", action="store_true", help="use the seeded dev credential for the branch")

    def handle(self, *args, **o):
        branch = Branch.objects.get(code=o["branch"])
        token = None
        if o["dev"]:
            from apps.core.fixtures_demo import dev_cloud_token
            token = dev_cloud_token(branch)
        transport = HttpTransport(token=token)
        while True:
            res = sync_once(branch, transport)
            up, inbox = res["upload"], res["inbox"]
            if up.get("sent") or inbox.get("processed") or up.get("error"):
                self.stdout.write(f"{branch.code} {up} {inbox}", ending="\n")
                self.stdout.flush()
            if o["once"]:
                break
            time.sleep(o["interval"])
