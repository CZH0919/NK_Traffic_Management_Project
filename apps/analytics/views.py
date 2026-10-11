import json
import math
from datetime import date
from urllib.parse import urlencode
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import F,Q,Min,Max
from django.http import JsonResponse,Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.utils.dateparse import parse_datetime
from apps.core.permissions import require_capability
from apps.collisions.forms import SaveQueryForm
from apps.network.matching import rows
from apps.network.models import SamplingRoadMatch
from apps.traffic.models import SamplingSite
from .forms import make_form,RuleForm,bbox_values,DIMENSIONS
from .models import RiskRun,RiskResult,RuleVersion
from .query import cached_payload,drill_sql,scope_where,plain,consistent_read,data_version,digest
from .risk import enqueue,create_rule,is_stale
from .jobs import online

def save_form(request,kind,form):
    return SaveQueryForm(initial={'conditions':signing.dumps({'owner':request.user.pk,'kind':kind,'conditions':form.conditions()},salt='saved-query-v1',compress=True)})

def drill_url(conditions,kind='crashes',dimension=None,key=None):
    params={**conditions}
    if dimension is not None:params.update(bucket_dimension=dimension,bucket_key=key)
    return reverse('analysis_records',kwargs={'kind':kind})+'?'+urlencode(params)

@require_capability('analyze_data')
def statistics(request):
    form=make_form('statistics',request.GET);payload=None;status=200;context={'form':form,'kind':'statistics'}
    if form.is_valid():
        payload,hit=cached_payload('statistics',form);conditions=form.conditions()
        for row in payload['rows']:row['url']=drill_url(conditions,row['record_kind'],payload['dimension'],row['key'])
        summary=payload['summary']
        cards=[('n','登记事故','起','crashes'),('injured','已报告受伤','人','crashes'),('killed','已报告死亡','人','crashes'),('vehicles','关联车辆','条','vehicles'),('persons','关联人员','条','persons')]
        context.update(cards=[{'label':label,'value':summary[k],'unit':unit,'url':drill_url(conditions,record_kind)} for k,label,unit,record_kind in cards],
            payload=payload,summary=summary,cache_hit=hit,save_form=save_form(request,'statistics',form),conditions=urlencode(conditions),
            page=Paginator(payload['rows'],30).get_page(request.GET.get('page')),dimension_label=dict(DIMENSIONS)[payload['dimension']],
            computed_at=parse_datetime(payload['computed_at']),
            chart={**payload,'rows':payload['rows'][:20] if payload['dimension'] in ('factor','vehicle_type','person_type','injury') else payload['rows']})
    else:status=400
    return render(request,'analytics/statistics.html',context,status=status)

@require_capability('analyze_data')
def comparison(request):
    form=make_form('comparison',request.GET);context={'form':form,'kind':'comparison'};status=200
    if form.is_valid():
        payload,hit=cached_payload('comparison',form);a=form.conditions();b={k:v for k,v in a.items() if not k.startswith('compare_')}
        b.update(date_from=a['compare_from'],date_to=a['compare_to']);b.pop('borough',None)
        if a.get('compare_borough'):b['borough']=a['compare_borough']
        for row in payload['comparisons']:
            kind='vehicles' if row['key']=='vehicles' else 'persons' if row['key']=='persons' else 'crashes'
            row['url_a']=drill_url(a,kind);row['url_b']=drill_url(b,kind)
        for row in payload['series']:
            if row['date_a']:row['url_a']=drill_url(a,'crashes','date',row['date_a'])
            if row['date_b']:row['url_b']=drill_url(b,'crashes','date',row['date_b'])
        context.update(payload=payload,save_form=save_form(request,'comparison',form),conditions=urlencode(a),
            page=Paginator(payload['series'],30).get_page(request.GET.get('page')))
    else:status=400
    return render(request,'analytics/comparison.html',context,status=status)

@require_capability('analyze_data')
def analysis_records(request,kind):
    if kind not in ('crashes','vehicles','persons'):raise Http404
    form=make_form('statistics',request.GET);status=200;objects=[];count=0;dimension=request.GET.get('bucket_dimension');key=request.GET.get('bucket_key','')
    if form.is_valid():
        try:
            sql,args=drill_sql(form.cleaned_data,kind,dimension,key)
            count=rows('SELECT count(*) n'+sql,args)[0]['n'];pages=Paginator(range(count),30);page=pages.get_page(request.GET.get('page'));offset=(page.number-1)*30
            select={'crashes':"c.id,c.source_record_id,c.local_date,c.on_street label,c.persons_injured,c.persons_killed",
                'vehicles':"x.id,x.source_record_id,x.collision_id,x.vehicle_type label,x.make detail",'persons':"x.id,x.source_record_id,x.collision_id,x.person_type label,x.injury detail"}[kind]
            alias='c' if kind=='crashes' else 'x';objects=rows('SELECT '+select+sql+f' ORDER BY {alias}.id LIMIT 30 OFFSET %s',[*args,offset])
        except ValueError:form.add_error(None,'下钻维度与对象不匹配。');status=400;page=Paginator([],30).get_page(1)
    else:status=400;page=Paginator([],30).get_page(1)
    params=request.GET.copy();params.pop('page',None)
    return render(request,'analytics/records.html',{'form':form,'kind':kind,'objects':objects,'page':page,'count':count,'query':params.urlencode(),
        'conditions':urlencode(form.conditions()) if form.is_valid() else '', 'bucket':key,'dimension':dict(DIMENSIONS).get(dimension)},status=status)

