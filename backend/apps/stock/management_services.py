"""Stock item master, per-branch reorder levels and versioned recipe management.

Recipes are never edited in place: publishing creates version N+1 and retires N, so historical consumption keeps
pointing at the recipe it actually used (``StockMovement.recipe`` is PROTECT). Recipe cost uses the branch's
moving average cost per base unit; components without a cost are flagged, never guessed.
"""
from decimal import Decimal

from apps.core.errors import Conflict, DomainError, NotFound, StaleRevision

from .models import Recipe, RecipeComponent, StockItem, StockLevel, StockMovement
from .services import _item, _item_row, _level, _qty

UNITS = {u for u, _ in StockItem.UNITS}


def _dec(v, field, allow_zero=True, allow_none=False):
    if v is None or v == "":
        if allow_none:
            return None
        raise DomainError(f"{field} is required", field_errors={field: "required"})
    try:
        d = Decimal(str(v))
    except Exception:
        raise DomainError(f"{field} must be a number", field_errors={field: "invalid"})
    if d < 0 or (d == 0 and not allow_zero):
        raise DomainError(f"{field} must be positive", field_errors={field: "too_small"})
    return d


# ---------------------------------------------------------------- item master

def save_item(ctx, data, item_id=None):
    """Create a stock item or edit one. The base unit is fixed once movements exist (ledger quantities are stored
    in it). ``reorder_level`` is per branch: on hand at or below it raises a low-stock alert."""
    tenant = ctx.branch.tenant
    if item_id:
        item = StockItem.objects.select_for_update().filter(tenant=tenant, pk=item_id).first()
        if item is None:
            raise NotFound("Stock item not found")
        if data.get("expected_revision") is not None and int(data["expected_revision"]) != item.revision:
            raise StaleRevision("Item was changed on another device", current_revision=item.revision)
        prev = item.revision
        unit = data.get("base_unit", item.base_unit)
        if unit != item.base_unit:
            if unit not in UNITS:
                raise DomainError("Base unit must be g, ml or pcs", field_errors={"base_unit": "invalid"})
            if StockMovement.objects.filter(item=item).exists():
                raise Conflict("Base unit cannot change once stock has moved", code="unit_locked")
            item.base_unit = unit
        item.revision += 1
    else:
        code = str(data.get("code", "")).strip().lower().replace(" ", "-")[:40]
        if not code:
            raise DomainError("Code is required", field_errors={"code": "required"})
        if StockItem.objects.filter(tenant=tenant, code=code).exists():
            raise Conflict(f"Stock item {code} already exists", code="duplicate_code",
                           field_errors={"code": "duplicate"})
        unit = data.get("base_unit")
        if unit not in UNITS:
            raise DomainError("Base unit must be g, ml or pcs", field_errors={"base_unit": "invalid"})
        item, prev = StockItem(tenant=tenant, code=code, base_unit=unit), None
    if "name" in data or not item_id:
        name = str(data.get("name", "")).strip()
        if not name:
            raise DomainError("Name is required", field_errors={"name": "required"})
        item.name = name[:80]
    if "purchase_unit" in data:
        item.purchase_unit = str(data["purchase_unit"] or "")[:20]
    if "purchase_factor" in data or not item_id:
        item.purchase_factor = _dec(data.get("purchase_factor", 1), "purchase_factor", allow_zero=False)
    if "active" in data:
        item.is_active = bool(data["active"])
    item.save()
    lvl = _level(ctx.branch, item)
    if "reorder_level" in data or "par_level" in data:
        if "reorder_level" in data:
            lvl.reorder_level = _dec(data["reorder_level"], "reorder_level", allow_none=True)
        if "par_level" in data:
            lvl.par_level = _dec(data["par_level"], "par_level", allow_none=True)
        if lvl.par_level is not None and lvl.reorder_level is not None and lvl.par_level < lvl.reorder_level:
            raise DomainError("Order-up-to level must be at least the reorder level",
                              field_errors={"par_level": "too_small"})
        lvl.save(update_fields=["reorder_level", "par_level", "updated_at"])
    ctx.audit("stock.item_saved", item, prev, item.revision,
              data={k: str(v) for k, v in data.items() if k not in ("approval", "expected_revision")})
    return (200 if item_id else 201), {"item": _item_row(item, lvl)}


