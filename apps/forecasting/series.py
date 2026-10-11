"""Complete local-calendar periods over the shared S05 accident cohort."""
import calendar
from datetime import date, timedelta
from apps.analytics.query import scope_where, rows, coverage_intervals


def period_start(day, frequency):
    return day.replace(day=1) if frequency == 'month' else day - timedelta(days=day.weekday())


def next_start(day, frequency):
    if frequency == 'week':
        return day + timedelta(days=7)
    return date(day.year + day.month // 12, day.month % 12 + 1, 1)


def period_end(day, frequency):
    return next_start(day, frequency) - timedelta(days=1)


def series(d):
    where, args = scope_where(d)
    expression = "date_trunc('month',c.local_date)::date" if d['frequency'] == 'month' else "date_trunc('week',c.local_date)::date"
    aggregates = rows(f"SELECT {expression} period_start,count(*) n,sum(c.persons_injured) injured,sum(c.persons_killed) killed,count(*) FILTER(WHERE c.persons_injured IS NULL) missing_injured,count(*) FILTER(WHERE c.persons_killed IS NULL) missing_killed,count(*) FILTER(WHERE c.borough_id IS NULL) missing_borough FROM collisions_collision c WHERE {where} GROUP BY 1 ORDER BY 1", args)
    by_start = {r['period_start']: r for r in aggregates}
    intervals = coverage_intervals('crashes')
    output = []
    start = period_start(d['date_from'], d['frequency'])
    while start <= d['date_to']:
        end = period_end(start, d['frequency'])
        lo, hi = max(start, d['date_from']), min(end, d['date_to'])
        row = by_start.get(start, {'n': 0, 'injured': 0, 'killed': 0, 'missing_injured': 0, 'missing_killed': 0, 'missing_borough': 0})
        full = lo == start and hi == end
        days = (hi-lo).days+1
        covered = sum(any(a <= lo+timedelta(days=i) <= b for a, b in intervals) for i in range(days))
        missing = row.get('missing_'+d['metric'], 0)
        reasons = []
        if not full: reasons.append('首尾日历周期不完整')
        if covered < days: reasons.append('来源日期覆盖不足')
        if missing: reasons.append(f'{missing} 起所选伤亡字段缺失')
        reported = row['n'] if d['metric'] == 'crashes' else row[d['metric']]
        output.append({'start': start, 'end': end, 'observed_from': lo, 'observed_to': hi, 'label': start.strftime('%Y-%m') if d['frequency'] == 'month' else start.isoformat(),
                       'value': reported if not reasons else None, 'reported': reported, 'n': row['n'], 'missing': missing, 'missing_borough': row['missing_borough'],
                       'calendar_complete': full, 'covered_days': covered, 'days': days, 'reason': '；'.join(reasons)})
        start = next_start(start, d['frequency'])
    return output


def continuous_tail(history):
    complete_calendar = [r for r in history if r['calendar_complete']]
    tail = []
    for row in reversed(complete_calendar):
        if row['value'] is None:
            break
        tail.append(row)
    return list(reversed(tail))
