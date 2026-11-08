const el=id=>document.getElementById(id);
const defaults={FALSE_READING:{value:180},STUCK:{},DRIFT:{rate:1},NOISE:{amplitude:2},DELAYED:{lag_ticks:5},DROPOUT:{duration_ticks:10},DEAD:{}};
export class SensorWorkspace{
 constructor(store,controls){this.store=store;this.controls=controls;this.registry='';this.run=null;
  el('fault-type').addEventListener('change',()=>{el('fault-params').value=JSON.stringify(defaults[el('fault-type').value]);});
  el('sensor-select').addEventListener('change',()=>this.render());
  el('inject-fault').addEventListener('click',()=>this.inject(false));el('schedule-fault').addEventListener('click',()=>this.inject(true));
  for(const kind of ['clear','repair'])el('sensor-'+kind).addEventListener('click',()=>controls.send('sensor_'+kind,{sensor_id:el('sensor-select').value}));
  el('sensor-replace').addEventListener('click',()=>controls.send('sensor_replace',{sensor_id:el('sensor-select').value,new_id:el('replacement-id').value}));
 }
 inject(schedule){
  let parameters;try{parameters=JSON.parse(el('fault-params').value);}catch{el('sensor-detail').textContent='Invalid fault parameter JSON. No request sent.';return;}
  const params={sensor_id:el('sensor-select').value,fault_type:el('fault-type').value,parameters};
  if(schedule)this.controls.send('schedule',{action_id:'sensor-'+crypto.randomUUID(),due_tick:Number(el('fault-due').value),operation:'sensor_fault',params});
  else this.controls.send('sensor_fault',params);
 }
 render(){
  const f=this.store.frame,operator=f?.visibility==='PRIVATE_OPERATOR_ONLY';
  if(!operator||this.run!==f.run_id){this.registry='';el('sensor-select').replaceChildren();el('fault-type').value='FALSE_READING';el('fault-params').value=JSON.stringify(defaults.FALSE_READING);el('fault-due').value='20';el('replacement-id').value='';this.run=operator?f.run_id:null;}
  el('sensor-workspace').hidden=!operator;
  if(!operator){el('sensor-command-state').textContent='';el('replacement-id').value='';el('fault-params').value=JSON.stringify(defaults.FALSE_READING);el('sensor-detail').textContent='';el('registry-summary').textContent='';el('sensor-select').replaceChildren();this.registry='';return;}
  const identity=JSON.stringify(f.sensors);if(identity!==this.registry){const selected=el('sensor-select').value;el('sensor-select').replaceChildren();for(const s of f.sensors){const option=document.createElement('option');option.value=s.sensor_id;option.textContent=`${s.sensor_id} · ${s.active?'working source':'retired'}`;el('sensor-select').append(option);}el('sensor-select').value=f.sensors.some(s=>s.sensor_id===selected)?selected:(f.sensors.find(s=>s.active&&s.sensor_id==='DTN-WLV-01')||f.sensors.find(s=>s.active)).sensor_id;this.registry=identity;}
  const active=f.sensors.filter(s=>s.active);el('registry-summary').textContent=`${active.length} active PDF sensors · ${active.filter(s=>s.source).length} working sources. Legacy sensors disabled. Backups sample independently; no automatic switching. Port marker is schematic; its water storage is separate from River.`;
  const s=f.sensors.find(s=>s.sensor_id===el('sensor-select').value),r=f.comparisons?.find(r=>r.sensor_id===s?.sensor_id);
  if(!s)return;
  const data={sensor_id:s.sensor_id,state:s.active?(s.source?'BOUND':'UNSUPPORTED_SOURCE'):'RETIRED',source:s.source,interval_seconds:s.interval,hard_limits:s.hard_limits,normal_limits:s.normal_limits,backups:s.backup_ids,replaces:s.replaces,registration_run:s.registered_run_id,reading:r||'NO_SAMPLE',active_fault:f.diagnostics.active_faults[s.sensor_id]||null};
  el('sensor-detail').textContent=JSON.stringify(data,null,2);
  const enabled=s.active&&!!s.source&&!this.store.stale&&!this.controls.busy&&['READY','PAUSED','STOPPED'].includes(f.status.state)&&this.controls.allowed();
  for(const button of el('sensor-workspace').querySelectorAll('button'))button.disabled=!enabled;
  el('sensor-command-state').textContent=el('command-result').textContent;
 }
}
