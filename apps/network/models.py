from django.contrib.postgres.indexes import GistIndex
from django.db import models
from django.conf import settings
import uuid
from apps.core.fields import GeometryField

class NetworkVersion(models.Model):
    snapshot = models.OneToOneField('ingestion.Snapshot', on_delete=models.PROTECT)
    node_snapshot = models.ForeignKey('ingestion.Snapshot', null=True, blank=True, on_delete=models.PROTECT, related_name='node_networks')
    label = models.CharField(max_length=100)
    native_crs = models.CharField(max_length=80)
    description = models.TextField(blank=True)

    def __str__(self):return self.label

class Junction(models.Model):
    network = models.ForeignKey(NetworkVersion, on_delete=models.PROTECT)
    source_node_id = models.CharField(max_length=40)
    name = models.CharField(max_length=200, blank=True)
    is_intersection = models.BooleanField(null=True, blank=True)
    classification_method = models.CharField(max_length=100, blank=True)
    geom = GeometryField()
    borough = models.ForeignKey('core.Borough',null=True,blank=True,on_delete=models.PROTECT)
    virtual_intersection = models.BooleanField(default=False)
    road_degree = models.PositiveIntegerField(default=0)
    street_names = models.JSONField(default=list,blank=True)
    classification_evidence = models.JSONField(default=dict,blank=True)
    display_name = models.CharField(max_length=200,blank=True)
    review_note = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['network', 'source_node_id'], name='junction_version_node_unique')]
        indexes = [GistIndex(fields=['geom'], name='junction_geom_gist')]

class RoadSegment(models.Model):
    network = models.ForeignKey(NetworkVersion, on_delete=models.PROTECT)
    source_feature_id = models.CharField(max_length=60)
    source_segment_id = models.CharField(max_length=40, db_index=True)
    name = models.CharField(max_length=200)
    feature_type = models.CharField(max_length=20, blank=True)
    traffic_direction = models.CharField(max_length=20, blank=True)
    from_node = models.ForeignKey(Junction, null=True, blank=True, on_delete=models.PROTECT, related_name='outgoing_segments')
    to_node = models.ForeignKey(Junction, null=True, blank=True, on_delete=models.PROTECT, related_name='incoming_segments')
    left_borough = models.ForeignKey('core.Borough', null=True, blank=True, on_delete=models.PROTECT, related_name='left_roads')
    right_borough = models.ForeignKey('core.Borough', null=True, blank=True, on_delete=models.PROTECT, related_name='right_roads')
    geom = GeometryField(geometry_type='MULTILINESTRING')
    length_m = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    canonical = models.ForeignKey('self',null=True,blank=True,on_delete=models.PROTECT,related_name='source_variants')
    is_canonical = models.BooleanField(default=False)
    match_eligible = models.BooleanField(default=False)
    normalized_segment_id = models.CharField(max_length=40,blank=True,db_index=True)
    rb_layer = models.CharField(max_length=1,blank=True)
    segment_type = models.CharField(max_length=1,blank=True)
    level_from = models.CharField(max_length=1,blank=True)
    level_to = models.CharField(max_length=1,blank=True)
    road_type = models.CharField(max_length=4,blank=True)
    bearing_deg = models.FloatField(null=True,blank=True)
    source_variants_count = models.PositiveIntegerField(default=1)
    geometry_variants = models.PositiveIntegerField(default=1)
    aliases = models.JSONField(default=list,blank=True)
    classification_evidence = models.JSONField(default=dict,blank=True)
    display_name = models.CharField(max_length=200,blank=True)
    review_note = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['network', 'source_feature_id'], name='road_version_feature_unique'), models.CheckConstraint(condition=models.Q(length_m__isnull=True) | models.Q(length_m__gte=0), name='road_length_nonnegative')]
        indexes = [GistIndex(fields=['geom'], name='road_geom_gist'), models.Index(fields=['network', 'name']),
            GistIndex(fields=['geom'],condition=models.Q(is_canonical=True,match_eligible=True,is_active=True),name='road_match_geom_gist')]

MATCH_STATUSES=[('matched','已关联'),('ambiguous','待核查'),('unmatched','未匹配'),('missing','缺少位置')]

