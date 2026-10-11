"""Small deterministic forecasting methods; no random split, imputation or external model."""
import math
from statistics import median

RULE_PARAMETERS = {
    'engine': 'count-forecast-v1', 'season': {'month': 12, 'week': 52},
    'ma_windows': {'month': [3, 6, 12], 'week': [4, 8, 13]},
    'alphas': [0.1, 0.3, 0.5, 0.7, 0.9], 'min_seasons': 3,
    'min_total': 100, 'min_nonzero_fraction': 0.5,
    'interval_quantile': 0.8, 'interval_min_samples': 10,
    'anomaly_window': 12, 'anomaly_min_total': 30,
    'anomaly_mad_scale': 1.4826, 'anomaly_multiplier': 3, 'anomaly_min_margin': 5,
}
METHOD_NAMES = {'naive': '上一期值', 'seasonal': '季节滞后', 'ma': '移动平均', 'ses': '简单指数平滑'}


def specs(frequency, parameters):
    result = [{'key': 'naive', 'method': 'naive', 'label': '上一期值基线'},
              {'key': 'seasonal', 'method': 'seasonal', 'label': '12 个月前基线' if frequency == 'month' else '52 周前基线'}]
    result += [{'key': f'ma:{n}', 'method': 'ma', 'window': n, 'label': f'{n} 期移动平均'} for n in parameters['ma_windows'][frequency]]
    result += [{'key': f'ses:{a}', 'method': 'ses', 'alpha': a, 'label': f'指数平滑 α={a}'} for a in parameters['alphas']]
    return result


def predict(values, specification, horizon, season):
    if not values or any(v is None or v < 0 or not math.isfinite(v) for v in values):
        raise ValueError('A complete nonnegative history is required')
    method = specification['method']
    if method == 'seasonal':
        if len(values) < season: raise ValueError('Insufficient seasonal history')
        return [float(values[-season+i % season]) for i in range(horizon)]
    if method == 'naive': level = values[-1]
    elif method == 'ma':
        window = specification['window']
        if len(values) < window: raise ValueError('Insufficient moving window')
        level = sum(values[-window:])/window
    elif method == 'ses':
        alpha = specification['alpha']
        if not 0 < alpha <= 1: raise ValueError('Invalid smoothing parameter')
        level = values[0]
        for value in values[1:]: level = alpha*value+(1-alpha)*level
    else: raise ValueError('Unknown forecasting method')
    return [float(level)]*horizon


def error_metrics(folds):
    errors = [abs(f['actual']-f['predicted']) for f in folds]
    return {'n': len(errors), 'mae': sum(errors)/len(errors) if errors else None,
            'rmse': math.sqrt(sum(x*x for x in errors)/len(errors)) if errors else None}


def fold(history, i, step, spec, season, phase):
    predicted = predict([r['value'] for r in history[:i]], spec, step, season)[-1]
    target = history[i+step-1]
    return {'phase': phase, 'method': spec['key'], 'origin_date': history[i-1]['end'], 'train_end': history[i-1]['end'],
            'period_start': target['start'], 'period_end': target['end'], 'step': step, 'actual': target['value'],
            'predicted': predicted, 'baseline': float(history[i-1]['value']), 'absolute_error': abs(target['value']-predicted)}


