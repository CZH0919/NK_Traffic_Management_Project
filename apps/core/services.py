"""Shared, authenticated mutation boundaries. Raw evidence is never a write target."""
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from .audit import record_event
from .models import FieldChange,DataRevision

class ConcurrentChange(ValidationError): pass

def require_actor(actor,capability):
    if actor is None or not actor.is_authenticated or not actor.is_active or not actor.has_perm('core.'+capability):
        raise PermissionDenied

def clean_reason(reason):
    reason=str(reason or '').strip()
    if len(reason)<5 or len(reason)>1000: raise ValidationError('请填写 5—1000 字的操作依据。')
    return reason

@contextmanager
def maintenance_lock():
    # Transaction-scoped counterpart of S02's session lock; caller must be atomic.
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_xact_lock(%s,%s)',[20261004,2])
        if not cursor.fetchone()[0]: raise ConcurrentChange('数据处理正在执行，请稍后重试维护操作。')
    yield

def json_value(value):
    if isinstance(value,(date,datetime,time)): return value.isoformat()
    if isinstance(value,Decimal): return str(value)
    if hasattr(value,'pk'): return value.pk
    if isinstance(value,dict): return {str(k):json_value(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [json_value(v) for v in value]
    return value

def audit_changes(actor,action,kind,object_id,changes,reason,*,revision=None,snapshot_ids=None,data_revision=True):
    reason=clean_reason(reason)
    details={'fields':list(changes)}
    if revision is not None: details['revision']=revision
    event=record_event(actor=actor,action=action,object_type=kind,object_id=object_id,details=details)
    FieldChange.objects.bulk_create([FieldChange(event=event,field_name=field,
        previous_value=json_value(before),new_value=json_value(after),reason=reason) for field,(before,after) in changes.items()])
    if data_revision: DataRevision.objects.create(event=event,domain=kind,snapshot_ids=snapshot_ids or [])
    return event
