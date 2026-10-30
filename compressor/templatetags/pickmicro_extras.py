from django import template

register = template.Library()


@register.filter
def filesize(value):
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "-" if size < 0 else ""
    size = abs(size)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{sign}{int(size)} B" if unit == "B" else f"{sign}{size:.1f} {unit}"
        size /= 1024
