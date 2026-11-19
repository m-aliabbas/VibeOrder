from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Branch
from apps.ops.services import reconciliation, take_over


class Command(BaseCommand):
    help = "Fence other hosts: make this host (VIBEORDER_HOST_ID) the only writable host for a branch."

    def add_arguments(self, parser):
        parser.add_argument("--branch", required=True, help="Branch code, e.g. B1")
        parser.add_argument("--reason", required=True)

    def handle(self, *args, **opts):
        b = Branch.objects.filter(code=opts["branch"]).first()
        if b is None:
            raise CommandError("Unknown branch")
        self.stdout.write(str(take_over(b, opts["reason"])))
        r = reconciliation(b)
        self.stdout.write(f"Reconcile before resuming: {len(r['open_checks'])} open checks, "
                          f"{r['pending_sync_events']} pending sync events, {r['print_exceptions']} print exceptions, "
                          f"{r['pending_refunds']} pending refunds, rider outstanding {r['rider_outstanding_minor']}")
