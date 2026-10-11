from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from apps.collisions.forms import style_fields
from apps.ingestion.models import DataSource
from .models import Borough, CategoryMapping
from .permissions import require_capability
from .services import audit_changes, clean_reason, maintenance_lock


class MappingForm(forms.Form):
    kind=forms.ChoiceField(label='类别',choices=CategoryMapping._meta.get_field('kind').choices)
    raw_value=forms.CharField(label='来源原值',max_length=160)
    canonical_code=forms.SlugField(label='统一代码',max_length=50)
    canonical_label=forms.CharField(label='显示名称',max_length=120)
    is_active=forms.BooleanField(label='启用映射',required=False,initial=True)
    reason = forms.CharField(label='变更依据', min_length=5, max_length=1000, widget=forms.Textarea)
    expected_version = forms.IntegerField(min_value=0, initial=0, widget=forms.HiddenInput)
    class Meta:
        model = CategoryMapping
        fields = ['kind','raw_value','canonical_code','canonical_label','is_active']
        labels = {'kind':'类别','raw_value':'来源原值','canonical_code':'统一代码','canonical_label':'显示名称','is_active':'启用映射'}
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs); style_fields(self)


@require_capability('analyze_data')
def references(request):
    mappings = CategoryMapping.objects.all()
    q = request.GET.get('q','').strip()[:160]
    if q: mappings = mappings.filter(raw_value__icontains=q)
    page = Paginator(mappings,40).get_page(request.GET.get('page'))
    return render(request,'core/references.html',{'boroughs':Borough.objects.only('code','name','chinese_name'),
        'sources':DataSource.objects.all(), 'page_obj':page, 'q':q, 'querystring':'q='+__import__('urllib.parse',fromlist=['quote']).quote(q)})


@require_capability('maintain_data')
def mapping_edit(request, pk=None):
    existing = get_object_or_404(CategoryMapping,pk=pk) if pk else None
    initial = {f:getattr(existing,f) for f in MappingForm.Meta.fields} if existing else {}
    initial['expected_version'] = existing.version if existing else 0
    form = MappingForm(request.POST or None, initial=initial)
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic(), maintenance_lock():
                data=form.cleaned_data
                if existing and (data['kind']!=existing.kind or data['raw_value']!=existing.raw_value):
                    raise ValidationError('调整已登记的映射时，类别和来源原值必须保持一致。')
                previous=CategoryMapping.objects.filter(kind=data['kind'],raw_value=data['raw_value']).order_by('-version').first()
                if (previous.version if previous else 0)!=data['expected_version']:
                    raise ValidationError('映射版本已经变化，请从基础信息列表重新打开最新版本。')
                before={f:getattr(previous,f) for f in MappingForm.Meta.fields} if previous else None
                CategoryMapping.objects.filter(kind=data['kind'],raw_value=data['raw_value'],is_active=True).update(is_active=False)
                item=CategoryMapping.objects.create(**{f:data[f] for f in MappingForm.Meta.fields},version=(previous.version if previous else 0)+1)
                audit_changes(request.user,'reference_changed','category',item.pk,{'mapping':(before,{f:getattr(item,f) for f in MappingForm.Meta.fields})},clean_reason(data['reason']),revision=item.version)
            messages.success(request,'类别映射已保存为新版本，来源原值保持不变。'); return redirect('references')
        except ValidationError as exc: form.add_error(None,exc)
    return render(request,'core/maintenance_form.html',{'form':form,'title':'维护类别映射','back_url':'references',
        'explanation':'统一代码用于分类筛选；每次保存生成新版本。原始类别不会被替换。停用后可以通过新版本恢复。'})


@require_capability('maintain_data')
def reference_edit(request, kind, pk):
    models={'borough':(Borough,['chinese_name'],{'chinese_name':'行政区显示名称'}),
        'source':(DataSource,['name','publisher','homepage','description','license_note','is_active'],
                  {'name':'来源名称','publisher':'发布机构','homepage':'官方主页','description':'说明','license_note':'许可说明','is_active':'允许后续导入'})}
    if kind not in models:
        from django.http import Http404
        raise Http404
    model,fields,labels=models[kind]; item=get_object_or_404(model,pk=pk)
    Form=forms.modelform_factory(model,fields=fields,labels=labels)
    Form.base_fields['reason']=forms.CharField(label='变更依据',min_length=5,max_length=1000,widget=forms.Textarea)
    Form.base_fields['version']=forms.CharField(widget=forms.HiddenInput)
    values={f:getattr(item,f) for f in fields}
    token=signing.dumps({'kind':kind,'pk':item.pk,'values':values},salt='reference-edit')
    form=Form(request.POST or None,instance=item,initial={'version':token}); style_fields(form)
    if request.method=='POST' and form.is_valid():
        try:
            expected=signing.loads(form.cleaned_data['version'],salt='reference-edit',max_age=86400)
            with transaction.atomic(), maintenance_lock():
                current=model.objects.select_for_update().get(pk=pk)
                if expected!={'kind':kind,'pk':current.pk,'values':{f:getattr(current,f) for f in fields}}:
                    raise ValidationError('基础信息已经变化，请重新打开页面后保存。')
                changes={f:(getattr(current,f),form.cleaned_data[f]) for f in fields if getattr(current,f)!=form.cleaned_data[f]}
                for f in changes: setattr(current,f,form.cleaned_data[f])
                current.full_clean(); current.save(update_fields=fields)
                if changes: audit_changes(request.user,'reference_changed',kind,current.pk,changes,clean_reason(form.cleaned_data['reason']))
            messages.success(request,'基础信息已保存。'); return redirect('references')
        except (ValidationError,signing.BadSignature) as exc:
            form.add_error(None,exc if isinstance(exc,ValidationError) else '页面校验已过期，请重新打开。')
    return render(request,'core/maintenance_form.html',{'form':form,'title':'维护行政区名称' if kind=='borough' else '维护数据来源',
        'back_url':'references','explanation':'来源编号、行政区代码和原始空间边界受到保护。停用来源仅阻止后续导入，保留已发布数据及历史证据。'})
