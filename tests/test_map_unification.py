import json
from datetime import date, time
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, SimpleTestCase, override_settings
from django.db import connection
from shapely.geometry import shape, Point

from apps.core.models import AuditEvent, DataRevision
from apps.collisions.models import Collision
from apps.ingestion.models import StagedRecord, Snapshot
from apps.network.models import SpatialMatch, RoadSegment, NetworkVersion
from apps.network.map_cache import map_cache
from apps.analytics.forms import make_form
from apps.analytics.models import RiskRun, RiskResult, AnalysisTarget
from apps.analytics.risk import enqueue
from apps.analytics.query import data_version
from tests import test_s05 as fixtures


class ResearchBoundaryTests(SimpleTestCase):
    def test_geometry_and_reference_points(self):
        path=Path(__file__).resolve().parents[1]/'static/data/nyc_research_boundary.geojson'
        data=json.loads(path.read_text('utf-8'))
        self.assertLess(path.stat().st_size,1024*1024)
        self.assertEqual(data['metadata']['water_areas'],'included')
        self.assertTrue(data['metadata']['display_only'])
        features=data['features'];city=shape(next(f['geometry'] for f in features if f['properties']['role']=='city'))
        outside=shape(next(f['geometry'] for f in features if f['properties']['role']=='outside'))
        for feature in features:self.assertTrue(shape(feature['geometry']).is_valid)
        self.assertLess(city.intersection(outside).area,1e-10)
        # Five boroughs and the East River at the Brooklyn Bridge.
        for point in [(-73.98,40.76),(-73.87,40.85),(-73.95,40.65),(-73.82,40.73),(-74.15,40.58),(-73.9969,40.7061)]:
            self.assertTrue(city.covers(Point(point)),point)
        for point in [(-74.1724,40.7357),(-74.0776,40.7282),(-73.641,40.749)]:
            self.assertFalse(city.covers(Point(point)),point)
        self.assertEqual(len([f for f in features if f['properties']['role']=='label']),5)


