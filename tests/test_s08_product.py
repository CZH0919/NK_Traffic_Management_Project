"""Product interactions preserve the existing cohorts, permissions and frozen results."""
from copy import deepcopy
from datetime import date
from unittest.mock import patch
from django.contrib.auth.models import User, Group, AnonymousUser
from django.db.models import Exists, OuterRef, Q
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from apps.core.help_content import available_topics, search_topics
from apps.core.templatetags.ux_values import completeness_label
from apps.collisions.models import Collision, Vehicle
from apps.collisions.queries import published_records
from apps.ingestion.models import Snapshot, StagedRecord, QualityIssue
from apps.accounts.maintenance import AccountForm
from apps.analytics.forms import make_form as analysis_form
from apps.forecasting.forms import make_form as forecast_form
from apps.core.models import SavedQuery
from apps.core.navigation import safe_list_url
from apps.reports.forms import prepare_forms
from apps.reports.models import Report, AIBrief
from apps.reports import services, jobs, ai
from tests import test_s06


class ProductPresentationTests(SimpleTestCase):
    def test_completeness_never_rounds_a_missing_record_to_fully_complete(self):
        self.assertEqual(completeness_label({'n':24642,'missing_totals':0}),'100%（全部完整）')
        value=completeness_label({'n':24642,'missing_totals':1})
        self.assertIn('接近',value);self.assertIn('1 起不完整',value)
        self.assertIn('0.00%',completeness_label({'n':3,'missing_totals':3}))
        self.assertIn('没有',completeness_label({'n':0,'missing_totals':0}))

    def test_help_search_links_to_actual_explanation_and_hides_admin_topic(self):
        topics=available_topics(AnonymousUser())
        self.assertNotIn('maintenance',[t['slug'] for t in topics])
        result=search_topics(topics,'MAE')
        self.assertEqual(result[0]['topic']['slug'],'forecast')
        self.assertEqual(result[0]['anchor'],'fields')
        self.assertIn('平均绝对误差',result[0]['excerpt'])
        self.assertTrue(search_topics(topics,'比较流量'))
        self.assertEqual(search_topics(topics,'找不到的随机术语'),[])