@require_capability('analyze_data')
def analysis_map_data(request):
    form=make_form('statistics',request.GET)
    try:
        if not form.is_valid():raise ValidationError('统计条件不合法。')
        bbox=bbox_values(request.GET.get('view_bbox','-74.3,40.45,-73.65,41'))
        zoom=int(request.GET.get('zoom',10))
        if not 0<=zoom<=22:raise ValueError
        where,args=scope_where(form.cleaned_data,extra_bbox=bbox);where+=' AND c.geom IS NOT NULL'
        n=rows('SELECT count(*) n FROM collisions_collision c WHERE '+where,args)[0]['n'];cell=max(40,math.ceil(156543.033928/2**zoom*60/10)*10)
        if n>1000 or zoom<14:
            data=rows("SELECT count(*) n,ST_AsGeoJSON(ST_Centroid(ST_Collect(c.geom))) geometry FROM collisions_collision c WHERE "+where+' GROUP BY ST_SnapToGrid(ST_Transform(c.geom,3857),%s) ORDER BY n DESC LIMIT 1001',[*args,cell]);mode='aggregate'
        else:
            data=rows('SELECT c.id,c.on_street label,1 n,ST_AsGeoJSON(c.geom) geometry FROM collisions_collision c WHERE '+where+' ORDER BY c.id LIMIT 1001',args);mode='points'
        features=[{'type':'Feature','geometry':json.loads(r.pop('geometry')),'properties':r} for r in data[:1000]]
        return JsonResponse({'type':'FeatureCollection','features':features,'count':n,'mode':mode,'truncated':len(data)>1000})
    except (ValidationError,ValueError):return JsonResponse({'error':'地图或统计条件不合法。'},status=400)

@require_capability('analyze_data')
def risk_home(request):
    form=make_form('risk',request.GET);query=RiskRun.objects.select_related('rule','requested_by').all()
    context={'form':form,'runs':query[:30],'worker_online':online(),'kind':'risk'}
    if form.is_valid():context['save_form']=save_form(request,'risk',form)
    return render(request,'analytics/risk_home.html',context,status=200 if form.is_valid() else 400)

@require_POST
@require_capability('analyze_data')
def risk_create(request):
    form=make_form('risk',request.POST)
    if request.POST.get('action') == 'save_query':
        from apps.core.query_views import save_current_query
        if save_current_query(request, 'risk', form): return redirect('saved_queries')
        return render(request, 'analytics/risk_home.html', {'form':form, 'kind':'risk', 'worker_online':online(),
            'query_name':request.POST.get('query_name',''), 'runs':RiskRun.objects.select_related('rule')[:30]}, status=400)
    try:
        run=enqueue(request.user,form);return redirect('risk_run',pk=run.pk)
    except ValidationError as e:
        if form.is_valid():form.add_error(None,e)
        return render(request,'analytics/risk_home.html',{'form':form,'runs':RiskRun.objects.select_related('rule')[:30],'worker_online':online(),'kind':'risk'},status=400)

@require_capability('analyze_data')
def risk_run(request,pk):
    run=get_object_or_404(RiskRun.objects.select_related('rule','network','requested_by'),pk=pk);stale=is_stale(run)
    qs=run.results.select_related('target').order_by(F('score').desc(nulls_last=True),'-rank','pk')
    q=request.GET.get('q','').strip()[:200];grade=request.GET.get('grade','')
    if q:qs=qs.filter(label__icontains=q)
    if grade in ('lower','moderate','high','very_high','insufficient'):qs=qs.filter(grade=grade)
    page=Paginator(qs,30).get_page(request.GET.get('page'));params=request.GET.copy();params.pop('page',None)
    return render(request,'analytics/risk_run.html',{'run':run,'stale':stale,'page':page,'query':params.urlencode(),'worker_online':online(),'q':q,'grade_filter':grade})

@require_capability('analyze_data')
def risk_status(request,pk):
    run=get_object_or_404(RiskRun,pk=pk)
    return JsonResponse({'status':run.status,'label':run.get_status_display(),'worker_online':online(),'error':bool(run.error_code)})

