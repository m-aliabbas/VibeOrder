"""Branch side of replication: outbox uploader (contiguous acks, backoff with jitter) and online-offer import.

The import key is the cloud request id (OnlineImport primary key): replaying an offer returns the committed
result, so one web order gives at most one branch check and one kitchen dispatch (A15/A16). Acceptance, check,
round and kitchen jobs commit in one transaction.
"""
import json
import random
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.core.models import Device, Staff, SyncOutbox
from apps.ops.models import OnlineImport, SyncState


class TransportError(Exception):
    pass


class HttpTransport:
    def __init__(self, base_url=None, token=None, timeout=10):
        self.base = (base_url or settings.CLOUD_URL).rstrip("/") + "/api/v1/cloud/sync/"
        self.token = token or settings.CLOUD_BRANCH_TOKEN
        self.timeout = timeout

    def _call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Branch {self.token}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransportError(str(exc)) from exc

    def post(self, path, body):
        return self._call("POST", path, body)

    def get(self, path):
        return self._call("GET", path)


def _state(branch):
    st, _ = SyncState.objects.get_or_create(branch=branch)
    return st


def upload(branch, transport):
    st = _state(branch)
    now = timezone.now()
    if st.backoff_until and st.backoff_until > now:
        return {"skipped": "backoff", "until": st.backoff_until}
    batch = list(SyncOutbox.objects.filter(branch=branch, stream_position__gt=st.acked_position)
                 .order_by("stream_position")[:settings.SYNC_BATCH_SIZE])
    events = [{"event_id": str(e.event_id), "stream_position": e.stream_position, "schema_version": e.schema_version,
               "event_type": e.event_type, "aggregate_type": e.aggregate_type, "aggregate_id": str(e.aggregate_id),
               "revision": e.revision, "payload": e.payload} for e in batch]
    st.last_attempt_at = now
    try:
        res = transport.post("events", {"events": events})
        acked = int(res["acked_position"])
    except (TransportError, KeyError, ValueError, TypeError) as exc:
        st.consecutive_failures += 1
        delay = min(300, 2 ** min(st.consecutive_failures, 8)) * random.uniform(0.5, 1.5)
        st.backoff_until = now + timedelta(seconds=delay)
        st.last_error = str(exc)[:300]
        st.save()
        SyncOutbox.objects.filter(pk__in=[e.pk for e in batch]).update(attempts=1)
        return {"error": st.last_error, "retry_in": delay}
    SyncOutbox.objects.filter(branch=branch, stream_position__lte=acked, acked_at__isnull=True).update(acked_at=now)
    st.acked_position = max(st.acked_position, acked)
    st.last_success_at, st.last_error, st.consecutive_failures, st.backoff_until = now, "", 0, None
    st.save()
    return {"sent": len(events), "acked_position": acked, "missing": res.get("missing", [])}


# ---------------------------------------------------------------- online import

class SystemPrincipal:
    """Branch-internal identity for web order intake. Never logs in; scoped to one branch."""
    roles = {"system"}
    permissions = {"check.open", "check.append", "quote"}

    def __init__(self, branch):
        self.branch = branch
        self.staff, _ = Staff.objects.get_or_create(tenant=branch.tenant, username="web-orders", defaults={
            "display_name": "Online orders", "pin_hash": "!", "is_active": True})
        self.device = Device.objects.filter(branch=branch, kind="system").first()
        if self.device is None:
            import secrets
            self.device = Device.objects.create(branch=branch, name="Online intake", kind="system",
                                                token_hash=Device.hash_token(secrets.token_urlsafe(32)))

    def has(self, perm):
        return perm in self.permissions


