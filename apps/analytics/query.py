"""One cohort definition for aggregates, record drill-downs and spatial displays."""
import calendar
import hashlib
import json
from contextlib import contextmanager
from datetime import date,datetime,timedelta
from django.db import connection,transaction
from django.db.models import Max
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from apps.core.models import DataRevision
from apps.ingestion.models import Snapshot
from apps.network.matching import rows
from .forms import bbox_values

ENGINE='statistics-v2'
SEVERITY="CASE WHEN c.persons_killed>0 THEN 'fatal' WHEN c.persons_killed=0 AND c.persons_injured>0 THEN 'injured' WHEN c.persons_killed=0 AND c.persons_injured=0 THEN 'none' ELSE 'unknown' END"
SEVERITY_LABELS={'fatal':'有死亡','injured':'无死亡且有受伤','none':'报告伤亡均为零','unknown':'伤亡程度不确定'}
BOROUGH_LABELS={'1':'曼哈顿','2':'布朗克斯','3':'布鲁克林','4':'皇后区','5':'斯塔滕岛','unknown':'行政区未知'}
AGG="""count(*)::bigint n,sum(c.persons_injured)::bigint injured,sum(c.persons_killed)::bigint killed,
 count(*) FILTER(WHERE c.persons_injured IS NULL)::bigint missing_injured,
 count(*) FILTER(WHERE c.persons_killed IS NULL)::bigint missing_killed,
 count(*) FILTER(WHERE c.persons_injured IS NULL OR c.persons_killed IS NULL)::bigint missing_totals,
 count(*) FILTER(WHERE c.geom IS NULL)::bigint missing_position,
 count(*) FILTER(WHERE c.borough_id IS NULL)::bigint missing_borough,
 count(*) FILTER(WHERE c.time_status<>'unique')::bigint restricted_time"""

def plain(value):return json.loads(json.dumps(value,cls=DjangoJSONEncoder,ensure_ascii=False))
def digest(value):return hashlib.sha256(json.dumps(plain(value),sort_keys=True,separators=(',',':')).encode()).hexdigest()

@contextmanager
def consistent_read():
    outer=connection.in_atomic_block
    with transaction.atomic():
        if not outer:
            with connection.cursor() as c:c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        yield

def data_version():
    snapshots=list(Snapshot.objects.order_by('pk').values_list('pk','status','source_id','source_count','period_start','period_end','data_through'))
    return plain({'engine':ENGINE,'revision':DataRevision.objects.aggregate(n=Max('pk'))['n'] or 0,'snapshots':snapshots})

def coverage_intervals(kind):
    result=[]
    for snapshot in Snapshot.objects.filter(source__kind=kind,status='published',period_start__isnull=False,period_end__isnull=False).only('period_start','period_end','manifest'):
        m=snapshot.manifest
        if m.get('scope')=='user_supplied_file_only':continue
        expected=m.get('expected_rows')
        if not m.get('quality_checked') or expected is None or m.get('row_count')!=expected or m.get('count_after')!=expected:continue
        result.append((snapshot.period_start,snapshot.period_end))
    merged=[]
    for a,b in sorted(result):
        if merged and a.toordinal()<=merged[-1][1].toordinal()+1:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    return merged

def calendar_days(d):
    current=d['date_from'];last=d['date_to']
    while current<=last:
        if (not d.get('year') or current.year==int(d['year'])) and (d.get('month') in (None,'') or current.month==int(d['month'])) and (d.get('weekday') in (None,'') or current.weekday()==int(d['weekday'])):yield current
        current+=timedelta(days=1)

def coverage(d,kind='crashes'):
    intervals=coverage_intervals(kind);days=list(calendar_days(d));covered=sum(any(a<=day<=b for a,b in intervals) for day in days)
    return {'selected_days':len(days),'covered_days':covered,'complete':bool(days) and covered==len(days),
        'percent':round(100*covered/len(days),2) if days else None,'first':min((a for a,b in intervals),default=None),'last':max((b for a,b in intervals),default=None)}

def publication_clause(alias,kind):
    # Snapshot status is read in the same transaction as statistics. Avoid 4M raw-row joins when all typed sources are published.
    if not Snapshot.objects.filter(source__kind=kind).exclude(status='published').exists():return f"{alias}.origin='official' AND {alias}.is_active",[]
    return f"{alias}.origin='official' AND {alias}.is_active AND EXISTS(SELECT 1 FROM ingestion_stagedrecord raw JOIN ingestion_snapshot snap ON snap.id=raw.snapshot_id WHERE raw.id={alias}.raw_record_id AND snap.status='published')",[]

