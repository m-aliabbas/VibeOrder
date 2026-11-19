"""Write protocol (spec 02 §Write protocol).

1. Client persists an Idempotency-Key (UUID) bound to operator, device, branch, intent and payload hash.
2. One DB transaction: claim the key (unique constraint), lock aggregates in stable order, validate,
   apply, and persist revision, result, audit entry, outbox event and print jobs in the same commit.
3. Identical retry -> stored result. Same key + different request -> 409. Errors are not stored,
   so the client may refresh and resubmit under a new key.
"""
import hashlib
import os
import json
import uuid
from datetime import date
from dataclasses import dataclass, field

from django.db import IntegrityError, connection, transaction
from rest_framework.response import Response

from .errors import Conflict, DomainError, NotFound
from .models import AuditEntry, Branch, CommandReceipt, SyncOutbox


def canonical_hash(intent: str, payload) -> str:
    raw = json.dumps({"intent": intent, "payload": payload}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


GLOBAL_DAY = date(2000, 1, 1)


def next_branch_counter(branch_id, column: str) -> int:
    """Atomically increment the branch stream position. Holds the branch row lock until commit,
    so stream positions can never commit behind an acknowledged cursor."""
    assert column == "stream_position"
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE core_branch SET stream_position = stream_position + 1 WHERE id = %s RETURNING stream_position",
            [branch_id],
        )
        return cur.fetchone()[0]


def next_sequence(branch_id, name: str, business_date=None) -> int:
    """Per-branch, per-business-day sequence (business_date None = never resets)."""
    with connection.cursor() as cur:
        cur.execute(
            """INSERT INTO core_branchcounter (branch_id, name, business_date, value) VALUES (%s, %s, %s, 1)
               ON CONFLICT (branch_id, name, business_date) DO UPDATE SET value = core_branchcounter.value + 1
               RETURNING value""",
            [branch_id, name, business_date or GLOBAL_DAY],
        )
        return cur.fetchone()[0]


@dataclass
class CommandContext:
    principal: object
    branch: Branch
    key: uuid.UUID | None
    correlation_id: str
    events: list = field(default_factory=list)
    channel: str = "staff"

    @property
    def staff(self):
        return self.principal.staff

    @property
    def device(self):
        return self.principal.device

    def audit(self, action, aggregate, prev_revision=None, next_revision=None, reason="", data=None, approved_by=None):
        return AuditEntry.objects.create(
            branch=self.branch,
            staff=self.staff,
            device=self.device,
            approved_by=approved_by,
            action=action,
            aggregate_type=type(aggregate).__name__,
            aggregate_id=aggregate.pk,
            prev_revision=prev_revision,
            next_revision=next_revision,
            command_key=self.key,
            correlation_id=self.correlation_id,
            reason=reason,
            data=data or {},
        )

    def emit(self, event_type, aggregate, payload):
        """Queue an outbox event; positions are allocated at the end of the transaction."""
        self.events.append((event_type, aggregate, payload))

    def flush_events(self):
        for event_type, aggregate, payload in self.events:
            pos = next_branch_counter(self.branch.pk, "stream_position")
            SyncOutbox.objects.create(
                branch=self.branch,
                stream_position=pos,
                event_type=event_type,
                aggregate_type=type(aggregate).__name__,
                aggregate_id=aggregate.pk,
                revision=getattr(aggregate, "revision", 1),
                payload=json.loads(json.dumps(payload, default=str)),
            )
        self.events.clear()


class _ReplayExisting(Exception):
    pass


def _replay(receipt: CommandReceipt, payload_hash: str, principal):
    if (
        receipt.payload_hash != payload_hash
        or receipt.staff_id != principal.staff.pk
        or receipt.device_id != principal.device.pk
    ):
        raise Conflict(
            "Idempotency key was already used for a different request",
            code="idempotency_mismatch",
        )
    resp = Response(receipt.response, status=receipt.status_code)
    resp["Idempotent-Replay"] = "true"
    return resp


def run_command(request, branch_id, intent, payload, handler, success_status=201):
    principal = request.user
    if str(principal.branch.pk) != str(branch_id):
        raise NotFound("Branch not found")
    raw_key = request.headers.get("Idempotency-Key")
    try:
        key = uuid.UUID(raw_key)
    except (TypeError, ValueError):
        raise DomainError("Idempotency-Key header must be a UUID", code="missing_idempotency_key", http_status=400)

    payload_hash = canonical_hash(intent, payload)
    existing = CommandReceipt.objects.filter(branch=principal.branch, key=key).first()
    if existing:
        return _replay(existing, payload_hash, principal)

    ctx = CommandContext(principal, principal.branch, key, getattr(request, "correlation_id", ""))
    try:
        with transaction.atomic():
            try:
                with transaction.atomic():
                    receipt = CommandReceipt.objects.create(
                        branch=principal.branch, key=key, intent=intent, payload_hash=payload_hash,
                        staff=principal.staff, device=principal.device, status_code=0, response={},
                    )
            except IntegrityError:
                raise _ReplayExisting()
            body = handler(ctx)
            status_code = success_status
            if isinstance(body, tuple):
                status_code, body = body
            body = json.loads(json.dumps(body, default=str))
            ctx.flush_events()
            receipt.status_code = status_code
            receipt.response = body
            receipt.save(update_fields=["status_code", "response"])
    except _ReplayExisting:
        existing = CommandReceipt.objects.get(branch=principal.branch, key=key)
        return _replay(existing, payload_hash, principal)
    _maybe_crash(intent)
    return Response(body, status=status_code)


def _maybe_crash(intent):
    """Restart drill: die after commit, before the response, like a power cut at the worst moment."""
    from django.conf import settings
    targets = {t.strip() for t in settings.CRASH_AFTER_COMMIT.split(",") if t.strip()}
    if intent in targets:
        marker = settings.RUN_DIR / f"crashed-{intent}"
        if not marker.exists():  # crash once per intent, so the retry after restart can complete
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("1")
            os._exit(17)


def run_internal(principal, intent, handler, channel="staff"):
    """Execute a branch operation without an idempotency key (server-side tasks, tests)."""
    ctx = CommandContext(principal, principal.branch, None, "", channel=channel)
    with transaction.atomic():
        body = handler(ctx)
        ctx.flush_events()
    return body
