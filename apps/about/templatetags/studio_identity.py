"""``{% site_name %}`` for templates rendered without a request (emails, background jobs)."""

from django import template
from django.conf import settings

register = template.Library()


@register.simple_tag
def site_name() -> str:
    return settings.SITE_NAME
