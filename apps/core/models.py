import uuid
from django.conf import settings
from django.contrib.postgres.indexes import GistIndex
from django.db import models
from .fields import GeometryField

class AccessPolicy(models.Model):
    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ('view_workspace', '查看工作台'), ('view_sources', '查看来源登记'),
            ('view_batches', '查看导入批次'), ('maintain_data', '维护业务数据'),
            ('manage_users', '管理账户权限'), ('view_audit', '查看全局操作记录'),
            ('manage_system', '管理运行配置'), ('analyze_data', '查询与分析数据'),
            ('generate_reports', '生成分析报告'),
        ]


class MaintenanceRun(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    operation=models.CharField(max_length=12,choices=[('backup','维护备份'),('restore','隔离恢复')])
    source_run=models.ForeignKey('self',null=True,on_delete=models.PROTECT)
    target_label=models.CharField(max_length=63)
    artifact_relative_path=models.CharField(max_length=160,blank=True)
    status=models.CharField(max_length=12,default='running',choices=[('running','正在执行'),('succeeded','核验通过'),('failed','执行失败')])
    summary=models.JSONField(default=dict)
    error_code=models.CharField(max_length=50,blank=True)
    started_at=models.DateTimeField(auto_now_add=True)
    finished_at=models.DateTimeField(null=True)

    class Meta:
        ordering=['-started_at']
        indexes=[models.Index(fields=['operation','status','-started_at'])]
        constraints=[models.CheckConstraint(condition=models.Q(operation='backup',source_run__isnull=True)|models.Q(operation='restore',source_run__isnull=False),name='maintenance_source_kind')]

class Borough(models.Model):
    code = models.PositiveSmallIntegerField(primary_key=True)
    name = models.CharField(max_length=32, unique=True)
    chinese_name = models.CharField(max_length=32)
    geom = GeometryField(geometry_type='MULTIPOLYGON', null=True, blank=True)
    boundary_snapshot = models.ForeignKey('ingestion.Snapshot', null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        ordering = ['code']
        constraints = [models.CheckConstraint(condition=models.Q(code__gte=1, code__lte=5), name='borough_code_1_5')]
        indexes = [GistIndex(fields=['geom'], name='borough_geom_gist')]

    def __str__(self):
        return self.chinese_name

class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    actor_label = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=50)
    object_type = models.CharField(max_length=50)
    object_id = models.CharField(max_length=150, blank=True)
    outcome = models.CharField(max_length=12, choices=[('success', '成功'), ('failure', '失败')])
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['actor', '-created_at']), models.Index(fields=['object_type', 'object_id'])]

class FieldChange(models.Model):
    event = models.ForeignKey(AuditEvent, on_delete=models.PROTECT)
    field_name = models.CharField(max_length=80)
    previous_value = models.JSONField(null=True)
    new_value = models.JSONField(null=True)
    reason = models.TextField()

    class Meta:
        constraints = [models.CheckConstraint(condition=~models.Q(reason=''), name='change_reason_required')]

class SavedQuery(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=[('crashes','事故'),('vehicles','车辆'),('persons','人员'),('statistics','综合统计'),('comparison','统计对比'),('risk','历史风险'),('forecast','统计预测')])
    schema_version = models.PositiveSmallIntegerField(default=1)
    conditions = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['owner','name'], condition=models.Q(is_active=True), name='saved_query_owner_name_unique')]
        ordering = ['-updated_at','-id']

class CategoryMapping(models.Model):
    kind = models.CharField(max_length=30, choices=[('vehicle_type','车辆类型'),('person_type','人员类型'),('injury','伤害类别'),('factor','事故因素')])
    raw_value = models.CharField(max_length=160)
    canonical_code = models.SlugField(max_length=50)
    canonical_label = models.CharField(max_length=120)
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['kind','raw_value','version'], name='category_mapping_version_unique'),
            models.UniqueConstraint(fields=['kind','raw_value'], condition=models.Q(is_active=True), name='category_mapping_current_unique')]
        indexes = [models.Index(fields=['kind','canonical_code'])]
        ordering = ['kind','raw_value','-version']

class DataRevision(models.Model):
    event = models.OneToOneField(AuditEvent, on_delete=models.PROTECT)
    domain = models.CharField(max_length=30)
    snapshot_ids = models.JSONField(default=list)
    committed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
