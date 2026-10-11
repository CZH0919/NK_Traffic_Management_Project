from django.contrib.postgres.indexes import GistIndex
from django.db import models
from apps.core.fields import GeometryField
from apps.ingestion.models import SourceEntity

TIME_STATUS = [('unique','可唯一转换'),('ambiguous','重复小时'),('nonexistent','不存在的当地时间'),('missing','时间缺失')]

class Collision(SourceEntity):
    local_date = models.DateField()
    local_time = models.TimeField(null=True, blank=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    time_status = models.CharField(max_length=12, choices=TIME_STATUS, default='missing')
    borough = models.ForeignKey('core.Borough', null=True, blank=True, on_delete=models.PROTECT)
    borough_method = models.CharField(max_length=12, default='unknown', choices=[('reported','来源报告'),('spatial','空间推导'),('manual','人工修正'),('unknown','未知')])
    postal_code = models.CharField(max_length=20, blank=True)
    on_street = models.CharField(max_length=200, blank=True)
    off_street = models.CharField(max_length=200, blank=True)
    cross_street = models.CharField(max_length=200, blank=True)
    geom = GeometryField(null=True, blank=True)
    persons_injured = models.PositiveIntegerField(null=True, blank=True)
    persons_killed = models.PositiveIntegerField(null=True, blank=True)
    pedestrians_injured = models.PositiveIntegerField(null=True, blank=True)
    pedestrians_killed = models.PositiveIntegerField(null=True, blank=True)
    cyclists_injured = models.PositiveIntegerField(null=True, blank=True)
    cyclists_killed = models.PositiveIntegerField(null=True, blank=True)
    motorists_injured = models.PositiveIntegerField(null=True, blank=True)
    motorists_killed = models.PositiveIntegerField(null=True, blank=True)

    class Meta(SourceEntity.Meta):
        abstract = False
        indexes = [models.Index(fields=['local_date', 'id']), models.Index(fields=['borough','local_date']), GistIndex(fields=['geom'], name='collision_geom_gist')]
        constraints = SourceEntity.Meta.constraints + [models.CheckConstraint(condition=(models.Q(time_status='unique', occurred_at__isnull=False, local_time__isnull=False) | (~models.Q(time_status='unique') & models.Q(occurred_at__isnull=True))), name='collision_time_consistent')]

class Vehicle(SourceEntity):
    collision = models.ForeignKey(Collision, null=True, blank=True, on_delete=models.PROTECT, related_name='vehicles')
    source_collision_id = models.CharField(max_length=80, blank=True)
    source_vehicle_uuid = models.CharField(max_length=100, blank=True)
    vehicle_type = models.CharField(max_length=100, blank=True, db_index=True)
    make = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    manufacture_year = models.PositiveSmallIntegerField(null=True, blank=True)
    state_registration = models.CharField(max_length=30, blank=True)
    occupants = models.PositiveIntegerField(null=True, blank=True)
    travel_direction = models.CharField(max_length=40, blank=True)
    pre_crash = models.CharField(max_length=120, blank=True)
    driver_sex = models.CharField(max_length=30, blank=True)
    license_status = models.CharField(max_length=60, blank=True)
    license_jurisdiction = models.CharField(max_length=60, blank=True)
    point_of_impact = models.CharField(max_length=120, blank=True)
    public_property_damage = models.CharField(max_length=30, blank=True)
    public_property_damage_type = models.TextField(blank=True)

    class Meta(SourceEntity.Meta):
        abstract = False
        constraints = SourceEntity.Meta.constraints + [models.UniqueConstraint(fields=['id','collision'], name='vehicle_id_collision_pair')]

class Person(SourceEntity):
    collision = models.ForeignKey(Collision, null=True, blank=True, on_delete=models.PROTECT, related_name='persons')
    vehicle = models.ForeignKey(Vehicle, null=True, blank=True, on_delete=models.PROTECT, related_name='persons')
    source_collision_id = models.CharField(max_length=80, blank=True)
    source_vehicle_id = models.CharField(max_length=100, blank=True)
    source_person_uuid = models.CharField(max_length=100, blank=True)
    person_type = models.CharField(max_length=60, blank=True, db_index=True)
    injury = models.CharField(max_length=60, blank=True, db_index=True)
    age = models.PositiveSmallIntegerField(null=True, blank=True)
    sex = models.CharField(max_length=30, blank=True)
    ejection = models.CharField(max_length=100, blank=True)
    emotional_status = models.CharField(max_length=100, blank=True)
    bodily_injury = models.CharField(max_length=100, blank=True)
    position_in_vehicle = models.TextField(blank=True)
    safety_equipment = models.CharField(max_length=150, blank=True)
    pedestrian_location = models.CharField(max_length=120, blank=True)
    pedestrian_action = models.CharField(max_length=150, blank=True)
    pedestrian_role = models.CharField(max_length=80, blank=True)
    complaint = models.CharField(max_length=150, blank=True)

    class Meta(SourceEntity.Meta):
        abstract = False
        constraints = SourceEntity.Meta.constraints + [
            models.CheckConstraint(condition=models.Q(age__isnull=True) | models.Q(age__lte=130), name='person_age_plausible'),
            models.CheckConstraint(condition=models.Q(vehicle__isnull=True) | models.Q(collision__isnull=False), name='person_vehicle_requires_collision')]

class Factor(models.Model):
    name = models.CharField(max_length=160, unique=True)
    is_active = models.BooleanField(default=True)

class CollisionFactor(models.Model):
    collision = models.ForeignKey(Collision, on_delete=models.PROTECT)
    source_slot = models.PositiveSmallIntegerField()
    factor = models.ForeignKey(Factor, null=True, blank=True, on_delete=models.PROTECT)
    vehicle_type = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['collision','source_slot'], name='collision_factor_slot_unique'), models.CheckConstraint(condition=models.Q(source_slot__gte=1,source_slot__lte=5), name='collision_factor_slot_range')]

class VehicleFactor(models.Model):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT)
    source_slot = models.PositiveSmallIntegerField()
    factor = models.ForeignKey(Factor, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['vehicle','source_slot'], name='vehicle_factor_slot_unique'), models.CheckConstraint(condition=models.Q(source_slot__gte=1,source_slot__lte=2), name='vehicle_factor_slot_range')]

class PersonFactor(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT)
    source_slot = models.PositiveSmallIntegerField()
    factor = models.ForeignKey(Factor, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['person','source_slot'], name='person_factor_slot_unique'), models.CheckConstraint(condition=models.Q(source_slot__gte=1,source_slot__lte=2), name='person_factor_slot_range')]

class VehicleDamage(models.Model):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT)
    source_slot = models.PositiveSmallIntegerField()
    description = models.CharField(max_length=160)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['vehicle','source_slot'], name='vehicle_damage_slot_unique'), models.CheckConstraint(condition=models.Q(source_slot__gte=0,source_slot__lte=3), name='vehicle_damage_slot_range')]
