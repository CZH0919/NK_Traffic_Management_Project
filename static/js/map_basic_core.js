import {CITY,REGION,dark,boundaryData,json} from './map_shared.js?v=20261028-unified3';
let leafletReady;
export function loadLeaflet(){
  if(window.L)return Promise.resolve(window.L);
  if(!leafletReady)leafletReady=new Promise((resolve,reject)=>{
    const css=document.createElement('link');css.rel='stylesheet';css.href='/static/vendor/leaflet/leaflet.css';document.head.append(css);
    const script=document.createElement('script');script.src='/static/vendor/leaflet/leaflet.js';script.onload=()=>resolve(window.L);script.onerror=()=>reject(new Error('基础地图未能加载，请使用结果列表。'));document.head.append(script);
  });return leafletReady;
}
export async function attachBoundary(map,config,initial=true){
  const data=await boundaryData(config.boundary);
  if(!map.getPane('research')){map.createPane('research');map.getPane('research').style.zIndex=350;map.getPane('research').style.pointerEvents='none';}
  const style=f=>f.properties.role==='outside'?{stroke:false,fillColor:dark()?'#061521':'#677e88',fillOpacity:dark()?.12:.09}:{color:dark()?'#9bc7cc':'#487782',weight:f.properties.role==='city'?2.4:.85,dashArray:f.properties.role==='city'?'9 6':'2 4',fill:false,opacity:.85};
  const group=L.geoJSON(data,{pane:'research',interactive:false,filter:f=>['city','internal','outside'].includes(f.properties.role),style});
  if(initial)group.addTo(map);
  function theme(){group.setStyle(style);}
  document.addEventListener('traffic:themechange',theme);
  return {data,setVisible(value){if(value)group.addTo(map);else group.remove();},destroy(){group.remove();document.removeEventListener('traffic:themechange',theme);}};
}
export async function createBasic(node,config,{onMove,scopeVisible=true,onFailure=()=>{}}){
  await loadLeaflet();
  const map=L.map(node,{preferCanvas:true,minZoom:9,maxZoom:20,maxBounds:[[REGION[0][1],REGION[0][0]],[REGION[1][1],REGION[1][0]]],scrollWheelZoom:false}).fitBounds([[CITY[0][1],CITY[0][0]],[CITY[1][1],CITY[1][0]]]);
  map.attributionControl.setPrefix(false);map.attributionControl.addAttribution('NYC DCP / NYC Open Data · 基础地图');L.control.scale({imperial:false}).addTo(map);
  let land,boundary,disposed=false;
  const landStyle=()=>({fillColor:dark()?'#243744':'#eff1ed',fillOpacity:1,color:dark()?'#638796':'#a3bfc8',weight:.8});
  try{land=L.geoJSON(await json(config.land||'/static/data/borough_land.geojson'),{interactive:false,style:landStyle}).addTo(map).bringToBack();boundary=await attachBoundary(map,config,scopeVisible);}catch(_){onFailure('基础背景未能完整加载，仍可查询业务数据。');}
  function theme(){land?.setStyle(landStyle());}document.addEventListener('traffic:themechange',theme);
  let width=node.clientWidth,height=node.clientHeight;
  const observer=new ResizeObserver(()=>{if(disposed||!node.clientWidth||!node.clientHeight)return;if(node.clientWidth!==width||node.clientHeight!==height){width=node.clientWidth;height=node.clientHeight;map.invalidateSize();}});observer.observe(node);
  map.on('moveend',onMove);
  return {map,boundary:boundary?.data,setScopeVisible:value=>boundary?.setVisible(value),destroy(){disposed=true;observer.disconnect();document.removeEventListener('traffic:themechange',theme);boundary?.destroy();map.remove();}};
}
