"""Versioned forecasting evidence over the existing official accident cohort."""
from datetime import timedelta
from django.db import connection, transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.analytics.models import AnalysisTarget
from apps.analytics.query import consistent_read, data_version, digest, plain, scope_where, rows, coverage_intervals
from apps.core.services import require_actor
from apps.core.audit import record_event
from .models import ForecastRun, ForecastPoint, BacktestFold, AnomalyResult
from .forms import make_form
from .series import series, continuous_tail, next_start, period_end
from .methods import evaluate, anomalies, RULE_PARAMETERS


def version():
    return {**data_version(), 'forecast_engine': RULE_PARAMETERS['engine']}


def is_stale(run):
    return bool(run.data_version) and digest(run.data_version) != digest(version())


def label(target):
    if target.scope == 'city': return '纽约全市'
    if target.scope == 'borough': return target.borough.chinese_name
    obj = getattr(target, target.scope)
    return f'{obj.display_name or obj.name or "未命名"} · #{obj.pk}'


@transaction.atomic
def enqueue(actor, form):
    require_actor(actor, 'analyze_data')
    if not form.is_valid(): raise ValidationError('请修正预测条件。')
    d = form.cleaned_data
    with connection.cursor() as c:
        c.execute('SELECT pg_advisory_xact_lock(%s,%s)', [20261006, actor.pk])
    if ForecastRun.objects.filter(requested_by=actor, status__in=['queued', 'running']).exists():
        raise ValidationError('已有预测计算正在排队或执行，请先查看结果。')
    target_args = {'scope': d['level']}
    if d['level'] == 'borough': target_args['borough'] = d['borough']
    elif d['level'] in ('road', 'junction'): target_args[d['level']+'_id'] = d[d['level']]
    target, _ = AnalysisTarget.objects.get_or_create(**target_args)
    run = ForecastRun.objects.create(requested_by=actor, target=target, rule=d['rule'], network=d.get('network'),
        date_from=d['date_from'], date_to=d['date_to'], frequency=d['frequency'], metric=d['metric'], horizon=d['horizon'], conditions=form.conditions())
    record_event(actor=actor, action='forecast_requested', object_type='ForecastRun', object_id=run.pk)
    return run


def compute_run(pk):
    with consistent_read():
        with connection.cursor() as c:
            c.execute('SELECT pg_try_advisory_xact_lock_shared(%s,%s)', [20261004, 2])
            if not c.fetchone()[0]: return False
        run = ForecastRun.objects.select_related('requested_by', 'rule', 'target__borough', 'target__road', 'target__junction').get(pk=pk)
        require_actor(run.requested_by, 'analyze_data')
        if run.status != 'running': return True
        form = make_form(run.conditions)
        if not form.is_valid(): raise ValidationError('所选对象或规则已失效，请重新选择。')
        d, p = form.cleaned_data, run.rule.parameters
        if p.get('engine') != RULE_PARAMETERS['engine']: raise ValidationError('不支持该规则的算法版本。')
        history = series(d)
        tail = continuous_tail(history)
        summary, folds, future = evaluate(tail, run.frequency, run.horizon, d['method'], p)
        complete = [r for r in history if r['calendar_complete']]
        run.training_end = complete[-1]['end'] if complete else None
        # Child insertion guards require the actual complete-period origin.
        run.save(update_fields=['training_end'])
        if complete and complete[-1]['value'] is None:
            summary['reasons'].insert(0, '末端完整周期缺少可建模值，不能退回旧时点冒充当前预测')
        summary.update({'object_label': label(run.target), 'metric_label': run.get_metric_display(),
                        'data_available_through': max((b for a, b in coverage_intervals('crashes')), default=None),
                        'calendar_periods': len(history), 'excluded_partial_periods': sum(not r['calendar_complete'] for r in history),
                        'unusable_periods': sum(r['value'] is None for r in history),
                        'usable_from': tail[0]['start'] if tail else None,
                        'definition': '当前快照的历史回溯评估，不模拟当年数据发布、补报或历史路网状态；预测仅为周期数量外推，不是实时预警',
                        'interval_note': '按步长使用评估段绝对误差的经验 80% 分位；至少 10 个误差样本。仅为经验参考，不保证未来覆盖率。'})
        where, args = scope_where({'date_from': d['date_from'], 'date_to': d['date_to']})
        summary['unknown_borough_city'] = rows('SELECT count(*) n FROM collisions_collision c WHERE '+where+' AND c.borough_id IS NULL', args)[0]['n']
        anomaly_rows = anomalies(history, p)
        summary['anomalies'] = {state: sum(r['status'] == state for r in anomaly_rows) for state in ('high', 'low', 'normal', 'insufficient')}
        if future:
            start = run.training_end + timedelta(days=1)
            points = []
            for item in future:
                points.append(ForecastPoint(run=run, period_start=start, period_end=period_end(start, run.frequency), **item))
                start = next_start(start, run.frequency)
            ForecastPoint.objects.bulk_create(points)
        BacktestFold.objects.bulk_create([BacktestFold(run=run, **row) for row in folds], batch_size=1000)
        AnomalyResult.objects.bulk_create([AnomalyResult(run=run, **row) for row in anomaly_rows], batch_size=1000)
        run.history = plain(history)
        run.summary = plain(summary)
        run.data_version = version()
        run.status = 'succeeded'
        run.error_code = ''
        run.finished_at = timezone.now()
        run.save()
        record_event(actor=run.requested_by, action='forecast_computed', object_type='ForecastRun', object_id=run.pk, details={'count': len(future), 'revision': run.rule.version})
        return True
