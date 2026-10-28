import json
import math
from datetime import date
from django.core.exceptions import ValidationError
from .matching import rows
from .models import NetworkVersion
from .map_cache import map_cache

def current_network():return NetworkVersion.objects.filter(snapshot__status='published').order_by('-pk').first()

def parse_map(params):
    try:
        bbox=[float(x) for x in params.get('bbox','-74.27,40.48,-73.68,40.94').split(',')]
        if len(bbox)!=4 or not all(math.isfinite(x) for x in bbox) or not (-180<=bbox[0]<bbox[2]<=180 and -85<=bbox[1]<bbox[3]<=85):raise ValueError
        zoom=int(params.get('zoom',10));page=int(params.get('page',1));radius=int(params.get('radius',100))
        if not 0<=zoom<=22 or not 1<=page<=10000 or not 25<=radius<=500:raise ValueError
        start=date.fromisoformat(params['start']) if params.get('start') else None
        end=date.fromisoformat(params['end']) if params.get('end') else None
        if start and end and end<start:raise ValueError
        network=NetworkVersion.objects.get(pk=int(params['network']),snapshot__status='published') if params.get('network') else current_network()
        if not network:raise ValueError
        borough=int(params['borough']) if params.get('borough') else None
        if borough is not None and borough not in range(1,6):raise ValueError
        layers=set(filter(None,params.get('layers','roads,crashes,sites,junctions').split(',')))
        if layers-{'roads','crashes','sites','junctions'}:raise ValueError
        part=params.get('part','full')
        if part not in ('full','list'):raise ValueError
        statistics=params.get('statistics','0')
        if statistics not in ('0','1'):raise ValueError
        selected=None
        for kind in ['road','junction','site','collision']:
            if params.get(kind):
                if selected:raise ValueError
                selected=(kind,int(params[kind]))
                if not 1<=selected[1]<=9223372036854775807:raise ValueError
    except (ValueError,TypeError,KeyError,NetworkVersion.DoesNotExist):raise ValidationError('地图范围、日期或筛选参数不合法。')
    return dict(bbox=bbox,zoom=zoom,page=page,radius=radius,start=start,end=end,network=network,borough=borough,layers=layers,selected=selected,part=part,statistics=statistics=='1')

def geojson(values):
    return {'type':'FeatureCollection','features':[{'type':'Feature','geometry':json.loads(v.pop('geometry')),'properties':v} for v in values]}

def selected_feature(selected,network):
    if not selected:return None
    kind,pk=selected
    table={'road':'network_roadsegment','junction':'network_junction','site':'traffic_samplingsite','collision':'collisions_collision'}[kind]
    label={'road':'name','junction':'name','site':'street','collision':'on_street'}[kind]
    extra=' AND network_id=%s' if kind in ('road','junction') else ''
    values=rows(f'SELECT id,{label} name,ST_AsGeoJSON(geom) geometry FROM {table} WHERE id=%s AND geom IS NOT NULL'+extra,[pk,network.pk] if extra else [pk])
    if not values:raise ValidationError('所选对象在当前网络中没有可用几何。')
    values[0]['kind']=kind
    return geojson(values)['features'][0]

def selection_area(feature, radius):
    if not feature:return geojson([])
    return geojson(rows('SELECT ST_AsGeoJSON(ST_Buffer(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)::geography,%s)::geometry,6) geometry',
                       [json.dumps(feature['geometry']),radius]))

def map_data(options, use_cache=False):
    # Read publication eligibility and records from one transaction snapshot.
    from apps.analytics.query import consistent_read, data_version, digest
    from django.db import connection
    with consistent_read():
        version = digest([connection.settings_dict['NAME'], data_version(), 'map-v2']) if use_cache else None
        def cached(part, scope, calculate):
            if version is None:
                return calculate()
            key = digest([version, part, scope])
            value = map_cache.get(key)
            if value is None:
                value = calculate()
                map_cache.set(key, value)
            return value
        return _map_data(options, cached)


