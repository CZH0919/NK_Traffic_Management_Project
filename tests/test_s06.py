from copy import deepcopy
from datetime import date, datetime, time, timezone as utc
from unittest.mock import patch
from django.contrib.auth.models import User, Group
from django.core import signing
from django.db import connection, transaction, DatabaseError
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from apps.analytics.models import RuleVersion, AnalysisTarget
from apps.analytics.forms import make_form as risk_form
from apps.core.models import SavedQuery
from apps.collisions.models import Collision
from apps.collisions.services import save_entity
from apps.ingestion.models import DataSource, Snapshot, StagedRecord
from apps.network.models import NetworkVersion, RoadSegment, Junction, SpatialMatch
from apps.traffic.analysis import NY
from apps.forecasting.forms import make_form
from apps.forecasting.series import series, continuous_tail, next_start, period_start, period_end
from apps.forecasting.methods import predict, evaluate, anomalies, RULE_PARAMETERS
from apps.forecasting.services import enqueue, is_stale
from apps.forecasting.jobs import claim, execute, recover
from apps.forecasting.models import ForecastRun, ForecastPoint, BacktestFold, AnomalyResult


def history(values):
    start = date(2021, 1, 1)
    result = []
    for value in values:
        result.append({'start': start, 'end': period_end(start, 'month'), 'value': value, 'calendar_complete': True, 'reason': ''})
        start = next_start(start, 'month')
    return result


class ForecastMathTests(SimpleTestCase):
    def test_hand_calculated_methods(self):
        values = [1, 2, 3]
        self.assertEqual(predict(values, {'method': 'naive'}, 3, 2), [3, 3, 3])
        self.assertEqual(predict(values, {'method': 'ma', 'window': 2}, 2, 2), [2.5, 2.5])
        self.assertEqual(predict(values, {'method': 'ses', 'alpha': .5}, 2, 2), [2.25, 2.25])
        self.assertEqual(predict(values, {'method': 'seasonal'}, 3, 2), [2, 3, 2])

    def test_evaluation_values_do_not_select_parameters(self):
        a = history([100+i % 7 for i in range(60)])
        b = deepcopy(a)
        for row in b[48:]: row['value'] *= 5
        sa, fa, _ = evaluate(a, 'month', 3, 'auto', RULE_PARAMETERS)
        sb, fb, _ = evaluate(b, 'month', 3, 'auto', RULE_PARAMETERS)
        self.assertEqual(sa['selected'], sb['selected'])
        self.assertEqual(sa['candidates'], sb['candidates'])
        # At the first evaluation origin, even all future changes must be invisible.
        self.assertEqual([r['predicted'] for r in fa if r['origin_date'] == a[47]['end']], [r['predicted'] for r in fb if r['origin_date'] == b[47]['end']])

    def test_every_fold_origin_precedes_target_and_same_samples(self):
        _, folds, _ = evaluate(history([100+i % 9 for i in range(60)]), 'month', 5, 'auto', RULE_PARAMETERS)
        self.assertTrue(all(r['train_end'] <= r['origin_date'] < r['period_start'] for r in folds))
        groups = {}
        for row in folds:
            if row['phase'] == 'evaluation': groups.setdefault(row['method'], set()).add((row['origin_date'], row['step'], row['period_start']))
        self.assertEqual(len(groups), 4)
        self.assertTrue(all(value == next(iter(groups.values())) for value in groups.values()))

    def test_historical_errors_construct_band_only_with_sample_support(self):
        _, folds, points = evaluate(history([100+i % 13 for i in range(60)]), 'month', 12, 'naive', RULE_PARAMETERS)
        errors = sorted(r['absolute_error'] for r in folds if r['phase'] == 'evaluation' and r['method'] == 'naive' and r['step'] == 1)
        self.assertEqual(points[0]['upper']-points[0]['estimate'], errors[9])  # ceil(.8*12)-1
        self.assertEqual(points[0]['interval_samples'], 12)
        self.assertIsNone(points[-1]['upper'])
        self.assertEqual(points[-1]['interval_samples'], 1)

    def test_inferior_method_is_not_switched_after_evaluation(self):
        h = history([100]*48 + [200+i*20 for i in range(12)])
        summary, _, _ = evaluate(h, 'month', 1, 'ma', RULE_PARAMETERS)
        self.assertEqual(summary['selected']['method'], 'ma')
        self.assertFalse(summary['beats_naive'])
        self.assertGreater(summary['selected_mae'], summary['naive_mae'])

    def test_zero_and_sparse_history_remain_unforecasted(self):
        for values in ([0]*60, [0]*59+[1000], [10]*20):
            summary, folds, points = evaluate(history(values), 'month', 3, 'auto', RULE_PARAMETERS)
            self.assertFalse(summary['eligible']); self.assertEqual(folds, []); self.assertEqual(points, [])

    def test_calendar_boundaries_leap_year_and_monday(self):
        self.assertEqual(period_end(date(2024, 2, 1), 'month'), date(2024, 2, 29))
        self.assertEqual(period_start(date(2025, 1, 1), 'week'), date(2024, 12, 30))
        self.assertEqual(period_end(date(2024, 12, 30), 'week'), date(2025, 1, 5))

    def test_gaps_are_not_removed_to_join_disjoint_histories(self):
        h = history([100]*60); h[55]['value'] = None
        self.assertEqual(len(continuous_tail(h)), 4)
        h[-1]['value'] = None
        self.assertEqual(continuous_tail(h), [])

    def test_anomaly_uses_previous_window_not_current_or_future(self):
        h = history([10]*12+[100]+[10]*12)
        result = anomalies(h, RULE_PARAMETERS)
        self.assertEqual(result[12]['status'], 'high'); self.assertEqual(result[12]['baseline'], 10)
        altered = deepcopy(h); altered[13]['value'] = 10000
        self.assertEqual(result[:13], anomalies(altered, RULE_PARAMETERS)[:13])
        h[8]['value'] = None
        self.assertEqual(anomalies(h, RULE_PARAMETERS)[12]['status'], 'insufficient')

    def test_anomaly_constant_zero_and_no_future_rows(self):
        result = anomalies(history([0]*24), RULE_PARAMETERS)
        self.assertTrue(all(r['status'] == 'insufficient' for r in result))
        self.assertTrue(all(r['reference_to'] is None or r['reference_to'] < r['period_start'] for r in result))


