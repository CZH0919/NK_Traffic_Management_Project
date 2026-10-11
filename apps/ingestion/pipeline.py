"""Local CLI ETL. Each chunk commits raw evidence, typed rows and checkpoint together."""
from __future__ import annotations
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, datetime, time
from pathlib import Path

from django.conf import settings
from django.db import connection, models, transaction
from django.utils import timezone
from psycopg import sql
from psycopg.types.json import Jsonb
from pyproj import Transformer
from shapely import from_wkt, to_wkt
from shapely.geometry import shape, MultiLineString
from shapely.ops import transform
from shapely.errors import ShapelyError
from apps.core.audit import record_event
from apps.core.models import Borough
from apps.core.time_rules import resolve_wall_time
from apps.collisions.models import (Collision, Vehicle, Person, Factor, CollisionFactor,
    VehicleFactor, PersonFactor, VehicleDamage)
from apps.network.models import NetworkVersion, Junction, RoadSegment
from apps.traffic.models import SamplingRequest, SamplingSite, TrafficObservation
from .models import DataSource, Snapshot, ImportBatch, StagedRecord, QualityIssue

from apps.core.storage import storage_root
ROOT = storage_root()
ENTITY = {'crashes':Collision, 'vehicles':Vehicle, 'persons':Person, 'traffic':TrafficObservation,
          'nodes':Junction, 'roads':RoadSegment}
BORO = {'MANHATTAN':1, 'BRONX':2, 'BROOKLYN':3, 'QUEENS':4, 'STATEN ISLAND':5}
TO_WGS = Transformer.from_crs(2263, 4326, always_xy=True)
COUNTS = {'persons_injured':'number_of_persons_injured', 'persons_killed':'number_of_persons_killed',
    'pedestrians_injured':'number_of_pedestrians_injured', 'pedestrians_killed':'number_of_pedestrians_killed',
    'cyclists_injured':'number_of_cyclist_injured', 'cyclists_killed':'number_of_cyclist_killed',
    'motorists_injured':'number_of_motorist_injured', 'motorists_killed':'number_of_motorist_killed'}

@contextmanager
def import_lock():
    with connection.cursor() as c:
        c.execute('SELECT pg_try_advisory_lock(%s,%s)',[20261004,2])
        if not c.fetchone()[0]: raise RuntimeError('Another import or verification is running')
    try: yield
    finally:
        with connection.cursor() as c: c.execute('SELECT pg_advisory_unlock(%s,%s)',[20261004,2])

def safe_raw_path(relative):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to((ROOT/'data/raw').resolve()): raise ValueError('Raw path outside data/raw')
    return path

def read_manifest(path):
    path = Path(path).resolve()
    if not path.is_relative_to((ROOT/'data/manifests').resolve()): raise ValueError('Manifest outside data/manifests')
    m = json.loads(path.read_text(encoding='utf-8'))
    if m.get('status')!='complete' or m.get('kind') not in {*ENTITY, 'boundaries'}:
        raise ValueError('Incomplete or unsupported manifest')
    required={'crashes':{'collision_id','crash_date','crash_time'},
        'vehicles':{'unique_id','collision_id','vehicle_id'},'persons':{'unique_id','collision_id','vehicle_id'},
        'traffic':{'requestid','segmentid','yr','m','d','hh','mm','vol','wktgeom'},
        'roads':{'SegmentID','NodeIDFrom','NodeIDTo'},'nodes':{'NODEID'}}
    if 'fields' in m and not required.get(m['kind'],set()).issubset(m['fields']):
        raise ValueError('Required source schema changed')
    total=0
    for part in m['files']:
        with gzip.open(safe_raw_path(part['path']),'rb') as f: raw=f.read()
        rows=json.loads(raw)
        if hashlib.sha256(raw).hexdigest()!=part['sha256'] or len(rows)!=part['rows']:
            raise ValueError('Raw page integrity/count mismatch')
        if not all(isinstance(r,dict) for r in rows): raise ValueError('Expected object records')
        total+=len(rows)
    digest=hashlib.sha256(''.join(p['sha256'] for p in m['files']).encode()).hexdigest()
    if total!=m['row_count'] or total!=m['expected_rows'] or digest!=m['checksum']:
        raise ValueError('Manifest reconciliation failed')
    m['manifest_path']=path.relative_to(ROOT).as_posix()
    return m

