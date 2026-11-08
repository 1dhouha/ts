import {requestJSON} from './api.js';
const el=id=>document.getElementById(id);
export class OperatorControls {
 constructor(store,allowed){this.store=store;this.allowed=allowed;this.busy=false;this.intent=0;this.lastMode=null;this.lastRun=null;this.retry=null;this.install();}
 install(){
  for(const [id,kind] of [['start','start'],['pause','pause'],['stop','stop'],['step','step']])el(id).addEventListener('click',()=>this.send(kind,{}));
  el('device-id').addEventListener('change',()=>{const id=el('device-id').value;el('device-value').value=id==='A-VALVE-01'?'1':id.startsWith('A-SIG')?'"normal"':'true';});
  el('device-apply').addEventListener('click',()=>{try{this.send('device',{device_id:el('device-id').value,requested:JSON.parse(el('device-value').value)});}catch{el('command-result').textContent='Invalid JSON device value. No request sent.';}});
  el('set-speed').addEventListener('click',()=>this.send('speed',{speed:Number(el('speed').value)}));
  el('reset-city').addEventListener('click',()=>{if(confirm('Reset the whole local city? Pending actions and histories will be cleared.'))this.send('reset',{new_run_id:'dashboard-'+crypto.randomUUID()});});
  el('set-traffic').addEventListener('click',()=>this.send('traffic',{name:el('traffic-profile').value}));
  el('apply-incident').addEventListener('click',()=>{
   const operation=el('incident-kind').value;
   const params=['accident','partial_block','road_clear'].includes(operation)?{target:el('road-target').value}:{};
   this.send('incident',{operation,params});
  });
  el('preset-go').addEventListener('click',()=>this.send('preset',{name:el('preset').value,delay:0}));
  el('scenario-start').addEventListener('click',()=>this.startScenario());
  el('advanced-command').addEventListener('submit',event=>{event.preventDefault();try{this.send(el('command-kind').value,JSON.parse(el('command-args').value));}catch{el('command-result').textContent='Invalid JSON arguments. No command sent.';}});
  el('retry-command').addEventListener('click',()=>{if(this.retry)this.deliver(this.retry);});
 }
 render(){
  const f=this.store.frame;const operator=f?.visibility==='PRIVATE_OPERATOR_ONLY'&&this.allowed();
  const mode=operator?'operator':'report';
  if(this.lastMode!==mode||this.lastRun!==f?.run_id){this.intent++;this.retry=null;el('advanced-command').reset();el('device-id').selectedIndex=0;el('device-value').value='1';for(const id of ['speed','traffic-profile','incident-kind','road-target','preset'])el(id).selectedIndex=id==='speed'?1:0;el('command-result').textContent='No command submitted.';this.lastMode=mode;this.lastRun=f?.run_id;}
  el('operator-controls').hidden=!operator;
  const usable=operator&&!this.store.stale&&!this.busy;
  for(const item of el('operator-controls').querySelectorAll('button,input,select,textarea'))item.disabled=!usable;
  const state=f?.status?.state;
  for(const id of ['step','reset-city','set-traffic','apply-incident','preset-go','advanced-submit','device-apply']){
   const reset=id==='reset-city';el(id).disabled=!usable||!(reset?['READY','PAUSED','STOPPED','ERROR','BUDGET_EXHAUSTED']:['READY','PAUSED','STOPPED']).includes(state);
  }
  const activeScenario=f?.scenario?.status==='RUNNING';
  el('scenario-start').disabled=!usable||activeScenario||!['READY','RUNNING','PAUSED','STOPPED'].includes(state);
  el('scenario-status').textContent=f?.scenario
   ? `${f.scenario.scenario_id} · ${f.scenario.status} · correlation ${f.scenario.correlation_id} · Guardian policy ${f.scenario.guardian_policy}`
   : 'No scenario started.';
  el('retry-command').disabled=!usable||!this.retry;el('retry-command').hidden=!this.retry;
 }
 async startScenario(){
  if(!this.allowed()||this.store.stale||this.busy||this.store.frame?.visibility!=='PRIVATE_OPERATOR_ONLY')return;
  this.busy=true;this.render();el('scenario-status').textContent='Scenario start pending.';
  try{
   const result=await requestJSON('/api/operator/scenario',{scenario_id:el('scenario-select').value});
   if(this.store.frame)this.store.frame={...this.store.frame,scenario:result};
   el('scenario-status').textContent=`${result.scenario_id} · ${result.status} · correlation ${result.correlation_id}. Brain/Guardian must use the existing inbound contracts; no positive Guardian approval is available.`;
  }catch(failure){
   el('scenario-status').textContent=`Scenario start failed: ${failure.message}. No retry was sent.`;
  }finally{this.busy=false;this.render();}
 }
 send(kind,args){
  if(!this.allowed()||this.store.stale||this.busy||this.store.frame?.visibility!=='PRIVATE_OPERATOR_ONLY')return;
  const f=this.store.frame;
  const envelope={command_id:'ui-'+crypto.randomUUID(),run_id:f.run_id,expected_revision:['start','pause','resume','stop','speed'].includes(kind)?null:f.revision,kind,args};
  this.deliver(envelope);
 }
 async deliver(envelope){
  if(!this.allowed()||this.store.stale||this.busy||this.store.frame?.run_id!==envelope.run_id)return;
  const ticket=this.intent;this.busy=true;this.retry=null;el('command-result').textContent='PENDING / waiting for acknowledgement';this.render();
  try{
   const result=await requestJSON('/api/operator/command',envelope);
   if(ticket!==this.intent)return;
   el('command-result').textContent=`${result.status} · ${envelope.kind}${envelope.kind==='device'?` · requested ${JSON.stringify(result.result.requested)} / effective ${JSON.stringify(result.result.effective)}${result.result.error?' / '+result.result.error:''}`:' · check current state and timeline for physical consequences.'}`;
  }catch(failure){
   if(ticket!==this.intent)return;
   const code=failure.message;
   el('command-result').textContent=code==='STATE_CONFLICT'?'STATE_CONFLICT: Pause city → wait for refreshed state → explicitly submit again. No automatic physical retry.':`Command failed or outcome unknown: ${code}. Retry delivery uses the same command ID.`;
   if(!/^[A-Z_]+$/.test(code)||['INVALID_RESPONSE','RATE_LIMITED'].includes(code))this.retry=envelope;
  }finally{this.busy=false;this.render();}
 }
}
