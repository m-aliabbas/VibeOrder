from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.http import JsonResponse
from django.utils import timezone
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .auth import device_from_request
from .errors import DomainError, Forbidden, NotFound
from .models import Branch, Device, Membership, PairingCode, Staff, StaffSession, Station, new_token
from .scope import branch_for, require


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def pair_device(request):
    """Exchange a short-lived single-use pairing code for a device token."""
    code = str(request.data.get("code", "")).strip()
    name = str(request.data.get("name", "Device"))[:80]
    with transaction.atomic():
        pc = (
            PairingCode.objects.select_for_update()
            .select_related("branch")
            .filter(code=code, used_at__isnull=True, expires_at__gt=timezone.now())
            .first()
        )
        if pc is None:
            raise DomainError("Pairing code invalid or expired", code="invalid_pairing_code", http_status=400)
        raw = new_token()
        device = Device.objects.create(branch=pc.branch, name=name, kind=pc.kind, token_hash=Device.hash_token(raw))
        pc.used_at = timezone.now()
        pc.save(update_fields=["used_at"])
    return Response(
        {"device_id": str(device.pk), "device_token": raw, "branch_id": str(pc.branch.pk),
         "branch_name": pc.branch.name, "kind": device.kind},
        status=201,
    )


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def staff_directory(request, branch_id):
    device = device_from_request(request)
    if device is None or str(device.branch_id) != str(branch_id):
        raise NotFound("Branch not found")
    rows = (
        Membership.objects.filter(branch=device.branch, is_active=True, staff__is_active=True)
        .select_related("staff").order_by("staff__display_name")
    )
    staff = {}
    for m in rows:
        s = staff.setdefault(m.staff.pk, {"id": str(m.staff.pk), "username": m.staff.username,
                                          "display_name": m.staff.display_name, "roles": []})
        s["roles"].append(m.role)
    return Response({"staff": list(staff.values()), "device": {"id": str(device.pk), "name": device.name, "kind": device.kind}})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def login(request, branch_id):
    device = device_from_request(request)
    if device is None or str(device.branch_id) != str(branch_id):
        raise NotFound("Branch not found")
    username = str(request.data.get("username", ""))
    pin = str(request.data.get("pin", ""))
    with transaction.atomic():
        staff = Staff.objects.select_for_update().filter(tenant=device.branch.tenant, username=username, is_active=True).first()
        if staff is None or not staff.memberships.filter(branch=device.branch, is_active=True).exists():
            raise DomainError("Unknown staff or PIN", code="invalid_credentials", http_status=401)
        now = timezone.now()
        if staff.locked_until and staff.locked_until > now:
            raise DomainError("Too many attempts. Try again later.", code="locked", http_status=429)
        if not staff.check_pin(pin):
            staff.failed_attempts += 1
            if staff.failed_attempts >= settings.PIN_MAX_ATTEMPTS:
                staff.locked_until = now + timedelta(seconds=settings.PIN_LOCKOUT_SECONDS)
                staff.failed_attempts = 0
            staff.save(update_fields=["failed_attempts", "locked_until"])
            # Returning (not raising) commits the attempt counter.
            return Response({"code": "invalid_credentials", "message": "Unknown staff or PIN", "field_errors": {},
                             "current_revision": None, "correlation_id": request.correlation_id}, status=401)
        staff.failed_attempts = 0
        staff.locked_until = None
        staff.save(update_fields=["failed_attempts", "locked_until"])
        raw = new_token()
        session = StaffSession.objects.create(
            staff=staff, device=device, branch=device.branch, token_hash=Device.hash_token(raw),
            expires_at=now + timedelta(hours=settings.STAFF_SESSION_TTL_HOURS),
        )
    return Response({"session_token": raw, "expires_at": session.expires_at, "staff_id": str(staff.pk),
                     "display_name": staff.display_name}, status=201)


@api_view(["POST"])
def logout(request, branch_id):
    branch_for(request, branch_id)
    request.auth.revoked_at = timezone.now()
    request.auth.save(update_fields=["revoked_at"])
    return Response({"ok": True})


@api_view(["GET"])
def bootstrap(request, branch_id):
    from apps.cash.models import DrawerShift

    branch = branch_for(request, branch_id)
    p = request.user
    shift = DrawerShift.objects.filter(branch=branch).exclude(status="closed").order_by("-opened_at").first()
    release = branch.active_release
    return Response({
        "server_time": timezone.now(),
        "branch": {"id": str(branch.pk), "code": branch.code, "name": branch.name, "currency": branch.currency,
                   "timezone": branch.timezone, "business_date": branch.business_date()},
        "staff": {"id": str(p.staff.pk), "display_name": p.staff.display_name, "username": p.staff.username},
        "device": {"id": str(p.device.pk), "name": p.device.name, "kind": p.device.kind},
        "roles": sorted(p.roles),
        "permissions": sorted(p.permissions),
        "stations": [{"id": str(s.pk), "code": s.code, "name": s.name, "is_packing": s.is_packing}
                     for s in branch.stations.order_by("code")],
        "menu_release": {"id": str(release.pk), "version": release.version, "name": release.name} if release else None,
        "active_shift": {"id": str(shift.pk), "status": shift.status, "drawer_code": shift.drawer_code,
                         "revision": shift.revision} if shift else None,
    })


@api_view(["POST"])
def create_pairing_code(request, branch_id):
    branch = branch_for(request, branch_id)
    require(request, "device.pair")
    kind = request.data.get("kind", "tablet")
    if kind not in ("tablet", "cashier", "kds"):
        raise DomainError("Unknown device kind", field_errors={"kind": "invalid"})
    pc = PairingCode.objects.create(
        branch=branch, code=PairingCode.new_code(), kind=kind, created_by=request.user.staff,
        expires_at=timezone.now() + timedelta(seconds=settings.PAIRING_CODE_TTL_SECONDS),
    )
    return Response({"code": pc.code, "expires_at": pc.expires_at, "kind": kind}, status=201)


@api_view(["POST"])
def revoke_device(request, branch_id, device_id):
    branch = branch_for(request, branch_id)
    require(request, "device.revoke")
    device = Device.objects.filter(branch=branch, pk=device_id).first()
    if device is None:
        raise NotFound("Device not found")
    now = timezone.now()
    device.revoked_at = now
    device.save(update_fields=["revoked_at"])
    StaffSession.objects.filter(device=device, revoked_at__isnull=True).update(revoked_at=now)
    return Response({"id": str(device.pk), "revoked_at": now})


def healthz(request):
    return JsonResponse({"status": "ok", "profile": settings.PROFILE})


def readyz(request):
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return JsonResponse({"status": "ready", "database": "ok"})
    except Exception as exc:  # pragma: no cover
        return JsonResponse({"status": "unavailable", "database": str(exc)[:200]}, status=503)


@api_view(["GET"])
def recovery(request, branch_id):
    """Recovery reconciliation: what to compare with physical records before resuming (A19)."""
    from apps.ops.services import reconciliation
    branch = branch_for(request, branch_id)
    require(request, "recovery.view")
    return Response(reconciliation(branch))
