"""Single local worker; PostgreSQL owns the queue and committed checkpoints."""
import hashlib
import json
import os
import uuid
from pathlib import Path
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from openpyxl import load_workbook
from apps.core.audit import record_event
from apps.core.services import require_actor, audit_changes
from . import pipeline, uploads
from .models import ImportJob, Snapshot, StagedRecord
from .quality import classify, publish

from apps.core.storage import storage_root
HEARTBEAT=storage_root()/'var'/('import_worker_'+hashlib.sha256(settings.DATABASES['default']['NAME'].encode()).hexdigest()[:12]+'.json')

def worker_heartbeat():
    HEARTBEAT.parent.mkdir(parents=True,exist_ok=True)
    temp=HEARTBEAT.with_suffix('.tmp')
    temp.write_text(json.dumps({'at':timezone.now().isoformat(),'pid':os.getpid()}),encoding='utf-8')
    temp.replace(HEARTBEAT)

def worker_online():
    try:
        from datetime import datetime
        value=json.loads(HEARTBEAT.read_text(encoding='utf-8'))
        return 0<=(timezone.now()-datetime.fromisoformat(value['at'])).total_seconds()<15
    except (OSError,ValueError,KeyError): return False

@transaction.atomic
def transition_job(actor,pk,action):
    require_actor(actor,'maintain_data')
    job=ImportJob.objects.select_for_update().select_related('source').get(pk=pk)
    if job.created_by_id!=actor.pk and not actor.has_perm('core.manage_users'): raise PermissionDenied
    previous=job.status
    if action=='import' and job.status=='ready': job.status='queued_import'; job.phase='import'; job.progress_rows=0
    elif action=='retry' and job.status=='failed': job.status='queued_validation' if job.phase=='validate' else 'queued_import'
    elif action=='cancel' and job.status in ('queued_validation','ready','queued_import','failed'): job.status='cancelled'
    else: raise ValidationError('任务状态已变化或操作不适用，请刷新后检查。')
    if job.status!='cancelled' and not job.source.is_active: raise ValidationError('来源已停用。')
    job.requested_by=actor; job.error_code=''; job.finished_at=None; job.save()
    audit_changes(actor,'import_submitted','ImportJob',job.pk,{'status':(previous,job.status)},'由授权用户提交任务状态变更。',data_revision=False)
    return job

def traffic_notices(snapshot):
    receipt=Snapshot.objects.filter(source=snapshot.source,status='published',manifest__has_key='location_notice').order_by('-captured_at').values_list('manifest',flat=True).first()
    if not receipt: raise uploads.UploadError('notice_missing')
    receipt=receipt['location_notice']; path=pipeline.safe_raw_path(receipt['path'])
    if not path.is_file() or uploads.file_hash(path)!=receipt['sha256']: raise uploads.UploadError('notice_missing')
    book=load_workbook(path,read_only=True,data_only=True)
    try:
        rows=iter(book.active.values); next(rows,None); header=next(rows,None)
        if not header or str(header[1]).strip()!='RequestID' or str(header[2]).strip()!='SegmentId': raise uploads.UploadError('notice_missing')
        notices={(str(int(r[1])),str(int(r[2]))) for r in rows if r[1] is not None and r[2] is not None}
    finally: book.close()
    snapshot.manifest['location_notice']=receipt; snapshot.save(update_fields=['manifest'])
    return notices

@transaction.atomic
def claim_job():
    job=ImportJob.objects.select_for_update(skip_locked=True).filter(status__in=['queued_validation','queued_import']).order_by('created_at').first()
    if not job: return None
    job.phase='validate' if job.status=='queued_validation' else 'import'
    job.status='validating' if job.phase=='validate' else 'running'; job.attempt+=1
    job.worker_token=uuid.uuid4(); job.heartbeat_at=timezone.now(); job.error_code=''; job.save()
    return job.pk

def recover_interrupted():
    # Called only after acquiring the exclusive worker session lock.
    for job in ImportJob.objects.filter(status__in=['validating','running']):
        with transaction.atomic():
            previous=job.status; job.status='failed'; job.error_code='worker_interrupted'; job.finished_at=timezone.now(); job.save()
            record_event(action='import_finished',object_type='ImportJob',object_id=job.pk,outcome='failure',details={'reason_code':'worker_interrupted'})

