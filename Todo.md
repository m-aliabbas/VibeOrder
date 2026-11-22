# VibeOrder · Todo

Working backlog against `docs/05-delivery-and-acceptance.md`. Tick an item only when a test or recorded live
check proves it. Status as of 2026-11-18 (M1–M4 software complete, M5 purchasing/stock core; 72 backend tests,
10 Flutter widget tests, `make smoke` incl. delivery + web order).

Legend: `[x]` done + tested · `[~]` partially done (see note) · `[ ]` not started · 🔌 needs physical hardware

## M1 · Vertical slice — software done, physical demo outstanding

- [x] Seed tenant, branch B1, stations (pizza, fryer, packing), 5 staff / 4 roles, tables, demo menu (`seed_demo`)
- [x] Device pairing by short-lived single-use code; per-staff PIN sign-in; lockout after failed attempts; revoke
- [x] Open check (one active check per table, occupied → returns existing check id)
- [x] Quote (server priced, 5 min expiry) + preview quotes for builders
- [x] Round append with idempotency key, revalidation under lock, sellout check, menu-release check
- [x] Kitchen routing by station, ticket items, KDS page (`/kds/`) with start/ready
- [x] Durable print jobs + worker (spooler fake + raw TCP ESC/POS), outcome_unknown, labelled COPY reprint
- [x] Drawer shift open, cash tender with change, external card reference, close check, receipt number + print
- [x] Blind count, variance with reason + manager approval, counting drawer rejects postings, closed immutable
- [x] Transactional outbox with gap-free branch stream positions; audit entry per command
- [x] Flutter staff app: pair, PIN login, floor, order screen (unsent vs sent), pizza builder, deal builder,
      held courses, cashier (F2/F3/F4), drawer, devices/print health
- [x] Draft + unacknowledged command persisted per branch+operator; retry reuses the same key
- [ ] 🔌 Demo: two real Android tablets add rounds with WAN unplugged; real 80 mm printer; branch restart keeps sale
- [x] Branch restart drill `scripts/restart_drill.py` (`make restart-drill`): runs the real supervisor, kills the
      server right after the round / tender / close commits (fault injection `VIBEORDER_CRASH_AFTER_COMMIT`, DEBUG
      only), tablet retries with the same key → original result replayed, one round / ticket per station / KOT /
      tender / receipt; then kills the whole supervisor (power loss) and restarts: sale intact, nothing reprinted.
      Recorded run on Windows 2026-11-19: `RESTART DRILL OK` (outages 6–12 s incl. restart backoff)
- [x] Unattended service packaging: `manage.py run_branch` supervisor (waitress `serve` + print worker + sync
      workers, restart with backoff, logs in `var/logs`, state in `var/run/supervisor.json`, single-instance lock),
      Windows Scheduled Task installer `deploy/windows/install-service.ps1` (boot start, no sign-in, restart on
      failure), Linux systemd unit, Caddy TLS reverse proxy config, `branch.env` loader + `branch.env.example`,
      `manage.py branch_doctor` pre-flight check. Health + alerts show crashing workers / stopped supervisor /
      failed backups (`test_branch_ops.py`). 🔌 install-service.ps1 not yet run on a real branch PC (needs admin)

## M2 · Shift operations

- [x] Floor view, rounds, fixed-price deal with slots/upgrades, split tender (by amount)
- [x] Held courses + explicit fire
- [x] Phone pickup / delivery check from counter (customer record, promised time, zone + address)
- [x] Permissions: role matrix in `ROLE_PERMISSIONS`; per-branch discount limit `Branch.discount_limit_bp`
- [x] Corrections/amendments: `POST /checks/{id}/amendments` remove / modify (re-quoted) / remake; reason; change
      ticket per station with plain-language delta; progress kept on modify; cooked food → remake or manager remove
- [x] Remake / discard recorded separately from financial adjustment (A09); remake charges zero
- [x] Void check: manager, reason, explicit fired-food resolution; refused when payments exist (refund first)
- [x] Move check to free table; merge (both locked, manager, before any payment, movement record) (A22)
- [x] Kitchen change acknowledgment on KDS (highlighted change cards until acknowledged) + printed change tickets
- [x] Staff app: line menu Change / Remove / Remake, check menu Move / Merge / Void, manager-PIN approval dialog,
      corrections list, voided lines kept visible
