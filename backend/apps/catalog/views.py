from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.core.commands import run_command
from apps.core.errors import DomainError
from apps.core.scope import branch_for, require

from .models import BranchAvailability
from .pricing import Menu


def image_url(product):
    """Menu photo: explicit Product.image, else media/menu/<code>.(webp|jpg|png) when present."""
    from pathlib import Path

    from django.conf import settings
    root = Path(settings.MEDIA_ROOT) / "menu"
    names = [product.image] if product.image else [f"{product.code}.{ext}" for ext in ("webp", "jpg", "png")]
    for n in names:
        if n and (root / n).exists():
            return f"{settings.MEDIA_URL}menu/{n}"
    return None


def serialize_menu(branch):
    from apps.orders.services import menu_for

    release = branch.active_release
    if release is None:
        return {"release": None, "categories": []}
    menu: Menu = menu_for(release)
    unavailable = BranchAvailability.unavailable_codes(branch)
    cats = []
    for c in release.categories.order_by("sort"):
        prods = []
        for p in c.products.order_by("sort"):
            pd = menu.products[p.code]
            prods.append({
                **{k: pd[k] for k in ("code", "name", "name_local", "kind", "station")},
                "description": p.description,
                "image_url": image_url(p),
                "available": p.code not in unavailable["product"],
                "variants": [{"code": v.code, "name": v.name, "price_minor": v.price_minor} for v in p.variants.order_by("sort")],
                "option_groups": pd["groups"],
                "slots": [{"code": s["code"], "name": s["name"], "quantity": s["quantity"],
                           "choices": [{"product": k[0], "variant": k[1], "upgrade_minor": v} for k, v in s["choices"].items()]}
                          for s in menu.slots.get(p.code, [])],
            })
        cats.append({"code": c.code, "name": c.name, "products": prods})
    groups = []
    for g in menu.groups.values():
        groups.append({
            **{k: g[k] for k in ("code", "name", "kind", "min", "max", "sides")},
            "options": [{"code": o, "name": menu.options[o]["name"], "default": menu.options[o]["default"],
                         "excludes": menu.options[o]["excludes"], "available": o not in unavailable["option"],
                         "prices": [{"size": s or None, "whole_minor": w, "half_minor": h}
                                    for s, (w, h) in menu.options[o]["prices"].items()]}
                        for o in g["options"]],
        })
    return {
        "release": {"id": str(release.pk), "version": release.version, "name": release.name,
                    "mixed_policy": release.mixed_policy, "tax_rate_bp": release.tax_rate_bp},
        "currency": branch.currency, "categories": cats, "option_groups": groups, "server_time": timezone.now(),
    }


@api_view(["GET"])
def menu(request, branch_id):
    branch = branch_for(request, branch_id)
    return Response(serialize_menu(branch))


@api_view(["GET", "POST"])
def set_availability(request, branch_id):
    """Branch sellout switch (manager). GET lists what is currently sold out, with who and why."""
    branch = branch_for(request, branch_id)
    if request.method == "GET":
        now = timezone.now()
        rows = BranchAvailability.objects.filter(branch=branch, available=False).select_related("actor")
        return Response({"sold_out": [
            {"target_kind": r.target_kind, "target_code": r.target_code, "reason": r.reason,
             "actor": r.actor.display_name if r.actor_id else None, "since": r.updated_at,
             "expires_at": r.expires_at} for r in rows.order_by("-updated_at")
            if r.expires_at is None or r.expires_at > now]})
    require(request, "availability.set")
    data = request.data

    def handler(ctx):
        kind, code = data.get("target_kind"), data.get("target_code")
        if kind not in ("product", "option") or not code:
            raise DomainError("target_kind and target_code required", field_errors={"target_kind": "invalid"})
        row, _ = BranchAvailability.objects.select_for_update().get_or_create(
            branch=ctx.branch, target_kind=kind, target_code=code, defaults={"actor": ctx.staff})
        row.available = bool(data.get("available", False))
        row.reason = str(data.get("reason", ""))[:200]
        row.actor = ctx.staff
        row.revision += 1
        row.save()
        ctx.audit("availability.set", row, row.revision - 1, row.revision, reason=row.reason,
                  data={"kind": kind, "code": code, "available": row.available})
        ctx.emit("availability.changed", row, {"kind": kind, "code": code, "available": row.available})
        return 200, {"target_kind": kind, "target_code": code, "available": row.available}

    return run_command(request, branch_id, "availability.set", data, handler)
