from django.db import transaction
from django.utils import timezone
from apps.core.audit import record_event
from .models import ForecastRun
from .services import compute_run


@transaction.atomic
def claim():
    run = ForecastRun.objects.select_for_update(skip_locked=True).filter(status='queued').order_by('created_at').first()
    if not run: return None
    run.status = 'running'
    run.started_at = timezone.now()
    run.attempts += 1
    run.save(update_fields=['status', 'started_at', 'attempts'])
    return run.pk


def execute(pk):
    try:
        if not compute_run(pk):
            ForecastRun.objects.filter(pk=pk, status='running').update(status='queued', started_at=None)
    except Exception as error:
        with transaction.atomic():
            run = ForecastRun.objects.select_for_update().get(pk=pk)
            run.status, run.error_code, run.finished_at = 'failed', type(error).__name__, timezone.now()
            run.save(update_fields=['status', 'error_code', 'finished_at'])
            record_event(actor=run.requested_by, action='forecast_failed', object_type='ForecastRun', object_id=pk, outcome='failure', details={'reason_code': type(error).__name__})
        return False
    return True


def recover():
    ForecastRun.objects.filter(status='running').update(status='failed', error_code='interrupted', finished_at=timezone.now())
