"""Bounded pickers for business records and completed analysis results."""
from datetime import date
from uuid import UUID
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse
from apps.collisions.models import Collision, Vehicle
from apps.collisions.queries import published_records
from apps.analytics.models import RiskRun
from apps.forecasting.models import ForecastRun


def lookup(request):
    kind = request.GET.get('kind')
    q = request.GET.get('q', '').strip()
    raw_id = request.GET.get('id', '')
    try:
        page = int(request.GET.get('page', '1'))
        if not 1 <= page <= 1000 or len(q) > 200: raise ValueError
        identity = UUID(raw_id) if raw_id and kind in ('risk_run','forecast_run') else int(raw_id) if raw_id else None
        if raw_id and kind in ('collision','vehicle') and not 0 < identity < 2**63: raise ValueError
        parent = request.GET.get('collision_id', '')
        if parent and (not parent.isdigit() or not 0 < int(parent) < 2**63): raise ValueError
    except (ValueError, TypeError, OverflowError):
        return JsonResponse({'error':'编号或页码无效，请重新选择。'}, status=400)
    if not identity and len(q) < 2 and kind in ('collision','vehicle') and not (kind == 'vehicle' and parent):
        return JsonResponse({'items':[], 'has_more':False, 'message':'请输入至少两个字符，可用来源编号、街道、日期或对象名称。'})
    items=[]
    if kind in ('collision','vehicle'):
        model = Collision if kind == 'collision' else Vehicle
        qs = published_records(model.objects.filter(is_active=True), 'all')
        if kind == 'vehicle':
            qs = qs.select_related('collision')
            if parent: qs = qs.filter(collision_id=parent)
        prefix = '' if kind == 'collision' else 'collision__'
        query = Q(source_record_id__icontains=q) | Q(**{prefix+'on_street__icontains':q})
        if q.isdigit() and len(q) < 19: query |= Q(pk=int(q))
        if kind == 'vehicle': query |= Q(vehicle_type__icontains=q)
        try: query |= Q(**{prefix+'local_date':date.fromisoformat(q)})
        except ValueError: pass
        qs = qs.filter(pk=identity) if identity else qs.filter(query) if q else qs
        found = list(qs.order_by('-id')[(page-1)*20:page*20+1])
        for obj in found[:20]:
            event = obj if kind == 'collision' else obj.collision
            context = f'{event.local_date} · {event.on_street or "街道未提供"}' if event else '事故未关联'
            title = f'事故 · {context}' if kind == 'collision' else f'{obj.vehicle_type or "车型未提供"} · {context}'
            items.append(dict(id=obj.pk, name=title, detail=f'来源记录 {obj.source_record_id or "未提供"} · {obj.get_origin_display()}',
                              source_id=obj.source_record_id or '未提供', network_label='',
                              url=reverse('entity_detail', args=['crashes' if kind == 'collision' else 'vehicles', obj.pk]), map_url=''))
    else:
        model = RiskRun if kind == 'risk_run' else ForecastRun
        qs = model.objects.filter(status='succeeded').select_related('rule')
        if kind == 'forecast_run': qs = qs.select_related('target')
        if identity: qs = qs.filter(pk=identity)
        elif q:
            query = Q(summary__object_label__icontains=q) | Q(rule__name__icontains=q)
            levels = {'全市':'city','行政区':'borough','道路':'road','路口':'junction'}
            if q in levels: query |= Q(**{('target__scope' if kind == 'forecast_run' else 'level'):levels[q]})
            try: query |= Q(date_from=date.fromisoformat(q)) | Q(date_to=date.fromisoformat(q))
            except ValueError: pass
            try: query |= Q(pk=UUID(q))
            except ValueError: pass
            qs = qs.filter(query)
        found = list(qs.order_by('-finished_at','-pk')[(page-1)*20:page*20+1])
        for obj in found[:20]:
            name = obj.summary.get('object_label') or (obj.target.get_scope_display() if kind == 'forecast_run' else obj.get_level_display())
            detail = f'{obj.date_from} 至 {obj.date_to} · {obj.rule}'
            if kind == 'forecast_run':
                method = (obj.summary.get('selected') or {}).get('label') or '适用条件不足，保留历史'
                detail += f' · {obj.get_metric_display()} · {obj.get_frequency_display()}度 · 预测 {obj.horizon} 期 · {method}'
            if obj.finished_at:
                from .templatetags.ux_values import ny_datetime
                detail += ' · 完成 ' + ny_datetime(obj.finished_at)
            items.append(dict(id=str(obj.pk), name=name, detail=detail, source_id=str(obj.pk)[:8], network_label='',
                              url=reverse(kind, args=[obj.pk]), map_url=''))
    return JsonResponse({'items':items, 'page':page, 'has_more':len(found)>20,
                         'message':'' if items else '没有匹配对象，请核对名称、日期或来源编号。'})
