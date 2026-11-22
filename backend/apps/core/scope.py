"""Tenant/branch scoping: derived from the authenticated session, never from client-supplied IDs."""
from .errors import Forbidden, NotFound


def branch_for(request, branch_id):
    principal = request.user
    if principal is None or str(principal.branch.pk) != str(branch_id):
        # A guessed ID from another branch returns no data.
        raise NotFound("Branch not found")
    return principal.branch


def require(request, perm):
    if not request.user.has(perm):
        raise Forbidden(f"Your role does not allow {perm}", code="permission_denied")


def resolve_approver(principal, data, perm):
    """Approval by the operator if permitted, otherwise by a manager's username + password in the payload.
    Returns the approving Staff. The approver acts with their own identity."""
    from .errors import DomainError
    from .models import Staff

    if principal.has(perm):
        return principal.staff
    approval = (data or {}).get("approval") or {}
    username, password = approval.get("username"), approval.get("password")
    if not username or not password:
        raise Forbidden("Manager approval required", code="approval_required")
    staff = Staff.objects.filter(tenant=principal.branch.tenant, username=username, is_active=True).first()
    if staff is None or not staff.check_password(str(password)):
        raise Forbidden("Approval credentials rejected", code="approval_rejected")
    roles = set(staff.memberships.filter(branch=principal.branch, is_active=True).values_list("role", flat=True))
    if not roles & {"manager", "owner"}:
        raise Forbidden("Approver is not a manager at this branch", code="approval_rejected")
    if staff.pk == principal.staff.pk:
        raise DomainError("Approver must differ from operator", code="approval_rejected", http_status=403)
    return staff
