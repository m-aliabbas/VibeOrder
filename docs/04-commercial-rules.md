# 04 · Pricing, cash and inventory contracts

## Price calculation

All figures below are synthetic merchant settings for tests. Use decimal arithmetic at the pricing boundary and persist PKR minor units (100 per rupee). Keep an itemized snapshot containing rule IDs, menu release, configured rounding, discounts, fee and tax lines. Clients may preview a server quote; the server validates it at commit.

1. Resolve branch, channel, service mode, time window and effective menu release. Validate deal slots, size, topping side and mutually exclusive options.
2. Select the configured mixed-flavour rule: higher full-pizza price, arithmetic mean, or explicit mixed price for that size. A release must specify the rule and missing-price behavior. Reject a combination with an undefined price.
3. Add crust once per physical pizza and side-specific topping charges from the size price list. Whole and per-side charges are independently configurable. Replacing a default topping grants credit only where the release defines a substitution rule.
4. A fixed-price deal replaces the included components' base prices. Add only explicit slot upgrades and permitted modifiers. Prevent double charging for the included drink or calculating both a menu discount and a fixed-deal discount for the same component.
5. Apply configured discounts in deterministic order. Pilot default permits one eligible order promotion plus manager-authorized explicit line adjustments within a documented floor of zero. Allocate order discounts proportional to eligible line amounts using largest remainder with stable line-ID tie breaks.
6. Apply the merchant's configured tax basis, inclusive/exclusive mode, fee taxability and rounding. Pilot test profile uses tax-exclusive line rounding, half up to the nearest minor unit. Store original tax/discount attribution so partial refunds use the original allocation. Taxes and invoicing profiles require merchant-specific confirmation ahead of live use.
7. Calculate amount due as charge total less successful allocated payments, accounting for linked credit adjustments and refund postings. Keep a sale return/credit document paired with a money refund so a settled returned item does not accidentally reopen the customer's bill.

| Fixture | Expected result |
|---|---|
| Large tikka PKR 1,400, fajita 1,600; mixed policy higher price; stuffed crust 200; right-half cheese 100; tax zero | 1,600 + 200 + 100 = **PKR 1,900**. Kitchen receives one pizza. |
| Same order, average policy | 1,500 + 200 + 100 = **PKR 1,800**. Historical tickets retain their original policy. |
| Family deal 2,000 including one eligible large mixed pizza and one drink; same crust/cheese upgrades; delivery 120; tax zero | 2,000 + 200 + 100 + 120 = **PKR 2,420**. Pack checklist has a pizza and a drink. |
| Three eligible PKR 100 lines share a PKR 10 discount | Allocate 334, 333, 333 minor units in stable tie-break order. Sum = 1,000 minor units. |
| Synthetic exclusive tax 10% on a PKR 100 item after a PKR 10 discount | Tax 9, total **PKR 99**. This is a test rate. |
| Pay 1,000 cash and 900 via confirmed external terminal on a 1,900 bill | Two successful tenders, balance zero, drawer increases by 1,000. |
| Cash received 2,000 for 1,900 | Change 100, drawer increases by 1,900. Record received and change separately. |

Support split by amount and by item. Permit partial quantities only when the product's sale unit allows them. Under concurrent partial payments lock the same check and cap allocations at the remaining eligible balance. Keep each payer's receipt linked to that payer’s allocations. A refund requires original successful tender, amount, reason, manager identity and provider reference if applicable. Count changed cash, goods discarded and revenue reversed as three separate facts.

## Shift and rider ledger

Expected drawer cash = opening float + net cash applied to counter sales + rider cash actually remitted + approved cash paid in − cash refunds paid from this drawer − approved paid out − cash drops. Net applied already excludes customer change. Count each physical movement once. Card, wallet, pending transfer and unremitted COD are excluded from drawer cash.

**Golden close:** opening float 5,000; counter cash sales 8,300; successful card sales 4,000; rider collected 2,420, handed back 2,000; cash refund 300; paid-out expense 500. Expected drawer = **14,500**. Counted 14,450 means **−50** variance. Rider outstanding = **420**. Manager identifies the rider, collection and remaining amount before approval. A remittance is a cash transfer and cannot increase sales revenue again.

Define business day by branch cutoff and persist it when the relevant transaction posts. A check may span midnight; keep opened_at, settled_at and each posting's business date. Show open balances carried to the next shift. Record returns on their actual posting date and retain the original receipt date. Prevent a cashier from overwriting yesterday's signed totals.

## Stock and margin

Use recipe versions keyed to size and options. Example: large base requires 250 g dough, 120 g cheese and one box for takeaway. A mixed flavour applies half of each flavour-specific recipe; deduct base dough once. Right-half extra cheese adds 20 g under the fixture recipe. The resulting cheese consumption is 140 g. Record consumption at first production fire, packaging at packing and an explicit loss for stock discarded outside tracked production. A remake consumes its own ingredients. After preparation, a cash refund does not automatically restore ingredients; a physical, approved return has a separate stock movement. Never subtract a second waste movement for ingredients already consumed unless compensating the original posting.

Purchasing M5: receive 2 × 5 kg cheese against a supplier receipt; book 10,000 g in the stock ledger. Track receipts, transfer out/in by linked transfer ID, counts and manager-approved variances. Proposed cost model is moving weighted average per branch, with cost snapshot at consumption. Show missing costs explicitly. Start with a branch sellout switch; automatic sellout from theoretical inventory requires a merchant-configured buffer and reconciliation process.

## Report definitions

| Report | Calculation and drill-down |
|---|---|
| Net merchandise sales | Gross item charges less discounts and credited merchandise returns; show tax, delivery, tips and fees separately. Drill into charge/credit documents. |
| Collections by channel and tender | Group successful money receipts and refunds by tender, channel and posting day. Provide gross and net values. Map external references to settlement statements. |
| Product contribution | Net item revenue excluding tax less ingredient and directly attributed packaging costs. Report missing recipes/costs and exclude them from asserted margin percentages. |
| Service speed | Median and 90th percentile accepted-to-ready and ready-to-handoff durations over fulfilled eligible orders. Display count, missing timestamps and filters. |
| Marketplace settlement | Gross eligible sales minus commissions, funded discounts, refunds and fees, plus/minus statement adjustments. Link expected and actual payout to provider batch ID. |
| Till and rider discrepancy | Drill from variance to opening float, individual physical movements, counted close and supporting approval. Show outstanding COD by rider and age. |
