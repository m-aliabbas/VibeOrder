"""80 mm thermal output (ESC/POS), USB/Windows printer transport, printer setup, supplier payments and A4 ledgers."""
from pathlib import Path

from rest_framework.test import APIClient

from apps.kitchen import escpos
from apps.kitchen.models import Printer, PrintJob
from apps.kitchen.services import process_one
from apps.stock.models import SupplierPayment

from .conftest import MIXED_PIZZA, add_round, open_table
from .test_m1_flow import open_drawer


def close_cash(waiter, cashier, table=6, received=300000):
    chk = open_table(waiter, table)
    add_round(waiter, chk["check_id"], [MIXED_PIZZA])
    d = cashier.get(f"checks/{chk['check_id']}").json()
    t = cashier.post(f"checks/{chk['check_id']}/tenders", {"method": "cash", "amount_minor": d["balance_minor"],
                                                           "cash_received_minor": received,
                                                           "expected_revision": d["revision"]}).json()
    r = cashier.post(f"checks/{chk['check_id']}/close", {"expected_revision": t["revision"]})
    assert r.status_code == 200, r.content
    return chk["check_id"]


def test_escpos_markup_encodes_styles_cut_and_drawer():
    content = "{C}{W}DEMO\n{B}{H}2 x PIZZA\n{R}1,900\nplain – text\n{DRAWER}\n{CUT}"
    b = escpos.encode(content)
    assert b.startswith(b"\x1b@")
    assert b"\x1ba\x01\x1d!\x11DEMO\n" in b, "centred double size"
    assert b"\x1d!\x01\x1bE\x012 x PIZZA\n\x1bE\x00" in b, "bold double height"
    assert b"plain - text" in b, "en dash mapped to ASCII for the printer code page"
    assert b"\x1bp\x00" in b and b.endswith(b"\x1dV\x42\x00")
    text = escpos.plain(content, 20)
    assert "{" not in text and text.splitlines()[0] == "DEMO".center(20).rstrip()


def test_kot_and_receipt_layout_on_80mm(demo, waiter, cashier):
    from apps.core.models import Branch
    Branch.objects.filter(pk=demo["branch"].pk).update(phone="042-111", tax_number="1234567-8",
                                                      receipt_footer="Come again!")
    open_drawer(cashier)
    close_cash(waiter, cashier)
    kot = PrintJob.objects.get(kind="kitchen", ticket__station__code="pizza").content
    assert "{C}{W}TABLE 6" in kot and "{B}{H}1 x LARGE MIXED PIZZA" in kot and kot.endswith("{CUT}")
    receipt = PrintJob.objects.get(kind="receipt").content
    plain = escpos.plain(receipt)
    assert receipt.startswith("{DRAWER}"), "cash receipt opens the drawer"
    assert "SALES RECEIPT" in plain and "Receipt: B1-000001" in plain and "NTN 1234567-8" in plain
    assert "Change" in plain and "Come again!" in plain
    assert all(len(ln) <= 48 for ln in plain.splitlines()), "fits 80 mm paper"
    # The job log shows readable text, not markup
    job = PrintJob.objects.get(kind="receipt")
    r = cashier.get(f"print-jobs/{job.pk}")
    assert "{" not in r.json()["content"] and "SALES RECEIPT" in r.json()["content"]
    # A reprinted COPY never opens the drawer
    copy = cashier.post(f"print-jobs/{job.pk}/reprints", {"reason": "customer asked"}).json()
    assert "{DRAWER}" not in PrintJob.objects.get(pk=copy["id"]).content


def test_usb_printer_sends_raw_escpos_and_missing_printer_is_retryable(demo, monkeypatch, waiter, cashier):
    sent = []

    def fake_send(name, payload, title):
        if name != "EPSON TM-T88V":
            raise OSError(f"Printer '{name}' is not installed")
        sent.append((name, payload, title))
        return f"windows:{name}"

    monkeypatch.setattr(escpos, "send_system", fake_send)
    Printer.objects.filter(purpose="receipt", branch=demo["branch"]).update(transport="system", address="EPSON TM-T88V")
    Printer.objects.filter(purpose="kitchen", branch=demo["branch"]).update(transport="system", address="Kitchen USB")
    open_drawer(cashier)
    close_cash(waiter, cashier)
    for _ in range(PrintJob.objects.count()):  # failed jobs go back in the queue: one pass only
        process_one()
    receipt = PrintJob.objects.get(kind="receipt")
    assert receipt.status == "handed_to_spooler" and receipt.spooler_ref == "windows:EPSON TM-T88V"
    assert sent[0][1].startswith(b"\x1b@") and b"SALES RECEIPT" in sent[0][1]
    kot = PrintJob.objects.get(kind="kitchen", ticket__station__code="pizza")
    assert kot.status == "failed_before_send" and "not installed" in kot.last_error, "safe to retry automatically"