- [x] Discounts: one order promotion (`WEEKEND10`, `FLAT200` min, `STAFF50` needs approval) + line adjustments;
      cashier limit `Branch.discount_limit_bp` (10%), above → manager PIN; floor zero; largest-remainder allocation;
      tax after discount; re-applied on new rounds/corrections/merge; refused below paid (`test_discounts.py`)
- [x] Split by item in the cashier UI (tick lines → cash/card with allocations) + per-payer receipt
      (`POST /tenders/{id}/receipt`, `test_payer_receipt_lists_only_that_payers_items`, Flutter split test)
- [x] Refunds: `POST /tenders/{id}/refunds` with manager identity, reason, goods disposition, per-line reversal
      capped by original allocation; tender-locked cap incl. pending (A11); card w/o terminal ref → pending →
      `POST /refunds/{id}/resolve`; cash refund = drawer movement; refund slip printed; closed check stays closed
- [x] Cashier UI: Open/Closed bills, Refund per payment, pending refund Resolve; drawer summary shows refunds
- [x] Void after full refund allowed (void requires net paid = 0 and no pending refunds)
- [x] Phone customer lookup (masked `0300••••321`, ≥4 digits, `customer.lookup`) + repeat last order at current
      prices: price changes and sold-out items highlighted, address re-confirmed (A10 UI; backend + Flutter tests)
- [x] Quantities: draft +/- stepper, same simple item merges into one line, sent qty-N line `split` amendment
      (kitchen progress + stock kept, then change/remove/pay one), refund by item quantity
      (`test_split_two_pizzas_then_change_one`, Flutter quantities test)
- [x] KOT & receipt management: print log (`GET print-jobs?type=kot|receipts&problems=1&q=`), ticket content
      preview (`GET print-jobs/{id}`, copies listed), labelled COPY reprint with reason from any job, per-order
      prints on check detail, cashier Reprint receipt / Print bill, "KOT & receipts" screen
      (`test_print_log_filters_and_shows_ticket_content`, Flutter KOT log test)
- [ ] Customer receipt layout review with merchant
- [x] Golden close (spec 04): float 5,000 + cash 8,300 + rider remitted 2,000 − refund 300 − paid-out 500 =
      **14,500**, counted 14,450 → **−50**, card 4,000 excluded, rider outstanding **420** (`test_golden_close_full`)

## Simple service flow (2026-11-19)

- [x] General restaurant menu next to pizza: Karahi, BBQ, Rice, Naan & Roti, Raita/Salad/Lassi; new stations
      `kitchen` (hot kitchen) and `tandoor`; recipes with real quantities (e.g. mutton karahi half = 500 g mutton,
      full = 1 kg) and reorder levels; dev DBs get it as a published menu update (`ensure_general_menu`)
      (`test_recipe_consumes_mutton`)
- [x] Waiter pick-up: kitchen marks ready → waiter's floor shows "Ready to serve" per table + ready badge →
      `POST pickups/serve` marks served (`ticket.serve` for waiter/cashier) (`test_waiter_sees_ready_dishes_and_serves_them`)
- [x] Customer invoice: `GET checks/{id}/invoice` → signed link `/invoice/<token>` (Tax invoice when paid,
      Pro-forma before), A4 or 80 mm, print / save PDF; cashier **Invoice / Pro-forma** button
      (`test_invoice_link_renders_paid_invoice`)
- [x] Redesign: warm light theme, photo menu grid (`/media/menu/<code>.jpg`, gradient icon fallback), new sign-in
      with avatars and PIN dots, storefront thumbnails
- [x] 24 dish photos generated with Codex image_gen into `backend/media/menu/` (640 px JPEG, ~4 MB total)

## M3 · Delivery and recoverability

- [x] Packing checklist across stations (all live components incl. bought-in drinks, held lines shown as missing);
      handover requires all ready or an override reason + `pack.override` (manager) (A13); Flutter Dispatch screen
- [x] Delivery zones + fee (fee is a non-kitchen charge line so payments/refunds allocate to it), promised time,
      address snapshot, COD/prepaid, rider assignment, dispatch only after packing, delivered / failed with
      reason + food disposition (no silent reversal or stock), explicit retry = new attempt (failed kept)
- [x] Rider liability only for cash actually collected; remittance (manager) posts exactly one drawer movement,
      FIFO over collections, idempotent; rider sees only own active jobs (A14, `test_fulfillment.py`)
