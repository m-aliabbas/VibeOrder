---
name: dev
description: Develop VibeOrder (Django branch server + Flutter staff app + KDS). Use when implementing a feature, milestone or fix in this repo, starting the local stack, or deciding where code belongs. Covers layout, the write protocol every command must follow, how to add an endpoint, seed data, and how to run and look at the apps.
---

# VibeOrder development

Specs live in `docs/01..05-*.md` (product, architecture, API, commercial rules, acceptance). `Todo.md` is the
working backlog: pick the first unchecked item of the current milestone, and tick it (with the test that proves
it) when done. Read the relevant spec section before coding; record any deviation in `Todo.md` → *Deviations*.

## Stack and layout

| Path | What |
|---|---|
| `backend/` | Django 5.2 + DRF modular monolith, PostgreSQL 17 (Homebrew `postgresql@17`) |
| `backend/apps/core` | tenant, branch, staff, membership, device pairing, sessions, **write protocol** (`commands.py`), audit, outbox, counters, scope |
| `backend/apps/catalog` | versioned menu release, products/variants/options/deals, branch sellouts, **pricing engine** (`pricing.py`, pure) |
| `backend/apps/orders` | areas, tables, checks, rounds, lines, quotes (`services.py`); corrections after send: amend/remake/void/move/merge + `CheckAdjustment` (`corrections.py`) |
| `backend/apps/kitchen` | production tickets/items, station queue, printers, durable print jobs, `print_worker` command |
| `backend/apps/cash` | drawer shifts, movements, tenders, allocations, check close (`finalize_close`), refunds (`refunds.py`), payer receipts |
| `backend/apps/orders/discounts.py` | order promotion + line adjustments → `CheckLine.discount_minor/tax_minor` (`apply_discounts`) |
| `backend/apps/fulfillment` | customers (masked lookup, repeat order), delivery zones, deliveries + attempts, packing handover, rider collections/remittances |
| `backend/apps/stock` | stock items, recipes (base/product/option/packaging by size), ledger with unique origin refs, receipts, losses, counts, transfers, moving average |
| `backend/apps/ops` | encrypted backups + restore drill, host fencing middleware, `take_over`, recovery report, `SyncState`, `OnlineImport` |
| `backend/apps/cloud` | cloud profile: event ingest + owner projection, inbox/ack offers, storefront API, tracking, fake payment provider, owner portal API; branch side `branch_sync.py` + `sync_worker` |
| `backend/apps/reports` | report definitions (spec 04) + CSV with scope header, used by branch managers and the owner portal |
| `backend/templates/store.html`, `track.html`, `owner.html` | guest storefront `/s/demo/`, tracking `/t/<token>`, owner portal `/owner/` (plain JS) |
| `backend/templates/kds.html` | kitchen display (plain JS, polls the same REST API) |
| `app/` | Flutter staff app (waiter tablet, cashier); targets android, windows, macos, web |
| `app/lib/api` | `ApiClient` (error envelope, idempotency keys), `MenuView` |
| `app/lib/state/app_state.dart` | pairing, session, bootstrap, drafts + pending commands scoped to branch+operator |

## Start the stack

```bash
make setup      # first time: venv, deps, DBs, migrate, seed
make server     # :8000  API + /kds/ + /admin/
make worker     # print worker → backend/var/spool/<queue>/*.txt (spooler fake)
make sync       # branch↔cloud sync workers for B1 + B2 (needed for storefront orders and owner figures)
make backup     # encrypted DB backup → backend/var/backups ; make drill = backup + verified restore
make app-web    # builds Flutter web, serves on http://127.0.0.1:8081
make reset      # wipe dev DB and reseed
```

Run servers in the background. `seed_demo` prints staff PINs and single-use pairing codes (24h). Demo staff:
bilal 1111 / sana 2222 (waiters), hamza 3333 (cashier), usman 4444 (cook), zara 9999 (manager),
faisal 6666 (packer), imran 5555 / kashif 5557 (riders), ayesha 8888 (owner, B1+B2), nadia 7777 (B2 manager).
Branch B2 exists for multi-branch work (A20). Owner portal: http://localhost:8000/owner/ (business `demo`).
Storefront: http://localhost:8000/s/demo/ — branches show "offline" until `make sync` runs (60 s heartbeat).
New pairing code without reseeding: `make seed` (adds codes, leaves data), or as manager in the app → Devices.

Look at UI: Flutter web renders to canvas, so drive it with Playwright mouse coordinates
(`page.mouse.click(x, y)`) and screenshots; the KDS is normal HTML (use selectors).
`make app-macos` runs the desktop app. Android/Windows builds need those toolchains.

## Rules every change must follow

1. **Every business write goes through `run_command(request, branch_id, intent, payload, handler)`**
   (`apps/core/commands.py`). The handler runs inside one transaction with the claimed idempotency key; it must
   lock aggregates with `select_for_update()` in stable order (check → lines → shift; branch counters last),
   validate, write, call `ctx.audit(...)` and `ctx.emit(...)` (outbox). Return a dict (201) or `(status, dict)`.
   Raise `DomainError`/`Conflict`/`StaleRevision`/`Forbidden`/`NotFound` from `apps/core/errors.py` — never
   return error bodies by hand. Errors are not stored, so the client may resubmit under a new key.
