"""Branch operations: backups, restore drills, host fencing and cloud sync state (spec 02 §Security, deployment)."""
from django.db import models

from apps.core.models import Branch


class BackupRecord(models.Model):
    KINDS = [("backup", "Backup"), ("restore_drill", "Restore drill")]
    id = models.BigAutoField(primary_key=True)
    kind = models.CharField(max_length=14, choices=KINDS, default="backup")
    host_id = models.CharField(max_length=80)
    path = models.CharField(max_length=500)
    size_bytes = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    outbox_pending = models.PositiveIntegerField(default=0)
    ok = models.BooleanField(default=True)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class SyncState(models.Model):
    """Branch side of replication: uploader cursor, backoff and inbox polling."""
    branch = models.OneToOneField(Branch, on_delete=models.CASCADE, primary_key=True, related_name="sync_state")
    acked_position = models.BigIntegerField(default=0)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    consecutive_failures = models.PositiveIntegerField(default=0)
    backoff_until = models.DateTimeField(null=True, blank=True)
    last_inbox_at = models.DateTimeField(null=True, blank=True)


class OnlineImport(models.Model):
    """Branch import key for a web order: one cloud request → at most one branch order."""
    request_id = models.UUIDField(primary_key=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    check_ref = models.ForeignKey("orders.Check", null=True, blank=True, on_delete=models.PROTECT,
                                  db_column="check_id")
    status = models.CharField(max_length=10)  # accepted | rejected
    result = models.JSONField()
    ack_pending = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
