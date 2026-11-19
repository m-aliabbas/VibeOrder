import uuid

from django.db import models
from django.db.models import Q

from apps.catalog.models import MenuRelease
from apps.core.models import Branch, Device, Staff, Stamped


class Area(Stamped):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="areas")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=80)
    sort = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "code"], name="uniq_area_code")]


class DiningTable(Stamped):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="tables")
    area = models.ForeignKey(Area, on_delete=models.PROTECT, related_name="tables")
    label = models.CharField(max_length=20)
    seats = models.PositiveIntegerField(default=4)
    sort = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["area", "label"], name="uniq_table_label")]


class Check(Stamped):
    SERVICE = [("dine_in", "Dine-in"), ("counter", "Counter"), ("pickup", "Pickup"), ("delivery", "Delivery")]
    STATUS = [("open", "Open"), ("closed", "Closed"), ("voided", "Voided"), ("merged", "Merged")]

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="checks")
    service_type = models.CharField(max_length=20, choices=SERVICE)
    table = models.ForeignKey(DiningTable, null=True, blank=True, on_delete=models.PROTECT, related_name="checks")
    status = models.CharField(max_length=20, choices=STATUS, default="open")
    business_date = models.DateField()
    token = models.CharField(max_length=12)
    guest_count = models.PositiveIntegerField(default=1)
    customer_name = models.CharField(max_length=80, blank=True)
    customer_phone = models.CharField(max_length=30, blank=True)
    opened_by = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    receipt_no = models.CharField(max_length=30, blank=True)
    round_count = models.PositiveIntegerField(default=0)
    bill_requested_at = models.DateTimeField(null=True, blank=True)
    merged_into = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="merged_from")
    void_reason = models.CharField(max_length=200, blank=True)
    customer = models.ForeignKey("fulfillment.Customer", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="checks")
    channel = models.CharField(max_length=20, default="staff")  # staff | web
    requested_at = models.DateTimeField(null=True, blank=True)
    promised_at = models.DateTimeField(null=True, blank=True)
    handed_over_at = models.DateTimeField(null=True, blank=True)
    handed_over_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    handover_override = models.CharField(max_length=200, blank=True)
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["table"], condition=Q(status="open"), name="uniq_open_check_per_table"),
            models.UniqueConstraint(fields=["branch", "business_date", "token"], name="uniq_check_token"),
        ]


class Quote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    release = models.ForeignKey(MenuRelease, on_delete=models.PROTECT)
    service_type = models.CharField(max_length=20)
    items_hash = models.CharField(max_length=64)
    items = models.JSONField()
    lines = models.JSONField()
    totals = models.JSONField()
    expires_at = models.DateTimeField()
    created_by = models.ForeignKey(Staff, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class CheckRound(Stamped):
    check_ref = models.ForeignKey(Check, on_delete=models.PROTECT, related_name="rounds", db_column="check_id")
    number = models.PositiveIntegerField()
    added_by = models.ForeignKey(Staff, on_delete=models.PROTECT)
    device = models.ForeignKey(Device, on_delete=models.PROTECT)
    command_key = models.UUIDField(null=True)
    quote = models.ForeignKey(Quote, null=True, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["check_ref", "number"], name="uniq_round_number")]


class CheckLine(Stamped):
    FIRE = [("held", "Held"), ("fired", "Fired")]
    STATUS = [("active", "Active"), ("voided", "Voided")]

    check_ref = models.ForeignKey(Check, on_delete=models.PROTECT, related_name="lines", db_column="check_id")
    round = models.ForeignKey(CheckRound, on_delete=models.PROTECT, related_name="lines")
    line_no = models.PositiveIntegerField()
    client_line_id = models.CharField(max_length=60)
    menu_release = models.ForeignKey(MenuRelease, on_delete=models.PROTECT)
    product_code = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    variant = models.CharField(max_length=30, blank=True)
    quantity = models.PositiveIntegerField()
    unit_minor = models.BigIntegerField()
    total_minor = models.BigIntegerField()
    discount_minor = models.BigIntegerField(default=0)
    tax_minor = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="PKR")
    spec = models.JSONField()
    breakdown = models.JSONField()
    components = models.JSONField()
    fire_state = models.CharField(max_length=10, choices=FIRE, default="fired")
    status = models.CharField(max_length=10, choices=STATUS, default="active")
    amends = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="amendments")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["check_ref", "line_no"], name="uniq_line_no")]
        ordering = ["line_no"]

    @property
    def charge_minor(self):
        return self.total_minor - self.discount_minor + self.tax_minor


class CheckAdjustment(Stamped):
    """Correction / movement record. Financial delta, kitchen effect and approval are recorded separately
    (spec 04: changed cash, discarded goods and reversed revenue are separate facts)."""
    KINDS = [("remove", "Remove sent line"), ("modify", "Modify sent line"), ("remake", "Kitchen remake"),
             ("void", "Void check"), ("move", "Move table"), ("merge", "Merge checks"),
             ("split", "Split line")]

    check_ref = models.ForeignKey(Check, on_delete=models.PROTECT, related_name="adjustments", db_column="check_id")
    kind = models.CharField(max_length=10, choices=KINDS)
    line = models.ForeignKey(CheckLine, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    new_line = models.ForeignKey(CheckLine, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=200)
    description = models.CharField(max_length=300, blank=True)
    amount_delta_minor = models.BigIntegerField(default=0, help_text="Change to payable total; 0 for remakes")
    discarded = models.BooleanField(default=False, help_text="Cooked goods were discarded")
    staff = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    approved_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    data = models.JSONField(default=dict)


class CheckDiscount(Stamped):
    """One order promotion and/or manager-authorized line adjustments (spec 04 step 5).
    Amounts are recomputed into CheckLine.discount_minor by orders.discounts.apply_discounts()."""
    KINDS = [("promotion", "Order promotion"), ("line_adjustment", "Line adjustment")]

    check_ref = models.ForeignKey(Check, on_delete=models.PROTECT, related_name="discounts", db_column="check_id")
    kind = models.CharField(max_length=20, choices=KINDS)
    promotion = models.ForeignKey("catalog.Promotion", null=True, blank=True, on_delete=models.PROTECT)
    line = models.ForeignKey(CheckLine, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    percent_bp = models.PositiveIntegerField(null=True, blank=True)
    amount_minor = models.BigIntegerField(null=True, blank=True)
    applied_minor = models.BigIntegerField(default=0)
    reason = models.CharField(max_length=200)
    status = models.CharField(max_length=10, default="active")  # active | removed
    staff = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    approved_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    removed_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