2. Financial, destructive or structural commands take `expected_revision` and bump `revision`. Appends do not.
3. Scope comes from the session (`branch_for`, `require(perm)`), never from client IDs. Foreign branch → 404.
   Add new permissions to `ROLE_PERMISSIONS` in `apps/core/models.py`; manager/owner have `*`.
   Manager approval for a non-manager uses `resolve_approver(principal, data, perm)` (username+PIN payload).
4. Money is integer minor units (`*_minor`, PKR ×100) everywhere; Decimal only at rounding (`round_half_up`).
   Client totals are ignored; the server prices (`Pricer`) and revalidates quotes under lock.
5. Kitchen/receipt output is a `PrintJob` created in the same transaction; never send to a printer inline.
   Only `failed_before_send` is retried automatically; anything uncertain → `outcome_unknown` + labelled COPY.
6. Flutter: persist the command (`AppState.savePending`) **before** sending; on `ApiError.uncertain` keep it
   and retry with the same key; on a definitive error drop it and keep the draft. Never infer success from a
   timeout. Touch targets ≥ 48px; left/right halves always spelled out in words.
7. Schema changes: `make` a migration (`manage.py makemigrations <app>`), never edit applied ones once shared.

## Adding an endpoint (checklist)

1. Service function in `apps/<app>/services.py` taking `(ctx, id, data)`.
2. View in `apps/<app>/views.py`: `branch_for` → `require` → `run_command(...)` (or plain Response for reads).
3. Route in `vibeorder/urls.py` under `branch = [...]` (relative to `/api/v1/branches/<branch_id>/`).
4. Tests in `backend/tests/` (see the `test` skill), including the conflict/permission path.
5. Flutter: call via `app.api.command(path, body, key: ...)` / `app.api.get(path)`; handle `ApiError.code`.
6. Update `Todo.md`.

## Corrections rules (M2)

- Never edit a sent line: void it and create a new line with `amends=<old>`; record a `CheckAdjustment`
  (financial delta, `discarded`, approver are separate facts).
- Every kitchen effect goes on a `change`/`remake` ProductionTicket with a plain-language note
  ("Table 6, pizza 1, RIGHT half: remove Olives"); KDS lists them under `changes` until
  `POST tickets/{id}/acknowledge`.
- Cooked (ready/packed/served) food cannot be modified; remove needs `check.amend_cooked`, remake needs
  `production.remake`, void needs `check.void`, merge needs `check.merge` (manager, or manager PIN in `approval`).
- Remakes never change the payable total. Corrections that would drop the total below paid → `refund_required`.
- Merge/void are pilot-limited to checks with no payments. Merge locks both checks in sorted-id order.
- Refunds (`apps/cash/refunds.py`): lock check, then tender; cap = tender − (succeeded + pending refunds);
  manager via `refund.approve`; card without terminal ref → `pending` until `refunds/{id}/resolve`.
  A refund never reopens a check: `balance_minor` is unchanged, see `refunded_minor` / `net_paid_minor`.
- Flutter: use `runCorrection()` (`lib/widgets/corrections.dart`) — it handles approval, food resolution and
  uncertain outcomes (same key on retry).
- Format Dart with 120 columns (`analysis_options.yaml` pins it; `dart format lib test`).


## Fulfillment, stock, cloud rules (M3–M5)

- Takeaway checks (`counter`/`pickup`/`delivery`) get a customer (by normalized phone), `promised_at` and, for
  delivery, a `Delivery` attempt + a `delivery-fee` charge line (round 0, no kitchen component). Never add
  kitchen components to the fee line; exclude it from discounts and repeat orders.
- Handover (`POST checks/{id}/handover`) is the packing gate: all live components ready, else `override_reason`
  + `pack.override`. Dispatch requires a handover. Delivered COD creates a `rider_cod` tender + `RiderCollection`
  and closes the check via `finalize_close`. Only a remittance touches the drawer (one movement).
- Stock: post through `stock.services.post()` with a unique `source_ref` (`ti:<item>`, `rv:<item>`,
  `pk:<item>`, `rc:<receipt>:<i>` …) — never `StockMovement.objects.create` directly. Consumption is posted in
  `dispatch_lines`/remake/reroute; a reversal only for items that never started or were replaced.
- Cloud: branch → cloud only through `SyncOutbox` events (`ctx.emit`). New projection? Handle the event type in
  `cloud.services.apply_event` (idempotent per event, applied in stream order). Online orders enter the branch
  only via `branch_sync.import_offer` (key = cloud request id) using `SystemPrincipal` + `run_internal(...,
  channel="web")`.
- Host fencing: `HostFenceMiddleware` blocks branch writes when `Branch.writable_host` ≠ `VIBEORDER_HOST_ID`.
  In tests change the branch with `Branch.objects.filter(pk=…).update(...)` — saving a stale `Branch` object
  resets `stream_position` and breaks the outbox.
- Reports live in `apps/reports/services.py` (`FUNCS`); every new report must work per branch and go through
  `as_json`/`as_csv` so the scope header is always present.