@override_settings(ALLOWED_HOSTS=['testserver'])
class UnifiedMapTests(TestCase):
    setUpTestData=classmethod(fixtures.AnalyticsTests.setUpTestData.__func__)
    raw=fixtures.AnalyticsTests.raw
    event=fixtures.AnalyticsTests.event
    form=fixtures.AnalyticsTests.form
    risk=fixtures.AnalyticsTests.risk
    matched_events=fixtures.AnalyticsTests.matched_events

    def setUp(self):
        map_cache.clear();self.client.force_login(self.analyst)

    def query(self,**kwargs):
        params={'date_from':'2025-01-01','date_to':'2025-12-31','network':self.net.pk,'zoom':16,
                'view_bbox':'-74.3,40.45,-73.65,41',**kwargs}
        response=self.client.get('/analytics/map/data/',params)
        self.assertEqual(response.status_code,200,response.content)
        return response.json()

    def revision(self):
        event=AuditEvent.objects.create(actor=self.editor,action='test',object_type='collision',outcome='success')
        DataRevision.objects.create(event=event,domain='collision')

    def test_advanced_filters_and_viewport_only_intersect(self):
        wanted=self.event(injured=1,clock=time(12))
        self.event(injured=0);self.event(clock=time(13));self.event(day=date(2025,1,7));self.event(borough=2)
        no_position=self.event();Collision.objects.filter(pk=no_position.pk).update(geom=None)
        d=self.query(borough=1,year=2025,month=1,weekday=0,hour=12,severity='injured')
        self.assertEqual([f['properties']['id'] for f in d['features']],[wanted.pk])
        self.assertEqual(self.query(bbox='-73.99,40.70,-73.97,40.71',view_bbox='-74.2,40.8,-74.1,40.9')['count'],0)
        d2=self.query(borough=1,year=2025,month=1,weekday=0,hour=12,severity='injured',map_view_bbox='-74.2,40.8,-74.1,40.9',map_scope=0,map_mode='basic')
        self.assertEqual(d,d2)
        form=make_form('statistics',{'date_from':'2025-01-01','date_to':'2025-12-31','map_view_bbox':'1,2,3,4','map_scope':'0'})
        self.assertTrue(form.is_valid());self.assertFalse(any(k.startswith('map_') for k in form.conditions()))

    def test_matched_relation_never_becomes_nearby_query(self):
        matched=self.matched_events(1)[0];junction_event=self.event();self.event()  # Same coordinates, no relationship.
        SpatialMatch.objects.create(collision=junction_event,network=self.net,junction=self.node,status='matched',method='test',rule_version='test',input_revision=1)
        for key,pk,expected in [('road',self.road.pk,matched.pk),('junction',self.node.pk,junction_event.pk)]:
            self.assertEqual([f['properties']['id'] for f in self.query(**{key:pk})['features']],[expected])
        Collision.objects.filter(pk=matched.pk).update(revision=2);self.revision()
        self.assertEqual(self.query(road=self.road.pk)['count'],0)

    def test_cache_version_publication_and_shared_budget(self):
        wanted=self.event();self.event(is_active=False)
        Collision.objects.create(origin='demo',local_date=date(2025,1,6))
        self.assertEqual(self.query()['count'],1)
        with patch('apps.analytics.views.rows',side_effect=AssertionError('cached query unexpectedly repeated')):
            self.assertEqual(self.query()['count'],1)
        Collision.objects.filter(pk=wanted.pk).update(is_active=False);self.revision()
        self.assertEqual(self.query()['count'],0)
        Collision.objects.filter(pk=wanted.pk).update(is_active=True);self.revision()
        self.snaps['crashes'].status='quarantined';self.snaps['crashes'].save(update_fields=['status'])
        self.assertEqual(self.query()['count'],0)
        self.assertLessEqual(map_cache._bytes,24*1024*1024)

    def test_aggregate_true_counts_and_thousand_limit(self):
        self.matched_events(5)
        value=self.query(zoom=10)
        self.assertEqual(sum(f['properties']['n'] for f in value['features']),5)
        raw=StagedRecord.objects.bulk_create([StagedRecord(snapshot=self.snaps['crashes'],row_number=i+100,source_key=str(i+100),payload={}) for i in range(1001)])
        Collision.objects.bulk_create([Collision(source=self.sources['crashes'],source_record_id=str(i+100),raw_record=r,local_date=date(2025,1,6),geom=f'SRID=4326;POINT({-74.02+i%33*.002} {40.70+i//33*.002})') for i,r in enumerate(raw)])
        self.revision();value=self.query(zoom=20)
        self.assertEqual(value['count'],1006);self.assertEqual(len(value['features']),1000);self.assertTrue(value['truncated'])
        self.assertLess(sum(f['properties']['n'] for f in value['features']),value['count'])

    def test_risk_frozen_content_zero_missing_and_cap(self):
        run=enqueue(self.analyst,self.form('risk',level='road'));RiskRun.objects.filter(pk=run.pk).update(status='running')
        roads=RoadSegment.objects.bulk_create([RoadSegment(network=self.net,source_feature_id=f'map{i}',source_segment_id=f'map{i}',name=f'MAP {i}',is_canonical=True,match_eligible=True,geom='SRID=4326;MULTILINESTRING((-73.98 40.70,-73.98 40.71))') for i in range(502)])
        targets=AnalysisTarget.objects.bulk_create([AnalysisTarget(scope='road',road=r) for r in roads])
        RiskResult.objects.bulk_create([RiskResult(run=run,target=t,label=f'Frozen {i}',counts={'n':i},score=None if i==501 else 0,grade='insufficient' if i==501 else 'lower',point=[-73.98,40.705]) for i,t in enumerate(targets)])
        RiskRun.objects.filter(pk=run.pk).update(status='succeeded',data_version=data_version())
        response=self.client.get(f'/risk/runs/{run.pk}/map/').json()
        self.assertEqual(response['count'],502);self.assertEqual(len(response['features']),500);self.assertTrue(response['truncated'])
        self.assertEqual(float(response['features'][0]['properties']['score']),0)
        missing=self.client.get(f'/risk/runs/{run.pk}/map/',{'grade':'insufficient'}).json()
        self.assertIsNone(missing['features'][0]['properties']['score'])
        RoadSegment.objects.filter(pk=roads[-1].pk).update(is_active=False)
        self.revision();again=self.client.get(f'/risk/runs/{run.pk}/map/',{'grade':'insufficient'}).json()
        self.assertEqual(again['features'],missing['features']);self.assertTrue(again['stale'])
        self.assertEqual(self.client.get(f'/risk/runs/{run.pk}/map/',{'q':'%_'}).json()['count'],0)

    def test_map_templates_levels_invalid_and_permissions(self):
        self.matched_events(5)
        for level in ['city','borough','road','junction']:
            run=self.risk(level=level);response=self.client.get(f'/risk/runs/{run.pk}/')
            if level=='city':self.assertNotContains(response,'data-analysis-map=')
            else:self.assertContains(response,'data-analysis-map="risk"');self.assertContains(response,'map-core.css')
        response=self.client.get('/analytics/');self.assertContains(response,'data-analysis-map="statistics"');self.assertNotContains(response,'vendor/leaflet/leaflet.js')
        pending=enqueue(self.analyst,self.form('risk',level='road'))
        self.assertEqual(self.client.get(f'/risk/runs/{pending.pk}/map/').status_code,409)
        self.assertEqual(self.client.get('/analytics/map/data/',{'zoom':'nan'}).status_code,400)
        self.assertEqual(self.client.get('/analytics/map/data/',{'view_bbox':'1,2,1,2'}).status_code,400)
        self.client.logout();self.assertEqual(self.client.get('/analytics/map/data/').status_code,302)

    def test_picker_map_link_keeps_selected_network(self):
        from urllib.parse import urlsplit, parse_qs
        newer=Snapshot.objects.create(source=self.sources['roads'],version='new-map-network',captured_at=self.snaps['roads'].captured_at,relative_path='test-only',status='published')
        NetworkVersion.objects.create(snapshot=newer,label='Another published network',native_crs='EPSG:4326')
        for kind,pk in [('road',self.road.pk),('junction',self.node.pk)]:
            response=self.client.get('/objects/search/',{'kind':kind,'id':pk,'network':self.net.pk})
            item=response.json()['items'][0];query=parse_qs(urlsplit(item['map_url']).query)
            self.assertEqual(query['network'],[str(self.net.pk)])
            params={k:v[0] for k,v in query.items()}
            selection=self.client.get('/map/selection/',params)
            self.assertEqual(selection.status_code,200)
            self.assertEqual(selection.json()['selected']['properties']['id'],pk)