@override_settings(ALLOWED_HOSTS=['testserver', '127.0.0.1', 'localhost'])
class ForecastDatabaseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.analyst = User.objects.create_user('forecast-analyst'); cls.analyst.groups.add(Group.objects.get(name='分析用户'))
        cls.editor = User.objects.create_user('forecast-editor'); cls.editor.groups.add(Group.objects.get(name='数据维护人员'))
        cls.other = User.objects.create_user('forecast-other'); cls.other.groups.add(Group.objects.get(name='分析用户'))
        source = DataSource.objects.get(kind='crashes')
        cls.snapshot = Snapshot.objects.create(source=source, version='forecast test', captured_at=timezone.now(), relative_path='test-only', status='published',
            period_start=date(2021,1,1), period_end=date(2025,12,31), manifest={'quality_checked':True,'expected_rows':1,'row_count':1,'count_after':1})
        road_snapshot = Snapshot.objects.create(source=DataSource.objects.get(kind='roads'), version='forecast roads', captured_at=timezone.now(), relative_path='test-only',status='published')
        cls.network = NetworkVersion.objects.create(snapshot=road_snapshot, label='Forecast test network', native_crs='EPSG:4326')
        cls.road = RoadSegment.objects.create(network=cls.network, source_feature_id='fr', name='FORECAST ROAD', is_canonical=True, match_eligible=True, geom='SRID=4326;MULTILINESTRING((-73.98 40.70,-73.98 40.71))')
        cls.junction = Junction.objects.create(network=cls.network, source_node_id='fn', name='FORECAST JUNCTION', is_intersection=True, geom='SRID=4326;POINT(-73.98 40.705)')
        n = 0; cls.events = []
        for year in range(2021,2026):
            for month in range(1,13):
                for j in range(10+month % 5):
                    n += 1
                    raw = StagedRecord.objects.create(snapshot=cls.snapshot, row_number=n, source_key=str(n), payload={})
                    day = date(year,month,15)
                    event = Collision.objects.create(source=source, source_record_id=str(n), raw_record=raw, local_date=day, local_time=time(12),
                        occurred_at=datetime.combine(day,time(12),NY).astimezone(utc.utc), time_status='unique', borough_id=1,
                        persons_injured=2, persons_killed=0, geom='SRID=4326;POINT(-73.98 40.705)')
                    cls.events.append(event.pk)
                    SpatialMatch.objects.create(collision=event, network=cls.network, road=cls.road if j%2==0 else None,
                        junction=cls.junction if j%2 else None, status='matched', method='test', rule_version='test', input_revision=1)

    def setUp(self): self.client.force_login(self.analyst)

    def form(self, **kw):
        form = make_form({'date_from':'2021-01-01','date_to':'2025-12-31','network':str(self.network.pk),**kw})
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def run_forecast(self, **kw):
        run = enqueue(self.analyst, self.form(**kw))
        self.assertEqual(claim(), run.pk)
        self.assertTrue(execute(run.pk))
        run.refresh_from_db(); self.assertEqual(run.status, 'succeeded', run.error_code)
        return run

    def test_full_pipeline_persists_forecast_validation_and_anomalies(self):
        run = self.run_forecast()
        self.assertEqual(len(run.history), 60); self.assertEqual(run.points.count(), 3)
        self.assertEqual(run.anomalies.count(), 60)
        self.assertEqual(run.summary['validation_from'], '2024-01-01'); self.assertEqual(run.summary['evaluation_from'], '2025-01-01')
        self.assertEqual(run.points.first().period_start, date(2026,1,1)); self.assertEqual(run.training_end, date(2025,12,31))
        self.assertFalse(is_stale(run))

    def test_series_agrees_with_direct_sql(self):
        values = series(self.form().cleaned_data)
        with connection.cursor() as c:
            c.execute('SELECT count(*),sum(persons_injured) FROM collisions_collision WHERE is_active')
            n, injured = c.fetchone()
        self.assertEqual(sum(r['value'] for r in values), n)
        self.assertEqual(sum(r['value'] for r in series(self.form(metric='injured').cleaned_data)), injured)

    def test_missing_deaths_are_not_zero_or_forward_filled(self):
        Collision.objects.filter(pk=self.events[-1]).update(persons_killed=None)
        run = self.run_forecast(metric='killed')
        self.assertIsNone(run.history[-1]['value']); self.assertEqual(run.history[-1]['reported'],0)
        self.assertFalse(run.summary['eligible']); self.assertEqual(run.points.count(),0)
        self.assertIn('末端',run.summary['reasons'][0])

    def test_empty_borough_has_real_zeros_but_no_forecast(self):
        run = self.run_forecast(level='borough',borough='2')
        self.assertTrue(all(r['value']==0 for r in run.history))
        self.assertFalse(run.summary['eligible'])

    def test_partial_weeks_retained_but_training_ends_on_sunday(self):
        run = self.run_forecast(frequency='week')
        self.assertEqual(run.summary['excluded_partial_periods'],2)
        self.assertIsNone(run.history[0]['value']); self.assertIsNone(run.history[-1]['value'])
        self.assertEqual(run.training_end, date(2025,12,28))
        # Sparse synthetic weekly observations may legitimately fail the nonzero condition.

    def test_road_and_junction_use_distinct_matched_cohorts(self):
        for level, target in [('road',self.road),('junction',self.junction)]:
            run = self.run_forecast(level=level,**{level:str(target.pk)})
            expected = SpatialMatch.objects.filter(**{level:target}).count()
            self.assertEqual(sum(r['n'] for r in run.history),expected)
            self.assertEqual(run.target.scope,level)

    def test_unpublished_and_demo_records_excluded(self):
        Collision.objects.create(origin='demo',local_date=date(2025,12,10),persons_injured=100)
        self.snapshot.status='quarantined'; self.snapshot.save()
        values=series(self.form().cleaned_data)
        self.assertTrue(all(r['n']==0 and r['value'] is None for r in values))

    def test_future_cutoff_does_not_invent_observed_data(self):
        run=self.run_forecast(date_to='2026-01-31')
        self.assertFalse(run.summary['eligible']); self.assertIsNone(run.history[-1]['value'])
        self.assertEqual(run.summary['data_available_through'],'2025-12-31')

    def test_recompute_preserves_old_result_and_reproduces_numbers(self):
        a=self.run_forecast(); b=self.run_forecast()
        self.assertNotEqual(a.pk,b.pk)
        fields=('step','estimate','lower','upper')
        self.assertEqual(list(a.points.values_list(*fields)),list(b.points.values_list(*fields)))

    def test_rule_domains_are_separate(self):
        form=risk_form('risk',{})
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.cleaned_data['rule'].code,'historical-intensity')
        self.assertFalse(make_form({**self.form().conditions(),'rule':form.cleaned_data['rule'].pk}).is_valid())

    def test_evidence_and_completed_run_immutable(self):
        run=self.run_forecast()
        targets=[('forecasting_forecastrun','summary',"'{}'::jsonb",run.pk),('forecasting_forecastpoint','estimate','999',run.points.first().pk),
                 ('forecasting_backtestfold','predicted','999',run.folds.first().pk),('forecasting_anomalyresult','observed','999',run.anomalies.first().pk)]
        for table,col,value,pk in targets:
            with self.assertRaises(DatabaseError),transaction.atomic(),connection.cursor() as c:
                c.execute(f'UPDATE {table} SET {col}={value} WHERE id=%s',[pk])
        with self.assertRaises(DatabaseError),transaction.atomic():
            ForecastPoint.objects.create(run=run,step=4,period_start=date(2026,4,1),period_end=date(2026,4,30),estimate=1)

    def test_stale_support_disabled_after_fact_revision(self):
        run=self.run_forecast()
        save_entity(kind='crashes',pk=self.events[0],actor=self.editor,expected_revision=1,data={'review_note':'核验预测历史版本。'},reason='隔离测试核对预测版本更新边界。')
        self.assertTrue(is_stale(run))
        self.assertEqual(self.client.get(f'/forecast/runs/{run.pk}/periods/0/').status_code,409)
        self.assertContains(self.client.get(f'/forecast/runs/{run.pk}/'),'当前明细下钻已暂停')

    def test_saved_forecast_roundtrip_and_owner_isolation(self):
        f=self.form(level='borough',borough='1',horizon='4',method='ses')
        token=signing.dumps({'owner':self.analyst.pk,'kind':'forecast','conditions':f.conditions()},salt='saved-query-v1')
        self.assertEqual(self.client.post('/queries/save/forecast/',{'name':'预测条件','conditions':token}).status_code,302)
        saved=SavedQuery.objects.get(name='预测条件'); response=self.client.get(f'/queries/{saved.pk}/load/')
        self.assertIn('horizon=4',response.url); self.assertIn('method=ses',response.url)
        self.client.force_login(self.other); self.assertEqual(self.client.get(f'/queries/{saved.pk}/load/').status_code,404)

    def test_failed_job_has_no_partial_evidence_and_recovery_is_explicit(self):
        run=enqueue(self.analyst,self.form()); claim()
        with patch('apps.forecasting.services.BacktestFold.objects.bulk_create',side_effect=RuntimeError('test rollback')):
            self.assertFalse(execute(run.pk))
        run.refresh_from_db(); self.assertEqual(run.status,'failed'); self.assertFalse(run.points.exists()); self.assertEqual(run.history,[])
        run=enqueue(self.analyst,self.form()); claim(); recover(); run.refresh_from_db()
        self.assertEqual(run.error_code,'interrupted')

    def test_permissions_rechecked_in_worker_and_pending_limit(self):
        from django.core.exceptions import ValidationError
        run=enqueue(self.analyst,self.form())
        with self.assertRaises(ValidationError):enqueue(self.analyst,self.form())
        claim(); self.analyst.is_active=False; self.analyst.save()
        self.assertFalse(execute(run.pk)); self.assertFalse(run.points.exists())

    def test_form_rejects_invalid_target_dates_and_horizon(self):
        for data in [{'frequency':'month','horizon':'13'},{'date_from':'2025-12-31','date_to':'2025-01-01'},
                     {'level':'city','borough':'1'},{'level':'borough'},{'level':'road','road':'99999999'},
                     {'level':'city','road':str(self.road.pk)}]:
            self.assertFalse(make_form(data).is_valid(),data)

    def test_routes_support_dates_and_auth(self):
        run=self.run_forecast()
        for path in ['/forecast/','/forecast/rules/',f'/forecast/runs/{run.pk}/',f'/forecast/runs/{run.pk}/?panel=backtest',
                     f'/forecast/runs/{run.pk}/?panel=history',f'/forecast/runs/{run.pk}/?panel=anomalies',f'/forecast/runs/{run.pk}/status/']:
            self.assertEqual(self.client.get(path).status_code,200,path)
        response=self.client.get(f'/forecast/runs/{run.pk}/periods/0/')
        self.assertIn('date_from=2021-01-01',response.url); self.assertIn('date_to=2021-01-31',response.url)
        self.assertEqual(self.client.get(f'/forecast/runs/{run.pk}/?step=99').status_code,400)
        self.assertEqual(self.client.get('/forecast/create/').status_code,405)
        self.client.logout(); self.assertEqual(self.client.get('/forecast/').status_code,302)
