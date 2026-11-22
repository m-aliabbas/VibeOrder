from django import template

register = template.Library()


@register.filter
def rupees(minor):
    """Minor units -> '1,900' or '123.45' (no currency symbol)."""
    try:
        m = int(minor)
    except (TypeError, ValueError):
        return ""
    sign, m = ("-" if m < 0 else ""), abs(m)
    r, p = divmod(m, 100)
    return f"{sign}{r:,}" + (f".{p:02d}" if p else "")
