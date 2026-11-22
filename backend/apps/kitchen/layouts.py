"""80 mm thermal layouts: kitchen order ticket (KOT), bill, receipt, payer receipt and refund slip.

Output is line markup (see `escpos.py`): `{W}` lines hold width // 2 characters, every other line `width`.
The renderer never prints check IDs or other internal identifiers on customer paper.
"""
from django.utils import timezone

W_DEFAULT = 48


def money(minor, currency=""):
    sign = "-" if minor < 0 else ""
    rupees, paisa = divmod(abs(minor), 100)
    s = f"{rupees:,}" + (f".{paisa:02d}" if paisa else "")
    return f"{sign}{currency + ' ' if currency else ''}{s}"


def wrap(text, width, indent=""):
    words, out, cur = str(text).split(), [], indent
    for w in words:
        while len(w) > width - len(indent) - 2:  # very long word: hard split
            if cur.strip():
                out.append(cur.rstrip())
                cur = indent
            out.append(indent + w[:width - len(indent)])
            w = w[width - len(indent):]
        if len(cur) + len(w) + (1 if cur.strip() else 0) > width:
            out.append(cur.rstrip())
            cur = indent + "  " + w
        else:
            cur = (cur + " " + w) if cur.strip() else cur + w
    if cur.strip():
        out.append(cur.rstrip())
    return out


def lr(left, right, width):
    """Left text and right-aligned amount on one line (left side wraps when needed)."""
    right = str(right)
    if not right:
        return wrap(left, width) or [""]
    if len(left) + len(right) + 1 <= width:
        return [left + right.rjust(width - len(left))]
    rows = wrap(left, width - len(right) - 1)
    rows[-1] = rows[-1] + right.rjust(width - len(rows[-1]))
    return rows


def where_label(check):
    if check.table_id:
        return f"TABLE {check.table.label}"
    return {"counter": "TAKEAWAY", "pickup": "PICKUP", "delivery": "DELIVERY", "dine_in": "DINE-IN"}.get(
        check.service_type, check.service_type.upper())


def ticket_header(check):
    return f"#{check.token}  {where_label(check)}"


def branch_header(branch, width):
    out = [f"{{C}}{{W}}{branch.tenant.name[:width // 2]}"]
    sub = branch.name.replace(branch.tenant.name, "").strip(" -–") or branch.name
    if sub and sub != branch.tenant.name:
        out.append(f"{{C}}{sub[:width]}")
    for extra in (branch.address, f"Tel {branch.phone}" if branch.phone else "",
                  f"NTN {branch.tax_number}" if branch.tax_number else ""):
        if extra:
            out += [f"{{C}}{ln}" for ln in wrap(extra, width)]
    return out


def footer(branch, width):
    text = branch.receipt_footer or "Thank you! Please visit again."
    return ["-" * width] + [f"{{C}}{ln}" for ln in wrap(text, width)] + ["{C}Powered by VibeOrder", "{CUT}"]


# ---------------------------------------------------------------- KOT

