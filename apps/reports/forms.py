from datetime import timedelta
from django import forms
from django.core.exceptions import ValidationError
from apps.network.forms import StyledForm
from apps.core.models import SavedQuery, Borough
from apps.analytics.models import RiskRun
from apps.forecasting.models import ForecastRun
from apps.traffic.models import SamplingSite
from apps.traffic.forms import FlowQuery
from apps.traffic.analysis import bounds
from .models import KINDS


class RunChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        label = obj.summary.get('object_label', obj.get_level_display() if hasattr(obj,'level') else '')
        return f'{label} · {obj.date_from} 至 {obj.date_to} · {str(obj.pk)[:8]}'


class SiteChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'#{obj.pk} · {obj.display_name or obj.street} · {obj.direction}'


class SavedQueryChoice(forms.ModelChoiceField):
    def label_from_instance(self,obj):
        return obj.name


class ReportForm(StyledForm):
    kind = forms.ChoiceField(label='报告类型', choices=KINDS)
    title = forms.CharField(label='报告标题', max_length=180, required=False)
    saved_query = SavedQueryChoice(label='载入个人查询',queryset=SavedQuery.objects.none(),required=False,empty_label='直接设置范围')
    risk_run = RunChoice(label='已完成风险计算',queryset=RiskRun.objects.filter(status='succeeded'),required=False,empty_label='请选择已完成计算')
    forecast_run = RunChoice(label='已完成预测计算',queryset=ForecastRun.objects.filter(status='succeeded'),required=False,empty_label='请选择已完成计算')
    site = SiteChoice(label='采样地点',queryset=SamplingSite.objects.filter(is_active=True).order_by('pk'),required=False,empty_label='请选择采样地点')

    def __init__(self,*args,actor,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['saved_query'].queryset=SavedQuery.objects.filter(owner=actor,is_active=True,kind__in=['statistics','comparison','risk','forecast'])
        values=self.data if self.is_bound else self.initial
        kind=values.get('kind','statistics')
        for name, required_kind in [('risk_run','risk'),('forecast_run','forecast'),('site','flow')]:
            self.fields[name].required=kind==required_kind
        self.fields['title'].help_text=f'留空自动命名为“{dict(KINDS).get(kind,"分析")}报告”。'

    def clean(self):
        d=super().clean()
        for kind,key in [('risk','risk_run'),('forecast','forecast_run'),('flow','site')]:
            if d.get('kind')==kind and not d.get(key) and key not in self.errors:
                self.add_error(key, '请选择采样地点。' if kind=='flow' else '请选择一项已完成的计算。')
        return d


def prepare_forms(actor, data, load_saved=False, preview=False):
    values=data.dict() if hasattr(data,'dict') else dict(data)
    values.setdefault('kind','statistics')
    if load_saved and values.get('saved_query'):
        saved=SavedQuery.objects.filter(pk=values['saved_query'],owner=actor,is_active=True,kind__in=['statistics','comparison','risk','forecast']).first() if str(values['saved_query']).isdigit() else None
        if not saved:raise ValidationError('个人查询不存在或不可访问。')
        values.update(saved.conditions,kind=saved.kind,title=values.get('title') or saved.name)
        if saved.kind in ('risk','forecast'):
            model=RiskRun if saved.kind=='risk' else ForecastRun
            run=model.objects.filter(conditions=saved.conditions,status='succeeded').first()
            if run:values[saved.kind+'_run']=str(run.pk)
    main=ReportForm(values,actor=actor)
    source=None
    if values['kind'] in ('statistics','comparison'):
        from apps.analytics.forms import make_form
        source=make_form(values['kind'],values)
    elif values['kind']=='flow':
        site=SamplingSite.objects.filter(pk=values.get('site'),is_active=True).first() if str(values.get('site','')).isdigit() else None
        limits=bounds(site) if site else {'first':None,'last':None}
        initial={'start':limits['first'],'end':min(limits['last'],limits['first']+timedelta(days=6)) if limits['first'] else None,'grain':'day'}
        source=FlowQuery({**initial,**values})
        if site:
            source.fields['compare_site'].picker_empty=f'使用当前地点：{site.display_name or site.street} · {site.direction}'
            source.fields['start'].help_text=f'来源观测范围：{limits["first"] or "未提供"} 至 {limits["last"] or "未提供"}；其中可能存在缺测。'
        elif preview: source=None
    if preview:
        main=ReportForm(actor=actor,initial=values)
        if source is not None:
            old=source
            source=type(source)(initial=dict(old.data))
            for key in source.fields:
                source.fields[key].help_text=old.fields[key].help_text
                if hasattr(old.fields[key],'picker_empty'):source.fields[key].picker_empty=old.fields[key].picker_empty
    return main,source


class ObservationExportForm(StyledForm):
    site=SiteChoice(label='采样地点',queryset=SamplingSite.objects.all().order_by('pk'),required=False,empty_label='全部采样地点')
    date_from=forms.DateField(label='起始日期',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    date_to=forms.DateField(label='截止日期',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    borough=forms.ModelChoiceField(label='行政区',queryset=Borough.objects.all(),required=False)
    raw_start_time=forms.TimeField(label='每日起始时刻（含）',required=False,widget=forms.TimeInput(attrs={'type':'time'}))
    raw_end_time=forms.TimeField(label='每日截止时刻（不含）',required=False,widget=forms.TimeInput(attrs={'type':'time'}))

    def clean(self):
        d=super().clean()
        if d.get('date_from') and d.get('date_to') and d['date_from']>d['date_to']:raise ValidationError('起始日期不能晚于截止日期。')
        if d.get('raw_start_time') and d.get('raw_end_time') and d['raw_start_time']>=d['raw_end_time']:raise ValidationError('时刻范围须顺序正确，跨午夜请分两次导出。')
        return d

    def conditions(self):
        return {k:str(v.pk) if hasattr(v,'pk') else v.isoformat() if hasattr(v,'isoformat') else str(v) for k,v in self.cleaned_data.items() if v is not None and v!=''}


class BriefEditForm(StyledForm):
    revision=forms.IntegerField(min_value=0,widget=forms.HiddenInput)
    text=forms.CharField(label='编辑后的简报',max_length=24000,widget=forms.Textarea(attrs={'rows':22}))
