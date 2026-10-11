"""Conservative, versioned spatial associations; source geometries remain untouched."""
import json
import re
from decimal import Decimal
from django.db import connection,transaction
from django.db.models import Count
from django.utils import timezone
from apps.core.audit import record_event
from apps.core.models import DataRevision
from apps.collisions.models import Collision
from apps.traffic.models import SamplingSite
from .models import NetworkVersion,RoadSegment,Junction,SpatialMatch,SamplingRoadMatch,SpatialRun

RULE='nyc-spatial-v1'
PARAMETERS={'candidate_radius_m':80,'named_road_max_m':25,'unique_road_max_m':12,
    'candidate_gap_m':8,'junction_max_m':15,'junction_gap_m':8,'source_id_max_m':25,'bearing_tolerance_deg':50}
WORDS={'ST':'STREET','AVE':'AVENUE','AV':'AVENUE','BLVD':'BOULEVARD','RD':'ROAD','DR':'DRIVE','PL':'PLACE',
    'PKWY':'PARKWAY','EXPY':'EXPRESSWAY','EXPWY':'EXPRESSWAY','HWY':'HIGHWAY','LN':'LANE','CT':'COURT',
    'N':'NORTH','S':'SOUTH','E':'EAST','W':'WEST','FT':'FORT'}
CARDINAL={'NB':0,'EB':90,'SB':180,'WB':270}

def name_key(value):
    value=re.sub(r'(?<=\d)(ST|ND|RD|TH)\b','',str(value or '').upper())
    return ' '.join(WORDS.get(token,token) for token in re.findall(r'[A-Z0-9]+',value))

def rows(sql,params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql,params)
        names=[x[0] for x in cursor.description]
        return [dict(zip(names,row)) for row in cursor.fetchall()]

def json_value(value): return json.loads(value) if isinstance(value,str) else value

