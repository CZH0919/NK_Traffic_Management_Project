from django.core.exceptions import ValidationError
from django.db import transaction,connection
from django.db.models import Q
from apps.core.services import require_actor,clean_reason,maintenance_lock,audit_changes,ConcurrentChange
from apps.core.time_rules import resolve_wall_time
from apps.core.models import Borough
from .constants import MODELS,EDITABLE
from .models import Collision,Vehicle,Person,Factor,CollisionFactor,VehicleFactor,PersonFactor,VehicleDamage

def get_factors(kind,obj):
    cls,fk={'crashes':(CollisionFactor,'collision'),'vehicles':(VehicleFactor,'vehicle'),'persons':(PersonFactor,'person')}[kind]
    return [{'slot':r.source_slot,'name':r.factor.name if r.factor else '','vehicle_type':getattr(r,'vehicle_type','')}
        for r in cls.objects.filter(**{fk:obj}).select_related('factor').order_by('source_slot')]

def normal_factors(kind,values):
    rows=[]; seen=set(); maximum=5 if kind=='crashes' else 2
    for item in values:
        slot=int(item['slot']); name=str(item.get('name') or '').strip(); vtype=str(item.get('vehicle_type') or '').strip() if kind=='crashes' else ''
        if slot in seen or not 1<=slot<=maximum or len(name)>160 or len(vtype)>100: raise ValidationError('因素槽位或内容不合法。')
        seen.add(slot)
        if name or vtype: rows.append({'slot':slot,'name':name,'vehicle_type':vtype})
    return sorted(rows,key=lambda r:r['slot'])

def save_factors(kind,obj,rows,old):
    cls,fk={'crashes':(CollisionFactor,'collision'),'vehicles':(VehicleFactor,'vehicle'),'persons':(PersonFactor,'person')}[kind]
    names={r['name'] for r in old}
    cls.objects.filter(**{fk:obj}).delete()
    for item in rows:
        factor=None
        if item['name']:
            factor,_=Factor.objects.get_or_create(name=item['name'])
            if not factor.is_active and factor.name not in names: raise ValidationError('不能新增已停用因素的关联。')
        values={fk:obj,'source_slot':item['slot'],'factor':factor}
        if kind=='crashes': values['vehicle_type']=item['vehicle_type']
        cls.objects.create(**values)

def snapshot_ids(obj):
    if not obj.raw_record_id: return []
    from apps.ingestion.models import StagedRecord
    return [StagedRecord.objects.only('snapshot_id').get(pk=obj.raw_record_id).snapshot_id]

