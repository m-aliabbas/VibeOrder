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


class PurchaseReceipt(Stamped):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
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