@transaction.atomic
def prepare_network(network):
    """Build source-backed cartographic fields, duplicate groups and conservative junctions."""
    with connection.cursor() as c:
        c.execute("""UPDATE network_roadsegment r SET rb_layer=trim(coalesce(s.payload->>'RB_Layer','')),
          segment_type=trim(coalesce(s.payload->>'SegmentTyp','')),
          level_from=trim(coalesce(s.payload->>'NodeLevelF','')),level_to=trim(coalesce(s.payload->>'NodeLevelT','')),
          road_type=trim(coalesce(s.payload->>'RW_TYPE','')), normalized_segment_id=ltrim(r.source_segment_id,'0')
          FROM ingestion_stagedrecord s WHERE r.network_id=%s AND s.snapshot_id=%s AND s.source_key=r.source_feature_id""",
          [network.pk,network.snapshot_id])
        if c.rowcount!=RoadSegment.objects.filter(network=network).count(): raise ValueError('Road source rows do not reconcile')
        c.execute("""WITH groups AS (SELECT source_segment_id,min(id) primary_id,count(*) n,
            count(DISTINCT ST_AsEWKB(geom)) variants,
            count(DISTINCT ROW(feature_type,traffic_direction,from_node_id,to_node_id,rb_layer,segment_type,level_from,level_to)) attr_variants,
            jsonb_agg(DISTINCT name ORDER BY name) aliases
          FROM network_roadsegment WHERE network_id=%s GROUP BY source_segment_id)
          UPDATE network_roadsegment r SET canonical_id=g.primary_id,is_canonical=(r.id=g.primary_id),
            source_variants_count=g.n,geometry_variants=g.variants,aliases=g.aliases,
            match_eligible=(g.variants=1 AND g.attr_variants=1 AND r.feature_type IN ('0','6','A','C')
              AND r.traffic_direction IN ('W','A','T') AND r.rb_layer IN ('R','B') AND r.segment_type NOT IN ('C','T','G','F')),
            classification_evidence=jsonb_build_object('rule',%s,'source_snapshot',%s,'attribute_variants',g.attr_variants,
              'scope','physical_vehicular_roadbed','historical_geometry',false)
          FROM groups g WHERE r.network_id=%s AND r.source_segment_id=g.source_segment_id""",
          [network.pk,RULE,network.snapshot_id,network.pk])
        c.execute("""UPDATE network_roadsegment r SET bearing_deg=degrees(ST_Azimuth(f.geom::geography,t.geom::geography))
          FROM network_junction f,network_junction t WHERE r.network_id=%s AND f.id=r.from_node_id AND t.id=r.to_node_id""",[network.pk])
        if network.node_snapshot_id:
            c.execute("""UPDATE network_junction n SET virtual_intersection=(coalesce(r.payload->>'VIntersect','')='VirtualIntersection')
              FROM ingestion_stagedrecord r WHERE n.network_id=%s AND r.snapshot_id=%s AND r.source_key=n.source_node_id""",
              [network.pk,network.node_snapshot_id])
        c.execute("""WITH edges AS (
          SELECT id,name,from_node_id AS node_id,level_from AS level FROM network_roadsegment
             WHERE network_id=%s AND is_canonical AND match_eligible AND is_active
          UNION SELECT id,name,to_node_id,level_to FROM network_roadsegment
             WHERE network_id=%s AND is_canonical AND match_eligible AND is_active),
          stats AS (SELECT node_id,count(DISTINCT id) total_edges,
            count(DISTINCT id) FILTER(WHERE level='M') ground_edges,
            count(DISTINCT name) FILTER(WHERE level='M' AND name<>'') name_count,
            coalesce(jsonb_agg(DISTINCT name ORDER BY name) FILTER(WHERE level='M' AND name<>''),'[]') names
            FROM edges GROUP BY node_id), all_nodes AS (
          SELECT n.id,coalesce(s.total_edges,0) total_edges,coalesce(s.ground_edges,0) ground_edges,
            coalesce(s.name_count,0) name_count,coalesce(s.names,'[]'::jsonb) names
            FROM network_junction n LEFT JOIN stats s ON s.node_id=n.id WHERE n.network_id=%s)
          UPDATE network_junction n SET road_degree=a.ground_edges,street_names=a.names,
            name=coalesce(left((SELECT string_agg(x,' / ') FROM jsonb_array_elements_text(a.names) x),200),''),
            is_intersection=CASE WHEN n.classification_method LIKE 'manual%%' THEN n.is_intersection
              WHEN n.virtual_intersection THEN NULL WHEN a.ground_edges>=3 AND a.name_count>=2 THEN true
              WHEN a.total_edges=0 OR (a.ground_edges=a.total_edges AND a.name_count<=1) THEN false ELSE NULL END,
            classification_method=CASE WHEN n.classification_method LIKE 'manual%%' THEN n.classification_method ELSE %s END,
            classification_evidence=jsonb_build_object('ground_edges',a.ground_edges,'total_road_edges',a.total_edges,
                'street_names',a.name_count,'virtual',n.virtual_intersection,'rule',%s)
          FROM all_nodes a WHERE a.id=n.id""",[network.pk,network.pk,network.pk,'derived:'+RULE,RULE])
        # Empty name remains a real empty value, never a fake named intersection.
        c.execute("UPDATE network_junction SET name='' WHERE network_id=%s AND name IS NULL",[network.pk])
        c.execute("""WITH membership AS (SELECT n.id,CASE WHEN count(b.code)=1 THEN min(b.code) ELSE NULL END borough
          FROM network_junction n LEFT JOIN core_borough b ON n.geom && b.geom AND ST_Covers(b.geom,n.geom)
          WHERE n.network_id=%s GROUP BY n.id)
          UPDATE network_junction n SET borough_id=m.borough FROM membership m WHERE n.id=m.id""",[network.pk])
    return {'canonical_roads':RoadSegment.objects.filter(network=network,is_canonical=True).count(),
        'eligible_roads':RoadSegment.objects.filter(network=network,is_canonical=True,match_eligible=True).count(),
        'junctions':Junction.objects.filter(network=network,is_intersection=True).count(),
        'uncertain_nodes':Junction.objects.filter(network=network,is_intersection__isnull=True).count()}

def road_candidates(geom,network_id,source_id=None):
    if not geom: return []
    values=rows("""SELECT r.id,r.name,r.source_segment_id,r.normalized_segment_id,r.aliases,r.traffic_direction,
        r.bearing_deg,r.revision,ST_Distance(r.geom::geography,ST_GeomFromEWKT(%s)::geography) distance_m
      FROM network_roadsegment r WHERE r.network_id=%s AND r.is_canonical AND r.match_eligible AND r.is_active
        AND r.geom && ST_Expand(ST_GeomFromEWKT(%s),0.0011)
        AND ST_DWithin(r.geom::geography,ST_GeomFromEWKT(%s)::geography,%s)
      ORDER BY r.geom <-> ST_GeomFromEWKT(%s),r.id LIMIT 12""",[geom,network_id,geom,geom,PARAMETERS['candidate_radius_m'],geom])
    for row in values:
        row['aliases']=json_value(row['aliases']); row['distance_m']=round(float(row['distance_m']),3)
        row['kind']='road'; row['source_id_match']=bool(source_id and row['normalized_segment_id']==str(source_id).lstrip('0'))
    return sorted(values,key=lambda r:(r['distance_m'],r['id']))

