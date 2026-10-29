"""Regression coverage for the approved S08B usability changes."""
from copy import deepcopy
from urllib.parse import urlencode, urlsplit, parse_qs
from unittest.mock import patch
from django.contrib.auth.models import User, Group
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from apps.core.navigation import safe_list_url
from apps.core.models import SavedQuery
from apps.network.models import RoadSegment, NetworkVersion
from apps.ingestion.models import Snapshot, QualityIssue
from apps.forecasting.models import ForecastRun
from apps.analytics.models import RiskRun
from apps.reports.models import AIBrief, AIRequest
from apps.reports import services, jobs, ai
from apps.reports.forms import prepare_forms
from apps.forecasting.forms import make_form
from tests import test_s06 as forecast_fixture


class ReturnURLTests(SimpleTestCase):
    def test_only_known_local_list_routes_and_parameters(self):
        for value in ('https://example.com/', '//example.com/', '/\\example.com/', '/accounts/logout/',
                      '/records/missing/', '/records/crashes/1/', '/records/crashes/#fragment', '\n/records/crashes/'):
            with self.subTest(value=value): self.assertEqual(safe_list_url(value, '/fallback/'), '/fallback/')
        self.assertEqual(safe_list_url('/records/crashes/?page=2&sort=id_asc&next=https://example.com'),
                         '/records/crashes/?page=2&sort=id_asc')


