from apps.core.navigation import detail_url, return_context
import json
from urllib.parse import urlencode
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from apps.collisions.forms import style_fields
from apps.collisions.views import history
from apps.core.permissions import require_capability
from apps.core.services import audit_changes, clean_reason, maintenance_lock, require_actor, ConcurrentChange
from .models import QualityIssue, DataSource, StagedRecord
from .pipeline import ENTITY

QUALITY_LABELS={
    'missing_borough':'来源缺少行政区','missing_or_outside_nyc_coordinate':'坐标缺失或不可用',
    'missing_casualty_count':'伤亡计数缺失','casualty_subtotal_mismatch':'伤亡分项与合计不一致',
    'invalid_date':'日期不合法','missing_or_invalid_time':'时间缺失或不合法',
    'dst_ambiguous':'夏令时重复小时','dst_nonexistent':'夏令时不存在的小时',
    'invalid_number':'数值不合法','invalid_text':'文字字段不合法','invalid_geometry':'空间几何不合法',
    'orphan_collision':'事故关联缺失','orphan_vehicle':'车辆关联缺失','vehicle_collision_mismatch':'人员与车辆不属于同一事故',
    'parent_time_mismatch':'与所属事故时间不一致','implausible_vehicle_year':'车辆年份不合理',
    'missing_network_endpoint':'道路端点缺失','invalid_sampling_time':'采样时间不合法',
    'non_quarter_hour_sampling':'非整刻钟采样','missing_volume':'流量值缺失或不合法',
    'invalid_site_key':'采样位置标识不合法','official_location_notice':'来源已公告位置问题',
    'missing_source_key':'来源主键缺失','duplicate_source_key':'重复来源主键',
    'source_record_conflict':'同一来源编号的内容冲突','changed_source_record':'同一来源编号的内容变化','changed_source_key':'同一来源编号的内容变化',
    'flow_not_statistically_usable':'流量观测受限','reported_borough_geometry_mismatch':'报告行政区与坐标不一致',
}

class QualityFilter(forms.Form):
    status=forms.ChoiceField(label='处理状态',required=False,choices=[('','全部')]+QualityIssue._meta.get_field('status').choices)
    source=forms.ModelChoiceField(label='数据来源',queryset=DataSource.objects.all(),required=False,empty_label='全部来源')
    code=forms.CharField(label='问题类型',required=False,max_length=60,widget=forms.Select(choices=[('','全部类型')]+list(QUALITY_LABELS.items())))
    severity=forms.ChoiceField(label='严重程度',required=False,choices=[('','全部')]+QualityIssue._meta.get_field('severity').choices)
    record_id=forms.IntegerField(label='原始记录编号',required=False,min_value=1)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        code=self.data.get('code','')
        if code and code not in QUALITY_LABELS:
            self.fields['code'].widget.choices=list(self.fields['code'].widget.choices)+[(code,'其他问题（'+code[:60]+'）')]
        style_fields(self)

class ReviewForm(forms.Form):
    status=forms.ChoiceField(label='核查结论',choices=[('accepted','保留限制'),('resolved','已完成核查'),('open','重新打开')])
    revision=forms.IntegerField(widget=forms.HiddenInput,min_value=1)
    reason=forms.CharField(label='核查依据与处理说明',min_length=5,max_length=1000,widget=forms.Textarea)
    confirm=forms.BooleanField(label='确认此操作只更新核查结论，不解除统计限制')
    def __init__(self,*args,**kwargs): super().__init__(*args,**kwargs); style_fields(self)

@transaction.atomic
def review_issues(actor,versions,status,reason):
    require_actor(actor,'maintain_data'); reason=clean_reason(reason)
    if status not in ('accepted','resolved','open') or not 1<=len(versions)<=50: raise ValidationError('处理参数不合法；每次最多 50 项。')
    with maintenance_lock():
        issues=list(QualityIssue.objects.select_for_update().filter(pk__in=versions).order_by('pk'))
        if len(issues)!=len(versions) or any(i.revision!=versions[i.pk] for i in issues): raise ConcurrentChange('问题状态已变化，请刷新后重试；本次没有修改任何问题。')
        for item in issues:
            changes={'status':(item.status,status),'resolution':(item.resolution,reason),
                'resolved_by':(item.resolved_by_id,None if status=='open' else actor.pk)}
            item.status=status; item.resolution=reason; item.revision+=1
            item.resolved_by=actor if status!='open' else None; item.resolved_at=timezone.now() if status!='open' else None
            item.full_clean(exclude=['details']); item.save()
            audit_changes(actor,'quality_reviewed','quality',item.pk,changes,reason,revision=item.revision,snapshot_ids=[item.snapshot_id])
    return len(issues)

