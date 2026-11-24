from django import template

register = template.Library()


@register.filter
def label(mapping, key):
    """``labels|label:key``: the display label for a stored value, or the value itself."""
    return mapping.get(key, key) if hasattr(mapping, "get") else key
