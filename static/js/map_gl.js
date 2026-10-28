import * as ml from '../vendor/maplibre/maplibre-gl.mjs';

const el = id => document.getElementById(id);
const form = el('map-filters'), config = el('map-script').dataset;
const state = new URLSearchParams(location.search), boxes = [...document.querySelectorAll('.map-layer-controls input')];
const fallbackParams=new URLSearchParams(location.search);fallbackParams.set('basic','1');el('map-basic-link').href='?'+fallbackParams;
const CITY = [[-74.265, 40.49], [-73.69, 40.915]], REGION = [[-74.65, 40.35], [-73.30, 41.06]];
const EMPTY = {type:'FeatureCollection', features:[]};
const kinds = ['road','junction','site','collision'];
let map, land, latest, popup, timer, generation=0, inFlight=false, queued=null, lastRequest='', ready=false;
let page=Number(state.get('page'))||1, pages=1, selectionTicket=0, controller, hoverTimer;
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const dark = () => document.documentElement.dataset.theme === 'dark';
const visible = name => boxes.find(box => box.value===name)?.checked;
const palette = () => dark() ? {road:'#60949d', junction:'#a9b9d8', site:'#59bfb4', crash:'#c99561', text:'#192c39', selected:'#81e7cf', halo:'#5ec5b5', border:'#1a303f'} : {road:'#397880', junction:'#5b6f9b', site:'#0d8985', crash:'#b5753b', text:'#fff', selected:'#085c83', halo:'#4c9dba', border:'#fff'};
for(const name of ['borough','network','start','end','radius']) if(state.has(name)) form.elements[name].value=state.get(name);
if(state.has('layers')) boxes.forEach(box => box.checked=state.get('layers').split(',').includes(box.value));
el('map-prev').disabled=el('map-next').disabled=true;

function node(tag, text, parent) { const n=document.createElement(tag); n.textContent=text; parent?.append(n); return n; }
function path(kind,id) { return kind==='collision'?`/records/crashes/${id}/`:kind==='site'?`/traffic/${id}/`:`/network/${kind}/${id}/`; }
function featureBounds(feature) {
  const bounds=new ml.LngLatBounds();
  function visit(coords) { if(typeof coords[0]==='number') bounds.extend(coords); else coords.forEach(visit); }
  visit(feature.geometry.coordinates); return bounds;
}
function appliedFields() {
  for(const key of ['borough','network','start','end','radius']) {
    const value=form.elements[key].value; value?state.set(key,value):state.delete(key);
  }
}
function publicParams() {
  const p=new URLSearchParams(state); p.delete('basic'); p.delete('part'); p.delete('statistics');
  p.set('layers',boxes.filter(b=>b.checked).map(b=>b.value).join(','));
  const b=map.getBounds(); p.set('bbox',[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()].map(x=>x.toFixed(6)).join(','));
  p.set('zoom',Math.floor(map.getZoom())); p.set('page',page); return p;
}
function syncURL() {
  if(!map) return;
  const p=publicParams(); history.replaceState(null,'',`${location.pathname}?${p}`);
  p.set('basic','1'); el('map-basic-link').href=`${location.pathname}?${p}`;
}
function fallback(message) {
  el('map-loading').hidden=true; el('map-status').textContent=message+' 请使用“基础地图”继续查询。';
  el('map-basic-link').classList.add('btn','btn-outline-primary','btn-sm');
}

