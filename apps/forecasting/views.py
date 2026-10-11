from datetime import date
from urllib.parse import urlencode
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import JsonResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST
from apps.core.permissions import require_capability
from apps.analytics.views import save_form, drill_url
from apps.analytics.jobs import online
from apps.analytics.query import plain
from apps.analytics.models import RuleVersion
from .forms import make_form
from .models import ForecastRun
from .services import enqueue, is_stale


@require_capability('analyze_data')
def home(request):
    form = make_form(request.GET)
    context = {'form': form, 'kind': 'forecast', 'worker_online': online(),
               'runs': ForecastRun.objects.select_related('target', 'rule')[:30]}
    if form.is_valid(): context['save_form'] = save_form(request, 'forecast', form)
    return render(request, 'forecasting/home.html', context, status=200 if form.is_valid() else 400)


@require_POST
@require_capability('analyze_data')
def create(request):
    form = make_form(request.POST)
    if request.POST.get('action') == 'save_query':
        from apps.core.query_views import save_current_query
        if save_current_query(request, 'forecast', form): return redirect('saved_queries')
        return render(request, 'forecasting/home.html', {'form':form, 'kind':'forecast', 'worker_online':online(),
            'query_name':request.POST.get('query_name',''), 'runs':ForecastRun.objects.select_related('target','rule')[:30]}, status=400)
    try:
        return redirect('forecast_run', pk=enqueue(request.user, form).pk)
    except ValidationError as error:
        if form.is_valid(): form.add_error(None, error)
        return render(request, 'forecasting/home.html', {'form': form, 'kind': 'forecast', 'worker_online': online(),
            'runs': ForecastRun.objects.select_related('target', 'rule')[:30]}, status=400)


@require_POST
@require_capability('analyze_data')
def repeat(request, pk):
    run = get_object_or_404(ForecastRun, pk=pk)
    form = make_form(run.conditions)
    try:
        return redirect('forecast_run', pk=enqueue(request.user, form).pk)
    except ValidationError as error:
        return render(request, 'core/action_error.html', {'error': '；'.join(error.messages), 'back_url': reverse('forecast_run', kwargs={'pk': pk})}, status=400)


@require_capability('analyze_data')
def status(request, pk):
    run = get_object_or_404(ForecastRun, pk=pk)
    return JsonResponse({'status': run.status, 'label': run.get_status_display(), 'worker_online': online(), 'error': bool(run.error_code)})


@require_capability('analyze_data')
def run_view(request, pk):
    run = get_object_or_404(ForecastRun.objects.select_related('rule', 'target'), pk=pk)
    panel = request.GET.get('panel', 'forecast')
    if panel not in ('forecast', 'backtest', 'anomalies', 'history'): raise Http404
    try:
        step = int(request.GET.get('step', 1))
        if not 1 <= step <= run.horizon: raise ValueError
    except ValueError:
        return render(request, 'core/action_error.html', {'error': '回测步长超出此次外推范围。', 'back_url': reverse('forecast_run', kwargs={'pk': pk})}, status=400)
    stale = is_stale(run)
    history = [dict(row, link=reverse('forecast_period', kwargs={'pk': pk, 'index': i}) if not stale else None) for i, row in enumerate(run.history)]
    context = {'run': run, 'panel': panel, 'stale': stale, 'step': step, 'steps': range(1, run.horizon+1), 'worker_online': online(),
               'conditions': urlencode(run.conditions), 'kind': 'forecast'}
    if run.status == 'succeeded':
        context['points'] = list(run.points.all())
        context['chart'] = {'kind': 'forecast', 'history': history, 'points': plain(list(run.points.values())), 'unit': '起' if run.metric == 'crashes' else '人', 'origin': str(run.training_end or '')}
        if panel == 'history':
            context['page'] = Paginator(list(reversed(history)), 30).get_page(request.GET.get('page'))
        elif panel == 'anomalies':
            state = request.GET.get('state', 'flagged')
            if state not in ('flagged', 'all', 'insufficient'): raise Http404
            query = run.anomalies.all()
            if state == 'flagged': query = query.filter(status__in=['high', 'low'])
            elif state == 'insufficient': query = query.filter(status='insufficient')
            page = Paginator(query, 30).get_page(request.GET.get('page'))
            indices = {r['start']: i for i, r in enumerate(history)}
            for row in page:
                row.support_url = reverse('forecast_period', kwargs={'pk': pk, 'index': indices[row.period_start.isoformat()]}) if not stale else None
            context.update(page=page, state=state)
        elif panel == 'backtest' and run.summary.get('eligible'):
            selected = run.summary['selected']['key']
            values = list(run.folds.filter(phase='evaluation', method=selected, step=step).order_by('period_start').values())
            indices = {r['start']: i for i, r in enumerate(history)}
            for row in values:
                row['link'] = reverse('forecast_period', kwargs={'pk': pk, 'index': indices[row['period_start'].isoformat()]}) if not stale else None
            context.update(page=Paginator(values, 30).get_page(request.GET.get('page')), chart={'kind': 'backtest', 'rows': plain(values), 'unit': '起' if run.metric == 'crashes' else '人', 'step': step})
            context['step_comparisons'] = [{**row, 'current': next(m for m in row['by_step'] if m['step'] == step)} for row in run.summary['comparisons']]
            selected_step = next(row['current'] for row in context['step_comparisons'] if row['selected'])
            naive_step = next(row['current'] for row in context['step_comparisons'] if row['key'] == 'naive')
            context['step_beats_naive'] = selected_step['mae'] < naive_step['mae']
        params = request.GET.copy(); params.pop('page', None)
        context['query'] = params.urlencode()
    return render(request, 'forecasting/run.html', context)


@require_capability('analyze_data')
def period_records(request, pk, index):
    run = get_object_or_404(ForecastRun, pk=pk, status='succeeded')
    if not 0 <= index < len(run.history): raise Http404
    if is_stale(run):
        return render(request, 'core/action_error.html', {'error': '历史结果的数据版本已变化，请重新计算后查看当前支撑记录。', 'back_url': reverse('forecast_run', kwargs={'pk': pk})}, status=409)
    row = run.history[index]
    conditions = {k: v for k, v in run.conditions.items() if k in ('borough', 'road', 'junction', 'network')}
    conditions.update(date_from=row['observed_from'], date_to=row['observed_to'], dimension='date', metric=run.metric)
    return redirect(drill_url(conditions))


@require_capability('analyze_data')
def rules(request):
    return render(request, 'forecasting/rules.html', {'rules': RuleVersion.objects.filter(code='count-forecast')})
