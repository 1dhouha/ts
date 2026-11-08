import {lineSegments} from './history.js';
const el=id=>document.getElementById(id),NS='http://www.w3.org/2000/svg';
const names={'physical:water':'Physical surface depth / m','physical:queue':'Physical R-LOW queue / veh','physical:load':'Physical F-ESS load / kW','physical:air':'Physical Industrial PM2.5 / µg/m³'};
const traceMessages={
 RECEIVED:'Scenario started in Twin.',
 FAILED:'Twin could not complete scenario setup.',
 SENSOR_EVENT_SCHEDULED:'Scenario seeded a sensor input; see the sensor sample below for the reported value.',
 TWIN_UPDATED:'Twin recorded a sensor observation.',
 GUARDIAN_PENDING:'Twin marked this observation as awaiting Guardian review; this is not a Guardian response.',
 BRAIN_PENDING:'Twin marked this observation as awaiting a Brain command; this is not a Brain response.',
 BRAIN_DECIDED:'A Brain-role command was observed by Twin; the caller process is not independently attested.',
 COMMAND_PENDING:'Twin recorded the command as pending.',
 COMMAND_EXECUTED:'Twin applied the command to simulated state; this is not physical actuator feedback.',
 COMMAND_REJECTED:'Twin rejected the command.',
 ACTUATOR_COMMAND_REJECTED:'Twin rejected the actuator command.',
 GUARDIAN_REJECTED:'Twin recorded a Guardian-role isolation action; this is not positive authorization.',
 GUARDIAN_REVIEW:'Twin recorded a Guardian-role review or restoration action.',
 PARTNER_REQUEST_BLOCKED:'Twin blocked this request under its locally controlled outage flag; no partner network outage was tested.',
 PARTNER_CHANNEL_UNAVAILABLE:'A local scenario marked this partner channel unavailable; this is not a live connectivity check.',
 PARTNER_CHANNEL_RECOVERED:'A local scenario cleared its partner-channel outage flag; this is not a live recovery check.',
 PARTNER_CHANNEL_AVAILABLE:'Scenario marked this partner channel available locally; no external connection was checked.',
 SCENARIO_WINDOW_COMPLETE:'Twin finished the scenario window.'
};
export class Timeline{
 constructor(history,controls){this.history=history;this.controls=controls;this.signature='';this.activitySignature='';this.activityLayer='twin';this.frame=null;el('chart-series').addEventListener('change',()=>this.chart());for(const button of document.querySelectorAll('[data-activity-layer]'))button.addEventListener('click',()=>{this.activityLayer=button.dataset.activityLayer;for(const peer of document.querySelectorAll('[data-activity-layer]'))peer.setAttribute('aria-pressed',String(peer===button));this.activitySignature='';this.renderTrace(this.history.events,this.frame);});}
 render(frame){
  const h=this.history;this.frame=frame;el('history-notice').textContent=h.notice||'Retained local history only. No cross-layer Guardian verdicts or Brain decisions are fabricated.';this.renderTrace(h.events,frame);
  const key=`${h.run}:${h.visibility}:${h.publicSeq}:${h.eventSeq}:${frame?.sim_tick}:${frame?.revision}`;if(key===this.signature)return;this.signature=key;
  const select=el('chart-series'),selected=select.value;select.replaceChildren();
  for(const [id,points] of h.series){const option=document.createElement('option');option.value=id;option.textContent=names[id]||`${id} reported / ${points.at(-1)?.unit||''}`;select.append(option);}
  select.value=h.series.has(selected)?selected:(h.series.has('physical:water')?'physical:water':h.series.keys().next().value||'');this.chart();
  const list=el('timeline');list.replaceChildren();if(!h.events.length)list.textContent='No retained events.';
  for(const event of [...h.events].reverse()){const li=document.createElement('li');li.textContent=event.type&&event.details?`Private #${event.seq} · ${event.timestamp} · ${event.type} · ${JSON.stringify(event.details)}`:`Report #${event.seq} · sample ${event.timestamp} · delivered t=${event.delivery_tick}s · ${event.sensor_id} · ${event.reported_value??'unavailable'} ${event.unit} · ${event.status}`;list.append(li);}
  const actions=el('action-rows');actions.replaceChildren();el('pending-actions').hidden=frame?.visibility!=='PRIVATE_OPERATOR_ONLY';
  for(const action of (frame?.actions||[]).slice(-128)){const p=document.createElement('p');p.textContent=`${action.action_id} · ${action.kind} · due t=${action.due_tick}s · ${action.status}${action.error?' / '+action.error:''}`;actions.append(p);}
 }
 renderTrace(events,frame){
  const latest=el('activity-latest'),archive=el('activity-archive'),more=el('activity-more');
  const scenario=frame?.scenario;
  el('activity-scenario').textContent=frame?.visibility!=='PRIVATE_OPERATOR_ONLY'
   ?'Connect operator access to view private scenario activity'
   :scenario?`${scenario.scenario_id} · ${scenario.name} · ${scenario.status}`:'No scenario active · start one from Scenario campaign tools';
  const signature=`${frame?.run_id}:${frame?.visibility}:${scenario?.scenario_id}:${scenario?.status}:${this.history.eventSeq}:${this.history.events.length}:${this.activityLayer}`;
  if(signature===this.activitySignature)return;
  this.activitySignature=signature;
  latest.replaceChildren();archive.replaceChildren();
  if(frame?.visibility!=='PRIVATE_OPERATOR_ONLY'){
   const item=document.createElement('li');item.textContent='Detailed Brain, Guardian, and scenario actions are only available in the private operator view.';latest.append(item);more.hidden=true;return;
  }
  const relevant=(events||[]).filter(event=>{
   const details=event?.details||{};
   if(!traceMessages[details.event_type]&&!details.scenario_id)return false;
   return this.activityLayer==='all'||this.eventLayer(event)===this.activityLayer;
  });
  const ordered=relevant.slice(-512).reverse();
  if(!ordered.length){const item=document.createElement('li');item.textContent=this.activityLayer==='twin'?'No Twin events recorded for this scenario yet. Start a scenario to populate this timeline.':`No ${this.activityLayer==='all'?'layer':this.activityLayer} events recorded for this scenario yet.`;latest.append(item);more.hidden=true;return;}
  for(const [index,event] of ordered.entries()){
   const item=this.activityItem(event);
   (index<8?latest:archive).append(item);
  }
  more.hidden=ordered.length<=8;
  el('activity-more-label').textContent=`Show all ${ordered.length} recorded events`;
 }
 eventLayer(event){
  const details=event?.details||{},type=details.event_type||'',source=details.source||'';
  if(source==='brain'||type==='BRAIN_DECIDED'||type==='COMMAND_PENDING')return 'brain';
  if(source==='guardian'||type==='GUARDIAN_REJECTED'||type==='GUARDIAN_REVIEW')return 'guardian';
  return 'twin';
 }
 activityItem(event){
  const details=event.details||{},payload=details.payload||{},type=details.event_type||'Twin event';
  const item=document.createElement('li'),heading=document.createElement('strong');
  let title=traceMessages[type];
  if(type==='TWIN_UPDATED'&&payload.fault_type)title=`Twin injected ${payload.fault_type} sensor fault.`;
  else if(type==='TWIN_UPDATED'&&payload.fault_cleared)title='Twin cleared the sensor fault.';
  else if(type==='TWIN_UPDATED'&&payload.scenario_local_fixture)title='Scenario supplied a simulated sensor reading.';
  else if(type==='TWIN_UPDATED'&&(payload.reading||payload.reported_value!==undefined))title='Twin recorded a sensor sample.';
  else if(type==='BRAIN_DECIDED')title=`Brain command received: ${payload.command||'command not specified'}.`;
  else if(type==='COMMAND_EXECUTED')title='Twin applied the command to simulated city state.';
  else if(type==='COMMAND_REJECTED'||type==='ACTUATOR_COMMAND_REJECTED')title='Twin rejected the command; simulated effect was not applied.';
  else if(type==='GUARDIAN_REJECTED')title=`Guardian isolation action: ${payload.action||'ISOLATED'}.`;
  else if(type==='GUARDIAN_REVIEW')title=`Guardian review / restore action: ${payload.action||'REVIEW'}.`;
  else if(type==='RECEIVED'&&payload.name)title=`Scenario started: ${payload.name}.`;
  heading.textContent=title||type;item.append(heading);
  const context=[details.timestamp||event.timestamp,details.device_id&&`device ${details.device_id}`,details.source&&`source ${details.source}`].filter(Boolean);
  if(context.length){const meta=document.createElement('small');meta.textContent=context.join(' · ');item.append(document.createElement('br'),meta);}
  const reading=payload.reading||(
   payload.reported_value!==undefined?{reported_value:payload.reported_value,unit:payload.unit,status:payload.status}:null
  ), facts=[];
  if(reading)facts.push(`reported ${reading.reported_value??'unavailable'} ${reading.unit||''} · ${reading.status||'status unknown'}${reading.available===false?' · unavailable':''}`.trim());
  if(payload.fault_type)facts.push(`fault ${payload.fault_type}`);
  if(payload.action&&type!=='GUARDIAN_REJECTED'&&type!=='GUARDIAN_REVIEW')facts.push(`action ${payload.action}`);
  if(payload.error)facts.push(`reason ${typeof payload.error==='string'?payload.error:JSON.stringify(payload.error)}`);
  if(payload.reason)facts.push(`reason ${typeof payload.reason==='string'?payload.reason:JSON.stringify(payload.reason)}`);
  if(payload.partner)facts.push(`partner ${payload.partner}`);
  if(payload.effective_revision!==undefined)facts.push(`simulated revision ${payload.effective_revision}`);
  if(payload.request_id)facts.push(`request ${payload.request_id}`);
  if(facts.length){const detail=document.createElement('span');detail.textContent=` · ${facts.join(' · ')}`;item.append(detail);}
  return item;
 }
 chart(){
  const points=this.history.series.get(el('chart-series').value)||[];const svg=el('history-chart');svg.replaceChildren();const table=el('chart-data');table.replaceChildren();for(const p of points.slice(-64)){const tr=document.createElement('tr');for(const v of [p.sim_tick,p.reported_value??'unavailable',p.unit,p.seq??'physical frame']){const td=document.createElement('td');td.textContent=String(v);tr.append(td);}table.append(tr);}
  if(!points.length){el('chart-caption').textContent='No samples. Null values remain gaps.';return;}
  const finite=points.filter(p=>typeof p.reported_value==='number'&&Number.isFinite(p.reported_value));
  const minX=Math.min(...points.map(p=>p.sim_tick)),maxX=Math.max(minX+1,...points.map(p=>p.sim_tick));
  const minY=Math.min(0,...finite.map(p=>p.reported_value)),maxY=Math.max(minY+1,...finite.map(p=>p.reported_value));
  for(const segment of lineSegments(points)){const poly=document.createElementNS(NS,'polyline');poly.setAttribute('points',segment.map(p=>`${20+(p.sim_tick-minX)/(maxX-minX)*860},${140-(p.reported_value-minY)/(maxY-minY)*115}`).join(' '));poly.setAttribute('fill','none');poly.setAttribute('stroke','#7bd8eb');poly.setAttribute('stroke-width','2');svg.append(poly);for(const p of segment){const c=document.createElementNS(NS,'circle');c.setAttribute('cx',20+(p.sim_tick-minX)/(maxX-minX)*860);c.setAttribute('cy',140-(p.reported_value-minY)/(maxY-minY)*115);c.setAttribute('r',2);c.setAttribute('fill','#7bd8eb');svg.append(c);}}
  el('chart-caption').textContent=`${el('chart-series').selectedOptions[0]?.textContent} · t=${minX}–${maxX}s · range ${minY.toFixed(2)}–${maxY.toFixed(2)} · ${points.length} retained samples. X: simulated sample seconds. Null = gap. Physical series use latest observed committed frame per tick (≤4 polls/s), maximum1000 points; older points drop. Lines guide the eye, not extra measurements.`;
 }
}
