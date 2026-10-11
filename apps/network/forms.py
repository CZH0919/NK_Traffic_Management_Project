from django import forms
from apps.core.models import Borough
from .models import NetworkVersion

class StyledForm(forms.Form):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class','form-check-input' if isinstance(field.widget,forms.CheckboxInput) else 'form-select' if isinstance(field.widget,forms.Select) else 'form-control')
            if isinstance(field.widget,forms.DateInput):field.widget.format='%Y-%m-%d'

class CatalogFilter(StyledForm):
    q=forms.CharField(label='名称或来源编号',required=False,max_length=200)
    borough=forms.ModelChoiceField(label='行政区',queryset=Borough.objects.all(),required=False,empty_label='全部行政区')
    network=forms.ModelChoiceField(label='路网版本',queryset=NetworkVersion.objects.filter(snapshot__status='published'),required=False,empty_label='当前版本')
    state=forms.ChoiceField(label='对象范围',choices=[('eligible','可用道路／已识别路口'),('all','全部来源要素'),('inactive','已失效对象')],required=False)

class MaintenanceForm(StyledForm):
    revision=forms.IntegerField(widget=forms.HiddenInput,min_value=1)
    display_name=forms.CharField(label='展示名称',required=False,max_length=200)
    review_note=forms.CharField(label='维护说明',required=False,widget=forms.Textarea(attrs={'rows':3}),max_length=3000)
    is_active=forms.BooleanField(label='作为有效对象使用',required=False)
    is_intersection=forms.TypedChoiceField(label='地面路口分类',choices=[('','尚不确定'),('true','确认为路口'),('false','不是路口')],coerce=lambda x: {'true':True,'false':False,'':None}[x],empty_value=None,required=False)
    reason=forms.CharField(label='修改依据',min_length=5,max_length=1000,widget=forms.Textarea(attrs={'rows':2}))
    def __init__(self,*args,kind=None,**kwargs):
        super().__init__(*args,**kwargs)
        if kind!='junction':self.fields.pop('is_intersection')

class MatchForm(StyledForm):
    revision=forms.IntegerField(widget=forms.HiddenInput,min_value=1)
    input_revision=forms.IntegerField(widget=forms.HiddenInput,min_value=1)
    action=forms.ChoiceField(label='操作',choices=[('confirm','确认或校正到指定对象'),('unmatched','确认目前无法关联'),('recalculate','根据当前数据重新匹配')])
    target_kind=forms.ChoiceField(label='目标类型',choices=[('road','道路'),('junction','路口')])
    target_id=forms.IntegerField(label='目标内部编号',min_value=1,required=False)
    exposure_start=forms.DateField(label='已核实历史覆盖起日（仅采样地点）',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    exposure_end=forms.DateField(label='已核实历史覆盖止日（仅采样地点）',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    reason=forms.CharField(label='核查依据',min_length=5,max_length=1000,widget=forms.Textarea(attrs={'rows':3}))

class SpatialFilter(StyledForm):
    kind=forms.ChoiceField(label='对象类型',choices=[('collision','事故'),('site','采样地点')],required=False)
    network=forms.ModelChoiceField(label='目标网络',queryset=NetworkVersion.objects.filter(snapshot__status='published'),required=False,empty_label='当前版本')
    status=forms.ChoiceField(label='关联状态',choices=[('','全部状态'),('matched','已关联'),('ambiguous','待核查候选'),('unmatched','未匹配'),('missing','缺少位置'),('stale','输入已修订，待更新')],required=False)
    borough=forms.ModelChoiceField(label='行政区',queryset=Borough.objects.all(),required=False,empty_label='全部行政区')
    q=forms.CharField(label='来源编号或街道',max_length=200,required=False)
