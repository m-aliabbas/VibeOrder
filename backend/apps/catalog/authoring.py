"""Menu authoring: clone a release into a new draft version, change prices, preview, publish to selected branches.

Published releases are immutable (lines snapshot the release they were priced under), so every change is a new
version. Publishing activates the release only at the chosen branches; others keep their current release (A20).
"""
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.core.errors import Conflict, DomainError, NotFound

from .models import (Category, DealSlot, DealSlotChoice, MenuActivation, MenuRelease, Option, OptionGroup,
                     OptionPrice, Product, Variant)


@transaction.atomic
def clone_release(tenant, base, name, price_changes=(), mixed_policy=None, staff=None):
    version = (MenuRelease.objects.filter(tenant=tenant).aggregate(v=Max("version"))["v"] or 0) + 1
    new = MenuRelease.objects.create(tenant=tenant, version=version, name=name or f"Menu {version}", status="draft",
                                     mixed_policy=mixed_policy or base.mixed_policy, tax_rate_bp=base.tax_rate_bp,
                                     tax_inclusive=base.tax_inclusive)
    cats = {c.pk: Category.objects.create(release=new, code=c.code, name=c.name, sort=c.sort)
            for c in base.categories.all()}
    groups, options = {}, {}
    for g in base.option_groups.all():
        ng = OptionGroup.objects.create(release=new, code=g.code, name=g.name, kind=g.kind, min_select=g.min_select,
                                        max_select=g.max_select, sides_allowed=g.sides_allowed, sort=g.sort)
        groups[g.pk] = ng
        for o in g.options.all():
            no = Option.objects.create(group=ng, code=o.code, name=o.name, is_default=o.is_default,
                                       excludes=o.excludes, sort=o.sort)
            options[o.pk] = no
            for p in o.prices.all():
                OptionPrice.objects.create(option=no, size_code=p.size_code, whole_minor=p.whole_minor,
                                           half_minor=p.half_minor)
    products = {}
    changes = {(c["product"], c["variant"]): int(c["price_minor"]) for c in price_changes}
    applied = set()
    for p in base.products.all():
        np_ = Product.objects.create(release=new, category=cats[p.category_id], code=p.code, name=p.name,
                                     name_local=p.name_local, kind=p.kind, station_code=p.station_code,
                                     description=p.description, sort=p.sort)
        np_.option_groups.set([groups[g.pk] for g in p.option_groups.all()])
        products[p.pk] = np_
        for v in p.variants.all():
            price = changes.get((p.code, v.code), v.price_minor)
            if (p.code, v.code) in changes:
                applied.add((p.code, v.code))
            Variant.objects.create(product=np_, code=v.code, name=v.name, price_minor=price, sort=v.sort)
    unknown = set(changes) - applied
    if unknown:
        raise DomainError(f"Unknown product/variant {sorted(unknown)[0]}", field_errors={"price_changes": "unknown"})
    for s in DealSlot.objects.filter(deal__release=base):
        ns = DealSlot.objects.create(deal=products[s.deal_id], code=s.code, name=s.name, quantity=s.quantity,
                                     sort=s.sort)
        for c in s.choices.all():
            DealSlotChoice.objects.create(slot=ns, product=products[c.product_id], variant_code=c.variant_code,
                                          upgrade_minor=c.upgrade_minor)
    return new


def preview(release, branch, items):
    from apps.catalog.models import BranchAvailability
    from apps.catalog.pricing import Menu, Pricer
    lines, totals = Pricer(Menu.load(release), BranchAvailability.unavailable_codes(branch) if branch else None).price(items)
    return {"release": {"id": str(release.pk), "version": release.version, "status": release.status},
            "lines": [ln.as_dict() for ln in lines], **{k: v for k, v in totals.items() if k != "per_line"}}


@transaction.atomic
def publish(release, branches, staff):
    from apps.core.models import AuditEntry, Branch
    if not branches:
        raise DomainError("Choose at least one branch", field_errors={"branch_ids": "required"})
    release = MenuRelease.objects.select_for_update().get(pk=release.pk)
    if release.status == "draft":
        release.status = "published"
        release.published_at = timezone.now()
        release.revision += 1
        release.save()
    out = []
    for b in Branch.objects.select_for_update().filter(pk__in=[b.pk for b in branches]).order_by("pk"):
        if b.active_release_id == release.pk:
            out.append({"branch": b.code, "release": release.version, "changed": False})
            continue
        prev = b.active_release
        MenuActivation.objects.create(branch=b, release=release, previous=prev, published_by=staff)
        Branch.objects.filter(pk=b.pk).update(active_release=release, updated_at=timezone.now())
        AuditEntry.objects.create(branch=b, staff=staff, action="menu.activate", aggregate_type="Branch",
                                  aggregate_id=b.pk, data={"release": release.version,
                                                           "previous": prev.version if prev else None})
        out.append({"branch": b.code, "release": release.version, "changed": True})
    return out


def lock_published(release):
    if release.status != "draft":
        raise Conflict("Published releases are immutable; clone a new version", code="release_published")
    return release


def get_release(tenant, release_id):
    r = MenuRelease.objects.filter(tenant=tenant, pk=release_id).first()
    if r is None:
        raise NotFound("Release not found")
    return r