def scope_where(d,extra_bbox=None):
    where,args=publication_clause('c','crashes');parts=[where,'c.local_date BETWEEN %s AND %s'];args.extend([d['date_from'],d['date_to']])
    if d.get('borough'):parts.append('c.borough_id=%s');args.append(getattr(d['borough'],'pk',d['borough']))
    for key,expression in [('year','extract(year from c.local_date)'),('month','extract(month from c.local_date)'),('weekday','extract(isodow from c.local_date)-1')]:
        if d.get(key) not in (None,''):parts.append(expression+'=%s');args.append(int(d[key]))
    if d.get('hour') not in (None,''):
        if str(d['hour'])=='-1':parts.append('c.local_time IS NULL')
        else:parts.append('extract(hour from c.local_time)=%s');args.append(int(d['hour']))
    if d.get('severity'):parts.append('('+SEVERITY+')=%s');args.append(d['severity'])
    for kind,table,eligibility in [('road','network_roadsegment','t.is_canonical AND t.match_eligible'),('junction','network_junction','t.is_intersection IS TRUE')]:
        if d.get(kind):
            network=d.get('network');network=getattr(network,'pk',network)
            parts.append(f"EXISTS(SELECT 1 FROM network_spatialmatch m JOIN {table} t ON t.id=m.{kind}_id WHERE m.collision_id=c.id AND m.network_id=%s AND m.status='matched' AND m.input_revision=c.revision AND m.{kind}_id=%s AND t.is_active AND {eligibility})")
            args.extend([network,d[kind]])
    for value in (d.get('bbox'),extra_bbox):
        if value:
            bounds=bbox_values(value) if isinstance(value,str) else value
            parts.append('c.geom && ST_MakeEnvelope(%s,%s,%s,%s,4326)');args.extend(bounds)
    return ' AND '.join(parts),args

def cohort_sql(d):
    where,args=scope_where(d);return 'SELECT c.id FROM collisions_collision c WHERE '+where,args

def summary(d):
    where,args=scope_where(d);value=rows('SELECT '+AGG+' FROM collisions_collision c WHERE '+where,args)[0]
    if value['n']==0:value['injured']=value['killed']=0
    if d.get('borough'):
        global_where,global_args=scope_where({**d,'borough':None})
        value['unassigned_borough_in_period']=rows('SELECT count(*) n FROM collisions_collision c WHERE '+global_where+' AND c.borough_id IS NULL',global_args)[0]['n']
    else:value['unassigned_borough_in_period']=value['missing_borough']
    value['coverage']={kind:coverage(d,kind) for kind in ['crashes','vehicles','persons']}
    for kind,table in [('vehicles','collisions_vehicle'),('persons','collisions_person')]:
        child,child_args=publication_clause('x',kind)
        value[kind]=rows(f'SELECT count(*) n FROM {table} x WHERE {child} AND x.collision_id IN(SELECT c.id FROM collisions_collision c WHERE {where})',[*child_args,*args])[0]['n']
    value['severity']=rows('SELECT ('+SEVERITY+') key,count(*) n FROM collisions_collision c WHERE '+where+' GROUP BY 1',args)
    for row in value['severity']:row['label']=SEVERITY_LABELS[row['key']]
    return value

def category_key(expression):return f"CASE WHEN trim(coalesce({expression},''))='' THEN 'm:' ELSE 'v:'||{expression} END"