def _map_data(options, cached):
    from apps.analytics.query import publication_clause
    b, net, boro, zoom = options['bbox'], options['network'].pk, options['borough'], options['zoom']
    result = {kind: geojson([]) for kind in ('roads', 'junctions', 'sites', 'crashes')}
    result.update(notices=[], network=options['network'].label, part=options['part'])
    envelope = 'ST_MakeEnvelope(%s,%s,%s,%s,4326)'
    selected = selected_feature(options['selected'], options['network'])
    result['selected'] = selected
    result['nearby'] = cached('nearby',[selected,options['radius']],lambda:selection_area(selected,options['radius'])) if selected else geojson([])
    publication, args = publication_clause('c', 'crashes')
    where = f'{publication} AND c.geom && {envelope}'
    args.extend(b)
    if boro:
        where += ' AND c.borough_id=%s'; args.append(boro)
    for op, key in [('>=','start'), ('<=','end')]:
        if options[key]:
            where += f' AND c.local_date{op}%s'; args.append(options[key])
    if selected:
        # Use the geometry index before the exact metre-distance test. For the NYC
        # extent, 0.001 degrees adds >80 m around the buffered display geometry,
        # comfortably covering buffer tessellation/6-decimal display rounding.
        # The buffer itself never determines cohort membership.
        where += ' AND c.geom && ST_Expand(ST_Envelope(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)),0.001)'
        args.append(json.dumps(result['nearby']['features'][0]['geometry']))
        where += ' AND ST_DWithin(c.geom::geography,ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)::geography,%s)'
        args.extend([json.dumps(selected['geometry']), options['radius']])
    query = ' FROM collisions_collision c WHERE ' + where
    scope = [b, boro, options['start'], options['end'], net, selected, options['radius']]
    if 'crashes' in options['layers'] or options['statistics']:
        count = cached('count', scope, lambda: rows('SELECT count(*) n'+query, args)[0]['n'])
        result['crash_count'] = count
        result['crash_mode'] = 'aggregate' if count > 1200 or zoom < 14 else 'points'
        if options['part'] == 'full' and 'crashes' in options['layers']:
            if result['crash_mode'] == 'aggregate':
                cell = max(40, math.ceil((156543.033928 / 2**zoom) * 60 / 10) * 10)
                def aggregate():
                    values = rows('SELECT count(*) count,ST_AsGeoJSON(ST_SetSRID(ST_MakePoint(avg(ST_X(c.geom)),avg(ST_Y(c.geom))),4326),6) geometry'+query+
                                  ' GROUP BY ST_SnapToGrid(ST_Transform(c.geom,3857),%s) ORDER BY count(*) DESC LIMIT 2001', [*args, cell])
                    return {'data': geojson(values[:2000]), 'limited': len(values)>2000}
                clusters = cached('clusters', [scope, cell], aggregate)
                if clusters['limited']:
                    result['notices'].append('聚合符号已达 2000 个上限，请放大；列表总数仍包含全部结果。')
                result['crashes'], result['cell_m'] = clusters['data'], cell
            else:
                result['crashes'] = cached('points', scope, lambda: geojson(rows('SELECT c.id,c.on_street name,c.local_date,1 count,ST_AsGeoJSON(c.geom,6) geometry'+query+' ORDER BY c.id LIMIT 1200', args)))
        result['pages'] = max(1, (count+24)//25)
        result['page'] = min(options['page'], result['pages'])
        result['list'] = cached('list', [scope, result['page']], lambda: rows('SELECT c.id,c.on_street name,c.local_date,c.persons_injured,c.persons_killed'+query+' ORDER BY c.local_date DESC,c.id DESC LIMIT 25 OFFSET %s', [*args, (result['page']-1)*25]))
    else:
        result.update(crash_count=0, list=[], page=1, pages=1, crash_mode='hidden')
    if options['part'] == 'list':
        return result
    if 'roads' in options['layers'] and zoom >= 13:
        where = f'network_id=%s AND is_active AND is_canonical AND match_eligible AND geom && {envelope}'
        args = [net, *b]
        if boro:
            where += ' AND (left_borough_id=%s OR right_borough_id=%s)'; args.extend([boro, boro])
        roads = cached('roads', [net, b, boro], lambda: geojson(rows('SELECT id,name,source_segment_id,traffic_direction,ST_AsGeoJSON(geom,6) geometry FROM network_roadsegment WHERE '+where+' ORDER BY id LIMIT 3001', args)))
        if len(roads['features']) > 3000:
            result['notices'].append('业务道路显示前 3000 条，请放大；参考底图道路不受此限制。')
        result['roads'] = {**roads, 'features': roads['features'][:3000]}
    elif 'roads' in options['layers']:
        result['notices'].append('放大至街区级别后加载业务道路。')
    if 'junctions' in options['layers'] and zoom >= 15:
        where = f'network_id=%s AND is_active AND is_intersection IS TRUE AND geom && {envelope}'
        args = [net, *b]
        if boro:
            where += ' AND borough_id=%s'; args.append(boro)
        nodes = cached('junctions', [net, b, boro], lambda: geojson(rows('SELECT id,name,ST_AsGeoJSON(geom,6) geometry FROM network_junction WHERE '+where+' ORDER BY id LIMIT 801', args)))
        if len(nodes['features']) > 800:
            result['notices'].append('路口显示前 800 个，请放大视野。')
        result['junctions'] = {**nodes, 'features': nodes['features'][:800]}
    if 'sites' in options['layers']:
        where, args = f'is_active AND geom && {envelope}', list(b)
        if boro:
            where += ' AND borough_id=%s'; args.append(boro)
        sites = cached('sites', [b, boro], lambda: geojson(rows('SELECT id,street name,direction,ST_AsGeoJSON(geom,6) geometry FROM traffic_samplingsite WHERE '+where+' ORDER BY id LIMIT 801', args)))
        if len(sites['features']) > 800:
            result['notices'].append('采样地点显示前 800 个，请放大视野。')
        result['sites'] = {**sites, 'features': sites['features'][:800]}
    return result
