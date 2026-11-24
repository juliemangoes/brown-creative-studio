"""Spanish as used in Belize and Guatemala: a decimal point and comma thousands (1,800.00), unlike Spain."""

from django.conf.locale.es.formats import *  # noqa: F403

DECIMAL_SEPARATOR = "."
THOUSAND_SEPARATOR = ","
NUMBER_GROUPING = 3
