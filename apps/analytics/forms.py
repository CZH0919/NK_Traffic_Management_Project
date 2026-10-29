import math
from datetime import date
from django import forms
from apps.core.models import Borough
from apps.network.forms import StyledForm
from apps.network.models import NetworkVersion,RoadSegment,Junction
from apps.network.queries import current_network
from .models import RuleVersion,LEVELS

DIMENSIONS=[('month','月份'),('year','年份'),('date','日期'),('weekday','星期'),('hour','小时'),('borough','行政区'),('severity','严重程度'),('vehicle_type','车辆类型'),('person_type','人员类型'),('injury','人员伤害'),('factor','事故因素')]
METRICS=[('crashes','事故数'),('injured','已报告受伤人数'),('killed','已报告死亡人数'),('records','类别记录数')]

def bbox_values(value):
    try:
        numbers=[float(x) for x in value.split(',')]
        if len(numbers)!=4 or not all(math.isfinite(x) for x in numbers):raise ValueError
        w,s,e,n=numbers
        if not(-180<=w<e<=180 and -85<=s<n<=85):raise ValueError
        return numbers
    except (ValueError,TypeError,AttributeError):raise forms.ValidationError('地图范围参数不合法。')

class ConditionForm(StyledForm):
    date_from=forms.DateField(label='起始日期',widget=forms.DateInput(attrs={'type':'date'}))
    date_to=forms.DateField(label='截止日期',widget=forms.DateInput(attrs={'type':'date'}))
    borough=forms.ModelChoiceField(label='行政区',queryset=Borough.objects.all(),required=False,empty_label='全市（含归属未知）')
    network=forms.ModelChoiceField(label='空间网络',queryset=NetworkVersion.objects.filter(snapshot__status='published'),required=False,empty_label='当前已发布网络')
    def clean(self):
        d=super().clean();a=d.get('date_from');b=d.get('date_to')
        if a and b and (b<a or (b-a).days>3660 or a.year<2 or b.year>9998):raise forms.ValidationError('请选择顺序正确、最多十年的日期范围。')
        d['network']=d.get('network') or current_network()
        return d
    def conditions(self):
        result={}
        for k,v in self.cleaned_data.items():
            if v is None or v=='':continue
            result[k]=str(v.pk) if hasattr(v,'pk') else v.isoformat() if hasattr(v,'isoformat') else str(v)
        return result

class AnalysisForm(ConditionForm):
    metric_dimensions_enabled = True
    road=forms.IntegerField(label='道路',min_value=1,required=False)
    junction=forms.IntegerField(label='路口',min_value=1,required=False)
    year=forms.IntegerField(label='限定年份',min_value=2,max_value=9998,required=False)
    month=forms.ChoiceField(label='限定月份',choices=[('','全部月份')]+[(str(n),f'{n} 月') for n in range(1,13)],required=False)
    weekday=forms.ChoiceField(label='星期',choices=[('','全部星期')]+[(str(i),x) for i,x in enumerate(['周一','周二','周三','周四','周五','周六','周日'])],required=False)
    hour=forms.ChoiceField(label='当地小时',choices=[('','全部小时'),('-1','时间缺失')]+[(str(n),f'{n:02d}:00—{n:02d}:59') for n in range(24)],required=False)
    severity=forms.ChoiceField(label='严重程度',choices=[('','全部事故'),('fatal','有死亡'),('injured','无死亡且有受伤'),('none','报告伤亡均为零'),('unknown','伤亡程度不确定')],required=False)
    dimension=forms.ChoiceField(label='统计维度',choices=DIMENSIONS,required=False)
    metric=forms.ChoiceField(label='事故维度指标',choices=METRICS,required=False)
    bbox=forms.CharField(required=False,max_length=160,widget=forms.HiddenInput)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        values = self.data if self.is_bound else self.initial
        dimension = values.get('dimension') or 'month'
        requested = values.get('metric') or 'crashes'
        fixed = {'vehicle_type':('records','涉事车辆记录数'), 'person_type':('records','涉事人员记录数'),
                 'injury':('records','涉事人员记录数'), 'factor':('crashes','涉及事故数')}.get(dimension) if self.metric_dimensions_enabled else None
        effective = fixed[0] if fixed else 'crashes' if requested == 'records' else requested
        self.metric_adjusted = bool(values.get('metric') and requested != effective)
        self.fields['metric'].label = '统计指标'
        self.fields['metric'].choices = [fixed] if fixed else METRICS[:3]
        self.fields['metric'].fixed_label = fixed[1] if fixed else ''
        if requested in dict(METRICS):
            if self.is_bound:
                self.data = self.data.copy()
                self.data['metric'] = effective
            else: self.initial['metric'] = effective
        if fixed: self.fields['metric'].help_text = '此维度按明细条数统计。' if fixed[0] == 'records' else '每起事故按所涉及因素计数。'
    def clean(self):
        d=super().clean()
        if d.get('road') and d.get('junction'):raise forms.ValidationError('道路和路口应分别查询。')
        for key,model,fields in [('road',RoadSegment,{'is_canonical':True,'match_eligible':True}),('junction',Junction,{'is_intersection':True})]:
            if d.get(key) and not model.objects.filter(pk=d[key],network=d.get('network'),is_active=True,**fields).exists():self.add_error(key,'对象不属于选定网络的当前有效范围。')
        if d.get('bbox'):
            try:d['bbox']=','.join(f'{v:.6f}' for v in bbox_values(d['bbox']))
            except forms.ValidationError as e:self.add_error('bbox',e)
        d['dimension']=d.get('dimension') or 'month';d['metric']=d.get('metric') or 'crashes'
        if d['dimension'] in ('vehicle_type','person_type','injury'):d['metric']='records'
        elif d['dimension']=='factor':d['metric']='crashes'
        elif d['metric']=='records':d['metric']='crashes'
        return d