def test_manager_sets_up_usb_printer_and_prints_test_page(demo, manager, waiter, monkeypatch):
    monkeypatch.setattr(escpos, "list_system_printers", lambda: ["EPSON TM-T88V", "Microsoft Print to PDF"])
    data = manager.get("printers").json()
    assert data["installed"] == ["EPSON TM-T88V", "Microsoft Print to PDF"]
    assert {t["code"] for t in data["transports"]} == {"system", "tcp", "spool_file"}
    assert waiter.get("printers").status_code == 403
    r = manager.post("printers", {"name": "Counter", "purpose": "receipt", "transport": "system",
                                  "address": "EPSON TM-T88V", "width_chars": 48})
    assert r.status_code == 201, r.content
    pid = r.json()["id"]
    assert manager.post("printers", {"name": "Bad", "transport": "tcp", "address": ""}).status_code == 422
    assert manager.post(f"printers/{pid}", {"width_chars": 50}).status_code == 422
    r = manager.post(f"printers/{pid}", {"transport": "tcp", "address": "192.168.1.50"})
    assert r.json()["address"] == "192.168.1.50:9100"
    Printer.objects.filter(pk=pid).update(transport="spool_file", address="test-counter")
    r = manager.post(f"printers/{pid}/test")
    assert r.status_code == 201
    process_one()
    job = PrintJob.objects.get(kind="test")
    assert "TEST PRINT" in Path(job.spooler_ref).read_text()
    r = manager.post("receipt-settings", {"phone": "042-999", "receipt_footer": "Shukriya!"})
    assert r.status_code == 200 and r.json()["receipt_footer"] == "Shukriya!"
    assert manager.get("printers").json()["receipt"]["phone"] == "042-999"


def test_supplier_payment_cash_from_drawer_and_supplier_ledger(demo, manager, cashier):
    po = manager.post("stock/purchase-orders", {"supplier": "Dairy Co", "lines": [{"item": "cheese", "quantity": 2,
                                                                                    "expected_cost_minor": 2600000}]})
    po = manager.post(f"stock/purchase-orders/{po.json()['id']}", {"action": "send",
                                                                   "expected_revision": po.json()["revision"]}).json()
    manager.post(f"stock/purchase-orders/{po['id']}/receive", {"reference": "INV-9", "expected_revision": po["revision"],
                                                              "lines": [{"line_id": po["lines"][0]["id"], "quantity": 2,
                                                                         "cost_minor": 2600000}]})
    sup = next(s for s in manager.get("stock/suppliers").json()["suppliers"] if s["name"] == "Dairy Co")
    assert sup["balance_minor"] == 2600000

    # Cash needs an open drawer
    r = manager.post(f"stock/suppliers/{sup['id']}/payments", {"amount_minor": 1000000, "method": "cash"})
    assert r.status_code == 409 and r.json()["code"] == "no_open_drawer"
    open_drawer(cashier)
    r = manager.post(f"stock/suppliers/{sup['id']}/payments", {"amount_minor": 1000000, "method": "cash"})
    assert r.status_code == 201 and r.json()["balance_minor"] == 1600000
    assert SupplierPayment.objects.get(method="cash").drawer_movement.amount_minor == -1000000
    r = manager.post(f"stock/suppliers/{sup['id']}/payments", {"amount_minor": 600000, "method": "bank",
                                                                "reference": "TRX-1"})
    assert r.json()["balance_minor"] == 1000000
    assert cashier.post(f"stock/suppliers/{sup['id']}/payments", {"amount_minor": 1, "method": "bank"}).status_code == 403

    led = manager.get("ledgers/supplier", supplier=sup["id"]).json()
    rows = led["sections"][0]["rows"]
    assert [r["type"] for r in rows] == ["Opening balance", "Goods received", "Payment (Cash from drawer)",
                                         "Payment (Bank transfer)"]
    assert [r["balance"] for r in rows] == [0, 2600000, 1600000, 1000000]
    assert led["sections"][0]["totals"]["balance"] == 1000000

    cash = manager.get("ledgers/cash").json()
    paid_out = [r for r in cash["sections"][0]["rows"] if r["type"] == "Paid out"]
    assert paid_out[0]["out"] == 1000000 and "Dairy Co" in paid_out[0]["ref"]


def test_sales_cash_customer_stock_ledgers_and_a4_print_link(demo, manager, waiter, cashier):
    open_drawer(cashier)
    close_cash(waiter, cashier, 6)
    close_cash(waiter, cashier, 7)
    sales = manager.get("ledgers/sales").json()
    rows = sales["sections"][0]["rows"]
    assert [r["receipt"] for r in rows] == ["B1-000001", "B1-000002"] and all(r["payment"] == "Cash" for r in rows)
    assert sales["sections"][0]["totals"]["total"] == sum(r["total"] for r in rows)

    cash = manager.get("ledgers/cash").json()
    shift = cash["sections"][0]
    assert shift["rows"][0]["type"] == "Opening float" and shift["rows"][-1]["balance"] == 500000 + 2 * rows[0]["total"]

    stock = manager.get("ledgers/stock").json()
    cheese = next(r for r in stock["sections"][0]["rows"] if r["item"] == "Mozzarella")
    detail = manager.get("ledgers/stock", item=cheese["id"]).json()
    assert detail["sections"][0]["rows"][-1]["balance"] == cheese["closing"]

    assert manager.get("ledgers/customer").status_code == 200
    assert manager.get("ledgers/nope").status_code == 404
    assert waiter.get("ledgers/sales").status_code == 403

    page = APIClient().get(sales["print_path"])
    html = page.content.decode()
    assert page.status_code == 200 and "size: A4" in html and "B1-000002" in html and "Sales ledger" in html
    assert APIClient().get(sales["print_path"] + "x").status_code == 404, "tampered link refused"
