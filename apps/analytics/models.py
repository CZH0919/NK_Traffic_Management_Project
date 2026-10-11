import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q,F

LEVELS=[('city','全市'),('borough','行政区'),('road','道路'),('junction','路口')]

class AnalysisTarget(models.Model):
    scope=models.CharField(max_length=12,choices=LEVELS)
    borough=models.ForeignKey('core.Borough',null=True,blank=True,on_delete=models.PROTECT)
    road=models.ForeignKey('network.RoadSegment',null=True,blank=True,on_delete=models.PROTECT)
    junction=models.ForeignKey('network.Junction',null=True,blank=True,on_delete=models.PROTECT)
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(scope='city',borough__isnull=True,road__isnull=True,junction__isnull=True)|Q(scope='borough',borough__isnull=False,road__isnull=True,junction__isnull=True)|Q(scope='road',road__isnull=False,borough__isnull=True,junction__isnull=True)|Q(scope='junction',junction__isnull=False,borough__isnull=True,road__isnull=True),name='analysis_target_kind'),
            models.UniqueConstraint(fields=['scope'],condition=Q(scope='city'),name='analysis_city_single'),
            *[models.UniqueConstraint(fields=[key],condition=Q(**{key+'__isnull':False}),name='analysis_'+key+'_unique') for key in ['borough','road','junction']]]

class RuleVersion(models.Model):
    code=models.CharField(max_length=50,default='historical-intensity')
    version=models.PositiveIntegerField()
    name=models.CharField(max_length=120)
    parameters=models.JSONField()
    definition=models.TextField()
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['code','version'],name='analysis_rule_version_unique'),models.CheckConstraint(condition=Q(version__gte=1),name='analysis_rule_positive')]
        ordering=['-id']
    def __str__(self):return f'{self.name} · v{self.version}'

class RiskRun(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    requested_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    rule=models.ForeignKey(RuleVersion,on_delete=models.PROTECT)
    network=models.ForeignKey('network.NetworkVersion',null=True,on_delete=models.PROTECT)
    level=models.CharField(max_length=12,choices=LEVELS)
    date_from=models.DateField()
    date_to=models.DateField()
    conditions=models.JSONField()
    data_version=models.JSONField(default=dict,blank=True)
    summary=models.JSONField(default=dict,blank=True)
    status=models.CharField(max_length=12,default='queued',choices=[('queued','等待计算'),('running','正在计算'),('succeeded','计算完成'),('failed','计算失败')])
    error_code=models.CharField(max_length=80,blank=True)
    attempts=models.PositiveSmallIntegerField(default=0)
    created_at=models.DateTimeField(auto_now_add=True)
    started_at=models.DateTimeField(null=True)
    finished_at=models.DateTimeField(null=True)
    class Meta:
        ordering=['-created_at']
        constraints=[models.CheckConstraint(condition=Q(date_to__gte=F('date_from')),name='risk_run_period')]
        indexes=[models.Index(fields=['status','created_at'])]

class RiskResult(models.Model):
    run=models.ForeignKey(RiskRun,on_delete=models.PROTECT,related_name='results')
    target=models.ForeignKey(AnalysisTarget,on_delete=models.PROTECT)
    label=models.CharField(max_length=240)
    counts=models.JSONField()
    components=models.JSONField(default=dict)
    score=models.DecimalField(max_digits=6,decimal_places=3,null=True)
    grade=models.CharField(max_length=20,choices=[('lower','相对较低'),('moderate','中等'),('high','较高'),('very_high','很高'),('insufficient','不分级')])
    rank=models.PositiveIntegerField(null=True)
    limitation=models.TextField(blank=True)
    point=models.JSONField(null=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['run','target'],name='risk_result_run_target'),models.CheckConstraint(condition=Q(score__isnull=True)|Q(score__gte=0,score__lte=100),name='risk_score_range')]
        indexes=[models.Index(fields=['run','-score']),models.Index(fields=['run','grade'])]

class AggregateCache(models.Model):
    query_key=models.CharField(max_length=64,unique=True)
    data_token=models.CharField(max_length=64)
    payload=models.JSONField()
    created_at=models.DateTimeField(auto_now=True)
    expires_at=models.DateTimeField()
