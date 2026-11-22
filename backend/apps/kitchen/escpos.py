"""80 mm thermal printing: a tiny line markup, its ESC/POS encoding and the plain-text view.

Print jobs keep their rendered content as text with optional line tags, so the job log, previews and spool files
stay readable while the printer still gets bold, big and centred lines:

    {C}{W}DEMO PIZZERIA      centre, double width + height (24 chars on 80 mm)
    {B}{H}2 x LARGE PIZZA    bold, double height (full 48 chars)
    {R}Total 3,400           right aligned
    {CUT}                    feed and partial cut
    {DRAWER}                 open the cash drawer wired to the printer (cash receipts)

Tags only at the start of a line. Unknown text passes through unchanged.
"""
import re
import unicodedata

TAG = re.compile(r"^\{(B|W|H|C|R)\}")
CONTROL_LINES = {"{CUT}", "{DRAWER}"}

ESC, GS = b"\x1b", b"\x1d"
INIT = ESC + b"@" + ESC + b"t\x00"          # reset, code page PC437
ALIGN = {"L": ESC + b"a\x00", "C": ESC + b"a\x01", "R": ESC + b"a\x02"}
BOLD_ON, BOLD_OFF = ESC + b"E\x01", ESC + b"E\x00"
SIZE = {"": GS + b"!\x00", "W": GS + b"!\x11", "H": GS + b"!\x01"}
CUT = b"\n\n\n\n" + GS + b"V\x42\x00"        # feed then partial cut
DRAWER = ESC + b"p\x00\x19\xfa"              # pulse pin 2: 50 ms on, 500 ms off

# Characters the printer code page lacks, mapped to close ASCII.
TRANSLATE = str.maketrans({"–": "-", "—": "-", "·": "-", "’": "'", "‘": "'", "“": '"', "”": '"', "…": "...",
                           "₨": "Rs", "×": "x", "✓": "*", "★": "*"})


def parse(line):
    """Return (tags, text) for one markup line."""
    tags = set()
    while True:
        m = TAG.match(line)
        if not m:
            return tags, line
        tags.add(m.group(1))
        line = line[m.end():]


def to_ascii(text):
    text = text.translate(TRANSLATE)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def plain(content, width=48):
    """Readable text for previews, the job log and spool files (centring applied, tags removed)."""
    out = []
    for line in content.split("\n"):
        if line in CONTROL_LINES:
            continue
        tags, text = parse(line)
        if "C" in tags:
            text = text.center(width).rstrip()
        elif "R" in tags:
            text = text.rjust(width)
        out.append(text)
    return "\n".join(out)


def encode(content):
    """ESC/POS bytes for the printer."""
    buf = bytearray(INIT)
    for line in content.split("\n"):
        if line == "{CUT}":
            buf += CUT
            continue
        if line == "{DRAWER}":
            buf += DRAWER
            continue
        tags, text = parse(line)
        buf += ALIGN["C" if "C" in tags else "R" if "R" in tags else "L"]
        buf += SIZE["W" if "W" in tags else "H" if "H" in tags else ""]
        if "B" in tags:
            buf += BOLD_ON
        buf += to_ascii(text).encode("cp437", errors="replace") + b"\n"
        if "B" in tags:
            buf += BOLD_OFF
        if tags & {"W", "H"}:
            buf += SIZE[""]
        if tags & {"C", "R"}:
            buf += ALIGN["L"]
    if not content.rstrip().endswith("{CUT}"):
        buf += CUT
    return bytes(buf)


def list_system_printers():
    """Printers installed on this PC (Windows: USB/driver printers; macOS/Linux: CUPS queues)."""
    import subprocess
    import sys
    try:
        if sys.platform == "win32":
            import win32print  # pywin32
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            return sorted({p[2] for p in win32print.EnumPrinters(flags)})
        out = subprocess.run(["lpstat", "-e"], capture_output=True, text=True, timeout=5).stdout
        return sorted({ln.strip() for ln in out.splitlines() if ln.strip()})
    except Exception:  # no print system available: show none rather than fail the screen
        return []


def send_system(printer_name, payload, title):
    """Send raw ESC/POS to an installed printer by name. Raises OSError before any byte leaves when the printer
    cannot be opened; anything after that is an uncertain outcome for the caller."""
    import subprocess
    import sys
    if sys.platform == "win32":
        import win32print  # pywin32
        try:
            h = win32print.OpenPrinter(printer_name)
        except Exception as exc:  # pywintypes.error
            raise OSError(f"Windows printer '{printer_name}' not found: {exc}") from exc
        try:
            win32print.StartDocPrinter(h, 1, (title, None, "RAW"))
            try:
                win32print.StartPagePrinter(h)
                win32print.WritePrinter(h, payload)
                win32print.EndPagePrinter(h)
            finally:
                win32print.EndDocPrinter(h)
        finally:
            win32print.ClosePrinter(h)
        return f"windows:{printer_name}"
    if printer_name not in list_system_printers():
        raise OSError(f"Printer '{printer_name}' is not installed on this computer")
    r = subprocess.run(["lp", "-d", printer_name, "-o", "raw", "-t", title], input=payload, capture_output=True,
                       timeout=15)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace")[:200])
    return f"cups:{printer_name} {r.stdout.decode(errors='replace').strip()}"
