from datetime import date
from django import forms
from apps.analytics.forms import ConditionForm
from apps.analytics.models import RuleVersion, LEVELS
from apps.analytics.query import coverage_intervals
from apps.network.models import RoadSegment, Junction

METHODS = [('auto', '自动选择（根据较早历史）'), ('naive', '上一期值基线'), ('seasonal', '季节滞后基线'), ('ma', '移动平均'), ('ses', '简单指数平滑')]
METRICS = [('crashes', '事故数'), ('injured', '已报告受伤人数'), ('killed', '已报告死亡人数')]


class ForecastForm(ConditionForm):
    date_to = forms.DateField(label='历史截止日期', widget=forms.DateInput(attrs={'type': 'date'}))
    level = forms.ChoiceField(label='分析对象', choices=LEVELS)
    road = forms.IntegerField(label='道路', min_value=1, required=False)
    junction = forms.IntegerField(label='路口', min_value=1, required=False)
    frequency = forms.ChoiceField(label='统计周期', choices=[('month', '自然月'), ('week', '周一至周日')])
    metric = forms.ChoiceField(label='预测指标', choices=METRICS)
    horizon = forms.IntegerField(label='外推周期数', min_value=1, max_value=26)
    method = forms.ChoiceField(label='预测方法', choices=METHODS)
    rule = forms.ModelChoiceField(label='方法规则版本', queryset=RuleVersion.objects.filter(code='count-forecast'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        values = self.data if self.is_bound else self.initial
        self.fields['borough'].empty_label = '请选择行政区'
        monthly = values.get('frequency', 'month') == 'month'
        self.fields['horizon'].widget.attrs['max'] = 12 if monthly else 26
        self.fields['horizon'].help_text = '月度最多 12 期，周度最多 26 期；提前一期分别表示一个月或一周。'
        level = values.get('level', 'city')
        for name in ('borough', 'road', 'junction'):
            self.fields[name].required = level == name

    def clean(self):
        d = super().clean()
        if d.get('date_to') and d['date_to'].year > 9997:
            self.add_error('date_to', '截止日期超出可支持的日历范围。')
        if d.get('frequency') == 'month' and d.get('horizon', 0) > 12:
            self.add_error('horizon', '月度最多外推 12 个周期。')
        level = d.get('level')
        if level == 'city' and d.get('borough'):
            self.add_error('borough', '全市分析不限定行政区，请选择行政区分析。')
        if level == 'borough' and not d.get('borough'):
            self.add_error('borough', '请选择行政区。')
        for key, model, eligibility in [('road', RoadSegment, {'is_canonical': True, 'match_eligible': True}), ('junction', Junction, {'is_intersection': True})]:
            value = d.get(key)
            if level == key:
                if not value or not model.objects.filter(pk=value, network=d.get('network'), is_active=True, **eligibility).exists():
                    self.add_error(key, '请选择该网络内有效的道路／路口内部编号。')
            elif value:
                self.add_error(key, '该编号与当前分析对象类型不一致。')
        return d


def make_form(data):
    intervals = coverage_intervals('crashes')
    last = max((b for a, b in intervals), default=date.today())
    first = min((a for a, b in intervals), default=last)
    values = {'date_from': first.isoformat(), 'date_to': last.isoformat(), 'level': 'city', 'frequency': 'month', 'metric': 'crashes', 'horizon': '3', 'method': 'auto'}
    rule = RuleVersion.objects.filter(code='count-forecast').first()
    if rule:
        values['rule'] = str(rule.pk)
    values.update(data.dict() if hasattr(data, 'dict') else data)
    return ForecastForm(values)