def evaluate(history, frequency, horizon, requested, parameters):
    """Validation and evaluation occupy the final two seasons, with frozen selection."""
    season = parameters['season'][frequency]
    n = len(history)
    values = [r['value'] for r in history]
    reasons = []
    if n < parameters['min_seasons']*season: reasons.append(f'连续完整周期至少需要 {parameters["min_seasons"]*season} 个，当前 {n} 个')
    if sum(values) < parameters['min_total']: reasons.append(f'连续历史指标合计少于 {parameters["min_total"]}')
    if not values or sum(v > 0 for v in values) < parameters['min_nonzero_fraction']*n: reasons.append('非零周期不足一半，序列过于稀疏')
    summary = {'eligible': not reasons, 'reasons': reasons, 'usable_periods': n, 'total': sum(values), 'nonzero_periods': sum(v>0 for v in values)}
    if reasons: return summary, [], []
    validation_start, evaluation_start = n-2*season, n-season
    candidates = specs(frequency, parameters)
    candidate_scores, validation_folds = [], []
    for spec in candidates:
        folds = [fold(history, i, 1, spec, season, 'validation') for i in range(validation_start, evaluation_start)]
        validation_folds.extend(folds)
        candidate_scores.append({**spec, **error_metrics(folds)})
    # Input ordering defines reproducible tie breaking, with simple baselines first.
    winners = [min((c for c in candidate_scores if c['method'] == method), key=lambda c: c['mae']) for method in METHOD_NAMES]
    selected = min(candidate_scores if requested == 'auto' else [c for c in candidate_scores if c['method'] == requested], key=lambda c: c['mae'])
    evaluation_folds = []
    for spec in winners:
        for i in range(evaluation_start, n):
            for step in range(1, min(horizon, n-i)+1):
                evaluation_folds.append(fold(history, i, step, spec, season, 'evaluation'))
    comparisons = []
    for spec in winners:
        rows = [f for f in evaluation_folds if f['method'] == spec['key']]
        comparisons.append({**spec, 'selected': spec['key'] == selected['key'], 'overall': error_metrics(rows),
                            'by_step': [{'step': h, **error_metrics([f for f in rows if f['step'] == h])} for h in range(1, horizon+1)]})
    selected_rows = [r for r in evaluation_folds if r['method'] == selected['key']]
    naive_mae = next(c['overall']['mae'] for c in comparisons if c['method'] == 'naive')
    selected_mae = error_metrics(selected_rows)['mae']
    future = []
    for step, estimate in enumerate(predict(values, selected, horizon, season), 1):
        errors = sorted(r['absolute_error'] for r in selected_rows if r['step'] == step)
        width = None
        if len(errors) >= parameters['interval_min_samples']:
            width = errors[min(len(errors)-1, math.ceil(parameters['interval_quantile']*len(errors))-1)]
        future.append({'step': step, 'estimate': estimate, 'lower': max(0, estimate-width) if width is not None else None,
                       'upper': estimate+width if width is not None else None, 'interval_samples': len(errors),
                       'interval_method': 'absolute-residual-reference-v1' if width is not None else ''})
    summary.update({'selected': selected, 'candidates': candidate_scores, 'comparisons': comparisons,
                    'training_from': history[0]['start'], 'selection_training_end': history[validation_start-1]['end'],
                    'validation_from': history[validation_start]['start'], 'validation_to': history[evaluation_start-1]['end'],
                    'evaluation_from': history[evaluation_start]['start'], 'evaluation_to': history[-1]['end'],
                    'train_end': history[-1]['end'], 'selected_mae': selected_mae, 'naive_mae': naive_mae,
                    'beats_naive': selected_mae < naive_mae, 'requested': requested})
    return summary, validation_folds+evaluation_folds, future


def anomalies(history, parameters):
    output = []
    window = parameters['anomaly_window']
    for i, row in enumerate(history):
        ref = history[max(0, i-window):i]
        item = {'period_start': row['start'], 'period_end': row['end'], 'observed': row['value'], 'baseline': None, 'lower': None, 'upper': None,
                'reference_from': ref[0]['start'] if ref else None, 'reference_to': ref[-1]['end'] if ref else None,
                'reference_count': len(ref), 'status': 'insufficient', 'reason': ''}
        if row['value'] is None: item['reason'] = row['reason'] or '当前周期不可用于判断'
        elif len(ref) < window: item['reason'] = f'此前不足 {window} 个周期'
        elif any(r['value'] is None for r in ref): item['reason'] = '此前窗口存在缺数或不完整周期'
        elif sum(r['value'] for r in ref) < parameters['anomaly_min_total']: item['reason'] = '此前指标样本不足'
        else:
            middle = median(r['value'] for r in ref)
            mad = median(abs(r['value']-middle) for r in ref)
            margin = max(parameters['anomaly_multiplier']*parameters['anomaly_mad_scale']*mad,
                         parameters['anomaly_multiplier']*math.sqrt(max(middle, 1)), parameters['anomaly_min_margin'])
            lower, upper = max(0, middle-margin), middle+margin
            item.update(baseline=middle, lower=lower, upper=upper, status='high' if row['value']>upper else 'low' if row['value']<lower else 'normal',
                        reason='与此前完整窗口的稳健阈值比较；不是因果认定或实时预警')
        output.append(item)
    return output