def import_offer(branch, payload):
    """Accept once or reject with reason. Replays return the stored result."""
    from apps.catalog.pricing import PricingError
    from apps.core.commands import run_internal
    from apps.core.errors import DomainError
    from apps.fulfillment.models import DeliveryZone
    from apps.orders.services import append_round, create_quote, open_check

    rid = payload["request_id"]
    prior = OnlineImport.objects.filter(pk=rid).first()
    if prior is not None:
        return prior.result
    principal = SystemPrincipal(branch)

    def reject(reason):
        try:
            with transaction.atomic():
                OnlineImport.objects.create(request_id=rid, branch=branch, status="rejected",
                                            result={"status": "rejected", "reason": reason})
        except IntegrityError:
            pass
        return OnlineImport.objects.get(pk=rid).result

    deadline = parse_datetime(payload.get("deadline", ""))
    if deadline is None or deadline < timezone.now():
        return reject("expired before the branch received it")
    branch.refresh_from_db(fields=["online_paused", "active_release"])
    if branch.online_paused:
        return reject("branch paused online orders")
    if str(branch.active_release_id) != payload.get("menu_release"):
        return reject("menu changed; guest must review the order")

    def handler(ctx):
        # Claim the import key first: a concurrent replay blocks here, then sees the committed row.
        imp = OnlineImport.objects.create(request_id=rid, branch=branch, status="accepted", result={})
        open_data = {"service_type": payload["service_type"], "customer_name": payload["customer_name"],
                     "customer_phone": payload["phone"], "notes": payload.get("notes", "")}
        if payload["service_type"] == "delivery":
            zone = DeliveryZone.objects.filter(branch=branch, code=payload.get("zone_code"), is_active=True).first()
            if zone is None:
                raise DomainError("delivery zone not served", code="zone")
            open_data.update({"zone_id": str(zone.pk), "address_text": payload["address_text"],
                              "payment": "prepaid" if payload.get("payment") == "prepaid" else "cod"})
        opened = open_check(ctx, open_data)
        q = create_quote(principal, {"items": payload["items"], "service_type": payload["service_type"]})
        res = append_round(ctx, opened["check_id"], {"quote_id": q["quote_id"], "items": payload["items"]})
        from apps.orders.models import Check
        from apps.orders.services import check_amounts
        check = Check.objects.get(pk=opened["check_id"])
        total = check_amounts(check)["total_minor"]
        if total != int(payload["total_minor"]):
            raise DomainError("price changed", code="price")
        if payload.get("payment") == "prepaid":
            from apps.cash.models import Tender, TenderAllocation
            from apps.cash.services import allocate_to_lines
            t = Tender.objects.create(branch=branch, check_ref=check, method="online", status="succeeded",
                                      amount_minor=total, external_ref=payload.get("provider_ref", "")[:80],
                                      currency=branch.currency, staff=ctx.staff, business_date=branch.business_date())
            for lid, amt in allocate_to_lines(check, total):
                TenderAllocation.objects.create(tender=t, line_id=lid, amount_minor=amt)
            ctx.emit("tender.succeeded", t, {"check_id": check.pk, "tender_id": t.pk, "method": "online",
                                             "amount_minor": total, "business_date": t.business_date})
        imp.check_ref = check
        imp.result = {"status": "accepted", "check_id": str(check.pk), "token": check.token,
                      "promised_at": check.promised_at.isoformat() if check.promised_at else None,
                      "ticket_ids": res["ticket_ids"]}
        imp.save()
        ctx.audit("online.accept", check, None, check.revision, data={"request_id": rid,
                                                                      "reference": payload.get("reference")})
        ctx.emit("online.accepted", check, {"request_id": rid, "check_id": check.pk, "token": check.token})
        return imp.result

    try:
        return run_internal(principal, "online.accept", handler, channel="web")
    except IntegrityError:
        return OnlineImport.objects.get(pk=rid).result
    except PricingError as e:
        return reject(f"not available: {e.message}")
    except DomainError as e:
        return reject(e.message)


def pull_inbox(branch, transport):
    st = _state(branch)
    try:
        res = transport.get("inbox")
    except TransportError as exc:
        st.last_error = str(exc)[:300]
        st.save(update_fields=["last_error"])
        return {"error": st.last_error}
    done = []
    for cmd in res.get("commands", []):
        if cmd["kind"] != "online_offer":
            continue
        result = import_offer(branch, cmd["payload"])
        try:
            transport.post("ack", {"command_id": cmd["id"], "result": result})
            OnlineImport.objects.filter(pk=cmd["payload"]["request_id"]).update(ack_pending=False)
        except TransportError:
            pass  # stays ack_pending; the cloud re-delivers the command and the replay returns this result
        done.append({"command_id": cmd["id"], **result})
    st.last_inbox_at = timezone.now()
    st.save(update_fields=["last_inbox_at"])
    return {"processed": done}


def sync_once(branch, transport):
    return {"upload": upload(branch, transport), "inbox": pull_inbox(branch, transport)}
