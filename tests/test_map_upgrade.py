import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import TestCase, SimpleTestCase, override_settings
from apps.core.models import AuditEvent, DataRevision
from apps.ingestion.models import Snapshot, StagedRecord
from apps.collisions.models import Collision
from apps.network import queries
from apps.network.map_cache import MapCache, map_cache
from tests import test_s04 as fixtures


class MapCacheBoundsTests(SimpleTestCase):
    def test_capacity_expiration_and_independent_results(self):
        cache = MapCache(max_bytes=80, max_entries=2, ttl=10)
        cache.set('one', {'items':[1]})
        value = cache.get('one'); value['items'].append(2)
        self.assertEqual(cache.get('one'), {'items':[1]})
        cache.set('two', {'items':[2]}); cache.set('three', {'items':[3]})
        self.assertIsNone(cache.get('one'))
        cache.set('oversize', 'x'*100)
        self.assertIsNone(cache.get('oversize'))
        self.assertLessEqual(cache._bytes, 80)
        with patch('apps.network.map_cache.time.monotonic', return_value=10**15):
            self.assertIsNone(cache.get('two'))


@override_settings(ALLOWED_HOSTS=['testserver'])
class MapUpgradeTests(TestCase):
    setUpTestData = classmethod(fixtures.SpatialFlowTests.setUpTestData.__func__)
    crash = fixtures.SpatialFlowTests.crash

    def setUp(self):
        map_cache.clear()
        self.client.force_login(self.analyst)

    def options(self, **extra):
        return queries.parse_map({'network':str(self.net.pk), 'bbox':'-74,40.69,-73.9,40.72',
                                  'zoom':'15', 'statistics':'1', **extra})

    def revision(self):
        event = AuditEvent.objects.create(actor=self.editor, action='test', object_type='collision', outcome='success')
        DataRevision.objects.create(event=event, domain='collision')

    def test_published_only_and_cache_invalidates_on_publication(self):
        self.crash()
        self.assertEqual(queries.map_data(self.options(), use_cache=True)['crash_count'], 1)
        self.cs.status='quarantined'; self.cs.save(update_fields=['status'])
        self.assertEqual(queries.map_data(self.options(), use_cache=True)['crash_count'], 0)
        self.cs.status='published'; self.cs.save(update_fields=['status'])
        other=Snapshot.objects.create(source=self.crashsource, version='not published', captured_at=self.cs.captured_at,
                                      relative_path='test', status='staged')
        raw=StagedRecord.objects.create(snapshot=other,row_number=1,payload={},source_key='unpublished')
        Collision.objects.create(source=self.crashsource,source_record_id='unpublished',raw_record=raw,
                                 local_date='2025-01-06',geom='SRID=4326;POINT(-73.98 40.705)')
        self.assertEqual(queries.map_data(self.options(), use_cache=True)['crash_count'], 1)

    def test_revision_dates_and_network_do_not_reuse_another_scope(self):
        row=self.crash()
        self.assertEqual(queries.map_data(self.options(), use_cache=True)['crash_count'],1)
        self.assertEqual(queries.map_data(self.options(start='2026-01-01'), use_cache=True)['crash_count'],0)
        row.is_active=False; row.save(update_fields=['is_active']); self.revision()
        self.assertEqual(queries.map_data(self.options(), use_cache=True)['crash_count'],0)
        self.rs.status='quarantined'; self.rs.save(update_fields=['status'])
        with self.assertRaises(ValidationError): self.options()

    def test_list_only_reuses_count_and_never_builds_map_geometry(self):
        for _ in range(26): self.crash()
        first=queries.map_data(self.options(zoom='10'), use_cache=True)
        self.assertEqual(first['pages'],2)
        original=queries.rows
        with patch('apps.network.queries.rows', wraps=original) as calls:
            second=queries.map_data(self.options(zoom='10',part='list',page='2'), use_cache=True)
        sql=' '.join(call.args[0] for call in calls.call_args_list)
        self.assertNotIn('ST_SnapToGrid',sql)
        self.assertNotIn('SELECT count',sql)
        self.assertNotIn('network_roadsegment',sql)
        self.assertEqual(len(second['list']),1)
        self.assertEqual(second['roads']['features'],[])
        self.assertNotEqual(first['list'][0]['id'],second['list'][0]['id'])

    def test_hidden_crash_layer_keeps_statistics_and_nearby_scope(self):
        self.crash()
        self.crash(geom='SRID=4326;POINT(-73.95 40.71)')
        value=queries.map_data(self.options(layers='',site=str(self.site.pk),radius='100'))
        self.assertEqual(value['crash_count'],1)
        self.assertEqual(value['crashes']['features'],[])
        self.assertEqual(value['selected']['properties']['kind'],'site')
        self.assertEqual(value['nearby']['features'][0]['geometry']['type'],'Polygon')
        self.assertEqual(queries.map_data(self.options(layers='',statistics='0'))['crash_count'],0)

    def test_aggregation_count_and_display_precision_preserve_records(self):
        for _ in range(3): self.crash()
        before=list(Collision.objects.values_list('id','geom'))
        value=queries.map_data(self.options(zoom='10'))
        self.assertEqual(sum(f['properties']['count'] for f in value['crashes']['features']),3)
        self.assertEqual(before,list(Collision.objects.values_list('id','geom')))
        self.assertEqual(queries.map_data(self.options(page='10000'))['page'],1)

    def test_nearby_prefilter_preserves_exact_distance_boundary(self):
        from django.db import connection
        from math import pi
        # Include both sides of the cut-off in all directions, not only points
        # aligned with a polygon vertex or one street direction.
        for distance in (99.9,100.1,499.9,500.1):
            for bearing in range(0,360,23):
                with connection.cursor() as cursor:
                    cursor.execute('SELECT ST_AsEWKT(ST_Project(ST_SetSRID(ST_MakePoint(-73.98,40.705),4326)::geography,%s,%s)::geometry)',[distance,bearing*pi/180])
                    geometry=cursor.fetchone()[0]
                self.crash(geom=geometry)
        for kind,pk in [('site',self.site.pk),('road',self.road.pk)]:
            for radius in (50,100,200,500):
                options=self.options(**{kind:str(pk),'radius':str(radius)})
                result=queries.map_data(options)
                with connection.cursor() as cursor:
                    cursor.execute('SELECT count(*) FROM collisions_collision WHERE ST_DWithin(geom::geography,ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)::geography,%s)',[json.dumps(result['selected']['geometry']),radius])
                    independent=cursor.fetchone()[0]
                self.assertEqual(result['crash_count'],independent)

    def test_selection_routes_and_invalid_parameters(self):
        response=self.client.get('/map/selection/',{'road':self.road.pk,'network':self.net.pk,'radius':100})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['selected']['properties']['id'],self.road.pk)
        for params in ({'part':'arbitrary'}, {'statistics':'yes'}, {'road':'-1'}, {'road':'9'*30}, {'road':'1','site':'1'}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get('/map/data/',params).status_code,400)

    def test_range_streaming_head_errors_and_fixed_path(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'reference.pmtiles'; payload=b'PMTiles\x03'+bytes(range(200)); path.write_bytes(payload)
            with override_settings(MAP_ARCHIVE=path):
                for value,expected in [('bytes=0-7',payload[:8]),('bytes=8-11',payload[8:12]),
                                       ('bytes=-5',payload[-5:]),('bytes=200-',payload[200:]),('bytes=200-999',payload[200:])]:
                    with self.subTest(value=value):
                        response=self.client.get('/map/basemap.pmtiles',HTTP_RANGE=value)
                        self.assertEqual(response.status_code,206)
                        self.assertEqual(b''.join(response.streaming_content),expected)
                        self.assertEqual(int(response['Content-Length']),len(expected))
                response=self.client.head('/map/basemap.pmtiles',HTTP_RANGE='bytes=0-7')
                self.assertEqual(response.status_code,206);self.assertEqual(response.content,b'')
                self.assertEqual(response['Content-Length'],'8')
                self.assertEqual(self.client.get('/map/basemap.pmtiles',HTTP_IF_NONE_MATCH=response['ETag']).status_code,304)
                for invalid in ('bytes=1-0','bytes=-0','bytes=999-','bytes=0-1,3-4','bytes=-','garbage','bytes=1-999999999999999999999999'):
                    self.assertEqual(self.client.get('/map/basemap.pmtiles',HTTP_RANGE=invalid).status_code,416)
                response=self.client.get('/map/basemap.pmtiles?path=../../.env',HTTP_RANGE='bytes=0-7')
                self.assertEqual(b''.join(response.streaming_content),b'PMTiles\x03')
                response=self.client.get('/map/basemap.pmtiles',HTTP_RANGE='bytes=0-1',HTTP_IF_RANGE='"stale"')
                self.assertEqual(response.status_code,200);self.assertEqual(b''.join(response.streaming_content),payload)
                self.assertEqual(self.client.post('/map/basemap.pmtiles').status_code,405)
                self.client.logout();self.assertEqual(self.client.get('/map/basemap.pmtiles').status_code,302)

    def test_missing_archive_uses_basic_map_and_explicit_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'missing.pmtiles'
            with override_settings(MAP_ARCHIVE=path):
                response=self.client.get('/map/')
                self.assertTemplateUsed(response,'network/map_basic.html')
                self.assertContains(response,'参考底图尚未安装')
                self.assertEqual(self.client.get('/map/basemap.pmtiles').status_code,404)
            path.write_bytes(b'PMTiles\x03')
            with override_settings(MAP_ARCHIVE=path):
                self.assertTemplateUsed(self.client.get('/map/'),'network/map.html')
                self.assertTemplateUsed(self.client.get('/map/?basic=1'),'network/map_basic.html')
