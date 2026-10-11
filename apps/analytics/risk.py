"""Versioned descriptive intensity, never a probability or a traffic-exposure rate."""
from bisect import bisect_left,bisect_right
from datetime import date,timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import connection,transaction
from django.db.models import Max
from django.utils import timezone
from apps.core.services import require_actor,clean_reason,maintenance_lock
from apps.core.audit import record_event
from apps.network.matching import rows
from .models import RuleVersion,RiskRun,RiskResult,AnalysisTarget
from .query import AGG,scope_where,coverage,coverage_intervals,consistent_read,data_version,digest,plain

DEFAULT_PARAMETERS={'method':'empirical-midrank-v1','weights':{'frequency':50,'severity':35,'recent':15},'death_weight':5,
    'recent_days':90,'min_support':5,'completeness':95,'thresholds':[40,60,80],'city_min_months':6}
GRADES={'lower':'相对较低','moderate':'中等','high':'较高','very_high':'很高','insufficient':'不分级'}

def percentile(value,reference):
    if value<=0:return 0.0
    if not reference:return None
    return 100*(bisect_left(reference,value)+bisect_right(reference,value))/(2*len(reference))

def grade(score,thresholds):
    if score is None:return 'insufficient'
    return ['lower','moderate','high','very_high'][bisect_right(thresholds,score)]

@transaction.atomic
def create_rule(actor,data):
    require_actor(actor,'maintain_data');clean_reason(data['reason'])
    from .forms import RuleForm
    form=RuleForm(data)
    if not form.is_valid():raise ValidationError('规则参数不合法。')
    d=form.cleaned_data
    with maintenance_lock():
        latest=RuleVersion.objects.filter(code='historical-intensity').aggregate(v=Max('version'))['v'] or 0
        parameters={**DEFAULT_PARAMETERS,'weights':{k:d[k] for k in ('frequency','severity','recent')},
            **{k:d[k] for k in ('death_weight','recent_days','min_support','completeness')}}
        result=RuleVersion.objects.create(version=latest+1,name=d['name'],parameters=parameters,definition=d['reason'],created_by=actor)
        record_event(actor=actor,action='risk_rule_created',object_type='RuleVersion',object_id=result.pk,details={'revision':result.version})
        return result

@transaction.atomic
def enqueue(actor,form):
    require_actor(actor,'analyze_data')
    if not form.is_valid():raise ValidationError('请先修正风险查询条件。')
    d=form.cleaned_data;conditions=form.conditions()
    with connection.cursor() as c:c.execute('SELECT pg_advisory_xact_lock(%s,%s)',[20261005,actor.pk])
    if RiskRun.objects.filter(requested_by=actor,status__in=['queued','running']).exists():raise ValidationError('已有风险计算正在等待或执行，请先查看其结果。')
    result=RiskRun.objects.create(requested_by=actor,rule=d['rule'],network=d.get('network'),level=d['level'],date_from=d['date_from'],date_to=d['date_to'],conditions=conditions)
    record_event(actor=actor,action='risk_requested',object_type='RiskRun',object_id=result.pk)
    return result

def score_components(features,reference,parameters):
    result={};total=0
    for key in ('frequency','severity','recent'):
        normalized=percentile(features[key],reference[key]);weight=parameters['weights'][key]
        contribution=normalized*weight/100
        result[key]={'value':round(features[key],8),'percentile':round(normalized,5),'weight':weight,'contribution':round(contribution,5)}
        total+=contribution
    return round(total,3),result

