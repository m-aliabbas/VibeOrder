from django.db import models
from django.db.models import Q

from apps.core.models import Branch, Staff, Stamped, Station
from apps.orders.models import Check, CheckLine, CheckRound


class ProductionTicket(Stamped):
    KINDS = [("original", "Original"), ("change", "Change"), ("remake", "Remake"), ("fire", "Fire held")]

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    station = models.ForeignKey(Station, on_delete=models.PROTECT, related_name="tickets")
    check_ref = models.ForeignKey(Check, on_delete=models.PROTECT, related_name="tickets", db_column="check_id")
    round = models.ForeignKey(CheckRound, on_delete=models.PROTECT, related_name="tickets")
    seq = models.PositiveIntegerField(help_text="Ticket number within the branch business day")
    kind = models.CharField(max_length=10, choices=KINDS, default="original")
    due_at = models.DateTimeField()
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    note = models.CharField(max_length=300, blank=True, help_text="Plain-language change, e.g. table move")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["round", "station", "kind"], condition=Q(kind="original"),
                                    name="uniq_original_dispatch")
        ]


class TicketItem(Stamped):
    STATUS = [("held", "Held"), ("queued", "Queued"), ("preparing", "Preparing"), ("ready", "Ready"),
              ("packed", "Packed"), ("served", "Served"), ("cancelled", "Cancelled")]
    TRANSITIONS = {
        "held": {"queued", "cancelled"},
        "queued": {"preparing", "ready", "cancelled"},
        "preparing": {"ready", "cancelled"},
        "ready": {"packed", "served"},
        "packed": set(),
        "served": set(),
        "cancelled": set(),
    }

    ticket = models.ForeignKey(ProductionTicket, on_delete=models.PROTECT, related_name="items")
    line = models.ForeignKey(CheckLine, on_delete=models.PROTECT, related_name="ticket_items")
    component_index = models.PositiveIntegerField(default=0)
    label = models.CharField(max_length=160)
    detail = models.JSONField(default=list)
    notes = models.CharField(max_length=200, blank=True)
    slot_name = models.CharField(max_length=80, blank=True)
    quantity = models.PositiveIntegerField()
    status = models.CharField(max_length=12, choices=STATUS, default="queued")
    started_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    change_note = models.CharField(max_length=300, blank=True)
    remake_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="remakes")


class Printer(Stamped):
    TRANSPORTS = [("system", "Installed printer (USB / Windows)"), ("tcp", "Network printer (IP:9100)"),
                  ("spool_file", "Test: write to file")]

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="printers")
    name = models.CharField(max_length=80)
    station = models.ForeignKey(Station, null=True, blank=True, on_delete=models.PROTECT)
    purpose = models.CharField(max_length=20, default="kitchen")  # kitchen | receipt
    transport = models.CharField(max_length=20, choices=TRANSPORTS, default="spool_file")
    address = models.CharField(max_length=120, blank=True, help_text="host:port for tcp, queue name for spool")
    width_chars = models.PositiveIntegerField(default=48, help_text="80mm paper ~48 chars at font A")
    is_active = models.BooleanField(default=True)
    last_error = models.CharField(max_length=300, blank=True)
    last_ok_at = models.DateTimeField(null=True, blank=True)


class PrintJob(Stamped):
    STATUS = [("queued", "Queued"), ("sending", "Sending"), ("handed_to_spooler", "Handed to spooler"),
              ("failed_before_send", "Failed before send"), ("outcome_unknown", "Outcome unknown")]

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    printer = models.ForeignKey(Printer, on_delete=models.PROTECT, related_name="jobs")
    kind = models.CharField(max_length=20)  # kitchen | receipt
    ticket = models.ForeignKey(ProductionTicket, null=True, blank=True, on_delete=models.PROTECT)
    check_ref = models.ForeignKey(Check, null=True, blank=True, on_delete=models.PROTECT, db_column="check_id")
    content = models.TextField(help_text="Rendered snapshot; immutable")
    status = models.CharField(max_length=24, choices=STATUS, default="queued")
    attempts = models.PositiveIntegerField(default=0)
    lease_until = models.DateTimeField(null=True, blank=True)
    spooler_ref = models.CharField(max_length=500, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    original = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="copies")
    is_copy = models.BooleanField(default=False)
    requested_by = models.ForeignKey(Staff, null=True, blank=True, on_delete=models.PROTECT)
    reason = models.CharField(max_length=200, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
