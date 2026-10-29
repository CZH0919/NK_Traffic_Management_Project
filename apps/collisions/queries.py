from django import forms
from django.db.models import Q,Exists,OuterRef
from apps.core.models import Borough,CategoryMapping
from apps.ingestion.models import Snapshot,StagedRecord
from .constants import MODELS,ORIGINS
from .models import Vehicle,Person,CollisionFactor
from .forms import style_fields

class QueryForm(forms.Form):
    date_from=forms.DateField(label='起始日期',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    date_to=forms.DateField(label='截止日期',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    borough=forms.ModelChoiceField(Borough.objects.only('code','name','chinese_name'),label='行政区',required=False,empty_label='全部行政区')
    origin=forms.ChoiceField(label='数据来源',required=False,choices=ORIGINS)
    active=forms.ChoiceField(label='有效状态',required=False,choices=[('active','有效记录'),('inactive','已失效'),('all','全部状态')])
    source_id=forms.CharField(label='来源记录编号',required=False,max_length=150)
    q=forms.CharField(label='关键字',required=False,max_length=100)
    category=forms.CharField(label='统一类别代码',required=False,max_length=50)
    sort=forms.ChoiceField(label='排序',required=False)
    def __init__(self,*args,kind,**kwargs):
        self.kind=kind
        super().__init__(*args,**kwargs)
        if kind=='crashes':
            self.fields['q'].label='道路关键字'
            self.fields['severity']=forms.ChoiceField(label='伤亡情况',required=False,choices=[('','全部'),('fatal','有死亡'),('injured','有受伤'),('none','报告伤亡均为零'),('unknown','总伤亡数缺失')])
            self.fields['vehicle_type']=forms.CharField(label='关联车辆类型',required=False,max_length=100)
            self.fields['person_type']=forms.CharField(label='关联人员类型',required=False,max_length=60)
            self.fields['position']=forms.ChoiceField(label='位置状态',required=False,choices=[('','全部'),('known','有坐标'),('unknown','无可用坐标')])
            self.fields.pop('category')
        else:
            self.fields['collision_id']=forms.IntegerField(label='事故内部编号',min_value=1,required=False)
            self.fields['type']=forms.CharField(label='车辆类型' if kind=='vehicles' else '人员类型',required=False,max_length=100)
            if kind=='vehicles': self.fields['q'].label='制造商／车型关键字'
            else:
                self.fields['q'].label='伤情／角色关键字'
                self.fields['vehicle_id']=forms.IntegerField(label='车辆内部编号',min_value=1,required=False)
                self.fields['injury']=forms.CharField(label='伤害类别',required=False,max_length=60)
                self.fields['age_min']=forms.IntegerField(label='最小年龄',required=False,min_value=0,max_value=130)
                self.fields['age_max']=forms.IntegerField(label='最大年龄',required=False,min_value=0,max_value=130)
        self.fields['sort'].choices=([('date_desc','日期：新到旧'),('date_asc','日期：旧到新')] if kind=='crashes' else [])+[('id_desc','编号：大到小'),('id_asc','编号：小到大')]
        style_fields(self)
        from apps.core.form_presentation import SUGGESTIONS
        from apps.core.help_content import FIELD_HELP
        if kind == 'crashes': self.fields['q'].help_text = FIELD_HELP['street']
        elif 'type' in self.fields:
            self.fields['type'].suggestions = SUGGESTIONS['vehicle_type' if kind == 'vehicles' else 'person_type']
            self.fields['type'].help_text = FIELD_HELP['category']

    def clean(self):
        d=super().clean()
        if d.get('date_from') and d.get('date_to') and d['date_from']>d['date_to']: raise forms.ValidationError('起始日期不能晚于截止日期。')
        if d.get('age_min') is not None and d.get('age_max') is not None and d['age_min']>d['age_max']: raise forms.ValidationError('最小年龄不能大于最大年龄。')
        if d.get('q') and len(d['q'].strip())<2: raise forms.ValidationError('关键字至少填写两个字符。')
        d['origin']=d.get('origin') or 'official'; d['active']=d.get('active') or 'active'
        d['sort']=d.get('sort') or ('date_desc' if self.kind=='crashes' else 'id_desc')
        return d

    def conditions(self):
        result={}
        for k,v in self.cleaned_data.items():
            if v in (None,''): continue
            if hasattr(v,'pk'): v=str(v.pk)
            elif hasattr(v,'isoformat'): v=v.isoformat()
            else: v=str(v)
            result[k]=v
        return result

def published_records(qs,origin):
    # Evaluate unpublished evidence once in the same SQL snapshot. PKs are never
    # NULL; Django's exclusion preserves a nullable raw FK as the former NOT EXISTS did.
    unpublished=StagedRecord.objects.exclude(snapshot__status='published').values('pk')
    if origin=='official':return qs.exclude(raw_record_id__in=unpublished)
    if origin=='all':return qs.filter(~Q(origin='official')|~Q(raw_record_id__in=unpublished))
    return qs


def filtered_entities(kind,form):
    qs=MODELS[kind].objects.all(); d=form.cleaned_data
    origin=d['origin']
    if origin!='all': qs=qs.filter(origin=origin)
    # A record from an unfinished official import is not a queryable published fact.
    qs=published_records(qs,origin)
    if d['active']!='all': qs=qs.filter(is_active=d['active']=='active')
    if d.get('source_id'): qs=qs.filter(source_record_id=d['source_id'])
    prefix='' if kind=='crashes' else 'collision__'
    if d.get('date_from'): qs=qs.filter(**{prefix+'local_date__gte':d['date_from']})
    if d.get('date_to'): qs=qs.filter(**{prefix+'local_date__lte':d['date_to']})
    if d.get('borough'): qs=qs.filter(**{prefix+'borough':d['borough']})
    if kind=='crashes':
        if d.get('q'): qs=qs.filter(Q(on_street__icontains=d['q'])|Q(off_street__icontains=d['q'])|Q(cross_street__icontains=d['q']))
        severity=d.get('severity')
        if severity=='fatal': qs=qs.filter(persons_killed__gt=0)
        elif severity=='injured': qs=qs.filter(persons_injured__gt=0)
        elif severity=='none': qs=qs.filter(persons_killed=0,persons_injured=0)
        elif severity=='unknown': qs=qs.filter(Q(persons_killed__isnull=True)|Q(persons_injured__isnull=True))
        if d.get('position'): qs=qs.filter(geom__isnull=d['position']=='unknown')
        for key,model,field in [('vehicle_type',Vehicle,'vehicle_type'),('person_type',Person,'person_type')]:
            if not d.get(key): continue
            related=model.objects.filter(collision_id=OuterRef('pk'),is_active=True,**{field+'__icontains':d[key]})
            if origin!='all': related=related.filter(origin=origin)
            related=published_records(related,origin)
            qs=qs.filter(Exists(related))
    else:
        if d.get('collision_id'): qs=qs.filter(collision_id=d['collision_id'])
        type_field='vehicle_type' if kind=='vehicles' else 'person_type'
        if d.get('type'): qs=qs.filter(**{type_field+'__icontains':d['type']})
        if d.get('category'):
            values=CategoryMapping.objects.filter(kind=type_field,canonical_code=d['category'],is_active=True).values_list('raw_value',flat=True)
            qs=qs.filter(**{type_field+'__in':values})
        if kind=='vehicles':
            if d.get('q'): qs=qs.filter(Q(make__icontains=d['q'])|Q(model__icontains=d['q']))
        else:
            if d.get('vehicle_id'): qs=qs.filter(vehicle_id=d['vehicle_id'])
            if d.get('q'): qs=qs.filter(Q(complaint__icontains=d['q'])|Q(pedestrian_role__icontains=d['q']))
            if d.get('injury'): qs=qs.filter(injury__icontains=d['injury'])
            if d.get('age_min') is not None: qs=qs.filter(age__gte=d['age_min'])
            if d.get('age_max') is not None: qs=qs.filter(age__lte=d['age_max'])
    order={'date_desc':['-local_date','-id'],'date_asc':['local_date','id'],'id_desc':['-id'],'id_asc':['id']}[d['sort']]
    return qs.order_by(*order)
