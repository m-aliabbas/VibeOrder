"""Cloud-profile records (spec 02 §Synchronization and online intake, M4).

The branch commits first and exports immutable outbox events; the cloud deduplicates by event id, applies them to
owner projections strictly in stream order and acknowledges only contiguous positions. Online orders are offered
to one branch through the inbox; the branch import key is the cloud request id.

Dev/test deviation: both profiles share one database (separate tables). Production runs the cloud profile on its
own instance; nothing here reads branch tables except the storefront menu (published catalog, cloud-owned).
"""
import secrets

from django.db import models

from apps.core.models import Branch, Staff, Stamped, Tenant


class CloudBranch(Stamped):
    """Branch credential and replication cursor as seen by the cloud."""
    branch = models.OneToOneField(Branch, on_delete=models.PROTECT, related_name="cloud")
    token_hash = models.CharField(max_length=128, unique=True)
    acked_position = models.BigIntegerField(default=0, help_text="Highest contiguous stream position applied")
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    last_event_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)


class CloudEvent(models.Model):
    event_id = models.UUIDField(primary_key=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    stream_position = models.BigIntegerField()
    schema_version = models.PositiveSmallIntegerField(default=1)
    event_type = models.CharField(max_length=60)
    aggregate_type = models.CharField(max_length=40)
    aggregate_id = models.UUIDField()
    revision = models.PositiveIntegerField()
    payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "stream_position"], name="uniq_cloud_position")]


class SyncException(Stamped):
    """Operator-visible: a corrupt/conflicting event blocks cursor advancement past its gap."""
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    event_id = models.UUIDField(null=True)
    stream_position = models.BigIntegerField(null=True)
    error = models.CharField(max_length=300)
    resolved_at = models.DateTimeField(null=True, blank=True)


class DailyProjection(models.Model):
    """Owner projection per branch and business day, built only from applied events."""
    id = models.BigAutoField(primary_key=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    business_date = models.DateField()
    checks_closed = models.PositiveIntegerField(default=0)
    sales_minor = models.BigIntegerField(default=0)
    tenders = models.JSONField(default=dict)
    refunds_minor = models.BigIntegerField(default=0)
    voids = models.PositiveIntegerField(default=0)
    web_orders = models.PositiveIntegerField(default=0)
    last_position = models.BigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "business_date"], name="uniq_daily_projection")]


class OnlineOrder(Stamped):
    STATUS = [("awaiting_payment", "Awaiting payment"), ("submitted", "Submitted"), ("offered", "Offered to branch"),
              ("accepted", "Accepted"), ("rejected", "Rejected"), ("expired", "Expired"),
              ("reconciling", "Checking with branch")]

    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="online_orders")
    token = models.CharField(max_length=64, unique=True, help_text="Unguessable guest tracking token")
    reference = models.CharField(max_length=12, help_text="Pending reference shown to the guest")
    status = models.CharField(max_length=20, choices=STATUS, default="submitted")
    service_type = models.CharField(max_length=10, default="pickup")  # pickup | delivery
    payment = models.CharField(max_length=10, default="cod")  # cod | prepaid
    items = models.JSONField()
    menu_release = models.ForeignKey("catalog.MenuRelease", on_delete=models.PROTECT)
    total_minor = models.BigIntegerField()
    quote_lines = models.JSONField(default=list)
    currency = models.CharField(max_length=3, default="PKR")
    customer_name = models.CharField(max_length=80)
    phone = models.CharField(max_length=20)
    address_text = models.CharField(max_length=250, blank=True)
    zone_code = models.CharField(max_length=30, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    offer_deadline = models.DateTimeField(null=True, blank=True)
    offered_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    branch_token = models.CharField(max_length=12, blank=True)
    branch_check_id = models.UUIDField(null=True, blank=True)
    promised_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField(max_length=200, blank=True)
    client_key = models.UUIDField(null=True, blank=True, unique=True, help_text="Guest submission idempotency key")
    progress = models.CharField(max_length=30, blank=True, help_text="Projected from branch events")

    @staticmethod
    def new_token():
        return secrets.token_urlsafe(24)


class InboxCommand(Stamped):
    """Cloud → branch command (online offer). The branch acknowledges with its committed result."""
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="inbox")
    kind = models.CharField(max_length=30)
    order = models.ForeignKey(OnlineOrder, null=True, blank=True, on_delete=models.PROTECT, related_name="commands")
    payload = models.JSONField()
    delivered_count = models.PositiveIntegerField(default=0)
    last_delivered_at = models.DateTimeField(null=True, blank=True)
    acked_at = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(null=True, blank=True)


class ProviderPayment(Stamped):
    """Prepaid web order payment at an online provider (gated; fake provider in dev/tests)."""
    STATUS = [("pending", "Pending"), ("authorized", "Authorized"), ("failed", "Failed"),
              ("refund_required", "Late success: refund/void required"), ("refunded", "Refunded")]
    order = models.OneToOneField(OnlineOrder, on_delete=models.PROTECT, related_name="provider_payment")
    provider = models.CharField(max_length=20, default="fakepay")
    provider_ref = models.CharField(max_length=80, unique=True)
    status = models.CharField(max_length=20, choices=STATUS, default="pending")
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="PKR")
    expires_at = models.DateTimeField()


class ProviderCallback(models.Model):
    callback_id = models.CharField(max_length=80, primary_key=True)
    provider = models.CharField(max_length=20)
    payload = models.JSONField()
    outcome = models.CharField(max_length=60)
    received_at = models.DateTimeField(auto_now_add=True)


class OwnerSession(Stamped):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="owner_sessions")
    token_hash = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