- [x] Backups: `make backup` / `make drill` — pg_dump (incl. outbox) encrypted with Fernet, restore drill into a
      scratch DB with key-table counts; host fencing (`Branch.writable_host` + `HOST_ID`, 503 `host_fenced` on
      writes), `manage.py take_over`, `GET /recovery` reconciliation report (A19, `test_ops.py`)
- [x] Health: `GET devices/health` shows backup age/stale/last drill, cloud backlog age, last sync, last error
- [x] Recipe consumption on first fire (idempotent per item), remake consumes again, never-started cancel is
      compensated, cooked cancel/refund creates no positive stock, packaging at handover (A21, `test_stock.py`)
- [x] Backup scheduling inside the supervisor (every `VIBEORDER_BACKUP_INTERVAL` min, default 15), retention
      (newest `VIBEORDER_BACKUP_KEEP` + first of each of `VIBEORDER_BACKUP_KEEP_DAILY` days), daily restore drill
      at `VIBEORDER_DRILL_HOUR` (`test_prune_keeps_newest_and_first_of_each_day`, restart drill backup step)
- [ ] 🔌 Restore onto a real spare host; install the services + Caddy on the pilot PC

## M4 · Direct ordering and branches

- [x] Cloud profile endpoints `/api/v1/cloud/sync/{events,inbox,ack}` with branch credential; dedupe by event id,
      apply in stream order, contiguous cursor, missing ranges, conflicting event quarantined (`SyncException`);
      branch uploader with exponential backoff + jitter (`manage.py sync_worker`, `make sync`) (A15)
- [x] Owner projection per branch/day from events only (sales, tenders, refunds, voids, web orders) with
      as-of position; owner overview shows release, last sync, unsynced events, stale flag
- [x] Menu authoring: clone release with price changes, preview a sample check, publish to selected branches
      (`MenuActivation`), others keep their release (A20)
- [x] Storefront `/s/demo/` (Django-rendered, mobile): branch choice, per-branch menu + sellouts, builder for
      pizza / half & half / deals, basket, pickup or delivery (zone fee), guest checkout COD, price-change consent
      (409 `price_changed`), idempotent submit, tracking page `/t/<token>` (unguessable) with progress
- [x] Online intake: offer to one branch with deadline → branch import (key = cloud request id) validates
      release, price, zone, availability, pause, deadline under lock; accept creates check + round + kitchen in one
      transaction; lost ack past deadline shows "Checking with branch", never re-offered; replay converges (A16)
- [x] Intake pause per branch and automatic pause on stale heartbeat (60 s)
- [x] Owner reports + CSV export with scope header: sales (net merchandise, returns on refund date), collections
      by tender/channel/day, voids & approvals, rider balances, late orders, service speed (median/p90),
      product contribution (recipe cost, missing costs flagged), till & variance (owner portal `/owner/`, branch
      `GET reports/<name>?export=csv`) (A17)
- [x] Prepaid web orders behind `VIBEORDER_ONLINE_PAYMENTS=1`: fake provider, HMAC-signed callbacks, duplicate
      callback ids processed once, amount mismatch kept pending, late success → `refund_required`, branch
      rejection of a paid order → `refund_required`; kitchen only after authorization (A23)

## M5 · Expansion

- [x] Purchase receipts (purchase units → base units, supplier ref), moving weighted average cost per branch,
      cost snapshot on consumption, losses (manager), physical returns, counts with variance, transfers between
      branches (sent → received with cost carried) (`test_receipt_moving_average_count_transfer_and_loss`)
- [x] Flutter Stock screen (manager): levels, receive, loss, count
- [x] Stock item master: create/edit items (`POST stock/items`, `stock/items/{id}` with `expected_revision`),
      base unit locked once stock has moved, per-branch reorder level → status ok / low / out; movement history
      per item; Stock → Levels tab with Low/out filter (`test_item_create_edit_reorder_level_and_unit_lock`)
- [x] Recipe management: `GET stock/recipes` (active recipes with cost from moving average, missing-cost flag,
      menu targets incl. sizes, **missing recipes** for the active menu), `POST stock/recipes` publishes version N+1
      and retires N (never edited; past consumption keeps its recipe), empty components = retire, stale
      `expected_version` → 409, version history (`?target_kind=&target_code=&size_code=`); Stock → Recipes tab
      with editor (`test_recipe_new_version_changes_consumption_and_keeps_history`,
      `test_missing_recipe_listed_and_retire`, Flutter recipes test)
