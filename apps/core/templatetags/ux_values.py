"""Presentation helpers; raw stored values and analysis payloads stay unchanged."""
from datetime import datetime
import re
from zoneinfo import ZoneInfo
from django import template
from django.utils.dateparse import parse_datetime

register = template.Library()

@register.filter
def group_number(value):
    if value is None: return '未知'
    try: return format(value, ',')
    except (ValueError, TypeError): return value

@register.filter
def time_explanation(value):
    return {'unique':'可确定唯一时刻', 'ambiguous':'夏令时重复小时，无法确定唯一时刻',
            'nonexistent':'当地时钟跳过了这一时刻', 'missing':'时间缺失或不合法'}.get(value, '时间状态未明确')

@register.filter
def borough_reason(value):
    return {'reported':'来源报告', 'spatial':'根据坐标确定', 'manual':'人工核对', 'unknown':'尚未确定'}.get(value, value)

@register.filter
def object_kind(value):
    return {'road':'道路','junction':'路口','site':'采样地点','collision':'事故'}.get(value,value)

@register.filter
def match_method(value):
    if str(value).startswith('manual'): return '人工核对'
    if value == 'no_geometry': return '没有可用位置'
    return '按名称、位置及方向核对'

@register.filter
def completeness_label(counts):
    n, missing = counts.get('n', 0), counts.get('missing_totals', 0)
    if not n: return '没有可评价记录'
    if not missing: return '100%（全部完整）'
    percentage = 100 * (n-missing) / n
    value = '接近 100%' if round(percentage,2) == 100 else f'{percentage:.2f}%'
    return f'{value}（{missing:,} 起不完整）'

@register.filter
def reason_text(value):
    replacements = {
        '同一采样请求、同一路段缺少唯一反向观测，事故方向口径不能与单向流量相除。':'缺少同期、同一路段且可唯一对应的反向采样，无法核对双向流量。',
        '尚未人工核实这段历史期间的道路几何、方向与采样覆盖；当前路网不能自动证明历史口径。':'尚未确认这段历史期间的道路范围、方向与采样位置一致。',
        '双向没有共同有效观测时隙。':'两个方向没有同时有效的 15 分钟观测。',
        '共同有效时隙流量为零，不能作为分母。':'共同观测期间的流量为零，无法计算事故率。',
        '没有共同有效的 15 分钟时隙。':'两侧没有可对照的有效 15 分钟观测。',
        '未关联到目标的事故不进入道路／路口分子；没有关联事故的目标不列入结果，不代表安全。':'道路和路口评价仅统计已建立关联的事故；没有关联事故的对象不列入结果，不能据此判断其安全程度。',
    }
    text = replacements.get(value, value)
    if isinstance(text, str): text = text.replace('分子范围尚不可靠。','尚不能确定共同观测期间的完整事故数量。')
    return text

@register.filter
def anomaly_reason(value):
    return str(value or '').replace('不是因果认定或实时预警。', '').replace('不是因果认定或实时预警', '').rstrip('；。 ')
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
