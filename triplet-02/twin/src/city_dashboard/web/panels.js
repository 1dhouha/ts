const el=id=>document.getElementById(id);
const fmt=(value,unit='')=>typeof value==='number'&&Number.isFinite(value)?`${value.toFixed(1)} ${unit}`:'— / unavailable';
function metric(name,value){const row=document.createElement('div');row.className='metric';const label=document.createElement('span'),b=document.createElement('b');label.textContent=name;b.textContent=value;row.append(label,b);return row;}
export function renderMetrics(frame){
 const box=el('metrics');box.replaceChildren();const d=frame?.visibility==='PRIVATE_OPERATOR_ONLY'?frame.diagnostics:null;
 if(!d){box.textContent='Physical state requires operator access.';return;}
 const model=d.sensor_model;
 const rows=[...(model?[['Harbour level',fmt(model.harbour_volume_m3/10,'cm')],['Process temperature',fmt(model.process_c,'°C')],['Storm drain level',fmt(model.drain_volume_m3,'cm')],['Reservoir level',fmt(model.reservoir_volume_m3,'cm')]]:[]),['Basin level',fmt(d.water_levels.basin_m,'m')],['Basin volume',fmt(d.physical_state.water.basin_volume_m3,'m³')],['Surface volume',fmt(d.physical_state.water.surface_volume_m3,'m³')],['R-LOW depth',fmt(d.flood['R-LOW'].depth_m,'m')],['Rain',fmt(d.physical_state.water.rain_mm_h,'mm/h')],['Pump',d.power.pump_running?'RUNNING':'STOPPED'],['Hospital',d.power.hospital_powered?'POWERED':'NO POWER']];
 for(const [id,r] of Object.entries(d.physical_state.roads))rows.push([`${id} queue`,fmt(r.queue_vehicles,'veh')],[`${id} capacity`,fmt(d.traffic[id].capacity_veh_s,'veh/s')]);
 for(const [id,r] of Object.entries(d.power.feeders))rows.push([id,fmt(r.delivered_load_kw,'kW')]);
 for(const [id,r] of Object.entries(d.physical_state.air))rows.push([id.replace('Z-',''),fmt(r.pm25_ug_m3,'µg/m³')]);
 rows.push(['Hospital access',Object.values(d.access.reachable_by_origin).every(Boolean)?'ALL ORIGINS':'LIMITED']);
 for(const [id,r] of Object.entries(d.physical_state.responders))rows.push([id,`${r.status} / ${r.at_node_id||'route'}`]);
 box.append(...rows.map(([a,b])=>metric(a,b)));
}
export function renderReports(frame){
 const records=frame?.visibility==='PRIVATE_OPERATOR_ONLY'?frame.readings:frame?.records;
 const latest=new Map();for(const r of records||[])latest.set(r.sensor_id,r);
 const box=el('reports');box.replaceChildren();if(!latest.size){box.textContent='No samples.';return;}
 for(const r of latest.values()){const row=document.createElement('div');row.className='reading';const b=document.createElement('b');b.textContent=r.sensor_id;const p=document.createElement('p');p.textContent=`${fmt(r.reported_value,r.unit)} · ${r.status} · sample ${r.timestamp} · delivery t=${r.delivery_tick}s`;row.append(b,p);box.append(row);}
}
export function describeSelection(frame,id,layout){
 const box=el('selected');box.replaceChildren();if(!id){box.textContent='Select an asset on the map or in the list.';return;}
 const a=layout?.anchors.find(a=>a.id===id),device=layout?.devices.find(d=>d.id===id);const title=document.createElement('h3');title.textContent=id;box.append(title);
 const p=document.createElement('p');p.textContent=a?`District ${a.district} · illustrative anchor`:device?`${device.family} → ${device.target_id}`:'Unknown asset';box.append(p);
 const d=frame?.visibility==='PRIVATE_OPERATOR_ONLY'?frame.diagnostics:null;const target=device?.target_id||id;
 const value=d&&((d.physical_state.roads[target]?{...d.physical_state.roads[target],...d.traffic[target]}:null)||d.power.feeders[target]||d.physical_state.responders[target]||d.physical_state.air[target]||(target==='HOSP-01'?{powered:d.power.hospital_powered,access:d.access}:null));
 if(value){const pre=document.createElement('pre');pre.textContent=JSON.stringify(value,null,2);box.append(pre);}
}
