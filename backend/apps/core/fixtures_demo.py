"""Deterministic synthetic demo data (spec 04 fixtures). Never run in production."""
import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import (Category, DealSlot, DealSlotChoice, MenuRelease, Option, OptionGroup, OptionPrice,
                                 Product, Variant)
from apps.core.models import Branch, Membership, PairingCode, Staff, Station, Tenant
from apps.kitchen.models import Printer
from apps.orders.models import Area, DiningTable

NS = uuid.UUID("6f1c3a52-6d0e-4a7e-9a55-1c0f1e2d3b4a")


def did(name: str) -> uuid.UUID:
    return uuid.uuid5(NS, name)


STAFF = [
    # username, display, pin, roles
    ("bilal", "Bilal (Waiter)", "1111", ["waiter"]),
    ("sana", "Sana (Waiter)", "2222", ["waiter"]),
    ("hamza", "Hamza (Cashier)", "3333", ["cashier"]),
    ("usman", "Usman (Cook)", "4444", ["cook"]),
    ("zara", "Zara (Manager)", "9999", ["manager"]),
    ("faisal", "Faisal (Packer)", "6666", ["packer"]),
    ("imran", "Imran (Rider)", "5555", ["rider"]),
    ("kashif", "Kashif (Rider)", "5557", ["rider"]),
]
STAFF_B2 = [
    ("nadia", "Nadia (Manager B2)", "7777", ["manager"]),
    ("omer", "Omer (Waiter B2)", "1212", ["waiter"]),
]
OWNER = ("ayesha", "Ayesha (Owner)", "8888")

SIZES = [("small", "Small", 0), ("medium", "Medium", 1), ("large", "Large", 2)]
FLAVOURS = [
    # code, name, urdu, prices small/medium/large (PKR)
    ("tikka", "Chicken Tikka", "چکن تکہ", (650, 1000, 1400)),
    ("fajita", "Chicken Fajita", "چکن فجیتا", (750, 1150, 1600)),
    ("cheese-lover", "Cheese Lover", "چیز لوور", (600, 950, 1300)),
    ("veggie", "Garden Veggie", "سبزی", (550, 900, 1250)),
    ("pepperoni", "Beef Pepperoni", "پیپرونی", (800, 1200, 1700)),
]


def rs(pkr):
    return int(pkr * 100)


