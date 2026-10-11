from datetime import date, time
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction, IntegrityError, DatabaseError
from django.db.models.deletion import ProtectedError
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from apps.core.audit import record_event
from apps.core.models import AuditEvent
from apps.core.testing import assert_test_name, IsolatedTestRunner
from apps.core.time_rules import resolve_wall_time
from apps.ingestion.models import DataSource, Snapshot, ImportBatch, StagedRecord, QualityIssue
from apps.collisions.models import Collision, Vehicle, Person
from apps.traffic.models import SamplingRequest, SamplingSite, TrafficObservation, FlowConflict

class ClockRulesTests(SimpleTestCase):
    def test_winter_and_summer_offsets(self):
        winter, status = resolve_wall_time(date(2025,1,1), time(12))
        summer, _ = resolve_wall_time(date(2025,7,1), time(12))
        self.assertEqual((winter.hour, summer.hour, status), (17,16,'unique'))

    def test_fall_hour_is_not_arbitrarily_disambiguated(self):
        self.assertEqual(resolve_wall_time(date(2025,11,2),time(1,30)), (None,'ambiguous'))

    def test_spring_gap_is_not_shifted(self):
        self.assertEqual(resolve_wall_time(date(2025,3,9),time(2,30)), (None,'nonexistent'))

    def test_missing_time_is_not_midnight(self):
        self.assertEqual(resolve_wall_time(date(2025,1,1),None), (None,'missing'))

    def test_database_name_guard(self):
        for name in (settings.BUSINESS_DATABASE_NAME, 'nk_traffic', 'postgres'):
            with self.assertRaises(ImproperlyConfigured):
                assert_test_name(name)
        assert_test_name('test_nk_traffic')

    def test_django_runner_requires_keepdb(self):
        with self.assertRaises(ImproperlyConfigured):
            IsolatedTestRunner(keepdb=False).setup_databases()

class FoundationDatabaseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source = DataSource.objects.get(code='nyc-crashes')
        cls.snapshot = Snapshot.objects.create(source=cls.source, version='test-only', captured_at=timezone.now(),relative_path='data/raw/test.csv')
        cls.raw = StagedRecord.objects.create(snapshot=cls.snapshot,row_number=1,source_key='900001',payload={'collision_id':'900001'})
        cls.collision = Collision.objects.create(source=cls.source,source_record_id='900001',raw_record=cls.raw,local_date=date(2025,1,1),persons_injured=0,geom='SRID=4326;POINT(-73.9857 40.7484)')

    def test_test_database_and_nonprivileged_connection(self):
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_database(), rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user')
            self.assertEqual(cursor.fetchone(), ('test_nk_traffic',False,False,False))
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname='postgis'")
            self.assertTrue(cursor.fetchone()[0].startswith('3.6'))

    def test_migrations_include_foreign_keys_and_spatial_index(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT indexdef FROM pg_indexes WHERE indexname='collision_geom_gist'")
            self.assertIn('USING gist',cursor.fetchone()[0])
            cursor.execute("SELECT count(*) FROM pg_constraint WHERE conname='person_vehicle_same_collision'")
            self.assertEqual(cursor.fetchone()[0],1)

    def test_geometry_roundtrip_and_metres(self):
        self.collision.refresh_from_db()
        self.assertTrue(self.collision.geom.startswith('SRID=4326;POINT('))
        with connection.cursor() as cursor:
            cursor.execute('SELECT ST_SRID(geom), ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography) FROM collisions_collision WHERE id=%s',(-73.9857,40.7494,self.collision.pk))
            srid, distance = cursor.fetchone()
            self.assertEqual(srid,4326)
            self.assertTrue(110 < distance < 112)

    def test_wrong_srid_rejected(self):
        with self.assertRaises(DatabaseError), transaction.atomic():
            Collision.objects.create(origin='demo',local_date=date(2025,1,1),geom='SRID=2263;POINT(1000000 200000)')

    def test_coordinate_outside_world_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Collision.objects.create(origin='demo',local_date=date(2025,1,1),geom='SRID=4326;POINT(500 95)')

    def test_official_record_requires_evidence(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Collision.objects.create(local_date=date(2025,1,1))

    def test_source_key_unique_across_snapshots(self):
        raw2 = StagedRecord.objects.create(snapshot=self.snapshot,row_number=2,payload={})
        with self.assertRaises(IntegrityError), transaction.atomic():
            Collision.objects.create(source=self.source,source_record_id='900001',raw_record=raw2,local_date=date(2025,1,1))

    def test_missing_position_and_counts_preserved(self):
        row=Collision.objects.create(origin='demo',local_date=date(2025,1,2))
        self.assertIsNone(row.geom)
        self.assertIsNone(row.persons_injured)
        self.assertEqual(self.collision.persons_injured,0)

    def test_negative_injury_count_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Collision.objects.create(origin='demo',local_date=date(2025,1,1),persons_injured=-1)

    def test_ambiguous_time_has_no_invented_utc(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Collision.objects.create(origin='demo',local_date=date(2025,11,2),local_time=time(1,30),time_status='ambiguous',occurred_at=timezone.now())

    def test_nullable_orphan_relationship_preserves_source_key(self):
        vehicle=Vehicle.objects.create(origin='demo',source_collision_id='missing-parent')
        self.assertIsNone(vehicle.collision)
        self.assertEqual(vehicle.source_collision_id,'missing-parent')

    def test_relationship_prevents_deleting_parent(self):
        Vehicle.objects.create(origin='demo',collision=self.collision)
        with self.assertRaises(ProtectedError):
            self.collision.delete()

    def test_person_cannot_reference_vehicle_from_other_collision(self):
        other=Collision.objects.create(origin='demo',local_date=date(2025,1,2))
        vehicle=Vehicle.objects.create(origin='demo',collision=self.collision)
        with self.assertRaises(IntegrityError),transaction.atomic():
            Person.objects.create(origin='demo',collision=other,vehicle=vehicle)
            with connection.cursor() as cursor:
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')

    def test_raw_payload_immutable_but_disposition_mutable(self):
        with self.assertRaises(DatabaseError),transaction.atomic():
            StagedRecord.objects.filter(pk=self.raw.pk).update(payload={'changed':True})
        StagedRecord.objects.filter(pk=self.raw.pk).update(disposition='accepted')
        self.raw.refresh_from_db()
        self.assertEqual(self.raw.disposition,'accepted')
        self.assertEqual(self.raw.payload,{'collision_id':'900001'})

    def test_audit_is_append_only_and_rejects_unknown_detail_keys(self):
        event=record_event(action='login_failed',object_type='authentication',outcome='failure')
        with self.assertRaises(DatabaseError),transaction.atomic():
            AuditEvent.objects.filter(pk=event.pk).update(outcome='success')
        with self.assertRaises(DatabaseError),transaction.atomic():
            AuditEvent.objects.filter(pk=event.pk).delete()
        with self.assertRaises(ValueError):
            record_event(action='login',object_type='account',details={'password':'never-store'})

    def test_business_write_and_audit_roll_back_together(self):
        before=AuditEvent.objects.count()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                Collision.objects.create(origin='demo',local_date=date(2022,1,1))
                record_event(action='data_changed',object_type='collision',object_id='rollback-test')
                raise RuntimeError('expected rollback')
        self.assertFalse(Collision.objects.filter(local_date=date(2022,1,1)).exists())
        self.assertEqual(AuditEvent.objects.count(),before)

    def test_batch_accounting_rejects_overcount(self):
        with self.assertRaises(IntegrityError),transaction.atomic():
            ImportBatch.objects.create(snapshot=self.snapshot,total_rows=1,accepted_rows=2)

    def test_quality_resolution_requires_reason_actor_and_time(self):
        with self.assertRaises(IntegrityError),transaction.atomic():
            QualityIssue.objects.create(snapshot=self.snapshot,code='missing',severity='warning',status='resolved')

class FlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        source=DataSource.objects.get(code='nyc-traffic')
        request=SamplingRequest.objects.create(source=source,source_request_id='test')
        cls.site=SamplingSite.objects.create(request=request,location_key='site1',source_segment_id='050963',direction='WB')

    def test_conflicting_values_are_both_retained(self):
        conflict=FlowConflict.objects.create(site=self.site,local_date=date(2025,1,1),local_time=time(0))
        for volume in [18,57]:
            TrafficObservation.objects.create(origin='demo',site=self.site,local_date=date(2025,1,1),local_time=time(0),volume=volume,conflict=conflict,quality_status='conflict')
        self.assertEqual(TrafficObservation.objects.filter(conflict=conflict).count(),2)
        self.assertEqual(TrafficObservation.objects.filter(quality_status='valid').count(),0)

    def test_conflict_cannot_be_marked_valid(self):
        conflict=FlowConflict.objects.create(site=self.site,local_date=date(2025,1,1),local_time=time(0))
        with self.assertRaises(IntegrityError),transaction.atomic():
            TrafficObservation.objects.create(origin='demo',site=self.site,local_date=date(2025,1,1),local_time=time(0),volume=18,conflict=conflict,quality_status='valid')

    def test_conflict_must_match_observation_slot(self):
        conflict=FlowConflict.objects.create(site=self.site,local_date=date(2025,1,1),local_time=time(0))
        with self.assertRaises(IntegrityError),transaction.atomic():
            TrafficObservation.objects.create(origin='demo',site=self.site,local_date=date(2025,1,1),local_time=time(1),volume=18,conflict=conflict,quality_status='conflict')
            with connection.cursor() as cursor: cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')

    def test_zero_observation_is_not_missing(self):
        TrafficObservation.objects.create(origin='demo',site=self.site,local_date=date(2025,1,1),local_time=time(0),volume=0)
        self.assertTrue(TrafficObservation.objects.filter(volume=0).exists())
        self.assertFalse(TrafficObservation.objects.filter(local_time=time(0,15)).exists())

    def test_non_quarter_hour_rejected(self):
        with self.assertRaises(IntegrityError),transaction.atomic():
            TrafficObservation.objects.create(origin='demo',site=self.site,local_date=date(2025,1,1),local_time=time(0,7),volume=0)
