import * as ml from '../vendor/maplibre/maplibre-gl.mjs';
import {CITY,REGION,EMPTY,dark,reduced,boundaryData} from './map_shared.js?v=20261028-map-ui4';
export {ml,CITY,REGION,EMPTY};
let protocol;

export function featureBounds(feature) {
  const bounds=new ml.LngLatBounds();
  function visit(coords){if(typeof coords[0]==='number')bounds.extend(coords);else coords.forEach(visit);}
  visit(feature.geometry.coordinates);return bounds;
}

function style(config) {
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
      attribution:'© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a> · <a href="https://protomaps.com" target="_blank" rel="noopener">Protomaps</a> · NYC Open Data'}}, layers};
}


export function createMap(node,config,{onStyle=()=>{},onFailure=()=>{},onTheme=()=>{},scopeVisible=true,cooperative=false}={}) {
  if(!protocol){ml.setWorkerCount(2);protocol=new pmtiles.Protocol();ml.addProtocol('pmtiles',protocol.tile);}
  let boundary,styleReady=false,disposed=false,reported=false,scope=scopeVisible,themePending=false;
  node.setAttribute('aria-busy','true');
  const map=new ml.Map({container:node,style:style(config),center:[-73.98,40.72],zoom:10,maxZoom:19,minZoom:8,maxBounds:REGION,
    pitchWithRotate:false,dragRotate:false,touchPitch:false,renderWorldCopies:false,fadeDuration:reduced()?0:150,
    maxTileCacheSize:96,maxTileCacheZoomLevels:3,pixelRatio:Math.min(devicePixelRatio||1,2),localIdeographFontFamily:'Microsoft YaHei',attributionControl:false,
    cooperativeGestures:cooperative,
    locale:{'NavigationControl.ZoomIn':'放大','NavigationControl.ZoomOut':'缩小','AttributionControl.ToggleAttribution':'地图来源','Map.Title':node.getAttribute('aria-label')||'纽约市地图',
      'CooperativeGesturesHandler.WindowsHelpText':'按住 Ctrl 并滚动以缩放地图','CooperativeGesturesHandler.MacHelpText':'按住 ⌘ 并滚动以缩放地图','CooperativeGesturesHandler.MobileHelpText':'使用双指移动地图'}});
  map.touchZoomRotate.disableRotation();
  map.addControl(new ml.NavigationControl({showCompass:false}),'top-left');
  map.addControl(new ml.ScaleControl({unit:'metric',maxWidth:110}),'bottom-left');
  map.addControl(new ml.AttributionControl({compact:false}),'bottom-right');
  function fail(message){if(!disposed&&!reported){reported=true;onFailure(message);}}
  function setScopeVisible(value){scope=value;for(const id of ['research-outside','research-halo','research-city','research-internal','research-labels'])if(map.getLayer(id))map.setLayoutProperty(id,'visibility',scope?'visible':'none');}
  function installBoundary(){
    if(!boundary||!styleReady||disposed||map.getSource('research-boundary'))return;
    const before=map.getStyle().layers.find(l=>l.id==='nearby-fill'||l.id==='analysis-scope-fill'||l.type==='symbol')?.id;
    map.addSource('research-boundary',{type:'geojson',data:boundary,tolerance:0.2});
    const filter=role=>['==',['get','role'],role];
    map.addLayer({id:'research-outside',type:'fill',source:'research-boundary',filter:filter('outside'),paint:{'fill-color':dark()?'#061521':'#677e88','fill-opacity':dark()?0.12:0.09}},before);
    map.addLayer({id:'research-halo',type:'line',source:'research-boundary',filter:filter('city'),paint:{'line-color':dark()?'#182e3b':'#f7faf9','line-opacity':0.85,'line-width':['interpolate',['linear'],['zoom'],8,4.6,15,3.5]}},before);
    map.addLayer({id:'research-city',type:'line',source:'research-boundary',filter:filter('city'),paint:{'line-color':dark()?'#9bc7cc':'#487782','line-opacity':0.95,'line-width':['interpolate',['linear'],['zoom'],8,2.4,15,1.6],'line-dasharray':[4,2.5]}},before);
    map.addLayer({id:'research-internal',type:'line',source:'research-boundary',filter:filter('internal'),paint:{'line-color':dark()?'#8aa9b7':'#71939b','line-width':0.85,'line-opacity':0.7,'line-dasharray':[2,3]}},before);
    map.addLayer({id:'research-labels',type:'symbol',source:'research-boundary',filter:filter('label'),maxzoom:13,layout:{'text-field':['get','name'],'text-font':['Noto Sans Medium'],'text-size':13,'text-allow-overlap':false,'text-padding':5},paint:{'text-color':dark()?'#d5e3e8':'#365969','text-halo-color':dark()?'#243744':'#f3f5ef','text-halo-width':2}});
    setScopeVisible(scope);
  }
  map.on('style.load',()=>{styleReady=true;installBoundary();onStyle(map);});
  map.on('dataloading',()=>node.setAttribute('aria-busy','true'));
  map.on('idle',()=>node.setAttribute('aria-busy','false'));
  const boundaryReady=boundaryData(config.boundary).then(data=>{boundary=data;installBoundary();return data;}).catch(()=>{fail('研究范围边界未能加载。');return null;});
  map.on('error',()=>fail('地图未能完整加载。'));
  map.getCanvas().addEventListener('webglcontextlost',()=>fail('地图显示已中断。'));
  function changeTheme(){if(disposed)return;if(!node.clientWidth||!node.clientHeight){themePending=true;return;}themePending=false;node.setAttribute('aria-busy','true');onTheme();styleReady=false;map.setStyle(style(config));}
  document.addEventListener('traffic:themechange',changeTheme);
  let width=node.clientWidth,height=node.clientHeight;
  const observer=new ResizeObserver(()=>{if(disposed||!node.clientWidth||!node.clientHeight)return;if(themePending)changeTheme();if(width!==node.clientWidth||height!==node.clientHeight){width=node.clientWidth;height=node.clientHeight;map.resize();}});
  observer.observe(node);
  return {map,boundaryReady,setScopeVisible,destroy(){if(disposed)return;disposed=true;observer.disconnect();document.removeEventListener('traffic:themechange',changeTheme);map.remove();}};
}