@transaction.atomic
def seed(reset=False, pairing_codes=True):
    tenant, _ = Tenant.objects.get_or_create(id=did("tenant"), defaults={"name": "Demo Pizzeria", "slug": "demo"})
    branch, _ = Branch.objects.get_or_create(
        id=did("branch-B1"), defaults={"tenant": tenant, "code": "B1", "name": "Demo Pizzeria – Gulberg"})
    stations = {}
    for code, name, packing in [("pizza", "Pizza oven", False), ("fryer", "Fryer", False), ("packing", "Packing", True)]:
        stations[code], _ = Station.objects.get_or_create(
            id=did(f"station-{code}"), defaults={"branch": branch, "code": code, "name": name, "is_packing": packing})
    for username, display, pin, roles in STAFF:
        s = Staff.objects.filter(tenant=tenant, username=username).first()
        if s is None:
            s = Staff(id=did(f"staff-{username}"), tenant=tenant, username=username, display_name=display)
            s.set_pin(pin)
            s.save()
        for r in roles:
            Membership.objects.get_or_create(staff=s, branch=branch, role=r)
    area_main, _ = Area.objects.get_or_create(id=did("area-main"), defaults={"branch": branch, "code": "main", "name": "Main hall", "sort": 1})
    area_family, _ = Area.objects.get_or_create(id=did("area-family"), defaults={"branch": branch, "code": "family", "name": "Family hall", "sort": 2})
    for i in range(1, 9):
        DiningTable.objects.get_or_create(id=did(f"table-{i}"), defaults={"branch": branch, "area": area_main, "label": str(i), "sort": i})
    for i in range(1, 5):
        DiningTable.objects.get_or_create(id=did(f"table-F{i}"), defaults={"branch": branch, "area": area_family, "label": f"F{i}", "seats": 8, "sort": i})
    Printer.objects.get_or_create(id=did("printer-pizza"), defaults={
        "branch": branch, "name": "Pizza kitchen printer", "station": stations["pizza"], "purpose": "kitchen",
        "transport": "spool_file", "address": "kitchen-pizza"})
    Printer.objects.get_or_create(id=did("printer-counter"), defaults={
        "branch": branch, "name": "Counter receipt printer", "purpose": "receipt",
        "transport": "spool_file", "address": "counter-receipt"})

    release = MenuRelease.objects.filter(tenant=tenant, version=1).first()
    if release is None:
        release = build_release(tenant, version=1)
    if branch.active_release_id is None:
        branch.active_release = release
        branch.save(update_fields=["active_release"])
    b2 = seed_branch_b2(tenant, release)
    owner = Staff.objects.filter(tenant=tenant, username=OWNER[0]).first()
    if owner is None:
        owner = Staff(id=did(f"staff-{OWNER[0]}"), tenant=tenant, username=OWNER[0], display_name=OWNER[1])
        owner.set_pin(OWNER[2])
        owner.save()
    for br in (branch, b2):
        Membership.objects.get_or_create(staff=owner, branch=br, role="owner")
    seed_operations(tenant, branch, b2)
    codes = {}
    if pairing_codes:
        for kind in ("tablet", "tablet", "cashier", "kds"):
            pc = PairingCode.objects.create(branch=branch, code=PairingCode.new_code(), kind=kind,
                                            expires_at=timezone.now() + timedelta(days=1))
            codes.setdefault(kind, []).append(pc.code)
    return {"tenant": tenant, "branch": branch, "branch2": b2, "release": release, "stations": stations,
            "pairing_codes": codes, "cloud_tokens": {"B1": dev_cloud_token(branch), "B2": dev_cloud_token(b2)}}


def dev_cloud_token(branch):
    return f"dev-cloud-{branch.code}-{str(branch.pk)[:8]}"


def seed_branch_b2(tenant, release):
    b2, _ = Branch.objects.get_or_create(id=did("branch-B2"), defaults={
        "tenant": tenant, "code": "B2", "name": "Demo Pizzeria – Johar Town", "active_release": release})
    st = {}
    for code, name, packing in [("pizza", "Pizza oven", False), ("fryer", "Fryer", False), ("packing", "Packing", True)]:
        st[code], _ = Station.objects.get_or_create(
            id=did(f"B2-station-{code}"), defaults={"branch": b2, "code": code, "name": name, "is_packing": packing})
    area, _ = Area.objects.get_or_create(id=did("B2-area-main"), defaults={"branch": b2, "code": "main", "name": "Hall", "sort": 1})
    for i in range(1, 5):
        DiningTable.objects.get_or_create(id=did(f"B2-table-{i}"), defaults={"branch": b2, "area": area, "label": str(i), "sort": i})
    Printer.objects.get_or_create(id=did("B2-printer-counter"), defaults={
        "branch": b2, "name": "B2 counter printer", "purpose": "receipt", "transport": "spool_file",
        "address": "b2-counter-receipt"})
    for username, display, pin, roles in STAFF_B2:
        s = Staff.objects.filter(tenant=tenant, username=username).first()
        if s is None:
            s = Staff(id=did(f"staff-{username}"), tenant=tenant, username=username, display_name=display)
            s.set_pin(pin)
            s.save()
        for r in roles:
            Membership.objects.get_or_create(staff=s, branch=b2, role=r)
    return b2


