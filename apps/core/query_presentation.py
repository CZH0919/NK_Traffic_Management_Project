"""Readable saved conditions without executing the saved analysis."""
from apps.analytics.query import BOROUGH_LABELS
from apps.analytics.forms import DIMENSIONS, METRICS
from apps.network.models import RoadSegment, Junction
from apps.traffic.models import SamplingSite


def attach_summaries(records):
    records=list(records)
    targets={}
    for key, model in [('road',RoadSegment),('junction',Junction),('site',SamplingSite)]:
        ids={int(x.conditions[key]) for x in records if str(x.conditions.get(key,'')).isdigit() and len(str(x.conditions[key]))<19}
        targets[key]={str(obj.pk):obj.display_name or getattr(obj,'name','') or getattr(obj,'street','') for obj in model.objects.filter(pk__in=ids)}
    labels={'q':'关键字','source_id':'来源编号','vehicle_type':'关联车辆类型','person_type':'关联人员类型','type':'类别原值',
            'injury':'伤害类别','category':'统一类别','collision_id':'事故内部编号','vehicle_id':'车辆内部编号',
            'year':'年份','month':'月份','weekday':'星期','hour':'小时','severity':'伤亡情况'}
    for saved in records:
        c=saved.conditions
        summary=[f"{c.get('date_from') or c.get('start') or '不限起日'} 至 {c.get('date_to') or c.get('end') or '不限止日'}",
                 BOROUGH_LABELS.get(str(c.get('borough')),'全市')]
        for key, names in targets.items():
            if c.get(key):summary.append(names.get(str(c[key])) or f'{dict(road="道路",junction="路口",site="采样地点")[key]} #{c[key]}')
        if c.get('dimension'):summary.append(dict(DIMENSIONS).get(c['dimension'],c['dimension']))
        if c.get('metric'):
            metric = 'records' if c.get('dimension') in ('vehicle_type','person_type','injury') else 'crashes' if c.get('dimension')=='factor' else c['metric']
            summary.append(dict(METRICS).get(metric,metric))
        if c.get('frequency'):summary.append({'week':'周度','month':'月度'}.get(c['frequency'],c['frequency']))
        if c.get('horizon'):summary.append(f'预测 {c["horizon"]} 期')
        if c.get('bbox'):summary.append('已限定地图视野')
        if saved.kind in ('crashes','vehicles','persons'):
            summary += [{'official':'官方记录','manual':'人工记录','demo':'演示记录','all':'全部来源'}.get(c.get('origin','official'),'来源待核对'),
                        {'active':'有效记录','inactive':'已失效','all':'全部状态'}.get(c.get('active','active'),'状态待核对')]
        for key,label in labels.items():
            if c.get(key):summary.append(f'{label}：{c[key]}')
        if c.get('compare_from'):summary.append(f'比较 {c["compare_from"]} 至 {c.get("compare_to", "未指定")} · {BOROUGH_LABELS.get(str(c.get("compare_borough")), "全市")}')
        saved.scope_summary=' · '.join(summary)
    return records