@override_settings(ALLOWED_HOSTS=['testserver','127.0.0.1','localhost'],AI_CONFIG_FILE='')
class ProductDatabaseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_s06.ForecastDatabaseTests.setUpTestData.__func__(cls)

    def setUp(self):self.client.force_login(self.analyst)

    def test_guide_is_split_and_role_sensitive(self):
        index=self.client.get('/help/')
        self.assertEqual(index.status_code,200)
        self.assertNotContains(index,'一个小时通常包含四个')
        self.assertContains(self.client.get('/help/traffic/'),'一个小时通常包含四个')
        self.assertEqual(self.client.get('/help/maintenance/').status_code,404)
        self.assertEqual(self.client.get('/help/not-a-topic/').status_code,404)
        self.client.logout()
        self.assertEqual(self.client.get('/help/forecast/').status_code,200)

    def test_metric_selection_and_conditions_have_same_canonical_value(self):
        for dimension,effective in [('vehicle_type','records'),('person_type','records'),('injury','records'),('factor','crashes'),('month','injured')]:
            with self.subTest(dimension=dimension):
                form=analysis_form('statistics',{'dimension':dimension,'metric':'injured','date_from':'2025-01-01','date_to':'2025-12-31'})
                self.assertTrue(form.is_valid(),form.errors)
                self.assertEqual(form['metric'].value(),effective)
                self.assertEqual(form.conditions()['metric'],effective)
                response=self.client.get('/analytics/',{'dimension':dimension,'metric':'injured'})
                self.assertEqual(response.status_code,200)
                self.assertIn('metric='+effective,response.context['conditions'])
        invalid=analysis_form('statistics',{'dimension':'vehicle_type','metric':'unknown'})
        self.assertFalse(invalid.is_valid())

    def test_month_week_and_target_required_fields_follow_context(self):
        monthly=forecast_form({'frequency':'month','horizon':'13'})
        self.assertEqual(monthly.fields['horizon'].widget.attrs['max'],12)
        self.assertFalse(monthly.is_valid());self.assertIn('horizon',monthly.errors)
        weekly=forecast_form({'frequency':'week','horizon':'26'})
        self.assertTrue(weekly.is_valid(),weekly.errors)
        self.assertEqual(weekly.fields['horizon'].widget.attrs['max'],26)
        borough=forecast_form({'level':'borough','borough':''})
        self.assertTrue(borough.fields['borough'].required);self.assertFalse(borough.is_valid())
        road=forecast_form({'level':'road','road':self.road.pk})
        self.assertTrue(road.fields['road'].required);self.assertTrue(road.is_valid(),road.errors)

    def test_new_account_defaults_and_edit_password_rules(self):
        form=AccountForm()
        self.assertEqual(form['role'].value(),'分析用户')
        self.assertTrue(form.fields['password'].required)
        self.assertTrue(form.fields['password_confirm'].required)
        edited=AccountForm(account=self.editor)
        self.assertEqual(edited['role'].value(),'数据维护人员')
        self.assertFalse(edited.fields['password'].required)

    def test_exact_publication_filter_matches_original_including_manual_and_null_raw(self):
        private=Snapshot.objects.create(source=self.snapshot.source,version='unpublished',captured_at=timezone.now(),relative_path='test-only',status='quarantined')
        raw=StagedRecord.objects.create(snapshot=private,row_number=1,source_key='private',payload={})
        Collision.objects.create(source=self.snapshot.source,source_record_id='private',raw_record=raw,local_date=date(2025,1,1))
        Collision.objects.create(origin='manual',local_date=date(2025,1,1))
        for origin in ('official','all','manual'):
            base=Collision.objects.all() if origin=='all' else Collision.objects.filter(origin=origin)
            pending=StagedRecord.objects.filter(pk=OuterRef('raw_record_id')).exclude(snapshot__status='published')
            old=base.filter(~Exists(pending)) if origin=='official' else base.filter(~Q(origin='official')|~Exists(pending)) if origin=='all' else base
            self.assertEqual(list(old.order_by('pk').values_list('pk',flat=True)),list(published_records(base,origin).order_by('pk').values_list('pk',flat=True)))
        self.assertFalse(published_records(Collision.objects.all(),'all').filter(source_record_id='private').exists())

    def test_entity_picker_respects_parent_and_unpublished_records(self):
        first,second=[Collision.objects.get(pk=x) for x in self.events[:2]]
        own=Vehicle.objects.create(origin='demo',collision=first,vehicle_type='Sedan')
        other=Vehicle.objects.create(origin='demo',collision=second,vehicle_type='Sedan')
        data=self.client.get('/objects/search/',{'kind':'vehicle','collision_id':first.pk}).json()
        self.assertIn(own.pk,[x['id'] for x in data['items']]);self.assertNotIn(other.pk,[x['id'] for x in data['items']])
        result=self.client.get('/objects/search/',{'kind':'collision','id':first.pk})
        self.assertEqual(result.json()['items'][0]['id'],first.pk)
        self.assertEqual(self.client.get('/objects/search/?kind=collision&id=-1').status_code,400)
        self.client.logout();self.assertEqual(self.client.get('/objects/search/?kind=vehicle&q=Sedan').status_code,302)

    def test_report_preview_is_unbound_but_submission_still_requires_source(self):
        for kind in ('flow','risk','forecast'):
            response=self.client.get('/reports/new/',{'kind':kind,'title':'保留标题'})
            self.assertEqual(response.status_code,200)
            self.assertFalse(response.context['form'].is_bound)
            self.assertEqual(response.context['form']['title'].value(),'保留标题')
            self.assertNotContains(response,'form-error-summary')
            self.assertEqual(self.client.post('/reports/new/',{'kind':kind,'title':'保留标题'}).status_code,400)
        invalid=self.client.post('/reports/new/',{'kind':'statistics','title':'保留标题','date_from':'bad-date','date_to':'2025-12-31'})
        self.assertContains(invalid,'href="#id_date_from"',status_code=400)
        self.assertEqual(invalid.context['form']['title'].value(),'保留标题')
        self.assertEqual(Report.objects.count(),0)

    def test_saved_query_summary_and_duplicate_rename_retain_input(self):
        saved=SavedQuery.objects.create(owner=self.analyst,name='原名',kind='crashes',conditions={'date_from':'2025-01-01','borough':'1'})
        SavedQuery.objects.create(owner=self.analyst,name='已用名称',kind='crashes',conditions={})
        page=self.client.get('/queries/')
        self.assertContains(page,'2025-01-01');self.assertContains(page,'曼哈顿')
        response=self.client.post(f'/queries/{saved.pk}/update/',{'action':'rename','name':'已用名称'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.context['rename_value'],'已用名称')
        saved.refresh_from_db();self.assertEqual(saved.name,'原名')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/queries/{saved.pk}/load/').status_code,404)

    def test_file_scope_filters_list_totals_and_return_path(self):
        issue=QualityIssue.objects.create(snapshot=self.snapshot,code='missing_borough',severity='warning')
        response=self.client.get('/quality/',{'snapshot':self.snapshot.pk})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['totals']['open'],1)
        self.assertEqual([x.pk for x in response.context['page_obj']],[issue.pk])
        self.assertIn('snapshot=',safe_list_url(f'/quality/?snapshot={self.snapshot.pk}&page=2'))
        self.assertEqual(self.client.get('/quality/?snapshot=invalid').status_code,400)

    def test_quality_evidence_preserves_zero_and_does_not_write(self):
        raw=Collision.objects.get(pk=self.events[0]).raw_record
        from types import SimpleNamespace
        from apps.ingestion.quality_evidence import evidence
        item=SimpleNamespace(record=SimpleNamespace(payload={'number_of_persons_injured':0}),code='missing_casualty_count',details={'fields':['persons_injured']})
        result=evidence(item,None)
        self.assertEqual(next(x for x in result['rows'] if x['label']=='全部人员受伤')['source'],'0')
        issue=QualityIssue.objects.create(snapshot=self.snapshot,record=raw,code='reported_borough_geometry_mismatch',severity='warning')
        before=deepcopy(issue.details)
        response=self.client.get(f'/quality/{issue.pk}/',{'check_position':'1'})
        self.assertEqual(response.status_code,200)
        self.assertIsNotNone(response.context['evidence']['position'])
        issue.refresh_from_db();self.assertEqual(issue.details,before);self.assertEqual(issue.status,'open')

    def test_headers_are_source_specific_and_require_maintenance_permission(self):
        self.assertEqual(self.client.get('/imports/formats/traffic/headers.csv').status_code,403)
        self.client.force_login(self.editor)
        response=self.client.get('/imports/formats/traffic/headers.csv')
        self.assertEqual(response.status_code,200)
        self.assertIn('source_row_id',response.content.decode('utf-8-sig'))
        self.assertEqual(len(response.content.decode('utf-8-sig').splitlines()),1)
        self.assertEqual(self.client.get('/imports/formats/unknown/headers.csv').status_code,404)

    def test_new_brief_excerpt_note_only_when_actual_forecast_truncated(self):
        from apps.forecasting import services as forecasts,jobs as forecast_jobs
        for horizon in (3,8):
            run=forecasts.enqueue(self.analyst,forecast_form({'horizon':str(horizon)}))
            forecast_jobs.claim();self.assertTrue(forecast_jobs.execute(run.pk));run.refresh_from_db()
            item=self.client.get('/objects/search/',{'kind':'forecast_run','id':str(run.pk)}).json()['items'][0]
            self.assertIn(f'预测 {horizon} 期',item['detail'])
            self.assertIn(run.get_metric_display(),item['detail'])
            main,source=prepare_forms(self.analyst,{'kind':'forecast','forecast_run':str(run.pk)})
            report=services.enqueue(self.analyst,main,source);jobs.claim('report');self.assertTrue(jobs.execute('report',report.pk));report.refresh_from_db()
            before=deepcopy(report.payload)
            summary=ai.build_input(report)
            notes=' '.join(x['text'] for x in summary['limitations'])
            self.assertEqual('前 6 个预测周期' in notes,horizon>6)
            self.assertNotIn({'label':'规则编号','value':str(run.rule_id)},report.payload['scope'])
            self.client.get(f'/reports/{report.pk}/');report.refresh_from_db();self.assertEqual(report.payload,before)
        self.assertEqual(AIBrief.objects.count(),0)