def target_aggregates(d,level,recent_start):
    where,args=scope_where(d);params=[]
    network=getattr(d.get('network'),'pk',d.get('network'))
    if level=='city':target="'city'";join='';label="'纽约全市'";point='NULL::text'
    elif level=='borough':target='c.borough_id::text';join=' JOIN core_borough b ON b.code=c.borough_id';label='b.chinese_name';point='ST_AsGeoJSON(ST_PointOnSurface(b.geom))'
    else:
        table='network_roadsegment' if level=='road' else 'network_junction'
        eligibility='t.is_canonical AND t.match_eligible' if level=='road' else 't.is_intersection IS TRUE'
        target='t.id::text';label="coalesce(nullif(t.display_name,''),nullif(t.name,''),'未命名')"
        point='ST_AsGeoJSON(ST_Centroid(t.geom))'
        join=f" JOIN network_spatialmatch m ON m.collision_id=c.id AND m.status='matched' AND m.input_revision=c.revision AND m.network_id=%s JOIN {table} t ON t.id=m.{level}_id AND t.is_active AND {eligibility}"
        params=[network]
    sql=f'SELECT {target} target_key,{label} label,{point} point,'+AGG+f",count(*) FILTER(WHERE c.local_date>=%s)::bigint recent_n FROM collisions_collision c{join} WHERE {where} GROUP BY 1,2,3 ORDER BY 1"
    values=rows(sql,[recent_start,*params,*args])
    if level=='city' and not values:values=[{'target_key':'city','label':'纽约全市','point':None,'n':0,'injured':0,'killed':0,'missing_totals':0,'missing_injured':0,'missing_killed':0,'missing_borough':0,'missing_position':0,'restricted_time':0,'recent_n':0}]
    if level=='borough':
        from apps.core.models import Borough
        found={r['target_key'] for r in values}
        for b in Borough.objects.all():
            if str(b.pk) not in found:values.append({'target_key':str(b.pk),'label':b.chinese_name,'point':None,'n':0,'injured':0,'killed':0,'missing_totals':0,'missing_injured':0,'missing_killed':0,'missing_borough':0,'missing_position':0,'restricted_time':0,'recent_n':0})
    return values

def city_reference(d,parameters):
    intervals=coverage_intervals('crashes')
    if not intervals:return [],0
    begin=min(a for a,b in intervals)
    base={**d,'date_from':begin,'borough':None};where,args=scope_where(base)
    values=rows("SELECT to_char(c.local_date,'YYYY-MM') key,"+AGG+' FROM collisions_collision c WHERE '+where+' GROUP BY 1 ORDER BY 1',args)
    reference=[]
    import calendar
    for row in values:
        y,m=map(int,row['key'].split('-'));a=date(y,m,1);b=date(y,m,calendar.monthrange(y,m)[1])
        if b>d['date_to'] or not all(any(left<=a+timedelta(days=i)<=right for left,right in intervals) for i in range((b-a).days+1)):continue
        n=row['n'];complete=(n-row['missing_totals'])/n if n else 0
        if n<parameters['min_support'] or complete*100<parameters['completeness']:continue
        days=(b-a).days+1
        reference.append({'frequency':n/days,'severity':((row['injured'] or 0)+parameters['death_weight']*(row['killed'] or 0))/days,'recent':n/days})
    return reference,len(reference)