# ---------------------------------------------------------------- recipes

def _avg_costs(branch):
    return {lv.item_id: lv.avg_cost_minor for lv in StockLevel.objects.filter(branch=branch)}


def _recipe_row(r, costs):
    comps, cost, missing = [], Decimal(0), False
    for c in r.components.all():
        unit_cost = costs.get(c.item_id)
        line = None if unit_cost is None else c.quantity * unit_cost
        if line is None:
            missing = True
        else:
            cost += line
        comps.append({"item": c.item.code, "name": c.item.name, "unit": c.item.base_unit, "quantity": c.quantity,
                      "cost_minor": None if line is None else int(line.quantize(Decimal(1)))})
    return {"id": str(r.pk), "target_kind": r.target_kind, "target_code": r.target_code, "size_code": r.size_code,
            "version": r.version, "active": r.is_active, "note": r.note,
            "created_by": r.created_by.display_name if r.created_by_id else None, "created_at": r.created_at,
            "components": comps, "cost_minor": int(cost.quantize(Decimal(1))), "cost_missing": missing}


def _menu(branch):
    from apps.orders.services import menu_for
    return menu_for(branch.active_release) if branch.active_release is not None else None


def recipe_targets(branch):
    """Everything a recipe can be written for on this branch's menu, with sizes. ``required`` marks targets the
    menu cannot be costed without: simple products/pizza flavours per size and the pizza base per size. Deals and
    mixed pizzas consume their parts' recipes. Options and packaging are optional (a crust may use none)."""
    menu = _menu(branch)
    if menu is None:
        return []
    pizza_sizes = {}
    out = []
    for c, p in menu.products.items():
        if p["kind"] in ("deal", "mixed_pizza"):
            continue
        sizes = [{"code": v["code"], "name": v["name"]} for v in p["variants"].values()] or [{"code": "", "name": ""}]
        if p["kind"] == "pizza":
            pizza_sizes.update({s["code"]: s["name"] for s in sizes})
        out.append({"target_kind": "product", "target_code": c, "label": p["name"], "sizes": sizes,
                    "required": True})
    sizes = [{"code": k, "name": v} for k, v in pizza_sizes.items()]
    if sizes:
        out.insert(0, {"target_kind": "base", "target_code": "pizza", "label": "Pizza base (dough + cheese)",
                       "sizes": sizes, "required": True})
        out.append({"target_kind": "packaging", "target_code": "pizza", "label": "Pizza box", "sizes": sizes,
                    "required": False})
    for c, o in menu.options.items():
        sz = [{"code": s, "name": pizza_sizes.get(s, s)} for s in o["prices"] if s] or [{"code": "", "name": ""}]
        out.append({"target_kind": "option", "target_code": c, "label": o["name"], "sizes": sz, "required": False})
    return out


def missing_recipes(branch, active=None):
    if active is None:
        active = set(Recipe.objects.filter(tenant=branch.tenant, is_active=True)
                     .values_list("target_kind", "target_code", "size_code"))
    out = []
    for t in recipe_targets(branch):
        if not t["required"]:
            continue
        for s in t["sizes"]:
            key = (t["target_kind"], t["target_code"])
            if key + (s["code"],) in active or key + ("",) in active:
                continue
            out.append({"target_kind": t["target_kind"], "target_code": t["target_code"], "size_code": s["code"],
                        "label": t["label"] + (f" · {s['name']}" if s["name"] else "")})
    return out


