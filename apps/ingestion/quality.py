"""Idempotent quality classification and reconciliations on retained official data."""
from django.db import connection, transaction
from django.db.models import Count
from datetime import date,time
from apps.core.audit import record_event
from .models import Snapshot, StagedRecord, QualityIssue, ImportBatch

def classify(snapshots):
    """Run only after the full import. No data repair, averaging, or arbitrary conflict winner."""
    snapshots=list(snapshots)
    # An incremental flow file can conflict with a previously published file.
    # Reconcile the complete validated flow scope under the caller's import lock.
    if any(s.source.kind=='traffic' for s in snapshots):
        combined={s.pk:s for s in snapshots}
        combined.update((s.pk,s) for s in Snapshot.objects.filter(source__kind='traffic',status__in=['validated','published']).select_related('source'))
        snapshots=list(combined.values())
    ids=[s.id for s in snapshots]
    with transaction.atomic(), connection.cursor() as c:
        refined=[]
        for issue in QualityIssue.objects.filter(snapshot_id__in=ids,code='invalid_sampling_time').select_related('record'):
            r=issue.record.payload
            try:
                date(int(r['yr']),int(r['m']),int(r['d'])); clock=time(int(r['hh']),int(r['mm']))
            except (ValueError,KeyError,TypeError): continue
            if clock.minute%15:
                issue.code='non_quarter_hour_sampling'; issue.details={'fields':['mm'],'rule':'quarter_hour_grid'}
                refined.append(issue)
        QualityIssue.objects.bulk_update(refined,['code','details'],batch_size=1000)
        # Do not admit the aligned subset of a site whose source time grid is inconsistent.
        c.execute("""WITH affected AS (SELECT DISTINCT r.payload->>'requestid' AS request_id,
                r.payload->>'segmentid' AS segment_id,r.payload->>'direction' AS direction
            FROM ingestion_stagedrecord r JOIN ingestion_qualityissue q ON q.record_id=r.id
            WHERE r.snapshot_id=ANY(%s) AND q.code='non_quarter_hour_sampling')
            UPDATE traffic_trafficobservation o SET quality_status='excluded'
            FROM traffic_samplingsite s JOIN traffic_samplingrequest rq ON rq.id=s.request_id
            JOIN affected a ON a.request_id=rq.source_request_id AND a.segment_id=s.source_segment_id AND a.direction=s.direction
            WHERE o.site_id=s.id AND o.conflict_id IS NULL AND o.raw_record_id IN
                (SELECT id FROM ingestion_stagedrecord WHERE snapshot_id=ANY(%s))""",[ids,ids])
        c.execute("""INSERT INTO traffic_flowconflict(site_id,local_date,local_time,status,resolution)
            SELECT site_id,local_date,local_time,'unresolved','' FROM traffic_trafficobservation
            WHERE raw_record_id IN (SELECT id FROM ingestion_stagedrecord WHERE snapshot_id=ANY(%s))
            GROUP BY site_id,local_date,local_time HAVING count(*)>1
            ON CONFLICT (site_id,local_date,local_time) DO NOTHING""",[ids])
        c.execute("""UPDATE traffic_trafficobservation o SET conflict_id=f.id,quality_status='conflict'
            FROM traffic_flowconflict f WHERE o.site_id=f.site_id AND o.local_date=f.local_date
            AND o.local_time=f.local_time AND (o.conflict_id IS DISTINCT FROM f.id OR o.quality_status<>'conflict')
            AND o.raw_record_id IN (SELECT id FROM ingestion_stagedrecord WHERE snapshot_id=ANY(%s))""",[ids])
        # Different geometry at the same source candidate key is not resolved by keeping the first point.
        c.execute("""WITH bad AS (SELECT o.site_id FROM traffic_trafficobservation o
            JOIN ingestion_stagedrecord r ON r.id=o.raw_record_id WHERE r.snapshot_id=ANY(%s)
            GROUP BY o.site_id HAVING count(DISTINCT coalesce(r.payload->>'wktgeom',''))>1)
            UPDATE traffic_trafficobservation SET quality_status='excluded' WHERE site_id IN (SELECT site_id FROM bad)
            AND conflict_id IS NULL""",[ids])
        c.execute("""INSERT INTO ingestion_qualityissue(snapshot_id,record_id,code,severity,status,details,resolution,resolved_by_id,resolved_at)
            SELECT r.snapshot_id,r.id,'flow_not_statistically_usable','warning','open',
            jsonb_build_object('quality_status',o.quality_status),'',NULL,NULL
            FROM traffic_trafficobservation o JOIN ingestion_stagedrecord r ON r.id=o.raw_record_id
            WHERE r.snapshot_id=ANY(%s) AND o.quality_status<>'valid'
            AND NOT EXISTS(SELECT 1 FROM ingestion_qualityissue q WHERE q.record_id=r.id AND q.code='flow_not_statistically_usable')""",[ids])
        c.execute("""UPDATE ingestion_stagedrecord r SET disposition=CASE WHEN o.quality_status='conflict' THEN 'conflict' ELSE 'pending' END
            FROM traffic_trafficobservation o WHERE r.id=o.raw_record_id AND r.snapshot_id=ANY(%s) AND o.quality_status<>'valid'""",[ids])
        # Boundary inference applies only when a unique polygon contains the source coordinate.
        c.execute("""WITH matched AS (SELECT a.id,min(b.code) AS code FROM collisions_collision a
            JOIN core_borough b ON ST_Covers(b.geom,a.geom) WHERE a.borough_id IS NULL AND a.geom IS NOT NULL
            AND a.raw_record_id IN (SELECT id FROM ingestion_stagedrecord WHERE snapshot_id=ANY(%s))
            GROUP BY a.id HAVING count(*)=1)
            UPDATE collisions_collision a SET borough_id=m.code,borough_method='spatial' FROM matched m WHERE a.id=m.id""",[ids])
        c.execute("""INSERT INTO ingestion_qualityissue(snapshot_id,record_id,code,severity,status,details,resolution,resolved_by_id,resolved_at)
            SELECT r.snapshot_id,r.id,'reported_borough_geometry_mismatch','warning','open','{}'::jsonb,'',NULL,NULL
            FROM collisions_collision a JOIN core_borough b ON b.code=a.borough_id
            JOIN ingestion_stagedrecord r ON r.id=a.raw_record_id WHERE r.snapshot_id=ANY(%s)
            AND a.borough_method='reported' AND a.geom IS NOT NULL AND NOT ST_Covers(b.geom,a.geom)
            AND NOT EXISTS(SELECT 1 FROM ingestion_qualityissue q WHERE q.record_id=r.id AND q.code='reported_borough_geometry_mismatch')""",[ids])
        for snapshot in snapshots:
            counts=dict(StagedRecord.objects.filter(snapshot=snapshot).values_list('disposition').annotate(n=Count('id')))
            batch=ImportBatch.objects.filter(snapshot=snapshot).order_by('-attempt').first()
            if batch is None or batch.checkpoint!=snapshot.source_count or sum(counts.values())!=snapshot.source_count:
                raise ValueError('Incomplete snapshot cannot be classified')
            batch.accepted_rows=counts.get('accepted',0); batch.rejected_rows=counts.get('rejected',0)
            batch.pending_rows=counts.get('pending',0)+counts.get('conflict',0); batch.skipped_rows=counts.get('skipped',0)
            batch.status='partial' if batch.pending_rows or batch.rejected_rows else 'succeeded'; batch.save()