@transaction.atomic
def save_entity(*,kind,actor,data,reason,pk=None,expected_revision=None):
    require_actor(actor,'maintain_data'); reason=clean_reason(reason)
    if kind not in MODELS: raise ValidationError('不支持的记录类型。')
    data=dict(data); allowed=set(EDITABLE[kind])|{'factors','damage'}|({'geom'} if kind=='crashes' else set())|({'origin'} if pk is None else set())
    if set(data)-allowed: raise ValidationError('包含受保护或未知字段。')
    with maintenance_lock():
        model=MODELS[kind]; creating=pk is None
        obj=model() if creating else model.objects.select_for_update().get(pk=pk)
        if not creating and obj.revision!=expected_revision: raise ConcurrentChange('记录已被其他操作更新，请重新打开后核对。')
        if not creating and not obj.is_active: raise ValidationError('请先恢复有效状态，再修改业务字段。')
        if creating:
            origin=data.pop('origin','demo')
            if origin not in ('manual','demo'): raise ValidationError('人工新增不能冒充官方来源。')
            obj.origin=origin
        factors=data.pop('factors',None); damage=data.pop('damage',None)
        changes={}
        old_collision_id=getattr(obj,'collision_id',None)
        for name,value in data.items():
            field=model._meta.get_field(name)
            if field.is_relation: value=getattr(value,'pk',value)
            before=getattr(obj,field.attname)
            if before!=value: changes[name]=(before,value); setattr(obj,field.attname,value)
        if len(obj.review_note)>1000: raise ValidationError('核查备注不能超过 1000 字。')
        if kind=='crashes':
            instant,status=resolve_wall_time(obj.local_date,obj.local_time)
            for name,value in [('occurred_at',instant),('time_status',status)]:
                if getattr(obj,name)!=value: changes[name]=(getattr(obj,name),value); setattr(obj,name,value)
            if creating or 'borough' in changes:
                method='manual' if obj.borough_id else 'unknown'
                if obj.borough_method!=method: changes['borough_method']=(obj.borough_method,method); obj.borough_method=method
            if obj.geom and ('geom' in changes or 'borough' in changes or creating):
                with connection.cursor() as c:
                    c.execute('SELECT code FROM core_borough WHERE ST_Covers(geom,ST_GeomFromEWKT(%s))',[obj.geom]); codes=[r[0] for r in c.fetchall()]
                if obj.borough_id and codes and obj.borough_id not in codes: raise ValidationError('坐标与所选行政区不一致，请核实后再保存。')
                if not obj.borough_id and len(codes)==1:
                    changes['borough']=(None,codes[0]); obj.borough_id=codes[0]
                    changes['borough_method']=(obj.borough_method,'spatial'); obj.borough_method='spatial'
        else:
            if obj.collision_id:
                try: parent=Collision.objects.get(pk=obj.collision_id)
                except Collision.DoesNotExist: raise ValidationError('所属事故不存在。')
                if not parent.is_active: raise ValidationError('所属事故已失效，请先核查其状态。')
            if kind=='vehicles' and not creating and old_collision_id!=obj.collision_id and Person.objects.filter(vehicle=obj).exists():
                raise ValidationError('车辆已有人员关联；请先核查并调整人员关系，再更改所属事故。')
            if kind=='persons' and obj.vehicle_id:
                try: vehicle=Vehicle.objects.get(pk=obj.vehicle_id)
                except Vehicle.DoesNotExist: raise ValidationError('所属车辆不存在。')
                if vehicle.collision_id!=obj.collision_id or not obj.collision_id: raise ValidationError('人员与车辆必须属于同起事故。')
                if not vehicle.is_active: raise ValidationError('所属车辆已失效，请先核查其状态。')
        obj.full_clean()
        previous_factors=get_factors(kind,obj) if not creating else []
        if factors is not None:
            factors=normal_factors(kind,factors)
            if factors!=previous_factors: changes['factors']=(previous_factors,factors)
        if damage is not None:
            if kind!='vehicles': raise ValidationError('记录类型不支持损伤槽位。')
            previous_damage=list(VehicleDamage.objects.filter(vehicle=obj).order_by('source_slot').values('source_slot','description')) if not creating else []
            converted=[]; slots=set()
            for item in damage:
                slot=int(item['slot']); description=str(item.get('description') or '').strip()
                if slot in slots or not 0<=slot<=3 or len(description)>160: raise ValidationError('损伤槽位不合法。')
                slots.add(slot)
                if description: converted.append({'source_slot':slot,'description':description})
            converted.sort(key=lambda r:r['source_slot'])
            if converted!=previous_damage: changes['damage']=(previous_damage,converted)
        if not changes and not creating: return obj,False
        if creating: changes['origin']=(None,obj.origin)
        else: obj.revision+=1
        obj.save()
        if 'factors' in changes: save_factors(kind,obj,factors,previous_factors)
        if 'damage' in changes:
            VehicleDamage.objects.filter(vehicle=obj).delete()
            VehicleDamage.objects.bulk_create([VehicleDamage(vehicle=obj,**item) for item in converted])
        audit_changes(actor=actor,action='data_created' if creating else 'data_changed',kind=kind,object_id=obj.pk,
            changes=changes,reason=reason,revision=obj.revision,snapshot_ids=snapshot_ids(obj))
        return obj,True

@transaction.atomic
def change_status(*,kind,actor,versions,active,reason):
    require_actor(actor,'maintain_data'); reason=clean_reason(reason)
    if kind not in MODELS or not 1<=len(versions)<=50: raise ValidationError('每次请选择 1—50 条记录。')
    with maintenance_lock():
        objects=list(MODELS[kind].objects.select_for_update().filter(pk__in=versions).order_by('pk'))
        if len(objects)!=len(versions): raise ValidationError('部分记录不存在，未进行任何变更。')
        if any(obj.revision!=versions[obj.pk] for obj in objects): raise ConcurrentChange('选中记录已变化，请重新检索后操作。')
        changeset=[(kind,o) for o in objects]
        if not active:
            if kind=='crashes':
                changeset += [('vehicles',o) for o in Vehicle.objects.select_for_update().filter(collision_id__in=versions,is_active=True).order_by('pk')]
                changeset += [('persons',o) for o in Person.objects.select_for_update().filter(collision_id__in=versions,is_active=True).order_by('pk')]
            elif kind=='vehicles':
                changeset += [('persons',o) for o in Person.objects.select_for_update().filter(vehicle_id__in=versions,is_active=True).order_by('pk')]
        if len(changeset)>5000: raise ValidationError('关联记录超过单次维护上限，请减少选中对象。')
        count=0
        for object_kind,obj in changeset:
            if obj.is_active==active: continue
            if active and object_kind!='crashes':
                if obj.collision_id and not Collision.objects.filter(pk=obj.collision_id,is_active=True).exists(): raise ValidationError('请先恢复所属事故。')
                if object_kind=='persons' and obj.vehicle_id and not Vehicle.objects.filter(pk=obj.vehicle_id,is_active=True).exists(): raise ValidationError('请先恢复所属车辆。')
            before=obj.is_active; obj.is_active=active; obj.revision+=1; obj.save(update_fields=['is_active','revision','updated_at'])
            audit_changes(actor=actor,action='data_status_changed',kind=object_kind,object_id=obj.pk,
                changes={'is_active':(before,active)},reason=reason,revision=obj.revision,snapshot_ids=snapshot_ids(obj))
            count+=1
        return count
