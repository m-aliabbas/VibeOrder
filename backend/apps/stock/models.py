"""Stock ledger (spec 04 §Stock and margin, A21; purchasing M5).

Quantities are fixed-precision decimals in base units (g, ml, pcs). Every movement is signed and carries a
unique originating reference so a repeated fire event cannot consume twice. Costs: moving weighted average per
branch, snapshot on each consumption.
"""
from django.db import models
from django.db.models import Q

from apps.core.models import Branch, Staff, Stamped, Tenant

QTY = {"max_digits": 14, "decimal_places": 3}


class StockItem(Stamped):
    UNITS = [("g", "grams"), ("ml", "millilitres"), ("pcs", "pieces")]

    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="stock_items")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=80)
    base_unit = models.CharField(max_length=4, choices=UNITS)
    purchase_unit = models.CharField(max_length=20, blank=True, help_text="e.g. 5 kg bag")
    purchase_factor = models.DecimalField(default=1, help_text="Base units per purchase unit", **QTY)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "code"], name="uniq_stock_item")]


class StockLevel(models.Model):
    """Running balance and moving average cost per branch (derived; the ledger is authoritative)."""
    id = models.BigAutoField(primary_key=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name="levels")
    on_hand = models.DecimalField(default=0, **QTY)
    avg_cost_minor = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True,
                                         help_text="Minor units per base unit; null = cost unknown")
    reorder_level = models.DecimalField(null=True, blank=True, help_text="Alert when on hand falls to this (base "
                                        "units); null = no alert", **QTY)
    par_level = models.DecimalField(null=True, blank=True, help_text="Order up to this (base units) when "
                                    "suggesting purchase orders; null = twice the reorder level", **QTY)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "item"], name="uniq_stock_level")]


class Recipe(Stamped):
    """Versioned recipe keyed to a target: pizza base by size, flavour by size, option by size (whole amount;
    a half applies 0.5), simple item by variant, or packaging per product kind and size."""
    TARGETS = [("base", "Pizza base"), ("product", "Product / flavour"), ("option", "Option"),
               ("packaging", "Packaging")]

    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="recipes")
    target_kind = models.CharField(max_length=10, choices=TARGETS)
    target_code = models.CharField(max_length=40)
    size_code = models.CharField(max_length=30, blank=True, help_text="'' = any size")
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=200, blank=True, help_text="Why this version was published")
    created_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "target_kind", "target_code", "size_code", "version"],
                                    name="uniq_recipe_version"),
            models.UniqueConstraint(fields=["tenant", "target_kind", "target_code", "size_code"],
                                    condition=Q(is_active=True), name="uniq_active_recipe"),
        ]


class RecipeComponent(models.Model):
    id = models.BigAutoField(primary_key=True)
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="components")
    item = models.ForeignKey(StockItem, on_delete=models.PROTECT)
    quantity = models.DecimalField(**QTY)


class StockMovement(models.Model):
    KINDS = [("consumption", "Production consumption"), ("packaging", "Packaging at packing"),
             ("reversal", "Compensating reversal"), ("loss", "Loss / waste outside production"),
             ("receipt", "Purchase receipt"), ("transfer_out", "Transfer out"), ("transfer_in", "Transfer in"),
             ("count", "Count adjustment"), ("return", "Approved physical return")]

    id = models.BigAutoField(primary_key=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=14, choices=KINDS)
    quantity = models.DecimalField(help_text="Signed, base units", **QTY)
    unit_cost_minor = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
    source_ref = models.CharField(max_length=80, help_text="Originating event, e.g. ti:<ticket item id>")
    recipe = models.ForeignKey(Recipe, null=True, blank=True, on_delete=models.PROTECT)
    reverses = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    staff = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT)
    approved_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=200, blank=True)
    business_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["branch", "kind", "source_ref", "item"], name="uniq_stock_origin"),
        ]


class Supplier(Stamped):
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="suppliers")
    name = models.CharField(max_length=80)
    phone = models.CharField(max_length=30, blank=True)
    note = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)


