import test from 'node:test';
import assert from 'node:assert/strict';
import {latestRequest,parseBounds} from '../../static/js/map_shared.js';

const tick=()=>new Promise(resolve=>setTimeout(resolve,8));
test('moving retains one request and applies only the newest view',async()=>{
  const original=global.fetch,pending=[],applied=[],errors=[];
  global.fetch=(url)=>new Promise(resolve=>pending.push({url,resolve}));
  const queue=latestRequest({delay:0,apply:x=>applied.push(x),error:x=>errors.push(x)});
  try{
    queue.request('view-a');await tick();queue.request('view-b');queue.request('view-c');await tick();
    assert.equal(pending.length,1);
    pending[0].resolve({ok:true,json:async()=>({view:'a'})});await tick();
    assert.equal(pending.length,2);assert.equal(pending[1].url,'view-c');assert.deepEqual(applied,[]);
    pending[1].resolve({ok:true,json:async()=>({view:'c'})});await tick();
    assert.deepEqual(applied,[{view:'c'}]);queue.request('view-c');await tick();assert.equal(pending.length,2);
    queue.pause();queue.request('view-d');await tick();assert.equal(pending.length,2);
    queue.resume();queue.request('view-d');await tick();queue.pause();
    pending[2].resolve({ok:true,json:async()=>({view:'d'})});await tick();assert.equal(applied.length,1);
    queue.resume();queue.request('view-e');await tick();queue.destroy();
    pending[3].resolve({ok:true,json:async()=>({view:'e'})});await tick();assert.equal(applied.length,1);assert.deepEqual(errors,[]);
  }finally{queue.destroy();global.fetch=original;}
});
test('bounds accept valid coordinates and reject invalid display state',()=>{
  assert.deepEqual(parseBounds('-74,40,-73,41'),[[-74,40],[-73,41]]);
  for(const value of ['',null,'NaN,40,-73,41','-74,40,-74,41','-74,41,-73,40','-200,40,-73,41'])assert.equal(parseBounds(value),null);
});
