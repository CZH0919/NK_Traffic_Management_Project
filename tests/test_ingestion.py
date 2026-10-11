import gzip
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from apps.collisions.models import Collision,Vehicle,Person
from apps.ingestion import pipeline
from apps.ingestion.models import DataSource,Snapshot,StagedRecord,ImportBatch,QualityIssue
from apps.ingestion.quality import classify
from apps.traffic.models import TrafficObservation,FlowConflict

class ImportTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(pipeline.ROOT)/'var')
        self.root=Path(self.temp.name); (self.root/'data/raw').mkdir(parents=True)
        (self.root/'data/manifests').mkdir(parents=True)
        self.root_patch=patch.object(pipeline,'ROOT',self.root); self.root_patch.start()
        self.addCleanup(self.root_patch.stop); self.addCleanup(self.temp.cleanup)
        self.serial=0

    def snapshot(self,kind,rows):
        self.serial+=1
        raw=json.dumps(rows).encode(); digest=hashlib.sha256(raw).hexdigest()
        name=f'data/raw/{kind}_{self.serial}.json.gz'
        with gzip.open(self.root/name,'wb') as f: f.write(raw)
        manifest={'kind':kind,'dataset':'test','label':'test','status':'complete','captured_at':timezone.now().isoformat(),
            'row_count':len(rows),'expected_rows':len(rows),'source_url':'https://example.invalid',
            'files':[{'path':name,'rows':len(rows),'sha256':digest}], 'checksum':hashlib.sha256(digest.encode()).hexdigest()}
        path=self.root/f'data/manifests/{kind}_{self.serial}.json'; path.write_text(json.dumps(manifest),encoding='utf-8')
        return pipeline.register(pipeline.read_manifest(path))

    def crash(self,key='100',**values):
        return {'collision_id':key,'crash_date':'2025-01-01T00:00:00','crash_time':'1:30','borough':'QUEENS',**values}

    def flow(self,key='flow1',**values):
        return {'source_row_id':key,'requestid':'10','segmentid':'20','direction':'WB','boro':'Queens',
            'yr':'2025','m':'1','d':'1','hh':'10','mm':'0','vol':'12',
            'wktgeom':'POINT (1057055.68523224 198693.08834211103)',**values}

    def test_missing_locations_and_counts_are_retained_not_zeroed(self):
        s=self.snapshot('crashes',[self.crash(number_of_persons_injured='-1')])
        b,_=pipeline.import_snapshot(s)
        c=Collision.objects.get(source_record_id='100')
        self.assertIsNone(c.geom); self.assertIsNone(c.persons_injured)
        self.assertEqual(b.accepted_rows,1)
        self.assertTrue(QualityIssue.objects.filter(code='invalid_number').exists())

    def test_same_snapshot_is_noop_and_changed_record_is_quarantined(self):
        s=self.snapshot('crashes',[self.crash()]); pipeline.import_snapshot(s)
        _,cached=pipeline.import_snapshot(s); self.assertTrue(cached)
        changed=self.snapshot('crashes',[self.crash(borough='BRONX')]); batch,_=pipeline.import_snapshot(changed)
        self.assertEqual(Collision.objects.count(),1); self.assertEqual(batch.pending_rows,1)
        self.assertEqual(Collision.objects.get().borough_id,4)

    def test_exact_duplicate_new_snapshot_preserves_evidence(self):
        rows=[self.crash(source_row_id='first')]
        pipeline.import_snapshot(self.snapshot('crashes',rows))
        batch,_=pipeline.import_snapshot(self.snapshot('crashes',[self.crash(source_row_id='second')]))
        self.assertEqual(batch.skipped_rows,1); self.assertEqual(StagedRecord.objects.count(),2)

    def test_resume_after_committed_chunk_with_changed_chunk_size(self):
        s=self.snapshot('crashes',[self.crash(str(k)) for k in range(100,105)])
        def stop(batch): raise RuntimeError('test interruption')
        with self.assertRaises(RuntimeError): pipeline.import_snapshot(s,chunk_size=2,after_chunk=stop)
        self.assertEqual(Collision.objects.count(),2)
        failed=ImportBatch.objects.get(); self.assertEqual((failed.status,failed.checkpoint),('failed',2))
        batch,_=pipeline.import_snapshot(s,chunk_size=3)
        self.assertEqual((batch.attempt,batch.accepted_rows,batch.checkpoint),(2,5,5))
        self.assertEqual(StagedRecord.objects.count(),5)

    def test_failed_chunk_rolls_back_raw_typed_and_checkpoint(self):
        s=self.snapshot('crashes',[self.crash()])
        with patch.object(pipeline.Importer,'side_tables',side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError): pipeline.import_snapshot(s)
        self.assertFalse(StagedRecord.objects.exists()); self.assertFalse(Collision.objects.exists())
        self.assertEqual(ImportBatch.objects.get().checkpoint,0)
        pipeline.import_snapshot(s); self.assertEqual(Collision.objects.count(),1)

    def test_vehicle_person_real_keys_and_mismatch(self):
        pipeline.import_snapshot(self.snapshot('crashes',[self.crash('100'),self.crash('101')]))
        pipeline.import_snapshot(self.snapshot('vehicles',[dict(self.crash('100'),unique_id='200',vehicle_id='some-uuid')]))
        pipeline.import_snapshot(self.snapshot('persons',[
            dict(self.crash('100'),unique_id='300',vehicle_id='200'),
            dict(self.crash('101'),unique_id='301',vehicle_id='200'),
            dict(self.crash('999'),unique_id='302',vehicle_id='999')]))
        self.assertEqual(Person.objects.get(source_record_id='300').vehicle,Vehicle.objects.get())
        self.assertIsNone(Person.objects.get(source_record_id='301').vehicle)
        self.assertIsNone(Person.objects.get(source_record_id='302').collision)

    def test_property_damage_description_is_not_limited_to_200_characters(self):
        description='Source description. '*35
        pipeline.import_snapshot(self.snapshot('crashes',[self.crash()]))
        pipeline.import_snapshot(self.snapshot('vehicles',[dict(self.crash(),unique_id='200',public_property_damage_type=description)]))
        self.assertEqual(Vehicle.objects.get().public_property_damage_type,description.strip())
        self.assertFalse(QualityIssue.objects.filter(code='invalid_text').exists())

    def test_official_long_seat_category_is_preserved(self):
        description='Front passenger, if two or more persons, including the driver, are in the front seat'
        pipeline.import_snapshot(self.snapshot('crashes',[self.crash()]))
        pipeline.import_snapshot(self.snapshot('persons',[dict(self.crash(),unique_id='300',position_in_vehicle=description)]))
        self.assertEqual(Person.objects.get().position_in_vehicle,description)
        self.assertFalse(QualityIssue.objects.filter(code='invalid_text').exists())

    def test_flow_conflicts_are_all_retained_and_not_averaged(self):
        s=self.snapshot('traffic',[self.flow('a',vol='12'),self.flow('b',vol='14')])
        pipeline.import_snapshot(s); classify([s]); classify([s])
        self.assertEqual(FlowConflict.objects.count(),1)
        self.assertEqual(list(TrafficObservation.objects.order_by('volume').values_list('volume',flat=True)),[12,14])
        self.assertFalse(TrafficObservation.objects.filter(quality_status='valid').exists())
        self.assertEqual(ImportBatch.objects.get().pending_rows,2)
        self.assertEqual(QualityIssue.objects.filter(code='flow_not_statistically_usable').count(),2)

    def test_traffic_notice_and_dst_exclusion(self):
        s=self.snapshot('traffic',[self.flow('a'),self.flow('b',m='11',d='2',hh='1',mm='30')])
        pipeline.import_snapshot(s,notices={('10','20')})
        self.assertEqual(TrafficObservation.objects.filter(quality_status='excluded').count(),2)
        self.assertIsNone(TrafficObservation.objects.get(source_record_id='b').observed_at)

    def test_incremental_snapshot_conflict_quarantines_previous_members(self):
        first=self.snapshot('traffic',[self.flow('incremental-a')])
        pipeline.import_snapshot(first); classify([first]); first.status='published'; first.save()
        second=self.snapshot('traffic',[self.flow('incremental-b',vol='18')])
        pipeline.import_snapshot(second); classify([second])
        self.assertEqual(TrafficObservation.objects.filter(quality_status='conflict').count(),2)
        self.assertEqual(ImportBatch.objects.get(snapshot=first).pending_rows,1)
        self.assertEqual(ImportBatch.objects.get(snapshot=second).pending_rows,1)
        self.assertEqual(QualityIssue.objects.filter(code='flow_not_statistically_usable').count(),2)

    def test_equal_volume_in_different_source_rows_cannot_double_count(self):
        first=self.snapshot('traffic',[self.flow('equal-a')]); pipeline.import_snapshot(first); classify([first])
        second=self.snapshot('traffic',[self.flow('equal-b')]); pipeline.import_snapshot(second); classify([second])
        self.assertEqual(TrafficObservation.objects.count(),2)
        self.assertFalse(TrafficObservation.objects.filter(quality_status='valid').exists())

    def test_missing_geometry_cannot_borrow_existing_site_qualification(self):
        first=self.snapshot('traffic',[self.flow('geo-a')]); pipeline.import_snapshot(first); classify([first])
        second=self.snapshot('traffic',[self.flow('geo-b',mm='15',wktgeom=None)]); pipeline.import_snapshot(second); classify([second])
        self.assertEqual(TrafficObservation.objects.filter(quality_status='excluded').count(),2)

    def test_missing_direction_is_retained_in_quarantine(self):
        snapshot=self.snapshot('traffic',[self.flow('no-direction',direction='')]); batch,_=pipeline.import_snapshot(snapshot)
        self.assertEqual(batch.rejected_rows,1); self.assertFalse(TrafficObservation.objects.exists())
        self.assertTrue(QualityIssue.objects.filter(code='invalid_site_key').exists())

    def test_malformed_flow_geometry_preserves_observation_as_excluded(self):
        s=self.snapshot('traffic',[self.flow(wktgeom='broken WKT')])
        pipeline.import_snapshot(s)
        self.assertEqual(TrafficObservation.objects.get().quality_status,'excluded')
        self.assertIsNone(TrafficObservation.objects.get().site.geom)
        self.assertTrue(QualityIssue.objects.filter(code='invalid_geometry').exists())

    def test_missing_flow_geometry_does_not_abort_batch(self):
        s=self.snapshot('traffic',[self.flow(wktgeom=None)])
        batch,_=pipeline.import_snapshot(s)
        self.assertEqual(batch.checkpoint,1)
        self.assertEqual(TrafficObservation.objects.get().quality_status,'excluded')

    def test_non_quarter_hour_observation_is_retained_without_resampling(self):
        s=self.snapshot('traffic',[self.flow(mm='20')]); batch,_=pipeline.import_snapshot(s)
        self.assertEqual(batch.rejected_rows,1)
        self.assertFalse(TrafficObservation.objects.exists())
        self.assertEqual(StagedRecord.objects.get().payload['mm'],'20')
        self.assertTrue(QualityIssue.objects.filter(code='non_quarter_hour_sampling').exists())

    def test_aligned_subset_of_inconsistent_site_is_not_usable(self):
        s=self.snapshot('traffic',[self.flow('a',mm='20'),self.flow('b',mm='30')])
        pipeline.import_snapshot(s); classify([s])
        self.assertEqual(StagedRecord.objects.count(),2)
        self.assertEqual(TrafficObservation.objects.get().quality_status,'excluded')

    def test_bad_date_has_explicit_rejected_raw(self):
        s=self.snapshot('crashes',[self.crash(crash_date='broken')]); b,_=pipeline.import_snapshot(s)
        self.assertEqual(b.rejected_rows,1); self.assertEqual(StagedRecord.objects.get().disposition,'rejected')

    def test_manifest_escape_and_changed_raw_refused(self):
        with self.assertRaises(ValueError): pipeline.safe_raw_path('../outside')
        s=self.snapshot('crashes',[self.crash()]); path=self.root/s.relative_path
        part=s.manifest['files'][0]
        with gzip.open(self.root/part['path'],'wb') as f: f.write(b'[]')
        with self.assertRaises(ValueError): pipeline.read_manifest(path)