STOCK_ITEMS = [
    # code, name, unit, purchase unit, factor, opening qty (base), cost per base unit (minor)
    ("dough", "Pizza dough", "g", "10 kg batch", 10000, 50000, "20"),
    ("cheese", "Mozzarella", "g", "5 kg bag", 5000, 20000, "240"),
    ("chicken-tikka", "Chicken tikka", "g", "kg", 1000, 10000, "180"),
    ("chicken-fajita", "Chicken fajita", "g", "kg", 1000, 10000, "190"),
    ("pepperoni", "Beef pepperoni", "g", "kg", 1000, 5000, "320"),
    ("veg-mix", "Vegetable mix", "g", "kg", 1000, 8000, "60"),
    ("olives", "Black olives", "g", "kg", 1000, 3000, "200"),
    ("jalapeno", "Jalapeño", "g", "kg", 1000, 3000, "150"),
    ("mushrooms", "Mushrooms", "g", "kg", 1000, 3000, "170"),
    ("potato", "Frozen fries", "g", "2.5 kg bag", 2500, 20000, "45"),
    ("oil", "Cooking oil", "ml", "5 L can", 5000, 20000, "55"),
    ("wings", "Chicken wings", "pcs", "box of 60", 60, 300, "4500"),
    ("cola-500", "Cola 500ml bottle", "pcs", "crate of 24", 24, 240, "8500"),
    ("cola-1500", "Cola 1.5L bottle", "pcs", "crate of 12", 12, 120, "18000"),
    ("lemon-1500", "Lemon-lime 1.5L bottle", "pcs", "crate of 12", 12, 120, "18000"),
    ("water-500", "Water 500ml bottle", "pcs", "crate of 24", 24, 240, "5000"),
    ("box-small", "Pizza box small", "pcs", "bundle of 50", 50, 200, "2500"),
    ("box-medium", "Pizza box medium", "pcs", "bundle of 50", 50, 200, "3000"),
    ("box-large", "Pizza box large", "pcs", "bundle of 50", 50, 200, "3500"),
    ("fries-bag", "Fries bag", "pcs", "pack of 100", 100, 500, "500"),
]

# kind, code, {size: {item: qty}}  ('' = any size)
RECIPES = [
    ("base", "pizza", {"small": {"dough": 150, "cheese": 70}, "medium": {"dough": 200, "cheese": 95},
                       "large": {"dough": 250, "cheese": 120}}),
    ("product", "tikka", {"small": {"chicken-tikka": 50}, "medium": {"chicken-tikka": 75}, "large": {"chicken-tikka": 100}}),
    ("product", "fajita", {"small": {"chicken-fajita": 50}, "medium": {"chicken-fajita": 75}, "large": {"chicken-fajita": 100}}),
    ("product", "cheese-lover", {"small": {"cheese": 30}, "medium": {"cheese": 45}, "large": {"cheese": 60}}),
    ("product", "veggie", {"small": {"veg-mix": 60}, "medium": {"veg-mix": 90}, "large": {"veg-mix": 120}}),
    ("product", "pepperoni", {"small": {"pepperoni": 45}, "medium": {"pepperoni": 70}, "large": {"pepperoni": 90}}),
    ("option", "extra-cheese", {"small": {"cheese": 20}, "medium": {"cheese": 30}, "large": {"cheese": 40}}),
    ("option", "olives", {"small": {"olives": 15}, "medium": {"olives": 20}, "large": {"olives": 30}}),
    ("option", "jalapeno", {"small": {"jalapeno": 15}, "medium": {"jalapeno": 20}, "large": {"jalapeno": 30}}),
    ("option", "mushrooms", {"small": {"mushrooms": 20}, "medium": {"mushrooms": 30}, "large": {"mushrooms": 40}}),
    ("option", "stuffed", {"small": {"dough": 30}, "medium": {"dough": 40}, "large": {"dough": 50}}),
    ("product", "fries", {"regular": {"potato": 200, "oil": 20}, "large": {"potato": 300, "oil": 30}}),
    ("product", "wings", {"": {"wings": 6, "oil": 30}}),
    ("product", "cola-500", {"": {"cola-500": 1}}),
    ("product", "cola-1500", {"": {"cola-1500": 1}}),
    ("product", "lemon-1500", {"": {"lemon-1500": 1}}),
    ("product", "water-500", {"": {"water-500": 1}}),
    ("packaging", "pizza", {"small": {"box-small": 1}, "medium": {"box-medium": 1}, "large": {"box-large": 1}}),
    ("packaging", "fries", {"": {"fries-bag": 1}}),
]


