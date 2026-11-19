# 01 · Product and interaction requirements

## A shift at a glance

The manager opens the drawer and checks station and printer status. Waiters take tables. The counter receives phone and walk-in orders. A cook sees only the work routed to that station. Packing verifies all components before handing the order to the guest or rider. The cashier settles open bills, then the manager counts cash, checks outstanding rider collections and closes the shift. Every exception should lead back to an identifiable order, person and business date.

| Role | Default landing screen and primary tasks | Authority boundary |
|---|---|---|
| Waiter | Floor/table list, active check, recent sends | Open and add to permitted tables, request a bill. Sent-line correction requires the configured permission. |
| Cashier | Counter sale, open bills, payment and receipt | Take tenders on own open drawer, issue receipts. Discounts above configured limit and refunds require approval. |
| Cook | Station queue sorted by due time | Start, mark ready and acknowledge changes on assigned stations. See only fulfillment details. |
| Packer / dispatcher | Components to collect, late orders, rider assignment | Verify items, dispatch and record handover or failure. |
| Rider | Assigned jobs, amount to collect, returned cash | See only assigned contacts and addresses while the job is active. Pilot dispatcher may enter status on rider's behalf. |
| Branch manager | Approvals, exceptions, drawer close, local availability | Approve with own identity, record reasons, review branch reports. |
| Owner / menu administrator | Branch comparison, menu releases, staff access | Scope every operation to authorized tenant and branches. Record privileged changes. |
| Guest | Branch selection, menu, basket, checkout and tracking | Access own order using an unguessable scoped token. |

## Service flows

**Dine-in.** Select area/table, open a check with guest count, append rounds and show each round separately. Keep the table occupied until its checks are closed and it is released. Pilot uses one active check per table with payment splitting inside that check. Move a check to a free table in a single transaction. Merging two occupied tables requires locking both checks, manager permission, a movement record and a refreshed total. Pilot permits merge only before any payment. For course service, allow a line to be held and explicitly fired. A held item appears on the waiter screen until sent.

**Counter and phone.** Select pickup, delivery or counter service before confirming. Phone lookup should show masked matching customers to authorized staff. Confirm the delivery address even on repeat orders. Rebuild previous items at current availability and price and highlight changes. Assign a short branch/day token and a globally unique order ID. Record a requested and a promised time. Print the customer receipt independently of the kitchen ticket.

**Pizza and combos.** Choose a size followed by crust, flavour or two halves, then whole/left/right toppings. Show the current total next to the confirm action. Validate topping limits, excluded combinations and sold-out options. A family deal has named slots, quantities, eligible products, required selections and permitted upgrade charges. Preserve which pizza or side belongs to which deal. One physical pizza remains one production item even when the pricing record contains distinct halves.

**Corrections.** A draft line can be edited directly. Once sent, retain its original specification. Corrections reference the original ticket and show the difference in plain language, for example “Table 6, pizza 1, RIGHT half: remove olives.” Kitchen acknowledges. If already cooked, allow a manager to record a discard and a remake separately from any financial adjustment. A kitchen remake alone charges the customer zero additional money.

**Kitchen and packing.** Route the pizza to the pizza station, fries to fryer and drinks to packing. Each ticket displays order token, table or fulfillment type, quantity, due time, modifiers, allergy notes and revision. Mark each station item ready independently. Packing shows one checklist for all components, including bought-in drinks. Only authorized staff can override an incomplete pack, with a reason. A cancelled or changed item stays visible until acknowledged.

**Delivery.** Validate address and zone, calculate a fee, confirm the promised time and record COD or prepaid. Assign a rider, dispatch, record delivered or failed with reason. A failed attempt cannot silently create a sale reversal or fresh stock. Flag food disposition, refund and redelivery for an explicit decision. Maintain rider liability for cash actually collected until remitted. Keep the dispatch screen usable without a rider phone app.

**Direct ordering.** Support guest checkout on mobile, availability by branch, pickup or delivery, optional notes, final itemized total, estimated time and customer-visible confirmation. Busy branches may pause intake or widen promise windows. A web submission first receives a pending reference. Display accepted only after branch acknowledgment. M4 starts with pickup/COD; payment-provider capture and refunds are separately gated.

**Owner and menu operations.** Author a versioned menu, preview a sample check and publish to selected branches. Show each branch's active release and sync time. Branch managers can mark items or ingredients unavailable. Provide shift sales, payment breakdown, voids and approvals, rider balances, refunds and late orders with drill-down to transactions. Export CSV with date, branch, currency, business day and filter scope.

## Screen specifications

| Screen | Layout and critical behavior |
|---|---|
| Waiter floor | Default to assigned area. Tables show name/number, occupancy, waiter and pending bill. Include list view and search. After device unlock return to the same table only after verifying the new operator can access it. |
| Tablet order | On a wide screen show category/menu and current round side by side. On smaller tablets keep the active table, item count and total visible while switching panes. Separate “Unsent” from “Sent to kitchen”. Disable duplicate taps while awaiting the first result. |
| Pizza builder | Left/right must appear as words as well as a visual preview. Keep the chosen size, side and incremental price visible. Use explicit “Apply to both halves”. Editing one pizza in a quantity of two requires first splitting that line. |
| Cashier | Persistent check identifier, amount due, payment allocations and change. Keyboard sequence supports search, quantity, tender and receipt. Returning from an approval or printer error preserves the check. |
| Kitchen | Large text at working distance. Order age and due time have distinct labels. Provide touch targets usable in a busy kitchen. Show changed lines and timed-out confirmations with text and icons as well as colour. |
| Devices and jobs | Branch/LAN status separate from cloud status. Show printer job state, last error, queue and next action. Reprinting requires an operator and adds COPY plus original ticket ID. |
| Close shift | Blind cash entry before showing variance. Display unpaid checks, pending tenders, rider liabilities and handover actions. Save progress on interruption. Manager signs a discrepancy with a reason. |

Usability targets for the pilot: common tablet actions at least 48 logical pixels, readable at the installed viewing distance, support enlarged text and keyboard navigation on cashier. Test portrait and landscape on the actual tablet. Externalize labels and support Unicode menu names; test Urdu/English mixed labels on screen and in printed tickets. Use rasterized text where the printer font lacks glyphs. Full Urdu interface and RTL verification can be delivered as a separate localization milestone.
