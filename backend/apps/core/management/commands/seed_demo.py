from django.core.management.base import BaseCommand

from apps.core.fixtures_demo import OWNER, STAFF, seed


class Command(BaseCommand):
    help = "Seed deterministic synthetic demo business, branch, staff accounts and menu (dev/test only)."

    def handle(self, *args, **opts):
        out = seed()
        b = out["branch"]
        self.stdout.write(self.style.SUCCESS(f"Branch {b.name} id={b.pk}"))
        self.stdout.write("  Sign in with username + password:")
        for username, display, password, roles in STAFF:
            self.stdout.write(f"  {username:8s} / {password:10s} {','.join(roles)}")
        self.stdout.write(f"  {OWNER[0]:8s} / {OWNER[2]:10s} owner (all branches)")
        self.stdout.write("  Staff app: any device, enter the server address and sign in. KDS: http://localhost:8000/kds/")