def register(m, *, source=None):
    kind=m['kind']
    # S01's registered source is retained, including its explanatory description.
    if source is not None and (source.kind!=kind or source.dataset_id!=m['dataset']):
        raise ValueError('Manifest/source identity mismatch')
    source=source or DataSource.objects.filter(kind=kind,dataset_id=m['dataset']).first() or DataSource.objects.filter(kind=kind).first()
    if source is None:
        source=DataSource.objects.create(code='nyc-'+kind,name='NYC '+kind,publisher='NYC Open Data',
            dataset_id=m['dataset'],kind=kind,homepage=m['source_url'])
    if kind=='boundaries':
        source.dataset_id=m['dataset']; source.homepage='https://data.cityofnewyork.us/d/'+m['dataset']
        source.name='纽约行政区边界'; source.publisher='NYC DCP'
        source.description='五区行政边界，包含水域；同版陆域边界另存原始附件。空间归属采用唯一包含关系，来源报告与空间推导分开记录。'
        source.save(update_fields=['dataset_id','homepage','name','publisher','description'])
    snap,_=Snapshot.objects.get_or_create(source=source,checksum=m['checksum'],defaults={
        'version':m.get('version',m.get('period_start',''))+' / '+m['label'],
        'captured_at':datetime.fromisoformat(m['captured_at']), 'period_start':m.get('period_start'),
        'period_end':m.get('period_end'), 'data_through':m.get('period_end'),
        'relative_path':m['manifest_path'], 'source_count':m['row_count'],
        'native_crs':m.get('native_crs','EPSG:2263 (inferred; see quality report)' if kind=='traffic' else 'EPSG:4326' if kind=='crashes' else ''),
        'manifest':m})
    return snap

def copy_objects(model, objects):
    """COPY through the existing application connection; no server filesystem privilege.

    Allocate sequence IDs first to connect typed rows and side tables within a chunk.
    Django defaults are materialized by model construction; auto timestamps are set here.
    Caller owns the atomic transaction. SQL identifiers originate only in model metadata.
    """
    if not objects: return
    fields=list(model._meta.local_concrete_fields)
    pk=model._meta.pk
    with connection.cursor() as cursor:
        if isinstance(pk, models.AutoField):
            cursor.execute('SELECT nextval(pg_get_serial_sequence(%s, %s)) FROM generate_series(1,%s)',
                           [model._meta.db_table,pk.column,len(objects)])
            for obj, (identity,) in zip(objects,cursor.fetchall()): obj.pk=identity
        now=timezone.now()
        query=sql.SQL('COPY {} ({}) FROM STDIN').format(sql.Identifier(model._meta.db_table),
            sql.SQL(',').join(sql.Identifier(f.column) for f in fields))
        with cursor.copy(query) as stream:
            for obj in objects:
                values=[]
                for field in fields:
                    value=getattr(obj,field.attname)
                    if getattr(field,'auto_now',False) or getattr(field,'auto_now_add',False):
                        value=now; setattr(obj,field.attname,value)
                    if isinstance(field,models.JSONField): value=Jsonb(value)
                    values.append(value)
                stream.write_row(values)
        for obj in objects: obj._state.adding=False; obj._state.db='default'

def canonical_id(value):
    if value in (None,''): return ''
    try:
        n=int(str(value).strip())
        return str(n) if n>=0 else ''
    except (ValueError,TypeError): return ''

def source_key(kind,row):
    if kind=='crashes': return canonical_id(row.get('collision_id'))
    if kind in ('persons','vehicles'): return canonical_id(row.get('unique_id'))
    if kind=='traffic': return str(row.get('source_row_id',''))
    if kind=='nodes': return canonical_id(row.get('NODEID'))
    if kind=='roads': return canonical_id(row.get('_fid'))
    return canonical_id(row.get('borocode'))