def render_kot(ticket, width=W_DEFAULT):
    check = ticket.check_ref
    tz = check.branch.tz
    half = width // 2
    kind = {"fire": "FIRE HELD COURSE", "change": "CHANGE", "remake": "REMAKE", "void": "VOID"}.get(ticket.kind)
    out = [f"{{C}}{{B}}{ticket.station.name.upper()[:width]}"]
    if kind:
        out.append(f"{{C}}{{W}}** {kind} **")
    out += [
        f"{{C}}{{W}}{where_label(check)[:half]}",
        f"{{C}}{{B}}Order #{check.token}   KOT {ticket.seq}   Round {ticket.round.number}",
        "-" * width,
        *lr(f"Waiter: {ticket.round.added_by.display_name}", f"Guests {check.guest_count}", width),
        *lr(f"Sent {timezone.localtime(ticket.created_at, tz):%H:%M}",
            f"DUE {timezone.localtime(ticket.due_at, tz):%H:%M}", width),
    ]
    if check.customer_name and check.service_type != "dine_in":
        out.append(f"Customer: {check.customer_name}"[:width])
    out.append("=" * width)
    if ticket.note:
        out += [f"{{B}}{ln}" for ln in wrap(f">> {ticket.note}", width)] + ["-" * width]
    for item in ticket.items.order_by("line__line_no", "component_index"):
        if item.change_note and item.change_note != ticket.note:
            out += [f"{{B}}{ln}" for ln in wrap(f">> {item.change_note}", width)]
        prefix = "CANCEL " if item.status == "cancelled" else ""
        out += [f"{{B}}{{H}}{ln}" for ln in wrap(f"{prefix}{item.quantity} x {item.label}", width)]
        if item.slot_name:
            out += wrap(f"[{item.slot_name}]", width, "   ")
        for d in item.detail:
            style = "{B}" if "half" in d.lower() else ""
            out += [style + ln for ln in wrap(d, width, "   ")]
        if item.notes:
            out += [f"{{B}}{ln}" for ln in wrap(f"NOTE: {item.notes}", width, "   ")]
        out.append("")
    out += ["-" * width, f"{{C}}{timezone.localtime(timezone.now(), tz):%d-%m-%Y %H:%M}", "{CUT}"]
    return "\n".join(out)


# ---------------------------------------------------------------- bill / receipt

def _line_rows(ln, width, amount=None, part=False):
    """Item, qty x rate and amount, then the build details (crust, halves, options) indented."""
    amount = ln.total_minor if amount is None else amount
    name = ln.name + (" (part)" if part else "")
    rows = lr(name, money(amount), width)
    if ln.quantity > 1:
        rows.append(f"  {ln.quantity} x {money(ln.unit_minor)}")
    for comp in ln.components or []:
        for detail in comp.get("lines", [])[:6]:
            rows += wrap(detail, width, "  ")
    return rows


def render_bill(check, width=W_DEFAULT, title="RECEIPT", receipt_no=None):
    from apps.orders.services import check_amounts

    branch = check.branch
    tz = branch.tz
    a = check_amounts(check)
    is_receipt = receipt_no is not None
    out = branch_header(branch, width) + ["=" * width, f"{{C}}{{B}}{title}"]
    now = timezone.localtime(check.closed_at if is_receipt and check.closed_at else timezone.now(), tz)
    if receipt_no:
        out += lr(f"Receipt: {receipt_no}", f"{now:%d-%m-%Y %H:%M}", width)
    else:
        out += lr(f"Order: #{check.token}", f"{now:%d-%m-%Y %H:%M}", width)
    out += lr(f"Order #{check.token}  {where_label(check)}" if receipt_no else where_label(check),
              f"Guests {check.guest_count}" if check.service_type == "dine_in" else "", width)
    staff = check.closed_by if is_receipt and check.closed_by_id else check.opened_by
    out.append(f"{'Cashier' if is_receipt else 'Served by'}: {staff.display_name}"[:width])
    if check.customer_name or check.customer_phone:
        out.append(f"Customer: {check.customer_name} {check.customer_phone}".strip()[:width])
    d = check.deliveries.order_by("-attempt").first() if check.service_type == "delivery" else None
    if d is not None and getattr(d, "address_text", ""):
        out += wrap(f"Deliver to: {d.address_text}", width)
    out += ["-" * width, *lr("ITEM", "AMOUNT", width), "-" * width]
    for ln in check.lines.filter(status="active").order_by("line_no"):
        out += _line_rows(ln, width)
    out.append("-" * width)
    out += lr("Subtotal", money(a["subtotal_minor"]), width)
    for disc in check.discounts.filter(status="active").select_related("promotion"):
        label = disc.promotion.name if disc.promotion_id else (disc.reason or "Discount")
        out += lr(f"Discount: {label}", money(-disc.applied_minor), width)
    if a["discount_minor"] and not check.discounts.filter(status="active").exists():
        out += lr("Discount", money(-a["discount_minor"]), width)
    if a["tax_minor"]:
        out += lr("Tax", money(a["tax_minor"]), width)
    out.append(f"{{B}}{{H}}" + lr("TOTAL", money(a["total_minor"], branch.currency), width)[0])
    tenders = list(check.tenders.filter(status="succeeded").order_by("created_at"))
    if tenders:
        out.append("-" * width)
    cash = False
    for t in tenders:
        out += lr(t.get_method_display(), money(t.amount_minor), width)
        if t.method == "cash":
            cash = True
            if t.cash_received_minor:
                out += lr("  Cash received", money(t.cash_received_minor), width)
                out.append(f"{{B}}" + lr("  Change", money(t.change_minor), width)[0])
        if t.external_ref:
            out.append(f"  Ref {t.external_ref}"[:width])
    if a["balance_minor"] and tenders:
        out.append(f"{{B}}" + lr("BALANCE DUE", money(a["balance_minor"], branch.currency), width)[0])
    if not is_receipt:
        out += ["", "{C}{B}THIS IS NOT A RECEIPT"]
    out += footer(branch, width)
    if is_receipt and cash:
        out.insert(0, "{DRAWER}")
    return "\n".join(out)