def execute_job(pk):
    job=ImportJob.objects.select_related('source').get(pk=pk)
    actor=User.objects.get(pk=job.requested_by_id)
    def progress(rows,batch=None):
        # Refresh authorization at every committed chunk, so revocation halts future writes.
        fresh=User.objects.get(pk=actor.pk); require_actor(fresh,'maintain_data')
        if not type(job.source).objects.get(pk=job.source_id).is_active: raise uploads.UploadError('source_disabled')
        values={'progress_rows':rows,'heartbeat_at':timezone.now(),'updated_at':timezone.now()}
        if batch: values['batch']=batch
        ImportJob.objects.filter(pk=pk,worker_token=job.worker_token).update(**values)
    try:
        require_actor(actor,'maintain_data')
        if not job.source.is_active: raise uploads.UploadError('source_disabled')
        if job.phase=='validate':
            manifest,summary=uploads.validate_file(job,progress)
            with transaction.atomic():
                job.manifest_path=manifest; job.summary=summary; job.total_rows=summary['rows']; job.progress_rows=summary['rows']
                job.status='ready'; job.finished_at=timezone.now(); job.save()
                record_event(actor=actor,action='import_validated',object_type='ImportJob',object_id=job.pk,details={'count':summary['rows']})
        else:
            original=uploads.safe_path(job.relative_path)
            if uploads.file_hash(original)!=job.checksum: raise uploads.UploadError('integrity_failed')
            manifest=pipeline.read_manifest(uploads.safe_path(job.manifest_path,'data/manifests/uploads'))
            if manifest.get('original_checksum')!=job.checksum: raise uploads.UploadError('integrity_failed')
            with pipeline.import_lock():
                snapshot=pipeline.register(manifest,source=job.source)
                job.snapshot=snapshot; job.total_rows=snapshot.source_count; job.save(update_fields=['snapshot','total_rows'])
                notices=traffic_notices(snapshot) if job.source.kind=='traffic' else None
                batch,cached=pipeline.import_snapshot(snapshot,actor=actor,notices=notices,after_chunk=lambda b:progress(b.checkpoint,b))
                progress(batch.checkpoint,batch)
                if snapshot.status!='published':
                    classify([snapshot])
                    counts=dict(StagedRecord.objects.filter(snapshot=snapshot).values_list('disposition').annotate(n=Count('id')))
                    business=pipeline.ENTITY[job.source.kind].objects.filter(raw_record__snapshot=snapshot).count()
                    expected=counts.get('accepted',0)+(counts.get('pending',0)+counts.get('conflict',0) if job.source.kind=='traffic' else 0)
                    # Conflict raw rows with no typed observation stay in quarantine; count typed only.
                    if job.source.kind=='traffic':
                        expected=StagedRecord.objects.filter(snapshot=snapshot,trafficobservation__isnull=False).count()
                    checks={'raw_count':sum(counts.values())==snapshot.source_count,'checkpoint':batch.checkpoint==snapshot.source_count,'business_count':business==expected}
                    publish([snapshot],{'checks':checks},actor=actor)
                batch.refresh_from_db()
                with transaction.atomic():
                    job.batch=batch; job.status=batch.status; job.finished_at=timezone.now(); job.progress_rows=batch.checkpoint
                    job.summary.update({'accepted':batch.accepted_rows,'rejected':batch.rejected_rows,'pending':batch.pending_rows,
                        'skipped':batch.skipped_rows,'cached':cached,'snapshot_id':snapshot.pk})
                    job.save()
    except Exception as exc:
        code=exc.code if isinstance(exc,uploads.UploadError) else ('permission_revoked' if isinstance(exc,PermissionDenied) else 'validation_failed' if job.phase=='validate' else 'import_failed')
        with transaction.atomic():
            ImportJob.objects.filter(pk=pk).update(status='failed',error_code=code,finished_at=timezone.now(),updated_at=timezone.now())
            record_event(actor=actor,action='import_finished',object_type='ImportJob',object_id=pk,outcome='failure',details={'reason_code':code})
        # Local operator log contains only class and stable error code, never SQL or uploaded data.
        print(json.dumps({'job':str(pk),'error':code,'exception':type(exc).__name__}),flush=True)
        return False
    return True
