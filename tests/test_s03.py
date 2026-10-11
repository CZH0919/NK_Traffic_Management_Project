import gzip
import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth.models import User, Group
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connection, transaction
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from apps.accounts.maintenance import save_account, account_state
from apps.collisions.models import Collision, Vehicle, Person, Factor
from apps.collisions.services import save_entity, change_status
from apps.collisions.forms import EntityForm
from apps.collisions.queries import QueryForm, filtered_entities
from apps.core.models import AuditEvent, FieldChange, DataRevision, SavedQuery, CategoryMapping
from apps.core.services import ConcurrentChange
from apps.ingestion.models import DataSource, Snapshot, StagedRecord, QualityIssue, ImportJob
from apps.ingestion.quality_views import review_issues
from apps.ingestion import uploads, jobs, pipeline
from apps.ingestion.quality import classify

REASON='依据原始记录完成课程系统核查。'

@override_settings(ALLOWED_HOSTS=['testserver','127.0.0.1','localhost'])
class BusinessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users={}
        for name,role in [('admin','系统管理员'),('editor','数据维护人员'),('analyst','分析用户')]:
            user=User.objects.create_user(name,password='Strong-course-password-2026'); user.groups.add(Group.objects.get(name=role)); cls.users[name]=user
        cls.source=DataSource.objects.get(code='nyc-crashes')
        cls.snapshot=Snapshot.objects.create(source=cls.source,version='test evidence',captured_at=timezone.now(),relative_path='test-only',status='published',source_count=1)
        cls.raw=StagedRecord.objects.create(snapshot=cls.snapshot,row_number=1,source_key='123',payload={'collision_id':'123','number_of_persons_injured':'0'})
        cls.crash=Collision.objects.create(origin='official',source=cls.source,source_record_id='123',raw_record=cls.raw,local_date=date(2025,1,1),persons_injured=0,persons_killed=0)
        cls.vehicle=Vehicle.objects.create(origin='demo',collision=cls.crash,vehicle_type='Sedan')
        cls.person=Person.objects.create(origin='demo',collision=cls.crash,vehicle=cls.vehicle,age=0)
        cls.issue=QualityIssue.objects.create(snapshot=cls.snapshot,record=cls.raw,code='missing_borough',severity='warning')
    def setUp(self): self.client.force_login(self.users['editor'])
    def mutate(self,kind='crashes',obj=None,**data):
        obj=obj or self.crash
        return save_entity(kind=kind,actor=self.users['editor'],pk=obj.pk,expected_revision=obj.revision,data=data,reason=REASON)
    def test_readable_routes_with_real_relations_zero_and_evidence(self):
        for path in ['/records/crashes/','/records/vehicles/?origin=all','/records/persons/?origin=all',
                f'/records/crashes/{self.crash.pk}/',f'/records/vehicles/{self.vehicle.pk}/',f'/records/persons/{self.person.pk}/',
                f'/records/crashes/{self.crash.pk}/edit/',f'/records/crashes/{self.crash.pk}/status/',
                '/quality/',f'/quality/{self.issue.pk}/','/references/','/categories/new/','/queries/','/imports/','/imports/upload/']:
            self.assertEqual(self.client.get(path).status_code,200,path)
        detail=self.client.get(f'/records/crashes/{self.crash.pk}/').content.decode()
        self.assertIn('<dd >0</dd>',detail); self.assertIn('只读原始记录',detail)
    def test_mutation_records_before_after_and_raw_immutable(self):
        obj,_=self.mutate(review_note='已核对来源总伤亡为零。')
        self.assertEqual(obj.revision,2); self.assertEqual(DataRevision.objects.count(),1)
        change=FieldChange.objects.get(field_name='review_note'); self.assertEqual(change.previous_value,'')
        self.assertEqual(change.new_value,'已核对来源总伤亡为零。')
        self.raw.refresh_from_db(); self.assertEqual(self.raw.payload['number_of_persons_injured'],'0')
        for sql in ['UPDATE ingestion_stagedrecord SET payload=\'{}\' WHERE id=%s','DELETE FROM core_datarevision WHERE id=%s']:
            with self.assertRaises(DatabaseError),transaction.atomic(),connection.cursor() as c:
                c.execute(sql,[self.raw.pk if 'stagedrecord' in sql else DataRevision.objects.get().pk])
    def test_invalid_person_relation_and_age_roll_back(self):
        other=Collision.objects.create(origin='demo',local_date=date(2025,1,2))
        for data in ({'collision':other.pk},{'age':131},{'age':-1}):
            with self.assertRaises(ValidationError): self.mutate('persons',self.person,**data)
        self.person.refresh_from_db(); self.assertEqual(self.person.age,0); self.assertFalse(DataRevision.objects.exists())
    def test_source_and_origin_tampering_is_rejected(self):
        for data in ({'source_record_id':'changed'},{'raw_record':None},{'origin':'demo'}):
            with self.assertRaises(ValidationError): self.mutate(**data)
        with self.assertRaises(ValidationError): save_entity(kind='crashes',actor=self.users['editor'],data={'local_date':date(2025,1,1),'origin':'official'},reason=REASON)
    def test_stale_revision_does_not_overwrite(self):
        self.mutate(review_note='第一位维护人员核查。')
        with self.assertRaises(ConcurrentChange): self.mutate(review_note='第二位维护人员核查。')
        self.assertEqual(FieldChange.objects.filter(field_name='review_note').count(),1)
    def test_factor_failure_rolls_back_other_fields(self):
        Factor.objects.create(name='inactive',is_active=False)
        with self.assertRaises(ValidationError): self.mutate(review_note='不应提交的事务内容。',factors=[{'slot':1,'name':'inactive'}])
        self.crash.refresh_from_db(); self.assertEqual(self.crash.review_note,''); self.assertFalse(DataRevision.objects.exists())
    def test_cascade_and_ordered_restore_preserve_records(self):
        count=change_status(kind='crashes',actor=self.users['editor'],versions={self.crash.pk:1},active=False,reason=REASON)
        self.assertEqual(count,3)
        for obj in (self.crash,self.vehicle,self.person): obj.refresh_from_db(); self.assertFalse(obj.is_active)
        with self.assertRaises(ValidationError): change_status(kind='persons',actor=self.users['editor'],versions={self.person.pk:2},active=True,reason=REASON)
        for kind,obj in [('crashes',self.crash),('vehicles',self.vehicle),('persons',self.person)]:
            change_status(kind=kind,actor=self.users['editor'],versions={obj.pk:2},active=True,reason=REASON)
        self.assertEqual((Collision.objects.count(),Vehicle.objects.count(),Person.objects.count()),(1,1,1))
        self.assertEqual(DataRevision.objects.count(),6)
    def test_bulk_stale_entire_transaction_unchanged(self):
        other=Collision.objects.create(origin='demo',local_date=date(2025,1,1))
        with self.assertRaises(ConcurrentChange): change_status(kind='crashes',actor=self.users['editor'],versions={self.crash.pk:1,other.pk:2},active=False,reason=REASON)
        self.assertEqual(Collision.objects.filter(is_active=True).count(),2); self.assertFalse(DataRevision.objects.exists())
    def test_official_filter_excludes_demo_and_unpublished(self):
        Collision.objects.create(origin='demo',local_date=date(2025,1,1))
        form=QueryForm({},kind='crashes'); self.assertTrue(form.is_valid()); self.assertEqual(filtered_entities('crashes',form).count(),1)
        self.snapshot.status='validated'; self.snapshot.save(); self.assertEqual(filtered_entities('crashes',form).count(),0)
    def test_filters_invalid_and_vehicle_backlink_scope(self):
        form=QueryForm({'origin':'all','vehicle_id':str(self.vehicle.pk),'age_min':'0','age_max':'0'},kind='persons')
        self.assertTrue(form.is_valid()); self.assertEqual(list(filtered_entities('persons',form)),[self.person])
        for params in [{'sort':'raw_sql'},{'date_from':'2025-02-01','date_to':'2025-01-01'},{'origin':'forged'}]:
            self.assertEqual(self.client.get('/records/crashes/',params).status_code,400)
    def test_analyst_direct_mutation_routes_and_services_denied(self):
        self.client.force_login(self.users['analyst'])
        paths=[f'/records/crashes/{self.crash.pk}/edit/',f'/records/crashes/{self.crash.pk}/status/',
            '/records/crashes/new/','/records/crashes/bulk/',f'/quality/{self.issue.pk}/review/','/quality/bulk/',
            '/categories/new/','/references/borough/1/','/imports/upload/','/accounts/manage/new/']
        for path in paths: self.assertEqual(self.client.post(path,{'reason':REASON}).status_code,403,path)
        with self.assertRaises(PermissionDenied): save_entity(kind='crashes',actor=self.users['analyst'],data={},reason=REASON)
    def test_csrf_on_business_mutations(self):
        client=Client(enforce_csrf_checks=True); client.force_login(self.users['editor'])
        for url in [f'/quality/{self.issue.pk}/review/',f'/records/crashes/{self.crash.pk}/edit/','/imports/upload/']:
            self.assertEqual(client.post(url,{}).status_code,403)
    def test_query_save_load_owner_and_soft_delete(self):
        response=self.client.get('/records/crashes/',{'borough':1,'severity':'none'})
        token=response.context['save_form'].initial['conditions']
        self.assertEqual(self.client.post('/queries/save/crashes/',{'name':'零伤亡核查','conditions':token}).status_code,302)
        saved=SavedQuery.objects.get(); self.assertEqual(saved.conditions['severity'],'none')
        self.assertIn('severity=none',self.client.get(f'/queries/{saved.pk}/load/').url)
        self.client.force_login(self.users['analyst'])
        self.assertEqual(self.client.get(f'/queries/{saved.pk}/load/').status_code,404)
        self.assertEqual(self.client.post('/queries/save/crashes/',{'name':'盗用','conditions':token}).status_code,400)
        self.client.force_login(self.users['editor']); self.client.post(f'/queries/{saved.pk}/update/',{'action':'delete'})
        saved.refresh_from_db(); self.assertFalse(saved.is_active)
        self.client.post(f'/queries/{saved.pk}/update/',{'action':'rename','name':'恢复后的名称'})
        self.client.post(f'/queries/{saved.pk}/update/',{'action':'restore'}); saved.refresh_from_db(); self.assertTrue(saved.is_active)
    def test_quality_bulk_and_stale_review_leave_raw_unchanged(self):
        review_issues(self.users['editor'],{self.issue.pk:1},'accepted',REASON)
        self.issue.refresh_from_db(); self.assertEqual((self.issue.status,self.issue.revision),('accepted',2))
        with self.assertRaises(ConcurrentChange): review_issues(self.users['editor'],{self.issue.pk:1},'resolved',REASON)
        review_issues(self.users['editor'],{self.issue.pk:2},'open','重新核查原始来源说明。')
        self.issue.refresh_from_db(); self.assertIsNone(self.issue.resolved_by_id)
        self.raw.refresh_from_db(); self.assertEqual(self.raw.payload['collision_id'],'123')
    def test_category_new_version_does_not_modify_raw_type(self):
        payload={'kind':'vehicle_type','raw_value':'Sedan','canonical_code':'sedan','canonical_label':'轿车','is_active':'on','expected_version':'0','reason':REASON}
        self.assertEqual(self.client.post('/categories/new/',payload).status_code,302)
        original=CategoryMapping.objects.get(); payload.update(canonical_label='乘用轿车',expected_version='1')
        self.assertEqual(self.client.post(f'/categories/{original.pk}/',payload).status_code,302)
        original.refresh_from_db(); self.assertFalse(original.is_active); self.assertEqual(original.canonical_label,'轿车')
        self.assertEqual(CategoryMapping.objects.filter(is_active=True).get().version,2)
        self.vehicle.refresh_from_db(); self.assertEqual(self.vehicle.vehicle_type,'Sedan')
        self.assertEqual(self.client.post(f'/categories/{original.pk}/',payload).status_code,200)  # stale form shows a recoverable error
    def test_account_last_admin_protected_and_password_never_audited(self):
        admin=self.users['admin']; self.client.force_login(admin)
        state=account_state(admin); token=signing.dumps({'id':admin.pk,'state':state},salt='account-edit')
        data={'username':'admin','first_name':'','last_name':'','role':'分析用户','is_active':True,'password':'','version':token,'reason':REASON}
        with self.assertRaises(ValidationError): save_account(admin,data,admin.pk)
        data.update(username='new-user',role='数据维护人员',password='Private-test-passphrase-2026')
        user=save_account(admin,data); self.assertTrue(user.check_password(data['password']))
        self.assertEqual(user.groups.get().name,'数据维护人员')
        self.assertFalse(FieldChange.objects.filter(new_value__icontains='Private-test').exists())
        self.assertFalse(user.is_superuser or user.is_staff)
        data.update(version=signing.dumps({'id':user.pk,'state':account_state(user)},salt='account-edit'),password='',is_active=False)
        save_account(admin,data,user.pk); user.refresh_from_db(); self.assertFalse(user.is_active)
    def test_reference_update_token_prevents_lost_update(self):
        response=self.client.get('/references/borough/1/'); form=response.context['form']
        data={'chinese_name':'曼哈顿区','reason':REASON,'version':form.initial['version']}
        self.assertEqual(self.client.post('/references/borough/1/',data).status_code,302)
        response=self.client.post('/references/borough/1/',{**data,'chinese_name':'旧页面新名字'})
        self.assertContains(response,'基础信息已经变化')

    def test_import_lock_excludes_concurrent_business_maintenance(self):
        connection.ensure_connection(); params=connection.get_connection_params()
        self.assertTrue(params['dbname'].startswith('test_'))
        with connection.Database.connect(**params,autocommit=True) as other:
            other.execute('SELECT pg_advisory_lock(%s,%s)',[20261004,2])
            try:
                with self.assertRaises(ConcurrentChange): self.mutate(review_note='导入锁期间不能写入。')
            finally: other.execute('SELECT pg_advisory_unlock(%s,%s)',[20261004,2])
        self.assertFalse(DataRevision.objects.exists())

    def test_account_http_edit_and_privilege_tampering(self):
        self.client.force_login(self.users['admin'])
        response=self.client.get('/accounts/manage/new/'); self.assertEqual(response.status_code,200)
        payload={'username':'http-new','first_name':'测试','last_name':'','role':'分析用户','is_active':'on',
            'password':'Local-private-account-2026','password_confirm':'Local-private-account-2026','reason':REASON,'is_superuser':'on','is_staff':'on'}
        self.assertEqual(self.client.post('/accounts/manage/new/',payload).status_code,302)
        user=User.objects.get(username='http-new'); self.assertFalse(user.is_staff or user.is_superuser)
        self.client.force_login(self.users['editor']); self.assertEqual(self.client.get(f'/accounts/manage/{user.pk}/').status_code,403)

    def test_audit_detail_and_rejected_mutation_metadata(self):
        self.mutate(review_note='核查记录可在审计页回查。')
        event=AuditEvent.objects.get(action='data_changed')
        self.assertEqual(self.client.get(f'/system/audit/{event.pk}/').status_code,403)
        self.client.force_login(self.users['admin']); self.assertContains(self.client.get(f'/system/audit/{event.pk}/'),'核查记录可在审计页回查。')
        self.client.force_login(self.users['analyst']); self.client.post(f'/records/crashes/{self.crash.pk}/edit/',{'reason':'secret-not-audited'})
        failed=AuditEvent.objects.get(action='operation_rejected'); self.assertEqual(failed.outcome,'failure'); self.assertNotIn('secret',str(failed.details))

    def test_mapping_history_database_guard(self):
        item=CategoryMapping.objects.create(kind='vehicle_type',raw_value='Sedan',canonical_code='sedan',canonical_label='轿车')
        with self.assertRaises(DatabaseError),transaction.atomic(): CategoryMapping.objects.filter(pk=item.pk).update(canonical_label='overwrite')

    def test_actual_http_edit_and_stale_form(self):
        form=EntityForm(kind='crashes',instance=self.crash)
        values={f.name:(str(f.value()) if f.value() is not None else '') for f in form}
        values.update(review_note='通过请求完整验证表单修订。',reason=REASON)
        self.assertEqual(self.client.post(f'/records/crashes/{self.crash.pk}/edit/',values).status_code,302)
        self.assertEqual(self.client.post(f'/records/crashes/{self.crash.pk}/edit/',values).status_code,409)


class FileJobTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=pipeline.ROOT/'var'); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); (self.root/'data/raw').mkdir(parents=True); (self.root/'data/manifests').mkdir(parents=True)
        for module in (pipeline,uploads):
            patcher=patch.object(module,'ROOT',self.root); patcher.start(); self.addCleanup(patcher.stop)
        self.editor=User.objects.create_user('job-editor'); self.editor.groups.add(Group.objects.get(name='数据维护人员'))
        self.source=DataSource.objects.get(code='nyc-crashes')
    def upload(self,rows=None,content=None,name='source.json'):
        if content is None: content=json.dumps(rows or [{'collision_id':'500','crash_date':'2025-01-01','crash_time':'12:00'}]).encode()
        return uploads.submit_upload(self.editor,self.source,SimpleUploadedFile(name,content))
    def execute(self,job):
        self.assertEqual(jobs.claim_job(),job.pk); result=jobs.execute_job(job.pk); job.refresh_from_db(); return result
    def test_validation_submission_publication_and_idempotence(self):
        job=self.upload(); self.assertTrue(self.execute(job)); self.assertEqual(job.status,'ready'); self.assertFalse(Collision.objects.exists())
        jobs.transition_job(self.editor,job.pk,'import'); self.assertTrue(self.execute(job)); self.assertEqual(job.status,'succeeded')
        self.assertEqual((job.progress_rows,Collision.objects.count()),(1,1)); self.assertEqual(job.snapshot.status,'published')
        another=self.upload(); self.execute(another); jobs.transition_job(self.editor,another.pk,'import'); self.execute(another)
        self.assertTrue(another.summary['cached']); self.assertEqual(Collision.objects.count(),1); self.assertEqual(StagedRecord.objects.count(),1)
    def test_formats_csv_jsonl_gzip_and_reject_trailing_junk(self):
        samples=[('source.csv',b'collision_id,crash_date,crash_time\n500,2025-01-01,12:00\n'),
            ('source.jsonl',b'{"collision_id":"501","crash_date":"2025-01-01","crash_time":"12:00"}\n'),
            ('source.json.gz',gzip.compress(b'[{"collision_id":"502","crash_date":"2025-01-01","crash_time":"12:00"}]'))]
        for name,content in samples:
            job=self.upload(content=content,name=name); self.assertTrue(self.execute(job)); self.assertEqual(job.total_rows,1)
        job=self.upload(content=b'[{"collision_id":1}] trailing'); self.assertFalse(self.execute(job)); self.assertEqual(job.error_code,'invalid_format')
    def test_bad_schema_safe_retry_and_partial_import(self):
        bad=self.upload(content=b'[{"bad":"data"}]'); self.assertFalse(self.execute(bad)); self.assertEqual(bad.error_code,'schema_mismatch')
        jobs.transition_job(self.editor,bad.pk,'retry'); self.assertFalse(self.execute(bad)); self.assertEqual(bad.attempt,2)
        job=self.upload(rows=[{'collision_id':'1','crash_date':'invalid','crash_time':'12:00'},{'collision_id':'2','crash_date':'2025-01-01','crash_time':'12:00'}])
        self.execute(job); jobs.transition_job(self.editor,job.pk,'import'); self.execute(job)
        self.assertEqual((job.status,job.summary['accepted'],job.summary['rejected']),('partial',1,1))
        self.assertEqual(StagedRecord.objects.count(),2)
    def test_worker_interruption_recovers_without_losing_evidence(self):
        job=self.upload(); jobs.claim_job(); jobs.recover_interrupted(); job.refresh_from_db()
        self.assertEqual((job.status,job.error_code),('failed','worker_interrupted'))
        jobs.transition_job(self.editor,job.pk,'retry'); self.assertTrue(self.execute(job)); self.assertEqual(job.attempt,2)
    def test_worker_permission_revocation_and_source_disable(self):
        job=self.upload(); self.editor.groups.clear(); self.assertFalse(self.execute(job)); self.assertEqual(job.error_code,'permission_revoked')
        self.assertFalse(StagedRecord.objects.exists())
    def test_integrity_mutation_rejected(self):
        job=self.upload(); uploads.safe_path(job.relative_path).write_bytes(b'[]')
        self.assertFalse(self.execute(job)); self.assertEqual(job.error_code,'integrity_failed')
    def test_path_limits_and_unknown_file(self):
        with self.assertRaises(uploads.UploadError): uploads.safe_path('../../outside')
        with self.assertRaises(uploads.UploadError): self.upload(content=b'content',name='payload.html')
        with patch.object(uploads,'MAX_UPLOAD',2),self.assertRaises(uploads.UploadError): self.upload()
    def test_other_editor_cannot_resubmit_job(self):
        job=self.upload(); self.execute(job)
        other=User.objects.create_user('other-editor'); other.groups.add(Group.objects.get(name='数据维护人员'))
        with self.assertRaises(PermissionDenied): jobs.transition_job(other,job.pk,'import')

    def test_retry_after_committed_chunk_counts_match(self):
        job=self.upload(rows=[{'collision_id':str(i),'crash_date':'2025-01-01','crash_time':'12:00'} for i in range(1000,3001)])
        self.execute(job); jobs.transition_job(self.editor,job.pk,'import')
        original=pipeline.Importer.side_tables; calls=[]
        def fail_second(importer,pairs):
            calls.append(1)
            if len(calls)==2: raise RuntimeError('controlled test interruption')
            return original(importer,pairs)
        with patch.object(pipeline.Importer,'side_tables',fail_second): self.assertFalse(self.execute(job))
        self.assertEqual(Collision.objects.count(),2000); self.assertEqual(job.progress_rows,2000)
        jobs.transition_job(self.editor,job.pk,'retry'); self.assertTrue(self.execute(job))
        self.assertEqual(Collision.objects.count(),2001); self.assertEqual(StagedRecord.objects.count(),2001)
        self.assertEqual((job.batch.attempt,job.batch.checkpoint),(2,2001))

    def test_missing_parent_remains_visible_and_auditable(self):
        self.source=DataSource.objects.get(code='nyc-vehicles')
        job=self.upload(rows=[{'unique_id':'990','collision_id':'999999','vehicle_id':'uuid-990','crash_date':'2025-01-01','crash_time':'12:00'}])
        self.execute(job); jobs.transition_job(self.editor,job.pk,'import'); self.execute(job)
        self.assertEqual(Vehicle.objects.count(),1); self.assertIsNone(Vehicle.objects.get().collision_id)
        self.assertTrue(QualityIssue.objects.filter(code='orphan_collision').exists())

    def test_file_nul_duplicate_headers_and_trailing_comma_rejected(self):
        for name,content in [('source.json',b'[{"collision_id":"1","x":"\\u0000"}]'),
            ('source.csv',b'collision_id,collision_id\n1,2\n'),('source.json',b'[{"collision_id":"1"},]')]:
            job=self.upload(content=content,name=name); self.assertFalse(self.execute(job)); self.assertEqual(job.error_code,'invalid_format')
