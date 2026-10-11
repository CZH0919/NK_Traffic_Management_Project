import json
import os
import time
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from apps.core.audit import record_event
from .models import RiskRun
from .risk import compute_run

from apps.core.storage import storage_root
HEARTBEAT=storage_root()/'var/runtime/analysis_worker.json'

def heartbeat():
    HEARTBEAT.parent.mkdir(parents=True,exist_ok=True)
    HEARTBEAT.write_text(json.dumps({'pid':os.getpid(),'updated':time.time()}),encoding='utf-8')

def online():
    try:return time.time()-HEARTBEAT.stat().st_mtime<10
    except FileNotFoundError:return False

@transaction.atomic
def claim():
    run=RiskRun.objects.select_for_update(skip_locked=True).filter(status='queued').order_by('created_at').first()
    if not run:return None
    run.status='running';run.started_at=timezone.now();run.attempts+=1;run.save(update_fields=['status','started_at','attempts']);return run.pk

def execute(pk):
    try:
        if not compute_run(pk):RiskRun.objects.filter(pk=pk,status='running').update(status='queued',started_at=None)
    except Exception as e:
        with transaction.atomic():
            run=RiskRun.objects.select_for_update().get(pk=pk)
            run.status='failed';run.error_code=type(e).__name__;run.finished_at=timezone.now();run.save(update_fields=['status','error_code','finished_at'])
            record_event(actor=run.requested_by,action='risk_failed',object_type='RiskRun',object_id=pk,outcome='failure',details={'reason_code':type(e).__name__})
        return False
    return True

def recover():RiskRun.objects.filter(status='running').update(status='failed',error_code='interrupted',finished_at=timezone.now())
