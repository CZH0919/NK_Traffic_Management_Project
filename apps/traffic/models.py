from django.contrib.postgres.indexes import GistIndex
from django.db import models
from apps.core.fields import GeometryField
from apps.ingestion.models import SourceEntity
from apps.collisions.models import TIME_STATUS

class SamplingRequest(models.Model):
    source = models.ForeignKey('ingestion.DataSource', on_delete=models.PROTECT)
    source_request_id = models.CharField(max_length=80)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['source','source_request_id'], name='sampling_request_unique')]

class SamplingSite(models.Model):
    request = models.ForeignKey(SamplingRequest, on_delete=models.PROTECT)
    location_key = models.CharField(max_length=64)
    source_segment_id = models.CharField(max_length=40, db_index=True)
    source_network_version = models.CharField(max_length=60, default='LION version 14 (source metadata)')
    direction = models.CharField(max_length=30)
    street = models.CharField(max_length=200, blank=True)
    from_street = models.CharField(max_length=200, blank=True)
    to_street = models.CharField(max_length=200, blank=True)
    borough = models.ForeignKey('core.Borough', null=True, blank=True, on_delete=models.PROTECT)
    geom = GeometryField(null=True, blank=True)
    display_name = models.CharField(max_length=200,blank=True)
    review_note = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['request','location_key'], name='site_request_location_unique')]
        indexes = [GistIndex(fields=['geom'], name='site_geom_gist')]

class FlowConflict(models.Model):
    site = models.ForeignKey(SamplingSite, on_delete=models.PROTECT)
    local_date = models.DateField()
    local_time = models.TimeField()
    status = models.CharField(max_length=15, default='unresolved', choices=[('unresolved','未解决'),('resolved','已核查')])
    resolution = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['site','local_date','local_time'], name='flow_conflict_group_unique')]

class TrafficObservation(SourceEntity):
    site = models.ForeignKey(SamplingSite, on_delete=models.PROTECT)
    local_date = models.DateField()
    local_time = models.TimeField()
    observed_at = models.DateTimeField(null=True, blank=True)
    time_status = models.CharField(max_length=12, default='missing', choices=TIME_STATUS)
    volume = models.PositiveIntegerField()
    conflict = models.ForeignKey(FlowConflict, null=True, blank=True, on_delete=models.PROTECT)
    quality_status = models.CharField(max_length=15, default='pending', choices=[('pending','待核查'),('valid','有效'),('conflict','冲突'),('excluded','已排除')])

    class Meta(SourceEntity.Meta):
        abstract = False
        indexes = [models.Index(fields=['site','local_date','local_time']), models.Index(fields=['quality_status','local_date'])]
        constraints = SourceEntity.Meta.constraints + [
            models.CheckConstraint(condition=(models.Q(time_status='unique', observed_at__isnull=False) | (~models.Q(time_status='unique') & models.Q(observed_at__isnull=True))), name='flow_time_consistent'),
            models.CheckConstraint(condition=~models.Q(quality_status='valid') | models.Q(conflict__isnull=True), name='flow_conflict_not_valid'),
        ]