@override_settings(ALLOWED_HOSTS=['testserver','127.0.0.1','localhost'], AI_CONFIG_FILE='')
class UsabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        forecast_fixture.ForecastDatabaseTests.setUpTestData.__func__(cls)

    def setUp(self): self.client.force_login(self.analyst)

    def test_public_help_contains_no_user_or_admin_details(self):
        self.client.logout()
        response=self.client.get('/help/')
        self.assertContains(response,'系统使用指南')
        self.assertNotContains(response,'scripts/maintain_local.py')
        self.assertNotContains(response,self.analyst.username)
        self.assertNotContains(response,'本地账户凭据')
        self.assertEqual(self.client.get('/objects/search/?kind=road&q=FORECAST').status_code,302)

    def test_help_and_navigation_follow_role(self):
        response=self.client.get('/help/')
        self.assertNotContains(response,'scripts/maintain_local.py')
        self.assertContains(response,'查询与地图')
        admin=User.objects.create_user('ux-admin'); admin.groups.add(Group.objects.get(name='系统管理员'))
        self.client.force_login(admin)
        self.assertContains(self.client.get('/help/'),'/help/maintenance/')
        self.assertNotContains(self.client.get('/help/'),'scripts/maintain_local.py')
        self.assertContains(self.client.get('/help/maintenance/'),'scripts/maintain_local.py')

    def test_return_context_survives_detail_cancel_and_edit_save(self):
        self.client.force_login(self.editor)
        target='/records/crashes/?date_from=2025-01-01&date_to=2025-12-31&borough=1&sort=id_asc&page=2'
        pk=self.events[-1];query=urlencode({'return_to':target})
        detail=self.client.get(f'/records/crashes/{pk}/?{query}')
        self.assertEqual(detail.context['return_to'],target)
        self.assertContains(detail,'page%3D2')
        edit=self.client.get(f'/records/crashes/{pk}/edit/?{query}')
        self.assertContains(edit,'取消')
        self.assertContains(edit,'page%3D2')
        form=edit.context['form']
        data={k:v for k,v in form.initial.items() if v is not None}
        data['reason']='核对原记录，验证返回筛选上下文。'
        result=self.client.post(f'/records/crashes/{pk}/edit/?{query}',data)
        self.assertEqual(result.status_code,302)
        self.assertEqual(parse_qs(urlsplit(result.url).query)['return_to'],[target])

    def test_list_keeps_page_sort_and_expands_active_advanced_filters(self):
        response=self.client.get('/records/crashes/?sort=id_asc&page=2&severity=injured')
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.context['advanced_open'])
        self.assertIn('page%3D2',response.context['list_return_query'])
        self.assertContains(response,'官方记录')
        invalid=self.client.get('/records/crashes/?origin=invalid')
        self.assertEqual(invalid.status_code,400)
        self.assertTrue(invalid.context['advanced_open'])

    def test_quality_unknown_code_and_return_remain_supported(self):
        response=self.client.get('/quality/?code=future_source_code')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'future_source_code')
        issue=QualityIssue.objects.create(snapshot=self.snapshot,code='missing_borough',severity='warning')
        response=self.client.get(reverse('quality_detail',args=[issue.pk]),{'return_to':'/quality/?code=missing_borough&page=2'})
        self.assertContains(response,'没有额外检测明细')
        self.assertEqual(response.context['return_to'],'/quality/?code=missing_borough&page=2')

    def test_picker_keeps_same_name_objects_distinct_and_bounds_results(self):
        for n in range(23):
            RoadSegment.objects.create(network=self.network,source_feature_id=f'ux{n}',source_segment_id=f'ux-{n}',name='FORECAST ROAD',
                is_canonical=True,match_eligible=True,geom=self.road.geom)
        params={'kind':'road','q':'FORECAST ROAD','network':self.network.pk}
        page1=self.client.get('/objects/search/',params).json()
        page2=self.client.get('/objects/search/',{**params,'page':2}).json()
        self.assertEqual(len(page1['items']),20);self.assertTrue(page1['has_more'])
        ids=[i['id'] for i in page1['items']+page2['items']]
        self.assertEqual(len(ids),24);self.assertEqual(len(set(ids)),24)
        self.assertIn('source_id',page1['items'][0])

    def test_picker_rejects_wrong_network_ineligible_and_bad_input(self):
        unpublished=Snapshot.objects.create(source=self.network.snapshot.source,version='ux private',status='quarantined',relative_path='test-only',captured_at=timezone.now())
        other=NetworkVersion.objects.create(snapshot=unpublished,label='unpublished',native_crs='EPSG:4326')
        self.assertEqual(self.client.get('/objects/search/',{'kind':'road','network':other.pk,'q':'FORECAST'}).status_code,400)
        RoadSegment.objects.filter(pk=self.road.pk).update(is_active=False)
        self.assertEqual(self.client.get('/objects/search/',{'kind':'road','id':self.road.pk,'network':self.network.pk}).json()['items'],[])
        for params in ({'kind':'invalid'},{'kind':'road','page':'0'},{'kind':'road','network':'x'},{'kind':'road','borough':'x'},{'kind':'road','q':'x'*201}):
            with self.subTest(params=params):self.assertEqual(self.client.get('/objects/search/',params).status_code,400)

    def test_forecast_saves_current_submitted_values_without_running(self):
        self.client.get('/forecast/?horizon=3')
        before=ForecastRun.objects.count()
        response=self.client.post('/forecast/create/',{'action':'save_query','query_name':'当前新条件','horizon':'6','metric':'injured',
                                                      'date_from':'2022-01-01','date_to':'2025-12-31','network':self.network.pk})
        self.assertEqual(response.status_code,302)
        saved=SavedQuery.objects.get(owner=self.analyst,name='当前新条件')
        self.assertEqual(saved.conditions['horizon'],'6');self.assertEqual(saved.conditions['metric'],'injured')
        self.assertEqual(saved.conditions['date_from'],'2022-01-01')
        self.assertEqual(saved.conditions['network'],str(self.network.pk))
        self.assertEqual(ForecastRun.objects.count(),before)
        response=self.client.get(reverse('load_query',args=[saved.pk]));self.assertIn('horizon=6',response.url)

    def test_risk_saves_current_form_with_owner_and_does_not_compute(self):
        response=self.client.post('/risk/create/',{'action':'save_query','query_name':'风险当前条件','level':'borough','borough':'1','network':self.network.pk})
        self.assertEqual(response.status_code,302)
        saved=SavedQuery.objects.get(owner=self.analyst,name='风险当前条件')
        self.assertEqual(saved.conditions['level'],'borough');self.assertEqual(saved.conditions['borough'],'1')
        self.assertEqual(RiskRun.objects.count(),0)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('load_query',args=[saved.pk])).status_code,404)

    def test_invalid_or_unnamed_save_preserves_errors_and_creates_nothing(self):
        for extra in ({'horizon':'40','query_name':'错误范围'},{'horizon':'3','query_name':''}):
            response=self.client.post('/forecast/create/',{'action':'save_query',**extra})
            self.assertEqual(response.status_code,400)
            self.assertTrue(response.context['form'].errors)
        self.assertEqual(SavedQuery.objects.count(),0);self.assertEqual(ForecastRun.objects.count(),0)

    def test_report_and_brief_gets_never_enqueue_and_keep_frozen_payload(self):
        main,source=prepare_forms(self.analyst,{'kind':'statistics','date_from':'2025-01-01','date_to':'2025-12-31'})
        report=services.enqueue(self.analyst,main,source);jobs.claim('report');self.assertTrue(jobs.execute('report',report.pk))
        report.refresh_from_db();before=deepcopy(report.payload)
        from tests.test_s07 import CONFIG, valid_output
        with patch('apps.reports.ai.load_config',return_value=CONFIG):
            brief,_=ai.enqueue(self.analyst,report);jobs.claim('brief');raw=valid_output(brief.input_summary)
            with patch('apps.reports.ai.call_model',return_value=(raw,ai.parse_output(raw,brief.input_summary),{},1)):
                jobs.execute('brief',brief.pk)
            brief.refresh_from_db(); self.assertEqual(brief.status,'succeeded')
        count=AIRequest.objects.count()
        with patch('apps.reports.ai.enqueue',side_effect=AssertionError('GET must not enqueue')):
            listing=self.client.get('/reports/');self.assertContains(listing,'已生成')
            detail=self.client.get(reverse('report_detail',args=[report.pk]));self.assertContains(detail,'查看已有简报')
            self.assertContains(detail,'数据版本');self.assertNotContains(detail,'修订水位')
            self.assertEqual(self.client.get(reverse('brief_detail',args=[brief.pk])).status_code,200)
        report.refresh_from_db();self.assertEqual(report.payload,before)
        self.assertEqual(AIRequest.objects.count(),count)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('report_detail',args=[report.pk])).status_code,404)

    def test_report_creation_from_completed_forecast_keeps_the_selected_source(self):
        from apps.forecasting import services as forecasts, jobs as forecast_jobs
        run=forecasts.enqueue(self.analyst,make_form({'date_from':'2021-01-01','date_to':'2025-12-31'}))
        forecast_jobs.claim(); forecast_jobs.execute(run.pk)
        response=self.client.get('/reports/new/',{'kind':'forecast','forecast_run':str(run.pk)})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'已选分析结果')
        self.assertTrue(response.context['selected_source'])
        self.assertEqual(response.context['form']['forecast_run'].value(),str(run.pk))
        result=self.client.post('/reports/new/',{'kind':'forecast','forecast_run':str(run.pk),'title':'保留选定来源'})
        self.assertEqual(result.status_code,302)
        from apps.reports.models import Report
        self.assertEqual(Report.objects.get(title='保留选定来源').forecast_run_id,run.pk)
