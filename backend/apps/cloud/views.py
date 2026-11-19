"""Cloud-hosted endpoints: branch sync, public storefront + tracking, provider webhook, owner portal API."""
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.core.errors import DomainError, Forbidden, NotFound
from apps.core.models import Branch, Device, Membership, Staff, new_token

from . import services
from .models import CloudBranch, DailyProjection, OwnerSession

PUBLIC = (authentication_classes([]), permission_classes([AllowAny]))


def public(fn):
    return authentication_classes([])(permission_classes([AllowAny])(fn))


# ---------------------------------------------------------------- branch sync (branch credential)

def _cloud_branch(request):
    h = request.headers.get("Authorization", "")
    if not h.startswith("Branch "):
        raise DomainError("Branch credential required", code="unauthenticated", http_status=401)
    cb = CloudBranch.objects.select_related("branch").filter(token_hash=Device.hash_token(h[7:].strip())).first()
    if cb is None or cb.revoked_at is not None:
        raise DomainError("Branch credential revoked or unknown", code="unauthenticated", http_status=401)
    return cb


@api_view(["POST"])
@public
def sync_events(request):
    cb = _cloud_branch(request)
    events = request.data.get("events") or []
    if not isinstance(events, list):
        raise DomainError("events must be a list", field_errors={"events": "invalid"})
    return Response(services.ingest(cb, events))


@api_view(["GET"])
@public
def sync_inbox(request):
    return Response(services.inbox(_cloud_branch(request)))


@api_view(["POST"])
@public
def sync_ack(request):
    return Response(services.ack(_cloud_branch(request), request.data))


# ---------------------------------------------------------------- storefront (public)

@api_view(["GET"])
@public
def store(request, slug):
    _, data = services.store_branches(slug)
    return Response(data)


@api_view(["GET"])
@public
def store_menu(request, slug, code):
    return Response(services.store_menu(slug, code))


@api_view(["POST"])
@public
def store_quote(request, slug, code):
    return Response(services.store_quote(slug, code, request.data))


@api_view(["POST"])
@public
def store_order(request, slug, code):
    key = request.headers.get("Idempotency-Key")
    try:
        key = uuid.UUID(key) if key else None
    except ValueError:
        raise DomainError("Idempotency-Key must be a UUID", code="missing_idempotency_key", http_status=400)
    status, body = services.place_order(slug, code, request.data, key)
    return Response(body, status=status)


@api_view(["GET"])
@public
def track(request, token):
    return Response(services.track(token))


@api_view(["POST"])
@public
def fakepay_webhook(request):
    from .payments import webhook
    return Response(webhook(request.body, request.headers.get("X-Signature", "")))


@api_view(["POST"])
@public
def fakepay_simulate(request):
    """Dev-only stand-in for the provider's hosted page: signs and delivers a callback."""
    import json

    from .payments import sign, webhook
    from .models import ProviderPayment
    if not settings.DEBUG:
        raise NotFound("Not found")
    pay = ProviderPayment.objects.filter(provider_ref=request.data.get("provider_ref")).first()
    if pay is None:
        raise NotFound("Payment not found")
    body = json.dumps({"callback_id": f"cb_{uuid.uuid4().hex[:12]}", "provider_ref": pay.provider_ref,
                       "status": request.data.get("status", "authorized"), "amount_minor": pay.amount_minor,
                       "currency": pay.currency}).encode()
    return Response(webhook(body, sign(body)))


def storefront_page(request, slug, code=None):
    return render(request, "store.html", {"slug": slug, "code": code or ""})


def track_page(request, token):
    return render(request, "track.html", {"token": token})


def owner_page(request):
    return render(request, "owner.html", {})


# ---------------------------------------------------------------- owner portal API

class Owner:
    def __init__(self, staff):
        self.staff = staff
        self.tenant = staff.tenant
        owner_branches = Membership.objects.filter(staff=staff, role="owner", is_active=True).values_list("branch_id",
                                                                                                          flat=True)
        mgr = Membership.objects.filter(staff=staff, role="manager", is_active=True).values_list("branch_id", flat=True)
        self.branch_ids = set(owner_branches) | set(mgr)
        self.is_owner = bool(set(owner_branches))

    def branches(self, ids=None):
        qs = Branch.objects.filter(tenant=self.tenant, pk__in=self.branch_ids).order_by("code")
        if ids:
            wanted = {str(i) for i in ids}
            allowed = [b for b in qs if str(b.pk) in wanted or b.code in wanted]
            if len(allowed) != len(wanted):
                raise NotFound("Branch not found")  # outside scope looks like absence
            return allowed
        return list(qs)


def _owner(request):
    h = request.headers.get("Authorization", "")
    if not h.startswith("Owner "):
        raise DomainError("Sign in", code="unauthenticated", http_status=401)
    s = OwnerSession.objects.select_related("staff__tenant").filter(token_hash=Device.hash_token(h[6:].strip())).first()
    if s is None or s.revoked_at or s.expires_at < timezone.now() or not s.staff.is_active:
        raise DomainError("Session expired", code="unauthenticated", http_status=401)
    o = Owner(s.staff)
    if not o.branch_ids:
        raise Forbidden("No owner or manager access", code="permission_denied")
    return o