function style() {
  const night=dark(), flavor=basemaps.namedFlavor(night?'dark':'light');
  Object.assign(flavor, night ? {
    background:'#162b39', earth:'#243744', water:'#152b3b', buildings:'#334955',
    park_a:'#284a46', park_b:'#2e5149', industrial:'#2b3c48', school:'#35434b', hospital:'#3b4149',
    roads_label_minor:'#bac7cf', roads_label_minor_halo:'#283c48', roads_label_major:'#d5dfe5', roads_label_major_halo:'#243744',
    ocean_label:'#7ba1b7', subplace_label:'#a5b9c6', subplace_label_halo:'#243744', city_label:'#c4d1da', city_label_halo:'#243744'
  } : {
    background:'#d7e5ea', earth:'#eff1ed', water:'#cddfe6', buildings:'#d9dfdc',
    park_a:'#d4e4d9', park_b:'#b8d6c5', industrial:'#e2e7e6', school:'#e7e4da', hospital:'#e9e1de',
    roads_label_minor:'#697b82', roads_label_minor_halo:'#fafbf8', roads_label_major:'#4c6571', roads_label_major_halo:'#fafbf8',
    ocean_label:'#769ba9', subplace_label:'#7a8b91', subplace_label_halo:'#eff1ed', city_label:'#4f6975', city_label_halo:'#eff1ed'
  });
  flavor.landcover={...flavor.landcover, urban_area:night?'#243744':'#eff1ed'};
  const layers=basemaps.layers('reference',flavor,{lang:'en'}).filter(layer=>!['places_country','places_region'].includes(layer.id));
  const landmarks=layers.find(layer=>layer.id==='pois');
  landmarks.filter=['in',['get','kind'],['literal',['park','garden','hospital','station','aerodrome']]];
  landmarks.minzoom=14;
  delete landmarks.layout['icon-image'];
  landmarks.layout['text-size']=11;
  landmarks.layout['text-offset']=[0,0];
  landmarks.paint['text-color']=night?'#b6cbd3':'#577779';
  landmarks.paint['text-halo-color']=night?'#243744':'#f4f6f1';
  landmarks.paint['text-halo-width']=1.5;
  return {version:8, glyphs:new URL(config.assets+'fonts/{fontstack}/{range}.pbf',location.origin).href.replaceAll('%7B','{').replaceAll('%7D','}'),
    sprite:new URL(config.assets+'sprites/v4/'+(night?'dark':'light'),location.origin).href,
    sources:{reference:{type:'vector',url:'pmtiles://'+new URL(config.archive,location.origin).href,
      attribution:'© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> · <a href="https://protomaps.com" target="_blank" rel="noopener">Protomaps</a> · NYC Open Data'}}, layers};
}
function addBusinessLayers() {
  const c=palette();
  const labels=map.getStyle().layers.find(layer=>layer.type==='symbol')?.id;
  for(const name of ['roads','junctions','sites','crashes','selected','nearby']) map.addSource(name,{type:'geojson',data:EMPTY,tolerance:0.5,maxzoom:18});
  map.addLayer({id:'nearby-fill',type:'fill',source:'nearby',paint:{'fill-color':c.halo,'fill-opacity':0.12}},labels);
  map.addLayer({id:'nearby-line',type:'line',source:'nearby',paint:{'line-color':c.selected,'line-width':1,'line-dasharray':[3,3],'line-opacity':0.65}},labels);
  map.addLayer({id:'business-roads',type:'line',source:'roads',minzoom:13,paint:{'line-color':c.road,'line-width':['interpolate',['linear'],['zoom'],13,1.2,17,2.4],'line-opacity':0.65}},labels);
  map.addLayer({id:'business-junctions',type:'circle',source:'junctions',minzoom:15,paint:{'circle-radius':3,'circle-color':c.border,'circle-stroke-color':c.junction,'circle-stroke-width':1.3}});
  map.addLayer({id:'business-sites',type:'circle',source:'sites',minzoom:12,paint:{'circle-radius':['interpolate',['linear'],['zoom'],12,2.5,16,4.5],'circle-color':c.site,'circle-opacity':0.88,'circle-stroke-color':c.border,'circle-stroke-width':1}});
  map.addLayer({id:'business-crash-points',type:'circle',source:'crashes',filter:['has','id'],paint:{'circle-radius':4,'circle-color':c.crash,'circle-stroke-color':c.border,'circle-stroke-width':1,'circle-opacity':0.85}});
  map.addLayer({id:'business-clusters',type:'circle',source:'crashes',filter:['!', ['has','id']],paint:{'circle-radius':['interpolate',['linear'],['get','count'],1,10,100,13,1000,17,10000,21],'circle-color':c.crash,'circle-opacity':0.88,'circle-stroke-width':1.3,'circle-stroke-color':c.border}});
  map.addLayer({id:'business-counts',type:'symbol',source:'crashes',filter:['!', ['has','id']],layout:{'text-field':['get','short_count'],'text-font':['Noto Sans Medium'],'text-size':11,'text-allow-overlap':false,'text-ignore-placement':false},paint:{'text-color':c.text}});
  map.addLayer({id:'selected-line',type:'line',source:'selected',filter:['!=',['geometry-type'],'Point'],paint:{'line-color':c.selected,'line-width':5,'line-opacity':0.95}},labels);
  map.addLayer({id:'selected-point',type:'circle',source:'selected',filter:['==',['geometry-type'],'Point'],paint:{'circle-color':c.halo,'circle-radius':10,'circle-opacity':0.3,'circle-stroke-color':c.selected,'circle-stroke-width':3}});
  addBoroughs();
  if(latest) updateSources(latest);
  setVisibility();
}
function addBoroughs() {
  if(!land||!map.getLayer('business-roads')||map.getSource('boroughs'))return;
  map.addSource('boroughs',{type:'geojson',data:land});
  map.addLayer({id:'borough-boundaries',type:'line',source:'boroughs',maxzoom:13,paint:{'line-color':dark()?'#91a9b5':'#66858b','line-width':0.8,'line-opacity':0.55,'line-dasharray':[3,2]}},'business-roads');
  const names=['曼哈顿','布朗克斯','布鲁克林','皇后区','斯塔滕岛'];
  const labels=land.features.map(f=>({type:'Feature',properties:{name:names[f.properties.code-1]},geometry:{type:'Point',coordinates:featureBounds(f).getCenter().toArray()}}));
  map.addSource('borough-labels',{type:'geojson',data:{type:'FeatureCollection',features:labels}});
  map.addLayer({id:'borough-names',type:'symbol',source:'borough-labels',maxzoom:13,layout:{'text-field':['get','name'],'text-font':['Noto Sans Medium'],'text-size':13,'text-offset':[0,1.8],'text-allow-overlap':false},paint:{'text-color':dark()?'#d0e0e7':'#385b6b','text-halo-color':dark()?'#243744':'#f2f5f1','text-halo-width':2}});
}
function updateSources(data) {
  if(!map.getSource('roads')) return;
  for(const kind of ['roads','junctions','sites','crashes']) {
    if(kind==='crashes') for(const f of data.crashes.features) {
      const n=f.properties.count; f.properties.short_count=n>=10000?(n/1000).toFixed(0)+'k':n>=1000?(n/1000).toFixed(1).replace('.0','')+'k':String(n);
    }
    map.getSource(kind).setData(data[kind]);
  }
  map.getSource('selected').setData(data.selected?{type:'FeatureCollection',features:[data.selected]}:EMPTY);
  map.getSource('nearby').setData(data.nearby||EMPTY);
}
function setVisibility() {
  const ids={roads:['business-roads'],junctions:['business-junctions'],sites:['business-sites'],crashes:['business-crash-points','business-clusters','business-counts']};
  for(const [key,layers] of Object.entries(ids)) for(const id of layers) if(map.getLayer(id)) map.setLayoutProperty(id,'visibility',visible(key)?'visible':'none');
}
function renderList(data) {
  page=data.page; pages=data.pages;
  el('map-summary').textContent=`业务路网 ${data.network} · ${data.crash_count.toLocaleString()} 起有坐标事故`;
  el('result-count').textContent=`${data.crash_count.toLocaleString()} 条`;
  const body=el('map-results'); body.replaceChildren();
  for(const row of data.list) {
    const tr=document.createElement('tr'), cell=node('td','',tr), link=node('a','#'+row.id,cell); link.href=path('collision',row.id);
    node('td',row.local_date,tr); node('td',row.name||'未提供街道',tr); node('td',`${row.persons_injured??'未知'} / ${row.persons_killed??'未知'}`,tr);
    const action=node('td','',tr), button=node('button','定位',action); button.type='button'; button.className='btn btn-outline-primary btn-sm'; button.addEventListener('click',()=>select('collision',row.id)); body.append(tr);
  }
  if(!data.list.length) {const tr=node('tr','',body), td=node('td','当前范围没有符合条件的事故。',tr); td.colSpan=5;}
  el('map-page').textContent=`第 ${page} / ${pages} 页`; el('map-prev').disabled=page<=1; el('map-next').disabled=page>=pages;
  el('map-selection').hidden=!data.selected;
  if(data.selected) {
    const p=data.selected.properties; el('selection-link').textContent=`${p.name||'对象'} #${p.id}`; el('selection-link').href=path(p.kind,p.id);
    el('selection-description').textContent=`列表位于所选对象 ${state.get('radius')||100} 米内，并与当前视野相交；附近不等同于已关联。`;
  } else el('selection-description').textContent='事故列表与当前视野、日期和行政区同步；缺少坐标的事故可在事故档案中查询。';
}
function feedback() {
  if(!latest) return;
  const zoom=Math.floor(map.getZoom());
  el('map-mode').textContent=zoom<13?'城市全览':zoom<15?'街区视图':'道路详情';
  const mode=!visible('crashes')?'事故图层已隐藏；列表及总数保持不变。':latest.crash_mode==='aggregate'?'事故按网格聚合；悬停查看准确数量，点击放大。k 表示千。':'事故逐点显示；点击可查看档案与附近事故。';
  const notes=[mode,...latest.notices];
  if(visible('sites')&&zoom<12) notes.push('采样地点在进一步放大后显示。');
  if(visible('junctions')&&zoom<15) notes.push('路口在道路详情级别显示。');
  el('map-status').textContent=notes.join(' ');
}
function schedule(part='full', force=false, resetPage=true) {
  if(!ready) return;
  clearTimeout(timer); generation++; if(part==='full'&&resetPage) page=1;
  queued={part,force,generation}; syncURL();
  el('map-loading').textContent='正在更新当前范围…'; el('map-loading').hidden=false;
  el('map-prev').disabled=el('map-next').disabled=true;
  timer=setTimeout(run,180);
}
async function run() {
  // One request at a time: a new view replaces the queued view, not another SQL job.
  if(inFlight||!queued) return;
  const task=queued; queued=null; const params=publicParams(); params.set('statistics','1'); params.set('part',task.part);
  // Keep the crash source ready for instant visibility toggles, without changing the cohort.
  const requested=new Set(params.get('layers').split(',').filter(Boolean)); requested.add('crashes'); params.set('layers',[...requested].sort().join(','));
  const signature=params.toString();
  if(!task.force&&signature===lastRequest) {el('map-loading').hidden=true;el('map-prev').disabled=page<=1;el('map-next').disabled=page>=pages;feedback();return;}
  inFlight=true; controller=new AbortController();
  try {
    const response=await fetch('/map/data/?'+params,{signal:controller.signal,headers:{Accept:'application/json'}});
    if(response.redirected) throw new Error('登录已失效，请刷新页面重新登录。');
    const data=await response.json(); if(!response.ok) throw new Error(data.error||'查询失败，请重试。');
    if(task.generation!==generation) return;
    lastRequest=signature;
    if(task.part==='full') {latest=data;updateSources(data);} else if(latest) Object.assign(latest,{list:data.list,page:data.page,pages:data.pages,crash_count:data.crash_count});
    renderList(data); feedback(); syncURL();
  } catch(error) {
    if(error.name!=='AbortError'&&task.generation===generation) {
      el('map-status').textContent=error.message+' 当前图层和列表尚未更新。'; lastRequest='';
      if(!latest) {el('result-count').textContent='查询未完成';el('map-results').replaceChildren();const td=node('td','请调整筛选并重试。',node('tr','',el('map-results')));td.colSpan=5;}
    }
  } finally {inFlight=false;if(task.generation===generation)el('map-loading').hidden=true;if(queued)run();}
}
async function select(kind,id,initial=false) {
  const ticket=++selectionTicket; if(!initial) appliedFields();
  const params=new URLSearchParams(state); kinds.forEach(k=>params.delete(k));params.set(kind,id);
  try {
    const response=await fetch('/map/selection/?'+params,{headers:{Accept:'application/json'}});
    if(response.redirected) throw new Error('请重新登录后选择对象。');
    const data=await response.json(); if(!response.ok||!data.selected) throw new Error(data.error||'该对象没有可用位置。');
    if(ticket!==selectionTicket) return;
    kinds.forEach(k=>state.delete(k));state.set(kind,id);popup?.remove();
    if(!initial||!state.has('bbox')) map.fitBounds(featureBounds(data.nearby?.features[0]||data.selected),{padding:65,maxZoom:16,duration:0});
    if(!initial){el('traffic-map').scrollIntoView({block:'center',behavior:reduced?'instant':'smooth'});map.getCanvas().focus({preventScroll:true});}
    if(ready) schedule('full',true);
  } catch(error) {el('map-status').textContent=error.message;}
}
function showPopup(feature,coordinates) {
  popup?.remove(); const p=feature.properties, box=node('div',''); box.className='map-popup';
  if(!p.id) {
    node('strong',Number(p.count).toLocaleString()+' 起事故',box);node('small','当前网格内的事故数量，不是风险评分。',box);
    node('small','已达最大缩放级别。可缩短日期范围，或从下方列表查看事故档案。',box);
  } else {
    const kind={'business-roads':'road','business-junctions':'junction','business-sites':'site','business-crash-points':'collision'}[feature.layer.id];
    node('strong',p.name||'未命名对象',box);node('small','#'+p.id+(p.direction?' · '+p.direction:''),box);
    const link=node('a','查看档案',box);link.href=path(kind,p.id);
    const button=node('button','查看附近事故',box);button.type='button';button.className='btn btn-outline-primary btn-sm';button.addEventListener('click',()=>select(kind,p.id));
  }
  popup=new ml.Popup({closeButton:true,closeOnClick:true,focusAfterOpen:false}).setLngLat(coordinates).setDOMContent(box).addTo(map);
}

