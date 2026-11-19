"""A12: commercial fixtures from spec 04."""
import pytest

from apps.catalog.models import BranchAvailability
from apps.catalog.pricing import Menu, Pricer, PricingError, allocate, compute_totals
from apps.core.fixtures_demo import build_release

from .conftest import MIXED_PIZZA  # noqa: F401  (fixtures via conftest)


def pricer(release, unavailable=None):
    return Pricer(Menu.load(release), unavailable)


def test_mixed_higher_policy_1900(demo):
    lines, totals = pricer(demo["release"]).price([MIXED_PIZZA])
    assert lines[0].unit_minor == 190000
    assert totals["total_minor"] == 190000
    assert len(lines[0].components) == 1, "kitchen receives one pizza"
    assert lines[0].components[0]["station"] == "pizza"
    assert "RIGHT half: Chicken Fajita + Extra cheese" in lines[0].components[0]["lines"]


def test_mixed_average_policy_1800(demo):
    rel = build_release(demo["tenant"], version=2, mixed_policy="average")
    lines, totals = pricer(rel).price([MIXED_PIZZA])
    assert totals["total_minor"] == 180000


def test_family_deal_with_upgrades_and_delivery_2420(demo):
    item = {"client_line_id": "D1", "product": "family-deal", "quantity": 1, "selections": [
        {"slot": "pizza", "product": "mixed-pizza", "variant": "large", "crust": "stuffed",
         "halves": {"left": "tikka", "right": "fajita"}, "options": [{"id": "extra-cheese", "side": "right"}]},
        {"slot": "drink", "product": "cola-1500", "variant": "regular"},
    ]}
    lines, totals = pricer(demo["release"]).price([item], delivery_fee_minor=12000)
    assert lines[0].unit_minor == 230000
    assert totals["total_minor"] == 242000
    stations = sorted(c["station"] for c in lines[0].components)
    assert stations == ["packing", "pizza"], "pack checklist has a pizza and a drink"


def test_deal_slot_cardinality_and_eligibility(demo):
    p = pricer(demo["release"])
    with pytest.raises(PricingError):
        p.price([{"product": "family-deal", "selections": [{"slot": "pizza", "product": "tikka", "variant": "large"}]}])
    with pytest.raises(PricingError):
        p.price([{"product": "family-deal", "selections": [
            {"slot": "pizza", "product": "tikka", "variant": "small"},
            {"slot": "drink", "product": "cola-1500", "variant": "regular"}]}])


def test_discount_allocation_largest_remainder():
    assert allocate(1000, {"a": 10000, "b": 10000, "c": 10000}) == {"a": 334, "b": 333, "c": 333}


def test_exclusive_tax_after_discount_99():
    class L:
        client_line_id = "x"
        total_minor = 10000
    t = compute_totals([L()], tax_rate_bp=1000, discount_minor=1000)
    assert t["tax_minor"] == 900
    assert t["total_minor"] == 9900


def test_sold_out_option_rejected(demo):
    unavailable = {"product": set(), "option": {"olives"}}
    item = {**MIXED_PIZZA, "options": [{"id": "olives", "side": "left"}]}
    with pytest.raises(PricingError) as e:
        pricer(demo["release"], unavailable).price([item])
    assert e.value.code == "sold_out"


def test_excluded_combination_and_limits(demo):
    p = pricer(demo["release"])
    with pytest.raises(PricingError):
        p.price([{**MIXED_PIZZA, "options": [{"id": "extra-cheese", "side": "whole"}, {"id": "no-cheese", "side": "whole"}]}])
    too_many = [{"id": c, "side": "left"} for c in ["extra-cheese", "olives", "jalapeno", "mushrooms"]] + [{"id": "olives", "side": "whole"}]
    with pytest.raises(PricingError):
        p.price([{**MIXED_PIZZA, "options": too_many}])


def test_whole_and_half_topping_prices(demo):
    whole = {**MIXED_PIZZA, "options": [{"id": "extra-cheese", "side": "whole"}]}
    lines, _ = pricer(demo["release"]).price([whole])
    assert lines[0].unit_minor == 160000 + 20000 + 18000


def test_undefined_size_rejected(demo):
    with pytest.raises(PricingError):
        pricer(demo["release"]).price([{**MIXED_PIZZA, "variant": "xxl"}])


def test_preview_quote_not_persisted(demo, waiter):
    from apps.orders.models import Quote
    r = waiter.post("quotes", {"items": [MIXED_PIZZA], "preview": True}, key=False)
    assert r.status_code == 201 and r.json()["total_minor"] == 190000 and r.json()["quote_id"] is None
    assert Quote.objects.count() == 0
