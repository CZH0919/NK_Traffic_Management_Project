from urllib.parse import urlencode
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction,IntegrityError,connection
from django.shortcuts import get_object_or_404,render,redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from .permissions import require_capability
from .models import SavedQuery
from .audit import record_event
from apps.collisions.forms import SaveQueryForm
from apps.collisions.queries import QueryForm
from apps.collisions.views import valid_kind

ANALYSIS_KINDS={'statistics','comparison','risk','forecast'}


def save_current_query(request, kind, query):
    """Validate the submitted form, not a signature made on an earlier GET."""
    if not query.is_valid(): return False
    name = request.POST.get('query_name', '').strip()
    if not name or len(name) > 100:
        query.add_error(None, '保存查询时请填写 1—100 字的查询名称；当前条件尚未保存。')
        return False
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s,%s)', [20261005,request.user.pk])
            saved = SavedQuery.objects.create(owner=request.user, name=name, kind=kind,
                                             conditions=query.conditions(), schema_version=2)
            record_event(actor=request.user, action='query_saved', object_type='saved_query', object_id=saved.pk)
    except IntegrityError:
        query.add_error(None, '已有同名查询，请使用另一个名称；当前条件尚未保存。')
        return False
    messages.success(request, '当前表单条件已保存；载入时使用当前数据重新查询。')
    return True

def query_form(kind,conditions):
    if kind=='forecast':
        from apps.forecasting.forms import make_form
        return make_form(conditions)
    if kind in ANALYSIS_KINDS:
        from apps.analytics.forms import make_form
        return make_form(kind,conditions)
    valid_kind(kind)
    return QueryForm(conditions,kind=kind)

def query_destination(kind):
    return reverse({'statistics':'statistics','comparison':'comparison','risk':'risk_home','forecast':'forecast_home'}[kind]) if kind in ANALYSIS_KINDS else reverse('entity_list',kwargs={'kind':kind})

@require_capability('analyze_data')
def saved_queries(request):
    archived=request.GET.get('deleted')=='1'
    records=SavedQuery.objects.filter(owner=request.user,is_active=not archived)
    return render(request,'core/saved_queries.html',{'queries':records,'archived':archived})

@require_POST
@require_capability('analyze_data')
def save_query(request,kind):
    if kind not in ANALYSIS_KINDS:valid_kind(kind)
    form=SaveQueryForm(request.POST)
    error='请填写有效的查询名称。'
    if form.is_valid():
        try:
            payload=signing.loads(form.cleaned_data['conditions'],salt='saved-query-v1',max_age=86400)
            if payload.get('owner')!=request.user.pk or payload.get('kind')!=kind: raise signing.BadSignature
            query=query_form(kind,payload['conditions'])
            if not query.is_valid(): raise ValueError
            with transaction.atomic():
                with connection.cursor() as cursor: cursor.execute('SELECT pg_advisory_xact_lock(%s,%s)',[20261005,request.user.pk])
                saved=SavedQuery.objects.create(owner=request.user,name=form.cleaned_data['name'],kind=kind,conditions=query.conditions(),schema_version=2 if kind in ANALYSIS_KINDS else 1)
                record_event(actor=request.user,action='query_saved',object_type='saved_query',object_id=saved.pk)
            messages.success(request,'查询条件已保存；载入时将使用当前数据重新查询。')
            return redirect('saved_queries')
        except (signing.BadSignature,ValueError,KeyError): error='筛选状态已失效，请重新检索后保存。'
        except IntegrityError: error='已有同名查询，请使用另一个名称。'
    return render(request,'core/action_error.html',{'error':error,'back_url':query_destination(kind)},status=400)

@require_capability('analyze_data')
def load_query(request,pk):
    saved=get_object_or_404(SavedQuery,pk=pk,owner=request.user,is_active=True)
    form=query_form(saved.kind,saved.conditions)
    if saved.schema_version!=(2 if saved.kind in ANALYSIS_KINDS else 1) or not form.is_valid():
        return render(request,'core/action_error.html',{'error':'该查询条件版本需要重新核对。','back_url':reverse('saved_queries')},status=400)
    return redirect(query_destination(saved.kind)+'?'+urlencode(form.conditions()))

@require_POST
@require_capability('analyze_data')
@transaction.atomic
def update_query(request,pk):
    with connection.cursor() as cursor: cursor.execute('SELECT pg_advisory_xact_lock(%s,%s)',[20261005,request.user.pk])
    saved=get_object_or_404(SavedQuery.objects.select_for_update(),pk=pk,owner=request.user)
    action=request.POST.get('action')
    try:
        if action=='delete': saved.is_active=False
        elif action=='restore':
            if SavedQuery.objects.filter(owner=request.user,name=saved.name,is_active=True).exclude(pk=saved.pk).exists(): raise ValidationError('存在同名查询，请先改名再恢复。')
            saved.is_active=True
        elif action=='rename':
            name=request.POST.get('name','').strip()
            if not name or len(name)>100: raise ValidationError('名称需为 1—100 字。')
            if saved.is_active and SavedQuery.objects.filter(owner=request.user,name=name,is_active=True).exclude(pk=saved.pk).exists(): raise ValidationError('已有同名查询。')
            saved.name=name
        else: raise ValidationError('未知操作。')
        saved.save()
        record_event(actor=request.user,action='query_deleted' if action=='delete' else 'query_saved',object_type='saved_query',object_id=saved.pk)
        messages.success(request,'个人查询已更新。')
        return redirect('saved_queries')
    except ValidationError as e: return render(request,'core/action_error.html',{'error':'；'.join(e.messages),'back_url':reverse('saved_queries')},status=400)