class CompareForm(AnalysisForm):
    metric_dimensions_enabled = False
    compare_from=forms.DateField(label='B 期间起始',widget=forms.DateInput(attrs={'type':'date'}))
    compare_to=forms.DateField(label='B 期间截止',widget=forms.DateInput(attrs={'type':'date'}))
    compare_borough=forms.ModelChoiceField(label='B 行政区',queryset=Borough.objects.all(),required=False,empty_label='全市（含归属未知）')
    def clean(self):
        d=super().clean();a=d.get('compare_from');b=d.get('compare_to')
        if a and b and (b<a or (b-a).days>3660 or a.year<2 or b.year>9998):raise forms.ValidationError('B 期间须顺序正确且不超过十年。')
        if (d.get('road') or d.get('junction')) and d.get('borough')!=d.get('compare_borough'):raise forms.ValidationError('指定道路／路口时，跨期比较应使用同一行政区条件。')
        d['dimension']='date'
        if d.get('metric')=='records':d['metric']='crashes'
        return d

class RiskForm(ConditionForm):
    level=forms.ChoiceField(label='评价对象',choices=LEVELS)
    rule=forms.ModelChoiceField(label='规则版本',queryset=RuleVersion.objects.filter(code='historical-intensity'))
    def clean(self):
        d=super().clean()
        if d.get('level')=='city' and d.get('borough'):raise forms.ValidationError('全市评价请使用全市范围；单区请切换行政区评价。')
        if d.get('level') in ('road','junction') and not d.get('network'):raise forms.ValidationError('道路／路口评价需要已发布路网。')
        return d

class RuleForm(StyledForm):
    name=forms.CharField(label='规则名称',max_length=120,initial='历史事故强度')
    frequency=forms.IntegerField(label='频次权重（%）',min_value=0,max_value=100,initial=50)
    severity=forms.IntegerField(label='已报告伤亡负担权重（%）',min_value=0,max_value=100,initial=35)
    recent=forms.IntegerField(label='近期强度权重（%）',min_value=0,max_value=100,initial=15)
    death_weight=forms.IntegerField(label='死亡人数相对系数',min_value=1,max_value=100,initial=5)
    recent_days=forms.IntegerField(label='近期窗口（天）',min_value=7,max_value=365,initial=90)
    min_support=forms.IntegerField(label='最少支撑事故数',min_value=5,max_value=100,initial=5)
    completeness=forms.IntegerField(label='最低伤亡字段完整率（%）',min_value=95,max_value=100,initial=95)
    reason=forms.CharField(label='规则依据与适用限制',min_length=10,max_length=1000,widget=forms.Textarea(attrs={'rows':4}))
    def clean(self):
        d=super().clean()
        if all(x in d for x in ('frequency','severity','recent')) and sum(d[x] for x in ('frequency','severity','recent'))!=100:raise forms.ValidationError('三项权重之和必须为 100%。')
        return d

def make_form(kind,data):
    from .query import coverage_intervals
    intervals=coverage_intervals('crashes')
    end=max((b for a,b in intervals),default=date.today());first=min((a for a,b in intervals),default=end)
    defaults={'date_from':max(first,date(end.year,1,1)).isoformat(),'date_to':end.isoformat(),'dimension':'month','metric':'crashes','level':'road'}
    if kind=='comparison':
        prior=max(2,end.year-1);defaults.update(compare_from=date(prior,1,1).isoformat(),compare_to=date(prior,12,31).isoformat())
    if kind=='risk':
        rule=RuleVersion.objects.filter(code='historical-intensity').first()
        if rule:defaults['rule']=str(rule.pk)
    values={**defaults,**dict(data.dict() if hasattr(data,'dict') else data)}
    return {'statistics':AnalysisForm,'comparison':CompareForm,'risk':RiskForm}[kind](values)