async function start() {
  ml.setWorkerCount(2);
  const protocol=new pmtiles.Protocol();ml.addProtocol('pmtiles',protocol.tile);
  map=new ml.Map({container:'traffic-map',style:style(),center:[-73.98,40.72],zoom:10,maxZoom:19,minZoom:8,maxBounds:REGION,
    pitchWithRotate:false,dragRotate:false,touchPitch:false,renderWorldCopies:false,fadeDuration:reduced?0:150,
    maxTileCacheSize:96,maxTileCacheZoomLevels:3,pixelRatio:Math.min(devicePixelRatio||1,2),localIdeographFontFamily:'Microsoft YaHei',attributionControl:false,
    locale:{'NavigationControl.ZoomIn':'放大','NavigationControl.ZoomOut':'缩小','AttributionControl.ToggleAttribution':'地图来源','Map.Title':'纽约市道路地图'}});
  map.touchZoomRotate.disableRotation();
  map.addControl(new ml.NavigationControl({showCompass:false}),'top-left');map.addControl(new ml.ScaleControl({unit:'metric',maxWidth:110}),'bottom-left');map.addControl(new ml.AttributionControl({compact:false}),'bottom-right');
  map.on('style.load',()=>{addBusinessLayers();el('map-loading').hidden=true;});
  let reported=false;
  map.on('error',event=>{if(!reported){reported=true;console.error('地图资源加载失败',event.error?.message);fallback('详细底图未能完整加载。');}});
  map.getCanvas().addEventListener('webglcontextlost',()=>fallback('地图绘图环境暂不可用。'));
  let supplied=false;
  if(state.has('bbox')) {const b=state.get('bbox').split(',').map(Number);if(b.length===4&&b.every(Number.isFinite)&&b[0]<b[2]&&b[1]<b[3]&&b[1]>=-85&&b[3]<=85){map.fitBounds([[b[0],b[1]],[b[2],b[3]]],{duration:0,padding:0});supplied=true;}}
  if(!supplied) map.fitBounds(CITY,{padding:30,duration:0});
  const selected=kinds.find(k=>state.has(k));if(selected) await select(selected,state.get(selected),true);
  try {const response=await fetch(config.land);if(response.ok){land=await response.json();addBoroughs();}} catch(_) { /* Reference basemap remains available. */ }
  ready=true; schedule('full',true,false);
  map.on('moveend',()=>schedule());
  map.on('click',event=>{
    if(!map.getLayer('business-roads')) return;
    const order=['business-crash-points','business-clusters','business-sites','business-junctions','business-roads'];
    const found=map.queryRenderedFeatures([[event.point.x-4,event.point.y-4],[event.point.x+4,event.point.y+4]],{layers:order});
    found.sort((a,b)=>order.indexOf(a.layer.id)-order.indexOf(b.layer.id));
    if(found[0]?.layer.id==='business-clusters'&&map.getZoom()<18.9) {popup?.remove();map.easeTo({center:event.lngLat,zoom:Math.min(map.getZoom()+2,19),duration:reduced?0:250});}
    else if(found.length) showPopup(found[0],event.lngLat);
  });
  map.on('mousemove',event=>{
    clearTimeout(hoverTimer);hoverTimer=setTimeout(()=>{
      if(!map.getLayer('business-clusters')) return;
      const features=map.queryRenderedFeatures(event.point,{layers:['business-clusters','business-crash-points','business-sites','business-junctions','business-roads']});
      map.getCanvas().style.cursor=features.length?'pointer':'';
      map.getCanvas().title=features[0]?.properties.count?Number(features[0].properties.count).toLocaleString()+' 起事故':features[0]?.properties.name||'';
    },50);
  });
  let width=el('traffic-map').clientWidth,height=el('traffic-map').clientHeight;
  new ResizeObserver(()=>{
    const target=el('traffic-map');
    if(target.clientWidth!==width||target.clientHeight!==height){width=target.clientWidth;height=target.clientHeight;map.resize();}
  }).observe(el('traffic-map'));
  document.addEventListener('traffic:themechange',()=>{popup?.remove();map.setStyle(style());});
  form.addEventListener('submit',event=>{
    event.preventDefault();
    const previousNetwork=state.get('network')||form.elements.network.options[0]?.value;
    if(previousNetwork!==form.elements.network.value)for(const kind of ['road','junction'])state.delete(kind);
    appliedFields();
    const code=state.get('borough');if(code&&land){const f=land.features.find(f=>String(f.properties.code)===code);if(f)map.fitBounds(featureBounds(f),{padding:30,duration:0});}
    schedule('full',true);
  });
  boxes.forEach(box=>box.addEventListener('change',()=>{
    popup?.remove();setVisibility();syncURL();feedback();
    if(box.value!=='crashes'&&box.checked)schedule('full',true);
  }));
  el('map-home').addEventListener('click',()=>{map.fitBounds(CITY,{padding:30,duration:0});schedule();});
  el('clear-selection').addEventListener('click',()=>{selectionTicket++;kinds.forEach(k=>state.delete(k));schedule('full',true);});
  el('map-reset').addEventListener('click',()=>{selectionTicket++;[...state.keys()].forEach(k=>state.delete(k));form.reset();document.querySelectorAll('.map-picker').forEach(p=>p.hidden=p.dataset.objectPicker!=='road');boxes.forEach(b=>b.checked=true);setVisibility();map.fitBounds(CITY,{padding:30,duration:0});appliedFields();schedule('full',true);});
  el('map-prev').addEventListener('click',()=>{if(page>1){page--;schedule('list');}});el('map-next').addEventListener('click',()=>{if(page<pages){page++;schedule('list');}});
  el('map-object-kind').addEventListener('change',event=>document.querySelectorAll('.map-picker').forEach(p=>p.hidden=p.dataset.objectPicker!==event.target.value));
  document.querySelectorAll('.map-picker input').forEach(input=>input.addEventListener('change',()=>{if(input.value)select(input.closest('.map-picker').dataset.objectPicker,input.value);}));
  addEventListener('pagehide',event=>{controller?.abort();clearTimeout(timer);queued=null;if(!event.persisted)map.remove();});
  addEventListener('pageshow',event=>{if(event.persisted)schedule('full',true,false);});
}
start().catch(error=>{console.error('地图初始化失败',error);fallback('当前浏览器无法打开详细地图。');});
