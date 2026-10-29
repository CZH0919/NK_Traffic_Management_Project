from datetime import timedelta
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q,F,Min,Max,Count
from django.shortcuts import render,get_object_or_404
from apps.core.permissions import require_capability
from apps.network.models import SamplingRoadMatch
from apps.network.queries import current_network
from .models import SamplingSite,SamplingRequest,TrafficObservation
from .forms import SiteFilter,FlowQuery,ObservationTimeFilter
from .analysis import bounds,summarize,compare,exposure,official_observations

@require_capability('analyze_data')
def sites(request):
    form=SiteFilter(request.GET);qs=SamplingSite.objects.none()
    if form.is_valid():
        f=form.cleaned_data;qs=SamplingSite.objects.select_related('request','borough').order_by('pk')
        if not f.get('inactive'):qs=qs.filter(is_active=True)
        if f.get('q'):qs=qs.filter(Q(street__icontains=f['q'])|Q(source_segment_id__icontains=f['q'])|Q(display_name__icontains=f['q']))
        for key in ('borough','direction'):
            if f.get(key):qs=qs.filter(**{key:f[key]})
        if f.get('request'):qs=qs.filter(request__source_request_id__icontains=f['request'])
        if f.get('road'):qs=qs.filter(road_matches__road_id=f['road'],road_matches__status='matched',road_matches__input_revision=F('revision'))
    page=Paginator(qs,30).get_page(request.GET.get('page'));params=request.GET.copy();params.pop('page',None)
    return render(request,'traffic/sites.html',{'page':page,'form':form,'query':params.urlencode(),'requests_count':SamplingRequest.objects.count()})

@require_capability('analyze_data')
def flow(request,pk):
    site=get_object_or_404(SamplingSite.objects.select_related('request','borough'),pk=pk);limits=bounds(site)
    initial={'start':limits['first'],'end':min(limits['last'],limits['first']+timedelta(days=6)) if limits['first'] else None,'grain':'day'}
    query={**initial,**request.GET.dict()} if request.GET else initial
    form=FlowQuery(query);data=None;comparison=None;exposure_data=None;status=200
    from apps.core.templatetags.ux_values import source_label
    form.fields['compare_site'].picker_empty=f'使用当前地点：{site.display_name or site.street} · {source_label(site.direction)}'
    net=current_network();match=SamplingRoadMatch.objects.filter(site=site,network=net).select_related('road').first()
    if limits['first'] and form.is_valid():
        f=form.cleaned_data
        try:
            data=summarize(site,f['start'],f['end'],f['grain'])
            if f.get('compare_start'):
                other=get_object_or_404(SamplingSite,pk=f.get('compare_site') or site.pk,is_active=True)
                comparison=compare(site,other,f['start'],f['end'],f['compare_start'])
            if net:exposure_data=exposure(site,net,f['start'],f['end'])
        except ValidationError as e:form.add_error(None,e);status=400
    elif limits['first']:status=400
    series_page=Paginator(data['series'] if data else [],48).get_page(request.GET.get('table_page'))
    params=request.GET.copy();params.pop('table_page',None)
    comparison_page=Paginator(comparison['series'] if comparison else [],48).get_page(request.GET.get('compare_page'))
    comparison_params=request.GET.copy();comparison_params.pop('compare_page',None)
    observations=official_observations().filter(site=site)
    if form.is_valid():observations=observations.filter(local_date__range=(form.cleaned_data['start'],form.cleaned_data['end']))
    else:observations=observations.none()
    time_form=ObservationTimeFilter(request.GET)
    if time_form.is_valid():
        if time_form.cleaned_data.get('raw_start_time'):observations=observations.filter(local_time__gte=time_form.cleaned_data['raw_start_time'])
        if time_form.cleaned_data.get('raw_end_time'):observations=observations.filter(local_time__lt=time_form.cleaned_data['raw_end_time'])
    else:observations=observations.none();status=400
    preserved={k:v for k,v in query.items() if k not in ('raw_page','raw_start_time','raw_end_time') and v is not None}
    observations=observations.select_related('conflict').order_by('local_date','local_time','pk')
    raw_page=Paginator(observations,25).get_page(request.GET.get('raw_page'));raw_params=request.GET.copy();raw_params.pop('raw_page',None)
    return render(request,'traffic/flow.html',{'site':site,'limits':limits,'form':form,'data':data,'comparison':comparison,
        'exposure':exposure_data,'match':match,'stale':bool(match and match.input_revision!=site.revision),'table_page':series_page,
        'comparison_page':comparison_page,'comparison_query':comparison_params.urlencode(),'table_query':params.urlencode(),'raw_page':raw_page,'raw_query':raw_params.urlencode(),
        'time_form':time_form,'preserved_query':{k:v.isoformat() if hasattr(v,'isoformat') else v for k,v in preserved.items()}},status=status)
