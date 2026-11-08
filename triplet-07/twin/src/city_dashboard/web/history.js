// Bounded history of delivered samples and observed committed physical frames.
export function lineSegments(records){
 const result=[];let segment=[];
 for(const r of [...records].sort((a,b)=>a.sim_tick-b.sim_tick||(a.seq||0)-(b.seq||0))){
  if(typeof r.reported_value!=='number'||!Number.isFinite(r.reported_value)){if(segment.length)result.push(segment);segment=[];}
  else segment.push(r);
 }
 if(segment.length)result.push(segment);return result;
}
export class DashboardHistory{
 constructor(){this.clear();}
 clear(){this.run=null;this.visibility=null;this.series=new Map();this.events=[];this.publicSeq=0;this.eventSeq=0;this.notice='';this.lastTick=null;}
 accept(frame){
  if(!frame){this.clear();return;}
  if(frame.run_id!==this.run||frame.visibility!==this.visibility){this.clear();this.run=frame.run_id;this.visibility=frame.visibility;}
  const operator=frame.visibility==='PRIVATE_OPERATOR_ONLY';const records=operator?frame.readings:frame.records;
  const first=records?.[0]?.seq||frame.first_retained_seq||1;
  if(first>this.publicSeq+1){this.series.clear();this.events=[];this.notice=`HISTORY_GAP: earlier observations unavailable; resynced from delivery ${first}.`;this.publicSeq=first-1;}
  for(const r of records||[]){if(r.seq<=this.publicSeq)continue;const list=this.series.get(r.sensor_id)||[];this.series.set(r.sensor_id,[...list,r].slice(-1000));if(!operator)this.events=[...this.events,r].slice(-512);this.publicSeq=r.seq;}
  if(operator){
   const events=frame.events||[];if(events.length&&events[0].seq>this.eventSeq+1)this.notice=`HISTORY_GAP: private event retention begins at ${events[0].seq}.`;
   for(const e of events)if(e.seq>this.eventSeq){this.events=[...this.events,e].slice(-512);this.eventSeq=e.seq;}
   const d=frame.diagnostics,p=d?.physical_state;
   if(p){const rows=[['physical:water',d.water_levels?.surface_m,'m'],['physical:queue',p.roads['R-LOW'].queue_vehicles,'veh'],['physical:load',d.power.feeders['F-ESS'].delivered_load_kw,'kW'],['physical:air',p.air['Z-INDUSTRIAL'].pm25_ug_m3,'µg/m³']];
    for(const [key,value,unit] of rows){const list=this.series.get(key)||[];const point={sim_tick:frame.sim_tick,reported_value:value,unit};this.series.set(key,[...(list.at(-1)?.sim_tick===frame.sim_tick?list.slice(0,-1):list),point].slice(-1000));}
    this.lastTick=frame.sim_tick;
   }
  }
  while(this.series.size>64)this.series.delete(this.series.keys().next().value);
 }
}