def junction_candidates(geom,network_id):
    if not geom: return []
    values=rows("""SELECT id,name,street_names,revision,
        ST_Distance(geom::geography,ST_GeomFromEWKT(%s)::geography) distance_m
      FROM network_junction WHERE network_id=%s AND is_active AND is_intersection IS TRUE
        AND geom && ST_Expand(ST_GeomFromEWKT(%s),0.0006)
        AND ST_DWithin(geom::geography,ST_GeomFromEWKT(%s)::geography,40)
      ORDER BY geom <-> ST_GeomFromEWKT(%s),id LIMIT 4""",[geom,network_id,geom,geom,geom])
    for row in values:
        row['street_names']=json_value(row['street_names']);row['distance_m']=round(float(row['distance_m']),3);row['kind']='junction'
    return sorted(values,key=lambda r:(r['distance_m'],r['id']))

def direction_matches(road,direction):
    bearing=road.get('bearing_deg'); expected=CARDINAL.get(direction)
    if bearing is None or expected is None: return None
    def difference(a,b): return abs((a-b+180)%360-180)
    allowed=[bearing] if road['traffic_direction']=='W' else [(bearing+180)%360] if road['traffic_direction']=='A' else [bearing,(bearing+180)%360]
    return min(difference(value,expected) for value in allowed)<=PARAMETERS['bearing_tolerance_deg']

def pick_named(roads,names,*,site=False,direction=None):
    keys={name_key(x) for x in names if name_key(x)}
    for candidate in roads:
        candidate['name_match']=bool(keys & {name_key(x) for x in [candidate['name'],*candidate['aliases']]})
        if site: candidate['direction_compatible']=direction_matches(candidate,direction)
    eligible=[r for r in roads if not site or r.get('direction_compatible') is True]
    if site:
        exact=[r for r in eligible if r['source_id_match'] and r['distance_m']<=PARAMETERS['source_id_max_m'] and r['name_match']]
        if len(exact)==1: return exact[0],'source_id_name_geometry'
    named=[r for r in eligible if r['name_match'] and r['distance_m']<=PARAMETERS['named_road_max_m']]
    if named and (len(named)==1 or named[1]['distance_m']-named[0]['distance_m']>=PARAMETERS['candidate_gap_m']):
        return named[0],'name_geometry'
    if eligible and eligible[0]['distance_m']<=PARAMETERS['unique_road_max_m']:
        # Do not override an explicitly disagreeing source street using pure proximity.
        if not keys and (len(eligible)==1 or eligible[1]['distance_m']-eligible[0]['distance_m']>=PARAMETERS['candidate_gap_m']):
            return eligible[0],'unique_geometry'
    return None,'candidate_review' if roads else 'outside_radius'

def collision_proposal(collision,network_id):
    base={'road_id':None,'junction_id':None,'status':'missing','method':'no_geometry','distance_m':None,
        'rule_version':RULE,'input_revision':collision.revision,'candidates':[],
        'evidence':{'radius_m':PARAMETERS['candidate_radius_m'],'coordinate_source':'current_business','inferred':True}}
    if not collision.geom: return base
    roads=road_candidates(collision.geom,network_id); nodes=junction_candidates(collision.geom,network_id)
    main=name_key(collision.on_street); cross=name_key(collision.off_street)
    for node in nodes:
        keys={name_key(x) for x in node['street_names']}
        node['name_match']=bool(main and cross and main!=cross and main in keys and cross in keys)
    named=[n for n in nodes if n['name_match'] and n['distance_m']<=PARAMETERS['junction_max_m']]
    selected=None
    if len(named)==1: selected=named[0]; method='intersection_names_geometry'
    elif not main and not cross and nodes and nodes[0]['distance_m']<=8 and (len(nodes)==1 or nodes[1]['distance_m']-nodes[0]['distance_m']>=PARAMETERS['junction_gap_m']):
        selected=nodes[0];method='unique_ground_junction'
    road,road_method=pick_named(roads,[collision.on_street])
    if selected:
        base.update(junction_id=selected['id'],status='matched',method=method,distance_m=selected['distance_m'])
    elif road:
        base.update(road_id=road['id'],status='matched',method=road_method,distance_m=road['distance_m'])
    else: base.update(status='ambiguous' if roads or nodes else 'unmatched',method=road_method)
    base['candidates']=[*roads[:5],*nodes[:3]]
    return base