@require_capability('analyze_data')
def quality_list(request):
    form=QualityFilter(request.GET); valid=form.is_valid(); qs=QualityIssue.objects.select_related('snapshot__source')
    if valid:
        data=form.cleaned_data
        for key in ('status','code','severity','record_id'):
            if data.get(key): qs=qs.filter(**{key:data[key]})
        if data.get('source'): qs=qs.filter(snapshot__source=data['source'])
    else: qs=qs.none()
    page=Paginator(qs.order_by('-id'),40).get_page(request.GET.get('page'))
    for row in page: row.label=QUALITY_LABELS.get(row.code,row.code)
    params=request.GET.copy(); params.pop('page',None)
    totals=dict(QualityIssue.objects.values_list('status').annotate(n=Count('id')))
    return render(request,'ingestion/quality_list.html',{'form':form,'page_obj':page,'querystring':params.urlencode(),
        'totals':totals,'codes':QUALITY_LABELS.items()},status=200 if valid else 400)

@require_capability('analyze_data')
def quality_detail(request,pk):
    issue=get_object_or_404(QualityIssue.objects.select_related('snapshot__source','record','resolved_by'),pk=pk)
    form=ReviewForm(initial={'revision':issue.revision}) if request.user.has_perm('core.maintain_data') else None
    kind=issue.snapshot.source.kind; model=ENTITY.get(kind); obj=None
    if model and kind in ('crashes','vehicles','persons','traffic') and issue.record_id:
        obj=model.objects.filter(raw_record_id=issue.record_id).first()
        if obj is None: obj=model.objects.filter(source=issue.snapshot.source,source_record_id=issue.record.source_key).first()
    members=[]
    if kind=='traffic' and obj and obj.conflict_id:
        members=obj.conflict.trafficobservation_set.select_related('raw_record__snapshot').order_by('id')
    return render(request,'ingestion/quality_detail.html',{'issue':issue,'label':QUALITY_LABELS.get(issue.code,issue.code),
        'form':form,'entity':obj,'kind':kind,'members':members,'history':history('quality',pk),
        'raw_json':json.dumps(issue.record.payload,ensure_ascii=False,indent=2) if issue.record else '',
        'details_json':json.dumps(issue.details,ensure_ascii=False,indent=2)})

@require_POST
@require_capability('maintain_data')
def quality_review(request,pk):
    form=ReviewForm(request.POST)
    try:
        if not form.is_valid(): raise ValidationError('请完整填写核查结论、依据和确认项。')
        review_issues(request.user,{pk:form.cleaned_data['revision']},form.cleaned_data['status'],form.cleaned_data['reason'])
        messages.success(request,'核查结论已记录，原始数据和统计限制保持独立管理。'); return redirect(detail_url(request,'quality_detail',pk=pk))
    except ValidationError as exc:
        from django.urls import reverse
        return render(request,'core/action_error.html',{'error':'；'.join(exc.messages),'back_url':reverse('quality_detail',args=[pk])},status=409 if isinstance(exc,ConcurrentChange) else 400)

@require_POST
@require_capability('maintain_data')
def quality_bulk(request):
    try:
        items=request.POST.getlist('selection'); versions={}
        if not 1<=len(items)<=50: raise ValidationError('每次请选择 1—50 个问题。')
        for value in items:
            key,version=map(int,value.split(':'))
            if key in versions: raise ValidationError('选择包含重复问题。')
            versions[key]=version
        if request.POST.get('confirm')!='on': raise ValidationError('请确认批量核查范围。')
        count=review_issues(request.user,versions,request.POST.get('status'),request.POST.get('reason'))
        messages.success(request,f'已记录 {count} 个问题的核查结论。'); return redirect(return_context(request)['return_to'])
    except (ValueError,ValidationError) as exc:
        from django.urls import reverse
        return render(request,'core/action_error.html',{'error':'选择格式不合法。' if isinstance(exc,ValueError) else '；'.join(exc.messages),
            'back_url':reverse('quality_list')},status=409 if isinstance(exc,ConcurrentChange) else 400)
