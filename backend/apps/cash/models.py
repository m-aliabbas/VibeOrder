from django.db import models
from django.db.models import Q

from apps.core.models import Branch, Staff, Stamped
from apps.orders.models import Check, CheckLine


class DrawerShift(Stamped):
    STATUS = [("open", "Open"), ("counting", "Counting"), ("closed", "Closed")]

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="shifts")
    drawer_code = models.CharField(max_length=20, default="main")
    status = models.CharField(max_length=10, choices=STATUS, default="open")
    business_date = models.DateField()
    opened_by = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    opened_at = models.DateTimeField(auto_now_add=True)
    opening_float_minor = models.BigIntegerField()
    counted_minor = models.BigIntegerField(null=True, blank=True)
    expected_minor = models.BigIntegerField(null=True, blank=True)
    variance_minor = models.BigIntegerField(null=True, blank=True)
    close_reason = models.TextField(blank=True)
    closed_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    approved_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["branch", "drawer_code"], condition=~Q(status="closed"),
                                    name="uniq_active_drawer")
        ]


class DrawerMovement(models.Model):
    KINDS = [("cash_sale", "Cash sale"), ("cash_refund", "Cash refund"), ("paid_in", "Paid in"),
             ("paid_out", "Paid out"), ("drop", "Cash drop"), ("remittance", "Rider remittance")]

    id = models.BigAutoField(primary_key=True)
    shift = models.ForeignKey(DrawerShift, on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=20, choices=KINDS)
    amount_minor = models.BigIntegerField(help_text="Signed effect on drawer cash")
    tender = models.ForeignKey("Tender", null=True, blank=True, on_delete=models.PROTECT)
    refund = models.ForeignKey("Refund", null=True, blank=True, on_delete=models.PROTECT)
    staff = models.ForeignKey(Staff, on_delete=models.PROTECT)
    approved_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=200, blank=True)
    business_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)


class Tender(Stamped):
    METHODS = [("cash", "Cash"), ("external_card", "Card (external terminal)"),
               ("rider_cod", "Cash on delivery (rider)"), ("online", "Online provider")]
    STATUS = [("pending", "Pending"), ("succeeded", "Succeeded"), ("failed", "Failed"), ("cancelled", "Cancelled")]

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    check_ref = models.ForeignKey(Check, on_delete=models.PROTECT, related_name="tenders", db_column="check_id")
    shift = models.ForeignKey(DrawerShift, null=True, blank=True, on_delete=models.PROTECT, related_name="tenders")
    method = models.CharField(max_length=20, choices=METHODS)
    status = models.CharField(max_length=12, choices=STATUS, default="succeeded")
    amount_minor = models.BigIntegerField()
    cash_received_minor = models.BigIntegerField(null=True, blank=True)
    change_minor = models.BigIntegerField(default=0)
    external_ref = models.CharField(max_length=80, blank=True)
    currency = models.CharField(max_length=3, default="PKR")
    staff = models.ForeignKey(Staff, on_delete=models.PROTECT)
    business_date = models.DateField()

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount_minor__gt=0), name="tender_positive")]


class TenderAllocation(models.Model):
    id = models.BigAutoField(primary_key=True)
    tender = models.ForeignKey(Tender, on_delete=models.PROTECT, related_name="allocations")
    line = models.ForeignKey(CheckLine, on_delete=models.PROTECT, related_name="allocations")
    amount_minor = models.BigIntegerField()


class Refund(Stamped):
    """Money returned against one successful tender. Independent of the check status (a closed check stays
    closed). Pending refunds reserve refundable balance until reconciled."""
    STATUS = [("pending", "Pending (outcome unknown)"), ("succeeded", "Succeeded"), ("failed", "Failed")]
    GOODS = [("not_applicable", "Not applicable"), ("not_returned", "Kept by guest / consumed"),
             ("returned", "Returned to stock"), ("discarded", "Discarded")]

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    tender = models.ForeignKey(Tender, on_delete=models.PROTECT, related_name="refunds")
    check_ref = models.ForeignKey(Check, on_delete=models.PROTECT, related_name="refunds", db_column="check_id")
    method = models.CharField(max_length=20, choices=Tender.METHODS)
    status = models.CharField(max_length=12, choices=STATUS, default="succeeded")
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="PKR")
    reason = models.CharField(max_length=200)
    goods = models.CharField(max_length=20, choices=GOODS, default="not_applicable")
    external_ref = models.CharField(max_length=80, blank=True)
    shift = models.ForeignKey(DrawerShift, null=True, blank=True, on_delete=models.PROTECT, related_name="refunds")
    staff = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    approved_by = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    business_date = models.DateField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount_minor__gt=0), name="refund_positive")]


class RefundAllocation(models.Model):
    """Revenue reversed per line (uses the original payment attribution)."""
    id = models.BigAutoField(primary_key=True)
    refund = models.ForeignKey(Refund, on_delete=models.PROTECT, related_name="allocations")
    line = models.ForeignKey(CheckLine, on_delete=models.PROTECT, related_name="refund_allocations")
    amount_minor = models.BigIntegerField()