- [x] Sold-out switches in the app: `GET availability` (who/why/since) + Stock → Sold out tab (products + options)
- [x] Alert centre `GET alerts` (computed live, disappears when fixed, filtered by role): stock low/out with the
      menu items still on sale → one-tap "Mark sold out"; missing recipes; late KOTs (warning, critical after
      10 min); change/remake tickets not acknowledged after 2 min; KOT/receipt print failures or unknown outcome
      (→ open ticket, reprint COPY); print jobs stuck in queue (worker down); pending card refunds; stale backup,
      cloud sync behind, fenced host. Thresholds `VIBEORDER_ALERT_*`. Alerts screen + nav badge (polls 20 s)
      (`test_alerts_low_and_out_of_stock_offer_sellout`, `test_alerts_late_kot_unacked_change_and_print_problems`,
      Flutter alerts test)
- [x] Purchase orders: suppliers, reorder suggestions (on hand + on order ≤ reorder → up to `par_level`, rounded
      up to purchase units, cost estimate), PO numbers `PO-B1-0001`, draft → sent → part received → received /
      closed short / cancelled, receive deliveries against the order (each delivery = linked `PurchaseReceipt`,
      same moving-average path), supplier message text to copy into WhatsApp; Stock → Orders tab
      (`test_purchasing.py`, Flutter PO test)
- [x] Count sheets: count many items while service continues — each line snapshots theoretical on-hand when
      counted, approval (manager / manager PIN) posts `counted − expected` so sales after counting are kept;
      one open count at a time, variance value per line and total; Stock → Counts tab
      (`test_count_sheet_while_selling_posts_variance_at_count_time`, Flutter count test)
- [ ] Marketplace import + settlement; loyalty; real online payment provider (needs merchant contracts)
- [ ] Automatic sellout from theoretical stock (needs merchant buffer + reconciliation process)

## M6 · Simpler sign in, kitchen screen, dashboard (2026-11-22)

- [x] Everyone signs in with **username + password** (`POST /api/v1/login`); PINs and the PIN pad are gone.
      Lockout after `VIBEORDER_LOGIN_MAX_ATTEMPTS` wrong passwords. Manager approvals use username + password.
- [x] **No device pairing.** The app/KDS registers itself at first sign in and keeps a random `device_key`;
      same key → same device record. Managers can still block a lost device (Devices → Block), which signs
      everyone out of it and refuses that key. `VIBEORDER_BRANCH` picks the branch for people at several
      branches; otherwise the app asks once (`409 choose_branch`). Session survives an app restart.
