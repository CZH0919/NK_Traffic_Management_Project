from django.db import transaction
from django.utils import timezone
from django.core.exceptions import PermissionDenied, ValidationError
from apps.core.audit import record_event
from .models import Report, ExportJob, AIBrief, AIRequest
from . import services, exports, ai

MODELS={'report':Report,'export':ExportJob,'brief':AIBrief}


@transaction.atomic
def claim(kind):
    job=MODELS[kind].objects.select_for_update(skip_locked=True).filter(status='queued').order_by('created_at').first()
    if not job:return None
    job.status='running';job.started_at=timezone.now();job.save(update_fields=['status','started_at'])
    return job.pk


def execute(kind,pk):
    if kind=='brief':return ai.execute(pk)
    model=MODELS[kind]
    try:
        complete=(services.compute if kind=='report' else exports.compute)(pk)
        if not complete:model.objects.filter(pk=pk,status='running').update(status='queued',started_at=None)
    except Exception as error:
        code=str(error) if isinstance(error,exports.ExportError) and str(error) in exports.ERRORS else 'permission' if isinstance(error,PermissionDenied) else 'invalid_scope' if isinstance(error,ValidationError) else 'internal'
        with transaction.atomic():
            job=model.objects.select_for_update().get(pk=pk)
            job.status='failed';job.error_code=code;job.finished_at=timezone.now();job.save(update_fields=['status','error_code','finished_at'])
            record_event(actor=job.owner,action=kind+'_failed',object_type=model.__name__,object_id=pk,outcome='failure',details={'reason_code':code})
        return False
    return True


def recover():
    with transaction.atomic():
        for model in MODELS.values():model.objects.filter(status='running').update(status='failed',error_code='interrupted',finished_at=timezone.now())
        AIRequest.objects.filter(status='running').update(status='failed',error_code='interrupted',finished_at=timezone.now())
        # An interrupted claimed brief can still have a not-yet-sent queued attempt.
        AIRequest.objects.filter(status='queued',brief__status='failed').update(status='failed',error_code='interrupted',finished_at=timezone.now())
