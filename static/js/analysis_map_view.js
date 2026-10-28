import {CITY,EMPTY,dark,reduced,parseBounds,geometryBounds,boundaryData,latestRequest,inspector,elem} from './map_shared.js?v=20261028-unified3';

export function analysisMap(node,adapter){
  const host=node.closest('.map-workspace'),panel=node.closest('details.map-disclosure'),applied=new URL(node.dataset.endpoint,location.origin);
  const state=new URLSearchParams(location.search),status=host.querySelector('[data-map-status]'),loading=host.querySelector('[data-map-loading]');
  const scope=host.querySelector('[data-map-scope]'),home=host.querySelector('[data-map-home]'),mode=host.querySelector('[data-map-mode]'),fallback=host.querySelector('[data-map-fallback]');
  const apply=document.getElementById('apply-map-bounds'),clear=document.getElementById('clear-map-bounds');
  const committed=parseBounds(adapter.kind==='statistics'?applied.searchParams.get('bbox'):null);
  let core,map,engine,module,latest,popup,started=false,starting=false,disposed=false,active=!panel||panel.open,leafletLayer,scopeLayer;
  scope.checked=state.get('map_scope')!=='0';
  const detail=inspector(host.querySelector('[data-map-inspector]'),adapter.describe,f=>show(f));
  const requests=latestRequest({apply:data=>{latest=adapter.prepare(data);render();status.textContent=adapter.status(data);},error:e=>{status.textContent=e.message+' 上次图层尚未更新。';},busy:value=>{loading.hidden=!value;}});
  function bounds(){const b=map.getBounds();return [b.getWest(),b.getSouth(),b.getEast(),b.getNorth()].map(x=>x.toFixed(6)).join(',');}
  // A MapLibre zoom uses a 512 px world tile; the existing analysis API's grid
  // uses 256 px. Converting here preserves the intended screen-space density.
  function zoom(){return engine==='gl'?Math.floor(map.getZoom()+1):Math.floor(map.getZoom());}
  function sync(){
    if(!map||disposed)return;
    const p=new URLSearchParams(location.search);p.set('map_view_bbox',bounds());p.set('map_open',panel&&!panel.open?'0':'1');p.set('map_scope',scope.checked?'1':'0');
    engine==='basic'?p.set('map_mode','basic'):p.delete('map_mode');history.replaceState(null,'',location.pathname+'?'+p);
    const back=new URL(location.href);if(engine==='basic')back.searchParams.delete('map_mode');else back.searchParams.set('map_mode','basic');fallback.href=back;fallback.textContent=engine==='basic'?'重新尝试详细地图':'使用基础地图';
    if(adapter.kind==='statistics'){
      const dest=new URL(location.pathname,location.origin);dest.search=applied.search;dest.searchParams.set('bbox',bounds());dest.searchParams.delete('page');dest.searchParams.set('map_open','1');apply.href=dest;
      const clean=new URL(dest);clean.searchParams.delete('bbox');clear.href=clean;clear.hidden=!committed;
    }
  }
  function request(force=false){if(!started||!active||disposed)return;sync();const url=new URL(applied);for(const k of [...url.searchParams.keys()])if(k.startsWith('map_')||k==='page')url.searchParams.delete(k);url.searchParams.set('view_bbox',bounds());url.searchParams.set('zoom',zoom());requests.request(url,force);}
  function fit(b,padding=25,maxZoom=18){if(engine==='gl')map.fitBounds(b,{padding,maxZoom,duration:0});else map.fitBounds([[b[0][1],b[0][0]],[b[1][1],b[1][0]]],{padding:[padding,padding],maxZoom:maxZoom+1,animate:false});}
  function scopeFeature(){if(!committed)return EMPTY;const [[w,s],[e,n]]=committed;return {type:'FeatureCollection',features:[{type:'Feature',properties:{},geometry:{type:'Polygon',coordinates:[[[w,s],[e,s],[e,n],[w,n],[w,s]]]}}]};}
  function addLayers(){
    const labels=map.getStyle().layers.find(l=>l.type==='symbol')?.id;
    map.addSource('analysis-scope',{type:'geojson',data:scopeFeature()});
    map.addLayer({id:'analysis-scope-fill',type:'fill',source:'analysis-scope',paint:{'fill-color':dark()?'#bea6e4':'#775eae','fill-opacity':.035}},labels);
    map.addLayer({id:'analysis-scope-line',type:'line',source:'analysis-scope',paint:{'line-color':dark()?'#bea6e4':'#775eae','line-width':1.6,'line-dasharray':[2,2]}},labels);
    map.addSource('analysis-data',{type:'geojson',data:latest||EMPTY,tolerance:.5});
    const color=adapter.kind==='risk'?adapter.colorExpression(dark()):adapter.color(dark());
    map.addLayer({id:'analysis-points',type:'circle',source:'analysis-data',filter:['!=',['get','aggregate'],true],paint:{'circle-color':color,'circle-radius':adapter.kind==='risk'?['interpolate',['linear'],['zoom'],8,2.8,12,4.5,16,6]:4,'circle-stroke-color':dark()?'#1a303f':'#fff','circle-stroke-width':adapter.kind==='risk'?1:1.4,'circle-opacity':.88}});
    if(adapter.kind==='statistics'){
      map.addLayer({id:'analysis-clusters',type:'circle',source:'analysis-data',filter:['==',['get','aggregate'],true],paint:{'circle-radius':['interpolate',['linear'],['get','n'],1,10,100,13,1000,17,10000,21],'circle-color':color,'circle-opacity':.9,'circle-stroke-color':dark()?'#1a303f':'#fff','circle-stroke-width':1.3}});
      map.addLayer({id:'analysis-counts',type:'symbol',source:'analysis-data',filter:['==',['get','aggregate'],true],layout:{'text-field':['get','short_count'],'text-font':['Noto Sans Medium'],'text-size':11,'text-allow-overlap':false},paint:{'text-color':dark()?'#132a35':'#fff'}});
    }
  }
  function render(){
    if(!latest)return;detail.update(latest.features);
    if(engine==='gl'){map.getSource('analysis-data')?.setData(latest);return;}
    leafletLayer.clearLayers();
    L.geoJSON(latest,{pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:f.properties.aggregate?Math.min(20,10+Math.log10(Math.max(1,f.properties.n))*2):adapter.kind==='risk'?6:4,fillColor:adapter.color(dark(),f.properties.grade),fillOpacity:.9,color:dark()?'#1a303f':'#fff',weight:1.4}),onEachFeature:(f,l)=>{l.on('click',()=>show(f));if(f.properties.aggregate)l.bindTooltip(f.properties.short_count,{permanent:true,direction:'center',className:'map-basic-count'});}}).addTo(leafletLayer);
  }
  function show(feature){
    if(!map)return;const coordinate=feature.geometry.coordinates,box=elem('div','');box.className='map-popup';
    const enlarge=()=>{popup?.remove?.();if(engine==='gl')map.easeTo({center:coordinate,zoom:Math.min(19,map.getZoom()+2),duration:reduced()?0:250});else map.setView([coordinate[1],coordinate[0]],Math.min(20,map.getZoom()+2));};
    adapter.popup(feature,box,enlarge);
    if(engine==='gl'){popup?.remove();popup=new module.ml.Popup({closeButton:true,focusAfterOpen:true}).setLngLat(coordinate).setDOMContent(box).addTo(map);}
    else popup=L.popup().setLatLng([coordinate[1],coordinate[0]]).setContent(box).openOn(map);
  }
  function fail(message){if(disposed)return;loading.hidden=true;const notice=host.querySelector('[data-map-error]');notice.hidden=false;notice.textContent=message+' 可切换基础地图；业务范围保持不变。';fallback.hidden=false;fallback.classList.add('btn','btn-outline-primary','btn-sm');}
  async function initialize(basic=false,previousBounds=null){
    if(starting||disposed)return;starting=true;loading.hidden=false;host.querySelector('[data-map-error]').hidden=true;
    try{
      if(basic){engine='basic';module=await import('./map_basic_core.js?v=20261028-unified3');core=await module.createBasic(node,node.dataset,{onMove:()=>request(),scopeVisible:scope.checked,onFailure:fail});map=core.map;leafletLayer=L.layerGroup().addTo(map);scopeLayer=L.geoJSON(scopeFeature(),{interactive:false,style:()=>({color:dark()?'#bea6e4':'#775eae',weight:1.6,dashArray:'3 3',fillOpacity:.035})}).addTo(map);mode.textContent='基础地图';}
      else{engine='gl';module=await import('./map_core.js?v=20261028-unified3');core=module.createMap(node,node.dataset,{onStyle:addLayers,onFailure:fail,onTheme:()=>popup?.remove(),scopeVisible:scope.checked,cooperative:true});map=core.map;mode.textContent='本地详细地图';map.on('moveend',()=>request());
        map.on('click',event=>{if(!map.getLayer('analysis-points'))return;const layers=adapter.kind==='risk'?['analysis-points']:['analysis-points','analysis-clusters'];const features=map.queryRenderedFeatures([[event.point.x-5,event.point.y-5],[event.point.x+5,event.point.y+5]],{layers});if(features.length)show(features[0]);});
      }
      let initial=previousBounds||parseBounds(state.get('map_view_bbox'))||committed;
      if(!initial){const boundary=await boundaryData(node.dataset.boundary).catch(()=>null);const borough=applied.searchParams.get('borough')||node.dataset.borough;const f=boundary?.features.find(f=>f.properties.role==='borough'&&String(f.properties.code)===borough);initial=f?geometryBounds(f.geometry):CITY;}
      fit(initial,previousBounds||state.has('map_view_bbox')||committed?0:25);
      started=true;starting=false;render();request(true);sync();
    }catch(_){starting=false;loading.hidden=true;fail('当前浏览器无法打开详细地图。');}
  }
  async function basicMode(){
    if(starting)return;
    const previous=map?parseBounds(bounds()):null;started=false;requests.pause();core?.destroy();core=null;map=null;popup=null;node.replaceChildren();requests.resume();await initialize(true,previous);
  }
  fallback.addEventListener('click',e=>{if(engine==='basic')return;e.preventDefault();basicMode();});
  scope.addEventListener('change',()=>{core?.setScopeVisible(scope.checked);sync();});home.addEventListener('click',()=>{if(map)fit(CITY);});
  host.querySelector('[data-map-retry]').addEventListener('click',()=>request(true));
  if(apply)apply.addEventListener('click',e=>{if(!started)e.preventDefault();else sync();});
  function theme(){if(engine==='basic'&&map){popup?.remove();scopeLayer?.setStyle({color:dark()?'#bea6e4':'#775eae'});render();}}
  document.addEventListener('traffic:themechange',theme);
  function activate(){active=true;requests.resume();if(!started&&!starting)initialize(state.get('map_mode')==='basic');else if(started){engine==='gl'?map.resize():map.invalidateSize();request();}}
  if(panel){panel.addEventListener('toggle',()=>{if(panel.open)activate();else{active=false;requests.pause();sync();}});if(state.get('map_open')==='1')panel.open=true;if(panel.open)activate();}
  else{const observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){activate();observer.disconnect();}},{rootMargin:'150px'});observer.observe(node);}
  addEventListener('pagehide',event=>{requests.pause();if(!event.persisted){disposed=true;requests.destroy();core?.destroy();document.removeEventListener('traffic:themechange',theme);}});
  addEventListener('pageshow',event=>{if(event.persisted&&(!panel||panel.open)){active=true;requests.resume();if(map){engine==='gl'?map.resize():map.invalidateSize();request(true);}else activate();}});
}