- [x] Staff accounts (manager): `GET/POST staff`, `POST staff/{id}` (name, roles, switch off, reset password),
      `POST me/password` (signs out the person's other devices). Flutter Staff screen + "Change my password".
      (`test_accounts.py`, Flutter staff test)
- [x] Kitchen display redesign: one card per ticket, live timer with green/amber/red urgency and progress bar,
      tap a line to start / mark ready, "Start all" / "Done" per ticket, All-stations view, sound on new orders,
      full screen, Ready rail. Sign in with username + password.
- [x] Manager dashboard with charts (`GET dashboard?days=1|7|14|30|90`, fl_chart): KPI tiles with change vs
      previous period, sales per day / by hour, busy hours, channel mix, payments, best sellers, staff, kitchen
      time. Totals reconcile with the sales report (`test_dashboard.py`, Flutter dashboard test).
      Demo data: `make history` (30 days of past orders, DEBUG only).

## M7 · Printing and ledgers (2026-11-22)

- [x] 80 mm thermal layouts (`apps/kitchen/layouts.py`): KOT (station, big table/type, order + KOT no., waiter,
      sent/due, bold double-height items, halves in bold, notes), bill ("THIS IS NOT A RECEIPT"), sales receipt
      (business name, branch, address, phone, NTN, receipt no., cashier, items with qty × rate and build details,
      discounts by name, tax, TOTAL, cash received / change, footer), payer receipt, refund slip with signature
      line, test page. Line markup `{B}{W}{H}{C}{R}{CUT}{DRAWER}` → ESC/POS (`escpos.py`); job log, previews and
      spool files show plain text. Cash receipts open the drawer; a COPY never does.
- [x] USB printers on the counter PC: transport `system` sends raw ESC/POS to the installed Windows printer by name
      (pywin32; CUPS `lp -o raw` on macOS/Linux). Printer missing → `failed_before_send` (safe retry).
- [x] Print queue fairness: a job that failed before sending waits a backoff (2 s … 60 s) so one broken printer
      no longer starves the other printers (found by the new USB test).
- [x] Printer setup (manager): `GET/POST printers`, `POST printers/{id}`, `POST printers/{id}/test`, installed
      printers list, receipt header/footer (`POST receipt-settings`: address, phone, NTN, last line). Flutter
      Devices → Printer setup.
- [x] Supplier payments (`POST stock/suppliers/{id}/payments`: cash from drawer → paid-out movement in the same
      transaction, bank, wallet, cheque); supplier balance owed in `GET stock/suppliers`.
- [x] A4 ledgers (`GET ledgers/{cash|sales|supplier|customer|stock}`, signed 24 h print link `/print/ledger/…`):
      cash (per shift, running balance, close/variance), sales (every closed bill + totals by payment), supplier
      (opening, goods received, payments, balance owed), customer (orders vs payments, drill into one customer),
      stock (opening/in/out/closing + value, drill into one item). Flutter Ledgers screen with date range,
      drill-down, Pay supplier, Print A4. (`test_printing_ledgers.py`, Flutter ledgers + printer tests)
- [ ] 🔌 Print on the real USB thermal printer at the branch (install the Windows driver, then Printer setup → Test)
- [ ] Urdu on thermal paper needs a raster (image) print path; ESC/POS text mode is Latin only.

## M8 · Look and feel (2026-11-22)

- [x] Generated photography (Codex image_gen, no text/faces): login hero, splash oven, dashboard spice banner, KDS
      kitchen. Originals in `app/assets/images/raw/`, bundled JPEGs in `app/assets/images/` (see its README).
- [x] Animated splash (brand mark scales in, name fades up) while the app starts / restores the session;
      `VibeOrderApp(splash: Duration.zero)` in widget tests.
- [x] `PhotoBackdrop` (photo + ember glow + gradient overlay, gradient fallback if a file is missing), `GlassPanel`
      (frosted card), `BrandMark`, `HeroBanner` in `lib/widgets/visuals.dart`. Login: photo brand panel with feature
      chips on wide screens, full-screen photo + frosted card on phones. Dashboard: greeting banner. KDS sign in:
      kitchen photo + frosted card (`backend/media/ui/kitchen_bg.jpg`).

## Acceptance catalog status

| ID | Status | Evidence |
|---|---|---|
| A01 | ✅ | `test_a01_two_waiters_open_same_table` (2 real DB connections) |
| A02 | ✅ | `test_a02_concurrent_rounds_on_same_check` |
| A03 | ✅ | `test_a03_retry_returns_original_result`, `test_concurrent_identical_retry_single_write`, Flutter `A03/A06` widget test, smoke |
| A04 | ✅ | `test_a04_reused_key_with_changed_topping_conflicts` |
| A05 | ✅ | `test_a05_stale_tender_has_no_side_effects` |
| A06 | ✅ (software) | Flutter widget test: draft scoped to operator; 🔌 real Wi-Fi drop outstanding |
| A07 | ✅ (software) / 🔌 | LAN-only selling by design; owner overview flags a stale branch (`stale`, last sync); physical WAN unplug outstanding |
| A08 | ✅ | `test_a08_print_crash_marks_outcome_unknown_and_copy_is_labelled` |
| A09 | ✅ | `test_a09_amend_half_after_cook_starts_then_remake`, smoke (live amend + acknowledge) |
| A10 | ✅ | `test_a10_sellout_after_quote_rejected_and_snapshot_kept`, `test_a10_phone_lookup_and_repeat_highlights_changes`, Flutter A10 repeat test |
| A11 | ✅ | `test_a11_two_simultaneous_refunds_stay_within_refundable` (2 DB connections), pending card refund reserves |
| A12 | ✅ | `test_pricing.py` fixtures + `test_a12_family_deal_delivery_total_2420` + `test_golden_close_full` (14,500 / −50 / 420) |
| A13 | ✅ | `test_a13_packing_needs_all_components_or_override`, Flutter A13 override test |
| A14 | ✅ | `test_a14_prepaid_cod_failed_and_remittance` (one drawer movement, replay safe) |
| A15 | ✅ | `test_a15_retransmitted_batch_counted_once_and_gaps_wait`, `test_a15_web_acceptance_replay_one_order_one_dispatch` |
| A16 | ✅ | `test_a16_ack_lost_past_deadline_converges_on_acceptance`, `test_expired_offer_received_late_is_rejected` |
| A17 | ✅ | `test_a17_scope_reports_exports_and_sync_revocation` (+ foreign branch 404, device revocation earlier) |
| A18 | ✅ | `test_a18_counting_drawer_rejects_postings_and_variance_needs_manager` |
| A19 | ✅ (software) | `test_backup_is_encrypted_and_restore_drill_verifies`, `test_a19_fenced_host_rejects_writes_until_take_over`; 🔌 spare host |
| A20 | ✅ | `test_a20_release_at_b1_b2_keeps_menu_and_local_sellout` |
| A21 | ✅ | `test_a21_consumption_once_remake_and_cancel`, `test_cancel_before_preparation_reverses_and_packaging_at_handover` |
| A22 | ✅ | `test_a22_held_course_move_merge_then_split` (held fires alone, kitchen sees new table, merge, item split) |
| A23 | ✅ (fake provider) | `test_a23_prepaid_duplicate_and_late_callbacks` |
| A24 | ✅ | `test_a24_business_day_cutoff` (postings on each side of cutoff; refund on its own day, sale day kept) |
| A25 | 🔌 | on-site tablet/printer/LAN survey |

## Known gaps / tech debt

- Merge still requires no payments. Tender allocations on a removed/changed line are not moved to the new line.
- A void after refund records its own −total adjustment; reports must not double-count it with refund allocations
  (sales report counts closed checks only, so a voided check never appears in net sales).
- Modify-while-preparing reverses the old item's consumption and consumes the new spec (net delta), even if
  some ingredients were already on the pizza.
- Move/merge change tickets notify stations with live items only.
- Table is released as soon as its check closes (spec allows an explicit release step).
- Alerts are computed on read (no history, no push/SMS/sound). A per-device "seen" state and an audible KDS
  alert for late tickets would be the next step.
- Notifications are polling only (floor 5s, order 6s, KDS 2s, dispatch 5s, tracking 4s). WebSocket/SSE later.
- Staff session token is kept in memory on the app: a reload/restart asks for the PIN again (drafts survive).
- ESC/POS TCP transport sends cp437 text; Urdu needs rasterized printing (spec: rasterize when font lacks glyphs).
- `print_worker` / `sync_worker` log to stdout; add structured logging with command IDs, queue lag, app version.
- Expected-cash visibility: cashier sees expected only after counting; manager always sees it.
- No API schema/OpenAPI doc yet; add drf-spectacular and diff it per release.
- Load test (20 rounds/min, p95 < 500 ms append) not run yet.
- Owner portal and storefront are plain Django templates + JS (no build step); Urdu/RTL storefront later.
- PO receiving does not yet print a goods-received note; supplier price lists / last price per supplier are not
  tracked (cost comes from what was entered on delivery).
- Count sheets count every active item (API accepts an item list; no UI to pick a subset or a storage area yet).

## Deviations from spec (record rule, reason, test)

- Pairing codes are 6 digits valid 10 min (seed codes 24 h for dev). Spec only says "short-lived single-use".
- Command errors (4xx) are not stored in `CommandReceipt`; a retry with the same key re-executes validation.
  Reason: lets the client refresh and resubmit cleanly; successful results are always replayed. Tests: A03/A04.
- Quote total mismatch at commit returns `422 quote_stale` (the spec lists 422 for expired quote).
- Cloud and branch profiles share one database in dev/test (separate tables: `cloud_*` vs branch apps); the
  storefront reads the published catalog and branch sellouts directly. Production must run the cloud profile on
  its own instance and replicate catalog/sellouts as events. Tests: `test_cloud.py` exercise the HTTP sync API.
- Delivery fee is stored as a `delivery-fee` check line (round 0, no kitchen component) rather than a separate fee
  record, so payment allocation and refunds work unchanged. Tax on fees is 0 (pilot profile).
- Owner sign-in uses username + PIN (`/api/v1/owner/sessions`); managers may sign in and see their branch only.
- Report CSV parameter is `?export=csv` (DRF reserves `format`).

## Decisions still needed from the pilot merchant

Real menu + deal examples, mixed-pizza policy, printer model/driver, tablet models, tax/invoice profile,
drawer sharing, delivery zones, retention rules (see docs/05 §Decisions).
