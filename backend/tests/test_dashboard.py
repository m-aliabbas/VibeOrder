"""Manager dashboard: totals agree with the sales report, every day is present, waiters cannot see it."""
from django.core.management import call_command

from apps.orders.models import Check


def test_dashboard_matches_sales_report_and_fills_days(demo, manager, waiter, settings):
    settings.DEBUG = True
    call_command("seed_history", days=10, verbosity=0)
    call_command("seed_history", days=10, verbosity=0)  # second run adds nothing
    assert Check.objects.filter(token__startswith="H", business_date=Check.objects.first().business_date).count() < 200

    r = manager.get("dashboard", days=14)
    assert r.status_code == 200, r.content
    d = r.json()
    assert len(d["sales_by_day"]) == 14 and d["sales_by_day"][0]["net_minor"] == 0, "days without sales are zero"
    rep = manager.get("reports/sales", **{"from": d["range"]["from"], "to": d["range"]["to"]}).json()
    report_net = sum(row["net_merchandise_minor"] for row in rep["rows"])
    assert d["kpis"]["net_minor"] == report_net == sum(x["net_minor"] for x in d["sales_by_day"])
    assert d["kpis"]["orders"] == sum(x["orders"] for x in d["sales_by_day"]) > 0
    assert sum(h["orders"] for h in d["by_hour"]) == d["kpis"]["orders"]
    assert sum(c["orders"] for c in d["channels"]) == d["kpis"]["orders"]
    assert {c["code"] for c in d["channels"]} <= {"dine_in", "counter", "pickup", "delivery", "web"}
    assert sum(p["amount_minor"] for p in d["payments"]) == sum(c["net_minor"] for c in d["channels"])
    assert d["top_items"] and d["top_items"][0]["quantity"] >= d["top_items"][-1]["quantity"]

    assert manager.get("dashboard", days=5).status_code == 422
    assert waiter.get("dashboard").status_code == 403