class SpatialRun(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    network=models.ForeignKey(NetworkVersion,on_delete=models.PROTECT)
    rule_version=models.CharField(max_length=60)
    parameters=models.JSONField(default=dict)
    status=models.CharField(max_length=15,default='running',choices=[('running','执行中'),('succeeded','完成'),('failed','失败')])
    phase=models.CharField(max_length=30,default='prepare')
    checkpoint=models.PositiveBigIntegerField(default=0)
    summary=models.JSONField(default=dict,blank=True)
    started_at=models.DateTimeField(auto_now_add=True)
    finished_at=models.DateTimeField(null=True)
    error_code=models.CharField(max_length=80,blank=True)

    class Meta:
        ordering=['-started_at']

class SpatialMatch(models.Model):
    collision=models.ForeignKey('collisions.Collision',on_delete=models.PROTECT,related_name='spatial_matches')
    network=models.ForeignKey(NetworkVersion,on_delete=models.PROTECT)
    road=models.ForeignKey(RoadSegment,null=True,blank=True,on_delete=models.PROTECT,related_name='collision_matches')
    junction=models.ForeignKey(Junction,null=True,blank=True,on_delete=models.PROTECT,related_name='collision_matches')
    status=models.CharField(max_length=15,choices=MATCH_STATUSES)
    method=models.CharField(max_length=60)
    rule_version=models.CharField(max_length=60)
    distance_m=models.DecimalField(max_digits=12,decimal_places=3,null=True,blank=True)
    input_revision=models.PositiveIntegerField()
    candidates=models.JSONField(default=list,blank=True)
    evidence=models.JSONField(default=dict,blank=True)
    revision=models.PositiveIntegerField(default=1)
    reviewed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT)
    reviewed_at=models.DateTimeField(null=True,blank=True)
    updated_at=models.DateTimeField(auto_now=True)
    run=models.ForeignKey(SpatialRun,null=True,blank=True,on_delete=models.PROTECT)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['collision','network'],name='spatial_match_collision_network'),
            models.CheckConstraint(condition=(models.Q(status='matched') & ((models.Q(road__isnull=False,junction__isnull=True))|models.Q(road__isnull=True,junction__isnull=False))) | (~models.Q(status='matched') & models.Q(road__isnull=True,junction__isnull=True)),name='spatial_match_target_state'),
            models.CheckConstraint(condition=models.Q(distance_m__isnull=True)|models.Q(distance_m__gte=0),name='spatial_match_distance_nonnegative')]
        indexes=[models.Index(fields=['network','status']),models.Index(fields=['road','status']),models.Index(fields=['junction','status'])]

class SamplingRoadMatch(models.Model):
    site=models.ForeignKey('traffic.SamplingSite',on_delete=models.PROTECT,related_name='road_matches')
    network=models.ForeignKey(NetworkVersion,on_delete=models.PROTECT)
    road=models.ForeignKey(RoadSegment,null=True,blank=True,on_delete=models.PROTECT,related_name='sampling_matches')
    status=models.CharField(max_length=15,choices=MATCH_STATUSES)
    method=models.CharField(max_length=60)
    rule_version=models.CharField(max_length=60)
    distance_m=models.DecimalField(max_digits=12,decimal_places=3,null=True,blank=True)
    source_id_match=models.BooleanField(default=False)
    direction_compatible=models.BooleanField(null=True,blank=True)
    input_revision=models.PositiveIntegerField(default=1)
    candidates=models.JSONField(default=list,blank=True)
    evidence=models.JSONField(default=dict,blank=True)
    revision=models.PositiveIntegerField(default=1)
    reviewed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT)
    reviewed_at=models.DateTimeField(null=True,blank=True)
    updated_at=models.DateTimeField(auto_now=True)
    run=models.ForeignKey(SpatialRun,null=True,blank=True,on_delete=models.PROTECT)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['site','network'],name='sampling_match_site_network'),
            models.CheckConstraint(condition=models.Q(status='matched',road__isnull=False)|(~models.Q(status='matched')&models.Q(road__isnull=True)),name='sampling_match_target_state'),
            models.CheckConstraint(condition=models.Q(distance_m__isnull=True)|models.Q(distance_m__gte=0),name='sampling_match_distance_nonnegative')]
        indexes=[models.Index(fields=['network','status'])]
