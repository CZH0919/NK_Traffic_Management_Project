from apps.core.navigation import detail_url, return_context
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q,F,Count
from django.http import Http404,JsonResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST
from apps.core.permissions import require_capability
from apps.core.services import ConcurrentChange
from apps.core.models import Borough
from apps.traffic.models import SamplingSite
from apps.collisions.models import Collision
from .models import NetworkVersion,RoadSegment,Junction,SpatialMatch,SamplingRoadMatch,SpatialRun
from .forms import CatalogFilter,MaintenanceForm,MatchForm,SpatialFilter
from .services import CATALOG,maintain,review_match,initialize_match
from .queries import current_network,parse_map,map_data,selected_feature,selection_area

LABELS={'road':'道路','junction':'路口','site':'采样地点'}


@require_capability('analyze_data')
def object_search(request):
    """Name search for form pickers, with the same eligible network scope as analysis."""
    kind = request.GET.get('kind')
    if kind in ('collision', 'vehicle', 'risk_run', 'forecast_run'):
        from apps.core.object_lookup import lookup
        return lookup(request)
    if kind not in CATALOG:
        return JsonResponse({'error':'请选择道路、路口或采样地点。'}, status=400)
    try:
        page = int(request.GET.get('page', '1'))
        if not 1 <= page <= 1000: raise ValueError
        object_id = request.GET.get('id', '')
        if object_id and (not object_id.isdigit() or len(object_id) > 18): raise ValueError
        network_id = request.GET.get('network', '')
        network = current_network()
        if network_id:
            if not network_id.isdigit() or len(network_id) > 18: raise ValueError
            network = NetworkVersion.objects.filter(pk=network_id, snapshot__status='published').first()
            if not network: raise ValueError
        borough = request.GET.get('borough', '')
        if borough and not Borough.objects.filter(pk=borough).exists(): raise ValueError
    except (ValueError, TypeError, OverflowError):
        return JsonResponse({'error':'对象、网络或页码无效，请重新选择。'}, status=400)
    q = request.GET.get('q','').strip()
    if len(q) > 200: return JsonResponse({'error':'搜索内容最多 200 字。'}, status=400)
    if not object_id and len(q) < 2:
        return JsonResponse({'items':[], 'has_more':False, 'message':'输入至少两个字符，可使用道路名称或来源编号。'})
    objects = CATALOG[kind].objects.filter(is_active=True).order_by('pk')
    if kind == 'road':
        objects = objects.filter(network=network, is_canonical=True, match_eligible=True).select_related('left_borough','right_borough','from_node','to_node')
        if borough: objects = objects.filter(Q(left_borough_id=borough)|Q(right_borough_id=borough))
        search = Q(name__icontains=q)|Q(display_name__icontains=q)|Q(source_segment_id__icontains=q)
    elif kind == 'junction':
        objects = objects.filter(network=network, is_intersection=True).select_related('borough')
        if borough: objects = objects.filter(borough_id=borough)
        search = Q(name__icontains=q)|Q(display_name__icontains=q)|Q(source_node_id__icontains=q)
    else:
        objects = objects.select_related('borough','request')
        if borough: objects = objects.filter(borough_id=borough)
        search = Q(street__icontains=q)|Q(display_name__icontains=q)|Q(source_segment_id__icontains=q)
    objects = objects.filter(pk=object_id) if object_id else objects.filter(search)
    found = list(objects[(page-1)*20:page*20+1])
    items = []
    from apps.core.templatetags.ux_values import source_label, coordinates
    for obj in found[:20]:
        name = obj.display_name or getattr(obj,'name','') or getattr(obj,'street','') or '未命名对象'
        if kind == 'road':
            areas = list(dict.fromkeys(b.chinese_name for b in (obj.left_borough, obj.right_borough) if b))
            area = ' / '.join(areas) or '行政区未知'
            source = obj.source_segment_id
            endpoints = [' / '.join(node.street_names) or f'节点 #{node.pk}' if node else '端点未知' for node in (obj.from_node,obj.to_node)]
            detail = f'{area} · 长度 {obj.length_m if obj.length_m is not None else "未知"} 米 · {endpoints[0]} → {endpoints[1]}'
        elif kind == 'junction':
            source = obj.source_node_id
            detail = f'{obj.borough.chinese_name if obj.borough else "行政区未知"} · {" / ".join(obj.street_names)} · {coordinates(obj.geom)}'
        else:
            source = obj.source_segment_id
            detail = f'{obj.borough.chinese_name if obj.borough else "行政区未知"} · {source_label(obj.direction)} · {obj.from_street} → {obj.to_street} · 采样请求 {obj.request.source_request_id}'
        items.append({'id':obj.pk, 'name':name, 'detail':detail, 'source_id':source,
                      'network':str(network.pk) if network and kind != 'site' else '',
                      'network_label':str(network) if network and kind != 'site' else obj.source_network_version.replace('(source metadata)','（来源说明）') if kind == 'site' else '',
                      'url':reverse('network_detail',args=[kind,obj.pk]),
                      'map_url':reverse('map')+'?'+kind+'='+str(obj.pk)+(f'&network={network.pk}' if network and kind!='site' else '')})
    return JsonResponse({'items':items,'has_more':len(found)>20,'page':page,'message':'' if items else '没有匹配对象，请更换名称、行政区或网络版本。'})