def local_fields(row, issues):
    try: day=date.fromisoformat(str(row.get('crash_date',''))[:10])
    except ValueError: issues['invalid_date'].append('crash_date'); return None,None,None,'missing'
    try:
        parts=str(row.get('crash_time','')).split(':')
        clock=time(int(parts[0]),int(parts[1]))
    except (ValueError,IndexError): clock=None; issues['missing_or_invalid_time'].append('crash_time')
    instant,status=resolve_wall_time(day,clock)
    if status in ('ambiguous','nonexistent'): issues['dst_'+status].append('crash_time')
    return day,clock,instant,status

def text_field(row, name, limit, issues):
    value=str(row.get(name) or '').strip()
    if (limit is not None and len(value)>limit) or '\x00' in value:
        issues['invalid_text'].append(name); return ''
    return value

def integer(row, name, issues, maximum=2147483647):
    value=row.get(name)
    if value in (None,''): return None
    try:
        number=float(value)
        if not math.isfinite(number) or number!=int(number) or not 0<=number<=maximum: raise ValueError
        return int(number)
    except (ValueError,TypeError,OverflowError): issues['invalid_number'].append(name); return None

def projected_geometry(wkt, expected, issues):
    try:
        native=from_wkt(wkt)
        if native is None or native.is_empty or not native.is_valid: raise ValueError
        if expected=='MultiLineString' and native.geom_type=='LineString': native=MultiLineString([native])
        if native.geom_type!=expected: raise ValueError
        geom=transform(TO_WGS.transform,native)
        if not geom.is_valid or geom.is_empty or not all(math.isfinite(v) for v in geom.bounds): raise ValueError
        if not (-75<geom.bounds[0]<-72 and 39<geom.bounds[1]<42): raise ValueError
        return 'SRID=4326;'+to_wkt(geom,rounding_precision=12), native.length*1200/3937
    except (ValueError,TypeError,ShapelyError): issues['invalid_geometry'].append('geometry'); return None,None

def base_entity(raw,snapshot):
    return dict(source_id=snapshot.source_id, source_record_id=raw.source_key, raw_record_id=raw.id, origin='official')