def site_proposal(site,network_id):
    base={'road_id':None,'status':'missing','method':'no_geometry','distance_m':None,'rule_version':RULE,
        'input_revision':site.revision,'candidates':[],'source_id_match':False,'direction_compatible':None,
        'evidence':{'source_network':site.source_network_version,'radius_m':PARAMETERS['candidate_radius_m'],'inferred':True}}
    if not site.geom: return base
    roads=road_candidates(site.geom,network_id,site.source_segment_id)
    selected,method=pick_named(roads,[site.street],site=True,direction=site.direction)
    base['candidates']=roads[:8]
    base.update(status='ambiguous' if roads else 'unmatched',method=method)
    if selected:
        base.update(road_id=selected['id'],status='matched',distance_m=selected['distance_m'],
            source_id_match=selected['source_id_match'],direction_compatible=selected['direction_compatible'])
    return base

def save_proposals(model,objects,network,run,proposal):
    key='collision' if model is SpatialMatch else 'site'
    existing={getattr(obj,key+'_id'):obj for obj in model.objects.filter(network=network,**{key+'_id__in':[o.pk for o in objects]})}
    created=[]; updated=[]; fields=None
    for obj in objects:
        old=existing.get(obj.pk)
        if old and old.reviewed_by_id and old.input_revision==obj.revision: continue
        data=proposal(obj,network.pk); fields=list(data)+['revision','run','updated_at','reviewed_by','reviewed_at']
        if old:
            for name,value in data.items(): setattr(old,name,value)
            old.revision+=1;old.run=run;old.updated_at=timezone.now();old.reviewed_by=None;old.reviewed_at=None;updated.append(old)
        else: created.append(model(**{key:obj},network=network,run=run,**data))
    model.objects.bulk_create(created,batch_size=1000)
    if updated: model.objects.bulk_update(updated,fields,batch_size=500)

def run_matching(network,*,chunk_size=1000,resume_id=None,progress=None):
    from apps.ingestion.pipeline import import_lock
    if not 1<=chunk_size<=5000: raise ValueError('Chunk size must be 1..5000')
    with import_lock():
        run=SpatialRun.objects.get(pk=resume_id,network=network,status__in=['failed','running']) if resume_id else SpatialRun.objects.create(network=network,rule_version=RULE,parameters=PARAMETERS)
        if run.rule_version!=RULE: raise ValueError('Cannot resume a different matching rule')
        run.status='running';run.error_code='';run.save()
        try:
            if run.phase=='prepare':
                run.summary=prepare_network(network);run.phase='collisions';run.checkpoint=0;run.save()
                with connection.cursor() as c: c.execute('ANALYZE network_roadsegment,network_junction')
            if run.phase=='collisions':
                while True:
                    objects=list(Collision.objects.filter(pk__gt=run.checkpoint).order_by('pk').only('id','geom','on_street','off_street','revision')[:chunk_size])
                    if not objects: break
                    with transaction.atomic():
                        save_proposals(SpatialMatch,objects,network,run,collision_proposal)
                        run.checkpoint=objects[-1].pk;run.save(update_fields=['checkpoint'])
                    if progress: progress(run)
                run.phase='sites';run.checkpoint=0;run.save()
            if run.phase=='sites':
                while True:
                    objects=list(SamplingSite.objects.filter(pk__gt=run.checkpoint).order_by('pk')[:chunk_size])
                    if not objects: break
                    with transaction.atomic():
                        save_proposals(SamplingRoadMatch,objects,network,run,site_proposal)
                        run.checkpoint=objects[-1].pk;run.save(update_fields=['checkpoint'])
                    if progress: progress(run)
            with transaction.atomic():
                run.summary['collisions']=list(SpatialMatch.objects.filter(network=network).values('status','method').annotate(count=Count('pk')).order_by('status','method'))
                run.summary['sites']=list(SamplingRoadMatch.objects.filter(network=network).values('status','method').annotate(count=Count('pk')).order_by('status','method'))
                run.status='succeeded';run.phase='complete';run.finished_at=timezone.now();run.save()
                event=record_event(action='spatial_rebuilt',object_type='SpatialRun',object_id=run.pk,details={'count':SpatialMatch.objects.filter(network=network).count(),'reason_code':RULE})
                DataRevision.objects.create(event=event,domain='spatial',snapshot_ids=[network.snapshot_id])
            return run
        except Exception as exc:
            SpatialRun.objects.filter(pk=run.pk).update(status='failed',error_code=type(exc).__name__,finished_at=timezone.now())
            raise