def render_payer_receipt(tender, width=W_DEFAULT):
    """Receipt for one payer: only the lines (or parts) this tender paid for."""
    check = tender.check_ref
    branch = check.branch
    tz = branch.tz
    out = branch_header(branch, width) + ["=" * width, "{C}{B}PAYER RECEIPT"]
    out += lr(f"Receipt: {check.receipt_no}" if check.receipt_no else f"Order: #{check.token}",
              f"{timezone.localtime(tender.created_at, tz):%d-%m-%Y %H:%M}", width)
    out += [ticket_header(check), "-" * width]
    for alloc in tender.allocations.select_related("line").order_by("line__line_no"):
        out += _line_rows(alloc.line, width, alloc.amount_minor, alloc.amount_minor != alloc.line.charge_minor)
    out.append("-" * width)
    out.append("{B}{H}" + lr("PAID", money(tender.amount_minor, branch.currency), width)[0])
    out += lr(tender.get_method_display(), money(tender.amount_minor), width)
    if tender.method == "cash" and tender.cash_received_minor:
        out += lr("  Cash received", money(tender.cash_received_minor), width)
        out += lr("  Change", money(tender.change_minor), width)
    if tender.external_ref:
        out.append(f"Ref {tender.external_ref}"[:width])
    out += footer(branch, width)
    if tender.method == "cash":
        out.insert(0, "{DRAWER}")
    return "\n".join(out)


def render_refund(refund, width=W_DEFAULT):
    check = refund.check_ref
    branch = check.branch
    tz = branch.tz
    out = branch_header(branch, width) + ["=" * width, "{C}{W}REFUND"]
    out += lr(f"Receipt: {check.receipt_no}" if check.receipt_no else f"Order: #{check.token}",
              f"{timezone.localtime(timezone.now(), tz):%d-%m-%Y %H:%M}", width)
    out += [ticket_header(check), "-" * width]
    out.append("{B}{H}" + lr(f"Refund {refund.get_method_display()}", money(refund.amount_minor, refund.currency),
                             width)[0])
    if refund.external_ref:
        out += lr("Terminal ref", refund.external_ref, width)
    out += wrap(f"Reason: {refund.reason}", width)
    out += lr("Approved by", refund.approved_by.display_name, width)
    out += ["", "", "Customer signature: ____________________", "{CUT}"]
    return "\n".join(out)


def render_test_page(printer, width=W_DEFAULT):
    branch = printer.branch
    now = timezone.localtime(timezone.now(), branch.tz)
    return "\n".join([
        *branch_header(branch, width), "=" * width, "{C}{W}TEST PRINT", "{C}{B}" + printer.name[:width],
        f"Purpose: {printer.purpose}   Width: {width} chars",
        f"Printed {now:%d-%m-%Y %H:%M:%S}", "-" * width,
        "Normal text 0123456789 Rs 1,234.50",
        "{B}Bold text", "{H}Double height", "{C}Centred", "{R}Right aligned",
        "-" * width, "".join(str(i % 10) for i in range(width)), "{C}If this is readable, the printer is set up.",
        "{CUT}",
    ])