def compute_run(pk):
    """Worker-owned transaction, protected against concurrent application maintenance."""
    from .forms import make_form
    with consistent_read():
        with connection.cursor() as c:
            c.execute('SELECT pg_try_advisory_xact_lock_shared(%s,%s)',[20261004,2])
            if not c.fetchone()[0]:return False
        run=RiskRun.objects.select_related('requested_by','rule','network').get(pk=pk)
        require_actor(run.requested_by,'analyze_data')
        if run.status!='running':return True
        form=make_form('risk',run.conditions)
        if not form.is_valid():raise ValidationError('风险对象条件已失效。')
        d=form.cleaned_data;p=run.rule.parameters
        # Borough scores always use all five peer boroughs; selection only limits displayed results.
        selected_borough=d.get('borough')
        base={**d,'borough':None} if run.level=='borough' else d
        days=(d['date_to']-d['date_from']).days+1;recent_days=min(p['recent_days'],days);recent_start=d['date_to']-timedelta(days=recent_days-1)
        cov=coverage(base);version=data_version();raw=target_aggregates(base,run.level,recent_start)
        where,args=scope_where(base)
        source_context=rows('SELECT count(*) n,count(*) FILTER(WHERE c.borough_id IS NULL) unknown_borough FROM collisions_collision c WHERE '+where,args)[0]
        assigned_crashes=sum(r['n'] for r in raw)
        eligible=[]
        for item in raw:
            n=item['n'];item['complete_percent']=round(100*(n-item['missing_totals'])/n,5) if n else None
            item['features']={'frequency':n/days,'severity':((item['injured'] or 0)+p['death_weight']*(item['killed'] or 0))/days,'recent':item['recent_n']/recent_days}
            reasons=[]
            if not cov['complete']:reasons.append('来源日期覆盖不完整')
            if n<p['min_support']:reasons.append(f"支撑事故少于 {p['min_support']} 起")
            if n and item['complete_percent']<p['completeness']:reasons.append('伤亡字段完整率不足')
            item['reasons']=reasons
            if not reasons:eligible.append(item)
        if run.level=='city':
            reference_values,reference_size=city_reference(d,p)
            if reference_size<p['city_min_months']:
                for item in raw:item['reasons'].append('截至期末的完整参照月份不足')
            reference={k:sorted(row[k] for row in reference_values) for k in ('frequency','severity','recent')}
        else:
            reference_size=len(eligible);reference={k:sorted(row['features'][k] for row in eligible) for k in ('frequency','severity','recent')}
        # Materialize only targets with recorded associations, plus city/five boroughs. No incident is not a safety claim.
        keys=[int(r['target_key']) for r in raw] if run.level!='city' else []
        field=run.level if run.level!='city' else None
        existing=AnalysisTarget.objects.filter(scope=run.level)
        existing_map={str(getattr(t,field+'_id')) if field else 'city':t.pk for t in existing}
        new=[AnalysisTarget(scope=run.level,**{field+'_id':int(r['target_key'])} if field else {}) for r in raw if r['target_key'] not in existing_map]
        AnalysisTarget.objects.bulk_create(new,batch_size=1000)
        existing_map={str(getattr(t,field+'_id')) if field else 'city':t.pk for t in AnalysisTarget.objects.filter(scope=run.level)}
        results=[]
        import json
        for item in raw:
            if run.level=='borough' and selected_borough and item['target_key']!=str(selected_borough.pk):continue
            reasons=item.pop('reasons');features=item.pop('features');key=item.pop('target_key');label=item.pop('label');point=item.pop('point')
            score,components=(None,{}) if reasons or not reference_size else score_components(features,reference,p)
            if item['missing_totals'] and not reasons:reasons.append('按已报告伤亡计算，负担可能低估')
            results.append(RiskResult(run=run,target_id=existing_map[key],label=label[:240],counts=plain({**item,'days':days,'recent_days':recent_days}),components=components,
                score=score,grade=grade(score,p['thresholds']),limitation='；'.join(reasons),point=(json.loads(point)['coordinates'] if point else None)))
        ranked=sorted((r for r in results if r.score is not None),key=lambda r:(-r.score,-r.counts['n'],r.target_id))
        previous=None;rank=0
        for i,r in enumerate(ranked,1):
            if r.score!=previous:rank=i;previous=r.score
            r.rank=rank
        RiskResult.objects.bulk_create(results,batch_size=1000)
        run.data_version=version;run.summary=plain({'coverage':cov,'reference_size':reference_size,'reference_type':'截至期末的完整全市月份' if run.level=='city' else '同层级同期间合格对象',
            'reference_ranges':{k:{'min':min(v) if v else None,'max':max(v) if v else None} for k,v in reference.items()},'targets':len(results),'graded':len(ranked),'not_graded':len(results)-len(ranked),
            'recent_from':recent_start,'definition':'历史登记事故强度；非事故概率，非流量事故率','borough_reference_all_five':run.level=='borough',
            'base_crashes':source_context['n'],'assigned_crashes':assigned_crashes,'unknown_borough':source_context['unknown_borough'],
            'unassigned_policy':'未关联到目标的事故不进入道路／路口分子；没有关联事故的目标不列入结果，不代表安全。'})
        run.status='succeeded';run.finished_at=timezone.now();run.error_code='';run.save()
        record_event(actor=run.requested_by,action='risk_computed',object_type='RiskRun',object_id=run.pk,details={'count':len(results),'revision':run.rule.version})
        return True

def is_stale(run):return bool(run.data_version) and digest(run.data_version)!=digest(data_version())
