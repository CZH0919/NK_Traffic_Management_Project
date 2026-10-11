from django import forms
from apps.core.models import Borough
from .models import SamplingSite
from apps.network.forms import StyledForm

class SiteFilter(StyledForm):
    q=forms.CharField(label='街道或来源路段编号',required=False,max_length=200)
    borough=forms.ModelChoiceField(label='行政区',queryset=Borough.objects.all(),required=False,empty_label='全部行政区')
    direction=forms.ChoiceField(label='方向',choices=[('','全部方向'),('NB','北向'),('SB','南向'),('EB','东向'),('WB','西向')],required=False)
    request=forms.CharField(label='来源采样请求编号',required=False,max_length=80)
    road=forms.IntegerField(label='关联道路',required=False,min_value=1)
    inactive=forms.BooleanField(label='同时显示失效地点',required=False)

class FlowQuery(StyledForm):
    start=forms.DateField(label='开始日期',widget=forms.DateInput(attrs={'type':'date'}))
    end=forms.DateField(label='结束日期',widget=forms.DateInput(attrs={'type':'date'}))
    grain=forms.ChoiceField(label='统计粒度',choices=[('day','每日'),('hour','每小时'),('quarter','每 15 分钟')])
    compare_site=forms.IntegerField(label='比较地点（默认本地点）',required=False,min_value=1)
    compare_start=forms.DateField(label='比较期间起日（留空不比较）',required=False,widget=forms.DateInput(attrs={'type':'date'}))

class ObservationTimeFilter(StyledForm):
    raw_start_time=forms.TimeField(label='明细开始时刻（含）',required=False,widget=forms.TimeInput(format='%H:%M',attrs={'type':'time','step':900}))
    raw_end_time=forms.TimeField(label='明细结束时刻（不含）',required=False,widget=forms.TimeInput(format='%H:%M',attrs={'type':'time','step':900}))
    def clean(self):
        values=super().clean();start=values.get('raw_start_time');end=values.get('raw_end_time')
        if start and end and end<=start:raise forms.ValidationError('明细结束时刻应晚于开始时刻；跨午夜请分两次查询。')
        for value in (start,end):
            if value and (value.minute%15 or value.second):raise forms.ValidationError('明细时刻应按 15 分钟边界选择。')
        return values