@api_view(["POST"])
@public
def owner_login(request):
    from apps.core.models import Tenant
    tenant = Tenant.objects.filter(slug=request.data.get("tenant", "")).first()
    with transaction.atomic():
        staff = Staff.objects.select_for_update().filter(tenant=tenant, username=request.data.get("username", ""),
                                                         is_active=True).first() if tenant else None
        now = timezone.now()
        if staff and staff.locked_until and staff.locked_until > now:
            raise DomainError("Too many attempts. Try again later.", code="locked", http_status=429)
        if staff is None or not staff.check_pin(str(request.data.get("pin", ""))):
            if staff:
                staff.failed_attempts += 1
                if staff.failed_attempts >= settings.PIN_MAX_ATTEMPTS:
                    staff.locked_until = now + timedelta(seconds=settings.PIN_LOCKOUT_SECONDS)
                    staff.failed_attempts = 0
                staff.save(update_fields=["failed_attempts", "locked_until"])
            return Response({"code": "invalid_credentials", "message": "Unknown user or PIN", "field_errors": {}},
                            status=401)
        staff.failed_attempts = 0
        staff.save(update_fields=["failed_attempts"])
        if not Owner(staff).branch_ids:
            raise Forbidden("No owner or manager access", code="permission_denied")
        raw = new_token()
        OwnerSession.objects.create(staff=staff, token_hash=Device.hash_token(raw),
                                    expires_at=now + timedelta(hours=settings.OWNER_SESSION_TTL_HOURS))
    return Response({"token": raw, "name": staff.display_name, "tenant": tenant.name}, status=201)


@api_view(["GET"])
@public
def owner_overview(request):
    from apps.core.models import SyncOutbox
    o = _owner(request)
    today = timezone.now().date()
    out = []
    for b in o.branches():
        cb = getattr(b, "cloud", None)
        proj = DailyProjection.objects.filter(branch=b, business_date=b.business_date()).first()
        act = b.activations.order_by("-activated_at").first()
        stale = cb is None or cb.heartbeat_at is None or \
            (timezone.now() - cb.heartbeat_at).total_seconds() > settings.HEARTBEAT_STALE_SECONDS
        out.append({
            "id": str(b.pk), "code": b.code, "name": b.name, "business_date": b.business_date(),
            "release": {"id": str(b.active_release_id), "version": b.active_release.version,
                        "name": b.active_release.name} if b.active_release_id else None,
            "release_activated_at": act.activated_at if act else None,
            "last_sync_at": cb.heartbeat_at if cb else None, "stale": stale,
            "synced_position": cb.acked_position if cb else 0,
            "branch_position": b.stream_position,
            "unsynced_events": SyncOutbox.objects.filter(branch=b, acked_at__isnull=True).count(),
            "online_paused": b.online_paused, "accepting_online": services.accepting(b)[0],
            "today": {"sales_minor": proj.sales_minor if proj else 0, "checks": proj.checks_closed if proj else 0,
                      "tenders": proj.tenders if proj else {}, "refunds_minor": proj.refunds_minor if proj else 0,
                      "voids": proj.voids if proj else 0, "web_orders": proj.web_orders if proj else 0,
                      "as_of_position": proj.last_position if proj else 0},
        })
    return Response({"owner": o.staff.display_name, "tenant": o.tenant.name, "branches": out, "date": today,
                     "server_time": timezone.now()})


def _require_owner(o):
    if not o.is_owner:
        raise Forbidden("Only the owner can change menus", code="permission_denied")


@api_view(["GET", "POST"])
@public
def owner_releases(request):
    from apps.catalog.authoring import clone_release, get_release
    from apps.catalog.models import MenuRelease
    o = _owner(request)
    if request.method == "GET":
        active = {}
        for b in o.branches():
            active.setdefault(str(b.active_release_id), []).append(b.code)
        return Response({"releases": [{"id": str(r.pk), "version": r.version, "name": r.name, "status": r.status,
                                       "published_at": r.published_at, "active_at": active.get(str(r.pk), [])}
                                      for r in MenuRelease.objects.filter(tenant=o.tenant).order_by("-version")]})
    _require_owner(o)
    base = get_release(o.tenant, request.data.get("base_release_id"))
    r = clone_release(o.tenant, base, request.data.get("name", ""), request.data.get("price_changes") or [],
                      request.data.get("mixed_policy"), o.staff)
    return Response({"id": str(r.pk), "version": r.version, "status": r.status, "name": r.name}, status=201)


@api_view(["POST"])
@public
def owner_preview(request, release_id):
    from apps.catalog.authoring import get_release, preview
    o = _owner(request)
    r = get_release(o.tenant, release_id)
    b = o.branches([request.data["branch_id"]])[0] if request.data.get("branch_id") else None
    return Response(preview(r, b, request.data.get("items") or []))


@api_view(["POST"])
@public
def owner_publish(request, release_id):
    from apps.catalog.authoring import get_release, publish
    o = _owner(request)
    _require_owner(o)
    r = get_release(o.tenant, release_id)
    return Response({"activations": publish(r, o.branches(request.data.get("branch_ids") or []), o.staff)})


@api_view(["POST"])
@public
def owner_intake(request, branch_id):
    o = _owner(request)
    b = o.branches([branch_id])[0]
    paused = bool(request.data.get("paused"))
    Branch.objects.filter(pk=b.pk).update(online_paused=paused, updated_at=timezone.now())
    from apps.core.models import AuditEntry
    AuditEntry.objects.create(branch=b, staff=o.staff, action="online.pause" if paused else "online.resume",
                              aggregate_type="Branch", aggregate_id=b.pk, reason=str(request.data.get("reason", ""))[:200])
    return Response({"branch": b.code, "online_paused": paused})


@api_view(["GET"])
@public
def owner_report(request, name):
    from apps.reports import services as reports
    o = _owner(request)
    ids = [x for x in request.query_params.get("branches", "").split(",") if x]
    branches = o.branches(ids or None)
    d_from, d_to = reports.parse_range(request.query_params)
    if request.query_params.get("export") == "csv":
        resp = HttpResponse(reports.as_csv(name, branches, d_from, d_to, o.tenant), content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="{name}-{d_from}-{d_to}.csv"'
        return resp
    return Response(reports.as_json(name, branches, d_from, d_to, o.tenant))
