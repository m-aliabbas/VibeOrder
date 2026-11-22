# VibeOrder · Restaurant and pizzeria system

## Quick start (M1–M4 software complete; see Todo.md for physical checks outstanding)

Requires PostgreSQL 17 (`brew services start postgresql@17`), Python 3.13 with `uv`, Flutter 3.44.

```bash
make setup     # venv, deps, databases, migrations, demo data (prints the demo usernames + passwords)
make server    # branch API + kitchen display at http://localhost:8000/kds/
make worker    # print worker; tickets land in backend/var/spool/
make sync      # branch<->cloud sync (owner figures + storefront orders reach the branch)
make app-web   # staff app at http://127.0.0.1:8081 (or: make app-macos)
make test      # backend pytest + flutter analyze/test
make smoke     # live end-to-end run against the server
make drill     # encrypted backup + verified restore into a scratch DB
```

Guest storefront: http://localhost:8000/s/demo/ · Owner portal: http://localhost:8000/owner/ (ayesha / ayesha123)

Demo sign in (username / password): bilal / bilal123, sana / sana123 (waiters) · hamza / hamza123 (cashier) ·
usman / usman123 (cook) · zara / zara123 (manager) · faisal / faisal123 (packer) · imran / imran123 (rider) ·
ayesha / ayesha123 (owner, B1 + B2). Managers add people and reset passwords in the app → Staff. Full list: [DEMO_LOGINS.md](DEMO_LOGINS.md).

| Path | Contents |
|---|---|
| `backend/` | Django + DRF: core, catalog/pricing + menu authoring, orders/corrections/discounts, kitchen/printing, cash/refunds, fulfillment (packing, delivery, riders), stock, ops (backup, fencing), cloud (sync, storefront, owner, payments), reports; templates for KDS, storefront, tracking, owner portal |
| `app/` | Flutter staff app: username + password sign in, manager dashboard with charts, staff accounts, floor, order + pizza/deal builders, corrections, cashier (discounts, split by item, refunds, phone/delivery orders), dispatch, drawer + rider cash, stock, devices |
| `Todo.md` | Backlog and acceptance-case status per milestone |
| `.claude/skills/dev`, `.claude/skills/test` | How to develop and verify in this repo |

---

# Implementation specification

Engineering handoff v1.0 • 18 November 2026

Build a Pakistan-focused restaurant system that can run an entire shift with the internet disconnected. A waiter enters a pizza on a tablet, the kitchen receives an unambiguous ticket, the cashier takes the correct payment and the manager can explain the closing balance. Web orders join that same production workflow when the branch acknowledges them.

## Reading order

| File | Use it for |
|---|---|
| [docs/01-product.md](docs/01-product.md) | Actors, service workflows, screen behavior, scope and business rules |
| [docs/02-architecture.md](docs/02-architecture.md) | Deployment, ownership, transactions, sync, hardware and recovery |
| [docs/03-data-and-api.md](docs/03-data-and-api.md) | Entities, constraints, endpoints, commands and error behavior |
| [docs/04-commercial-rules.md](docs/04-commercial-rules.md) | Pricing fixtures, split bills, cash, inventory and reports |
| [docs/05-delivery-and-acceptance.md](docs/05-delivery-and-acceptance.md) | Build sequence, test scenarios, deployment decisions and release evidence |

## Working agreement for Opus and Codex

Paste the following into either coding agent after adding this directory to the target repository.

> Read README.md and the five numbered specifications in docs/. Inspect the existing application, dependency lockfiles, migrations, tests and repository instructions. Map the first unfinished milestone to existing entry points. Implement that milestone through the actual staff interface, database and branch processes. Reuse compatible modules. Preserve existing public contracts and historical records. Run the milestone's acceptance cases, record commands and actual results, and identify physical-device checks still outstanding. Keep the next milestone independently reviewable. Use the documented defaults for synthetic tests. Record deviations with the affected rule, reason and tests. Keep live payments and fiscal integrations disabled until the merchant-specific configuration has been verified.

When using both agents, one can own the Django transaction and API contract while the other implements Flutter against the agreed examples. Give migrations, endpoint schemas and generated clients a single owner per change. Merge the backend contract before the dependent app integration. Both implementations must pass the same end-to-end scenarios.

## Scope decisions

- Chosen stack: Flutter waiter tablets and Windows cashier, Django plus Django REST Framework and PostgreSQL, local kitchen browser and print worker, Django-rendered mobile storefront and cloud owner views.
- Pilot assumption: one branch, two or more shared tablets, one counter, one LAN printer and one kitchen station. Model tenant, branch, station and drawer separately from the start.
- Default currency PKR, display locale configurable, branch timezone Asia/Karachi. Store timestamps in UTC and a branch business date for shift reporting. Obtain actual menu, deal examples, equipment and operating hours before commissioning.
- Milestones M1–M3 deliver a local operating pilot. M4 adds direct ordering and multi-branch visibility. M5 expands purchasing, loyalty and external channels. A menu importer, manual terminal reference or mock printer alone does not satisfy the corresponding end-to-end workflow.
