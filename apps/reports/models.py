import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q

STATES = [('queued', '等待处理'), ('running', '正在处理'), ('succeeded', '已完成'), ('failed', '处理失败')]
KINDS = [('statistics', '综合统计'), ('comparison', '区域与期间对比'), ('risk', '历史风险评价'), ('forecast', '统计预测与回测'), ('flow', '采样流量分析')]


class Report(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    title = models.CharField(max_length=180)
    kind = models.CharField(max_length=20, choices=KINDS)
    conditions = models.JSONField(default=dict)
    risk_run = models.ForeignKey('analytics.RiskRun', null=True, on_delete=models.PROTECT)
    forecast_run = models.ForeignKey('forecasting.ForecastRun', null=True, on_delete=models.PROTECT)
    data_version = models.JSONField(default=dict)
    payload = models.JSONField(default=dict)
    template_version = models.CharField(max_length=24, default='report-v1')
    status = models.CharField(max_length=12, choices=STATES, default='queued')
    error_code = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['owner', '-created_at']), models.Index(fields=['status', 'created_at'])]
        constraints = [models.CheckConstraint(condition=Q(kind='risk', risk_run__isnull=False, forecast_run__isnull=True) | Q(kind='forecast', forecast_run__isnull=False, risk_run__isnull=True) | Q(kind__in=['statistics','comparison','flow'],risk_run__isnull=True,forecast_run__isnull=True), name='report_source_kind')]


class ReportSnapshot(models.Model):
    report = models.ForeignKey(Report, on_delete=models.PROTECT, related_name='sources')
    snapshot = models.ForeignKey('ingestion.Snapshot', on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['report','snapshot'], name='report_source_unique')]


class ExportJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    kind = models.CharField(max_length=16, choices=[('crashes','事故'),('vehicles','车辆'),('persons','人员'),('observations','流量观测'),('report','报告数据')])
    format = models.CharField(max_length=8, choices=[('csv','CSV 数据包'),('xlsx','Excel')])
    report = models.ForeignKey(Report, null=True, on_delete=models.PROTECT)
    conditions = models.JSONField(default=dict)
    metadata = models.JSONField(default=dict)
    row_count = models.PositiveBigIntegerField(default=0)
    file_size = models.PositiveBigIntegerField(default=0)
    relative_path = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=12, choices=STATES, default='queued')
    error_code = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['owner','-created_at']),models.Index(fields=['status','created_at'])]
        constraints = [models.CheckConstraint(condition=Q(kind='report',report__isnull=False)|Q(kind__in=['crashes','vehicles','persons','observations'],report__isnull=True),name='export_report_kind'),models.CheckConstraint(condition=Q(format__in=['csv','xlsx']),name='export_format')]


class AIBrief(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(Report, on_delete=models.PROTECT, related_name='briefs')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    provider = models.CharField(max_length=40)
    model = models.CharField(max_length=80)
    endpoint = models.URLField()
    prompt_version = models.CharField(max_length=32)
    fingerprint = models.CharField(max_length=64)
    input_summary = models.JSONField()
    raw_text = models.TextField(blank=True)
    generated_text = models.TextField(blank=True)
    edited_text = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, choices=STATES, default='queued')
    error_code = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    edited_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['owner','fingerprint']),models.Index(fields=['status','created_at'])]


class AIRequest(models.Model):
    brief = models.ForeignKey(AIBrief, on_delete=models.PROTECT, related_name='requests')
    attempt = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=12, choices=STATES, default='queued')
    error_code = models.CharField(max_length=50, blank=True)
    http_status = models.PositiveSmallIntegerField(null=True)
    prompt_tokens = models.PositiveIntegerField(null=True)
    completion_tokens = models.PositiveIntegerField(null=True)
    elapsed_ms = models.PositiveIntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ['attempt']
        constraints = [models.UniqueConstraint(fields=['brief','attempt'],name='ai_attempt_unique'),models.CheckConstraint(condition=Q(attempt__gte=1),name='ai_attempt_positive')]


class BriefRevision(models.Model):
    brief = models.ForeignKey(AIBrief, on_delete=models.PROTECT, related_name='edits')
    number = models.PositiveIntegerField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['number']
        constraints = [models.UniqueConstraint(fields=['brief','number'],name='brief_edit_unique'),models.CheckConstraint(condition=Q(number__gte=1),name='brief_edit_positive')]
