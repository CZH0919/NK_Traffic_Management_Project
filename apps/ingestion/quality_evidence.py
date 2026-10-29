"""Read-only evidence cards. Never reconstruct an old detection as a historical fact."""
import json
import re
from django.db import connection
from django.utils import timezone

COUNTS = {f'{who}_{outcome}':f'number_of_{who}_{outcome}' for who in ('persons','pedestrians','cyclists','motorists') for outcome in ('injured','killed')}
FIELD_NAMES = {'borough':'行政区','latitude':'纬度','longitude':'经度','crash_date':'来源日期','crash_time':'来源时刻',
               'collision_id':'所属事故来源编号','vehicle_id':'车辆来源标识','person_age':'年龄','vehicle_year':'车辆年份',
               'vol':'15 分钟流量','source_row_id':'来源行标识','requestid':'采样请求','segmentid':'来源路段',
               'direction':'方向','yr':'年','m':'月','d':'日','hh':'小时','mm':'分钟','wktgeom':'来源空间位置'}
for field, raw in COUNTS.items():
    who, outcome=field.rsplit('_',1)
    FIELD_NAMES[raw]={'persons':'全部人员','pedestrians':'行人','cyclists':'骑行者','motorists':'驾乘人员'}[who]+('受伤' if outcome=='injured' else '死亡')
CURRENT = {v:k for k,v in COUNTS.items()}
CURRENT.update({'crash_date':'local_date','crash_time':'local_time','person_age':'age','vehicle_year':'manufacture_year',
                'vol':'volume','wktgeom':'geom'})


def display(value):
    if value is None or value == '': return '未提供'
    if isinstance(value,(list,dict)): return json.dumps(value,ensure_ascii=False)[:600]
    return str(value)[:600]


def evidence(issue, obj, *, check_position=False):
    raw=issue.record.payload if issue.record else {}
    code=issue.code
    keys=[]; explanation='请核对下列来源字段、已保存检测信息及当前业务值。'
    if code in ('missing_borough','reported_borough_geometry_mismatch','missing_or_outside_nyc_coordinate','invalid_geometry','official_location_notice'):
        keys=['borough','longitude','latitude'] if issue.snapshot.source.kind=='crashes' else ['wktgeom','segmentid','direction']
        explanation='核对来源行政区与坐标。当前业务值和本次空间核对不代表当时检测时的值。'
    elif code in ('missing_casualty_count','casualty_subtotal_mismatch'):
        keys=list(COUNTS.values()); explanation='分别核对全部人员与行人、骑行者、驾乘人员的伤亡计数；未提供的项目不能作为零。'
    elif code in ('orphan_collision','orphan_vehicle','vehicle_collision_mismatch','parent_time_mismatch'):
        keys=['collision_id','vehicle_id','crash_date','crash_time'];explanation='来源关联使用发布方编号。当前关联应指向同一起事故；日期与时间也需要核对。'
    elif code in ('invalid_date','missing_or_invalid_time','dst_ambiguous','dst_nonexistent','invalid_sampling_time','non_quarter_hour_sampling'):
        keys=['yr','m','d','hh','mm'] if issue.snapshot.source.kind=='traffic' else ['crash_date','crash_time']
        explanation='检查来源日期、时刻及纽约夏令时。流量还需核对是否为 15 分钟观测边界。'
    elif code in ('missing_volume','invalid_site_key','flow_not_statistically_usable'):
        keys=['source_row_id','requestid','segmentid','direction','yr','m','d','hh','mm','vol','wktgeom']
        explanation='核对地点、方向、时间及流量；存在冲突或缺失时保留原始观测，不能选择其中一条代替全部证据。'
    elif code in ('missing_source_key','duplicate_source_key','source_record_conflict','changed_source_record','changed_source_key'):
        keys=['collision_id','unique_id','source_row_id','vehicle_id','crash_date','crash_time','vol']
        keys=[key for key in keys if key in raw]
        explanation='来源编号相同的记录需要核对内容是否相同；可对照当前记录最初关联的来源原文，不能由重复编号推断内容一致。'
    elif code=='implausible_vehicle_year':keys=['vehicle_year','crash_date']
    details=issue.details if isinstance(issue.details,dict) else {}
    fields=details.get('fields',[])
    fields=[key for key in fields if isinstance(key,str)] if isinstance(fields,list) else []
    for key in fields:
        key=COUNTS.get(key,key)
        if key in raw and key not in keys:keys.append(key)
    rows=[]
    for key in keys:
        value=None
        if obj is not None:
            if key=='borough':value=obj.borough.chinese_name if getattr(obj,'borough_id',None) else None
            elif key in ('latitude','longitude'):
                point=re.search(r'POINT\s*\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)',str(getattr(obj,'geom','') or ''))
                value=point.group(1 if key=='longitude' else 2) if point else None
            elif key=='collision_id' and hasattr(obj,'collision'):
                value=obj.collision.source_record_id if obj.collision_id else None
            elif key=='vehicle_id' and hasattr(obj,'vehicle'):
                value=obj.vehicle.source_record_id if obj.vehicle_id else None
            else:value=getattr(obj,CURRENT.get(key,key),None)
        rows.append({'label':FIELD_NAMES.get(key,key),'source':display(raw.get(key)),'current':display(value)})
    prior=[]
    if code in ('changed_source_key','changed_source_record','source_record_conflict') and obj and obj.raw_record_id and obj.raw_record_id!=issue.record_id:
        original=obj.raw_record.payload
        for key in sorted(set(original)|set(raw)):
            if key!='source_row_id' and original.get(key)!=raw.get(key):
                prior.append({'label':FIELD_NAMES.get(key,key),'source':display(raw.get(key)),'current':display(original.get(key))})
    position=None
    eligible=code in ('missing_borough','reported_borough_geometry_mismatch') and bool(obj and getattr(obj,'geom',None))
    if eligible and check_position:
        with connection.cursor() as cursor:
            cursor.execute('''SELECT b.chinese_name,s.version FROM core_borough b
              LEFT JOIN ingestion_snapshot s ON s.id=b.boundary_snapshot_id
              WHERE ST_Covers(b.geom,ST_GeomFromEWKT(%s)) ORDER BY b.code''',[obj.geom])
            values=cursor.fetchall()
        position={'areas':'、'.join(r[0] for r in values) or '当前边界未覆盖此坐标',
                  'versions':'、'.join(dict.fromkeys(r[1] or '版本未登记' for r in values)), 'checked_at':timezone.now()}
    return {'explanation':explanation,'rows':rows,'differences':prior[:40],'difference_count':len(prior),
            'has_saved_detection':bool(details),'position_available':eligible,'position':position,
            'saved_fields':[FIELD_NAMES.get(COUNTS.get(k,k),k) for k in fields]}