def rows(query,params=None):
    with connection.cursor() as c:
        c.execute(query,params or [])
        return [dict(zip([d[0] for d in c.description],r)) for r in c.fetchall()]

def quality_report(snapshots):
    ids=[s.id for s in snapshots]
    report={'snapshots':[]}
    for s in snapshots:
        counts=dict(StagedRecord.objects.filter(snapshot=s).values_list('disposition').annotate(n=Count('id')))
        batch=ImportBatch.objects.filter(snapshot=s).order_by('-attempt').first()
        if s.source.kind in ('crashes','vehicles','persons','traffic'):
            from .pipeline import ENTITY
            typed=ENTITY[s.source.kind].objects.filter(raw_record__snapshot=s).count()
        else:
            from apps.network.models import Junction,RoadSegment
            from apps.core.models import Borough
            typed=(Junction.objects.filter(network__node_snapshot=s).count() if s.source.kind=='nodes' else
                RoadSegment.objects.filter(network__snapshot=s).count() if s.source.kind=='roads' else Borough.objects.filter(boundary_snapshot=s).count())
        report['snapshots'].append({'snapshot':s.pk,'kind':s.source.kind,'version':s.version,
            'source_rows':s.source_count,'staged_rows':sum(counts.values()),'dispositions':counts,
            'business_rows':typed,
            'checkpoint':batch.checkpoint if batch else None,'status':batch.status if batch else None,
            'seconds':round((batch.finished_at-batch.started_at).total_seconds(),3) if batch and batch.finished_at else None})
    report['issues']=list(QualityIssue.objects.filter(snapshot_id__in=ids).values('code','severity').annotate(count=Count('id')).order_by('code'))
    report['business_counts']=rows("""SELECT 'crashes' AS kind,count(*) AS rows FROM collisions_collision WHERE origin='official'
        UNION ALL SELECT 'vehicles',count(*) FROM collisions_vehicle WHERE origin='official'
        UNION ALL SELECT 'persons',count(*) FROM collisions_person WHERE origin='official'
        UNION ALL SELECT 'traffic',count(*) FROM traffic_trafficobservation WHERE origin='official'
        UNION ALL SELECT 'roads',count(*) FROM network_roadsegment
        UNION ALL SELECT 'nodes',count(*) FROM network_junction
        UNION ALL SELECT 'boundaries',count(*) FROM core_borough WHERE geom IS NOT NULL""")
    report['crashes_by_year_borough']=rows("""SELECT extract(year from local_date)::int AS year,
        coalesce(b.name,'UNKNOWN') AS borough,count(*) AS rows,count(*) FILTER(WHERE a.geom IS NULL) AS no_geometry,
        count(*) FILTER(WHERE borough_method='spatial') AS spatially_inferred
        FROM collisions_collision a LEFT JOIN core_borough b ON a.borough_id=b.code
        WHERE origin='official' GROUP BY 1,2 ORDER BY 1,2""")
    report['relationships']=rows("""SELECT
        (SELECT count(*) FROM collisions_vehicle WHERE origin='official' AND collision_id IS NULL) AS orphan_vehicles,
        (SELECT count(*) FROM collisions_person WHERE origin='official' AND collision_id IS NULL) AS orphan_person_crashes,
        (SELECT count(*) FROM collisions_person WHERE origin='official' AND source_vehicle_id<>'' AND vehicle_id IS NULL) AS unmatched_person_vehicle,
        (SELECT count(*) FROM collisions_person p JOIN collisions_vehicle v ON p.vehicle_id=v.id WHERE p.origin='official' AND p.collision_id<>v.collision_id) AS invalid_person_vehicle,
        (SELECT count(*) FROM collisions_collision c WHERE c.origin='official' AND NOT EXISTS(SELECT 1 FROM collisions_person p WHERE p.collision_id=c.id)) AS crashes_without_persons,
        (SELECT count(*) FROM collisions_collision c WHERE c.origin='official' AND NOT EXISTS(SELECT 1 FROM collisions_vehicle v WHERE v.collision_id=c.id)) AS crashes_without_vehicles""")[0]
    report['missing_fields']=rows("""SELECT
        (SELECT count(*) FROM collisions_collision WHERE origin='official' AND geom IS NULL) AS crashes_without_usable_geometry,
        (SELECT count(*) FROM collisions_collision WHERE origin='official' AND borough_id IS NULL) AS crashes_without_borough,
        (SELECT count(*) FROM collisions_collision WHERE origin='official' AND borough_method='spatial') AS inferred_boroughs,
        (SELECT count(*) FROM collisions_collision WHERE origin='official' AND (persons_injured IS NULL OR persons_killed IS NULL)) AS unknown_total_casualties,
        (SELECT count(*) FROM collisions_person WHERE origin='official' AND age IS NULL) AS persons_without_usable_age,
        (SELECT count(*) FROM collisions_vehicle WHERE origin='official' AND manufacture_year IS NULL) AS vehicles_without_usable_year,
        (SELECT count(*) FROM collisions_person WHERE origin='official' AND source_vehicle_id='') AS persons_without_reported_vehicle""")[0]
    report['source_coordinate_missingness']=rows("""SELECT
        count(*) FILTER(WHERE coalesce(r.payload->>'latitude','')='' OR coalesce(r.payload->>'longitude','')='') AS missing_source_coordinate,
        count(*) FILTER(WHERE coalesce(r.payload->>'latitude','')<>'' AND coalesce(r.payload->>'longitude','')<>'' AND c.geom IS NULL) AS unusable_reported_coordinate
        FROM collisions_collision c JOIN ingestion_stagedrecord r ON r.id=c.raw_record_id WHERE c.origin='official'""")[0]
    report['traffic_quality']=rows("""SELECT extract(year from local_date)::int AS year,quality_status,time_status,
        count(*) AS rows,count(*) FILTER(WHERE volume=0) AS measured_zeroes FROM traffic_trafficobservation WHERE origin='official'
        GROUP BY 1,2,3 ORDER BY 1,2,3""")
    report['traffic_coverage']=rows("""SELECT extract(year from o.local_date)::int AS year,coalesce(b.name,'UNKNOWN') AS borough,
        count(*) AS observations,count(DISTINCT o.site_id) AS sites,count(DISTINCT o.local_date) AS observed_days,
        count(*) FILTER(WHERE o.quality_status='valid') AS eligible_observations,min(o.local_date) AS first_date,max(o.local_date) AS last_date
        FROM traffic_trafficobservation o JOIN traffic_samplingsite s ON s.id=o.site_id LEFT JOIN core_borough b ON b.code=s.borough_id WHERE o.origin='official'
        GROUP BY 1,2 ORDER BY 1,2""")
    report['traffic_groups']=rows("""SELECT
        (SELECT count(*) FROM traffic_flowconflict) AS conflicting_groups,
        (SELECT count(*) FROM traffic_samplingsite) AS sites,
        (SELECT count(*) FROM traffic_samplingrequest) AS requests,
        (SELECT count(*) FROM (SELECT site_id,local_date,local_time,volume FROM traffic_trafficobservation
          GROUP BY 1,2,3,4 HAVING count(*)>1) d) AS repeated_equal_volumes,
        (SELECT count(*) FROM (SELECT site_id,local_date,local_time FROM traffic_trafficobservation
          WHERE quality_status='valid' GROUP BY 1,2,3 HAVING count(*)>1) d) AS eligible_duplicate_slots""")[0]
    report['non_quarter_hour_sampling']=rows("""SELECT r.payload->>'requestid' AS request_id,
        r.payload->>'segmentid' AS segment_id,r.payload->>'direction' AS direction,count(*) AS rows
        FROM ingestion_stagedrecord r JOIN ingestion_qualityissue q ON q.record_id=r.id
        WHERE r.snapshot_id=ANY(%s) AND q.code='non_quarter_hour_sampling'
        GROUP BY 1,2,3 ORDER BY 1,2,3""",[ids])
    report['raw_traffic_sites']=rows("""SELECT count(DISTINCT (r.payload->>'requestid',r.payload->>'segmentid',r.payload->>'direction')) AS sites
        FROM ingestion_stagedrecord r JOIN ingestion_snapshot s ON s.id=r.snapshot_id
        JOIN ingestion_datasource d ON d.id=s.source_id WHERE r.snapshot_id=ANY(%s) AND d.kind='traffic'""",[ids])[0]['sites']
    report['geometry']=rows("""SELECT 'roads' AS kind,count(*) AS rows,
        count(*) FILTER(WHERE NOT ST_IsValid(geom) OR ST_IsEmpty(geom) OR ST_SRID(geom)<>4326) AS invalid,
        count(*) FILTER(WHERE from_node_id IS NULL OR to_node_id IS NULL) AS missing_endpoints,
        ST_Extent(geom)::text AS extent FROM network_roadsegment
        UNION ALL SELECT 'nodes',count(*),count(*) FILTER(WHERE NOT ST_IsValid(geom) OR ST_IsEmpty(geom) OR ST_SRID(geom)<>4326),0,ST_Extent(geom)::text FROM network_junction
        UNION ALL SELECT 'sampling_sites',count(*),count(*) FILTER(WHERE geom IS NULL OR NOT ST_IsValid(geom) OR ST_SRID(geom)<>4326),0,ST_Extent(geom)::text FROM traffic_samplingsite""")
    report['traffic_crs_check']=rows("""SELECT count(*) AS sites,
        count(*) FILTER(WHERE EXISTS(SELECT 1 FROM core_borough b WHERE ST_Covers(b.geom,s.geom))) AS inside_borough,
        min(distance_m) AS min_distance_m,percentile_cont(0.5) WITHIN GROUP(ORDER BY distance_m) AS median_distance_m,
        percentile_cont(0.95) WITHIN GROUP(ORDER BY distance_m) AS p95_distance_m,max(distance_m) AS max_distance_m
        FROM traffic_samplingsite s CROSS JOIN LATERAL
        (SELECT ST_Distance(s.geom::geography,r.geom::geography) AS distance_m FROM network_roadsegment r ORDER BY r.geom <-> s.geom LIMIT 1) n
        WHERE s.geom IS NOT NULL""")[0]
    report['all_raw_traffic_crs_check']=rows("""WITH locations AS MATERIALIZED (
        SELECT DISTINCT r.payload->>'requestid' AS request_id,r.payload->>'segmentid' AS segment_id,
            r.payload->>'direction' AS direction,r.payload->>'wktgeom' AS wkt
        FROM ingestion_stagedrecord r JOIN ingestion_snapshot s ON s.id=r.snapshot_id
        JOIN ingestion_datasource d ON d.id=s.source_id WHERE r.snapshot_id=ANY(%s) AND d.kind='traffic'),
        points AS MATERIALIZED (SELECT ST_Transform(ST_GeomFromText(wkt,2263),4326) AS geom FROM locations)
        SELECT count(*) AS sites,count(*) FILTER(WHERE EXISTS(SELECT 1 FROM core_borough b WHERE ST_Covers(b.geom,p.geom))) AS inside_borough,
        percentile_cont(0.5) WITHIN GROUP(ORDER BY distance_m) AS median_distance_m,
        percentile_cont(0.95) WITHIN GROUP(ORDER BY distance_m) AS p95_distance_m,max(distance_m) AS max_distance_m
        FROM points p CROSS JOIN LATERAL
        (SELECT ST_Distance(p.geom::geography,r.geom::geography) AS distance_m FROM network_roadsegment r ORDER BY r.geom <-> p.geom LIMIT 1) n""",[ids])[0]
    report['time_status']=rows("""SELECT 'crashes' AS kind,time_status,count(*) AS rows FROM collisions_collision WHERE origin='official' GROUP BY 2
        UNION ALL SELECT 'traffic',time_status,count(*) FROM traffic_trafficobservation WHERE origin='official' GROUP BY 2""")
    report['checks']={
        'source_raw_reconciled':all(s['source_rows']==s['staged_rows']==s['checkpoint'] for s in report['snapshots']),
        'no_invalid_person_vehicle':report['relationships']['invalid_person_vehicle']==0,
        'valid_spatial_geometry':all(g['invalid']==0 for g in report['geometry']),
        'no_equal_volume_duplicates':report['traffic_groups']['eligible_duplicate_slots']==0,
        'business_rows_accounted':all(s['business_rows']==s['dispositions'].get('accepted',0)+(
            s['dispositions'].get('pending',0)+s['dispositions'].get('conflict',0) if s['kind']=='traffic' else 0)
            for s in report['snapshots']),
    }
    return report

def publish(snapshots,report, *, actor=None):
    if not all(report['checks'].values()): raise ValueError('Acceptance checks failed; publication refused')
    with transaction.atomic():
        for snapshot in snapshots:
            if snapshot.status not in ('validated','published'): raise ValueError('Snapshot not validated')
            if snapshot.status=='published': continue
            snapshot.status='published'; snapshot.manifest['quality_checked']=True
            snapshot.save(update_fields=['status','manifest'])
            event=record_event(actor=actor,action='import_finished',object_type='Snapshot',object_id=snapshot.pk,
                details={'count':snapshot.source_count,'reason_code':'quality_reconciled'})
            from apps.core.models import DataRevision
            DataRevision.objects.create(event=event,domain='import',snapshot_ids=[snapshot.pk])
    # A tiny snapshot catalog can remain below autovacuum's analyze threshold even after
    # all statuses change. Stale selectivity then causes millions of nested index probes.
    with connection.cursor() as cursor:
        cursor.execute('ANALYZE ingestion_snapshot, ingestion_stagedrecord, collisions_collision, '
            'collisions_vehicle, collisions_person, traffic_trafficobservation')
