import secrets
import uuid
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone


class Stamped(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Tenant(Stamped):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    currency = models.CharField(max_length=3, default="PKR")

    def __str__(self):
        return self.name


class Branch(Stamped):
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="branches")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    timezone = models.CharField(max_length=64, default="Asia/Karachi")
    day_cutoff = models.TimeField(default=time(4, 0), help_text="Business day starts at this local time")
    currency = models.CharField(max_length=3, default="PKR")
    active_release = models.ForeignKey(
        "catalog.MenuRelease", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    stream_position = models.BigIntegerField(default=0)
    discount_limit_bp = models.PositiveIntegerField(
        default=1000, help_text="Line adjustment a cashier may give without a manager (basis points of the line)")
    writable_host = models.CharField(max_length=80, blank=True,
                                     help_text="Host id allowed to write (fencing); blank = any (dev)")
    fence_epoch = models.PositiveIntegerField(default=0)
    online_paused = models.BooleanField(default=False)
    pickup_minutes = models.PositiveIntegerField(default=20)
    address = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    tax_number = models.CharField(max_length=40, blank=True, help_text="NTN / STRN printed on receipts")
    receipt_footer = models.CharField(max_length=200, blank=True, help_text="Last line of every receipt")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "code"], name="uniq_branch_code")]

    def __str__(self):
        return self.name

    @property
    def tz(self):
        return ZoneInfo(self.timezone)

    def business_date(self, at: datetime | None = None):
        """Business date for a UTC instant using the branch's local cutoff."""
        at = at or timezone.now()
        local = at.astimezone(self.tz)
        cutoff = datetime.combine(local.date(), self.day_cutoff, tzinfo=self.tz)
        return (local - timedelta(days=1)).date() if local < cutoff else local.date()


class Station(Stamped):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="stations")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=80)
    is_packing = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "code"], name="uniq_station_code")]

    def __str__(self):
        return f"{self.branch.code}/{self.code}"


class Role(models.TextChoices):
    WAITER = "waiter"
    CASHIER = "cashier"
    COOK = "cook"
    PACKER = "packer"
    RIDER = "rider"
    MANAGER = "manager"
    OWNER = "owner"


ROLE_PERMISSIONS = {
    "waiter": {"check.open", "check.append", "check.view", "floor.view", "quote", "menu.view", "check.request_bill",
               "check.amend", "check.move", "ticket.serve"},
    "cashier": {"check.open", "check.append", "check.view", "floor.view", "quote", "menu.view",
                "tender.take", "check.close", "receipt.print", "shift.view", "drawer.open", "print.reprint", "check.request_bill",
                "check.amend", "check.move", "discount.apply", "customer.lookup", "delivery.manage", "pack.verify",
                "station.view", "rider.view", "ticket.transition", "ticket.serve"},
    "cook": {"station.view", "ticket.transition", "ticket.acknowledge", "menu.view", "print.reprint"},
    "packer": {"station.view", "ticket.transition", "ticket.acknowledge", "menu.view", "pack.verify", "print.reprint",
               "check.view", "delivery.manage", "rider.view"},
    "rider": {"delivery.view_own"},
    "manager": {"*"},
    "owner": {"*"},
}


class Staff(Stamped):
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="staff")
    display_name = models.CharField(max_length=80)
    username = models.CharField(max_length=40)
    password_hash = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    failed_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "username"], name="uniq_staff_username")]

    def set_password(self, password: str):
        self.password_hash = make_password(password)

    def check_password(self, password: str) -> bool:
        return bool(password) and check_password(password, self.password_hash)

    def __str__(self):
        return self.display_name


class Membership(Stamped):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="memberships")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["staff", "branch", "role"], name="uniq_membership")]

    @property
    def permissions(self):
        return ROLE_PERMISSIONS.get(self.role, set())


class Device(Stamped):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="devices")
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=20, default="tablet")  # tablet | cashier | kds
    token_hash = models.CharField(max_length=128, unique=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    @staticmethod
    def hash_token(raw: str) -> str:
        import hashlib
        return hashlib.sha256(raw.encode()).hexdigest()


class StaffSession(Stamped):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="sessions")
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="sessions")
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_valid(self):
        return self.revoked_at is None and self.expires_at > timezone.now()


class BranchCounter(models.Model):
    """Sequences per branch and business day (order tokens, ticket numbers, receipts)."""

    id = models.BigAutoField(primary_key=True)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE)
    name = models.CharField(max_length=20)
    business_date = models.DateField()
    value = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "name", "business_date"], name="uniq_counter")]


class CommandReceipt(models.Model):
    """Idempotency record: one per (branch, key). Stores the committed result."""

    id = models.BigAutoField(primary_key=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    key = models.UUIDField()
    intent = models.CharField(max_length=60)
    payload_hash = models.CharField(max_length=64)
    staff = models.ForeignKey(Staff, on_delete=models.PROTECT)
    device = models.ForeignKey(Device, on_delete=models.PROTECT)
    status_code = models.PositiveSmallIntegerField()
    response = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "key"], name="uniq_command_key")]


class AuditEntry(models.Model):
    id = models.BigAutoField(primary_key=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    staff = models.ForeignKey(Staff, null=True, on_delete=models.PROTECT)
    approved_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    device = models.ForeignKey(Device, null=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=60)
    aggregate_type = models.CharField(max_length=40)
    aggregate_id = models.UUIDField()
    prev_revision = models.PositiveIntegerField(null=True)
    next_revision = models.PositiveIntegerField(null=True)
    command_key = models.UUIDField(null=True)
    correlation_id = models.CharField(max_length=64, blank=True)
    reason = models.TextField(blank=True)
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class SyncOutbox(models.Model):
    """Immutable branch events, exported to the cloud in stream order."""

    event_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    stream_position = models.BigIntegerField()
    schema_version = models.PositiveSmallIntegerField(default=1)
    event_type = models.CharField(max_length=60)
    aggregate_type = models.CharField(max_length=40)
    aggregate_id = models.UUIDField()
    revision = models.PositiveIntegerField()
    payload = models.JSONField()
    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    acked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["branch", "stream_position"], name="uniq_outbox_position")
        ]


def new_token() -> str:
    return secrets.token_urlsafe(32)


def default_expiry(seconds: int):
    return timezone.now() + timedelta(seconds=seconds)
