# 03 · Persistence and API contract

## Core schema

Common mutable entities have UUID, tenant, branch where applicable, revision and timestamps. Enforce scoped relationships using database constraints or transactional validation plus tests. Use protected foreign keys and inactive flags for referenced catalog records. All monetary records carry currency and integer minor units. Store measured ingredient quantities in fixed precision decimal base units.

| Entities | Critical fields and invariants |
|---|---|
| Tenant, Branch, Staff, Membership, Device | Branch timezone, day cutoff, roles, device registration and revocation. Unique membership per operator and scope. |
| Area, DiningTable, Check | Table label unique in area. Check holds service type, business date, token, status and revision. Partial unique active occupancy per table. Released table keeps historical check relation. |
| MenuRelease, Product, Variant, OptionGroup, Option, DealSlot, BranchAvailability | Immutable published release; size, crust, hemisphere eligibility, defaults, min/max, slot cardinality, upgrade price and tax category. Availability overrides have reason, actor and optional expiry. |
| CheckLine, LineOption, DealSelection, Adjustment | Quantity, unit snapshot, applied menu release, labels, halves, component IDs, taxes and allocated discounts. Each line traces to its pricing inputs. Amendments point to prior line and reason. |
| ProductionTicket, TicketItem, PackItem, PrintJob | Station, round, ticket sequence, revision, quantity, status and fulfillment facts. Unique original dispatch per line/revision/station. Reprints reference original job. |
| Tender, TenderAllocation, Refund, DrawerShift, DrawerMovement | Positive integer amounts and explicit transaction kind, provider refs, approval, drawer and shift. Allocations reference charges; successful refund total cannot exceed captured amount less previous refunds. |
| Customer, Address, Delivery, RiderCollection, RiderRemittance | Address snapshot per delivery, fee, assigned rider, delivery status and separate cash liability. Remittance posts an offsetting drawer movement once. |
| StockItem, RecipeVersion, RecipeComponent, StockMovement, PurchaseReceipt | Base units, conversion factor, yield and signed quantity. Unique originating event/component reference for each consumption or reversal. |
| CommandReceipt, AuditEntry, SyncOutbox, SyncInbox, MenuActivation, OnlineRequest | Persist idempotency result and hash, ordered stream cursor, previous/next revision, operator, device and correlation IDs. Unique cloud request per imported branch order. |

## State transitions

- Check: open → closed after amount due reaches zero and the cashier completes settlement; open → voided with manager reason and explicit resolution of previously fired food and payments. Part-paid checks remain open. A later refund references a closed check without reopening it.
- Production item: held → queued → preparing → ready → packed or served. Allow queued/preparing → cancelled with corrective ticket. Remakes create a new linked production item. Whole-order “ready” requires all applicable noncancelled items ready or further advanced.
- Delivery: awaiting_assignment → assigned → dispatched → delivered or failed. An explicit retry creates a delivery attempt and retains the failed attempt record. Pickup uses ready → collected.
- Tender: pending → succeeded, failed or cancelled. Later refunds are independent records. Only successful tenders change paid balance. Cash collection and physical cash handover have different ledger owners.
- Drawer: open → counting → closed. Stop drawer postings while counting. Reopen counting to open with a reason or route new activity to another open drawer. A closed shift is immutable.
- Web order: submitted → offered → accepted or rejected/expired. Unknown branch outcome uses reconciling until resolved. Guest sees acceptance only after the branch commit.

## API surface

Prefix branch routes with /api/v1/branches/{branch_id}. UUIDs in actual payloads are opaque. Apply the write protocol in 02 to every business command. Use RFC 3339 timestamps, cursor pagination and stable ordering. Major reads return resource revision and server time. REST is authoritative; socket events contain IDs/revisions and signal clients to refresh.

