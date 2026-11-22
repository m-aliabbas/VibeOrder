"""Demo only: past closed orders so the dashboard charts have something to show.

Writes checks, lines and tenders straight to the database (no kitchen, no outbox, no stock), with lunch and
dinner peaks, weekend uplift and a realistic channel / payment mix. Tokens start with "H" and the command
refuses to run twice for the same day, so it never touches real service data.
"""
import random
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

HOUR_WEIGHTS = {11: 2, 12: 6, 13: 9, 14: 6, 15: 3, 16: 2, 17: 3, 18: 5, 19: 9, 20: 12, 21: 11, 22: 7, 23: 3}


class Command(BaseCommand):
    help = "Demo only: generate past closed orders for the dashboard (default 30 days, branch B1)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument("--branch", default="B1")
        parser.add_argument("--seed", type=int, default=7)

    def handle(self, *args, **o):
        if not settings.DEBUG:
            raise CommandError("seed_history is for demo databases only (DEBUG=1)")
        from apps.cash.models import Tender
        from apps.catalog.models import Variant
        from apps.core.models import Branch, Device, Membership
        from apps.orders.models import Check, CheckLine, CheckRound

        rnd = random.Random(o["seed"])
        branch = Branch.objects.get(code=o["branch"])
        tz = ZoneInfo(branch.timezone)
        release = branch.active_release
        variants = list(Variant.objects.filter(product__release=release).exclude(product__kind="deal")
                        .select_related("product"))
        if not variants:
            raise CommandError("Branch has no active menu")
        waiters = [m.staff for m in Membership.objects.filter(branch=branch, role__in=["waiter", "cashier"],
                                                              is_active=True).select_related("staff")]
        device = Device.objects.filter(branch=branch).first() or Device.objects.create(
            branch=branch, name="History import", kind="system", token_hash=uuid.uuid4().hex)
        today = branch.business_date()
        made = 0
        for back in range(o["days"], 0, -1):
            day = today - timedelta(days=back)
            if Check.objects.filter(branch=branch, business_date=day, token__startswith="H").exists():
                continue
            weekend = day.weekday() in (4, 5, 6)
            n = int(rnd.gauss(70 if weekend else 48, 8))
            hours = rnd.choices(list(HOUR_WEIGHTS), weights=list(HOUR_WEIGHTS.values()), k=max(10, n))
            with transaction.atomic():
                for i, hour in enumerate(sorted(hours), start=1):
                    opened = datetime.combine(day, time(hour, rnd.randrange(60)), tz)
                    service = rnd.choices(["dine_in", "counter", "pickup", "delivery"], weights=[45, 20, 12, 23])[0]
                    channel = "web" if service in ("pickup", "delivery") and rnd.random() < 0.35 else "staff"
                    staff = rnd.choice(waiters)
                    c = Check.objects.create(branch=branch, service_type=service, status="closed", business_date=day,
                                             token=f"H{i:03d}", opened_by=staff, closed_by=staff, channel=channel,
                                             guest_count=rnd.randint(1, 6), receipt_no=f"H-{day:%m%d}-{i:03d}")
                    rd = CheckRound.objects.create(check_ref=c, number=1, added_by=staff, device=device)
                    total = 0
                    for ln in range(1, rnd.choices([1, 2, 3, 4, 5], weights=[20, 35, 25, 12, 8])[0] + 1):
                        v = rnd.choice(variants)
                        q = rnd.choices([1, 2, 3], weights=[75, 20, 5])[0]
                        CheckLine.objects.create(
                            check_ref=c, round=rd, line_no=ln, client_line_id=f"h{ln}", menu_release=release,
                            product_code=v.product.code, name=v.product.name, variant=v.code, quantity=q,
                            unit_minor=v.price_minor, total_minor=v.price_minor * q, spec={}, breakdown={},
                            components=[])
                        total += v.price_minor * q
                    method = rnd.choices(["cash", "external_card"], weights=[68, 32])[0]
                    if service == "delivery":
                        method = "rider_cod" if channel == "staff" or rnd.random() < 0.6 else "online"
                    Tender.objects.create(branch=branch, check_ref=c, method=method, amount_minor=total, staff=staff,
                                          business_date=day)
                    closed = opened + timedelta(minutes=rnd.randint(18, 70))
                    Check.objects.filter(pk=c.pk).update(opened_at=opened, closed_at=closed)
                    made += 1
        self.stdout.write(self.style.SUCCESS(f"Added {made} past orders to {branch.code}"))