@require_POST
@require_capability('analyze_data')
def risk_repeat(request,pk):
    run=get_object_or_404(RiskRun,pk=pk);form=make_form('risk',run.conditions)
    try:return redirect('risk_run',pk=enqueue(request.user,form).pk)
    except ValidationError as e:return render(request,'core/action_error.html',{'error':'；'.join(e.messages),'back_url':reverse('risk_run',kwargs={'pk':pk})},status=400)

def result_conditions(result):
    run=result.run;conditions={k:v for k,v in run.conditions.items() if k in ('date_from','date_to','borough','network')}
    if result.target.scope=='borough':conditions['borough']=str(result.target.borough_id)
    elif result.target.scope in ('road','junction'):conditions[result.target.scope]=str(getattr(result.target,result.target.scope+'_id'))
    return conditions

@require_capability('analyze_data')
def risk_detail(request,pk):
    result=get_object_or_404(RiskResult.objects.select_related('run__rule','run__network','target'),pk=pk);stale=is_stale(result.run)
    conditions=result_conditions(result);form=make_form('statistics',conditions);support=[];sites=[];exposure_result=None
    if form.is_valid() and not stale:
        sql,args=drill_sql(form.cleaned_data,'crashes');support=rows('SELECT c.id,c.local_date,c.on_street,c.persons_injured,c.persons_killed'+sql+' ORDER BY c.local_date DESC,c.id DESC LIMIT 12',args)
    if result.target.scope=='road':
        site_ids=SamplingRoadMatch.objects.filter(road_id=result.target.road_id,network=result.run.network,status='matched',input_revision=F('site__revision'),site__is_active=True).values('site_id')
        sites=list(SamplingSite.objects.filter(pk__in=site_ids).annotate(first=Min('trafficobservation__local_date'),last=Max('trafficobservation__local_date')).order_by('pk'))
        for site in sites:
            site.window_from=max(site.first,result.run.date_from) if site.first else None;site.window_to=min(site.last,result.run.date_to) if site.last else None
            site.overlaps=bool(site.window_from and site.window_to>=site.window_from)
        selected=request.GET.get('exposure_site')
        if selected and not stale:
            site=next((s for s in sites if str(s.pk)==selected and s.overlaps),None)
            if site:
                from apps.traffic.analysis import exposure
                exposure_result=exposure(site,result.run.network,site.window_from,site.window_to)
                exposure_result.update(date_from=site.window_from,date_to=site.window_to)
    return render(request,'analytics/risk_detail.html',{'result':result,'run':result.run,'stale':stale,'support':support,'support_url':drill_url(conditions),
        'statistics_url':reverse('statistics')+'?'+urlencode(conditions),'sites':sites,'exposure':exposure_result,'components':list(result.components.items())})

@require_capability('analyze_data')
def risk_map_data(request,pk):
    run=get_object_or_404(RiskRun,pk=pk)
    if run.status!='succeeded':return JsonResponse({'error':'计算结果尚未就绪。'},status=409)
    try:west,south,east,north=bbox_values(request.GET.get('view_bbox','-74.3,40.45,-73.65,41'))
    except ValidationError:return JsonResponse({'error':'地图范围不合法。'},status=400)
    filters="run_id=%s AND point IS NOT NULL AND (point->>0)::double precision BETWEEN %s AND %s AND (point->>1)::double precision BETWEEN %s AND %s";args=[run.pk,west,east,south,north]
    q=request.GET.get('q','').strip()[:200];grade=request.GET.get('grade','')
    if q:filters+=' AND label ILIKE %s';args.append('%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
    if grade in ('lower','moderate','high','very_high','insufficient'):filters+=' AND grade=%s';args.append(grade)
    n=rows('SELECT count(*) n FROM analytics_riskresult WHERE '+filters,args)[0]['n']
    values=rows('SELECT id,label,score,grade,point,counts->>\'n\' n FROM analytics_riskresult WHERE '+filters+' ORDER BY score DESC NULLS LAST,id LIMIT 500',args)
    features=[]
    for row in values:
        point=json.loads(row.pop('point')) if isinstance(row['point'],str) else row.pop('point')
        features.append({'type':'Feature','geometry':{'type':'Point','coordinates':point},'properties':row})
    return JsonResponse({'type':'FeatureCollection','features':features,'count':n,'truncated':n>500,'stale':is_stale(run)})

@require_capability('analyze_data')
def rules(request):
    return render(request,'analytics/rules.html',{'rules':RuleVersion.objects.select_related('created_by').filter(code='historical-intensity')})

@require_capability('maintain_data')
def rule_create(request):
    form=RuleForm(request.POST or None);status=200
    if request.method=='POST':
        if form.is_valid():
            try:
                create_rule(request.user,form.cleaned_data);messages.success(request,'新规则版本已保存，原有规则和结果保留。');return redirect('risk_rules')
            except ValidationError as e:form.add_error(None,e);status=400
        else:status=400
    return render(request,'analytics/rule_form.html',{'form':form},status=status)
