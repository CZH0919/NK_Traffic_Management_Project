"""Presentation helpers; raw stored values and analysis payloads stay unchanged."""
from datetime import datetime
import re
from zoneinfo import ZoneInfo
from django import template
from django.utils.dateparse import parse_datetime

register = template.Library()
LABELS = {'NB':'北向','SB':'南向','EB':'东向','WB':'西向',
          'Pedestrian':'行人','Bicyclist':'骑行者','Occupant':'车内人员',
          'Injured':'受伤','Killed':'死亡','Unspecified':'未说明','Does Not Apply':'不适用'}

@register.filter
def source_label(value):
    label = LABELS.get(value) if isinstance(value, str) else None
    return f'{label}（{value}）' if label else value

@register.filter
def road_direction(value):
    # Meanings used by the existing, source-backed spatial matching rules.
    labels = {'T':'双向','W':'沿起点至终点方向','A':'逆起点至终点方向'}
    return f'{labels[value]}（{value}）' if value in labels else f'来源方向代码 {value or "未提供"}'

@register.filter
def ny_datetime(value):
    if isinstance(value, str):
        try: value = parse_datetime(value)
        except ValueError: return '时间未明确'
    if not isinstance(value, datetime): return '时间未明确'
    if value.tzinfo: value = value.astimezone(ZoneInfo('America/New_York'))
    return value.strftime('%Y-%m-%d %H:%M') + '（纽约）'

@register.filter
def coordinates(value):
    match = re.search(r'POINT\s*\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)', str(value or ''), re.I)
    if not match: return '没有可用坐标'
    try: return f'经度 {float(match[1]):.6f} · 纬度 {float(match[2]):.6f}'
    except ValueError: return '没有可用坐标'
