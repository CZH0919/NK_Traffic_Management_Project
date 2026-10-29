"""Observation-supported flow summaries. Missing measurements are never zero-filled."""
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError
from django.db.models import F, Min, Max
from apps.collisions.models import Collision
from apps.network.models import SamplingRoadMatch, SpatialMatch
from apps.network.matching import rows
from .models import TrafficObservation, SamplingSite

NY=ZoneInfo('America/New_York')
UTC=timezone.utc
QUARTER=timedelta(minutes=15)
LIMITS={'quarter':31,'hour':93,'day':366}

def official_observations():
    return TrafficObservation.objects.filter(is_active=True,origin='official',raw_record__snapshot__status='published',site__is_active=True)

def bounds(site):
    return official_observations().filter(site=site).aggregate(first=Min('local_date'),last=Max('local_date'))

def validate_range(start,end,grain='day'):
    if grain not in LIMITS: raise ValidationError('请选择 15 分钟、小时或日粒度。')
    if not isinstance(start,date) or not isinstance(end,date) or end<start: raise ValidationError('起止日期不合法。')
    if (end-start).days+1>LIMITS[grain]: raise ValidationError(f'当前粒度最多查询 {LIMITS[grain]} 天，请缩小范围或切换粒度。')

def expected_slots(start,end):
    current=datetime.combine(start,time.min,NY).astimezone(UTC)
    stop=datetime.combine(end+timedelta(days=1),time.min,NY).astimezone(UTC)
    while current<stop:
        yield current
        current+=QUARTER

def slot_data(site,start,end):
    grouped=defaultdict(list)
    data=official_observations().filter(site=site,local_date__range=(start,end)).values(
        'local_date','local_time','observed_at','time_status','quality_status','volume','conflict_id')
    source_rows=0
    for row in data:
        grouped[(row['local_date'],row['local_time'])].append(row); source_rows+=1
    result=[]
    for stamp in expected_slots(start,end):
        local=stamp.astimezone(NY); values=grouped.get((local.date(),local.time().replace(tzinfo=None)),[])
        state='missing'; volume=None
        if values:
            if len(values)>1 or any(r['quality_status']=='conflict' or r['conflict_id'] for r in values): state='conflict'
            elif values[0]['time_status']!='unique': state='ambiguous'
            elif values[0]['quality_status']!='valid': state='excluded'
            elif values[0]['observed_at']!=stamp: state='excluded'
            else: state='valid';volume=values[0]['volume']
        result.append({'stamp':stamp,'local':local,'state':state,'volume':volume})
    return result,source_rows

def aggregate_slots(slots,grain='day'):
    groups={}
    for slot in slots:
        local=slot['local']
        label=local.strftime('%Y-%m-%d') if grain=='day' else local.strftime('%Y-%m-%d %H:00 %z') if grain=='hour' else local.strftime('%Y-%m-%d %H:%M %z')
        row=groups.setdefault(label,dict(label=label,expected=0,valid=0,missing=0,conflict=0,excluded=0,ambiguous=0,observed_sum=None,total=None))
        row['expected']+=1;row[slot['state']]+=1
        if slot['state']=='valid': row['observed_sum']=(row['observed_sum'] or 0)+slot['volume']
    for row in groups.values():
        row['complete']=row['valid']==row['expected']
        row['total']=row['observed_sum'] if row['complete'] else None
        row['coverage']=round(100*row['valid']/row['expected'],1)
    return list(groups.values())

def summarize(site,start,end,grain='day'):
    validate_range(start,end,grain)
    slots,source_rows=slot_data(site,start,end)
    overall=aggregate_slots(slots,'day')
    coverage={name:sum(x[name] for x in overall) for name in ['expected','valid','missing','conflict','excluded','ambiguous']}
    coverage.update(source_rows=source_rows,coverage=round(100*coverage['valid']/coverage['expected'],1) if slots else 0,
        observed_sum=sum(s['volume'] for s in slots if s['state']=='valid') if coverage['valid'] else None,
        complete_days=sum(x['complete'] for x in overall),days=len(overall))
    peaks=[]
    for name,begin,stop in [('早高峰 07:00—10:00',7,10),('晚高峰 16:00—19:00',16,19)]:
        selected=[s for s in slots if begin<=s['local'].hour<stop]
        rows_=aggregate_slots(selected)
        peaks.append(dict(label=name,expected=len(selected),valid=sum(x['valid'] for x in rows_),
            total=sum(x['total'] for x in rows_) if rows_ and all(x['complete'] for x in rows_) else None,
            observed_sum=sum(x['observed_sum'] or 0 for x in rows_) if any(x['valid'] for x in rows_) else None,
            complete_days=sum(x['complete'] for x in rows_)))
    return {'series':aggregate_slots(slots,grain),'coverage':coverage,'peaks':peaks,'daily_coverage':overall,
        'grain':grain,'start':start.isoformat(),'end':end.isoformat(),'site':site.pk,'direction':site.direction}

