import {lineSegments} from './history.js';
const el=id=>document.getElementById(id),NS='http://www.w3.org/2000/svg';
const names={'physical:water':'Physical surface depth / m','physical:queue':'Physical R-LOW queue / veh','physical:load':'Physical F-ESS load / kW','physical:air':'Physical Industrial PM2.5 / µg/m³'};
export class Timeline{
 constructor(history,controls){this.history=history;this.controls=controls;this.signature='';el('chart-series').addEventListener('change',()=>this.chart());}
 render(frame){
  const h=this.history;el('history-notice').textContent=h.notice||'Retained local history only. No cross-layer Guardian verdicts or Brain decisions are fabricated.';
  const key=`${h.run}:${h.visibility}:${h.publicSeq}:${h.eventSeq}:${frame?.sim_tick}:${frame?.revision}`;if(key===this.signature)return;this.signature=key;
  const select=el('chart-series'),selected=select.value;select.replaceChildren();
  for(const [id,points] of h.series){const option=document.createElement('option');option.value=id;option.textContent=names[id]||`${id} reported / ${points.at(-1)?.unit||''}`;select.append(option);}
  select.value=h.series.has(selected)?selected:(h.series.has('physical:water')?'physical:water':h.series.keys().next().value||'');this.chart();
  const list=el('timeline');list.replaceChildren();if(!h.events.length)list.textContent='No retained events.';
  for(const event of [...h.events].reverse()){const li=document.createElement('li');li.textContent=event.type&&event.details?`Private #${event.seq} · ${event.timestamp} · ${event.type} · ${JSON.stringify(event.details)}`:`Report #${event.seq} · sample ${event.timestamp} · delivered t=${event.delivery_tick}s · ${event.sensor_id} · ${event.reported_value??'unavailable'} ${event.unit} · ${event.status}`;list.append(li);}
  const actions=el('action-rows');actions.replaceChildren();el('pending-actions').hidden=frame?.visibility!=='PRIVATE_OPERATOR_ONLY';
  for(const action of (frame?.actions||[]).slice(-128)){const p=document.createElement('p');p.textContent=`${action.action_id} · ${action.kind} · due t=${action.due_tick}s · ${action.status}${action.error?' / '+action.error:''}`;actions.append(p);}
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
