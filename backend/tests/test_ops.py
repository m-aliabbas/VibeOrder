"""A19 recovery: encrypted backup + verified restore drill, host fencing, take-over and reconciliation report."""
import pytest

from apps.ops.models import BackupRecord
from apps.ops.services import backup, restore_drill, take_over

from .conftest import MIXED_PIZZA, Staff, add_round, open_table


@pytest.mark.django_db(transaction=True)
def test_backup_is_encrypted_and_restore_drill_verifies(tmp_path, settings):
    from apps.core.fixtures_demo import seed
    settings.PRINT_SPOOL_DIR = tmp_path / "spool"
    settings.BACKUP_DIR = tmp_path / "backups"
    d = seed()
    w = Staff(d["branch"], "bilal", "bilal123")
    chk = open_table(w, 6)
    add_round(w, chk["check_id"], [MIXED_PIZZA])
    rec = backup()
    assert rec.ok, rec.detail
    raw = open(rec.path, "rb").read()
    assert not raw.startswith(b"PGDMP") and rec.outbox_pending >= 2  # encrypted; outbox included
    drill = restore_drill(rec)
    assert drill.ok, drill.detail
    assert drill.detail["restored_counts"]["orders_check"] == 1
    assert drill.detail["restored_counts"]["core_syncoutbox"] == rec.outbox_pending
    health = w.get("devices/health").json()
    assert health["backup"]["last_drill_ok"] is True and health["backup"]["stale"] is False


def test_a19_fenced_host_rejects_writes_until_take_over(demo, waiter, manager, settings):
    b = demo["branch"]
    chk = open_table(waiter, 6)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA])
    # The restored spare host is this one; the old host is still recorded as writable.
    settings.HOST_ID = "spare-host"
    type(b).objects.filter(pk=b.pk).update(writable_host="old-host")
    b.refresh_from_db()
    r = waiter.post("checks", {"service_type": "dine_in", "table_id": chk["check_id"]})
    assert r.status_code == 503 and r.json()["code"] == "host_fenced"
    assert waiter.get(f"checks/{chk['check_id']}").status_code == 200  # reads still work to reconcile
    rec = manager.get("recovery").json()
    assert not rec["host"]["writable_here"] and len(rec["open_checks"]) == 1
    assert rec["pending_sync_events"] >= 2
    assert waiter.get("recovery").status_code == 403
    out = take_over(b, "old host destroyed by power surge")
    assert out["previous_host"] == "old-host" and out["fence_epoch"] == 1
    assert manager.get("recovery").json()["host"]["writable_here"]
    _, r = add_round(waiter, chk["check_id"], [{"client_line_id": "c", "product": "cola-500", "quantity": 1}])
    assert r.status_code == 201
    # The old host is now fenced: it would be rejected
    settings.HOST_ID = "old-host"
    r = waiter.post("quotes", {"items": [{"client_line_id": "d", "product": "cola-500"}]}, key=False)
    assert r.status_code == 503
    assert BackupRecord.objects.count() == 0
