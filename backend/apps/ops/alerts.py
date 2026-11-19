"""Branch alert centre: things a person should act on now, computed from authoritative state on every read.

Alerts are not stored: each one disappears as soon as its cause is fixed (stock received, ticket bumped, job
reprinted, refund resolved), so the list can never disagree with the ledger. Each alert has a stable ``id`` so a
client can remember which ones it has already shown, a ``severity`` (critical > warning > info), a ``category``
the caller's role is allowed to see, and an optional ``action`` the app can offer (e.g. mark dependent products
sold out). Thresholds are in settings (``ALERT_*``) with pilot defaults.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

# category -> permission needed to see it
CATEGORY_PERMS = {
    "stock": "stock.view",
    "kitchen": "station.view",
    "print": "print.reprint",
    "cash": "tender.take",
    "system": "ops.view",
}


def _setting(name, default):
    return getattr(settings, name, default)


def _qty(d, unit):
    d = float(d)
    if unit in ("g", "ml") and abs(d) >= 1000:
        return f"{d / 1000:.2f} {'kg' if unit == 'g' else 'L'}"
    return f"{int(d) if d == int(d) else round(d, 1)} {unit}"


def stock_alerts(branch):
    from apps.catalog.models import BranchAvailability
    from apps.stock.management_services import dependents, missing_recipes
    from apps.stock.models import StockItem, StockLevel

    out = []
    menu_names = {}
    if branch.active_release is not None:
        from apps.orders.services import menu_for
        menu = menu_for(branch.active_release)
        menu_names = {("product", c): p["name"] for c, p in menu.products.items()}
        menu_names.update({("option", c): o["name"] for c, o in menu.options.items()})
    unavailable = BranchAvailability.unavailable_codes(branch)
    deps = dependents(branch)
    levels = {lv.item_id: lv for lv in StockLevel.objects.filter(branch=branch)}
    for item in StockItem.objects.filter(tenant=branch.tenant, is_active=True).order_by("name"):
        lvl = levels.get(item.pk)
        on_hand = lvl.on_hand if lvl else 0
        reorder = lvl.reorder_level if lvl else None
        if on_hand > 0 and (reorder is None or on_hand > reorder):
            continue
        d = deps.get(item.pk, {"products": set(), "options": set()})
        on_sale = [{"kind": "product", "code": c, "name": menu_names.get(("product", c), c)}
                   for c in sorted(d["products"]) if ("product", c) in menu_names and c not in unavailable["product"]]
        on_sale += [{"kind": "option", "code": c, "name": menu_names.get(("option", c), c)}
                    for c in sorted(d["options"]) if ("option", c) in menu_names and c not in unavailable["option"]]
        out_ = on_hand <= 0
        detail = (f"On hand {_qty(on_hand, item.base_unit)}"
                  + (f", reorder at {_qty(reorder, item.base_unit)}" if reorder is not None else ""))
        if on_sale:
            detail += ". Still on sale: " + ", ".join(x["name"] for x in on_sale[:6]) + (
                f" +{len(on_sale) - 6} more" if len(on_sale) > 6 else "")
        out.append({
            "id": f"stock:{'out' if out_ else 'low'}:{item.code}", "category": "stock",
            "severity": "critical" if out_ else "warning",
            "title": f"{item.name} {'is out of stock' if out_ else 'is running low'}",
            "detail": detail, "at": lvl.updated_at if lvl else None,
            "ref": {"item": item.code, "on_hand": on_hand, "reorder_level": reorder, "unit": item.base_unit},
            "action": {"type": "sellout", "targets": on_sale} if on_sale else {"type": "receive", "item": item.code},
        })
    missing = missing_recipes(branch)
    if missing:
        out.append({
            "id": "stock:recipes_missing", "category": "stock", "severity": "info",
            "title": f"{len(missing)} menu item{'s' if len(missing) != 1 else ''} without a recipe",
            "detail": "Stock is not consumed and margins are unknown for: "
                      + ", ".join(m["label"] for m in missing[:5]) + (" …" if len(missing) > 5 else ""),
            "at": None, "ref": {"missing": missing}, "action": {"type": "recipes"},
        })
    return out


def kitchen_alerts(branch, now):
    from apps.kitchen.models import ProductionTicket, TicketItem
    from apps.kitchen.services import ticket_header

    out = []
    late = (TicketItem.objects.filter(ticket__branch=branch, status__in=["queued", "preparing"],
                                      ticket__due_at__lt=now)
            .select_related("ticket__station", "ticket__check_ref__table").order_by("ticket__due_at"))
    by_ticket = {}
    for it in late:
        by_ticket.setdefault(it.ticket_id, (it.ticket, []))[1].append(it)
    critical_after = _setting("ALERT_KITCHEN_CRITICAL_MINUTES", 10)
    for t, items in by_ticket.values():
        mins = int((now - t.due_at).total_seconds() // 60)
        out.append({
            "id": f"kitchen:late:{t.pk}", "category": "kitchen",
            "severity": "critical" if mins >= critical_after else "warning",
            "title": f"KOT {t.seq} late at {t.station.name} · {ticket_header(t.check_ref)}",
            "detail": f"{mins} min past due: " + ", ".join(f"{i.quantity}× {i.label}" for i in items[:4])
                      + (" …" if len(items) > 4 else ""),
            "at": t.due_at, "ref": {"ticket": str(t.pk), "station": t.station.code, "minutes_late": mins},
            "action": None,
        })
    grace = timedelta(minutes=_setting("ALERT_CHANGE_ACK_MINUTES", 2))
    for t in (ProductionTicket.objects.filter(branch=branch, kind__in=["change", "remake"], acknowledged_at=None,
                                              created_at__lt=now - grace)
              .select_related("station", "check_ref__table").order_by("created_at")):
        out.append({
            "id": f"kitchen:unacked:{t.pk}", "category": "kitchen", "severity": "warning",
            "title": f"{t.station.name} has not acknowledged a {t.get_kind_display().lower()}",
            "detail": f"{ticket_header(t.check_ref)}: {t.note or 'see KDS'}", "at": t.created_at,
            "ref": {"ticket": str(t.pk), "station": t.station.code}, "action": None,
        })
    return out


def print_alerts(branch, now):
    from apps.kitchen.models import PrintJob
    from apps.kitchen.services import serialize_job

    out = []
    problems = (PrintJob.objects.filter(branch=branch, status__in=["failed_before_send", "outcome_unknown"])
                .exclude(copies__status="handed_to_spooler")
                .select_related("printer", "ticket", "check_ref").order_by("-created_at")[:20])
    for j in problems:
        s = serialize_job(j)
        what = f"KOT {j.ticket.seq}" if j.kind == "kitchen" and j.ticket_id else j.kind.replace("_", " ")
        if j.check_ref_id:
            what += f" #{j.check_ref.token}"
        unknown = j.status == "outcome_unknown"
        out.append({
            "id": f"print:{j.pk}", "category": "print", "severity": "critical",
            "title": f"{what[0].upper() + what[1:]} {'may not have printed' if unknown else 'did not print'} on {j.printer.name}",
            "detail": " ".join(x for x in (s["next_action"], j.last_error) if x),
            "at": j.created_at, "ref": {"job": str(j.pk), "kind": j.kind, "ticket_seq": s["ticket_seq"]},
            "action": {"type": "reprint", "job": str(j.pk)},
        })
    stuck_after = timedelta(minutes=_setting("ALERT_PRINT_STUCK_MINUTES", 2))
    stuck = PrintJob.objects.filter(branch=branch, status="queued", created_at__lt=now - stuck_after)
    n = stuck.count()
    if n:
        oldest = stuck.order_by("created_at").first()
        out.append({
            "id": "print:stuck", "category": "print", "severity": "critical",
            "title": f"{n} print job{'s' if n != 1 else ''} waiting — is the print worker running?",
            "detail": f"Oldest queued since {timezone.localtime(oldest.created_at):%H:%M}. "
                      "Kitchen can still read orders on the KDS.",
            "at": oldest.created_at, "ref": {"count": n}, "action": None,
        })
    return out


def cash_alerts(branch):
    from apps.cash.models import Refund
    out = []
    for r in Refund.objects.filter(branch=branch, status="pending").select_related("check_ref").order_by("created_at"):
        out.append({
            "id": f"cash:refund:{r.pk}", "category": "cash", "severity": "warning",
            "title": f"Card refund #{r.check_ref.token} not confirmed",
            "detail": f"Rs {r.amount_minor / 100:,.0f} — confirm on the terminal and resolve it at the counter",
            "at": r.created_at, "ref": {"refund": str(r.pk)}, "action": None,
        })
    return out


def system_alerts(branch):
    from apps.kitchen.services import device_health
    h = device_health(branch)
    out = []
    b = h["backup"]
    if b["stale"]:
        out.append({
            "id": "system:backup", "category": "system", "severity": "warning",
            "title": "No recent backup" if b["age_minutes"] is None else f"Last backup {b['age_minutes']} min ago",
            "detail": "Run the backup task (make backup) and check the scheduler.", "at": b["last_backup_at"],
            "ref": {}, "action": None,
        })
    c = h["cloud"]
    if c["status"] == "stale":
        out.append({
            "id": "system:cloud", "category": "system", "severity": "warning",
            "title": f"Cloud sync behind ({c['pending_events']} events waiting)",
            "detail": (c["last_error"] or "Selling continues offline; online orders pause until sync resumes."),
            "at": c["last_success_at"], "ref": {}, "action": None,
        })
    sv = h.get("services")
    if sv is not None:
        names = {"server": "Branch server", "print": "Print worker"}
        if sv["stale"]:
            out.append({
                "id": "system:supervisor", "category": "system", "severity": "critical",
                "title": "Branch services are not being supervised",
                "detail": "The VibeOrder Branch service stopped: printing, sync and backups will not recover on "
                          "their own. Start it again (deploy/README.md).", "at": sv.get("updated_at"),
                "ref": {}, "action": None,
            })
        else:
            for name, c in sv["children"].items():
                if c["status"] != "running" or c["exits_last_10min"] >= 3:
                    label = names.get(name, name.replace("sync-", "Cloud sync "))
                    out.append({
                        "id": f"system:service:{name}", "category": "system", "severity": "critical",
                        "title": f"{label} keeps stopping" if c["exits_last_10min"] >= 3 else f"{label} is restarting",
                        "detail": f"{c['exits_last_10min']} exits in 10 min, last exit code {c['last_exit_code']}. "
                                  f"See var/logs/{name}.log.", "at": c.get("last_exit_at"), "ref": {"service": name},
                        "action": None,
                    })
            bk = sv.get("backup") or {}
            if bk.get("last_ok") is False:
                out.append({
                    "id": "system:backup_failed", "category": "system", "severity": "critical",
                    "title": "Scheduled backup failed", "detail": bk.get("last_error") or "see var/logs",
                    "at": bk.get("last_at"), "ref": {}, "action": None,
                })
    if not h["branch"]["writable"]:
        out.append({
            "id": "system:fenced", "category": "system", "severity": "critical",
            "title": "This server is read-only (another host is writable)",
            "detail": "Use the writable host, or run take_over after isolating it.", "at": None, "ref": {},
            "action": None,
        })
    return out


def alerts_for(branch, principal):
    now = timezone.now()
    allowed = {c for c, perm in CATEGORY_PERMS.items() if principal.has(perm)}
    out = []
    if "stock" in allowed:
        out += stock_alerts(branch)
    if "kitchen" in allowed:
        out += kitchen_alerts(branch, now)
    if "print" in allowed:
        out += print_alerts(branch, now)
    if "cash" in allowed:
        out += cash_alerts(branch)
    if "system" in allowed:
        out += system_alerts(branch)
    out.sort(key=lambda a: (SEVERITY_ORDER[a["severity"]], a["category"], a["title"]))
    counts = {s: sum(1 for a in out if a["severity"] == s) for s in SEVERITY_ORDER}
    return {"alerts": out, "counts": counts, "server_time": now}
