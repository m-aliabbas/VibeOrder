# 05 · Delivery, verification and release

## Milestones

| Gate | Deliverable | Demonstration required |
|---|---|---|
| M1 · Vertical slice | Seed tenant, branch, stations, three staff roles, two devices, sample menu and table. Implement pairing/login, open check, quote, round append, kitchen queue/printing, cash payment, drawer and receipt. | Two actual waiter tablets add rounds while WAN is disconnected. Counter settles, printer produces correct tickets, branch restart preserves one sale. |
| M2 · Shift operations | Floor, rounds, deals, corrections, permissions, split tender, refunds, held courses, phone pickup, kitchen change acknowledgments and drawer reconciliation. | Run the pizza fixtures and golden close with a cashier, waiter and manager. Fix any unexplained balance or duplicate production item. |
| M3 · Delivery and recoverability | Packing, delivery zones, rider collection/remittance, durable jobs, backups, health, updates and restore. | Combine counter, card reference and COD in one shift. Restore onto a spare host and reconcile jobs, drawer and rider balances. |
| M4 · Direct ordering and branches | Authenticated replication, menu publication, storefront, branch acceptance, owner reports and cloud freshness. | Two branches, different sellouts, one interrupted internet link, one accepted web order. No cross-branch leak or duplicate dispatch. |
| M5 · Expansion | Receiving, transfers, recipe costing, stock counts, marketplace import/settlement, online provider integration, loyalty. | Each enabled module has real adapter or explicit manual operation, reconciliation, reversal and integration tests. |

Loyalty implementation: append points earned or redeemed per finalized eligible order, with reversal on qualifying return. Enforce unique posting per order/rule and lock customer balance during redemption. Define earn rate, expiration and eligibility per tenant before enabling. Marketplace implementation: map external item and option IDs into a versioned menu, quarantine unrecognized mappings for staff resolution and preserve external order/payment IDs. A repeated import must return the original internal order. Configure fees and dispute handling from the actual merchant contract.

## Acceptance catalog

Run transaction/concurrency tests against PostgreSQL with independent connections, plus HTTP tests, Flutter widget/integration tests and branch/cloud process interruption tests. Use a spooler fake for deterministic CI and the physical printer for commissioning. Keep expected and observed outcomes distinct.

| ID | Setup / action | Pass condition |
|---|---|---|
| A01 | Two waiters simultaneously open Table 6 | One active check. Both devices can recover its ID without creating a second occupancy. |
| A02 | Both add distinct rounds against the same open check | Both survive with distinct tickets. Ordered revisions strictly increase. |
| A03 | Server commits a round; drop response; restart tablet; resend | Original receipt/ticket returned. One kitchen job and one audited sale addition. |
| A04 | Reuse command key with a changed topping | Conflict, no second write. Operator can submit revised intent using a new key. |
| A05 | Waiter adds dessert while cashier settles based on old total | If append wins, stale tender has zero side effects. If close wins, append is refused. Paid checks cannot acquire an unnoticed balance. |
| A06 | Disconnect tablet Wi-Fi, compose pizza, switch user and reconnect | Original draft persists under its owner. New waiter cannot send it as their own. Kitchen receives it only after explicit valid send. |
| A07 | Unplug WAN with branch LAN intact | Existing authorized staff can sell, print, cook and take cash. Owner portal shows stale branch timestamp. |
| A08 | Printer accepts bytes; kill worker before it records handoff | Job becomes uncertain; staff can verify paper and issue one marked COPY. No automatic blind re-fire. |
| A09 | Amend half topping after cook starts; remake after product is cooked | Correct station sees delta and acknowledges. Original receipt trail retained. Only explicitly charged adjustment changes payable total. |
| A10 | Change menu release mid-shift; repeat yesterday's order | Existing line remains at snapshot price. New quote reflects new menu, and sold-out option is highlighted. |
| A11 | Post two simultaneous refunds against same tender | Cumulative successful and reserved refunds stay within refundable balance. Uncertain provider result remains pending for reconciliation. |
| A12 | Exercise all commercial fixtures in 04 | Exact minor-unit totals, tax, change, split allocations and rider figures match. |
| A13 | Mark pizza ready while drink outstanding | Packing still shows drink. Handover needs all required components or a recorded override. |
| A14 | Deliver prepaid, collect COD, fail another delivery | Only collected COD creates rider liability. Remittance reduces liability and raises drawer once. |
| A15 | Cloud retransmits event batch and branch replays web acceptance | Owner projection counted once, one branch order, one production dispatch. |
| A16 | Web offer accepted before deadline, acknowledgment lost past deadline | Reconciliation converges on original acceptance. No duplicate order, cancellation or reassignment on timeout alone. |
| A17 | Revoke device/user; request foreign branch check, report and export | Local revocation immediately effective at issuing branch. Sync revocations take effect on receipt; offline expiry enforced. All endpoints enforce scope. |
| A18 | Count drawer with mismatch; attempt new posting during counting | Variance requires manager reason and identity. Counting drawer rejects new entries; closed values remain immutable. |
| A19 | Restore backup while old branch host is isolated | Exactly one writable branch host. Compare accepted orders, pending sync, cash and print exceptions with physical records before resuming. |
| A20 | Apply release at B1; B2 stays on previous menu and has one local sellout | Branch-specific quote, availability, dispatch and reporting all match the branch chosen by the guest. |
| A21 | Repeat a recipe fire event; remake pizza; cancel after cooking | Original consumption once, remake once, financial reversal alone creates no positive stock. |
| A22 | Held course, move table, merge eligible checks, then split payment | Only released course fires. Kitchen sees correct table changes. Guest items, receipts and payment allocations remain traceable. |
| A23 | Prepaid gateway sends duplicate or late callback after timeout | Authenticated matching event processed once. Ambiguity is reconciled and failed fulfillment routes to void/refund. |
| A24 | Sell on both sides of configured business-day cutoff | Per-posting day, shift totals and original sale-to-refund link remain correct. |
| A25 | Test tablet at counter, kitchen and farthest dining table | Staff can read modifiers, see loss of connectivity and recover a draft. Log printer model, driver, tablet OS and LAN coverage observations. |

