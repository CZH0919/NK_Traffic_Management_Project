import json
import math
from datetime import date
from django.core.exceptions import ValidationError
from .matching import rows
from .models import NetworkVersion

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
        selected=None
        for kind in ['road','junction','site','collision']:
            if params.get(kind):
                if selected:raise ValueError
                selected=(kind,int(params[kind]))
    except (ValueError,TypeError,KeyError,NetworkVersion.DoesNotExist):raise ValidationError('地图范围、日期或筛选参数不合法。')
    return dict(bbox=bbox,zoom=zoom,page=page,radius=radius,start=start,end=end,network=network,borough=borough,layers=layers,selected=selected)

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

def map_data(options):
    b=options['bbox'];net=options['network'].pk;boro=options['borough'];zoom=options['zoom']
    result={'roads':geojson([]),'junctions':geojson([]),'sites':geojson([]),'crashes':geojson([]),'notices':[],'network':options['network'].label}
    envelope='ST_MakeEnvelope(%s,%s,%s,%s,4326)'
    selected=selected_feature(options['selected'],options['network']);result['selected']=selected
    where=f"c.is_active AND c.origin='official' AND c.geom && {envelope} AND snap.status='published'"
    args=list(b)
    if boro:where+=' AND c.borough_id=%s';args.append(boro)
    for op,key in [('>=','start'),('<=','end')]:
        if options[key]:where+=f' AND c.local_date{op}%s';args.append(options[key])
    if selected:
        geometry=json.dumps(selected['geometry'])
        where+=' AND ST_DWithin(c.geom::geography,ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)::geography,%s)';args.extend([geometry,options['radius']])
    joins=' FROM collisions_collision c JOIN ingestion_stagedrecord raw ON raw.id=c.raw_record_id JOIN ingestion_snapshot snap ON snap.id=raw.snapshot_id WHERE '+where
    if 'crashes' in options['layers']:
        count=rows('SELECT count(*) n'+joins,args)[0]['n'];result['crash_count']=count
        if count>1200 or zoom<14:
            # A ~60px Web Mercator grid prevents count symbols from obscuring the land at city scale.
            cell=max(40,math.ceil((156543.033928/2**zoom)*60/10)*10)
            values=rows("SELECT count(*) count,ST_AsGeoJSON(ST_Transform(ST_Centroid(ST_Collect(c.geom)),4326)) geometry"+joins+
                ' GROUP BY ST_SnapToGrid(ST_Transform(c.geom,3857),%s) ORDER BY count(*) DESC LIMIT 2001',[*args,cell])
            if len(values)>2000:result['notices'].append('密集点超过显示上限，请缩小视野；列表总数仍包含全部结果。')
            result['crashes']=geojson(values[:2000]);result['crash_mode']='aggregate';result['cell_m']=cell
        else:
            values=rows("SELECT c.id,c.on_street name,c.local_date,1 count,ST_AsGeoJSON(c.geom) geometry"+joins+' ORDER BY c.id LIMIT 1200',args)
            result['crashes']=geojson(values);result['crash_mode']='points'
        result['list']=rows('SELECT c.id,c.on_street name,c.local_date,c.persons_injured,c.persons_killed'+joins+' ORDER BY c.local_date DESC,c.id DESC LIMIT 25 OFFSET %s',[*args,(options['page']-1)*25])
        result['page']=options['page'];result['pages']=max(1,(count+24)//25)
    else:result.update(crash_count=0,list=[],page=1,pages=1,crash_mode='hidden')
    if 'roads' in options['layers'] and zoom>=13:
        where=f'network_id=%s AND is_active AND is_canonical AND match_eligible AND geom && {envelope}';args=[net,*b]
        if boro:where+=' AND (left_borough_id=%s OR right_borough_id=%s)';args.extend([boro,boro])
        values=rows('SELECT id,name,source_segment_id,traffic_direction,ST_AsGeoJSON(geom) geometry FROM network_roadsegment WHERE '+where+' ORDER BY id LIMIT 3001',args)
        if len(values)>3000:result['notices'].append('道路显示前 3000 条，请放大以查看完整局部路网。')
        result['roads']=geojson(values[:3000])
    elif 'roads' in options['layers']:result['notices'].append('放大至街区级别后加载道路。')
    if 'junctions' in options['layers'] and zoom>=15:
        where=f'network_id=%s AND is_active AND is_intersection IS TRUE AND geom && {envelope}';args=[net,*b]
        if boro:where+=' AND borough_id=%s';args.append(boro)
        values=rows('SELECT id,name,ST_AsGeoJSON(geom) geometry FROM network_junction WHERE '+where+' ORDER BY id LIMIT 801',args)
        if len(values)>800:result['notices'].append('路口显示前 800 个，请放大视野。')
        result['junctions']=geojson(values[:800])
    if 'sites' in options['layers']:
        where=f'is_active AND geom && {envelope}';args=list(b)
        if boro:where+=' AND borough_id=%s';args.append(boro)
        values=rows('SELECT id,street name,direction,ST_AsGeoJSON(geom) geometry FROM traffic_samplingsite WHERE '+where+' ORDER BY id LIMIT 801',args)
        if len(values)>800:result['notices'].append('采样地点显示前 800 个，请缩小视野。')
        result['sites']=geojson(values[:800])
    return result
