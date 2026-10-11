"""Freeze report content without changing source facts or historical analyses."""
from collections import Counter
from django.db import connection, transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.core.services import require_actor, json_value
from apps.core.audit import record_event
from apps.ingestion.models import Snapshot
from apps.analytics.query import consistent_read, data_version, analysis_payload, compare_payload, plain
from apps.analytics.forms import make_form
from apps.traffic.models import SamplingSite
from apps.traffic.analysis import summarize, compare, exposure
from apps.network.queries import current_network
from .models import Report, ReportSnapshot
from .tables import table_payload

LABELS={'date_from':'历史起日','date_to':'历史止日','borough':'行政区编号','network':'网络版本编号','dimension':'统计维度','metric':'指标','year':'限定年份','month':'限定月份','weekday':'星期','hour':'当地小时','severity':'伤亡情况','road':'道路编号','junction':'路口编号','compare_from':'B 期间起日','compare_to':'B 期间止日','compare_borough':'B 行政区编号','rule':'规则编号','frequency':'周期','horizon':'外推期数','method':'方法','start':'观察起日','end':'观察止日','grain':'汇总粒度','site':'采样地点编号','compare_start':'B 观察起日','compare_site':'B 采样地点编号','bbox':'地理筛选范围'}
VALUE_LABELS={'crashes':'事故数','injured':'已报告受伤人数','killed':'已报告死亡人数','month':'月','week':'周','day':'日','hour':'小时','quarter':'15 分钟','auto':'比较段自动选择','naive':'上一期','seasonal':'季节滞后','ma':'移动平均','ses':'指数平滑'}
BASE_NOTES=['事故按纽约当地日期统计；官方发布记录不代表现实中的全部事故。',
            '已报告伤亡合计与完整伤亡总量不同，缺失不补零；事故、车辆与人员分别计数。',
            '报告保存生成时的确定结果。来源修订后旧报告保持原值，不能当作实时数据。']


def allow(actor):
    require_actor(actor,'generate_reports')
    require_actor(actor,'analyze_data')


def lock_owner(actor, key):
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s,%s)',[key,actor.pk])


def metric(key,label,value,unit=''):
    return dict(key=key,label=label,value=value,unit=unit)


def describe(conditions):
    from apps.core.models import Borough
    from apps.network.models import NetworkVersion
    ordered=['date_from','date_to','start','end','borough','compare_from','compare_to','compare_borough','compare_start','site','compare_site','road','junction','metric','dimension','frequency','horizon','method','grain','rule','network','year','month','weekday','hour','severity','bbox','level']
    output=[]
    if 'borough' not in conditions:
        output.append({'label':'行政区范围','value':'全市（含归属未知记录）' if not any(k in conditions for k in ('road','junction','site')) else '按所选对象，不额外限定行政区'})
    for k in sorted(conditions,key=lambda key:ordered.index(key) if key in ordered else len(ordered)):
        v=conditions[k]
        label=LABELS.get(k,k)
        value=VALUE_LABELS.get(str(v),str(v))
        if k in ('borough','compare_borough'):
            borough=Borough.objects.filter(pk=v).first()
            value=borough.chinese_name if borough else value
            label='A 行政区' if k=='borough' and 'compare_from' in conditions else '行政区' if k=='borough' else 'B 行政区'
        if k=='network':
            network=NetworkVersion.objects.filter(pk=v).first()
            value=network.label if network else value
            label='道路网络版本'
        if k=='level':
            label='分析层级';value={'city':'全市','borough':'行政区','road':'道路','junction':'路口'}.get(str(v),str(v))
        output.append({'label':label,'value':value})
    if 'compare_from' in conditions and 'compare_borough' not in conditions:
        output.append({'label':'B 行政区','value':'全市（含归属未知记录）'})
    return output


def source_metadata(version):
    states={int(row[0]):row[1] for row in version.get('snapshots',[]) if row[1]=='published'}
    return [dict(id=s.pk,name=s.source.name,publisher=s.source.publisher,dataset_id=s.source.dataset_id,homepage=s.source.homepage,
                 version=s.version,captured_at=s.captured_at,period_start=s.period_start,period_end=s.period_end,
                 data_through=s.data_through,source_count=s.source_count,status=states[s.pk],kind=s.source.kind)
            for s in Snapshot.objects.filter(pk__in=states).select_related('source').order_by('pk')]


