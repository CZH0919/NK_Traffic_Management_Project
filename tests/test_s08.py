import csv,io,json,tempfile,zipfile
from pathlib import Path
from unittest.mock import patch,MagicMock
from datetime import date
import psycopg
from django.contrib.auth.models import User,Group
from django.core.exceptions import PermissionDenied
from django.db import transaction,DatabaseError,OperationalError,connection
from django.test import SimpleTestCase,TestCase,override_settings
from apps.core import maintenance as ops
from apps.core.models import MaintenanceRun,AuditEvent,DataRevision
from apps.core.storage import storage_root
from apps.collisions.models import Collision,Person,Vehicle
from apps.collisions.queries import QueryForm,filtered_entities
from apps.collisions.services import save_entity
from apps.ingestion.models import Snapshot,StagedRecord,DataSource,ImportJob
from apps.reports import services as reports,jobs as report_jobs,exports
from apps.reports.forms import prepare_forms
from apps.analytics import risk
from apps.analytics.forms import make_form as analysis_form
from apps.analytics.query import cached_payload
from apps.analytics.models import RiskRun
from apps.forecasting import services as forecast,jobs as forecast_jobs
from apps.forecasting.forms import make_form as forecast_form
from tests import test_s06 as fixture


class MaintenanceFileTests(SimpleTestCase):
    def test_restore_targets_never_accept_business_test_or_connection_strings(self):
        for value in ['nk_traffic','test_nk_traffic','postgres','template0','restore_other','restore_nk_traffic_a;DROP','host=x','restore_nk_traffic_'+('x'*45)]:
            with self.subTest(value=value),self.assertRaises(ops.MaintenanceError):ops.target_name(value)
        self.assertEqual(ops.target_name('restore_nk_traffic_unit'),'restore_nk_traffic_unit')

    def test_file_bundle_roundtrip_is_separate_and_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'original';file=root/'data/raw/sample.csv';file.parent.mkdir(parents=True);file.write_bytes(b'x\n0\n')
            (root/'.env').write_text('not-in-backup',encoding='utf-8')
            entries=ops.file_inventory(root);self.assertEqual(len(entries),1)
            archive=Path(temp)/'files.zip';ops.write_files(root,archive,entries)
            dest=Path(temp)/'restored';ops.extract_files(archive,dest,entries)
            self.assertEqual((dest/'data/raw/sample.csv').read_bytes(),file.read_bytes());self.assertFalse((dest/'.env').exists())

    def test_archive_traversal_and_unlisted_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);sentinel=base/'keep.txt';sentinel.write_text('keep')
            z=base/'bad.zip'
            with zipfile.ZipFile(z,'w') as archive:archive.writestr('../keep.txt','overwrite')
            with self.assertRaises(ops.MaintenanceError):ops.extract_files(z,base/'dest',[{'path':'../keep.txt','bytes':9,'sha256':'bad'}])
            self.assertEqual(sentinel.read_text(),'keep')
            for name in ['C:/x','/x','data/raw/../x','data/raw/a:b','data\\raw\\x','Agent_Handoff/key.json']:
                with self.assertRaises(ops.MaintenanceError):ops.safe_member(name)

    def test_corrupted_file_is_not_accepted_as_restored(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);z=root/'bad.zip'
            with zipfile.ZipFile(z,'w') as archive:archive.writestr('data/raw/x.csv','bad')
            with self.assertRaises(ops.MaintenanceError):ops.extract_files(z,root/'restored',[{'path':'data/raw/x.csv','bytes':3,'sha256':'wrong'}])

    def test_isolated_storage_uses_distinct_export_directory(self):
        with tempfile.TemporaryDirectory() as temp,override_settings(TRAFFIC_STORAGE_ROOT=temp):
            from apps.reports.models import ExportJob
            job=ExportJob(format='csv')
            self.assertEqual(exports.path_for(job).parent,Path(temp)/'var/exports')
            self.assertEqual(storage_root(),Path(temp).resolve())