class PurchaseOrder(Stamped):
    """What the branch asked a supplier for. Stock moves only when goods are received against it (each delivery is
    a PurchaseReceipt), so a PO can be received in parts, and the rest closed short with a reason."""
    STATUS = [("draft", "Draft"), ("sent", "Sent to supplier"), ("partial", "Partly received"),
              ("received", "Received"), ("closed", "Closed short"), ("cancelled", "Cancelled")]

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    number = models.CharField(max_length=30, help_text="e.g. PO-B1-0007")
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.PROTECT)
    status = models.CharField(max_length=10, choices=STATUS, default="draft")
    expected_on = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    sent_at = models.DateTimeField(null=True, blank=True)
    closed_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "number"], name="uniq_po_number")]


class PurchaseOrderLine(models.Model):
    id = models.BigAutoField(primary_key=True)
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(StockItem, on_delete=models.PROTECT)
    quantity = models.DecimalField(help_text="Ordered, base units", **QTY)
    expected_cost_minor = models.BigIntegerField(default=0, help_text="Expected line total; 0 = unknown")
    received = models.DecimalField(default=0, help_text="Received so far, base units", **QTY)


class PurchaseReceipt(Stamped):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    purchase_order = models.ForeignKey(PurchaseOrder, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="receipts")
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.PROTECT)
    reference = models.CharField(max_length=60, blank=True, help_text="Supplier invoice / delivery note")
    total_minor = models.BigIntegerField(default=0)
    staff = models.ForeignKey(Staff, on_delete=models.PROTECT)
    business_date = models.DateField()


class StockTransfer(Stamped):
    STATUS = [("sent", "Sent"), ("received", "Received")]
    from_branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="+")
    to_branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=10, choices=STATUS, default="sent")
    lines = models.JSONField(help_text="[{item, quantity}]")
    sent_by = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    received_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    received_at = models.DateTimeField(null=True, blank=True)


class StockCount(Stamped):
    """A count sheet: one counting session over many items. Each line records the theoretical on-hand at the
    moment it was counted, so service can continue while counting; approval posts counted − expected-at-count."""
    STATUS = [("open", "Counting"), ("submitted", "Waiting for approval"), ("approved", "Approved"),
              ("cancelled", "Cancelled")]

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    number = models.CharField(max_length=30)
    status = models.CharField(max_length=10, choices=STATUS, default="open")
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    submitted_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    business_date = models.DateField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "number"], name="uniq_count_number")]


class StockCountLine(models.Model):
    id = models.BigAutoField(primary_key=True)
    count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(StockItem, on_delete=models.PROTECT)
    counted = models.DecimalField(null=True, blank=True, **QTY)
    expected = models.DecimalField(null=True, blank=True, help_text="Theoretical on hand when counted", **QTY)
    unit_cost_minor = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
    counted_at = models.DateTimeField(null=True, blank=True)
    counted_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["count", "item"], name="uniq_count_item")]


class SupplierPayment(Stamped):
    """Money paid to a supplier. Purchases (receipts) raise what we owe; payments lower it (supplier ledger).
    Cash paid from the drawer also posts a `paid_out` drawer movement in the open shift."""
    METHODS = [("cash", "Cash from drawer"), ("bank", "Bank transfer"), ("wallet", "JazzCash / EasyPaisa"),
               ("cheque", "Cheque"), ("other", "Other")]

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="payments")
    amount_minor = models.BigIntegerField()
    method = models.CharField(max_length=10, choices=METHODS)
    reference = models.CharField(max_length=60, blank=True, help_text="Cheque / transaction no.")
    note = models.CharField(max_length=200, blank=True)
    drawer_movement = models.OneToOneField("cash.DrawerMovement", null=True, blank=True, on_delete=models.PROTECT)
    staff = models.ForeignKey(Staff, on_delete=models.PROTECT)
    business_date = models.DateField()

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount_minor__gt=0), name="supplier_payment_positive")]