@require_capability('analyze_data')
def spatial_list(request):
    form=SpatialFilter(request.GET);net=current_network();kind='collision';qs=SpatialMatch.objects.none();totals=[]
    if form.is_valid():
        f=form.cleaned_data;kind=f.get('kind') or 'collision';net=f.get('network') or net
        model=SpatialMatch if kind=='collision' else SamplingRoadMatch
        qs=model.objects.filter(network=net).select_related(kind,'road').order_by('pk')
        totals=list(qs.order_by().values('status').annotate(count=Count('pk')))
        names={'matched':'已关联','ambiguous':'待核查','unmatched':'未匹配','missing':'缺少位置'}
        for item in totals:item['label']=names[item['status']]
        if f.get('status')=='stale':qs=qs.exclude(input_revision=F(kind+'__revision'))
        elif f.get('status'):qs=qs.filter(status=f['status'])
        if f.get('borough'):qs=qs.filter(**{kind+'__borough':f['borough']})
        if f.get('q'):
            fields=['source_record_id','on_street'] if kind=='collision' else ['source_segment_id','street']
            qs=qs.filter(Q(**{kind+'__'+fields[0]+'__icontains':f['q']})|Q(**{kind+'__'+fields[1]+'__icontains':f['q']}))
    page=Paginator(qs,30).get_page(request.GET.get('page'));params=request.GET.copy();params.pop('page',None)
    for item in page:
        owner=getattr(item,kind);item.owner_id=owner.pk;item.stale=item.input_revision!=owner.revision
        item.owner_name=getattr(owner,'on_street',getattr(owner,'street',''))
        item.owner_source=getattr(owner,'source_record_id',getattr(owner,'source_segment_id',''))
    run=SpatialRun.objects.filter(network=net).first()
    return render(request,'network/spatial_list.html',{'form':form,'page':page,'query':params.urlencode(),'totals':totals,'kind':kind,'network':net,'run':run})

@require_capability('analyze_data')
def map_view(request):
    return render(request,'network/map.html',{'boroughs':Borough.objects.all(),'networks':NetworkVersion.objects.filter(snapshot__status='published').order_by('-pk'),'current_network':current_network()})

@require_capability('analyze_data')
def map_selection(request):
    try:
        options=parse_map(request.GET)
        feature=selected_feature(options['selected'],options['network'])
        return JsonResponse({'selected':feature,'nearby':selection_area(feature,options['radius'])})
    except ValidationError as e:return JsonResponse({'error':'；'.join(e.messages)},status=400)

@require_capability('analyze_data')
def map_api(request):
    try:
        from time import perf_counter
        started=perf_counter()
        response=JsonResponse(map_data(parse_map(request.GET),use_cache=True))
        response['Cache-Control']='no-store'
        response['Server-Timing']=f'map;dur={(perf_counter()-started)*1000:.1f}'
        return response
    except ValidationError as e:return JsonResponse({'error':'；'.join(e.messages)},status=400)

@require_capability('analyze_data')
def catalog(request,kind):
    if kind not in ('road','junction'):raise Http404
    form=CatalogFilter(request.GET);objects=CATALOG[kind].objects.none();network=current_network()
    if form.is_valid():
        f=form.cleaned_data;network=f.get('network') or network
        objects=CATALOG[kind].objects.filter(network=network).order_by('pk')
        state=f.get('state') or 'eligible'
        if state=='eligible':objects=objects.filter(is_active=True,**({'is_canonical':True,'match_eligible':True} if kind=='road' else {'is_intersection':True}))
        elif state=='inactive':objects=objects.filter(is_active=False)
        if f.get('q'):
            objects=objects.filter(Q(name__icontains=f['q'])|Q(display_name__icontains=f['q'])|Q(**{('source_segment_id' if kind=='road' else 'source_node_id')+'__icontains':f['q']}))
        if f.get('borough'):
            objects=objects.filter(Q(left_borough=f['borough'])|Q(right_borough=f['borough'])) if kind=='road' else objects.filter(borough=f['borough'])
    page=Paginator(objects,30).get_page(request.GET.get('page'));params=request.GET.copy();params.pop('page',None)
    return render(request,'network/catalog.html',{'kind':kind,'title':LABELS[kind],'form':form,'page':page,'query':params.urlencode(),'network':network})