def seed_operations(tenant, branch, b2):
    from decimal import Decimal

    from apps.catalog.models import Promotion
    from apps.cloud.models import CloudBranch
    from apps.core.models import Device
    from apps.fulfillment.models import DeliveryZone
    from apps.stock.models import Recipe, RecipeComponent, StockItem, StockLevel
    from apps.stock.services import post

    for br, zones in ((branch, [("gulberg", "Gulberg", 120, 35), ("model-town", "Model Town", 180, 45),
                                ("dha", "DHA", 250, 55)]),
                      (b2, [("johar-town", "Johar Town", 100, 35), ("wapda-town", "Wapda Town", 150, 45)])):
        for code, name, fee, eta in zones:
            DeliveryZone.objects.get_or_create(id=did(f"{br.code}-zone-{code}"), defaults={
                "branch": br, "code": code, "name": name, "fee_minor": rs(fee), "eta_minutes": eta})
    for code, name, kind, value, minimum, approval in [
        ("WEEKEND10", "Weekend 10% off", "percent", 1000, 0, False),
        ("FLAT200", "Rs 200 off orders over Rs 1,500", "fixed", rs(200), rs(1500), False),
        ("STAFF50", "Staff meal 50%", "percent", 5000, 0, True),
    ]:
        Promotion.objects.get_or_create(tenant=tenant, code=code, defaults={
            "name": name, "kind": kind, "value": value, "min_subtotal_minor": minimum, "requires_approval": approval})
    items = {}
    for code, name, unit, pu, factor, opening, cost in STOCK_ITEMS:
        items[code], _ = StockItem.objects.get_or_create(tenant=tenant, code=code, defaults={
            "name": name, "base_unit": unit, "purchase_unit": pu, "purchase_factor": factor})
    for kind, code, sizes in RECIPES:
        for size, comps in sizes.items():
            r, created = Recipe.objects.get_or_create(tenant=tenant, target_kind=kind, target_code=code,
                                                      size_code=size, version=1)
            if created:
                for item_code, qty in comps.items():
                    RecipeComponent.objects.create(recipe=r, item=items[item_code], quantity=qty)
    for br in (branch, b2):
        for code, name, unit, pu, factor, opening, cost in STOCK_ITEMS:
            post(br, items[code], "receipt", Decimal(opening), "seed:opening", unit_cost=Decimal(cost),
                 reason="Opening stock (demo)")
            # Demo reorder level: a quarter of the opening stock (kept if a manager already changed it).
            StockLevel.objects.filter(branch=br, item=items[code], reorder_level__isnull=True).update(
                reorder_level=Decimal(opening) / 4)
        CloudBranch.objects.get_or_create(branch=br, defaults={"token_hash": Device.hash_token(dev_cloud_token(br))})


