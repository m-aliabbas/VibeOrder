---
name: test
description: Test and verify VibeOrder changes. Use after any backend or Flutter change, when asked to run tests, check acceptance cases (A01–A25), or prove a milestone. Covers pytest against PostgreSQL (incl. real multi-connection concurrency), Flutter widget tests with the fake backend, the live smoke script, and visual checks.
---

# VibeOrder testing

Always report expected vs observed. A milestone item is done only when its acceptance case passes
through the real interface (HTTP + DB + worker), not a mock alone.

## Commands

```bash
make test-backend   # cd backend && .venv/bin/python -m pytest   (~25s, needs postgres running)
make test-app       # flutter analyze && flutter test
make smoke          # live E2E against a running `make server` (signs in with the demo passwords)
```

Single test: `cd backend && .venv/bin/python -m pytest tests/test_protocol.py -k a05 -x`.
After a migration change run once with `--create-db` (pytest.ini uses `--reuse-db`).
Postgres down? `brew services start postgresql@17`.

## Backend test layout (`backend/tests/`)

- `conftest.py` – `demo` fixture seeds deterministic data (`apps/core/fixtures_demo.py`, uuid5 IDs);
  `Staff` helper signs in with username + password (`/api/v1/login`) and sends `Idempotency-Key` automatically
  (`post(path, data, key=False)` for non-commands, `key=<uuid>` to force a retry). Fixtures: `waiter`,
  `waiter2`, `cashier`, `cook`, `manager`. Helpers `open_table`, `add_round`, constant `MIXED_PIZZA`
  (spec fixture: large tikka/fajita, stuffed, right-half cheese = PKR 1,900).
- `test_pricing.py` – A12 commercial fixtures from spec 04 (exact minor units).
- `test_m1_flow.py` – M1 vertical slice over HTTP incl. kitchen queue, spooler output, cash, close, outbox order.
- `test_protocol.py` – A01–A05, A08, A10, A17, A18, login lockout, print recovery.
  Concurrency tests use `@pytest.mark.django_db(transaction=True)` + `_run_parallel` (threads, separate DB
  connections, barrier) — use this pattern for any new race (A11 refunds, merges, etc.).
- `test_corrections.py` (A09, A22, payer receipt), `test_refunds.py` (A11, golden close drawer),
  `test_discounts.py`, `test_fulfillment.py` (A10 repeat, A12 deal+fee, A13, A14, full golden close),
  `test_stock.py` (A21, purchasing), `test_ops.py` (A19 backup/drill/fencing),
  `test_cloud.py` (A15, A16, A17, A20, A23, A24) — `CloudLink` drives the real `/api/v1/cloud/sync/*` endpoints
  with the seeded dev credential (`demo["cloud_tokens"]`); `fail_ack=True` / `down=True` simulate lost acks and an
  unreachable cloud. Storefront needs a heartbeat: call `sync_once(branch, link)` before placing an order.
- Time-dependent cases (A24): patch `django.utils.timezone.now` and move the branch cutoff relative to real now
  (sessions are created at real time; don't jump more than a few hours).
- Print path: call `apps.kitchen.services.process_one(send=fake)` to simulate transports; a raising
  `PrintTransportError` = known not sent; any other exception = outcome unknown.

New acceptance case → name the test `test_aNN_<what>` and tick it in `Todo.md`.

## Flutter tests (`app/test/`)

- `fake_backend.dart` – `MockClient` branch server using real payloads in `test/fixtures/*.json`
  (regenerate with `cd backend && .venv/bin/python scripts/smoke.py --dump-fixtures ../app/test/fixtures`
  when menu/serializers change). `dropNextRoundResponse` simulates commit-then-lost-response.
- `app_test.dart` – username + password login (device key reused), dashboard, staff accounts, pizza builder (LEFT/RIGHT words, spec item), A03/A06 retry-same-key and
  operator-scoped drafts. Set `tester.view.physicalSize` for tablet/desktop layouts; `pump()` after taps
  before asserting text.

## Live / visual verification

1. `make server`, `make worker` and `make sync` in the background, `make smoke` → must print `SMOKE OK`
   (covers dine-in, amend, cash, refund, delivery COD + rider remittance, and a storefront order accepted via
   the sync worker; `--skip-web` without a sync worker).
2. Kitchen tickets: `backend/var/spool/kitchen-pizza/*.txt`, receipts: `backend/var/spool/counter-receipt/`.
3. UI: `make app-web` (port 8081) and open in Playwright; sign in with a demo account (e.g. zara / zara123); drive canvas with
   `page.mouse.click`. KDS at http://127.0.0.1:8000/kds/ (HTML selectors work).
4. Guest/owner pages are plain HTML: storefront `/s/demo/B1/`, tracking `/t/<token>`, owner `/owner/`
   (ayesha / 8888). Check them at phone width (375 px) and desktop.
5. Backups: `make drill` must print `restore drill ok=True` with restored counts.
6. Physical-device checks (real tablets, 80 mm printer, WAN unplugged, Urdu on paper) cannot run here —
   list them as outstanding in `Todo.md` rather than claiming them.