@override_settings(ALLOWED_HOSTS=['testserver','127.0.0.1'],AI_CONFIG_FILE='')
class MaintenanceDatabaseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin=User.objects.create_user('maintenance-admin');cls.admin.groups.add(Group.objects.get(name='系统管理员'))
        cls.analyst=User.objects.create_user('maintenance-reader');cls.analyst.groups.add(Group.objects.get(name='分析用户'))

    def test_only_admin_can_start_or_view_maintenance(self):
        with self.assertRaises(PermissionDenied):ops.begin(self.analyst,'backup','test_nk_traffic')
        self.assertEqual(MaintenanceRun.objects.count(),0)
        self.client.force_login(self.analyst);self.assertEqual(self.client.get('/system/maintenance/').status_code,403)
        self.client.force_login(self.admin);self.assertEqual(self.client.get('/system/maintenance/').status_code,200)

    def test_success_and_failure_leave_immutable_audit_evidence(self):
        for error in ['', 'dump_failed']:
            run=ops.begin(self.admin,'backup','test_nk_traffic');ops.finish(run,{'file_count':2},error)
            with self.assertRaises(DatabaseError),transaction.atomic():MaintenanceRun.objects.filter(pk=run.pk).update(target_label='changed')
            with self.assertRaises(DatabaseError),transaction.atomic():MaintenanceRun.objects.filter(pk=run.pk).delete()
            self.assertTrue(AuditEvent.objects.filter(object_id=str(run.pk),action='maintenance_failed' if error else 'maintenance_finished').exists())

    def test_maintenance_detail_does_not_render_internal_hash_or_private_paths(self):
        run=ops.begin(self.admin,'backup','test_nk_traffic');ops.finish(run,{'manifest_sha256':'private-fingerprint','file_count':3,'table_count':55,'elapsed_seconds':2})
        self.client.force_login(self.admin);response=self.client.get(f'/system/maintenance/{run.pk}/')
        self.assertContains(response,'归档目录');self.assertNotContains(response,'private-fingerprint')

    def test_existing_restore_target_is_rejected_before_creation_or_record(self):
        source=ops.begin(self.admin,'backup','test_nk_traffic');ops.finish(source,{})
        with tempfile.TemporaryDirectory() as temp:
            secret=Path(temp)/'secret.txt';secret.write_text('mock-only')
            context=MagicMock();context.__enter__.return_value=context;context.execute.return_value.fetchone.return_value=(1,)
            with patch.object(ops,'tools_at',return_value={}),patch.object(ops,'checked_bundle',return_value=(Path(temp),{})),patch.object(ops.psycopg,'connect',return_value=context):
                with self.assertRaises(ops.MaintenanceError):ops.restore(self.admin,source,'restore_nk_traffic_exists',temp,secret)
            self.assertEqual(MaintenanceRun.objects.count(),1)
            self.assertEqual(context.execute.call_count,1)

    def test_tampered_manifest_is_rejected_before_any_database_connection(self):
        with tempfile.TemporaryDirectory() as temp,override_settings(BASE_DIR=Path(temp)):
            source=ops.begin(self.admin,'backup','test_nk_traffic');root=Path(temp)/'var/backups'/str(source.pk);root.mkdir(parents=True)
            (root/'manifest.json').write_text('{}');source.artifact_relative_path=root.relative_to(temp).as_posix();source.save();ops.finish(source,{'manifest_sha256':'wrong'})
            with self.assertRaises(ops.MaintenanceError):ops.checked_bundle(source)

    def test_busy_lock_does_not_override_another_session(self):
        with psycopg.connect(**ops.params()) as first,psycopg.connect(**ops.params()) as second:
            with ops.data_lock(first):
                with self.assertRaises(ops.MaintenanceError):
                    with ops.data_lock(second):self.fail('must not enter')
            with ops.data_lock(second):pass

    def test_each_pending_import_phase_prevents_a_maintenance_snapshot(self):
        job=ImportJob.objects.create(source=DataSource.objects.get(kind='crashes'),created_by=self.admin,requested_by=self.admin,
            original_name='queued.jsonl',relative_path='data/raw/uploads/queued.jsonl',checksum='0'*64,status='ready')
        ops.assert_quiet(connection.connection)
        for state in ('queued_validation','validating','queued_import','running'):
            with self.subTest(state=state):
                ImportJob.objects.filter(pk=job.pk).update(status=state)
                with self.assertRaises(ops.MaintenanceError) as error:ops.assert_quiet(connection.connection)
                self.assertEqual(error.exception.code,'busy')
        ImportJob.objects.filter(pk=job.pk).update(status='succeeded')
        ops.assert_quiet(connection.connection)


