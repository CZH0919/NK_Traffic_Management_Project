from apps.core.navigation import detail_url, return_context
from urllib.parse import urlencode
import json
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction,IntegrityError
from django.shortcuts import get_object_or_404,render,redirect
from django.http import Http404
from django.urls import reverse
from django.views.decorators.http import require_POST
from apps.core.permissions import require_capability
from apps.core.models import AuditEvent,FieldChange,CategoryMapping
from apps.core.services import ConcurrentChange
from apps.ingestion.models import QualityIssue
from .constants import MODELS,TITLES,LABELS,EDITABLE
from .models import Vehicle,Person,VehicleDamage
from .forms import EntityForm,StatusForm,SaveQueryForm
from .queries import QueryForm,filtered_entities
from .services import save_entity,change_status,get_factors

def valid_kind(kind):
    if kind not in MODELS: raise Http404
    return MODELS[kind]

def add_errors(form,exc):
    if hasattr(exc,'message_dict'):
        for name,errors in exc.message_dict.items(): form.add_error(name if name in form.fields else None,errors)
    else: form.add_error(None,exc)

def history(kind,pk):
    events=list(AuditEvent.objects.filter(object_type=kind,object_id=str(pk)).prefetch_related('fieldchange_set')[:30])
    for event in events:
        event.change_rows=[{'label':LABELS.get(c.field_name,c.field_name),'before':c.previous_value,'after':c.new_value,'reason':c.reason} for c in event.fieldchange_set.all()]
    return events

@require_capability('analyze_data')
def entity_list(request,kind):
    model=valid_kind(kind); form=QueryForm(request.GET,kind=kind)
    valid=form.is_valid(); qs=filtered_entities(kind,form) if valid else model.objects.none()
    if kind=='crashes': qs=qs.select_related('borough').defer('geom','review_note','borough__geom')
    elif kind=='vehicles': qs=qs.select_related('collision').defer('review_note','collision__geom','collision__review_note')
    else: qs=qs.select_related('collision','vehicle').defer('review_note','collision__geom','collision__review_note','vehicle__review_note')
    page=Paginator(qs,40).get_page(request.GET.get('page'))
    conditions=form.conditions() if valid else {}
    token=signing.dumps({'owner':request.user.pk,'kind':kind,'conditions':conditions},salt='saved-query-v1',compress=True)
    save_form=SaveQueryForm(initial={'conditions':token})
    category_field='vehicle_type' if kind=='vehicles' else 'person_type'
    mapping=dict(CategoryMapping.objects.filter(kind=category_field,is_active=True).values_list('raw_value','canonical_label')) if kind!='crashes' else {}
    for row in page:
        if kind!='crashes': row.category_label=mapping.get(getattr(row,category_field),'未归类')
    common_names=('date_from','date_to','borough','q','source_id','sort')
    advanced_names=[name for name in form.fields if name not in common_names]
    defaults={'origin':'official','active':'active'}
    advanced_open=any(conditions.get(name) not in (None,'',defaults.get(name)) or name in form.errors for name in advanced_names)
    return render(request,'collisions/list.html',{'kind':kind,'title':TITLES[kind]+'档案','form':form,'page_obj':page,
        'common_fields':[form[name] for name in common_names], 'advanced_fields':[form[name] for name in advanced_names],
        'advanced_open':advanced_open,
        'origin_label':dict(form.fields['origin'].choices).get(conditions.get('origin','official')),
        'active_label':dict(form.fields['active'].choices).get(conditions.get('active','active')),
        'querystring':urlencode(conditions),'save_form':save_form,'valid_query':valid},status=200 if valid else 400)


@require_capability('analyze_data')
def entity_detail(request,kind,pk):
    model=valid_kind(kind); obj=get_object_or_404(model.objects.select_related('source'),pk=pk)
    if obj.origin=='official' and obj.raw_record.snapshot.status!='published' and not request.user.has_perm('core.maintain_data'): raise Http404
    fields=[]
    for name in EDITABLE[kind]:
        value=getattr(obj,model._meta.get_field(name).attname)
        if name=='borough': value=obj.borough.chinese_name if obj.borough_id else None
        fields.append((LABELS[name],value))
    raw=obj.raw_record if obj.raw_record_id else None
    changes=history(kind,pk)
    context={'kind':kind,'object':obj,'title':TITLES[kind]+'详情','fields':fields,'factors':get_factors(kind,obj),
        'history':changes,'raw':raw,'raw_json':json.dumps(raw.payload,ensure_ascii=False,indent=2) if raw else '',
        'issues':QualityIssue.objects.filter(record=raw).select_related('resolved_by') if raw else [],
        'has_human_changes':any(e.actor_id and e.change_rows for e in changes)}
    if kind=='crashes':
        context['vehicles']=obj.vehicles.order_by('id')[:200]
        context['persons']=obj.persons.select_related('vehicle').order_by('id')[:300]
        context['vehicle_count']=obj.vehicles.count(); context['person_count']=obj.persons.count()
    elif kind=='vehicles':
        context['persons']=obj.persons.order_by('id')[:300]; context['person_count']=obj.persons.count()
        context['damage']=VehicleDamage.objects.filter(vehicle=obj).order_by('source_slot')
    return render(request,'collisions/detail.html',context)

