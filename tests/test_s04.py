from datetime import date,time,datetime,timedelta,timezone as tz
from django.contrib.auth.models import User,Group
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import connection,transaction,DatabaseError
from django.test import TestCase,override_settings
from django.utils import timezone
from apps.ingestion.models import DataSource,Snapshot,StagedRecord
from apps.collisions.models import Collision
from apps.core.models import FieldChange,DataRevision
from apps.core.services import ConcurrentChange
from apps.core.time_rules import resolve_wall_time
from apps.network.models import NetworkVersion,RoadSegment,Junction,SpatialMatch,SamplingRoadMatch
from apps.network.matching import name_key,pick_named,collision_proposal,site_proposal,prepare_network
from apps.network.services import review_match,maintain,initialize_match
from apps.network.queries import parse_map,map_data
from apps.traffic.models import SamplingRequest,SamplingSite,TrafficObservation,FlowConflict
from apps.traffic.analysis import summarize,compare,exposure,expected_slots,NY

REASON='依据来源几何和观测范围完成核查。'

@override_settings(ALLOWED_HOSTS=['testserver','127.0.0.1','localhost'])
class SpatialFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.editor=User.objects.create_user('spatial-editor');cls.editor.groups.add(Group.objects.get(name='数据维护人员'))
        cls.analyst=User.objects.create_user('spatial-analyst');cls.analyst.groups.add(Group.objects.get(name='分析用户'))
        cls.roadsource=DataSource.objects.get(code='nyc-lion');cls.flowsource=DataSource.objects.get(kind='traffic')
        cls.crashsource=DataSource.objects.get(code='nyc-crashes')
        cls.rs=Snapshot.objects.create(source=cls.roadsource,version='spatial test',captured_at=timezone.now(),relative_path='test',status='published')
        cls.fs=Snapshot.objects.create(source=cls.flowsource,version='flow test',captured_at=timezone.now(),relative_path='test',status='published')
        cls.cs=Snapshot.objects.create(source=cls.crashsource,version='crash test',captured_at=timezone.now(),relative_path='test',status='published')
        cls.net=NetworkVersion.objects.create(snapshot=cls.rs,label='Test network',native_crs='EPSG:4326')
        cls.road=RoadSegment.objects.create(network=cls.net,source_feature_id='r1',source_segment_id='001',normalized_segment_id='1',name='MAIN STREET',
            geom='SRID=4326;MULTILINESTRING((-73.98 40.70,-73.98 40.71))',feature_type='0',traffic_direction='T',rb_layer='B',segment_type='U',
            is_canonical=True,match_eligible=True,bearing_deg=0,aliases=['MAIN STREET'])
        cls.req=SamplingRequest.objects.create(source=cls.flowsource,source_request_id='spatial-test')
        cls.site=SamplingSite.objects.create(request=cls.req,location_key='north',source_segment_id='1',direction='NB',street='MAIN ST',geom='SRID=4326;POINT(-73.98 40.705)')
        cls.south=SamplingSite.objects.create(request=cls.req,location_key='south',source_segment_id='1',direction='SB',street='MAIN ST',geom='SRID=4326;POINT(-73.98 40.705)')
    def setUp(self):self.client.force_login(self.editor)
    def observation(self,day,clock=time(0),volume=0,site=None,quality='valid',status='unique'):
        serial=StagedRecord.objects.filter(snapshot=self.fs).count()+1
        raw=StagedRecord.objects.create(snapshot=self.fs,row_number=serial,payload={'Vol':volume},source_key=str(serial))
        stamp=datetime.combine(day,clock,NY).astimezone(tz.utc) if status=='unique' else None
        return TrafficObservation.objects.create(source=self.flowsource,source_record_id=str(serial),raw_record=raw,site=site or self.site,
            local_date=day,local_time=clock,observed_at=stamp,time_status=status,volume=volume,quality_status=quality)
    def crash(self,day=date(2025,1,6),clock=time(0,5),geom='SRID=4326;POINT(-73.98 40.705)',street='MAIN STREET'):
        serial=StagedRecord.objects.filter(snapshot=self.cs).count()+1;raw=StagedRecord.objects.create(snapshot=self.cs,row_number=serial,payload={},source_key=str(serial))
        return Collision.objects.create(source=self.crashsource,source_record_id=str(serial),raw_record=raw,local_date=day,local_time=clock,
            occurred_at=datetime.combine(day,clock,NY).astimezone(tz.utc),time_status='unique',geom=geom,on_street=street)
    def match(self,site=None,reviewed=False):
        site=site or self.site
        return SamplingRoadMatch.objects.create(site=site,network=self.net,road=self.road,status='matched',method='test',rule_version='test',input_revision=site.revision,
            direction_compatible=True,reviewed_by=self.editor if reviewed else None,
            evidence={'target_revision':self.road.revision,'exposure_start':'2025-01-01','exposure_end':'2025-12-31'} if reviewed else {})
    def test_zero_missing_partial_and_conflicting_duplicates(self):
        day=date(2025,1,6);self.observation(day,volume=0);self.observation(day,time(0,15),10)
        self.observation(day,time(0,30),10);self.observation(day,time(0,30),10)
        self.observation(day,time(0,45),40,quality='excluded')
        result=summarize(self.site,day,day,'hour');first=result['series'][0]
        self.assertEqual((first['valid'],first['conflict'],first['excluded'],first['observed_sum'],first['total']),(2,1,1,10,None))
        self.assertEqual(result['coverage']['missing'],92)
        q=summarize(self.site,day,day,'quarter')['series'];self.assertEqual(q[0]['total'],0);self.assertIsNone(q[4]['observed_sum'])
    def test_dst_days_and_repeated_hour_stay_ambiguous(self):
        for day,n in [(date(2025,3,9),92),(date(2025,11,2),100),(date(2025,1,6),96)]:
            self.assertEqual(len(list(expected_slots(day,day))),n)
        day=date(2025,11,2);self.observation(day,time(1,15),30,quality='excluded',status='ambiguous')
        result=summarize(self.site,day,day,'hour');self.assertEqual(result['coverage']['ambiguous'],2)
        repeated=[r for r in result['series'] if '01:00' in r['label']];self.assertEqual(len(repeated),2);self.assertNotEqual(repeated[0]['label'],repeated[1]['label'])
    def test_peak_windows_do_not_extrapolate_partial_data(self):
        day=date(2025,1,6)
        for hour in range(7,10):
            for minute in (0,15,30,45):self.observation(day,time(hour,minute),5)
        self.observation(day,time(16),7)
        peaks=summarize(self.site,day,day)['peaks']
        self.assertEqual(peaks[0]['total'],60);self.assertIsNone(peaks[1]['total']);self.assertEqual(peaks[1]['observed_sum'],7)
    def test_comparison_uses_common_slots_and_no_zero_percentage(self):
        a=date(2025,1,6);b=date(2025,1,13)
        self.observation(a,volume=0);self.observation(b,volume=10);self.observation(a,time(0,15),999)
        result=compare(self.site,self.site,a,a,b)
        self.assertTrue(result['comparable']);self.assertEqual((result['common_slots'],result['a_volume'],result['b_volume']),(1,0,10));self.assertIsNone(result['percent'])
        self.assertEqual(len(result['series']),96);self.assertIsNone(result['series'][1]['a'])
        self.assertFalse(compare(self.site,self.south,a,a,b)['comparable'])
        self.assertFalse(compare(self.site,self.site,a,a,b+timedelta(days=1))['comparable'])
    def test_range_limits_and_unpublished_or_inactive_observations(self):
        with self.assertRaises(ValidationError):summarize(self.site,date(2025,1,1),date(2025,2,2),'quarter')
        day=date(2025,1,6);row=self.observation(day,volume=20);row.is_active=False;row.save()
        self.assertEqual(summarize(self.site,day,day)['coverage']['valid'],0)
        row.is_active=True;row.save();self.fs.status='quarantined';self.fs.save()
        self.assertEqual(summarize(self.site,day,day)['coverage']['valid'],0)
    def test_matching_names_geometry_direction_and_ambiguity(self):
        self.assertEqual(name_key('East 42nd St.'),'EAST 42 STREET')
        proposal=site_proposal(self.site,self.net.pk);self.assertEqual(proposal['road_id'],self.road.pk);self.assertTrue(proposal['direction_compatible'])
        crash=self.crash();self.assertEqual(collision_proposal(crash,self.net.pk)['road_id'],self.road.pk)
        RoadSegment.objects.create(network=self.net,source_feature_id='r2',source_segment_id='002',normalized_segment_id='2',name='MAIN STREET',
            geom='SRID=4326;MULTILINESTRING((-73.98001 40.70,-73.98001 40.71))',is_canonical=True,match_eligible=True,traffic_direction='T',bearing_deg=0,aliases=['MAIN STREET'])
        self.assertEqual(collision_proposal(crash,self.net.pk)['status'],'ambiguous')
        self.site.direction='EB';self.assertNotEqual(site_proposal(self.site,self.net.pk)['status'],'matched')
    def test_source_aliases_deduplicate_and_different_geometry_stays_restricted(self):
        for fid,sid,name,geom in [('r2','001','MAIN ST',self.road.geom),('r3','002','OTHER STREET',self.road.geom),
                ('r4','002','OTHER STREET','SRID=4326;MULTILINESTRING((-73.981 40.70,-73.981 40.71))')]:
            RoadSegment.objects.create(network=self.net,source_feature_id=fid,source_segment_id=sid,name=name,geom=geom,feature_type='0',traffic_direction='T')
        for i,road in enumerate(RoadSegment.objects.all(),1):
            StagedRecord.objects.create(snapshot=self.rs,row_number=i,source_key=road.source_feature_id,payload={'RB_Layer':'B','SegmentTyp':'U','NodeLevelF':'M','NodeLevelT':'M','RW_TYPE':'1'})
        node=Junction.objects.create(network=self.net,source_node_id='empty',geom='SRID=4326;POINT(-73.98 40.705)',virtual_intersection=True)
        stats=prepare_network(self.net);self.road.refresh_from_db();node.refresh_from_db()
        self.assertEqual(stats['canonical_roads'],2);self.assertEqual(stats['eligible_roads'],1)
        self.assertEqual(self.road.source_variants_count,2);self.assertEqual(set(self.road.aliases),{'MAIN STREET','MAIN ST'})
        self.assertEqual(RoadSegment.objects.filter(source_segment_id='002',match_eligible=True).count(),0)
        self.assertIsNone(node.is_intersection);self.assertEqual(node.name,'')
    def test_new_record_match_is_explicit_audited_and_idempotent(self):
        crash=self.crash()
        obj=initialize_match('collision',crash.pk,self.editor,1,REASON)
        again=initialize_match('collision',crash.pk,self.editor,1,REASON)
        self.assertEqual(obj.pk,again.pk);self.assertEqual(DataRevision.objects.count(),1)
        with self.assertRaises(PermissionDenied):initialize_match('collision',crash.pk,self.analyst,1,REASON)
    def test_intersection_name_match_and_virtual_nodes_not_guessed(self):
        crash=self.crash();crash.off_street='CROSS AVENUE'
        node=Junction.objects.create(network=self.net,source_node_id='j1',name='MAIN STREET / CROSS AVENUE',street_names=['MAIN STREET','CROSS AVENUE'],
            geom=crash.geom,is_intersection=True)
        self.assertEqual(collision_proposal(crash,self.net.pk)['junction_id'],node.pk)
        node.is_intersection=None;node.virtual_intersection=True;node.save()
        self.assertIsNone(collision_proposal(crash,self.net.pk)['junction_id'])
    def test_manual_review_permissions_audit_stale_and_distance(self):
        match=self.match()
        kwargs=dict(kind='site',pk=match.pk,revision=1,input_revision=1,action='confirm',target_kind='road',target_id=self.road.pk,reason=REASON)
        with self.assertRaises(PermissionDenied):review_match(actor=self.analyst,**kwargs)
        value=review_match(actor=self.editor,**kwargs,exposure_start=date(2025,1,1),exposure_end=date(2025,12,31))
        self.assertEqual(value.revision,2);self.assertTrue(FieldChange.objects.exists());self.assertEqual(DataRevision.objects.count(),1)
        with self.assertRaises(ConcurrentChange):review_match(actor=self.editor,**kwargs)
        maintain('site',self.site.pk,self.editor,1,{'review_note':'重新核查地点范围'},REASON)
        self.site.refresh_from_db();self.assertNotEqual(value.input_revision,self.site.revision)
        with self.assertRaises(ValidationError):maintain('road',self.road.pk,self.editor,1,{'geom':'POINT(0 0)'},REASON)
    def test_source_geometry_and_cross_network_match_database_guards(self):
        with self.assertRaises(DatabaseError),transaction.atomic(),connection.cursor() as c:
            c.execute("UPDATE network_roadsegment SET geom=ST_GeomFromText('MULTILINESTRING((-73 40,-73 41))',4326) WHERE id=%s",[self.road.pk])
        snapshot=Snapshot.objects.create(source=self.roadsource,version='other',captured_at=timezone.now(),relative_path='test',status='published')
        net=NetworkVersion.objects.create(snapshot=snapshot,label='other',native_crs='EPSG:4326')
        with self.assertRaises(DatabaseError),transaction.atomic():
            SamplingRoadMatch.objects.create(network=net,site=self.site,road=self.road,status='matched',method='bad',rule_version='test')
    def test_exposure_requires_reviewed_two_way_support_and_exact_windows(self):
        day=date(2025,1,6);self.match(reviewed=True)
        self.assertFalse(exposure(self.site,self.net,day,day)['eligible'])
        self.match(self.south,reviewed=True)
        for site in (self.site,self.south):self.observation(day,volume=100,site=site)
        for clock in (time(0,5),time(1,5)):
            crash=self.crash(clock=clock);SpatialMatch.objects.create(collision=crash,network=self.net,road=self.road,status='matched',input_revision=1,method='test',rule_version='test')
        value=exposure(self.site,self.net,day,day);self.assertTrue(value['eligible']);self.assertEqual((value['volume'],value['collisions'],value['slots']),(200,1,1));self.assertEqual(value['rate_per_million'],5000)
        self.crash(clock=time(0,7));self.assertFalse(exposure(self.site,self.net,day,day)['eligible'])
    def test_exposure_does_not_use_auto_match_as_historical_evidence(self):
        self.match();self.match(self.south)
        value=exposure(self.site,self.net,date(2025,1,6),date(2025,1,6));self.assertFalse(value['eligible']);self.assertIn('历史',value['reasons'][0])
    def test_map_validation_filters_aggregation_and_layer_off(self):
        for params in ({'bbox':'nan,40,-73,41'},{'bbox':'1,2,0,4'},{'radius':'100000'},{'layers':'unknown'},{'road':'1','site':'2'}):
            with self.assertRaises(ValidationError):parse_map(params)
        crash=self.crash();value=map_data(parse_map({'bbox':'-74,40.69,-73.9,40.72','network':str(self.net.pk),'zoom':'10'}))
        self.assertEqual(value['crash_count'],1);self.assertEqual(value['crash_mode'],'aggregate');self.assertEqual(value['crashes']['features'][0]['properties']['count'],1)
        empty=map_data(parse_map({'layers':'','network':str(self.net.pk)}));self.assertEqual(empty['crash_count'],0)
    def test_real_routes_and_analyst_cannot_post(self):
        self.observation(date(2025,1,6));match=self.match();crash=self.crash()
        for path in ['/map/','/spatial/','/spatial/?kind=site&status=ambiguous','/network/road/','/network/junction/',f'/network/road/{self.road.pk}/',f'/network/site/{self.site.pk}/',
            f'/network/site/{self.site.pk}/edit/',f'/spatial/site/{match.pk}/',f'/spatial/collisions/{crash.pk}/','/traffic/',f'/traffic/{self.site.pk}/']:
            response=self.client.get(path);self.assertEqual(response.status_code,200,path)
        self.assertContains(response,'value="2025-01-06"')
        self.client.force_login(self.analyst)
        self.assertEqual(self.client.post(f'/spatial/site/{match.pk}/',{}).status_code,403)
        self.assertEqual(self.client.post(f'/network/site/{self.site.pk}/edit/',{}).status_code,403)
        self.client.logout();self.assertEqual(self.client.get('/map/data/').status_code,302)
    def test_observation_clock_filter_preserves_full_day_coverage(self):
        day=date(2025,1,6);self.observation(day,time(8),10);self.observation(day,time(9),20)
        response=self.client.get(f'/traffic/{self.site.pk}/',{'raw_start_time':'08:00','raw_end_time':'09:00'})
        self.assertEqual(response.status_code,200);self.assertEqual(response.context['raw_page'].paginator.count,1)
        self.assertEqual(response.context['data']['coverage']['observed_sum'],30)
        self.assertEqual(self.client.get(f'/traffic/{self.site.pk}/',{'raw_start_time':'09:00','raw_end_time':'08:00'}).status_code,400)
        error=self.client.get(f'/traffic/{self.site.pk}/',{'start':'2025-01-07','end':'2025-01-06','grain':'day'})
        self.assertEqual(error.status_code,400);self.assertEqual(error.context['raw_page'].paginator.count,0)