def build_release(tenant, version=1, mixed_policy="higher", tax_rate_bp=0, price_overrides=None):
    price_overrides = price_overrides or {}
    r = MenuRelease.objects.create(id=did(f"release-{version}"), tenant=tenant, version=version,
                                   name=f"Menu {version}", status="published", mixed_policy=mixed_policy,
                                   tax_rate_bp=tax_rate_bp, published_at=timezone.now())
    cat_pizza = Category.objects.create(release=r, code="pizza", name="Pizza", sort=1)
    cat_deals = Category.objects.create(release=r, code="deals", name="Deals", sort=0)
    cat_sides = Category.objects.create(release=r, code="sides", name="Sides", sort=2)
    cat_drinks = Category.objects.create(release=r, code="drinks", name="Drinks", sort=3)

    crust = OptionGroup.objects.create(release=r, code="crust", name="Crust", kind="crust", min_select=1, max_select=1, sort=1)
    for i, (code, name, prices, default) in enumerate([
        ("regular", "Regular crust", (0, 0, 0), True),
        ("thin", "Thin crust", (0, 0, 0), False),
        ("stuffed", "Stuffed crust", (100, 150, 200), False),
    ]):
        o = Option.objects.create(group=crust, code=code, name=name, is_default=default, sort=i)
        for (size, _, idx) in SIZES:
            OptionPrice.objects.create(option=o, size_code=size, whole_minor=rs(prices[idx]), half_minor=0)
    tops = OptionGroup.objects.create(release=r, code="toppings", name="Extra toppings", kind="topping",
                                      min_select=0, max_select=4, sides_allowed=True, sort=2)
    for i, (code, name, whole, half, excludes) in enumerate([
        ("extra-cheese", "Extra cheese", (100, 140, 180), (50, 70, 100), []),
        ("olives", "Black olives", (60, 90, 120), (30, 50, 60), []),
        ("jalapeno", "Jalapeño", (60, 90, 120), (30, 50, 60), []),
        ("mushrooms", "Mushrooms", (80, 110, 150), (40, 60, 80), []),
        ("no-cheese", "No cheese", (0, 0, 0), (0, 0, 0), ["extra-cheese"]),
    ]):
        o = Option.objects.create(group=tops, code=code, name=name, excludes=excludes, sort=i)
        for (size, _, idx) in SIZES:
            OptionPrice.objects.create(option=o, size_code=size, whole_minor=rs(whole[idx]), half_minor=rs(half[idx]))

    flavour_products = []
    for i, (code, name, urdu, prices) in enumerate(FLAVOURS):
        p = Product.objects.create(release=r, category=cat_pizza, code=code, name=name, name_local=urdu,
                                   kind="pizza", station_code="pizza", sort=i + 1)
        p.option_groups.set([crust, tops])
        for (size, sname, idx) in SIZES:
            Variant.objects.create(product=p, code=size, name=sname,
                                   price_minor=price_overrides.get((code, size), rs(prices[idx])), sort=idx)
        flavour_products.append(p)
    mixed = Product.objects.create(release=r, category=cat_pizza, code="mixed-pizza", name="Half & Half Pizza",
                                   name_local="آدھا آدھا پیزا", kind="mixed_pizza", station_code="pizza", sort=0,
                                   description="Two flavours, one pizza")
    mixed.option_groups.set([crust, tops])
    fries = Product.objects.create(release=r, category=cat_sides, code="fries", name="Fries", name_local="فرائز",
                                   kind="item", station_code="fryer", sort=1)
    Variant.objects.create(product=fries, code="regular", name="Regular", price_minor=rs(350), sort=0)
    Variant.objects.create(product=fries, code="large", name="Large", price_minor=rs(500), sort=1)
    wings = Product.objects.create(release=r, category=cat_sides, code="wings", name="Hot Wings (6)", kind="item",
                                   station_code="fryer", sort=2)
    Variant.objects.create(product=wings, code="regular", name="Regular", price_minor=rs(650))
    drinks = []
    for i, (code, name, price) in enumerate([("cola-1500", "Cola 1.5L", 250), ("lemon-1500", "Lemon-lime 1.5L", 250),
                                              ("cola-500", "Cola 500ml", 120), ("water-500", "Water 500ml", 80)]):
        d = Product.objects.create(release=r, category=cat_drinks, code=code, name=name, kind="item",
                                   station_code="packing", sort=i)
        Variant.objects.create(product=d, code="regular", name="Regular", price_minor=rs(price))
        drinks.append(d)
    deal = Product.objects.create(release=r, category=cat_deals, code="family-deal", name="Family Deal",
                                  name_local="فیملی ڈیل", kind="deal", station_code="packing", sort=1,
                                  description="1 large pizza (any flavour or half & half) + 1.5L drink")
    Variant.objects.create(product=deal, code="regular", name="Regular", price_minor=rs(2000))
    s_pizza = DealSlot.objects.create(deal=deal, code="pizza", name="Large pizza", quantity=1, sort=0)
    for p in flavour_products + [mixed]:
        DealSlotChoice.objects.create(slot=s_pizza, product=p, variant_code="large",
                                      upgrade_minor=rs(100) if p.code == "pepperoni" else 0)
    s_drink = DealSlot.objects.create(deal=deal, code="drink", name="1.5L drink", quantity=1, sort=1)
    for d in drinks[:2]:
        DealSlotChoice.objects.create(slot=s_drink, product=d, variant_code="regular", upgrade_minor=0)
    return r
