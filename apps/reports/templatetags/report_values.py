from django import template
from apps.reports.figures import number

register=template.Library()

@register.filter
def report_number(value):
    return number(value)
