from datetime import date
from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError
from shapely import from_wkt
from .constants import MODELS,EDITABLE,LABELS
from .models import CollisionFactor,VehicleFactor,PersonFactor,VehicleDamage
from apps.core.models import Borough

def style_fields(form):
    for f in form.fields.values():
        if isinstance(f.widget,forms.HiddenInput): continue
        f.widget.attrs['class']='form-select' if isinstance(f.widget,forms.Select) else 'form-check-input' if isinstance(f.widget,forms.CheckboxInput) else 'form-control'
        if isinstance(f.widget,forms.Textarea): f.widget.attrs.setdefault('rows',3)

class EntityForm(forms.Form):
    def __init__(self,*args,kind,instance=None,**kwargs):
        self.kind=kind; self.instance=instance; model=MODELS[kind]
        initial={}
        if instance:
            initial={name:getattr(instance,model._meta.get_field(name).attname) for name in EDITABLE[kind]}
            initial['revision']=instance.revision
            if kind=='crashes' and instance.geom:
                p=from_wkt(instance.geom.split(';',1)[1]); initial.update(longitude=Decimal(str(p.x)),latitude=Decimal(str(p.y)))
        super().__init__(*args,initial=initial,**kwargs)
        for name in EDITABLE[kind]:
            field=model._meta.get_field(name)
            if name in ('collision','vehicle'): self.fields[name]=forms.IntegerField(required=False,min_value=1,label=LABELS[name],help_text='使用详情页中的内部编号；来源编号可先在列表检索。')
            elif name=='borough': self.fields[name]=forms.ModelChoiceField(Borough.objects.only('code','name','chinese_name'),required=False,label='行政区',empty_label='未知')
            else: self.fields[name]=field.formfield(label=LABELS[name])
            if name=='local_date': self.fields[name].widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'})
            if name=='local_time': self.fields[name].widget=forms.TimeInput(format='%H:%M',attrs={'type':'time'})
        self.fields['review_note'].max_length=1000
        self.fields['review_note'].help_text='记录业务核查依据，不填写个人身份信息。'
        if not instance:
            self.fields['origin']=forms.ChoiceField(label='记录来源',choices=[('demo','演示记录'),('manual','人工记录')],initial='demo',help_text='官方来源只能由有证据的导入流程建立；演示记录不进入默认官方统计。')
        else: self.fields['revision']=forms.IntegerField(widget=forms.HiddenInput,min_value=1)
        if kind=='crashes':
            self.fields['longitude']=forms.DecimalField(label='经度',required=False,max_digits=18,decimal_places=12,min_value=Decimal('-74.3'),max_value=Decimal('-73.65'),widget=forms.NumberInput(attrs={'step':'any'}))
            self.fields['latitude']=forms.DecimalField(label='纬度',required=False,max_digits=18,decimal_places=12,min_value=Decimal('40.45'),max_value=Decimal('40.95'),widget=forms.NumberInput(attrs={'step':'any'}))
            for slot in range(1,6):
                self.fields[f'factor_{slot}']=forms.CharField(label=f'因素槽位 {slot}',required=False,max_length=160)
                self.fields[f'slot_type_{slot}']=forms.CharField(label=f'槽位 {slot} 车型',required=False,max_length=100)
            if instance:
                for f in CollisionFactor.objects.filter(collision=instance).select_related('factor'):
                    self.initial[f'factor_{f.source_slot}']=f.factor.name if f.factor else ''
                    self.initial[f'slot_type_{f.source_slot}']=f.vehicle_type
        else:
            for slot in (1,2): self.fields[f'factor_{slot}']=forms.CharField(label=f'因素槽位 {slot}',required=False,max_length=160)
            cls=VehicleFactor if kind=='vehicles' else PersonFactor
            if instance:
                for f in cls.objects.filter(**{('vehicle' if kind=='vehicles' else 'person'):instance}).select_related('factor'):
                    self.initial[f'factor_{f.source_slot}']=f.factor.name
            if kind=='vehicles':
                for slot in range(4): self.fields[f'damage_{slot}']=forms.CharField(label=f'损伤部位 {slot+1}',required=False,max_length=160)
                if instance:
                    for f in VehicleDamage.objects.filter(vehicle=instance): self.initial[f'damage_{f.source_slot}']=f.description
        self.fields['reason']=forms.CharField(label='操作依据',min_length=5,max_length=1000,widget=forms.Textarea,help_text='说明来源、核查依据或维护原因；将与前后变化一并留存。')
        style_fields(self)

    def clean(self):
        data=super().clean()
        if self.kind=='crashes':
            x,y=data.get('longitude'),data.get('latitude')
            if (x is None)!=(y is None): raise ValidationError('经度和纬度必须同时填写或同时留空。')
            geom=None if x is None else f'SRID=4326;POINT ({x} {y})'
            if self.instance and self.instance.geom and x is not None:
                old=from_wkt(self.instance.geom.split(';',1)[1])
                if (old.x,old.y)==(float(x),float(y)): geom=self.instance.geom
            data['geom']=geom
        return data

    def mutation_data(self):
        data={name:self.cleaned_data.get(name) for name in EDITABLE[self.kind]}
        if self.kind=='crashes': data['geom']=self.cleaned_data['geom']
        if not self.instance: data['origin']=self.cleaned_data['origin']
        data['factors']=[{'slot':s,'name':self.cleaned_data.get(f'factor_{s}',''),'vehicle_type':self.cleaned_data.get(f'slot_type_{s}','')} for s in range(1,6 if self.kind=='crashes' else 3)]
        if self.kind=='vehicles': data['damage']=[{'slot':s,'description':self.cleaned_data.get(f'damage_{s}','')} for s in range(4)]
        return data

class StatusForm(forms.Form):
    revision=forms.IntegerField(widget=forms.HiddenInput,min_value=1)
    reason=forms.CharField(label='操作依据',min_length=5,max_length=1000,widget=forms.Textarea)
    confirm=forms.BooleanField(label='我已核对对象及影响，确认执行此操作')
    def __init__(self,*args,**kwargs): super().__init__(*args,**kwargs); style_fields(self)

class SaveQueryForm(forms.Form):
    name=forms.CharField(label='查询名称',max_length=100)
    conditions=forms.CharField(widget=forms.HiddenInput)
    def __init__(self,*args,**kwargs): super().__init__(*args,**kwargs); style_fields(self)
