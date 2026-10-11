import json
from datetime import date,time,datetime,timezone as utc,timedelta
from unittest.mock import patch
from django.contrib.auth.models import User,Group
from django.core import signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import connection,transaction,DatabaseError
from django.test import TestCase,override_settings
from django.utils import timezone
from apps.core.models import SavedQuery,DataRevision,FieldChange
from apps.core.audit import record_event
from apps.ingestion.models import DataSource,Snapshot,StagedRecord
from apps.collisions.models import Collision,Vehicle,Person,Factor,CollisionFactor
from apps.collisions.services import save_entity
from apps.network.models import NetworkVersion,RoadSegment,Junction,SpatialMatch
from apps.analytics.forms import make_form,RuleForm
from apps.analytics.models import RuleVersion,RiskRun,RiskResult,AnalysisTarget,AggregateCache
from apps.analytics.query import summary,grouped,drill_sql,rows,compare_payload,cached_payload,coverage,data_version
from apps.analytics.risk import enqueue,compute_run,is_stale,percentile,score_components,create_rule,DEFAULT_PARAMETERS
from apps.analytics.jobs import claim,execute,recover
from apps.traffic.analysis import NY

REASON='依据隔离测试数据核验统计与修订一致性。'

@override_settings(ALLOWED_HOSTS=['testserver','127.0.0.1','localhost'])
class AnalyticsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.editor=User.objects.create_user('analysis-editor');cls.editor.groups.add(Group.objects.get(name='数据维护人员'))
        cls.analyst=User.objects.create_user('analysis-user');cls.analyst.groups.add(Group.objects.get(name='分析用户'))
        cls.other=User.objects.create_user('analysis-other');cls.other.groups.add(Group.objects.get(name='分析用户'))
        cls.sources={kind:DataSource.objects.get(kind=kind) for kind in ['crashes','vehicles','persons','roads']}
        manifest={'quality_checked':True,'expected_rows':10,'row_count':10,'count_after':10}
        cls.snaps={kind:Snapshot.objects.create(source=src,version='analysis test '+kind,captured_at=timezone.now(),relative_path='test-only',
            status='published',period_start=date(2024,1,1),period_end=date(2025,12,31),manifest=manifest) for kind,src in cls.sources.items()}
        cls.net=NetworkVersion.objects.create(snapshot=cls.snaps['roads'],label='Analysis test',native_crs='EPSG:4326')
        cls.road=RoadSegment.objects.create(network=cls.net,source_feature_id='a',source_segment_id='001',name='FIRST STREET',
            is_canonical=True,match_eligible=True,geom='SRID=4326;MULTILINESTRING((-73.98 40.70,-73.98 40.71))')
        cls.node=Junction.objects.create(network=cls.net,source_node_id='n',name='FIRST / SECOND',is_intersection=True,geom='SRID=4326;POINT(-73.98 40.705)')
    def setUp(self):self.client.force_login(self.analyst)
    def raw(self,kind):
        n=StagedRecord.objects.filter(snapshot=self.snaps[kind]).count()+1
        return StagedRecord.objects.create(snapshot=self.snaps[kind],row_number=n,source_key=str(n),payload={})
    def event(self,day=date(2025,1,6),injured=1,killed=0,borough=1,clock=time(12),**kw):
        raw=self.raw('crashes');stamp=datetime.combine(day,clock,NY).astimezone(utc.utc) if clock else None
        return Collision.objects.create(source=self.sources['crashes'],source_record_id=str(raw.row_number),raw_record=raw,local_date=day,
            local_time=clock,occurred_at=stamp,time_status='unique' if clock else 'missing',borough_id=borough,
            persons_injured=injured,persons_killed=killed,geom='SRID=4326;POINT(-73.98 40.705)',**kw)
    def child(self,kind,parent,**kw):
        raw=self.raw(kind);model=Vehicle if kind=='vehicles' else Person
        return model.objects.create(source=self.sources[kind],source_record_id=str(raw.row_number),raw_record=raw,collision=parent,**kw)
    def form(self,kind='statistics',**kwargs):
        f=make_form(kind,{'date_from':'2025-01-01','date_to':'2025-12-31','network':str(self.net.pk),**kwargs})
        self.assertTrue(f.is_valid(),f.errors);return f
    def risk(self,level='road',**kwargs):
        form=self.form('risk',level=level,**kwargs);run=enqueue(self.analyst,form)
        RiskRun.objects.filter(pk=run.pk).update(status='running');self.assertTrue(compute_run(run.pk));run.refresh_from_db();return run
    def matched_events(self,n=5,road=None):
        events=[]
        for _ in range(n):
            c=self.event();SpatialMatch.objects.create(collision=c,network=self.net,road=road or self.road,status='matched',method='test',rule_version='test',input_revision=c.revision);events.append(c)
        return events
    def test_one_to_many_does_not_multiply_event_or_injury(self):
        c=self.event(injured=2,killed=1)
        for _ in range(3):self.child('vehicles',c,vehicle_type='Sedan')
        for _ in range(4):self.child('persons',c,person_type='Occupant',injury='Injured')
        d=summary(self.form().cleaned_data)
        self.assertEqual((d['n'],d['injured'],d['killed'],d['vehicles'],d['persons']),(1,2,1,3,4))
        self.assertEqual(sum(r['n'] for r in d['severity']),1)
    def test_factors_count_unique_crashes_not_slots(self):
        c=self.event();factor=Factor.objects.create(name='Driver attention')
        for slot in (1,2):CollisionFactor.objects.create(collision=c,factor=factor,source_slot=slot)
        d=self.form(dimension='factor').cleaned_data;r=grouped(d);self.assertEqual(r[0]['n'],1)
        sql,args=drill_sql(d,'crashes','factor','Driver attention');self.assertEqual(rows('SELECT count(*) n'+sql,args)[0]['n'],1)
    def test_unknown_counts_and_mutually_exclusive_severity(self):
        self.event(injured=2,killed=1);self.event(injured=1);self.event(injured=0);self.event(injured=None,killed=None)
        d=summary(self.form().cleaned_data);self.assertEqual(d['n'],4);self.assertEqual(d['missing_totals'],1)
        self.assertEqual({x['key']:x['n'] for x in d['severity']},{'fatal':1,'injured':1,'none':1,'unknown':1})
        only=self.form(severity='unknown');unknown=summary(only.cleaned_data);self.assertIsNone(unknown['injured']);self.assertIsNone(unknown['killed'])
    def test_borough_unknown_stays_in_city_and_has_own_bucket(self):
        self.event(borough=None);self.event(borough=1)
        f=self.form(dimension='borough');r=grouped(f.cleaned_data);self.assertEqual(sum(x['n'] for x in r),2)
        self.assertEqual(next(x['n'] for x in r if x['key']=='unknown'),1)
        self.assertEqual(summary(self.form(borough='1').cleaned_data)['n'],1)
    def test_time_filters_and_date_drill_use_original_local_clock(self):
        self.event(day=date(2025,1,6),clock=time(23,45));self.event(day=date(2025,1,7),clock=time(0));self.event(clock=None)
        f=self.form(year='2025',month='1',weekday='0',hour='23',dimension='date')
        self.assertEqual(summary(f.cleaned_data)['n'],1)
        sql,args=drill_sql(f.cleaned_data,'crashes','date','2025-01-06');self.assertEqual(rows('SELECT count(*) n'+sql,args)[0]['n'],1)
        self.assertEqual(summary(self.form(hour='-1').cleaned_data)['n'],1)
    def test_no_coverage_is_not_a_zero_bucket(self):
        self.event();f=self.form(date_from='2025-12-01',date_to='2026-01-31',dimension='month')
        data=grouped(f.cleaned_data);self.assertEqual(data[0]['value'],0);self.assertIsNone(data[1]['value'])
        self.assertFalse(coverage(f.cleaned_data)['complete'])
    def test_local_upload_does_not_prove_complete_coverage(self):
        snapshot=self.snaps['crashes'];snapshot.manifest={**snapshot.manifest,'scope':'user_supplied_file_only'};snapshot.save()
        self.assertFalse(coverage(self.form().cleaned_data)['complete'])
    def test_unpublished_inactive_and_demo_do_not_enter_aggregates(self):
        c=self.event();c.is_active=False;c.save();Collision.objects.create(origin='demo',local_date=date(2025,1,6),persons_injured=99)
        self.assertEqual(summary(self.form().cleaned_data)['n'],0)
        c.is_active=True;c.save();s=self.snaps['crashes'];s.status='quarantined';s.save()
        self.assertEqual(summary(self.form().cleaned_data)['n'],0)
    def test_category_totals_and_drill_are_consistent(self):
        c=self.event();self.child('vehicles',c,vehicle_type='Sedan');self.child('vehicles',c,vehicle_type='');self.child('persons',c,person_type='Pedestrian',injury='Injured')
        for dimension,kind in [('vehicle_type','vehicles'),('person_type','persons'),('injury','persons')]:
            f=self.form(dimension=dimension)
            for row in grouped(f.cleaned_data):
                sql,args=drill_sql(f.cleaned_data,kind,dimension,row['key']);self.assertEqual(rows('SELECT count(*) n'+sql,args)[0]['n'],row['n'])
    def test_spatial_cohort_excludes_stale_and_inactive_targets(self):
        c=self.matched_events(1)[0];f=self.form(road=str(self.road.pk));self.assertEqual(summary(f.cleaned_data)['n'],1)
        c.revision=2;c.save();self.assertEqual(summary(f.cleaned_data)['n'],0)
        self.road.is_active=False;self.road.save();self.assertFalse(make_form('statistics',{'road':self.road.pk}).is_valid())
    def test_compare_zero_base_and_different_day_counts(self):
        self.event(day=date(2025,1,2),injured=0)
        d=self.form('comparison',date_from='2025-01-01',date_to='2025-01-01',compare_from='2025-01-02',compare_to='2025-01-03').cleaned_data
        data=compare_payload(d);row=data['comparisons'][0]
        self.assertEqual(row['difference'],1);self.assertIsNone(row['percent']);self.assertIsNone(row['daily_percent']);self.assertIsNone(data['series'][1]['a'])
    def test_compare_equal_coverage_same_dates_by_borough(self):
        self.event(borough=1);self.event(borough=2);self.event(borough=2)
        data=compare_payload(self.form('comparison',borough='1',compare_borough='2',compare_from='2025-01-01',compare_to='2025-12-31').cleaned_data)
        self.assertEqual(data['comparisons'][0]['percent'],100)
    def test_compare_unknown_deaths_does_not_return_percentage(self):
        self.event(killed=None);self.event(day=date(2024,1,6),killed=1)
        data=compare_payload(self.form('comparison',compare_from='2024-01-01',compare_to='2024-12-30').cleaned_data)
        death=next(r for r in data['comparisons'] if r['key']=='killed');self.assertIsNone(death['percent']);self.assertIsNone(death['daily_percent'])
    def test_cache_invalidates_on_business_revision_and_snapshot_change(self):
        c=self.event();form=self.form();a,hit=cached_payload('statistics',form);self.assertFalse(hit)
        _,hit=cached_payload('statistics',form);self.assertTrue(hit)
        save_entity(kind='crashes',pk=c.pk,actor=self.editor,expected_revision=1,data={'persons_injured':3},reason=REASON)
        b,hit=cached_payload('statistics',form);self.assertFalse(hit);self.assertEqual(b['summary']['injured'],3)
        snapshot=self.snaps['crashes'];snapshot.status='quarantined';snapshot.save()
        c,hit=cached_payload('statistics',form);self.assertFalse(hit);self.assertEqual(c['summary']['n'],0)
    def test_saved_statistics_roundtrip_and_owner_isolation(self):
        form=self.form(dimension='weekday',hour='12',borough='1')
        token=signing.dumps({'owner':self.analyst.pk,'kind':'statistics','conditions':form.conditions()},salt='saved-query-v1')
        response=self.client.post('/queries/save/statistics/',{'name':'统计条件','conditions':token});self.assertEqual(response.status_code,302)
        saved=SavedQuery.objects.get(name='统计条件');self.assertEqual(saved.schema_version,2)
        loaded=self.client.get(f'/queries/{saved.pk}/load/');self.assertIn('hour=12',loaded.url);self.assertIn('dimension=weekday',loaded.url)
        self.client.force_login(self.other);self.assertEqual(self.client.get(f'/queries/{saved.pk}/load/').status_code,404)
        self.assertEqual(self.client.post('/queries/save/statistics/',{'name':'伪造拥有者','conditions':token}).status_code,400)
    def test_risk_rule_math_zero_and_ties(self):
        self.assertEqual(percentile(0,[0,0]),0);self.assertEqual(percentile(2,[1,2,2,3]),50)
        score,components=score_components({'frequency':2,'severity':0,'recent':3},{'frequency':[1,2,2,3],'severity':[0,1],'recent':[1,2,3]},DEFAULT_PARAMETERS)
        self.assertAlmostEqual(score,sum(x['contribution'] for x in components.values()),places=3)
        self.assertEqual(components['severity']['contribution'],0)
    def test_risk_results_and_support_reconcile(self):
        self.matched_events(6);self.event(injured=100) # No spatial match must not inflate road scores.
        run=self.risk();result=run.results.get();self.assertEqual(result.counts['n'],6);self.assertIsNotNone(result.score)
        sql,args=drill_sql(self.form(road=str(self.road.pk)).cleaned_data,'crashes')
        self.assertEqual(rows('SELECT count(*) n'+sql,args)[0]['n'],result.counts['n'])
        self.assertFalse(is_stale(run));self.assertEqual(run.summary['reference_size'],1)
    def test_low_support_and_missing_data_do_not_get_grade(self):
        self.matched_events(4);run=self.risk();self.assertEqual(run.results.get().grade,'insufficient')
        self.assertIsNone(run.results.get().score)
    def test_completed_results_and_rules_are_immutable(self):
        self.matched_events(5);run=self.risk();result=run.results.get()
        for sql,pk in [('UPDATE analytics_riskresult SET score=100 WHERE id=%s',result.pk),('UPDATE analytics_ruleversion SET name=\'x\' WHERE id=%s',run.rule_id),('UPDATE analytics_riskrun SET status=\'failed\' WHERE id=%s',run.pk)]:
            with self.assertRaises(DatabaseError),transaction.atomic(),connection.cursor() as c:c.execute(sql,[pk])
    def test_stale_result_stops_current_support_drill(self):
        events=self.matched_events(5);run=self.risk();result=run.results.get()
        save_entity(kind='crashes',pk=events[0].pk,actor=self.editor,expected_revision=1,data={'review_note':'已复核来源记录。'},reason=REASON)
        self.assertTrue(is_stale(run));response=self.client.get(f'/risk/results/{result.pk}/');self.assertContains(response,'停止把当前明细当作旧分子的证据')
        self.assertNotContains(response,'查看同口径全部记录')
    def test_async_claim_failure_rollback_and_recovery(self):
        self.matched_events(5);run=enqueue(self.analyst,self.form('risk',level='road'));self.assertEqual(claim(),run.pk)
        with patch('apps.analytics.risk.RiskResult.objects.bulk_create',side_effect=RuntimeError('test-only rollback')):self.assertFalse(execute(run.pk))
        run.refresh_from_db();self.assertEqual(run.status,'failed');self.assertFalse(run.results.exists());self.assertEqual(AnalysisTarget.objects.count(),0)
        next_run=enqueue(self.analyst,self.form('risk',level='road'));claim();recover();next_run.refresh_from_db();self.assertEqual(next_run.error_code,'interrupted')
    def test_permissions_rechecked_when_worker_executes(self):
        self.matched_events(5);run=enqueue(self.analyst,self.form('risk',level='road'));claim()
        self.analyst.is_active=False;self.analyst.save();self.assertFalse(execute(run.pk));self.assertFalse(run.results.exists())
    def test_rule_creation_permissions_and_prior_version_preserved(self):
        data={'name':'测试规则','frequency':60,'severity':30,'recent':10,'death_weight':5,'recent_days':60,'min_support':5,'completeness':95,'reason':REASON}
        with self.assertRaises(PermissionDenied):create_rule(self.analyst,data)
        old=RuleVersion.objects.filter(code='historical-intensity').first();new=create_rule(self.editor,data);old.refresh_from_db();self.assertEqual(old.parameters['weights']['frequency'],50);self.assertEqual(new.version,old.version+1)
        self.assertFalse(RuleForm({**data,'frequency':99}).is_valid())
    def test_source_coverage_and_city_reference_require_evidence(self):
        self.matched_events(5);run=self.risk('city');self.assertEqual(run.results.get().grade,'insufficient')
        self.assertEqual(run.summary['reference_size'],1)
    def test_map_and_ranking_treat_search_wildcards_as_literal(self):
        road=RoadSegment.objects.create(network=self.net,source_feature_id='literal',name='A\\B_%',is_canonical=True,
            match_eligible=True,geom='SRID=4326;MULTILINESTRING((-73.98 40.70,-73.98 40.71))')
        self.matched_events(5,road=road);run=self.risk()
        for query,expected in [('A\\B_%',1),('A\\C',0)]:
            data=self.client.get(f'/risk/runs/{run.pk}/map/',{'q':query}).json()
            self.assertEqual(data['count'],expected)
            page=self.client.get(f'/risk/runs/{run.pk}/',{'q':query})
            self.assertEqual(page.context['page'].paginator.count,expected)
    def test_routes_invalid_parameters_and_map_cohort(self):
        self.matched_events(5);run=self.risk();result=run.results.get()
        paths=['/analytics/','/analytics/?dimension=vehicle_type','/analytics/compare/','/analytics/records/crashes/',
            '/risk/','/risk/rules/',f'/risk/runs/{run.pk}/',f'/risk/runs/{run.pk}/status/',f'/risk/results/{result.pk}/',f'/risk/runs/{run.pk}/map/','/analytics/map/data/']
        for path in paths:self.assertEqual(self.client.get(path).status_code,200,path)
        self.assertEqual(self.client.get('/analytics/?date_from=2025-12-31&date_to=2025-01-01').status_code,400)
        self.assertEqual(self.client.get('/analytics/map/data/?view_bbox=nan,40,-73,41').status_code,400)
        self.assertEqual(self.client.get('/analytics/records/crashes/?bucket_dimension=vehicle_type&bucket_key=v:Sedan').status_code,400)
        self.assertEqual(self.client.post('/risk/rules/new/',{}).status_code,403)
        self.client.logout();self.assertEqual(self.client.get('/analytics/').status_code,302)