def recipes(branch, include_history=False):
    tenant = branch.tenant
    qs = Recipe.objects.filter(tenant=tenant).select_related("created_by").prefetch_related("components__item")
    if not include_history:
        qs = qs.filter(is_active=True)
    costs = _avg_costs(branch)
    targets = recipe_targets(branch)
    labels = {(t["target_kind"], t["target_code"]): t["label"] for t in targets}
    rows = []
    for r in qs.order_by("target_kind", "target_code", "size_code", "-version"):
        row = _recipe_row(r, costs)
        row["label"] = labels.get((r.target_kind, r.target_code), r.target_code)
        rows.append(row)
    active = {(r["target_kind"], r["target_code"], r["size_code"]) for r in rows if r["active"]}
    return {"recipes": rows, "missing": missing_recipes(branch, active), "targets": targets,
            "items": [{"code": i.code, "name": i.name, "unit": i.base_unit}
                      for i in StockItem.objects.filter(tenant=tenant, is_active=True).order_by("name")]}


def recipe_history(branch, kind, code, size):
    costs = _avg_costs(branch)
    qs = (Recipe.objects.filter(tenant=branch.tenant, target_kind=kind, target_code=code, size_code=size or "")
          .select_related("created_by").prefetch_related("components__item").order_by("-version"))
    return {"versions": [_recipe_row(r, costs) for r in qs]}


def publish_recipe(ctx, data):
    """Publish a new recipe version for (kind, code, size); empty components retire the active version."""
    kind = data.get("target_kind")
    if kind not in dict(Recipe.TARGETS):
        raise DomainError("Unknown recipe target", field_errors={"target_kind": "invalid"})
    code = str(data.get("target_code", "")).strip()
    if not code:
        raise DomainError("Target code is required", field_errors={"target_code": "required"})
    size = str(data.get("size_code") or "").strip()
    tenant = ctx.branch.tenant
    versions = list(Recipe.objects.select_for_update().filter(tenant=tenant, target_kind=kind, target_code=code,
                                                              size_code=size).order_by("-version"))
    current = next((r for r in versions if r.is_active), None)
    expected = data.get("expected_version")
    if expected is not None and (current.version if current else 0) != int(expected):
        raise StaleRevision("Recipe was changed on another device",
                            current_revision=current.version if current else 0)
    comps, seen = [], set()
    for i, c in enumerate(data.get("components") or []):
        item = _item(ctx.branch, c.get("item"))
        if item.pk in seen:
            raise DomainError(f"{item.name} is listed twice", field_errors={f"components[{i}].item": "duplicate"})
        seen.add(item.pk)
        comps.append((item, _qty(c.get("quantity"), f"components[{i}].quantity")))
    if not comps and current is None:
        raise DomainError("Add at least one ingredient", field_errors={"components": "empty"})
    if current:
        current.is_active = False
        current.revision += 1
        current.save(update_fields=["is_active", "revision", "updated_at"])
    note = str(data.get("note", "")).strip()[:200]
    if not comps:
        ctx.audit("stock.recipe_retired", current, current.version, None, reason=note)
        return 200, {"retired": True, "recipe": None}
    r = Recipe.objects.create(tenant=tenant, target_kind=kind, target_code=code, size_code=size,
                              version=(versions[0].version + 1) if versions else 1, note=note, created_by=ctx.staff)
    RecipeComponent.objects.bulk_create([RecipeComponent(recipe=r, item=it, quantity=q) for it, q in comps])
    ctx.audit("stock.recipe_published", r, current.version if current else None, r.version, reason=note,
              data={"target": f"{kind}:{code}:{size}", "components": {it.code: str(q) for it, q in comps}})
    r = Recipe.objects.prefetch_related("components__item").select_related("created_by").get(pk=r.pk)
    return 201, {"retired": False, "recipe": _recipe_row(r, _avg_costs(ctx.branch))}


def dependents(branch):
    """stock item id -> {"products": {code}, "options": {code}} via active recipes and the branch's menu."""
    menu = _menu(branch)
    pizzas = {c for c, p in menu.products.items() if p["kind"] == "pizza"} if menu else set()
    out = {}
    for r in Recipe.objects.filter(tenant=branch.tenant, is_active=True).prefetch_related("components"):
        for c in r.components.all():
            d = out.setdefault(c.item_id, {"products": set(), "options": set()})
            if r.target_kind == "product":
                d["products"].add(r.target_code)
            elif r.target_kind == "base":
                d["products"].update(pizzas)
            elif r.target_kind == "option":
                d["options"].add(r.target_code)
    return out