@require_capability('maintain_data')
def entity_edit(request,kind,pk=None):
    model=valid_kind(kind); obj=get_object_or_404(model,pk=pk) if pk else None
    form=EntityForm(request.POST if request.method=='POST' else None,kind=kind,instance=obj)
    status=200
    if request.method=='POST':
        if form.is_valid():
            try:
                result,changed=save_entity(kind=kind,actor=request.user,data=form.mutation_data(),reason=form.cleaned_data['reason'],
                    pk=pk,expected_revision=form.cleaned_data.get('revision'))
                messages.success(request,'记录已保存，修改依据及前后变化已留存。' if changed else '内容没有变化，未产生新的修订。')
                return redirect(detail_url(request,'entity_detail',kind=kind,pk=result.pk))
            except ConcurrentChange as e: add_errors(form,e); status=409
            except ValidationError as e: add_errors(form,e); status=400
            except IntegrityError: form.add_error(None,'关联或唯一性校验未通过，所有修改已回滚。'); status=400
        else: status=400
    basic=[]; extra=[]
    for field in form:
        if field.name.startswith(('factor_','slot_type_','damage_')): extra.append(field)
        elif not field.is_hidden and field.name!='reason': basic.append(field)
    return render(request,'collisions/form.html',{'kind':kind,'object':obj,'form':form,'basic_fields':basic,'extra_fields':extra,'field_sections':group_fields(basic),
        'title':('修改' if obj else '新增')+TITLES[kind]},status=status)

@require_capability('maintain_data')
def entity_status(request,kind,pk):
    model=valid_kind(kind); obj=get_object_or_404(model,pk=pk)
    active=not obj.is_active
    form=StatusForm(request.POST if request.method=='POST' else None,initial={'revision':obj.revision})
    status=200
    if request.method=='POST':
        if form.is_valid():
            try:
                count=change_status(kind=kind,actor=request.user,versions={pk:form.cleaned_data['revision']},active=active,reason=form.cleaned_data['reason'])
                messages.success(request,f'已更新 {count} 条记录的有效状态，原始证据和关联均保留。')
                return redirect(detail_url(request,'entity_detail',kind=kind,pk=pk))
            except ConcurrentChange as e: add_errors(form,e); status=409
            except ValidationError as e: add_errors(form,e); status=400
        else: status=400
    return render(request,'collisions/status.html',{'kind':kind,'object':obj,'form':form,'active':active,
        'title':('恢复' if active else '失效')+TITLES[kind]},status=status)

@require_POST
@require_capability('maintain_data')
def entity_bulk(request,kind):
    valid_kind(kind)
    try:
        versions={}
        items=request.POST.getlist('selection')
        if not 1<=len(items)<=50: raise ValidationError('每次请选择 1—50 条记录。')
        for item in items:
            key,revision=map(int,item.split(':'))
            if key in versions: raise ValidationError('选择包含重复记录。')
            versions[key]=revision
        action=request.POST.get('action')
        if action not in ('deactivate','restore'): raise ValidationError('请选择有效的批量操作。')
        if request.POST.get('confirm')!='on': raise ValidationError('请确认批量维护的对象与影响。')
        count=change_status(kind=kind,actor=request.user,versions=versions,active=action=='restore',reason=request.POST.get('reason'))
        messages.success(request,f'批量维护完成，共更新 {count} 条记录。')
        return redirect(return_context(request)['return_to'])
    except (ValueError,ValidationError) as e:
        message='选择参数不合法。' if isinstance(e,ValueError) else '；'.join(e.messages)
        return render(request,'core/action_error.html',{'error':message,'back_url':reverse('entity_list',kwargs={'kind':kind})},status=409 if isinstance(e,ConcurrentChange) else 400)


def group_fields(fields):
    location={'borough','on_street','off_street','cross_street','latitude','longitude','postal_code','zip_code'}
    casualties={'persons_injured','persons_killed','pedestrians_injured','pedestrians_killed','cyclists_injured','cyclists_killed','motorists_injured','motorists_killed'}
    groups=[('基本信息',[]),('位置与道路',[]),('伤亡人数',[]),('来源与说明',[])]
    for field in fields:
        index=1 if field.name in location else 2 if field.name in casualties else 3 if field.name in ('origin','review_note') else 0
        groups[index][1].append(field)
    return [(title,items) for title,items in groups if items]