@override_settings(ALLOWED_HOSTS=['testserver','127.0.0.1'],AI_CONFIG_FILE='')
class CrossModuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):fixture.ForecastDatabaseTests.setUpTestData.__func__(cls)

    def test_revision_invalidates_cache_and_analysis_but_preserves_report(self):
        settings={'date_from':'2021-01-01','date_to':'2025-12-31','network':str(self.network.pk)}
        form=analysis_form('statistics',settings);self.assertTrue(form.is_valid());old,_=cached_payload('statistics',form)
        risk_run=risk.enqueue(self.analyst,analysis_form('risk',{**settings,'level':'borough'}))
        RiskRun.objects.filter(pk=risk_run.pk).update(status='running');risk.compute_run(risk_run.pk)
        forecast_run=forecast.enqueue(self.analyst,forecast_form(settings));forecast_jobs.claim();forecast_jobs.execute(forecast_run.pk)
        main,source=prepare_forms(self.analyst,{'kind':'statistics',**settings});report=reports.enqueue(self.analyst,main,source)
        report_jobs.claim('report');report_jobs.execute('report',report.pk);report.refresh_from_db();frozen=json.dumps(report.payload,sort_keys=True)
        event=Collision.objects.get(pk=self.events[0]);raw=dict(event.raw_record.payload)
        save_entity(kind='crashes',actor=self.editor,pk=event.pk,expected_revision=event.revision,data={'persons_injured':9},reason='隔离综合测试：核对缓存、分析、报告及日志的跨模块一致性。')
        current_form=analysis_form('statistics',settings);self.assertTrue(current_form.is_valid());current,cached=cached_payload('statistics',current_form)
        self.assertFalse(cached);self.assertEqual(current['summary']['injured'],old['summary']['injured']+7)
        risk_run.refresh_from_db();forecast_run.refresh_from_db()
        self.assertTrue(risk.is_stale(risk_run));self.assertTrue(forecast.is_stale(forecast_run))
        report.refresh_from_db();self.assertEqual(json.dumps(report.payload,sort_keys=True),frozen)
        event.refresh_from_db();self.assertEqual(event.raw_record.payload,raw)
        self.assertTrue(DataRevision.objects.exists());self.assertTrue(AuditEvent.objects.filter(object_id=str(event.pk),action='data_changed').exists())

    def test_unpublished_and_demo_records_keep_original_query_semantics(self):
        demo=Collision.objects.create(origin='demo',local_date=date(2025,1,1))
        def ids(origin):
            form=QueryForm({'origin':origin},kind='crashes');self.assertTrue(form.is_valid());return set(filtered_entities('crashes',form).values_list('id',flat=True))
        self.assertEqual(ids('official'),set(self.events));self.assertIn(demo.pk,ids('all'))
        self.snapshot.status='quarantined';self.snapshot.save()
        self.assertEqual(ids('official'),set());self.assertEqual(ids('all'),{demo.pk});self.assertEqual(ids('demo'),{demo.pk})

    def test_unpublished_related_rows_cannot_satisfy_accident_filter(self):
        source=DataSource.objects.get(kind='vehicles');snapshot=Snapshot.objects.create(source=source,version='hidden-child',captured_at=self.snapshot.captured_at,relative_path='test',status='validated')
        raw=StagedRecord.objects.create(snapshot=snapshot,row_number=1,payload={})
        Vehicle.objects.create(source=source,source_record_id='hidden',raw_record=raw,collision_id=self.events[0],vehicle_type='Hidden Sedan')
        form=QueryForm({'vehicle_type':'Hidden Sedan'},kind='crashes');self.assertTrue(form.is_valid())
        self.assertEqual(filtered_entities('crashes',form).count(),0)
        snapshot.status='published';snapshot.save();self.assertEqual(filtered_entities('crashes',form).count(),1)

    def test_database_failure_renders_503_without_dsn_or_traceback(self):
        self.client.force_login(self.analyst)
        with patch('apps.collisions.views.filtered_entities',side_effect=OperationalError('password=do-not-display')):
            response=self.client.get('/records/crashes/')
        self.assertEqual(response.status_code,503);self.assertNotContains(response,'do-not-display',status_code=503);self.assertNotContains(response,'Traceback',status_code=503)
