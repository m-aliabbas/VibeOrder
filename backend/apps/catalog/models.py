from django.db import models
from django.utils import timezone

from apps.core.models import Branch, Staff, Stamped, Tenant


class MenuRelease(Stamped):
    """Immutable once published. Lines snapshot the release they were priced under."""

    MIXED_POLICIES = [("higher", "Higher full-pizza price"), ("average", "Arithmetic mean"), ("explicit", "Explicit mixed price")]

    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="releases")
    version = models.PositiveIntegerField()
    name = models.CharField(max_length=120)
    status = models.CharField(max_length=20, default="draft")  # draft | published
    mixed_policy = models.CharField(max_length=20, choices=MIXED_POLICIES, default="higher")
    tax_rate_bp = models.PositiveIntegerField(default=0, help_text="Basis points, 1000 = 10%")
    tax_inclusive = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "version"], name="uniq_release_version")]

    def __str__(self):
        return f"{self.name} v{self.version}"


class Category(Stamped):
    release = models.ForeignKey(MenuRelease, on_delete=models.CASCADE, related_name="categories")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=80)
    sort = models.PositiveIntegerField(default=0)


class Product(Stamped):
    KINDS = [("pizza", "Pizza flavour"), ("mixed_pizza", "Half and half pizza"), ("item", "Simple item"), ("deal", "Fixed price deal")]

    release = models.ForeignKey(MenuRelease, on_delete=models.CASCADE, related_name="products")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    name_local = models.CharField(max_length=120, blank=True, help_text="Urdu / local label")
    kind = models.CharField(max_length=20, choices=KINDS, default="item")
    station_code = models.CharField(max_length=30, default="packing")
    description = models.CharField(max_length=300, blank=True)
    image = models.CharField(max_length=200, blank=True, help_text="File under MEDIA_ROOT/menu/, e.g. naan.png")
    sort = models.PositiveIntegerField(default=0)
    option_groups = models.ManyToManyField("OptionGroup", blank=True, related_name="products")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["release", "code"], name="uniq_product_code")]


class Variant(Stamped):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=60)
    price_minor = models.BigIntegerField()
    sort = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["product", "code"], name="uniq_variant_code")]


class OptionGroup(Stamped):
    KINDS = [("crust", "Crust"), ("topping", "Topping"), ("choice", "Choice")]

    release = models.ForeignKey(MenuRelease, on_delete=models.CASCADE, related_name="option_groups")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=20, choices=KINDS)
    min_select = models.PositiveIntegerField(default=0)
    max_select = models.PositiveIntegerField(default=1)
    sides_allowed = models.BooleanField(default=False)
    sort = models.PositiveIntegerField(default=0)


class Option(Stamped):
    group = models.ForeignKey(OptionGroup, on_delete=models.CASCADE, related_name="options")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=80)
    is_default = models.BooleanField(default=False)
    excludes = models.JSONField(default=list, blank=True, help_text="Option codes that cannot be combined")
    sort = models.PositiveIntegerField(default=0)


class OptionPrice(Stamped):
    """Size-specific option price. size_code '' applies to all sizes."""

    option = models.ForeignKey(Option, on_delete=models.CASCADE, related_name="prices")
    size_code = models.CharField(max_length=30, blank=True)
    whole_minor = models.BigIntegerField(default=0)
    half_minor = models.BigIntegerField(default=0)


class DealSlot(Stamped):
    deal = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="slots")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=80)
    quantity = models.PositiveIntegerField(default=1)
    sort = models.PositiveIntegerField(default=0)


class DealSlotChoice(Stamped):
    slot = models.ForeignKey(DealSlot, on_delete=models.CASCADE, related_name="choices")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="+")
    variant_code = models.CharField(max_length=30)
    upgrade_minor = models.BigIntegerField(default=0)


class BranchAvailability(Stamped):
    """Branch sellout override for a product or option code, with reason, actor and optional expiry."""

    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="availability")
    target_kind = models.CharField(max_length=10)  # product | option
    target_code = models.CharField(max_length=40)
    available = models.BooleanField(default=False)
    reason = models.CharField(max_length=200, blank=True)
    actor = models.ForeignKey(Staff, null=True, on_delete=models.PROTECT)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["branch", "target_kind", "target_code"], name="uniq_availability")
        ]

    @classmethod
    def unavailable_codes(cls, branch):
        now = timezone.now()
        rows = cls.objects.filter(branch=branch, available=False).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        )
        out = {"product": set(), "option": set()}
        for r in rows:
            out.setdefault(r.target_kind, set()).add(r.target_code)
        return out


class Promotion(Stamped):
    """Order promotion. Pilot: one per check, applied before tax, allocated to lines by largest remainder."""
    KINDS = [("percent", "Percent of eligible lines"), ("fixed", "Fixed amount")]

    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="promotions")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=10, choices=KINDS)
    value = models.PositiveIntegerField(help_text="Basis points for percent, minor units for fixed")
    min_subtotal_minor = models.BigIntegerField(default=0)
    max_discount_minor = models.BigIntegerField(null=True, blank=True)
    requires_approval = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    channels = models.JSONField(default=list, blank=True, help_text="Empty = all channels")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "code"], name="uniq_promotion_code")]


class MenuActivation(Stamped):
    """Which release is active at which branch, who published it and when (owner portal, A20)."""
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="activations")
    release = models.ForeignKey(MenuRelease, on_delete=models.PROTECT, related_name="activations")
    previous = models.ForeignKey(MenuRelease, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    published_by = models.ForeignKey(Staff, null=True, on_delete=models.PROTECT)
    activated_at = models.DateTimeField(auto_now_add=True)
