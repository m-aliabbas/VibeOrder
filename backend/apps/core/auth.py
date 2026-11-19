"""Device + staff session authentication.

Every staff request carries X-Device-Token (paired device) and Authorization: Bearer <session>.
The session binds operator, device and branch; scope is derived from it, never from the URL.
"""
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import Device, StaffSession


class Principal:
    """Authenticated operator on a paired device inside one branch."""

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


def device_from_request(request):
    raw = request.headers.get("X-Device-Token")
    if not raw:
        return None
    device = Device.objects.select_related("branch").filter(token_hash=Device.hash_token(raw)).first()
    if device is None or device.revoked_at is not None:
        raise AuthenticationFailed("Device not paired or revoked")
    return device


class StaffSessionAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        device = device_from_request(request)
        if device is None:
            raise AuthenticationFailed("Missing device token")
        token_hash = Device.hash_token(header[7:].strip())
        session = (
            StaffSession.objects.select_related("staff", "device", "branch")
            .filter(token_hash=token_hash, device=device)
            .first()
        )
        if session is None or not session.is_valid or not session.staff.is_active:
            raise AuthenticationFailed("Session expired or revoked")
        Device.objects.filter(pk=device.pk).update(last_seen_at=timezone.now())
        return (Principal(session), session)

    def authenticate_header(self, request):
        return "Bearer"
