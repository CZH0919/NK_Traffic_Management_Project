import {elem,shortCount} from './map_shared.js?v=20261028-unified3';
export const statisticsAdapter={
  kind:'statistics',color:night=>night?'#67bfbf':'#327d8c',
  prepare(data){return {...data,features:data.features.map(f=>({...f,properties:{...f.properties,aggregate:data.mode==='aggregate',short_count:shortCount(f.properties.n)}}))};},
  describe(f){return f.properties.aggregate?`${Number(f.properties.n).toLocaleString()} 起事故（聚合）`:`事故 #${f.properties.id} · ${f.properties.label||'街道未提供'}`;},
  popup(f,box,zoom){const p=f.properties;if(p.aggregate){elem('strong',`${Number(p.n).toLocaleString()} 起事故`,box);elem('small','当前网格内的事故数量，不是风险评分。',box);const b=elem('button','放大此处',box);b.type='button';b.className='btn btn-outline-primary btn-sm';b.onclick=zoom;}else{elem('strong',p.label||`事故 #${p.id}`,box);elem('small','属于已应用统计条件的有坐标事故。',box);const a=elem('a','查看事故档案',box);a.href=`/records/crashes/${p.id}/`;}} ,
  status(data){return `当前视野 ${data.count.toLocaleString()} 起有坐标事故，显示 ${data.features.length.toLocaleString()} 个${data.mode==='aggregate'?'聚合单元':'事故点'}。${data.mode==='aggregate'?'数字表示事故数，不是风险评分。k 表示千。':''}${data.truncated?'已达到 1,000 个显示对象上限，请放大查看其余记录。':''}`;},
};
