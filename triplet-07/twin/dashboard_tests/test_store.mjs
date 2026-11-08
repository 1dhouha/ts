import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const file = new URL('../src/city_dashboard/web/store.js', import.meta.url);
try { await fs.access(file); } catch { assert.fail('D02 needs a run-aware browser store'); }
const {FrameStore} = await import(file);
const frame = (run, seq, extra={}) => ({schema_version:'twin-dashboard-operator-0.1-local',
  visibility:'PRIVATE_OPERATOR_ONLY',run_id:run,frame_seq:seq,sim_tick:seq,revision:seq,
  events:[],readings:[],...extra});
let now=0;
const store = new FrameStore(() => now);
let request = store.begin();
assert.equal(store.accept(frame('one',1),request),true);
assert.equal(store.stale,false);
store.selection='R-LOW';
assert.equal(store.accept(frame('one',0),request),false);
assert.equal(store.accept(frame('two',2),request),true);
assert.equal(store.selection,null);
assert.deepEqual(store.history,[]);
assert.equal(store.accept(frame('one',1),request),false);
const oldRequest=request;
request=store.begin();
assert.equal(store.accept(frame('old',20),oldRequest),false);
assert.equal(store.accept(frame('two',3),request),true);
now=1501;
assert.equal(store.stale,true);
store.fail();
assert.equal(store.stale,true);
assert.equal(store.accept(frame('two',4),request),true);
assert.equal(store.stale,false);
assert.equal(store.accept(frame('two',NaN),request),false);
assert.equal(store.accept(frame('two',5,{schema_version:'unknown'}),request),false);
assert.equal(store.accept(frame('two',5,{events:Array.from({length:700},(_,i)=>({seq:i}))}),request),true);
assert.equal(store.history.length,512);
store.clear();
assert.equal(store.frame,null);
assert.equal(store.selection,null);
assert.equal(store.stale,true);
assert.deepEqual(store.history,[]);
const report={schema_version:'twin-dashboard-report-0.1-local', visibility:'REPORTED_ONLY',
  run_id:'report-a',frame_seq:10,records:[{seq:1,reported_value:null}],next_after_seq:10,at_end:true};
request=store.begin();
assert.equal(store.accept(report,request),true);
assert.equal(store.accept({...report,run_id:'report-b',frame_seq:0,records:[]},request),true);
assert.deepEqual(store.history,[]);
console.log('browser store: run/reset, ordering, stale, schema, bounded history and null checks passed');
