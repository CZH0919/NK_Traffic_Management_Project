import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q, F


class ForecastRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    target = models.ForeignKey('analytics.AnalysisTarget', on_delete=models.PROTECT)
    rule = models.ForeignKey('analytics.RuleVersion', on_delete=models.PROTECT)
    network = models.ForeignKey('network.NetworkVersion', null=True, on_delete=models.PROTECT)
    date_from = models.DateField()
    date_to = models.DateField()
    training_end = models.DateField(null=True)
    frequency = models.CharField(max_length=8, choices=[('month', '月'), ('week', '周')])
    metric = models.CharField(max_length=12, choices=[('crashes', '事故数'), ('injured', '已报告受伤'), ('killed', '已报告死亡')])
    horizon = models.PositiveSmallIntegerField()
    conditions = models.JSONField()
    data_version = models.JSONField(default=dict)
    history = models.JSONField(default=list)
    summary = models.JSONField(default=dict)
    status = models.CharField(max_length=12, default='queued', choices=[('queued', '等待计算'), ('running', '正在计算'), ('succeeded', '计算完成'), ('failed', '计算失败')])
    error_code = models.CharField(max_length=80, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'created_at'])]
        constraints = [
            models.CheckConstraint(condition=Q(date_to__gte=F('date_from')), name='forecast_run_dates'),
            models.CheckConstraint(condition=Q(training_end__isnull=True) | Q(training_end__lte=F('date_to')), name='forecast_train_cutoff'),
            models.CheckConstraint(condition=Q(frequency='month', horizon__gte=1, horizon__lte=12) | Q(frequency='week', horizon__gte=1, horizon__lte=26), name='forecast_frequency_horizon'),
        ]


class ForecastPoint(models.Model):
    run = models.ForeignKey(ForecastRun, on_delete=models.PROTECT, related_name='points')
    step = models.PositiveSmallIntegerField()
    period_start = models.DateField()
    period_end = models.DateField()
    estimate = models.DecimalField(max_digits=16, decimal_places=4)
    lower = models.DecimalField(max_digits=16, decimal_places=4, null=True)
    upper = models.DecimalField(max_digits=16, decimal_places=4, null=True)
    interval_samples = models.PositiveSmallIntegerField(default=0)
    interval_method = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ['step']
        constraints = [
            models.UniqueConstraint(fields=['run', 'step'], name='forecast_point_unique'),
            models.CheckConstraint(condition=Q(step__gte=1, estimate__gte=0, period_end__gte=F('period_start')), name='forecast_point_valid'),
            models.CheckConstraint(condition=Q(lower__isnull=True, upper__isnull=True) | Q(lower__isnull=False, upper__isnull=False, lower__gte=0, lower__lte=F('estimate'), upper__gte=F('estimate')), name='forecast_band_valid'),
        ]


class BacktestFold(models.Model):
    run = models.ForeignKey(ForecastRun, on_delete=models.PROTECT, related_name='folds')
    phase = models.CharField(max_length=12, choices=[('validation', '参数比较'), ('evaluation', '独立评估')])
    method = models.CharField(max_length=32)
    origin_date = models.DateField()
    train_end = models.DateField()
    period_start = models.DateField()
    period_end = models.DateField()
    step = models.PositiveSmallIntegerField()
    actual = models.DecimalField(max_digits=16, decimal_places=4)
    predicted = models.DecimalField(max_digits=16, decimal_places=4)
    baseline = models.DecimalField(max_digits=16, decimal_places=4)
    absolute_error = models.DecimalField(max_digits=16, decimal_places=4)

    class Meta:
        ordering = ['period_start', 'step', 'method']
        indexes = [models.Index(fields=['run', 'phase', 'step'])]
        constraints = [
            models.UniqueConstraint(fields=['run', 'phase', 'method', 'origin_date', 'step'], name='forecast_fold_unique'),
            models.CheckConstraint(condition=Q(train_end__lte=F('origin_date'), origin_date__lt=F('period_start'), period_start__lte=F('period_end')), name='forecast_fold_no_future'),
            models.CheckConstraint(condition=Q(step__gte=1, actual__gte=0, predicted__gte=0, baseline__gte=0, absolute_error__gte=0), name='forecast_fold_nonnegative'),
        ]


class AnomalyResult(models.Model):
    run = models.ForeignKey(ForecastRun, on_delete=models.PROTECT, related_name='anomalies')
    period_start = models.DateField()
    period_end = models.DateField()
    observed = models.DecimalField(max_digits=16, decimal_places=4, null=True)
    baseline = models.DecimalField(max_digits=16, decimal_places=4, null=True)
    lower = models.DecimalField(max_digits=16, decimal_places=4, null=True)
    upper = models.DecimalField(max_digits=16, decimal_places=4, null=True)
    reference_from = models.DateField(null=True)
    reference_to = models.DateField(null=True)
    reference_count = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=14, choices=[('high', '高于历史阈值'), ('low', '低于历史阈值'), ('normal', '阈值内'), ('insufficient', '不可判断')])
    reason = models.CharField(max_length=240)

    class Meta:
        ordering = ['-period_start']
        constraints = [
            models.UniqueConstraint(fields=['run', 'period_start'], name='forecast_anomaly_unique'),
            models.CheckConstraint(condition=Q(period_end__gte=F('period_start')), name='forecast_anomaly_period'),
            models.CheckConstraint(condition=Q(reference_to__isnull=True) | Q(reference_to__lt=F('period_start')), name='forecast_anomaly_no_future'),
        ]
