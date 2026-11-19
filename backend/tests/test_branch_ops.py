"""Branch host operations: backup retention and supervisor health in health + alerts."""
import json
from datetime import timedelta

from django.utils import timezone

from apps.ops.models import BackupRecord
from apps.ops.services import prune_backups


def _backup(tmp_path, when, name):
    p = tmp_path / name
    p.write_bytes(b"x")
    r = BackupRecord.objects.create(host_id="t", path=str(p), ok=True)
    BackupRecord.objects.filter(pk=r.pk).update(created_at=when)
    return p


def test_prune_keeps_newest_and_first_of_each_day(db, tmp_path):
    now = timezone.now().replace(hour=12)
    files = []
    for day in range(4):  # 4 days x 3 backups
        for h in (1, 5, 9):
            files.append(_backup(tmp_path, now - timedelta(days=day, hours=12 - h), f"d{day}h{h}.vob"))
    removed = prune_backups(keep=2, keep_daily=3)
    kept = sorted(p.name for p in tmp_path.iterdir())
    # newest two (today 9h, 5h) + first of each of the last 3 days (today 1h, d1h1, d2h1)
    assert kept == sorted(["d0h9.vob", "d0h5.vob", "d0h1.vob", "d1h1.vob", "d2h1.vob"])
    assert removed == 12 - 5
    assert BackupRecord.objects.filter(path="").count() == 7


def _write_state(settings, tmp_path, **overrides):
    settings.RUN_DIR = tmp_path
    now = timezone.localtime()
    st = {"pid": 1, "host_id": "t", "started_at": now.isoformat(), "updated_at": now.isoformat(), "stopped": False,
          "children": {"server": {"status": "running", "exits_last_10min": 0, "last_exit_code": None,
                                  "last_exit_at": None},
                       "print": {"status": "running", "exits_last_10min": 0, "last_exit_code": None,
                                 "last_exit_at": None}},
          "backup": {"last_ok": True, "last_error": "", "last_at": now.isoformat()}}
    for k, v in overrides.items():
        st[k] = v
    (tmp_path / "supervisor.json").write_text(json.dumps(st))
    return st


def test_supervisor_health_alerts(demo, manager, settings, tmp_path):
    def ids():
        return {a["id"] for a in manager.get("alerts").json()["alerts"]}

    _write_state(settings, tmp_path)
    assert not any(i.startswith("system:service") or i == "system:supervisor" for i in ids())
    st = _write_state(settings, tmp_path)
    st["children"]["print"].update(status="restarting", exits_last_10min=4, last_exit_code=1)
    st["backup"] = {"last_ok": False, "last_error": "pg_dump: connection refused", "last_at": None}
    (tmp_path / "supervisor.json").write_text(json.dumps(st))
    got = ids()
    assert "system:service:print" in got and "system:backup_failed" in got
    health = manager.get("devices/health").json()["services"]
    assert health["children"]["print"]["exits_last_10min"] == 4 and health["stale"] is False
    # Supervisor not updating any more → one "not supervised" alert
    old = (timezone.localtime() - timedelta(minutes=5)).isoformat()
    _write_state(settings, tmp_path, updated_at=old)
    got = ids()
    assert "system:supervisor" in got and "system:service:print" not in got