def compare(site,other,start,end,other_start):
    other_end=other_start+(end-start);validate_range(start,end);validate_range(other_start,other_end)
    reasons=[]
    if site.pk!=other.pk: reasons.append('地点不同；可并列查看，但不能解释为同一地点流量变化。')
    if site.direction!=other.direction: reasons.append('观测方向不同。')
    if start.weekday()!=other_start.weekday(): reasons.append('起始星期不同，工作日／周末构成不一致。')
    a,_=slot_data(site,start,end);b,_=slot_data(other,other_start,other_end)
    def index(values,first):
        return {((s['local'].date()-first).days,s['local'].hour,s['local'].minute,s['local'].fold):s
            for s in values if s['state']=='valid'}
    ia=index(a,start);ib=index(b,other_start);common=sorted(ia.keys()&ib.keys())
    if not common: reasons.append('没有共同有效的 15 分钟时隙。')
    va=sum(ia[k]['volume'] for k in common) if common else None
    vb=sum(ib[k]['volume'] for k in common) if common else None
    timeline=sorted({((s['local'].date()-start).days,s['local'].hour,s['local'].minute,s['local'].fold) for s in a}
        |{((s['local'].date()-other_start).days,s['local'].hour,s['local'].minute,s['local'].fold) for s in b})
    shared=set(common)
    return {'comparable':not reasons,'reasons':reasons,'common_slots':len(common),'a_valid':len(ia),'b_valid':len(ib),
        'a_volume':va,'b_volume':vb,'difference':vb-va if not reasons else None,
        'percent':round(100*(vb-va)/va,2) if not reasons and va else None,
        'other_start':other_start.isoformat(),'other_end':other_end.isoformat(),
        'series':[{'label':f'第 {k[0]+1} 日 {k[1]:02d}:{k[2]:02d}'+('（重复小时）' if k[3] else ''),
            'a':ia[k]['volume'] if k in shared else None,'b':ib[k]['volume'] if k in shared else None} for k in timeline]}

def exposure(site,network,start,end):
    """A rate needs reviewed historical road scope and matched two-way observation windows."""
    result={'eligible':False,'reasons':[],'rate_per_million':None,'volume':None,'collisions':None,'slots':0}
    match=SamplingRoadMatch.objects.filter(site=site,network=network,status='matched',input_revision=site.revision,
        road__is_active=True,road__is_canonical=True,road__match_eligible=True).select_related('road').first()
    if not match:
        result['reasons'].append('采样地点尚无当前有效道路关联。');return result
    result['road_id']=match.road_id
    result['historical_collisions']=Collision.objects.filter(is_active=True,origin='official',raw_record__snapshot__status='published',
        local_date__range=(start,end),spatial_matches__network=network,spatial_matches__road=match.road,
        spatial_matches__status='matched',spatial_matches__input_revision=F('revision')).count()
    opposite={'NB':'SB','SB':'NB','EB':'WB','WB':'EB'}.get(site.direction)
    partners=list(SamplingRoadMatch.objects.filter(network=network,road=match.road,status='matched',
        site__request=site.request,site__direction=opposite,site__is_active=True,input_revision=F('site__revision')).select_related('site'))
    if len(partners)!=1:
        result['reasons'].append('同一采样请求、同一路段缺少唯一反向观测，事故方向口径不能与单向流量相除。');return result
    partner=partners[0];result['partner_id']=partner.site_id
    for m in (match,partner):
        evidence=m.evidence or {}
        if not (m.reviewed_by_id and m.direction_compatible and evidence.get('target_revision')==match.road.revision and evidence.get('exposure_start','9999')<=start.isoformat()
                and evidence.get('exposure_end','0000')>=end.isoformat()):
            result['reasons'].append('尚未人工核实这段历史期间的道路几何、方向与采样覆盖；当前路网不能自动证明历史口径。');return result
    a,_=slot_data(site,start,end);b,_=slot_data(partner.site,start,end)
    ia={s['stamp']:s['volume'] for s in a if s['state']=='valid'};ib={s['stamp']:s['volume'] for s in b if s['state']=='valid'}
    common=sorted(ia.keys()&ib.keys());result['slots']=len(common)
    if not common: result['reasons'].append('双向没有共同有效观测时隙。');return result
    volume=sum(ia[t]+ib[t] for t in common);result['volume']=volume
    if not volume: result['reasons'].append('共同有效时隙流量为零，不能作为分母。');return result
    # Potentially relevant nearby records with missing time or stale/unresolved spatial associations inhibit a rate.
    unresolved=rows("""SELECT count(*) n FROM collisions_collision c LEFT JOIN network_spatialmatch m
      ON m.collision_id=c.id AND m.network_id=%s JOIN ingestion_stagedrecord raw ON raw.id=c.raw_record_id
      JOIN ingestion_snapshot snap ON snap.id=raw.snapshot_id
      WHERE c.is_active AND c.origin='official' AND snap.status='published' AND c.local_date BETWEEN %s AND %s
      AND c.geom && ST_Expand(ST_GeomFromEWKT(%s),0.0011)
      AND ST_DWithin(c.geom::geography,ST_GeomFromEWKT(%s)::geography,80)
      AND (c.time_status<>'unique' OR m.id IS NULL OR m.input_revision<>c.revision OR m.status<>'matched')""",
      [network.pk,start,end,match.road.geom,match.road.geom])[0]['n']
    result['nearby_restricted']=unresolved
    if unresolved: result['reasons'].append(f'附近有 {unresolved} 条事故时间或空间关联受限，分子范围尚不可靠。');return result
    stamps=set(common)
    crashes=Collision.objects.filter(is_active=True,origin='official',raw_record__snapshot__status='published',
        time_status='unique',local_date__range=(start,end),spatial_matches__network=network,spatial_matches__road=match.road,
        spatial_matches__status='matched',spatial_matches__input_revision=F('revision')).values_list('occurred_at',flat=True)
    count=0
    for stamp in crashes:
        slot=stamp.replace(minute=stamp.minute//15*15,second=0,microsecond=0)
        count+=slot in stamps
    result.update(eligible=True,collisions=count,rate_per_million=round(count/volume*1000000,4),
        scope='仅含可定位、当前道路关联有效且落入双向共同观测时隙的事故；排除路口事故，不能视为完整道路事故风险。')
    return result