class Importer:
    def __init__(self,snapshot,network=None,notices=None):
        self.snapshot=snapshot; self.kind=snapshot.source.kind; self.network=network
        self.notices=notices or set(); self.site_cache={}; self.request_cache={}
        self.sources=dict(DataSource.objects.filter(code__startswith='nyc-').values_list('kind','id'))
        self.factors=dict(Factor.objects.values_list('name','id'))
        self.node_cache=dict(Junction.objects.filter(network=network).values_list('source_node_id','id')) if network else {}

    def refs(self, rows):
        collision_keys={canonical_id(r.get('collision_id')) for r in rows}
        self.crashes={k:(pk,day,clock) for k,pk,day,clock in Collision.objects.filter(
            origin='official',source_id=self.sources.get('crashes'),source_record_id__in=collision_keys).values_list('source_record_id','id','local_date','local_time')}
        self.vehicles={k:(pk,cid) for k,pk,cid in Vehicle.objects.filter(origin='official',
            source_id=self.sources.get('vehicles'),
            source_record_id__in={canonical_id(r.get('vehicle_id')) for r in rows}).values_list('source_record_id','id','collision_id')} if self.kind=='persons' else {}

    def parse(self, raw, issues):
        r=raw.payload; kind=self.kind
        if kind=='crashes':
            day,clock,instant,status=local_fields(r,issues)
            if day is None: return None
            borough=BORO.get(str(r.get('borough','')).upper())
            if not borough: issues['missing_borough'].append('borough')
            geom=None
            try:
                x,y=float(r['longitude']),float(r['latitude'])
                if not(-74.3<x<-73.65 and 40.45<y<40.95): raise ValueError
                geom=f'SRID=4326;POINT ({x} {y})'
            except (KeyError,ValueError,TypeError): issues['missing_or_outside_nyc_coordinate'].append('location')
            values={f:integer(r,s,issues) for f,s in COUNTS.items()}
            if any(v is None for v in values.values()): issues['missing_casualty_count'].extend(f for f,v in values.items() if v is None)
            for result in ('injured','killed'):
                total=values['persons_'+result]; parts=[values[p+'_'+result] for p in ('pedestrians','cyclists','motorists')]
                if total is not None and all(v is not None for v in parts) and total!=sum(parts): issues['casualty_subtotal_mismatch'].append(result)
            return Collision(**base_entity(raw,self.snapshot),local_date=day,local_time=clock,occurred_at=instant,time_status=status,
                borough_id=borough,borough_method='reported' if borough else 'unknown',geom=geom,**values,
                **{f:text_field(r,s,limit,issues) for f,s,limit in [('postal_code','zip_code',20),('on_street','on_street_name',200),('off_street','off_street_name',200),('cross_street','cross_street_name',200)]})
        if kind in ('vehicles','persons'):
            key=canonical_id(r.get('collision_id')); parent=self.crashes.get(key)
            day,clock,_,_=local_fields(r,issues)
            if not parent: issues['orphan_collision'].append('collision_id')
            elif (day,clock)!=(parent[1],parent[2]): issues['parent_time_mismatch'].append('crash_date/time')
            common=dict(**base_entity(raw,self.snapshot),collision_id=parent[0] if parent else None,source_collision_id=key)
            if kind=='vehicles':
                mapping={'source_vehicle_uuid':'vehicle_id','vehicle_type':'vehicle_type','make':'vehicle_make','model':'vehicle_model',
                    'state_registration':'state_registration','travel_direction':'travel_direction','pre_crash':'pre_crash','driver_sex':'driver_sex',
                    'license_status':'driver_license_status','license_jurisdiction':'driver_license_jurisdiction','point_of_impact':'point_of_impact',
                    'public_property_damage':'public_property_damage','public_property_damage_type':'public_property_damage_type'}
                common.update(manufacture_year=integer(r,'vehicle_year',issues,32767),occupants=integer(r,'vehicle_occupants',issues))
                year=common['manufacture_year']
                if year is not None and (year<1886 or (day and year>day.year+1)):
                    issues['implausible_vehicle_year'].append('vehicle_year'); common['manufacture_year']=None
                cls=Vehicle
            else:
                vid=canonical_id(r.get('vehicle_id')); vehicle=self.vehicles.get(vid)
                if vid and not vehicle: issues['orphan_vehicle'].append('vehicle_id')
                if vehicle and (not parent or vehicle[1]!=parent[0]):
                    issues['vehicle_collision_mismatch'].append('vehicle_id'); vehicle=None
                common.update(source_vehicle_id=vid,vehicle_id=vehicle[0] if vehicle else None,age=integer(r,'person_age',issues,130))
                mapping={'source_person_uuid':'person_id','person_type':'person_type','injury':'person_injury','sex':'person_sex',
                    'ejection':'ejection','emotional_status':'emotional_status','bodily_injury':'bodily_injury','position_in_vehicle':'position_in_vehicle',
                    'safety_equipment':'safety_equipment','pedestrian_location':'ped_location','pedestrian_action':'ped_action',
                    'pedestrian_role':'ped_role','complaint':'complaint'}
                cls=Person
            common.update({f:text_field(r,s,cls._meta.get_field(f).max_length,issues) for f,s in mapping.items()})
            return cls(**common)
        if kind in ('nodes','roads'):
            geom,length=projected_geometry(r.get('_geometry_wkt'),'Point' if kind=='nodes' else 'MultiLineString',issues)
            if not geom: return None
            if kind=='nodes': return Junction(network=self.network,source_node_id=raw.source_key,geom=geom)
            start=self.node_cache.get(canonical_id(r.get('NodeIDFrom'))); end=self.node_cache.get(canonical_id(r.get('NodeIDTo')))
            if not start or not end: issues['missing_network_endpoint'].append('NodeIDFrom/To')
            lb=canonical_id(r.get('LBoro')); rb=canonical_id(r.get('RBoro'))
            return RoadSegment(network=self.network,source_feature_id=raw.source_key,source_segment_id=str(r.get('SegmentID') or ''),
                name=text_field(r,'Street',200,issues),feature_type=str(r.get('FeatureTyp') or ''),traffic_direction=str(r.get('TrafDir') or ''),
                from_node_id=start,to_node_id=end,left_borough_id=int(lb) if lb in {'1','2','3','4','5'} else None,
                right_borough_id=int(rb) if rb in {'1','2','3','4','5'} else None,geom=geom,length_m=round(length,3))
        if kind=='traffic': return self.flow(raw,issues)
        if kind=='boundaries':
            try:
                geom=shape(r['the_geom'])
                if geom.geom_type!='MultiPolygon' or geom.is_empty or not geom.is_valid: raise ValueError
                borough=Borough.objects.get(code=int(r['borocode']))
                # Never silently replace an already imported different boundary version.
                if borough.boundary_snapshot_id not in (None,self.snapshot.id): raise ValueError('Boundary version already assigned')
                borough.geom='SRID=4326;'+to_wkt(geom,rounding_precision=12); borough.boundary_snapshot=self.snapshot
                borough.save(update_fields=['geom','boundary_snapshot']); return borough
            except (KeyError,TypeError): issues['invalid_geometry'].append('the_geom'); return None

    def flow(self,raw,issues):
        r=raw.payload
        try:
            day=date(int(r['yr']),int(r['m']),int(r['d'])); clock=time(int(r['hh']),int(r['mm']))
        except (ValueError,TypeError,KeyError): issues['invalid_sampling_time'].append('yr/m/d/hh/mm'); return None
        if clock.minute%15:
            issues['non_quarter_hour_sampling'].append('mm'); return None
        volume=integer(r,'vol',issues)
        if volume is None: issues['missing_volume'].append('vol'); return None
        req=canonical_id(r.get('requestid')); seg=canonical_id(r.get('segmentid'))
        if not req or not seg: issues['invalid_site_key'].append('requestid/segmentid'); return None
        # Physical/time conflict identity is the source's candidate key, regardless of description variations.
        direction=text_field(r,'direction',30,issues)
        if not direction: issues['invalid_site_key'].append('direction'); return None
        location_key=hashlib.sha256(json.dumps([seg,direction],separators=(',',':')).encode()).hexdigest()
        site_key=(req,location_key)
        if site_key not in self.site_cache:
            if req not in self.request_cache:
                request,_=SamplingRequest.objects.get_or_create(source_id=self.snapshot.source_id,source_request_id=req)
                self.request_cache[req]=request
            geom,_=projected_geometry(r.get('wktgeom'),'Point',issues)
            site,_=SamplingSite.objects.get_or_create(request=self.request_cache[req],location_key=location_key,defaults={
                'source_segment_id':seg,'direction':direction,'street':text_field(r,'street',200,issues),
                'from_street':text_field(r,'fromst',200,issues),'to_street':text_field(r,'tost',200,issues),
                'borough_id':BORO.get(str(r.get('boro','')).upper()),'geom':geom})
            self.site_cache[site_key]=site
        site=self.site_cache[site_key]
        if (req,seg) in self.notices: issues['official_location_notice'].append('requestid/segmentid')
        instant,status=resolve_wall_time(day,clock)
        if status!='unique': issues['dst_'+status].append('local_time')
        # Native CRS inferred, not asserted as explicit source metadata; S04 must validate road mapping.
        quality='excluded' if 'official_location_notice' in issues or not site.geom or status!='unique' else 'valid'
        return TrafficObservation(**base_entity(raw,self.snapshot),site=site,local_date=day,local_time=clock,
            observed_at=instant,time_status=status,volume=volume,quality_status=quality)

    def side_tables(self,pairs):
        factors=[]; names=set(); damages=[]
        for obj,raw,issues in pairs:
            r=raw.payload
            if self.kind=='crashes':
                for slot in range(1,6):
                    name=text_field(r,f'contributing_factor_vehicle_{slot}',160,issues)
                    vehicle_type=text_field(r,f'vehicle_type_code{slot}',100,issues)
                    if name or vehicle_type: factors.append((obj,slot,name,vehicle_type))
                    if name: names.add(name)
            elif self.kind in ('vehicles','persons'):
                for slot in range(1,3):
                    name=text_field(r,f'contributing_factor_{slot}',160,issues)
                    if name: factors.append((obj,slot,name,'')); names.add(name)
                if self.kind=='vehicles':
                    for slot in range(4):
                        name=text_field(r,'vehicle_damage'+(f'_{slot}' if slot else ''),160,issues)
                        if name: damages.append(VehicleDamage(vehicle_id=obj.pk,source_slot=slot,description=name))
        missing=names-self.factors.keys()
        if missing:
            created=[Factor(name=n) for n in sorted(missing)]; copy_objects(Factor,created)
            self.factors.update((f.name,f.pk) for f in created)
        cls={'crashes':CollisionFactor,'vehicles':VehicleFactor,'persons':PersonFactor}.get(self.kind)
        if cls:
            fk={'crashes':'collision_id','vehicles':'vehicle_id','persons':'person_id'}[self.kind]
            rows=[cls(**{fk:o.pk,'source_slot':slot,'factor_id':self.factors.get(name),
                **({'vehicle_type':vtype} if self.kind=='crashes' else {})}) for o,slot,name,vtype in factors]
            copy_objects(cls,rows)
        copy_objects(VehicleDamage,damages)

    def chunk(self,rows,offset,batch):
        raws=[StagedRecord(snapshot=self.snapshot,row_number=offset+i+1,source_key=source_key(self.kind,r),payload=r) for i,r in enumerate(rows)]
        cls=ENTITY.get(self.kind)
        existing={}
        if self.kind in ('crashes','vehicles','persons','traffic'):
            existing={key:payload for key,payload in cls.objects.filter(source_id=self.snapshot.source_id,
                source_record_id__in=[r.source_key for r in raws]).values_list('source_record_id','raw_record__payload')}
        elif self.kind in ('nodes','roads'):
            field='source_node_id' if self.kind=='nodes' else 'source_feature_id'
            existing={key:None for key in cls.objects.filter(network=self.network,**{field+'__in':[r.source_key for r in raws]}).values_list(field,flat=True)}
        if self.kind in ('vehicles','persons'): self.refs(rows)
        pairs=[]; issues_by_raw=[]; seen={}; counts=Counter()
        for raw in raws:
            issues=defaultdict(list); issues_by_raw.append((raw,issues))
            if not raw.source_key or len(raw.source_key)>150:
                issues['missing_source_key'].append('source_key'); raw.disposition='rejected'
                raw.source_key=''
            elif raw.source_key in existing or raw.source_key in seen:
                prior=existing.get(raw.source_key,seen.get(raw.source_key))
                comparable=lambda r:{k:v for k,v in (r or {}).items() if k!='source_row_id'}
                equal=comparable(prior)==comparable(raw.payload)
                raw.disposition='skipped' if equal else 'conflict'
                issues['duplicate_source_key' if equal else 'changed_source_key'].append('source_key')
            else:
                obj=self.parse(raw,issues)
                raw.disposition='accepted' if obj is not None else 'rejected'
                if obj is not None: pairs.append((obj,raw,issues)); seen[raw.source_key]=raw.payload
            counts[raw.disposition]+=1
        copy_objects(StagedRecord,raws)
        if self.kind in ('crashes','vehicles','persons','traffic'):
            for obj,raw,_ in pairs: obj.raw_record_id=raw.pk
        if cls: copy_objects(cls,[obj for obj,_,_ in pairs])
        self.side_tables(pairs)
        copy_objects(QualityIssue,[QualityIssue(snapshot=self.snapshot,record_id=raw.pk,code=code,
            severity='error' if raw.disposition in ('rejected','conflict') else 'warning',details={'fields':sorted(set(fields))})
            for raw,issues in issues_by_raw for code,fields in issues.items()])
        batch.checkpoint=offset+len(rows); batch.accepted_rows+=counts['accepted']; batch.rejected_rows+=counts['rejected']
        batch.pending_rows+=counts['conflict']+counts['pending']; batch.skipped_rows+=counts['skipped']
        batch.save(update_fields=['checkpoint','accepted_rows','rejected_rows','pending_rows','skipped_rows'])

