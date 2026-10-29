"""Shared presentation metadata; never changes model values or query meaning."""
from django import forms
from .help_content import FIELD_HELP

SUGGESTIONS = {
    'vehicle_type': [('Sedan', '轿车'), ('Station Wagon/Sport Utility Vehicle', '旅行车／SUV'), ('Bike', '自行车'), ('Taxi', '出租车'), ('Bus', '公共汽车')],
    'person_type': [('Occupant', '车内人员'), ('Pedestrian', '行人'), ('Bicyclist', '骑行者')],
    'injury': [('Injured', '受伤'), ('Killed', '死亡'), ('Unspecified', '未说明')],
}


def picker(field, kind, *, empty='', scope=''):
    field.picker_kind = kind
    field.picker_empty = empty
    field.picker_scope = scope
    attrs = dict(field.widget.attrs)
    attrs['class'] = 'form-control'
    field.widget = forms.TextInput(attrs=attrs)
    return field


def configure_fields(fields):
    kinds = {'road':'road', 'junction':'junction', 'compare_site':'site', 'site':'site',
             'collision':'collision', 'collision_id':'collision', 'vehicle':'vehicle', 'vehicle_id':'vehicle',
             'risk_run':'risk_run', 'forecast_run':'forecast_run'}
    for name, field in fields.items():
        if name in kinds:
            picker(field, kinds[name], scope='collision' if name == 'vehicle' else 'collision_id' if name == 'vehicle_id' else '')
            if name in ('collision_id','vehicle_id'):
                field.label = '关联事故' if name == 'collision_id' else '关联车辆'
        if name in SUGGESTIONS:
            field.suggestions = SUGGESTIONS[name]
            field.help_text = FIELD_HELP['category'] + ' 建议为常见示例，也可输入其他来源原值。'
        if name == 'source_id': field.help_text = FIELD_HELP['source']
        if name == 'request': field.help_text = FIELD_HELP['request']
        if name == 'category': field.help_text = '填写基础信息中已登记的统一类别代码，例如 sedan；留空不按统一类别限制。'
