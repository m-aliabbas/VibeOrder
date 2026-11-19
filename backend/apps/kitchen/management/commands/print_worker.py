import time

from django.core.management.base import BaseCommand

from apps.kitchen.services import process_one, recover_stale_jobs


class Command(BaseCommand):
    help = "Durable print worker: lease queued jobs, send once, record spooler handoff."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Drain the queue then exit")
        parser.add_argument("--interval", type=float, default=1.0)

    def handle(self, *args, **opts):
        n = recover_stale_jobs()
        if n:
            self.stdout.write(self.style.WARNING(f"{n} job(s) marked outcome_unknown after restart"))
        while True:
            did = process_one()
            if did:
                self.stdout.write(f"processed {did}")
                continue
            if opts["once"]:
                return
            recover_stale_jobs()
            time.sleep(opts["interval"])
