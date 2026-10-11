from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from apps.core.services import require_actor,clean_reason,maintenance_lock,audit_changes,ConcurrentChange
from apps.traffic.models import SamplingSite
from apps.collisions.models import Collision
from .models import RoadSegment,Junction,SpatialMatch,SamplingRoadMatch
from .matching import rows,collision_proposal,site_proposal,RULE,direction_matches
from .queries import current_network

CATALOG={'road':RoadSegment,'junction':Junction,'site':SamplingSite}

@transaction.atomic
def initialize_match(kind,pk,actor,input_revision,reason):
    require_actor(actor,'maintain_data');reason=clean_reason(reason)
    with maintenance_lock():
        network=current_network()
        if not network:raise ValidationError('没有已发布网络。')
        model=SpatialMatch if kind=='collision' else SamplingRoadMatch
        owner=(Collision if kind=='collision' else SamplingSite).objects.select_for_update().get(pk=pk)
        if owner.revision!=input_revision:raise ConcurrentChange('对象已修订，请刷新后重试。')
        key='collision' if kind=='collision' else 'site'
        old=model.objects.filter(network=network,**{key:owner}).first()
        if old:return old
        data=(collision_proposal if kind=='collision' else site_proposal)(owner,network.pk)
        result=model.objects.create(network=network,**{key:owner},**data)
        audit_changes(actor,'spatial_reviewed',type(result).__name__,result.pk,{'status':(None,result.status)},reason,revision=result.revision,snapshot_ids=[network.snapshot_id])
        return result

@transaction.atomic
def maintain(kind,pk,actor,revision,data,reason):
    require_actor(actor,'maintain_data');reason=clean_reason(reason)
    with maintenance_lock():
        obj=CATALOG[kind].objects.select_for_update().get(pk=pk)
        if obj.revision!=revision: raise ConcurrentChange('记录已被修改，请刷新后重试。')
        allowed={'display_name','review_note','is_active'}|({'is_intersection'} if kind=='junction' else set())
        if set(data)-allowed: raise ValidationError('不能修改来源标识、坐标或采样事实。')
        changes={}
        for key,value in data.items():
            if getattr(obj,key)!=value: changes[key]=(getattr(obj,key),value);setattr(obj,key,value)
        if kind=='junction' and 'is_intersection' in changes:
            obj.classification_method='manual:review';obj.classification_evidence={**obj.classification_evidence,'manual_reason':reason}
        if changes:
            obj.revision+=1;obj.full_clean();obj.save()
            audit_changes(actor,'site_changed' if kind=='site' else 'network_changed',kind,pk,changes,reason,revision=obj.revision)
        return obj

@transaction.atomic
def review_match(kind,pk,actor,revision,input_revision,action,target_kind=None,target_id=None,reason='',exposure_start=None,exposure_end=None):
    require_actor(actor,'maintain_data');reason=clean_reason(reason)
    model=SpatialMatch if kind=='collision' else SamplingRoadMatch
    with maintenance_lock():
        match=model.objects.select_for_update().select_related('network').get(pk=pk)
        owner=(Collision if kind=='collision' else SamplingSite).objects.select_for_update().get(pk=match.collision_id if kind=='collision' else match.site_id)
        if match.revision!=revision or owner.revision!=input_revision: raise ConcurrentChange('对象或关联已被修改，请刷新后重试。')
        if match.network.snapshot.status!='published': raise ValidationError('只能核查已发布网络。')
        before={k:getattr(match,k) for k in ['status','method','road_id','distance_m','input_revision','evidence']}
        if kind=='collision': before['junction_id']=match.junction_id
        match.road=None
        if kind=='collision': match.junction=None
        match.input_revision=owner.revision;match.rule_version=RULE
        if action=='recalculate':
            data=(collision_proposal if kind=='collision' else site_proposal)(owner,match.network_id)
            for key,value in data.items():setattr(match,key,value)
            match.reviewed_by=None;match.reviewed_at=None
        elif action=='unmatched':
            match.status='unmatched';match.method='manual:unmatched';match.distance_m=None;match.evidence={'reason':reason}
            match.reviewed_by=actor;match.reviewed_at=timezone.now()
            if kind=='site':match.direction_compatible=None;match.source_id_match=False
        elif action=='confirm':
            if target_kind not in ('road','junction') or (kind=='site' and target_kind!='road') or not target_id: raise ValidationError('请选择有效候选对象。')
            target=CATALOG[target_kind].objects.get(pk=target_id)
            if target.network_id!=match.network_id or not target.is_active:raise ValidationError('目标网络或有效状态不一致。')
            if target_kind=='road' and not (target.is_canonical and target.match_eligible): raise ValidationError('目标必须是无来源歧义的实际机动车路段。')
            if target_kind=='junction' and target.is_intersection is not True:raise ValidationError('该节点尚未确认为路口。')
            if not owner.geom: raise ValidationError('对象没有坐标，不能核验距离。')
            distance=rows('SELECT ST_Distance(ST_GeomFromEWKT(%s)::geography,ST_GeomFromEWKT(%s)::geography) distance',[owner.geom,target.geom])[0]['distance']
            if distance>200:raise ValidationError('目标距离超过 200 米，请先核查对象坐标。')
            setattr(match,target_kind,target);match.status='matched';match.method='manual:confirmed';match.distance_m=round(distance,3)
            match.evidence={'reason':reason,'target_revision':target.revision,'distance_limit_m':200}
            match.reviewed_by=actor;match.reviewed_at=timezone.now()
            if kind=='site':
                match.source_id_match=owner.source_segment_id.lstrip('0')==target.normalized_segment_id
                match.direction_compatible=direction_matches({'bearing_deg':target.bearing_deg,'traffic_direction':target.traffic_direction},owner.direction)
                if bool(exposure_start)!=bool(exposure_end) or (exposure_start and exposure_end<exposure_start):raise ValidationError('历史覆盖的起止日期须成对填写且顺序正确。')
                if exposure_start:
                    if match.direction_compatible is not True:raise ValidationError('采样方向与路段不符，不能确认暴露量覆盖。')
                    match.evidence.update(exposure_start=exposure_start.isoformat(),exposure_end=exposure_end.isoformat())
        else:raise ValidationError('操作类型不合法。')
        match.revision+=1;match.full_clean();match.save()
        changes={k:(value,getattr(match,k)) for k,value in before.items() if value!=getattr(match,k)}
        changes['review_revision']=(revision,match.revision)
        audit_changes(actor,'spatial_reviewed',type(match).__name__,match.pk,changes,reason,revision=match.revision,snapshot_ids=[match.network.snapshot_id])
        return match
