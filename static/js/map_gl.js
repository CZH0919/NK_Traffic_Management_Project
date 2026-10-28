import {ml,CITY,EMPTY,createMap,featureBounds} from './map_core.js?v=20261028-map-ui4';
import {inspector} from './map_shared.js?v=20261028-map-ui4';

const el = id => document.getElementById(id);
const form = el('map-filters'), config = el('map-script').dataset;
const state = new URLSearchParams(location.search), boxes = [...document.querySelectorAll('.map-layer-controls input')];
const kinds = ['road','junction','site','collision'];
let map, core, detail, land, latest, popup, timer, generation=0, inFlight=false, queued=null, lastRequest='', ready=false;
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
function appliedFields() {
  for(const key of ['borough','network','start','end','radius']) {
    const value=form.elements[key].value; value?state.set(key,value):state.delete(key);
  }
}
function publicParams() {
  const p=new URLSearchParams(state); p.delete('basic'); p.delete('map_mode'); p.delete('part'); p.delete('statistics');
  p.set('layers',boxes.filter(b=>b.checked).map(b=>b.value).join(','));
  const b=map.getBounds(); p.set('bbox',[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()].map(x=>x.toFixed(6)).join(','));
  p.set('zoom',Math.floor(map.getZoom())); p.set('page',page); return p;
}
function syncURL() {
  if(!map) return;
  const p=publicParams(); history.replaceState(null,'',`${location.pathname}?${p}`);
}
function showError(message) {
  el('map-loading').hidden=true;el('traffic-map').setAttribute('aria-busy','false');
  el('map-resource-error').hidden=false;el('map-resource-message').textContent=message+' 请刷新地图重试。';
}
el('map-retry').addEventListener('click',()=>location.reload());

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
  if(latest) updateSources(latest);
  setVisibility();
}
function updateSources(data) {
  detail?.update(data.crashes.features);
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
  el('map-summary').textContent=`${data.network} · ${data.crash_count.toLocaleString()} 起有坐标事故`;
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
    el('selection-description').textContent=`当前视野与所选对象 ${state.get('radius')||100} 米周边范围内的事故。`;
  } else el('selection-description').textContent='列表显示当前视野内符合筛选条件的事故。';
}
function feedback() {
  if(!latest) return;
  const zoom=Math.floor(map.getZoom());
  el('map-mode').textContent=zoom<13?'城市全览':zoom<15?'街区视图':'道路详情';
  const mode=!visible('crashes')?'事故图层已隐藏；列表及总数保持不变。':latest.crash_mode==='aggregate'?'点击聚合标记查看事故数量或放大。k 表示千。':'事故逐点显示；点击可查看档案与附近事故。';
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
    node('strong',Number(p.count).toLocaleString()+' 起事故',box);node('small','当前区域内的事故数量。',box);
    if(map.getZoom()<18.9){const button=node('button','放大此处',box);button.type='button';button.className='btn btn-outline-primary btn-sm';button.onclick=()=>{popup?.remove();map.easeTo({center:coordinates,zoom:Math.min(map.getZoom()+2,19),duration:reduced?0:250});};}else node('small','已达最大缩放级别。可缩短日期范围，或从下方列表查看事故档案。',box);
  } else {
    const kind={'business-roads':'road','business-junctions':'junction','business-sites':'site','business-crash-points':'collision'}[feature.layer.id];
    node('strong',p.name||'未命名对象',box);node('small','#'+p.id+(p.direction?' · '+p.direction:''),box);
    const link=node('a','查看档案',box);link.href=path(kind,p.id);
    const button=node('button','查看附近事故',box);button.type='button';button.className='btn btn-outline-primary btn-sm';button.addEventListener('click',()=>select(kind,p.id));
  }
  popup=new ml.Popup({closeButton:true,closeOnClick:true,focusAfterOpen:true}).setLngLat(coordinates).setDOMContent(box).addTo(map);
}