@require_capability('analyze_data')
def detail(request,kind,pk):
    if kind not in CATALOG:raise Http404
    obj=get_object_or_404(CATALOG[kind],pk=pk);net=current_network() if kind=='site' else obj.network
    matches=SamplingRoadMatch.objects.filter(site=obj,network=net).select_related('road') if kind=='site' else SpatialMatch.objects.filter(**{kind:obj},network=net,status='matched',input_revision=F('collision__revision'),collision__is_active=True).select_related('collision')
    try:feature=selected_feature((kind,pk),net)
    except ValidationError:feature=None
    context={'kind':kind,'title':LABELS[kind],'object':obj,'display_name':obj.display_name or getattr(obj,'name','') or getattr(obj,'street','') or '未命名对象','network':net,'matches':matches[:30],'match_count':matches.count(),'feature':feature}
    if kind=='road':
        context['variants']=RoadSegment.objects.filter(network=net,source_segment_id=obj.source_segment_id).order_by('pk')[:100]
        context['sites']=SamplingRoadMatch.objects.filter(road=obj,status='matched',input_revision=F('site__revision'),site__is_active=True).select_related('site')[:50]
    return render(request,'network/detail.html',context)

@require_capability('maintain_data')
def edit(request,kind,pk):
    if kind not in CATALOG:raise Http404
    obj=get_object_or_404(CATALOG[kind],pk=pk)
    initial={f:getattr(obj,f) for f in ('revision','display_name','review_note','is_active')}
    if kind=='junction':initial['is_intersection']='' if obj.is_intersection is None else str(obj.is_intersection).lower()
    form=MaintenanceForm(request.POST or None,initial=initial,kind=kind);status=200
    if request.method=='POST':
        if form.is_valid():
            data=form.cleaned_data.copy();revision=data.pop('revision');reason=data.pop('reason')
            try:
                maintain(kind,pk,request.user,revision,data,reason);messages.success(request,'维护信息已保存，来源标识与几何保留。')
                return redirect(detail_url(request,'network_detail',kind=kind,pk=pk))
            except ValidationError as e:form.add_error(None,e);status=409 if isinstance(e,ConcurrentChange) else 400
        else:status=400
    return render(request,'network/edit.html',{'form':form,'kind':kind,'object':obj,'title':'维护'+LABELS[kind]},status=status)

@require_capability('analyze_data')
def match_detail(request,kind,pk):
    if kind not in ('collision','site'):raise Http404
    model=SpatialMatch if kind=='collision' else SamplingRoadMatch
    match=get_object_or_404(model.objects.select_related('network','road'),pk=pk)
    obj=match.collision if kind=='collision' else match.site
    initial={'revision':match.revision,'input_revision':obj.revision,'target_kind':'junction' if kind=='collision' and match.junction_id else 'road',
        'target_id':match.junction_id if kind=='collision' and match.junction_id else match.road_id}
    form=MatchForm(request.POST or None,initial=initial);status=200
    from apps.core.form_presentation import picker
    picker_kind=request.POST.get('target_kind',initial['target_kind']) if request.method=='POST' else initial['target_kind']
    picker(form.fields['target_id'], picker_kind if picker_kind in ('road','junction') else initial['target_kind'])
    form.fields['target_id'].label='目标对象'
    if kind=='collision':form.fields.pop('exposure_start');form.fields.pop('exposure_end')
    else:form.fields['target_kind'].choices=[('road','道路')]
    if request.method=='POST':
        if not request.user.has_perm('core.maintain_data'):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        if form.is_valid():
            try:
                review_match(kind,pk,request.user,**form.cleaned_data);messages.success(request,'空间关联已更新并保留核查依据。')
                return redirect('match_detail',kind=kind,pk=pk)
            except (RoadSegment.DoesNotExist,Junction.DoesNotExist):form.add_error('target_id','目标对象不存在。');status=400
            except ValidationError as e:form.add_error(None,e);status=409 if isinstance(e,ConcurrentChange) else 400
        else:status=400
    return render(request,'network/match.html',{'match':match,'object':obj,'kind':kind,'form':form,'stale':match.input_revision!=obj.revision},status=status)

@require_capability('analyze_data')
def collision_matches(request,pk):
    obj=get_object_or_404(Collision,pk=pk)
    return render(request,'network/matches.html',{'object':obj,'matches':SpatialMatch.objects.filter(collision=obj).select_related('network','road','junction')})

@require_POST
@require_capability('maintain_data')
def match_initialize(request,kind,pk):
    if kind not in ('collision','site'):raise Http404
    try:
        value=initialize_match(kind,pk,request.user,int(request.POST.get('input_revision','')),request.POST.get('reason'))
        return redirect('match_detail',kind=kind,pk=value.pk)
    except (Collision.DoesNotExist,SamplingSite.DoesNotExist):raise Http404
    except (ValidationError,ValueError) as e:
        return render(request,'core/action_error.html',{'error':'；'.join(e.messages) if isinstance(e,ValidationError) else '修订号不合法。',
            'back_url':reverse('collision_matches',kwargs={'pk':pk}) if kind=='collision' else reverse('network_detail',kwargs={'kind':'site','pk':pk})},status=409 if isinstance(e,ConcurrentChange) else 400)
