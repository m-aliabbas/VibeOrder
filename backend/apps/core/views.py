from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.http import JsonResponse
from django.utils import timezone
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .errors import DomainError, Forbidden, NotFound
from .models import Branch, Device, Membership, Staff, StaffSession, Station, new_token
from .scope import branch_for, require


KINDS = ("tablet", "cashier", "kds", "office")


def _login_response(staff, branch, device, raw_session, expires_at, device_key):
    roles = sorted(staff.memberships.filter(branch=branch, is_active=True).values_list("role", flat=True))
    return {"session_token": raw_session, "expires_at": expires_at, "staff_id": str(staff.pk),
            "display_name": staff.display_name, "username": staff.username, "roles": roles,
            "branch_id": str(branch.pk), "branch_name": branch.name, "branch_code": branch.code,
            "device_id": str(device.pk), "device_key": device_key}


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def login(request):
    """Username + password sign in. No pairing: the device registers itself with a random key it keeps."""
    from .auth import InvalidLogin, verify_password

    d = request.data
    try:
        staff = verify_password(d.get("username", ""), d.get("password", ""))
    except InvalidLogin:
        return Response({"code": "invalid_credentials", "message": "Wrong username or password", "field_errors": {},
                         "current_revision": None, "correlation_id": request.correlation_id}, status=401)
    branches = list(Branch.objects.filter(memberships__staff=staff, memberships__is_active=True).distinct()
                    .order_by("code"))
    if not branches:
        raise Forbidden("Your account has no branch yet. Ask a manager.", code="no_branch")
    wanted = str(d.get("branch") or settings.LOCAL_BRANCH or "").strip()
    branch = None
    if wanted:
        branch = next((b for b in branches if wanted in (str(b.pk), b.code)), None)
    if branch is None:
        if len(branches) == 1:
            branch = branches[0]
        else:
            return Response({"code": "choose_branch", "message": "Choose a branch",
                             "branches": [{"id": str(b.pk), "code": b.code, "name": b.name} for b in branches],
                             "field_errors": {}, "correlation_id": request.correlation_id}, status=409)
    kind = d.get("device_kind") if d.get("device_kind") in KINDS else "tablet"
    name = str(d.get("device_name") or "Device").strip()[:80] or "Device"
    raw_key = str(d.get("device_key") or "").strip()
    now = timezone.now()
    with transaction.atomic():
        device = Device.objects.filter(token_hash=Device.hash_token(raw_key)).first() if raw_key else None
        if device is not None and device.revoked_at is not None:
            raise Forbidden("This device was blocked by a manager.", code="device_blocked")
        if device is None or device.branch_id != branch.pk:
            raw_key = new_token()
            device = Device.objects.create(branch=branch, name=name, kind=kind, token_hash=Device.hash_token(raw_key))
        else:
            Device.objects.filter(pk=device.pk).update(name=name, kind=kind, last_seen_at=now)
        raw = new_token()
        session = StaffSession.objects.create(
            staff=staff, device=device, branch=branch, token_hash=Device.hash_token(raw),
            expires_at=now + timedelta(hours=settings.STAFF_SESSION_TTL_HOURS),
        )
    return Response(_login_response(staff, branch, device, raw, session.expires_at, raw_key), status=201)


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


# ---------------------------------------------------------------- staff accounts (manager)

MIN_PASSWORD = 6


def _staff_row(staff, branch):
    roles = sorted(m.role for m in staff.memberships.all() if m.branch_id == branch.pk and m.is_active)
    last = staff.sessions.filter(branch=branch).order_by("-created_at").values_list("created_at", flat=True).first()
    return {"id": str(staff.pk), "username": staff.username, "display_name": staff.display_name,
            "roles": roles, "is_active": staff.is_active, "revision": staff_revision(staff),
            "last_login_at": last, "locked": bool(staff.locked_until and staff.locked_until > timezone.now())}


def staff_revision(staff):
    return int(staff.updated_at.timestamp() * 1000) if getattr(staff, "updated_at", None) else 0


def _clean_roles(request, roles):
    from .models import Role
    valid = {c for c, _ in Role.choices}
    roles = sorted(set(roles or []))
    bad = [r for r in roles if r not in valid]
    if bad or not roles:
        raise DomainError("Pick at least one valid role", field_errors={"roles": "invalid"})
    if "owner" in roles and "owner" not in request.user.roles:
        raise Forbidden("Only an owner can create another owner", code="permission_denied")
    return roles


def _check_password_rules(pw, field="password"):
    if len(str(pw or "")) < MIN_PASSWORD:
        raise DomainError(f"Password must be at least {MIN_PASSWORD} characters", field_errors={field: "too_short"})