def import_snapshot(snapshot, *, network=None, notices=None, chunk_size=2000, after_chunk=None, actor=None):
    if not 1<=chunk_size<=10000: raise ValueError('Chunk size must be 1..10000')
    # Cross-source relations use a fixed ordered CLI run. This session lock prevents competing importers.
    with connection.cursor() as c:
        c.execute('SELECT pg_try_advisory_lock(%s,%s)',[20261004,2])
        if not c.fetchone()[0]: raise RuntimeError('Another import is running')
    batch=None
    try:
        previous=ImportBatch.objects.filter(snapshot=snapshot).order_by('-attempt').first()
        if previous and previous.status in ('succeeded','partial') and previous.checkpoint==snapshot.source_count:
            return previous,True
        with transaction.atomic():
            if previous and previous.status=='running':
                previous.status='failed'; previous.error_code='interrupted'; previous.finished_at=timezone.now(); previous.save()
            batch=ImportBatch.objects.create(snapshot=snapshot,created_by=actor,attempt=previous.attempt+1 if previous else 1,
                started_at=timezone.now(),status='running',total_rows=snapshot.source_count,
                **{field:getattr(previous,field) if previous else 0 for field in ('checkpoint','accepted_rows','rejected_rows','pending_rows','skipped_rows')})
            if StagedRecord.objects.filter(snapshot=snapshot).count()!=batch.checkpoint: raise ValueError('Checkpoint/raw rows differ')
            record_event(actor=actor,action='import_started',object_type='ImportBatch',object_id=batch.pk,details={'snapshot_id':snapshot.pk})
        importer=Importer(snapshot,network,notices); offset=0
        for part in snapshot.manifest['files']:
            if offset+part['rows']<=batch.checkpoint: offset+=part['rows']; continue
            with gzip.open(safe_raw_path(part['path']),'rt',encoding='utf-8') as f: rows=json.load(f)
            for start in range(0,len(rows),chunk_size):
                group=rows[start:start+chunk_size]
                if offset+start+len(group)<=batch.checkpoint: continue
                # Resume correctly even when chunk size changes between attempts.
                skip=max(0,batch.checkpoint-(offset+start)); group=group[skip:]
                with transaction.atomic(): importer.chunk(group,offset+start+skip,batch)
                if after_chunk: after_chunk(batch)
            offset+=len(rows)
            print(json.dumps({'kind':snapshot.source.kind,'snapshot':snapshot.pk,'rows':batch.checkpoint,'total':batch.total_rows}),flush=True)
        if batch.checkpoint!=snapshot.source_count: raise ValueError('Final checkpoint mismatch')
        with transaction.atomic():
            batch.status='partial' if batch.pending_rows or batch.rejected_rows else 'succeeded'; batch.finished_at=timezone.now(); batch.save()
            snapshot.status='validated'; snapshot.save(update_fields=['status'])
            record_event(actor=actor,action='import_finished',object_type='ImportBatch',object_id=batch.pk,details={'count':batch.checkpoint,'snapshot_id':snapshot.pk})
        return batch,False
    except Exception as exc:
        if batch and ImportBatch.objects.filter(pk=batch.pk).exists():
            with transaction.atomic():
                # Reload committed counters: the failing chunk may have changed in-memory values before rollback.
                batch.refresh_from_db(); batch.status='failed'; batch.error_code=type(exc).__name__; batch.finished_at=timezone.now(); batch.save()
                record_event(actor=actor,action='import_finished',object_type='ImportBatch',object_id=batch.pk,outcome='failure',details={'reason_code':type(exc).__name__})
        raise
    finally:
        with connection.cursor() as c: c.execute('SELECT pg_advisory_unlock(%s,%s)',[20261004,2])
