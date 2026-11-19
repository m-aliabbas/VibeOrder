"""Customers, delivery zones, deliveries and the rider cash ledger (spec 01 §Delivery, spec 04 §Shift and rider ledger).

Rider liability exists only for cash actually collected (RiderCollection). A remittance offsets collections and
posts exactly one drawer movement. A failed delivery never reverses the sale or restores stock by itself.
"""
from django.db import models
from django.db.models import Q

from apps.core.models import Branch, Staff, Stamped, Tenant


class Customer(Stamped):
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="customers")
    phone = models.CharField(max_length=20, help_text="Digits only")
    name = models.CharField(max_length=80, blank=True)
    last_order_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "phone"], name="uniq_customer_phone")]

    @property
    def masked_phone(self):
        p = self.phone
        return p if len(p) < 7 else f"{p[:4]}{'•' * (len(p) - 7)}{p[-3:]}"


class DeliveryZone(Stamped):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="zones")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=80)
    fee_minor = models.BigIntegerField(default=0)
    eta_minutes = models.PositiveIntegerField(default=40)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "code"], name="uniq_zone_code")]


class Address(Stamped):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="addresses")
    text = models.CharField(max_length=250)
    landmark = models.CharField(max_length=120, blank=True)
    zone = models.ForeignKey(DeliveryZone, null=True, blank=True, on_delete=models.SET_NULL)
    last_used_at = models.DateTimeField(null=True, blank=True)


class Delivery(Stamped):
    """One delivery attempt. A retry creates a new attempt; the failed attempt is kept."""
    STATUS = [("awaiting_assignment", "Awaiting rider"), ("assigned", "Assigned"), ("dispatched", "Out for delivery"),
              ("delivered", "Delivered"), ("failed", "Failed")]
    TRANSITIONS = {
        "awaiting_assignment": {"assigned", "failed"},
        "assigned": {"assigned", "dispatched", "awaiting_assignment", "failed"},
        "dispatched": {"delivered", "failed"},
        "delivered": set(),
        "failed": set(),
    }
    PAYMENT = [("cod", "Cash on delivery"), ("prepaid", "Prepaid")]
    FOOD = [("", "Not decided"), ("discarded", "Discarded"), ("returned", "Returned to branch"),
            ("redeliver", "Redeliver")]

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="deliveries")
    check_ref = models.ForeignKey("orders.Check", on_delete=models.PROTECT, related_name="deliveries",
                                  db_column="check_id")
    attempt = models.PositiveIntegerField(default=1)
    previous = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=24, choices=STATUS, default="awaiting_assignment")
    payment = models.CharField(max_length=10, choices=PAYMENT, default="cod")
    customer_name = models.CharField(max_length=80)
    phone = models.CharField(max_length=20)
    address_text = models.CharField(max_length=250, help_text="Snapshot at order time")
    landmark = models.CharField(max_length=120, blank=True)
    zone = models.ForeignKey(DeliveryZone, on_delete=models.PROTECT)
    fee_minor = models.BigIntegerField(default=0)
    promised_at = models.DateTimeField()
    rider = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="deliveries")
    assigned_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=200, blank=True)
    food_disposition = models.CharField(max_length=12, choices=FOOD, blank=True)
    decision_required = models.BooleanField(default=False, help_text="Failed: refund/redelivery decision pending")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["check_ref", "attempt"], name="uniq_delivery_attempt"),
            models.UniqueConstraint(fields=["check_ref"], condition=~Q(status__in=["delivered", "failed"]),
                                    name="uniq_live_delivery"),
        ]


class RiderCollection(Stamped):
    """Cash a rider actually collected from a guest: the rider owes it until remitted."""
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    rider = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="collections")
    delivery = models.OneToOneField(Delivery, on_delete=models.PROTECT, related_name="collection")
    tender = models.OneToOneField("cash.Tender", on_delete=models.PROTECT, related_name="rider_collection")
    amount_minor = models.BigIntegerField()
    remitted_minor = models.BigIntegerField(default=0)
    business_date = models.DateField()

    @property
    def outstanding_minor(self):
        return self.amount_minor - self.remitted_minor


class RiderRemittance(Stamped):
    """Physical cash handed back by a rider. Posts one drawer movement; never revenue."""
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    rider = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="remittances")
    amount_minor = models.BigIntegerField()
    shift = models.ForeignKey("cash.DrawerShift", on_delete=models.PROTECT, related_name="remittances")
    movement = models.OneToOneField("cash.DrawerMovement", on_delete=models.PROTECT, related_name="remittance")
    staff = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    approved_by = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    note = models.CharField(max_length=200, blank=True)
    business_date = models.DateField()

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount_minor__gt=0), name="remittance_positive")]


class RemittanceAllocation(models.Model):
    id = models.BigAutoField(primary_key=True)
    remittance = models.ForeignKey(RiderRemittance, on_delete=models.PROTECT, related_name="allocations")
    collection = models.ForeignKey(RiderCollection, on_delete=models.PROTECT, related_name="allocations")
    amount_minor = models.BigIntegerField()
