"""Deterministic pricing (spec 04 §Price calculation).

All amounts are integer PKR minor units (100 per rupee). Decimal is used only at rounding boundaries.
The engine is pure: give it a loaded Menu + item specs + unavailable codes, get priced lines back.
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from apps.core.errors import DomainError

from .models import DealSlot, MenuRelease

SIDES = ("whole", "left", "right")


def round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def allocate(amount: int, weights: dict) -> dict:
    """Split `amount` proportionally to weights using largest remainder; ties broken by stable key order."""
    total = sum(weights.values())
    if amount == 0 or total == 0:
        return {k: 0 for k in weights}
    keys = sorted(weights)
    shares, remainders = {}, []
    for k in keys:
        exact = Decimal(amount) * Decimal(weights[k]) / Decimal(total)
        floor = int(exact // 1)
        shares[k] = floor
        remainders.append((exact - floor, k))
    leftover = amount - sum(shares.values())
    # Largest remainder first; on equal remainder, earlier key wins.
    remainders.sort(key=lambda r: (-r[0], keys.index(r[1])))
    for _, k in remainders[:leftover]:
        shares[k] += 1
    return shares


class PricingError(DomainError):
    http_status = 422
    code = "invalid_item"


@dataclass
class Menu:
    release: MenuRelease
    products: dict
    groups: dict
    options: dict  # option code -> dict
    slots: dict  # deal code -> list

    @classmethod
    def load(cls, release: MenuRelease) -> "Menu":
        products, groups, options, slots = {}, {}, {}, {}
        for g in release.option_groups.prefetch_related("options__prices"):
            groups[g.code] = {
                "code": g.code, "name": g.name, "kind": g.kind, "min": g.min_select, "max": g.max_select,
                "sides": g.sides_allowed, "options": [o.code for o in g.options.all()],
            }
            for o in g.options.all():
                options[o.code] = {
                    "code": o.code, "name": o.name, "group": g.code, "default": o.is_default, "excludes": o.excludes,
                    "prices": {p.size_code: (p.whole_minor, p.half_minor) for p in o.prices.all()},
                }
        for p in release.products.select_related("category").prefetch_related("variants", "option_groups"):
            products[p.code] = {
                "code": p.code, "name": p.name, "name_local": p.name_local, "kind": p.kind,
                "station": p.station_code, "category": p.category.code,
                "variants": {v.code: {"code": v.code, "name": v.name, "price": v.price_minor} for v in p.variants.all()},
                "groups": [g.code for g in p.option_groups.all()],
            }
        for s in DealSlot.objects.filter(deal__release=release).prefetch_related(
            "choices__product"
        ).select_related("deal").order_by("sort"):
            slots.setdefault(s.deal.code, []).append({
                "code": s.code, "name": s.name, "quantity": s.quantity,
                "choices": {(c.product.code, c.variant_code): c.upgrade_minor for c in s.choices.all()},
            })
        return cls(release, products, groups, options, slots)


@dataclass
class PricedLine:
    client_line_id: str
    product: str
    name: str
    variant: str
    quantity: int
    unit_minor: int
    total_minor: int
    breakdown: list
    spec: dict
    components: list = field(default_factory=list)

    def as_dict(self):
        return {
            "client_line_id": self.client_line_id, "product": self.product, "name": self.name,
            "variant": self.variant, "quantity": self.quantity, "unit_minor": self.unit_minor,
            "total_minor": self.total_minor, "breakdown": self.breakdown, "spec": self.spec,
            "components": self.components,
        }


class Pricer:
    def __init__(self, menu: Menu, unavailable: dict | None = None):
        self.menu = menu
        self.unavailable = unavailable or {"product": set(), "option": set()}

    # -- helpers -------------------------------------------------------
    def _product(self, code, field_path):
        p = self.menu.products.get(code)
        if p is None:
            raise PricingError(f"Unknown product {code}", field_errors={field_path: "unknown_product"})
        if code in self.unavailable.get("product", set()):
            raise PricingError(f"{p['name']} is sold out", code="sold_out", field_errors={field_path: "sold_out"})
        return p

    def _option_price(self, opt, size, side):
        prices = opt["prices"]
        whole, half = prices.get(size) or prices.get("") or (0, 0)
        return whole if side == "whole" else half

    def _validate_options(self, product, size, raw_options, crust, path):
        """Return (crust_opt, [(opt, side)]) after validating group min/max, sides, exclusions, sellouts."""
        chosen = []
        for i, o in enumerate(raw_options or []):
            code, side = o.get("id"), o.get("side", "whole")
            opt = self.menu.options.get(code)
            if opt is None or opt["group"] not in product["groups"]:
                raise PricingError(f"Option {code} not allowed", field_errors={f"{path}.options[{i}]": "not_allowed"})
            if side not in SIDES:
                raise PricingError("Side must be whole, left or right", field_errors={f"{path}.options[{i}].side": "invalid"})
            group = self.menu.groups[opt["group"]]
            if side != "whole" and not group["sides"]:
                raise PricingError(f"{opt['name']} cannot be applied to one half", field_errors={f"{path}.options[{i}].side": "whole_only"})
            if code in self.unavailable.get("option", set()):
                raise PricingError(f"{opt['name']} is sold out", code="sold_out", field_errors={f"{path}.options[{i}]": "sold_out"})
            chosen.append((opt, side))
        crust_opt = None
        crust_groups = [g for g in product["groups"] if self.menu.groups[g]["kind"] == "crust"]
        if crust_groups:
            g = self.menu.groups[crust_groups[0]]
            code = crust or next((c for c in g["options"] if self.menu.options[c]["default"]), None)
            crust_opt = self.menu.options.get(code)
            if crust_opt is None or crust_opt["group"] != g["code"]:
                raise PricingError("Choose a crust", field_errors={f"{path}.crust": "required"})
            if code in self.unavailable.get("option", set()):
                raise PricingError(f"{crust_opt['name']} is sold out", code="sold_out", field_errors={f"{path}.crust": "sold_out"})
        # Group max applies to each half: whole selections count on both sides.
        for gcode in {opt["group"] for opt, _ in chosen}:
            g = self.menu.groups[gcode]
            for side in ("left", "right"):
                n = sum(1 for o, s in chosen if o["group"] == gcode and s in ("whole", side))
                if n > g["max"]:
                    raise PricingError(f"Too many {g['name']} choices (max {g['max']})",
                                       field_errors={f"{path}.options": "too_many"})
        codes = {opt["code"] for opt, _ in chosen}
        for opt, _ in chosen:
            clash = codes.intersection(opt["excludes"])
            if clash:
                raise PricingError(f"{opt['name']} cannot be combined with {', '.join(sorted(clash))}",
                                   field_errors={f"{path}.options": "excluded_combination"})
        seen = set()
        for opt, side in chosen:
            if (opt["code"], side) in seen:
                raise PricingError(f"{opt['name']} chosen twice", field_errors={f"{path}.options": "duplicate"})
            seen.add((opt["code"], side))
        return crust_opt, chosen

    # -- pizza ----------------------------------------------------------
    def price_pizza(self, spec, path, in_deal=False):
        """Price one physical pizza. Returns (amount, breakdown, normalized_spec, component)."""
        product = self._product(spec.get("product"), f"{path}.product")
        size = spec.get("variant")
        breakdown = []
        halves = spec.get("halves")
        if product["kind"] == "mixed_pizza":
            if not halves or not halves.get("left") or not halves.get("right"):
                raise PricingError("Choose a flavour for each half", field_errors={f"{path}.halves": "required"})
            left = self._product(halves["left"], f"{path}.halves.left")
            right = self._product(halves["right"], f"{path}.halves.right")
            for side, fl in (("left", left), ("right", right)):
                if fl["kind"] != "pizza":
                    raise PricingError("Halves must be pizza flavours", field_errors={f"{path}.halves.{side}": "not_flavour"})
                if size not in fl["variants"]:
                    raise PricingError(f"{fl['name']} not available in {size}", field_errors={f"{path}.variant": "undefined_price"})
            lp, rp = left["variants"][size]["price"], right["variants"][size]["price"]
            policy = self.menu.release.mixed_policy
            if policy == "higher":
                base, rule = max(lp, rp), "mixed.higher"
            elif policy == "average":
                base, rule = round_half_up(Decimal(lp + rp) / 2), "mixed.average"
            else:
                v = product["variants"].get(size)
                if v is None:
                    raise PricingError("No explicit mixed price for this size", field_errors={f"{path}.variant": "undefined_price"})
                base, rule = v["price"], "mixed.explicit"
            label = f"{left['name']} / {right['name']}"
        elif product["kind"] == "pizza":
            v = product["variants"].get(size)
            if v is None:
                raise PricingError(f"Size {size} not available", field_errors={f"{path}.variant": "unknown_variant"})
            base, rule, label = v["price"], "variant.price", product["name"]
            halves = None
        else:
            raise PricingError("Not a pizza", field_errors={f"{path}.product": "not_pizza"})
        size_name = self._size_name(product, halves, size)
        if in_deal:
            breakdown.append({"rule": "deal.included", "label": f"{size_name} {label} (in deal)", "amount_minor": 0})
            base = 0
        else:
            breakdown.append({"rule": rule, "label": f"{size_name} {label}", "amount_minor": base})

        crust_opt, chosen = self._validate_options(product, size, spec.get("options"), spec.get("crust"), path)
        amount = base
        if crust_opt:
            c = self._option_price(crust_opt, size, "whole")
            amount += c
            breakdown.append({"rule": "crust", "label": crust_opt["name"], "amount_minor": c})
        for opt, side in chosen:
            c = self._option_price(opt, size, side)
            amount += c
            side_txt = "" if side == "whole" else f" ({side.upper()} half)"
            breakdown.append({"rule": f"option.{side}", "label": f"{opt['name']}{side_txt}", "amount_minor": c})

        norm = {
            "product": product["code"], "variant": size, "crust": crust_opt["code"] if crust_opt else None,
            "halves": halves if product["kind"] == "mixed_pizza" else None,
            "options": [{"id": o["code"], "side": s} for o, s in chosen],
        }
        if spec.get("notes"):
            norm["notes"] = str(spec["notes"])[:200]
        component = {
            "station": product["station"],
            "kind": "pizza",
            "label": f"{size_name.upper()} {'MIXED ' if halves else ''}PIZZA",
            "lines": self._kitchen_lines(product, halves, crust_opt, chosen),
            "notes": norm.get("notes", ""),
        }
        return amount, breakdown, norm, component

    def _size_name(self, product, halves, size):
        if halves:
            fl = self.menu.products[halves["left"]]
            return fl["variants"][size]["name"]
        v = product["variants"].get(size)
        return v["name"] if v else size

    def _kitchen_lines(self, product, halves, crust_opt, chosen):
        out = []
        if crust_opt:
            out.append(f"Crust: {crust_opt['name']}")
        if halves:
            for side in ("left", "right"):
                fl = self.menu.products[halves[side]]
                adds = [o["name"] for o, s in chosen if s == side]
                out.append(f"{side.upper()} half: {fl['name']}" + (f" + {', '.join(adds)}" if adds else ""))
        else:
            out.append(f"Flavour: {product['name']}")
            for o, s in chosen:
                if s != "whole":
                    out.append(f"{s.upper()} half: + {o['name']}")
        whole = [o["name"] for o, s in chosen if s == "whole"]
        if whole:
            out.append("WHOLE: + " + ", ".join(whole))
        return out

    # -- simple item ------------------------------------------------------
    def price_item(self, spec, path, in_deal=False):
        product = self._product(spec.get("product"), f"{path}.product")
        size = spec.get("variant") or next(iter(product["variants"]), None)
        v = product["variants"].get(size)
        if v is None:
            raise PricingError("Unknown size", field_errors={f"{path}.variant": "unknown_variant"})
        _, chosen = self._validate_options(product, size, spec.get("options"), None, path)
        base = 0 if in_deal else v["price"]
        label = product["name"] if len(product["variants"]) == 1 else f"{v['name']} {product['name']}"
        breakdown = [{"rule": "deal.included" if in_deal else "variant.price", "label": label, "amount_minor": base}]
        amount = base
        for opt, side in chosen:
            c = self._option_price(opt, size, side)
            amount += c
            breakdown.append({"rule": "option.whole", "label": opt["name"], "amount_minor": c})
        norm = {"product": product["code"], "variant": size, "options": [{"id": o["code"], "side": s} for o, s in chosen]}
        if spec.get("notes"):
            norm["notes"] = str(spec["notes"])[:200]
        component = {
            "station": product["station"], "kind": "item", "label": label.upper(),
            "lines": [o["name"] for o, _ in chosen], "notes": norm.get("notes", ""),
        }
        return amount, breakdown, norm, component

    def _price_component(self, spec, path, in_deal=False):
        product = self._product(spec.get("product"), f"{path}.product")
        if product["kind"] in ("pizza", "mixed_pizza"):
            return self.price_pizza(spec, path, in_deal)
        if product["kind"] == "deal":
            raise PricingError("Deals cannot be nested", field_errors={f"{path}.product": "nested_deal"})
        return self.price_item(spec, path, in_deal)

    # -- deal -------------------------------------------------------------
    def price_deal(self, spec, path):
        deal = self._product(spec.get("product"), f"{path}.product")
        v = next(iter(deal["variants"].values()))
        slots = self.menu.slots.get(deal["code"], [])
        selections = spec.get("selections") or []
        by_slot = {}
        for i, sel in enumerate(selections):
            by_slot.setdefault(sel.get("slot"), []).append((i, sel))
        unknown = set(by_slot) - {s["code"] for s in slots}
        if unknown:
            raise PricingError("Unknown deal slot", field_errors={f"{path}.selections": f"unknown_slot:{sorted(unknown)[0]}"})
        amount = v["price"]
        breakdown = [{"rule": "deal.price", "label": deal["name"], "amount_minor": v["price"]}]
        components, norm_sel = [], []
        for slot in slots:
            chosen = by_slot.get(slot["code"], [])
            if len(chosen) != slot["quantity"]:
                raise PricingError(f"{slot['name']}: choose {slot['quantity']}",
                                   field_errors={f"{path}.selections.{slot['code']}": "slot_cardinality"})
            for i, sel in chosen:
                sp = f"{path}.selections[{i}]"
                eligible_key = (sel.get("product"), sel.get("variant"))
                if eligible_key not in slot["choices"]:
                    # Mixed pizza eligibility is declared on the mixed product.
                    raise PricingError(f"{sel.get('product')} is not eligible for {slot['name']}",
                                       field_errors={sp: "not_eligible"})
                upgrade = slot["choices"][eligible_key]
                a, b, n, comp = self._price_component(sel, sp, in_deal=True)
                if upgrade:
                    b.insert(1, {"rule": "deal.upgrade", "label": "Upgrade", "amount_minor": upgrade})
                amount += a + upgrade
                breakdown.extend(b)
                n["slot"] = slot["code"]
                norm_sel.append(n)
                comp["slot"] = slot["code"]
                comp["slot_name"] = slot["name"]
                components.append(comp)
        norm = {"product": deal["code"], "variant": v["code"], "selections": norm_sel}
        return amount, breakdown, norm, components

    # -- entry ------------------------------------------------------------
    def price_line(self, spec, index) -> PricedLine:
        path = f"items[{index}]"
        qty = spec.get("quantity", 1)
        if not isinstance(qty, int) or qty < 1 or qty > 99:
            raise PricingError("Quantity must be 1-99", field_errors={f"{path}.quantity": "invalid"})
        product = self._product(spec.get("product"), f"{path}.product")
        if product["kind"] == "deal":
            unit, breakdown, norm, comps = self.price_deal(spec, path)
        else:
            unit, breakdown, norm, comp = self._price_component(spec, path)
            comps = [comp]
        name = product["name"]
        if product["kind"] == "mixed_pizza":
            name = f"{self._size_name(product, norm['halves'], norm['variant'])} Half & Half Pizza"
        elif product["kind"] == "pizza":
            name = f"{product['variants'][norm['variant']]['name']} {product['name']}"
        return PricedLine(
            client_line_id=str(spec.get("client_line_id") or f"line-{index}"),
            product=product["code"], name=name, variant=norm.get("variant") or "",
            quantity=qty, unit_minor=unit, total_minor=unit * qty, breakdown=breakdown, spec=norm, components=comps,
        )

    def price(self, items, discount_minor=0, delivery_fee_minor=0):
        if not items:
            raise PricingError("Add at least one item", field_errors={"items": "empty"})
        lines = [self.price_line(spec, i) for i, spec in enumerate(items)]
        return lines, compute_totals(
            lines, self.menu.release.tax_rate_bp, discount_minor=discount_minor, delivery_fee_minor=delivery_fee_minor
        )


def compute_totals(lines, tax_rate_bp, discount_minor=0, delivery_fee_minor=0):
    """Tax-exclusive line rounding, half up (pilot test profile). Discount allocated by largest remainder."""
    weights = {ln.client_line_id: ln.total_minor for ln in lines}
    discount_minor = min(discount_minor, sum(weights.values()))
    alloc = allocate(discount_minor, weights)
    rate = Decimal(tax_rate_bp) / Decimal(10000)
    tax_total = 0
    per_line = {}
    for ln in lines:
        taxable = ln.total_minor - alloc[ln.client_line_id]
        tax = round_half_up(Decimal(taxable) * rate)
        tax_total += tax
        per_line[ln.client_line_id] = {"discount_minor": alloc[ln.client_line_id], "tax_minor": tax}
    subtotal = sum(weights.values())
    return {
        "subtotal_minor": subtotal,
        "discount_minor": discount_minor,
        "tax_minor": tax_total,
        "delivery_fee_minor": delivery_fee_minor,
        "total_minor": subtotal - discount_minor + tax_total + delivery_fee_minor,
        "per_line": per_line,
    }
