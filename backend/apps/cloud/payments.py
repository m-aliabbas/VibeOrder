"""Prepaid web orders through an online provider (gated by ONLINE_PAYMENTS_ENABLED; "fakepay" adapter).

Separate provider authorization, branch acceptance and fulfillment release: the order is offered to the branch
(and so reaches the kitchen) only after an authenticated success callback. Callbacks are deduplicated by id,
amount/currency mismatches are rejected and leave the payment pending, and a success that arrives after the
payment window (or for an order the branch rejected) routes to refund/void instead of fulfillment (A23).
"""
import hashlib
import hmac
import json
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.errors import DomainError

from .models import OnlineOrder, ProviderCallback, ProviderPayment


def start_payment(order):
    return ProviderPayment.objects.create(
        order=order, provider_ref=f"fp_{order.token[:20]}", amount_minor=order.total_minor, currency=order.currency,
        expires_at=timezone.now() + timedelta(seconds=settings.PAYMENT_WINDOW_SECONDS))


def sign(body: bytes) -> str:
    return hmac.new(settings.FAKEPAY_SECRET.encode(), body, hashlib.sha256).hexdigest()


def expire_unpaid():
    """Orders whose payment window passed without success expire; the payment stays open for a late callback."""
    now = timezone.now()
    ids = ProviderPayment.objects.filter(status="pending", expires_at__lt=now).values_list("order_id", flat=True)
    return OnlineOrder.objects.filter(pk__in=list(ids), status="awaiting_payment").update(status="expired", updated_at=now)


def webhook(raw: bytes, signature: str):
    if not signature or not hmac.compare_digest(sign(raw), signature):
        raise DomainError("Invalid signature", code="bad_signature", http_status=401)
    try:
        body = json.loads(raw)
        cb_id, ref, status = str(body["callback_id"]), str(body["provider_ref"]), body["status"]
        amount, currency = int(body["amount_minor"]), str(body.get("currency", ""))
    except (KeyError, TypeError, ValueError):
        raise DomainError("Malformed callback", code="bad_callback", http_status=400)
    expire_unpaid()
    with transaction.atomic():
        try:
            with transaction.atomic():
                cb = ProviderCallback.objects.create(callback_id=cb_id, provider="fakepay", payload=body, outcome="")
        except IntegrityError:
            prior = ProviderCallback.objects.get(callback_id=cb_id)
            return {"callback_id": cb_id, "outcome": prior.outcome, "duplicate": True}
        pay = ProviderPayment.objects.select_for_update().filter(provider_ref=ref).first()
        if pay is None:
            outcome = "unknown_payment"
        elif amount != pay.amount_minor or currency != pay.currency:
            outcome = "rejected_mismatch"  # ambiguous: keep pending for reconciliation
        else:
            order = OnlineOrder.objects.select_for_update().get(pk=pay.order_id)
            if status == "authorized":
                if pay.status in ("authorized", "refund_required", "refunded"):
                    outcome = "already_processed"
                elif order.status == "awaiting_payment" and timezone.now() <= pay.expires_at:
                    pay.status = "authorized"
                    from .services import offer
                    offer(order)
                    outcome = "authorized_offered"
                else:
                    # Late success after timeout/expiry: never fulfil silently; compensate.
                    pay.status = "refund_required"
                    if order.status == "awaiting_payment":
                        order.status = "expired"
                        order.save(update_fields=["status", "updated_at"])
                    outcome = "late_success_refund_required"
            elif status == "failed":
                if pay.status == "pending":
                    pay.status = "failed"
                    if order.status == "awaiting_payment":
                        order.status = "expired"
                        order.save(update_fields=["status", "updated_at"])
                outcome = "failed"
            else:
                outcome = "ignored_status"
            pay.revision += 1
            pay.save()
        cb.outcome = outcome
        cb.save(update_fields=["outcome"])
    return {"callback_id": cb_id, "outcome": outcome, "duplicate": False}


def on_branch_rejected(order):
    pay = ProviderPayment.objects.select_for_update().filter(order=order).first()
    if pay and pay.status == "authorized":
        pay.status = "refund_required"
        pay.revision += 1
        pay.save()
