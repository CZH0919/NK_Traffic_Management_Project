from django.shortcuts import render, get_object_or_404
from django.core.cache import cache
from django.db.models import Max
import hashlib
import json
from django.core.paginator import Paginator
from apps.collisions.models import Collision, Vehicle, Person
from apps.traffic.models import TrafficObservation
from apps.ingestion.models import Snapshot
from .audit import ACTION_LABELS
from .models import AuditEvent, DataRevision
from .permissions import require_capability


def help_page(request):
    return render(request, 'core/help.html')

def label_events(events):
    for event in events:
        event.action_label = ACTION_LABELS.get(event.action, event.action)
        if event.object_type == 'account' and event.object_id == str(event.actor_id):
            event.object_label = event.actor_label
        else:
            kind = {'authentication':'身份认证', 'account':'账户', 'collision':'事故', 'crashes':'事故','vehicles':'车辆','persons':'人员','quality':'质量问题','category':'类别映射','source':'数据来源','borough':'行政区','saved_query':'个人查询','ImportJob':'文件任务','import':'导入批次', 'ImportBatch':'导入批次', 'Snapshot':'数据快照','RiskRun':'风险计算','ForecastRun':'预测计算','Report':'分析报告','ExportJob':'导出任务','AIBrief':'AI 简报','RuleVersion':'规则版本','SpatialRun':'空间关联任务','MaintenanceRun':'运行维护','road':'道路','junction':'路口','site':'采样地点'}.get(event.object_type, event.object_type)
            reference = str(event.object_id)
            event.object_label = f'{kind} {reference[:8] + chr(8230) if len(reference) > 16 else reference}'.strip()
    return events

@require_capability('view_workspace')
def dashboard(request):
    published = Snapshot.objects.filter(status='published')
    has_data = published.exists()
    catalog=list(Snapshot.objects.order_by('pk').values_list('pk','status','source__kind'))
    revision=DataRevision.objects.aggregate(value=Max('id'))['value'] or 0
    key='workspace-counts:'+hashlib.sha256(repr((catalog,revision)).encode()).hexdigest()
    metrics=cache.get(key)
    if metrics is None:
        metrics=[]
        for label,icon,kind,model in [('事故记录','document','crashes',Collision),('涉事车辆','car','vehicles',Vehicle),('涉事人员','person','persons',Person),('流量观测','chart','traffic',TrafficObservation)]:
            source_states=[state for _,state,source_kind in catalog if source_kind==kind]
            count=None
            if 'published' in source_states:
                qs=model.objects.filter(origin='official',is_active=True)
                # Official-reference constraints guarantee every row has a raw snapshot.
                # If every snapshot in this domain is published, avoid a multi-million-row join.
                if any(state!='published' for state in source_states): qs=qs.filter(raw_record__snapshot__status='published')
                count=qs.count()
            metrics.append({'label':label,'icon':icon,'value':count})
        cache.set(key,metrics,60)
    recent = AuditEvent.objects.all() if request.user.has_perm('core.view_audit') else AuditEvent.objects.filter(actor=request.user)
    coverage={}
    for snapshot in published.select_related('source').order_by('source_id','period_start'):
        item=coverage.setdefault(snapshot.source_id,{'name':snapshot.source.name,'start':None,'end':None,'versions':set(),'count':0})
        item['count']+=1
        if snapshot.period_start: item['start']=min(item['start'],snapshot.period_start) if item['start'] else snapshot.period_start
        if snapshot.period_end: item['end']=max(item['end'],snapshot.period_end) if item['end'] else snapshot.period_end
        if snapshot.manifest.get('version'): item['versions'].add(snapshot.manifest['version'])
    for item in coverage.values(): item['versions']='、'.join(sorted(item['versions']))
    return render(request, 'core/dashboard.html', {'metrics':metrics, 'coverage':list(coverage.values()), 'has_data': has_data, 'events':label_events(list(recent[:3]))})

@require_capability('view_audit')
def audit_log(request):
    outcome = request.GET.get('outcome', '')
    events = AuditEvent.objects.all()
    if outcome in ('success','failure'):
        events = events.filter(outcome=outcome)
    page = Paginator(events, 25).get_page(request.GET.get('page'))
    label_events(page.object_list)
    return render(request, 'core/audit.html', {'page_obj':page, 'outcome':outcome})

@require_capability('view_audit')
def audit_detail(request,pk):
    event=get_object_or_404(AuditEvent.objects.prefetch_related('fieldchange_set'),pk=pk)
    label_events([event])
    from apps.collisions.constants import LABELS
    rows=[]
    for change in event.fieldchange_set.all():
        rows.append({'field':LABELS.get(change.field_name,change.field_name),
            'before':json.dumps(change.previous_value,ensure_ascii=False,indent=2),
            'after':json.dumps(change.new_value,ensure_ascii=False,indent=2),'reason':change.reason})
    return render(request,'core/audit_detail.html',{'event':event,'changes':rows})

def forbidden(request, exception=None):
    return render(request, 'errors/403.html', status=403)

def not_found(request, exception=None):
    return render(request, 'errors/404.html', status=404)

def server_error(request):
    from django.template.loader import get_template
    from django.http import HttpResponse
    return HttpResponse(get_template('errors/unavailable.html').render({}), status=500)
