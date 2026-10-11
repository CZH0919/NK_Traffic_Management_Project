import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q, F

class DataSource(models.Model):
    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=160)
    publisher = models.CharField(max_length=120)
    dataset_id = models.CharField(max_length=80, blank=True)
    kind = models.CharField(max_length=20, choices=[(x, x) for x in ['crashes', 'vehicles', 'persons', 'traffic', 'roads', 'nodes', 'boundaries']])
    homepage = models.URLField(max_length=500)
    description = models.TextField(blank=True)
    license_note = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['id']
        constraints = [models.UniqueConstraint(fields=['kind','dataset_id'], condition=~Q(dataset_id=''), name='source_kind_dataset_unique')]

    def __str__(self):
        return self.name

class Snapshot(models.Model):
    source = models.ForeignKey(DataSource, on_delete=models.PROTECT, related_name='snapshots')
    version = models.CharField(max_length=100)
    captured_at = models.DateTimeField()
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    data_through = models.DateField(null=True, blank=True)
    relative_path = models.CharField(max_length=400)
    checksum = models.CharField(max_length=64, blank=True)
    source_count = models.PositiveBigIntegerField(null=True, blank=True)
    native_crs = models.CharField(max_length=80, blank=True)
    manifest = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default='registered', choices=[('registered', '已登记'), ('validated', '已核验'), ('published', '已发布'), ('quarantined', '已隔离')])

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['source', 'checksum'], condition=~Q(checksum=''), name='snapshot_source_checksum_unique'),
            models.CheckConstraint(condition=Q(period_start__isnull=True, period_end__isnull=True) | Q(period_start__isnull=False, period_end__isnull=False, period_end__gte=F('period_start')), name='snapshot_period_valid'),
        ]

class ImportBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    snapshot = models.ForeignKey(Snapshot, on_delete=models.PROTECT)
    attempt = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default='queued', choices=[(x, y) for x, y in [('queued','排队'),('running','执行中'),('succeeded','成功'),('partial','部分成功'),('failed','失败'),('cancelled','已取消')]])
    total_rows = models.PositiveBigIntegerField(default=0)
    accepted_rows = models.PositiveBigIntegerField(default=0)
    rejected_rows = models.PositiveBigIntegerField(default=0)
    pending_rows = models.PositiveBigIntegerField(default=0)
    skipped_rows = models.PositiveBigIntegerField(default=0)
    checkpoint = models.PositiveBigIntegerField(default=0)
    error_code = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['snapshot', 'attempt'], name='batch_attempt_unique'),
            models.CheckConstraint(condition=Q(attempt__gte=1), name='batch_attempt_positive'),
            models.CheckConstraint(condition=Q(total_rows__gte=F('accepted_rows') + F('rejected_rows') + F('pending_rows') + F('skipped_rows')), name='batch_counts_bounded')]

class StagedRecord(models.Model):
    snapshot = models.ForeignKey(Snapshot, on_delete=models.PROTECT)
    row_number = models.PositiveBigIntegerField()
    source_key = models.CharField(max_length=150, blank=True)
    payload = models.JSONField()
    disposition = models.CharField(max_length=12, default='pending', choices=[('pending','待核查'),('accepted','已接受'),('rejected','已拒绝'),('conflict','冲突'),('skipped','重复跳过')])

    class Meta:
        constraints = [models.UniqueConstraint(fields=['snapshot', 'row_number'], name='raw_snapshot_row_unique'), models.CheckConstraint(condition=Q(row_number__gte=1), name='raw_row_positive')]
        indexes = [models.Index(fields=['snapshot', 'source_key']), models.Index(fields=['snapshot', 'disposition'])]

class QualityIssue(models.Model):
    revision = models.PositiveIntegerField(default=1, db_default=1)
    snapshot = models.ForeignKey(Snapshot, on_delete=models.PROTECT)
    record = models.ForeignKey(StagedRecord, null=True, blank=True, on_delete=models.PROTECT)
    code = models.CharField(max_length=60)
    severity = models.CharField(max_length=12, choices=[('info','说明'),('warning','警告'),('error','错误')])
    status = models.CharField(max_length=12, default='open', choices=[('open','待处理'),('resolved','已处理'),('accepted','保留限制')])
    details = models.JSONField(default=dict)
    resolution = models.TextField(blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['status', 'code'])]
        constraints = [models.CheckConstraint(condition=Q(status='open') | (Q(resolved_at__isnull=False, resolved_by__isnull=False) & ~Q(resolution='')), name='quality_resolution_required')]

class SourceEntity(models.Model):
    """Current business state, with immutable evidence referenced separately."""
    source = models.ForeignKey(DataSource, null=True, blank=True, on_delete=models.PROTECT)
    source_record_id = models.CharField(max_length=150, null=True, blank=True)
    raw_record = models.OneToOneField(StagedRecord, null=True, blank=True, on_delete=models.PROTECT)
    origin = models.CharField(max_length=10, default='official', choices=[('official','官方'),('manual','人工'),('demo','演示')])
    is_active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)
    review_note = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(fields=['source','source_record_id'], condition=Q(source__isnull=False, source_record_id__isnull=False), name='%(app_label)s_%(class)s_source_uniq'),
            models.CheckConstraint(condition=~Q(origin='official') | (Q(source__isnull=False, source_record_id__isnull=False, raw_record__isnull=False) & ~Q(source_record_id='')), name='%(app_label)s_%(class)s_official_ref'),
            models.CheckConstraint(condition=Q(revision__gte=1), name='%(app_label)s_%(class)s_revision'),
        ]

class ImportJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(DataSource, on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='import_jobs')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='requested_import_jobs')
    original_name = models.CharField(max_length=200)
    relative_path = models.CharField(max_length=400)
    file_size = models.PositiveBigIntegerField(default=0)
    checksum = models.CharField(max_length=64)
    status = models.CharField(max_length=20, default='queued_validation', choices=[
        ('queued_validation','等待文件校验'),('validating','正在校验'),('ready','校验完成，待导入'),
        ('queued_import','等待导入'),('running','正在导入'),('succeeded','导入完成'),('partial','完成，有待核查记录'),
        ('failed','执行失败'),('cancelled','已取消')])
    phase = models.CharField(max_length=12, default='validate')
    attempt = models.PositiveIntegerField(default=0)
    progress_rows = models.PositiveBigIntegerField(default=0)
    total_rows = models.PositiveBigIntegerField(null=True)
    summary = models.JSONField(default=dict)
    error_code = models.CharField(max_length=80, blank=True)
    manifest_path = models.CharField(max_length=400, blank=True)
    snapshot = models.ForeignKey(Snapshot, null=True, on_delete=models.PROTECT)
    batch = models.ForeignKey(ImportBatch, null=True, on_delete=models.PROTECT)
    worker_token = models.UUIDField(null=True)
    heartbeat_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status','created_at'])]