def _set_roles(staff, branch, roles):
    current = {m.role: m for m in Membership.objects.select_for_update().filter(staff=staff, branch=branch)}
    for role, m in current.items():
        active = role in roles
        if m.is_active != active:
            m.is_active = active
            m.save(update_fields=["is_active"])
    for role in roles:
        if role not in current:
            Membership.objects.create(staff=staff, branch=branch, role=role)


@api_view(["GET", "POST"])
def staff_accounts(request, branch_id):
    from .commands import run_command

    branch = branch_for(request, branch_id)
    require(request, "staff.manage")
    if request.method == "GET":
        rows = (Staff.objects.filter(memberships__branch=branch).exclude(password_hash="!").distinct()
                .prefetch_related("memberships").order_by("display_name"))
        from .models import Role
        return Response({"staff": [_staff_row(s, branch) for s in rows],
                         "roles": [{"code": c, "name": n} for c, n in Role.choices]})

    def handler(ctx):
        data = request.data
        username = str(data.get("username", "")).strip().lower()
        name = str(data.get("display_name", "")).strip()
        if not username or not username.replace(".", "").replace("_", "").isalnum() or len(username) > 40:
            raise DomainError("Username: letters, numbers, dot or underscore", field_errors={"username": "invalid"})
        if not name:
            raise DomainError("Enter the person's name", field_errors={"display_name": "required"})
        _check_password_rules(data.get("password"))
        roles = _clean_roles(request, data.get("roles"))
        if Staff.objects.filter(tenant=branch.tenant, username__iexact=username).exists():
            raise DomainError("That username is taken", code="username_taken", field_errors={"username": "taken"},
                              http_status=409)
        s = Staff(tenant=branch.tenant, username=username, display_name=name[:80])
        s.set_password(str(data["password"]))
        s.save()
        _set_roles(s, branch, roles)
        ctx.audit("staff.create", s, data={"username": username, "roles": roles})
        return _staff_row(Staff.objects.prefetch_related("memberships").get(pk=s.pk), branch)

    return run_command(request, branch_id, "staff.create", request.data, handler)


@api_view(["POST"])
def staff_account(request, branch_id, staff_id):
    """Edit name / roles / active, or reset the password (the person then signs in with the new one)."""
    from .commands import run_command

    branch = branch_for(request, branch_id)
    require(request, "staff.manage")

    def handler(ctx):
        data = request.data
        s = Staff.objects.select_for_update().filter(pk=staff_id, tenant=branch.tenant).first()
        if s is None or not s.memberships.filter(branch=branch).exists():
            raise NotFound("Staff not found")
        changed = []
        if "display_name" in data:
            name = str(data["display_name"]).strip()
            if not name:
                raise DomainError("Enter the person's name", field_errors={"display_name": "required"})
            s.display_name = name[:80]
            changed.append("display_name")
        if "is_active" in data:
            if s.pk == request.user.staff.pk and not data["is_active"]:
                raise DomainError("You cannot switch off your own account", code="self_deactivate")
            s.is_active = bool(data["is_active"])
            changed.append("is_active")
            if not s.is_active:
                StaffSession.objects.filter(staff=s, revoked_at__isnull=True).update(revoked_at=timezone.now())
        if data.get("password"):
            _check_password_rules(data["password"])
            s.set_password(str(data["password"]))
            s.failed_attempts, s.locked_until = 0, None
            changed.append("password")
        if "roles" in data:
            roles = _clean_roles(request, data["roles"])
            if s.pk == request.user.staff.pk and not ({"manager", "owner"} & set(roles)):
                raise DomainError("You cannot remove your own manager role", code="self_demote")
            _set_roles(s, branch, roles)
            changed.append("roles")
        s.save()
        ctx.audit("staff.update", s, data={"changed": changed})
        return 200, _staff_row(Staff.objects.prefetch_related("memberships").get(pk=s.pk), branch)

    return run_command(request, branch_id, "staff.update", request.data, handler)


@api_view(["POST"])
def change_my_password(request, branch_id):
    branch_for(request, branch_id)
    s = request.user.staff
    if not s.check_password(str(request.data.get("current_password", ""))):
        raise DomainError("Current password is wrong", code="invalid_credentials",
                          field_errors={"current_password": "wrong"}, http_status=400)
    _check_password_rules(request.data.get("new_password"), "new_password")
    s.set_password(str(request.data["new_password"]))
    s.save(update_fields=["password_hash"])
    # Sign out the person's other devices; this one stays signed in.
    StaffSession.objects.filter(staff=s, revoked_at__isnull=True).exclude(pk=request.auth.pk) \
        .update(revoked_at=timezone.now())
    return Response({"ok": True})