@transaction.atomic
def enqueue(actor,main,source):
    allow(actor)
    if not main.is_valid() or source is not None and not source.is_valid():raise ValidationError('请修正报告范围。')
    lock_owner(actor,20261007)
    if Report.objects.filter(owner=actor,status__in=['queued','running']).exists():raise ValidationError('已有报告正在处理，请先查看结果。')
    d=main.cleaned_data;kind=d['kind']
    risk=d.get('risk_run') if kind=='risk' else None
    forecast=d.get('forecast_run') if kind=='forecast' else None
    if risk or forecast:conditions=(risk or forecast).conditions
    elif kind=='flow':
        conditions={k:str(json_value(v)) for k,v in source.cleaned_data.items() if v is not None and v!=''}
        conditions['site']=str(d['site'].pk)
    else:conditions=source.conditions()
    report=Report.objects.create(owner=actor,kind=kind,title=d['title'] or dict(main.fields['kind'].choices)[kind]+'报告',
                                 conditions=conditions,risk_run=risk,forecast_run=forecast)
    record_event(actor=actor,action='report_requested',object_type='Report',object_id=report.pk)
    return report


def current_payload(report):
    kind=report.kind
    payload={'schema_version':1,'metrics':[],'tables':[],'charts':[],'notes':list(BASE_NOTES),'scope':describe(report.conditions)}
    version=data_version()
    if kind in ('statistics','comparison'):
        form=make_form(kind,report.conditions)
        if not form.is_valid():raise ValidationError('报告范围已失效。')
        d=form.cleaned_data
        data=analysis_payload(d) if kind=='statistics' else compare_payload(d)
        if kind=='statistics':
            summary=data['summary']
            payload['metrics']=[metric(key,label,summary[key],unit) for key,label,unit in [('n','登记事故','起'),('injured','已报告受伤','人'),('killed','已报告死亡','人'),('vehicles','关联车辆','条'),('persons','关联人员','条')]]
            payload['metrics'] += [metric(key,label,summary[key],'起') for key,label in [('missing_injured','受伤总数字段缺失'),('missing_killed','死亡总数字段缺失'),('missing_position','缺少位置'),('unassigned_borough_in_period','同期全市行政区未知')]]
            columns=[('label','分类／期间'),('value','指标值'),('unit','统计单位'),('n','对应实体计数'),('supporting_crashes','支撑事故'),('missing_injured','受伤字段缺失'),('missing_killed','死亡字段缺失'),('complete','来源日期完整')]
            payload['tables'].append(table_payload('statistics','全部分组统计',columns,data['rows']))
            unit=data['rows'][0]['unit'] if data['rows'] else '起事故'
            timeline=data['dimension'] in ('year','month','date','weekday','hour')
            chart_rows=data['rows'] if timeline else sorted(data['rows'],key=lambda r:r['value'] if r['value'] is not None else -1,reverse=True)[:12]
            payload['charts'].append(dict(kind='line' if timeline else 'bar',title='所选范围的分组数量',unit=unit,labels=[r['label'] for r in chart_rows],series=[dict(name='已登记数量',values=[r['value'] for r in chart_rows])],note='按当前统计维度展示。'+('' if timeline else f'图示前 {len(chart_rows)} 项；完整 {len(data["rows"])} 项在数据表及附件中。')))
            payload['notes'].append(f'事故来源日期覆盖 {summary["coverage"]["crashes"]["covered_days"]} / {summary["coverage"]["crashes"]["selected_days"]} 天。')
        else:
            payload['metrics']=[metric('a','A 期间登记事故',data['a']['n'],'起'),metric('b','B 期间登记事故',data['b']['n'],'起')]
            columns=[('label','指标'),('a','A 数量'),('b','B 数量'),('difference','绝对变化 B−A'),('percent','相对变化（%）'),('daily_a','A 日均'),('daily_b','B 日均'),('daily_percent','日均变化（%）'),('reasons','可比限制')]
            payload['tables'].append(table_payload('comparison','指标并列与变化',columns,data['comparisons']))
            payload['tables'].append(table_payload('comparison_periods','全部相对日期序列',[('label','相对日'),('date_a','A 日期'),('date_b','B 日期'),('a','A 指标'),('b','B 指标')],data['series']))
            payload['charts'].append(dict(kind='line',title='A / B 期间按相对日对齐',unit='起' if data['metric']=='crashes' else '人',labels=[r['label'] for r in data['series']],series=[dict(name='A 期间',values=[r['a'] for r in data['series']]),dict(name='B 期间',values=[r['b'] for r in data['series']])],note='横轴为各期间的第几日，实际日期列于数据表；天数、来源覆盖和零基数限制随指标保留。'))
            for row in data['comparisons']:payload['notes'].extend(row['reasons'])
        payload['analysis']=data
    elif kind=='risk':
        run=report.risk_run
        if run.status!='succeeded':raise ValidationError('风险结果未完成。')
        version=run.data_version
        qs=run.results.all();graded=qs.filter(score__isnull=False)
        payload.update(analysis_at=run.finished_at,rule={'code':run.rule.code,'version':run.rule.version,'name':run.rule.name,'parameters':run.rule.parameters,'definition':run.rule.definition},analysis=run.summary)
        payload['metrics']=[metric('objects','分析对象',qs.count(),'个'),metric('graded','可以分级',graded.count(),'个'),metric('ungraded','不满足分级条件',qs.filter(score__isnull=True).count(),'个')]
        top=list(graded.order_by('-score','pk').values('label','score')[:12])
        payload['charts'].append(dict(kind='bar',title='历史强度评分较高的对象',unit='分',labels=[r['label'] for r in top],series=[dict(name='历史强度分',values=[r['score'] for r in top])],note=f'图示前 {len(top)} 个可评分对象；同分可能并列。分数不代表未来事故概率，未分级对象不等于低风险。'))
        payload['notes'] += ['历史强度在同层级／同期间参照中归一化；参照、权重和门槛随规则保存。','流量暴露量必须单独验证，历史强度分不当作单位流量事故率。']
    elif kind=='forecast':
        run=report.forecast_run
        if run.status!='succeeded':raise ValidationError('预测结果未完成。')
        version=run.data_version;summary=run.summary
        unit='起' if run.metric=='crashes' else '人'
        payload.update(analysis_at=run.finished_at,rule={'code':run.rule.code,'version':run.rule.version,'name':run.rule.name,'parameters':run.rule.parameters,'definition':run.rule.definition},analysis=summary)
        payload['metrics']=[metric('history','连续完整历史',summary['usable_periods'],'期'),metric('mae','全部可评估步长 MAE',summary.get('selected_mae'),unit),metric('naive_mae','上一期基线 MAE',summary.get('naive_mae'),unit)]
        payload['tables'].append(table_payload('history','完整历史与部分周期',[('start','周期起日'),('end','周期止日'),('value','建模值'),('reported','已登记／报告部分'),('n','支撑事故'),('covered_days','已覆盖天数'),('days','所选天数'),('reason','限制')],run.history))
        comparisons=[dict(method=row['label'],selected=row['selected'],step=x['step'],n=x['n'],mae=x['mae'],rmse=x['rmse']) for row in summary.get('comparisons',[]) for x in row['by_step']]
        payload['tables'].append(table_payload('methods','各方法逐步长误差',[('method','方法'),('selected','所选方法'),('step','提前期数'),('n','样本数'),('mae','MAE'),('rmse','RMSE')],comparisons))
        actual={r['start']:r['value'] for r in run.history}
        points={str(r.period_start):r for r in run.points.all()}
        labels=sorted(set(actual)|set(points))
        predicted={key:point.estimate for key,point in points.items()}
        if predicted:
            last=next((r for r in reversed(run.history) if r['value'] is not None),None)
            if last:predicted[last['start']]=last['value']
        payload['charts'].append(dict(kind='line',title='已观测历史与周期数量外推',unit=unit,labels=labels,series=[dict(name='完整历史',values=[actual.get(k) for k in labels]),dict(name='周期外推',values=[predicted.get(k) for k in labels])],lower=[points[k].lower if k in points else None for k in labels],upper=[points[k].upper if k in points else None for k in labels],note='完整历史实线、外推虚线、浅色为经验误差参考带；缺失断开。日期为周期起日，完整截止 '+str(run.training_end or '无')+'。'))
        payload['notes'] += summary.get('reasons',[])
        payload['notes'] += [summary['definition'],summary['interval_note'],'参数比较后固定参数，各步长误差需分别判断；多步平均较小不保证单步占优。']
    else:
        from .forms import prepare_forms
        main,form=prepare_forms(report.owner,{**report.conditions,'kind':'flow'})
        if not main.is_valid() or not form.is_valid():raise ValidationError('流量范围或地点已失效。')
        d=form.cleaned_data;site=main.cleaned_data['site']
        data=summarize(site,d['start'],d['end'],d['grain']);c=data['coverage']
        payload['metrics']=[metric(k,label,c[k],unit) for k,label,unit in [('expected','应有观测时隙','个'),('valid','有效时隙','个'),('missing','缺测时隙','个'),('conflict','冲突时隙','个'),('coverage','有效覆盖','%'),('observed_sum','有效观测部分合计','辆次')]]
        payload['tables'].append(table_payload('flow','全部时段与完整性',[(k,label) for k,label in [('label','时段'),('observed_sum','已观测部分'),('total','完整总量'),('expected','应有时隙'),('valid','有效时隙'),('missing','缺测'),('conflict','冲突'),('excluded','排除'),('ambiguous','歧义'),('coverage','覆盖（%）'),('complete','完整')]],data['series']))
        payload['tables'].append(table_payload('peaks','早晚高峰完整性',[('label','时段'),('expected','应有时隙'),('valid','有效时隙'),('total','完整总量'),('observed_sum','观测部分'),('complete_days','完整天数')],data['peaks']))
        payload['charts'].append(dict(kind='line',title=f'{site.display_name or site.street} · {site.direction}',unit='辆次',labels=[r['label'] for r in data['series']],series=[dict(name='完整时段总量',values=[r['total'] for r in data['series']]),dict(name='观测部分',values=[r['observed_sum'] if not r['complete'] else None for r in data['series']])],note='部分观测不外推为完整总量。来源为间歇采样，不是持续在线传感器。'))
        if d.get('compare_start'):
            other=SamplingSite.objects.filter(pk=d.get('compare_site') or site.pk,is_active=True).first()
            if not other:raise ValidationError('比较地点不存在。')
            comparison=compare(site,other,d['start'],d['end'],d['compare_start'])
            payload['tables'].append(table_payload('flow_compare','共同有效时隙比较',[('label','相对时段'),('a','A 流量'),('b','B 流量')],comparison['series']))
            payload['notes']+=comparison['reasons']
            payload['comparison']=comparison
        net=current_network();payload['exposure']=exposure(site,net,d['start'],d['end']) if net else {'eligible':False,'reasons':['缺少可核验网络。']}
        if payload['exposure']['eligible']:
            e=payload['exposure']
            payload['metrics'] += [metric('exposure_volume','已核验共同有效时隙双向流量',e['volume'],'辆次'),metric('exposure_collisions','同范围事故',e['collisions'],'起'),metric('exposure_rate','流量归一化事故率',e['rate_per_million'],'起／百万辆次')]
        payload['analysis']=data
        payload['notes']=['纽约当地观测时隙，夏令时歧义和重复小时分别处理。','部分流量、缺测、冲突与真实零不同；地点和方向不可任意互换。']+payload['exposure']['reasons']+payload['notes']
    payload['notes']=list(dict.fromkeys(payload['notes']))
    payload['sources']=source_metadata(version)
    domain='traffic' if kind=='flow' else 'crashes'
    payload['data_through']=max((s['data_through'] for s in payload['sources'] if s['kind']==domain and s['data_through']),default=None)
    return payload,version


def compute(pk):
    with consistent_read():
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_xact_lock_shared(%s,%s)',[20261004,2])
            if not cursor.fetchone()[0]:return False
        report=Report.objects.select_related('owner','risk_run__rule','forecast_run__rule').get(pk=pk)
        allow(report.owner)
        if report.status!='running':return True
        payload,version=current_payload(report)
        from .figures import make_figures
        payload['figures']=make_figures(payload['charts'])
        report.payload=plain(payload);report.data_version=plain(version)
        ReportSnapshot.objects.bulk_create([ReportSnapshot(report=report,snapshot_id=s['id']) for s in payload['sources']])
        report.finished_at=timezone.now();report.status='succeeded';report.error_code='';report.save()
        record_event(actor=report.owner,action='report_created',object_type='Report',object_id=pk)
        return True
