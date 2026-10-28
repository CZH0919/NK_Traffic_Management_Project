// Renderer-independent display helpers. Business conditions remain with each adapter.
export const EMPTY = {type:'FeatureCollection',features:[]};
export const CITY = [[-74.25874746,40.47737324],[-73.70000906,40.91766181]];
export const REGION = [[-74.65,40.35],[-73.30,41.06]];
export const dark = () => document.documentElement.dataset.theme === 'dark';
export const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
export const shortCount = n => n >= 10000 ? (n/1000).toFixed(0)+'k' : n >= 1000 ? (n/1000).toFixed(1).replace('.0','')+'k' : String(n);
export function elem(tag,text,parent) {const n=document.createElement(tag);n.textContent=text;parent?.append(n);return n;}
export function parseBounds(value) {
  if(!value)return null;
  const b=value.split(',').map(Number);
  return b.length===4&&b.every(Number.isFinite)&&b[0]<b[2]&&b[1]<b[3]&&b[0]>=-180&&b[2]<=180&&b[1]>=-85&&b[3]<=85 ? [[b[0],b[1]],[b[2],b[3]]] : null;
}
export function geometryBounds(geometry) {
  const b=[Infinity,Infinity,-Infinity,-Infinity];
  function visit(c){if(typeof c[0]==='number'){b[0]=Math.min(b[0],c[0]);b[1]=Math.min(b[1],c[1]);b[2]=Math.max(b[2],c[0]);b[3]=Math.max(b[3],c[1]);}else c.forEach(visit);}
  if(geometry.type==='GeometryCollection') geometry.geometries.forEach(g=>{const x=geometryBounds(g);visit(x);}); else visit(geometry.coordinates);
  return [[b[0],b[1]],[b[2],b[3]]];
}
export async function json(url,signal) {
  let response;
  try{response=await fetch(url,{signal,headers:{Accept:'application/json'}});}
  catch(error){if(error.name==='AbortError')throw error;throw new Error('地图请求未完成，请检查本机服务并重试。');}
  if(response.redirected)throw new Error('登录已失效，请刷新页面重新登录。');
  if(!response.ok)throw new Error('地图查询未完成，请重试或调整条件。');
  try{return await response.json();}catch(_){throw new Error('地图响应未能读取，请重试。');}
}
const boundaryFiles=new Map();
export function boundaryData(url='/static/data/nyc_research_boundary.geojson') {
  if(!boundaryFiles.has(url))boundaryFiles.set(url,json(url).catch(error=>{boundaryFiles.delete(url);throw error;}));
  return boundaryFiles.get(url);
}

// At most one in-flight request. Moving replaces the pending view, never starts
// parallel database work; a previous response cannot overwrite a newer view.
export function latestRequest({apply,error,busy,delay=180}) {
  let queued=null,inFlight=false,timer,serial=0,signature='',controller,paused=false,disposed=false;
  async function run(){
    if(inFlight||!queued||paused||disposed)return;
    const task=queued;queued=null;
    if(!task.force&&signature===task.url){busy?.(false);return;}
    inFlight=true;controller=new AbortController();
    try{const data=await json(task.url,controller.signal);if(task.serial===serial&&!paused&&!disposed){signature=task.url;apply(data);}}
    catch(e){if(e.name!=='AbortError'&&task.serial===serial&&!paused&&!disposed){signature='';error(e);}}
    finally{inFlight=false;if(task.serial===serial)busy?.(false);if(queued)run();}
  }
  return {
    request(url,force=false){if(disposed||paused)return;serial++;queued={url:String(url),force,serial};clearTimeout(timer);busy?.(true);timer=setTimeout(run,delay);},
    pause(){paused=true;serial++;queued=null;clearTimeout(timer);busy?.(false);},
    resume(){paused=false;},
    destroy(){disposed=true;serial++;queued=null;clearTimeout(timer);controller?.abort();},
  };
}

// Exact quantities and popup actions remain available without hover or a pointer.
// Only 20 rows are materialised, even when the map contains 1,000 grid cells.
export function inspector(host,describe,show){
  const panel=elem('details','',host);panel.className='map-inspector';
  const summary=elem('summary','查看地图数据',panel),content=elem('div','',panel);
  let rows=[],page=0;
  function render(){
    content.replaceChildren();if(!panel.open)return;
    const list=elem('ul','',content);list.className='map-feature-list';
    rows.slice(page*20,page*20+20).forEach(f=>{const row=elem('li','',list),button=elem('button',describe(f),row);button.type='button';button.className='map-feature-button';button.addEventListener('click',()=>show(f));});
    if(!rows.length)elem('p','当前地图没有可显示的对象。',content);
    if(rows.length>20){const bar=elem('div','',content);bar.className='pagination-row';elem('span',`${page+1} / ${Math.ceil(rows.length/20)}`,bar);for(const [label,step] of [['上一组',-1],['下一组',1]]){const b=elem('button',label,bar);b.type='button';b.className='btn btn-outline-primary btn-sm';b.disabled=step<0?page===0:(page+1)*20>=rows.length;b.onclick=()=>{page+=step;render();};}}
  }
  panel.addEventListener('toggle',render);
  return {update(features){rows=features;page=0;summary.textContent=`查看地图数据（${rows.length.toLocaleString()} 个显示对象）`;render();}};
}
