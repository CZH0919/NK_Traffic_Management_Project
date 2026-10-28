import {elem} from './map_shared.js?v=20261028-map-ui4';
export const GRADES={lower:'相对较低',moderate:'中等',high:'较高',very_high:'很高',insufficient:'不分级'};
const COLORS={lower:'#4a7d96',moderate:'#b19a60',high:'#b97743',very_high:'#ad4d45',insufficient:'#8897a2'};
const NIGHT={lower:'#92b6cd',moderate:'#d4b875',high:'#dfa16d',very_high:'#e6887b',insufficient:'#a5b5c0'};
export const riskAdapter={
  kind:'risk',color:(night,grade)=>((night?NIGHT:COLORS)[grade]||COLORS.insufficient),
  colorExpression(night){return ['match',['get','grade'],...Object.entries(night?NIGHT:COLORS).flat(),COLORS.insufficient];},
  prepare:data=>data,
  describe(f){const p=f.properties;return `${p.label} · ${GRADES[p.grade]||'不分级'} · ${p.score===null?'无评分':Number(p.score)+' / 100'} · 支撑 ${Number(p.n).toLocaleString()} 起`;},
  popup(f,box){const p=f.properties;elem('strong',p.label,box);elem('small',`${GRADES[p.grade]||'不分级'} · ${p.score===null?'无评分':Number(p.score)+' / 100'}`,box);elem('small',`支撑 ${Number(p.n).toLocaleString()} 起事故；位置和分数来自该次历史结果。`,box);const a=elem('a','查看评分依据',box);a.href=`/risk/results/${p.id}/`;},
  status(data){return `当前视野 ${data.count.toLocaleString()} 个历史评价对象，显示 ${data.features.length.toLocaleString()} 个。${data.truncated?'已按分数截取前 500 个，请放大或查询列表。':''}${data.stale?'这是已修订数据之前的历史结果。':''}点位为对象代表位置。`;},
};