## Release evidence

Per milestone, publish the changed modules, migration notes, API schema diff, test commands, fixture results and short operator walkthrough. Include fixtures with deterministic IDs, seeded branch roles and a teardown path for disposable environments. Check migrations on a prior schema containing completed historical sales. Production deploys must create no sample customers or orders.

Proposed pilot performance targets, to validate on selected hardware: 10 active staff clients, a 300-item menu, 20 confirmed rounds per minute for 15 minutes, p95 local append acknowledgment under 500 ms and committed ticket visible on the kitchen screen within 2 seconds at p95. Exclude human interaction time and physical paper motion from those intervals. Report dataset, device, network, sample size, percentiles and any lost/duplicated events. Correctness conditions apply at every load tested.

## Decisions to obtain from the pilot merchant

| Decision | Build-time default | Production evidence |
|---|---|---|
| Devices and host | Two Android tablets, Windows cashier client, branch host on LAN; simulate missing hardware in CI | Device/OS inventory, actual printer/driver, restart and unattended startup test |
| Menu policy | Higher-priced half, explicit side prices, one fixed deal as in 04 | Owner-approved examples for halves, upgrades, discount stacking and receipt layout |
| Fiscal and payment configuration | Synthetic tax fixtures, cash and manual external reference | Applicable authority and invoice profile, merchant accounts, refund process and verified samples. Verify local obligations for each pilot branch. |
| Cash and staffing | Single drawer, operator logins, manager review of refunds and close variances | Actual drawer sharing, rider handover and shift cutoff signed off during pilot |
| Fulfillment and sync | Manual rider assignment, cloud intake timeout configurable, outbox replay | Agreed delivery polygons/areas, promised times, capacity and stale-branch thresholds |
| Retention and recovery | Data minimization, encrypted backups, scoped guest access | Merchant retention rules, restore results, recovery contacts and replacement-host procedure |

## Reference material

The architecture and thresholds in this pack are design decisions. Official starting points for implementers include [Flutter platform support](https://docs.flutter.dev/reference/supported-platforms), [Flutter platform channels](https://docs.flutter.dev/platform-integration/platform-channels), [Django transactions](https://docs.djangoproject.com/en/stable/topics/db/transactions/) and [Punjab's Restaurant Invoice Monitoring System](https://pitb.gov.pk/rims). For competitive context see [OnlineOrder.pk pricing](https://onlineorder.pk/pricing/) and [HungerRush pizza and delivery](https://www.hungerrush.com/who-we-serve/pizza-and-delivery/).