def grouped(d,dimension=None):
    dimension=dimension or d.get('dimension','month');where,args=scope_where(d)
    if dimension in ('vehicle_type','person_type','injury'):
        kind='vehicles' if dimension=='vehicle_type' else 'persons';table='collisions_vehicle' if kind=='vehicles' else 'collisions_person'
        child,child_args=publication_clause('x',kind);key=category_key('x.'+dimension)
        result=rows(f'SELECT {key} key,count(*) n,count(DISTINCT x.collision_id) supporting_crashes FROM {table} x WHERE {child} AND x.collision_id IN(SELECT c.id FROM collisions_collision c WHERE {where}) GROUP BY 1 ORDER BY n DESC,key',[*child_args,*args])
        complete=coverage(d,kind)['complete']
        for row in result:row.update(label='未提供' if row['key']=='m:' else row['key'][2:],value=row['n'],unit='车辆记录' if kind=='vehicles' else '人员记录',record_kind=kind,complete=complete)
        return result
    if dimension=='factor':
        result=rows(f"SELECT f.name key,count(DISTINCT x.collision_id) n FROM collisions_collisionfactor x JOIN collisions_factor f ON f.id=x.factor_id WHERE x.collision_id IN(SELECT c.id FROM collisions_collision c WHERE {where}) GROUP BY f.name ORDER BY n DESC,f.name",args)
        complete=coverage(d)['complete']
        for row in result:row.update(label=row['key'],value=row['n'],unit='涉及事故',record_kind='crashes',complete=complete)
        return result
    expressions={'year':"extract(year from c.local_date)::int::text",'month':"to_char(c.local_date,'YYYY-MM')",'date':"c.local_date::text",
        'weekday':"(extract(isodow from c.local_date)::int-1)::text",'hour':"coalesce(extract(hour from c.local_time)::int::text,'unknown')",
        'borough':"coalesce(c.borough_id::text,'unknown')",'severity':'('+SEVERITY+')'}
    result=rows('SELECT '+expressions[dimension]+' key,'+AGG+' FROM collisions_collision c WHERE '+where+' GROUP BY 1 ORDER BY 1',args)
    bykey={r['key']:r for r in result};intervals=coverage_intervals('crashes')
    slots={}
    if dimension in ('year','month','date'):
        for day in calendar_days(d):
            key=str(day.year) if dimension=='year' else day.strftime('%Y-%m') if dimension=='month' else day.isoformat()
            slots.setdefault(key,[]).append(day)
    elif dimension=='weekday':slots={str(i):[] for i in range(7) if d.get('weekday') in (None,'',str(i))}
    elif dimension=='hour':slots={str(i):[] for i in range(24) if d.get('hour') in (None,'',str(i))};slots['unknown']=[]
    elif dimension=='borough':slots={k:[] for k in BOROUGH_LABELS}
    elif dimension=='severity':slots={k:[] for k in SEVERITY_LABELS}
    keys=list(slots) if slots else list(bykey)
    for key in bykey:
        if key not in keys:keys.append(key)
    full=coverage(d)['complete'];metric=d.get('metric') or 'crashes';metric='n' if metric in ('crashes','records') else metric
    output=[]
    for key in keys:
        row=bykey.get(key,{'key':key,'n':0,'injured':0,'killed':0,'missing_injured':0,'missing_killed':0,'missing_totals':0})
        days=slots.get(key,[]);complete=all(any(a<=day<=b for a,b in intervals) for day in days) if days else full
        label=BOROUGH_LABELS.get(key,key) if dimension=='borough' else SEVERITY_LABELS.get(key,key) if dimension=='severity' else ['周一','周二','周三','周四','周五','周六','周日'][int(key)] if dimension=='weekday' else ('时间缺失' if key=='unknown' else f'{int(key):02d}:00') if dimension=='hour' else key
        row.update(label=label,value=row[metric] if complete else None,complete=complete,unit='起事故' if metric=='n' else '人',record_kind='crashes')
        output.append(row)
    return output

def bucket_clause(dimension,key,alias='c'):
    """Only server-owned dimensions and bound values can extend the cohort query."""
    if dimension=='factor':return f"EXISTS(SELECT 1 FROM collisions_collisionfactor cf JOIN collisions_factor f ON f.id=cf.factor_id WHERE cf.collision_id={alias}.id AND f.name=%s)",[key]
    if dimension in ('vehicle_type','person_type','injury'):return category_key(alias+'.'+dimension)+'=%s',[key]
    expressions={'year':f'extract(year from {alias}.local_date)::int::text','month':f"to_char({alias}.local_date,'YYYY-MM')",'date':f'{alias}.local_date::text',
        'weekday':f'(extract(isodow from {alias}.local_date)::int-1)::text','hour':f"coalesce(extract(hour from {alias}.local_time)::int::text,'unknown')",
        'borough':f"coalesce({alias}.borough_id::text,'unknown')",'severity':'('+SEVERITY+')'}
    if dimension not in expressions:raise ValueError('Unsupported bucket')
    return expressions[dimension]+'=%s',[key]

