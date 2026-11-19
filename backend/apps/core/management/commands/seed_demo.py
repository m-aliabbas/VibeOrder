from django.core.management.base import BaseCommand

from apps.core.fixtures_demo import STAFF, seed


class Command(BaseCommand):
    help = "Seed deterministic synthetic demo tenant, branch, staff, menu and pairing codes (dev/test only)."

    def handle(self, *args, **opts):
        out = seed()
        b = out["branch"]
        self.stdout.write(self.style.SUCCESS(f"Branch {b.name} id={b.pk}"))
        for username, display, pin, roles in STAFF:
            self.stdout.write(f"  staff {username:8s} PIN {pin}  {','.join(roles)}")
        for kind, codes in out["pairing_codes"].items():
            self.stdout.write(f"  pairing code ({kind}): {', '.join(codes)}  (valid 24h, single use)")
        self.stdout.write("  KDS: http://localhost:8000/kds/")