async function start() {
  core=createMap(el('traffic-map'),config,{onStyle:()=>{addBusinessLayers();el('map-loading').hidden=true;},onFailure:showError,onTheme:()=>popup?.remove(),scopeVisible:state.get('map_scope')!=='0'});
  map=core.map;
  const scope=el('map-scope');scope.checked=state.get('map_scope')!=='0';
  scope.addEventListener('change',()=>{state.set('map_scope',scope.checked?'1':'0');core.setScopeVisible(scope.checked);syncURL();});
  detail=inspector(el('map-inspector-host'),f=>f.properties.id?`事故 #${f.properties.id}`:`${Number(f.properties.count).toLocaleString()} 起事故（聚合）`,f=>showPopup({...f,layer:{id:f.properties.id?'business-crash-points':'business-clusters'}},f.geometry.coordinates));
  let supplied=false;
  if(state.has('bbox')) {const b=state.get('bbox').split(',').map(Number);if(b.length===4&&b.every(Number.isFinite)&&b[0]<b[2]&&b[1]<b[3]&&b[1]>=-85&&b[3]<=85){map.fitBounds([[b[0],b[1]],[b[2],b[3]]],{duration:0,padding:0});supplied=true;}}
  if(!supplied) map.fitBounds(CITY,{padding:30,duration:0});
  const selected=kinds.find(k=>state.has(k));if(selected) await select(selected,state.get(selected),true);
  const boundaries=await core.boundaryReady;
  if(boundaries)land={type:'FeatureCollection',features:boundaries.features.filter(f=>f.properties.role==='borough')};
  ready=true; schedule('full',true,false);
  map.on('moveend',()=>schedule());
  map.on('click',event=>{
    if(!map.getLayer('business-roads')) return;
    const order=['business-crash-points','business-clusters','business-sites','business-junctions','business-roads'];
    const found=map.queryRenderedFeatures([[event.point.x-4,event.point.y-4],[event.point.x+4,event.point.y+4]],{layers:order});
    found.sort((a,b)=>order.indexOf(a.layer.id)-order.indexOf(b.layer.id));
    if(found.length) showPopup(found[0],event.lngLat);
  });
  map.on('mousemove',event=>{
    clearTimeout(hoverTimer);hoverTimer=setTimeout(()=>{
      if(!map.getLayer('business-clusters')) return;
      const features=map.queryRenderedFeatures(event.point,{layers:['business-clusters','business-crash-points','business-sites','business-junctions','business-roads']});
      map.getCanvas().style.cursor=features.length?'pointer':'';
      map.getCanvas().title=features[0]?.properties.count?Number(features[0].properties.count).toLocaleString()+' 起事故':features[0]?.properties.name||'';
    },50);
  });
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
  el('map-reset').addEventListener('click',()=>{selectionTicket++;[...state.keys()].forEach(k=>state.delete(k));form.reset();scope.checked=true;core.setScopeVisible(true);state.set('map_scope','1');document.querySelectorAll('.map-picker').forEach(p=>p.hidden=p.dataset.objectPicker!=='road');boxes.forEach(b=>b.checked=true);setVisibility();map.fitBounds(CITY,{padding:30,duration:0});appliedFields();schedule('full',true);});
  el('map-prev').addEventListener('click',()=>{if(page>1){page--;schedule('list');}});el('map-next').addEventListener('click',()=>{if(page<pages){page++;schedule('list');}});
  el('map-object-kind').addEventListener('change',event=>document.querySelectorAll('.map-picker').forEach(p=>p.hidden=p.dataset.objectPicker!==event.target.value));
  document.querySelectorAll('.map-picker input').forEach(input=>input.addEventListener('change',()=>{if(input.value)select(input.closest('.map-picker').dataset.objectPicker,input.value);}));
  addEventListener('pagehide',event=>{controller?.abort();clearTimeout(timer);queued=null;if(!event.persisted){clearTimeout(hoverTimer);core.destroy();}});
  addEventListener('pageshow',event=>{if(event.persisted)schedule('full',true,false);});
}
start().catch(error=>{console.error('地图初始化失败',error);showError('地图暂时无法显示。');});