def drill_sql(d,kind,dimension=None,key=None):
    where,args=scope_where(d)
    if kind=='crashes':
        if dimension:
            if dimension in ('vehicle_type','person_type','injury'):raise ValueError('A child category requires child records')
            extra,values=bucket_clause(dimension,key);where+=' AND '+extra;args+=values
        return ' FROM collisions_collision c WHERE '+where,args
    if kind not in ('vehicles','persons'):raise ValueError('Unsupported record kind')
    child,childargs=publication_clause('x',kind);table='collisions_vehicle' if kind=='vehicles' else 'collisions_person'
    sql=f' FROM {table} x WHERE {child} AND x.collision_id IN(SELECT c.id FROM collisions_collision c WHERE {where})';args=childargs+args
    if dimension:
        if dimension not in (['vehicle_type'] if kind=='vehicles' else ['person_type','injury']):raise ValueError('Unsupported child bucket')
        extra,values=bucket_clause(dimension,key,'x');sql+=' AND '+extra;args+=values
    return sql,args

def analysis_payload(d):
    return {'summary':summary(d),'rows':grouped(d),'dimension':d.get('dimension','month'),'metric':d.get('metric','crashes')}

def compare_payload(d):
    a={k:v for k,v in d.items() if not k.startswith('compare_')};b={**a,'date_from':d['compare_from'],'date_to':d['compare_to'],'borough':d.get('compare_borough')}
    sa=summary(a);sb=summary(b);comparisons=[]
    for key,label,kind in [('n','事故数','crashes'),('injured','已报告受伤人数','crashes'),('killed','已报告死亡人数','crashes'),('vehicles','关联车辆','vehicles'),('persons','关联人员','persons')]:
        va=sa[key];vb=sb[key];ca=sa['coverage'][kind];cb=sb['coverage'][kind];reasons=[]
        if not(ca['complete'] and cb['complete']):reasons.append('来源日期覆盖不完整')
        if ca['selected_days']!=cb['selected_days']:reasons.append('所选天数不同，宜比较日均登记量')
        missing='missing_'+key
        if key in ('injured','killed') and (sa[missing] or sb[missing]):reasons.append('伤亡字段有缺失，只能查看已报告值')
        avg_a=va/ca['selected_days'] if va is not None and ca['complete'] else None
        avg_b=vb/cb['selected_days'] if vb is not None and cb['complete'] else None
        quality=(key not in ('injured','killed') or not(sa[missing] or sb[missing]))
        comparisons.append({'key':key,'label':label,'a':va,'b':vb,'difference':vb-va if va is not None and vb is not None else None,
            'percent':round(100*(vb-va)/va,2) if not reasons and va and vb is not None else None,'reasons':reasons,
            'daily_a':round(avg_a,4) if avg_a is not None else None,'daily_b':round(avg_b,4) if avg_b is not None else None,
            'daily_percent':round(100*(avg_b-avg_a)/avg_a,2) if quality and avg_a and avg_b is not None else None})
    ga={r['key']:r for r in grouped(a,'date')};gb={r['key']:r for r in grouped(b,'date')};series=[]
    na=(a['date_to']-a['date_from']).days+1;nb=(b['date_to']-b['date_from']).days+1
    for i in range(max(na,nb)):
        da=(a['date_from']+timedelta(days=i)).isoformat() if i<na else None;db=(b['date_from']+timedelta(days=i)).isoformat() if i<nb else None
        series.append({'label':f'第 {i+1} 日','date_a':da,'date_b':db,'a':ga.get(da,{}).get('value'),'b':gb.get(db,{}).get('value')})
    return {'a':sa,'b':sb,'comparisons':comparisons,'series':series,'metric':d.get('metric','crashes')}

def cached_payload(kind,form):
    from .models import AggregateCache
    conditions=form.conditions();key=digest({'kind':kind,'conditions':conditions,'engine':ENGINE});now=timezone.now()
    with consistent_read():
        version=data_version();token=digest(version)
        cached=AggregateCache.objects.filter(query_key=key,data_token=token,expires_at__gt=now).first()
        if cached:return cached.payload,True
        payload=(compare_payload if kind=='comparison' else analysis_payload)(form.cleaned_data)
        payload=plain({**payload,'data_version':version,'computed_at':now.isoformat(),'conditions':conditions})
    AggregateCache.objects.update_or_create(query_key=key,defaults={'data_token':token,'payload':payload,'expires_at':now+timedelta(minutes=15)})
    return payload,False
