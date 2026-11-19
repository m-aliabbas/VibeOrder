# 02 · Architecture and operational behavior

## Deployment and ownership

Use one Django modular monolith with branch and cloud deployment profiles. Suggested modules are identity, catalog, sales, production, payments, dispatch, stock, sync and reporting. Flutter shares API types, session handling and reusable order widgets across staff clients. Keep the floor, kitchen and cash close screens role-specific. A branch install consists of an application service, PostgreSQL, a durable worker and a reverse proxy with TLS. Package service startup, database migration, health and backup steps for the chosen host. A dedicated local Linux host or a supported service installation on the operator's Windows PC is a deployment choice to confirm during the pilot.

| Authority | Owns | Replication rule |
|---|---|---|
| Cloud | Tenant, published catalog versions, central roles, online requests and provider callbacks | Branch pulls signed/authenticated configuration and inbox commands. Display configuration revision and last successful update. |
| Branch | Table occupancy, accepted orders, current sellouts, production, local tenders, drawers and riders | Branch commits first, then exports immutable events. Cloud reports carry a last-synced watermark. |
| Tablet | Operator draft and unacknowledged submission identity | Persist locally across restart under operator/branch scope. Clear after confirmed acceptance or explicit discard. |
| Provider | External transaction outcome | Save verified callback or reconciliation reference. Staff recording an external terminal receipt is a separate tender type. |

Branch and cloud use distinct database instances. Keep a single writable branch authority for a given check. Normal WAN failure leaves all branch devices on the same LAN. Total loss of the branch host puts clients into draft-only operation. Provide a paper contingency and a manager reconciliation procedure using uniquely numbered manual tickets. Recovery imports a manual ticket with its original time and an explicit “already prepared” flag to prevent firing it again.

## Write protocol

1. Persist a unique command UUID on the client before sending. Bind it to operator, device, branch, intent and canonical payload hash. An uncertain response reuses exactly that command.
2. In a database transaction, authorize, claim the unique command key, lock the affected aggregate rows in stable ID order, validate current state and apply the business operation. Persist revision, result, audit entry, outbox event and any print job in the same commit. Handle concurrent claims with a unique constraint and reread the committed result.
3. For an identical retry return the original successful result. A reused key with a different request yields a conflict. A rejected stale quote is refreshed under a new command. A deliberately repeated identical item is a new command.
4. Concurrent append operations apply independently to the same open check. Financial, destructive or structural commands carry the observed revision and fail on mismatch. At final settlement lock the check and recompute balance before accepting the tender. Closed checks reject all appends.
5. Notifications wake clients after commit. On disconnect or app resume, reload the authoritative snapshot or request events since a cursor. Polling is a valid MVP transport. An event timeout must never cause the UI to guess whether a sale succeeded.

## Synchronization and online intake

Use transactional outbox and inbox tables with event ID, branch, stream position, schema version, aggregate ID, revision, payload, attempts and timestamps. Allocate branch stream positions under a transactionally locked branch counter so commits cannot appear behind an acknowledged cursor. Batch uploads over outbound TLS. Cloud deduplicates event IDs, updates projections and persists its cursor atomically. Acknowledge only contiguous positions; retain pending gaps and request replay. Back off with jitter, expose backlog age and retry errors, and preserve records until acknowledged and included in backup retention. One corrupt event goes to an operator-visible exception queue and blocks cursor advancement past its gap.

For online intake, use a cloud request ID as the branch import key. Branch validates menu version, price, zone, availability and acceptance deadline under lock; accepts once or rejects with reason. Acceptance and kitchen jobs share a transaction. The cloud retries an uncertain receipt using the original ID. If acknowledgment is missing at the deadline, display “Checking with branch” until reconciled; prevent a second offer to another branch until the first branch has confirmed rejection or absence after expiry. Reject newly received expired offers. Pause new ordering on stale branch heartbeat using a configurable threshold (pilot proposal 60 seconds). Never promise a new total for a price mismatch without guest consent.

Later prepaid flow: separate provider authorization, branch acceptance, capture and fulfillment release. Hold dispatch to kitchen until the selected payment policy allows it. Confirm provider state through authenticated webhook or server reconciliation, deduplicate callback IDs, reject mismatched currency/amount and keep ambiguous outcomes pending. Define void/refund compensation for authorizations or captures on expired orders. Integration tests must exercise late success and duplicate callbacks before enabling a provider.

## Printing and hardware

Assign each printer a branch, station, transport, queue name or IP, paper width and tested capabilities. Route jobs by station; snapshot the rendered content or template version plus immutable data. A worker leases a queued job, sends once and records the spooler reference. Distinguish queued, sending, handed_to_spooler, failed_before_send and outcome_unknown. Automatic retry is safe only before handoff is known to have started. After a crash during transmission, let the operator check the physical ticket and request a labeled reprint. A spooler acceptance does not prove paper emerged. The KDS remains usable during a printer outage. Test the actual 80 mm printer, cutter, mixed-script text, long item wrapping, router restart and recovery from paper-out. Thermal customer receipts, kitchen tickets and drawer kicks have separate permission and routing rules.

## Security, deployment and maintenance

- Pair each staff device by short-lived single-use branch code. Install the branch trust certificate or use a valid managed hostname and certificate. Staff sign in individually. Support PIN/lock on shared devices, throttled attempts and revocation. Cache only necessary offline authorization at the branch with a configurable expiry and explicit last-sync time. Disable departed staff locally as soon as known.
- Derive tenant and branch scope from authenticated membership. Enforce it in querysets, object permissions, exports, command handlers, device pairing and background tasks. A guessed ID from another branch returns no data. Keep payment credentials off staff devices; exclude card data, guest addresses and tokens from application logs.
- Use a branch-assigned hostname, address reservation, wired host and printer, protected staff Wi-Fi and tested power backup. Store deployment secrets outside source control. Provision cloud and branch credentials separately and rotate them without re-enrolling every waiter.
- Back up the local database, unreplayed outbox and configuration to encrypted storage. Proposed pilot objectives: maximum 15 minutes of data loss with functioning backup storage, restore within two hours on a spare host. Show last successful backup and verify by restoring. A WAN outage requires local backup media or a second local target for the same objective.
- On branch host replacement, fence the old host before enabling writes. Reconcile restored cursors against cloud receipts. If restoration predates acknowledged events, recover missing transactions from verified durable history or hold affected checks for manager reconciliation. Reconcile physical receipts, print jobs and unconfirmed payment references before resuming them.
- Version API and event schemas. Support the previous deployed client during rollout; add fields before making them mandatory. Stage database migrations on a restored production-shaped copy. Log structured command IDs, queue lag, printer backlog, last cloud sync and app version. Expose separate liveness and database readiness probes.