| Method and relative route | Required inputs | Result or conflict |
|---|---|---|
| GET /bootstrap | Paired device and staff session | Branch, roles, permissions, stations, current release and active shift. |
| GET /floor; GET /checks/{id} | Scoped staff | Table occupancy or check with unsatisfied amount, rounds, options and current revision. |
| POST /checks | Service mode, optional table/customer, guest count | Open check; occupied table returns existing check reference to authorized operator. |
| POST /quotes | Release, proposed items, mode and deal selections | Itemized short-lived quote, currency, expiry and menu revision. |
| POST /checks/{id}/rounds | Quote, selected items, fire/hold flags and client line IDs | Appended lines, new revision and ticket IDs. Revalidate stock/sellouts and quote under lock. |
| POST /checks/{id}/amendments | Prior line, reason, action, expected revision | Adjustment and station change ticket, or approval-required error. |
| POST /checks/{id}/move; POST /checks/{id}/merge | Source and destination, observed revisions | Atomic occupancy changes or refreshed conflict snapshot. |
| GET /stations/{id}/queue; POST /ticket-items/{id}/transitions | Read filter or transition plus expected revision | Queue snapshot or updated ticket item. |
| POST /checks/{id}/tenders | Method, amount, allocations, expected revision, cash received if applicable | Tender status, change, balance and new revision. |
| POST /checks/{id}/close; POST /tenders/{id}/refunds | Expected revision; refund also includes amount, items, reason and approval | Closed receipt or linked refund. |
| POST /deliveries/{id}/transitions; POST /riders/{id}/remittances | State/attempt or cash and collection IDs, expected revision | Delivery history or remittance plus drawer entry. |
| POST /shifts; POST /shifts/{id}/count; POST /shifts/{id}/close | Drawer/float, counted amount or manager approval | Open shift, variance, signed close summary. |
| GET /devices/health; POST /print-jobs/{id}/reprints | Authorized operator; reason for reprint | Health by component or a new COPY job. |
| POST /sync/events; GET /sync/inbox; POST /sync/ack | Branch credential, stream position or command acknowledgment | Durable cursor or missing sequence range. These endpoints are cloud-hosted. |

### Worked append

The example uses readable fixture identifiers. Quote Q7 covers one large mixed pizza at the configured fixture prices in 04, without the family-deal drink.

```http
POST /api/v1/branches/B1/checks/C6/rounds
Idempotency-Key: e5cdb9d9-5ef7-4655-8364-a7e576bb936d
Content-Type: application/json

{"quote_id":"Q7","menu_release":"M7","items":[{"client_line_id":"L-phone-1","variant":"large","product":"mixed-pizza","quantity":1,"crust":"stuffed","halves":{"left":"tikka","right":"fajita"},"options":[{"id":"extra-cheese","side":"right"}],"fire":"now"}]}
```

```json
{"check_id":"C6","revision":8,"accepted_lines":["L1"],"ticket_ids":["K17"],"round_total_minor":190000,"currency":"PKR","production_status":"queued"}
```

Set authenticated staff/device identity via credentials. Ignore client-supplied totals. A successful new command returns 201 for a created resource, 200 for an update; replay returns the stored status and body. Errors share a JSON envelope with code, message, field_errors, current_revision and correlation_id. Use 403 for denied role, 404 for inaccessible resource, 409 for stale revision, occupied table or idempotency mismatch, 422 for invalid modifiers/expired quote or insufficient refundable amount, 503 for unavailable branch service. Never turn a timeout into an automatic fresh payment attempt.

### Settlement contract

Example request: {"method":"cash","amount_minor":190000,"cash_received_minor":200000,"expected_revision":8,"allocations":[{"line_id":"L1","amount_minor":190000}]}. A successful result has applied 190000, change 10000, balance 0 and revision 9. Closing against revision 9 issues a unique receipt. If another round wins first, reject the stale tender before posting any drawer movement. Additional public endpoints for M4: GET /store/{slug}/menu, POST /store/{slug}/quotes, POST /store/{slug}/orders and GET /track/{token}. Keep internal customer IDs, margin and staff details out of public serializers.
