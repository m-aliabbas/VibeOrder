"""Staff authentication: every person signs in with username + password.

Login (`POST /api/v1/login`) returns a session token. Every request carries `Authorization: Bearer <session>`.
The session binds operator, branch and the device it was opened on; scope is derived from it, never from the URL.
Devices register themselves at login (the app keeps a random `device_key`), so there is no pairing step.
A manager can still block a lost device from the Devices screen.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import Device, Staff, StaffSession


class Principal:
    """Authenticated operator on a device inside one branch."""

    is_authenticated = True

    def __init__(self, session: StaffSession):
        self.session = session
        self.staff = session.staff
        self.device = session.device
        self.branch = session.branch
        self.roles = set(
            session.staff.memberships.filter(branch=session.branch, is_active=True).values_list("role", flat=True)
        )
        from .models import ROLE_PERMISSIONS
        perms = set()
        for r in self.roles:
            perms |= ROLE_PERMISSIONS.get(r, set())
        self.permissions = perms

    def has(self, perm: str) -> bool:
        return "*" in self.permissions or perm in self.permissions


class InvalidLogin(Exception):
    """Wrong username or password. Raised after the attempt counter is committed."""


def verify_password(username: str, password: str, staff_qs=None) -> Staff:
    """Check username + password with lockout after repeated failures. Commits the attempt counter."""
    from .errors import DomainError  # late: errors imports DRF views, which load this module

    username = (username or "").strip().lower()
    qs = staff_qs if staff_qs is not None else Staff.objects.all()
    with transaction.atomic():
        staff = qs.select_for_update().filter(username__iexact=username, is_active=True).order_by("created_at").first()
        now = timezone.now()
        if staff and staff.locked_until and staff.locked_until > now:
            raise DomainError("Too many wrong passwords. Try again in a few minutes.", code="locked", http_status=429)
        if staff is None or not staff.check_password(str(password or "")):
            if staff:
                staff.failed_attempts += 1
                if staff.failed_attempts >= settings.LOGIN_MAX_ATTEMPTS:
                    staff.locked_until = now + timedelta(seconds=settings.LOGIN_LOCKOUT_SECONDS)
                    staff.failed_attempts = 0
                staff.save(update_fields=["failed_attempts", "locked_until"])
            ok = False
        else:
            staff.failed_attempts = 0
            staff.locked_until = None
            staff.save(update_fields=["failed_attempts", "locked_until"])
            ok = True
    if not ok:
        raise InvalidLogin()
    return staff


class StaffSessionAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        session = (
            StaffSession.objects.select_related("staff", "device", "branch")
            .filter(token_hash=Device.hash_token(header[7:].strip()))
            .first()
        )
        if session is None or not session.is_valid or not session.staff.is_active:
            raise AuthenticationFailed("Session expired. Please sign in again.")
        if session.device.revoked_at is not None:
            raise AuthenticationFailed("This device was blocked by a manager.")
        Device.objects.filter(pk=session.device_id).update(last_seen_at=timezone.now())
        return (Principal(session), session)

    def authenticate_header(self, request):
        return "Bearer"
