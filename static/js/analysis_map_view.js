import {CITY,EMPTY,dark,reduced,parseBounds,geometryBounds,boundaryData,latestRequest,inspector,elem} from './map_shared.js?v=20261028-map-ui4';

export function analysisMap(node,adapter){
  const host=node.closest('.map-workspace'),panel=node.closest('details.map-disclosure'),applied=new URL(node.dataset.endpoint,location.origin);
  const state=new URLSearchParams(location.search),status=host.querySelector('[data-map-status]'),loading=host.querySelector('[data-map-loading]');
  const scope=host.querySelector('[data-map-scope]'),home=host.querySelector('[data-map-home]');
  const apply=document.getElementById('apply-map-bounds'),clear=document.getElementById('clear-map-bounds');
  const committed=parseBounds(adapter.kind==='statistics'?applied.searchParams.get('bbox'):null);
  let core,map,module,latest,popup,started=false,starting=false,disposed=false,failed=false,active=!panel||panel.open;
  scope.checked=state.get('map_scope')!=='0';
  const detail=inspector(host.querySelector('[data-map-inspector]'),adapter.describe,f=>show(f));
  const requests=latestRequest({apply:data=>{latest=adapter.prepare(data);render();status.textContent=adapter.status(data);},error:e=>{status.textContent=e.message+' 上次图层尚未更新。';},busy:value=>{loading.hidden=!value;}});
  function bounds(){const b=map.getBounds();return [b.getWest(),b.getSouth(),b.getEast(),b.getNorth()].map(x=>x.toFixed(6)).join(',');}
  // A MapLibre zoom uses a 512 px world tile; the existing analysis API's grid
  // uses 256 px. Converting here preserves the intended screen-space density.
  function zoom(){return Math.floor(map.getZoom()+1);}
  function sync(){
    if(!map||disposed)return;
    const p=new URLSearchParams(location.search);p.set('map_view_bbox',bounds());p.set('map_open',panel&&!panel.open?'0':'1');p.set('map_scope',scope.checked?'1':'0');
    p.delete('map_mode');p.delete('basic');history.replaceState(null,'',location.pathname+'?'+p);
    if(adapter.kind==='statistics'){
      const dest=new URL(location.pathname,location.origin);dest.search=applied.search;dest.searchParams.set('bbox',bounds());dest.searchParams.delete('page');dest.searchParams.set('map_open','1');apply.href=dest;
      const clean=new URL(dest);clean.searchParams.delete('bbox');clear.href=clean;clear.hidden=!committed;
    }
  }
  function request(force=false){if(!started||!active||disposed)return;sync();const url=new URL(applied);for(const k of [...url.searchParams.keys()])if(k.startsWith('map_')||k==='page')url.searchParams.delete(k);url.searchParams.set('view_bbox',bounds());url.searchParams.set('zoom',zoom());requests.request(url,force);}
  function fit(b,padding=25,maxZoom=18){map.fitBounds(b,{padding,maxZoom,duration:0});}
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
    map.getSource('analysis-data')?.setData(latest);
  }
  function show(feature){
    if(!map)return;const coordinate=feature.geometry.coordinates,box=elem('div','');box.className='map-popup';
    const enlarge=()=>{popup?.remove();map.easeTo({center:coordinate,zoom:Math.min(19,map.getZoom()+2),duration:reduced()?0:250});};
    adapter.popup(feature,box,enlarge);
    popup?.remove();popup=new module.ml.Popup({closeButton:true,focusAfterOpen:true}).setLngLat(coordinate).setDOMContent(box).addTo(map);
  }
  function fail(message){if(disposed)return;failed=true;loading.hidden=true;node.setAttribute('aria-busy','false');const notice=host.querySelector('[data-map-error]');notice.hidden=false;notice.textContent=message+' 请刷新地图重试。';}
  async function initialize(){
    if(starting||disposed)return;starting=true;loading.hidden=false;host.querySelector('[data-map-error]').hidden=true;
    try{
      module=await import('./map_core.js?v=20261028-map-ui4');core=module.createMap(node,node.dataset,{onStyle:addLayers,onFailure:fail,onTheme:()=>popup?.remove(),scopeVisible:scope.checked,cooperative:true});map=core.map;map.on('moveend',()=>request());
        map.on('click',event=>{if(!map.getLayer('analysis-points'))return;const layers=adapter.kind==='risk'?['analysis-points']:['analysis-points','analysis-clusters'];const features=map.queryRenderedFeatures([[event.point.x-5,event.point.y-5],[event.point.x+5,event.point.y+5]],{layers});if(features.length)show(features[0]);});
      let initial=parseBounds(state.get('map_view_bbox'))||committed;
      if(!initial){const boundary=await boundaryData(node.dataset.boundary).catch(()=>null);const borough=applied.searchParams.get('borough')||node.dataset.borough;const f=boundary?.features.find(f=>f.properties.role==='borough'&&String(f.properties.code)===borough);initial=f?geometryBounds(f.geometry):CITY;}
      fit(initial,state.has('map_view_bbox')||committed?0:25);
      started=true;starting=false;render();request(true);sync();
    }catch(_){starting=false;loading.hidden=true;fail('地图暂时无法显示。');}
  }
  scope.addEventListener('change',()=>{core?.setScopeVisible(scope.checked);sync();});home.addEventListener('click',()=>{if(map)fit(CITY);});
  host.querySelector('[data-map-retry]').addEventListener('click',()=>{if(failed)location.reload();else request(true);});
  if(apply)apply.addEventListener('click',e=>{if(!started)e.preventDefault();else sync();});
  function activate(){active=true;requests.resume();if(!started&&!starting&&!failed)initialize();else if(started){map.resize();request();}}
  if(panel){panel.addEventListener('toggle',()=>{if(panel.open)activate();else{active=false;requests.pause();sync();}});if(state.get('map_open')==='1')panel.open=true;if(panel.open)activate();}
  else{const observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){activate();observer.disconnect();}},{rootMargin:'150px'});observer.observe(node);}
  addEventListener('pagehide',event=>{requests.pause();if(!event.persisted){disposed=true;requests.destroy();core?.destroy();}});
  addEventListener('pageshow',event=>{if(event.persisted&&(!panel||panel.open)){active=true;requests.resume();if(map){map.resize();request(true);}else activate();}});
}
