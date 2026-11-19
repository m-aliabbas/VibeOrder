# Branch host install (pilot)

One PC at the branch (usually the cashier PC) runs PostgreSQL, the VibeOrder services and the TLS proxy. Tablets,
the KDS browser and the counter app talk to it over the LAN; selling never needs the internet.

## What runs

| Process | Started by | What |
|---|---|---|
| `manage.py run_branch` | Scheduled Task "VibeOrder Branch" (Windows) / `vibeorder-branch.service` (Linux) | Supervisor: keeps `serve` (waitress API + KDS), `print_worker` and `sync_worker` running, restarts them with backoff, backs up every 15 min, prunes old backups, daily restore drill |
| `caddy run` | Scheduled Task "VibeOrder TLS Proxy" | HTTPS on the LAN → 127.0.0.1:8000 |
| PostgreSQL 17 | its own Windows service | database |

Logs: `backend/var/logs/<server|print|sync-B1>.log`. Live state: `backend/var/run/supervisor.json`, also shown in
the app (Devices → health, Alerts: "Print worker keeps stopping", "Scheduled backup failed", …).

## Windows install

1. Install PostgreSQL 17 (EDB installer, includes the VC++ runtime) and create the database + user.
2. Copy the repo to e.g. `C:\VibeOrder`. In `backend`: `uv venv .venv` then `uv pip install -r requirements.txt`.
3. `copy branch.env.example branch.env` and fill it in. **Generate `VIBEORDER_BACKUP_KEY` and store a copy off the
   PC** (password manager / printed in the owner's safe) — backups cannot be restored without it.
4. `.venv\Scripts\python manage.py migrate`, load the merchant menu, then
   `.venv\Scripts\python manage.py branch_doctor --printers` until it says OK.
5. Download `caddy.exe`, set the site address in `deploy/Caddyfile`, then from an **elevated** PowerShell:
   `.\deploy\windows\install-service.ps1 -CaddyExe C:\VibeOrder\caddy.exe`
6. Reboot the PC once and confirm the app reconnects without anyone signing in to Windows.

Remove: `.\deploy\windows\uninstall-service.ps1` (keeps data, backups, logs).

## Proving it before go-live

```
cd backend
.venv\Scripts\python scripts\restart_drill.py        # crash after commit ×3 + full power-loss restart
.venv\Scripts\python manage.py backup_branch --drill   # backup + verified restore into a scratch DB
```

`restart_drill` must print `RESTART DRILL OK`. It uses the dev database and a scratch port; run it on a staging copy,
not the live branch database.
