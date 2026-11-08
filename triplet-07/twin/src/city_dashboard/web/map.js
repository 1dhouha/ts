export function pointAlong(points,fraction){
 const lengths=points.slice(1).map((p,i)=>Math.hypot(p[0]-points[i][0],p[1]-points[i][1]));
 let distance=lengths.reduce((a,b)=>a+b,0)*Math.max(0,Math.min(1,fraction));
 for(let i=0;i<lengths.length;i++){if(distance<=lengths[i]){const t=lengths[i]?distance/lengths[i]:0;return [points[i][0]+(points[i+1][0]-points[i][0])*t,points[i][1]+(points[i+1][1]-points[i][1])*t];}distance-=lengths[i];}
 return points.at(-1);
}
const NS='http://www.w3.org/2000/svg';
const svgNode=(tag,attrs)=>{const n=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);return n;};
export class CityMap{
 constructor(container,onSelect){this.container=container;this.onSelect=onSelect;this.layers=new Map();this.selected=null;this.tracks=new Map();this.run=null;this.box=[0,0,1536,1024];this.ready=this.init();}
 async init(){
  const [layout,source]=await Promise.all([fetch('/map-layout.json').then(r=>r.json()),fetch('/city-map.svg').then(r=>r.text())]);
  const port={id:'PRT',x:1320,y:850,district:'PRT',selectable:true};
  this.baseDevices=layout.devices.filter(d=>!d.id.startsWith('S-'));
  this.layout={...layout,anchors:[...layout.anchors,port],devices:[...this.baseDevices]};this.anchors=new Map(this.layout.anchors.map(a=>[a.id,a]));
  this.svg=new DOMParser().parseFromString(source,'image/svg+xml').documentElement;
  this.svg.setAttribute('aria-label','Convergence City interactive map');this.svg.removeAttribute('aria-labelledby');
  this.svg.querySelector('title').textContent='Convergence City';this.svg.querySelector('desc').textContent='Illustrative map with selectable catalog assets. Overlays show committed operator state only.';
  this.svg.setAttribute('aria-describedby','map-help');this.svg.setAttribute('role','group');this.svg.setAttribute('tabindex','0');this.container.replaceChildren(this.svg);
  for(const name of ['Traffic','Water','Power','Air','Emergency','Sensor']){const g=svgNode('g',{'data-overlay':name});this.layers.set(name,g);this.svg.append(g);}
  this.roads=new Map();this.pulses=new Map();for(const road of layout.roads){const p=svgNode('polyline',{'data-road':road.id,points:road.points.map(p=>p.join(',')).join(' '),fill:'none',stroke:'#7bd8eb','stroke-width':9,'stroke-linecap':'round','pointer-events':'none'});this.layers.get('Traffic').append(p);this.roads.set(road.id,p);const pulse=svgNode('polyline',{points:road.points.map(p=>p.join(',')).join(' '),class:'traffic-pulse',fill:'none',stroke:'#ffffff','stroke-width':4,'stroke-dasharray':'2 40','pointer-events':'none'});this.layers.get('Traffic').append(pulse);this.pulses.set(road.id,pulse);}
  this.water=svgNode('ellipse',{cx:900,cy:810,rx:170,ry:68,fill:'#45b8ee',opacity:0,'pointer-events':'none'});this.layers.get('Water').append(this.water);
  this.powerMarkers=new Map();for(const id of ['F-ESS','F-URBAN','F-IND']){const a=this.anchors.get(id);const p=svgNode('circle',{cx:a.x,cy:a.y,r:23,fill:'none',stroke:'#7bd8eb','stroke-width':5,'pointer-events':'none'});this.layers.get('Power').append(p);this.powerMarkers.set(id,p);}
  this.airMarkers=new Map();for(const a of layout.anchors.filter(a=>a.id.startsWith('Z-'))){const p=svgNode('circle',{cx:a.x,cy:a.y,r:48,fill:'#d2ad67',opacity:0,'pointer-events':'none'});this.layers.get('Air').append(p);this.airMarkers.set(a.id,p);}
  this.sensorMarkers=[];
  const portLabel=svgNode('text',{x:1190,y:910,fill:'#ffd38a','font-size':17});portLabel.textContent='Port / separate schematic model';this.layers.get('Sensor').append(portLabel);this.portLabel=portLabel;
  this.responderMarkers=new Map();this.responderRoutes=new Map();this.responderLabels=new Map();for(const id of ['AMB-01','AMB-02','POL-01']){const a=this.anchors.get(id);const p=svgNode('circle',{cx:a.x,cy:a.y,r:9,fill:'#bca6ff',stroke:'#fff','stroke-width':2,'pointer-events':'none'});this.layers.get('Emergency').append(p);this.responderMarkers.set(id,p);p.setAttribute('data-responder',id);
   const route=svgNode('polyline',{fill:'none',stroke:'#c9b3ff','stroke-width':4,'stroke-dasharray':'8 6','pointer-events':'none'});const label=svgNode('text',{fill:'#e6d8ff','font-size':17,'paint-order':'stroke',stroke:'#071723','stroke-width':4,'pointer-events':'none'});this.layers.get('Emergency').append(route,label);this.responderRoutes.set(id,route);this.responderLabels.set(id,label);
  }
  this.markers=new Map();for(const a of this.layout.anchors){const g=svgNode('g',{role:'button',tabindex:0,'aria-label':a.id,'aria-pressed':'false',transform:`translate(${a.x} ${a.y})`});g.append(svgNode('circle',{r:13,fill:'#102e41',stroke:'#8bd8e8','stroke-width':3}));const title=svgNode('title',{});title.textContent=a.id;g.append(title,svgNode('circle',{r:28,fill:'transparent',stroke:'transparent','stroke-width':14,'vector-effect':'non-scaling-stroke',class:'hit-target'}));g.addEventListener('click',()=>this.onSelect(a.id));g.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();this.onSelect(a.id);}});this.svg.append(g);this.markers.set(a.id,g);}
  this.svg.addEventListener('keydown',e=>{if(e.target!==this.svg)return;const amount=this.box[2]*.05;if(e.key.startsWith('Arrow')){e.preventDefault();if(e.key==='ArrowLeft')this.box[0]-=amount;if(e.key==='ArrowRight')this.box[0]+=amount;if(e.key==='ArrowUp')this.box[1]-=amount;if(e.key==='ArrowDown')this.box[1]+=amount;this.apply();}if(['+','=','-','0'].includes(e.key)){e.preventDefault();e.key==='0'?this.fitCity():this.zoom(e.key==='-'?1.25:.8);}});
  this.svg.addEventListener('wheel',e=>{e.preventDefault();this.zoom(e.deltaY<0?.85:1.18);},{passive:false});
  let drag=null;this.svg.addEventListener('pointerdown',e=>{if(e.target.closest('[role=button]'))return;this.svg.focus();drag={x:e.clientX,y:e.clientY,box:[...this.box]};this.svg.setPointerCapture(e.pointerId);});
  this.svg.addEventListener('pointermove',e=>{if(!drag)return;const r=this.svg.getBoundingClientRect();const scale=Math.min(r.width/drag.box[2],r.height/drag.box[3]);this.box=[drag.box[0]-(e.clientX-drag.x)/scale,drag.box[1]-(e.clientY-drag.y)/scale,...drag.box.slice(2)];this.apply();});
  this.svg.addEventListener('pointerup',()=>drag=null);this.svg.addEventListener('pointercancel',()=>drag=null);
 }
 apply(){this.svg?.setAttribute('viewBox',this.box.join(' '));}
 zoom(factor){const [x,y,w,h]=this.box;const width=Math.max(384,Math.min(3072,w*factor));this.box=[x+(w-width)/2,y+(h-width*2/3)/2,width,width*2/3];this.apply();}
 fitCity(){this.box=[0,0,1536,1024];this.apply();}
 setLayer(name,visible){const g=this.layers.get(name);if(g)g.style.display=visible?'':'none';}
 selectEntity(id){this.selected=id;const target=this.layout?.devices.find(d=>d.id===id)?.target_id||id;for(const [key,g] of this.markers)g.setAttribute('aria-pressed',String(key===target));}
 renderFrame(frame,{stale=false}={}){
  if(!this.svg)return;const d=frame?.visibility==='PRIVATE_OPERATOR_ONLY'?frame.diagnostics:null;
  this.renderSensors(frame);
  if(this.run!==frame?.run_id||!d){this.tracks.clear();this.run=frame?.run_id;}
  this.layers.get('Traffic').classList.toggle('flowing',!!d&&!stale&&frame.status.state==='RUNNING');
  for(const [id,p] of this.pulses){const usable=d?.traffic[id].usable&&d?.traffic[id].capacity_veh_s>0;p.setAttribute('visibility',usable?'visible':'hidden');p.style.animationDuration=`${Math.max(.25,3/((frame?.status?.speed||1)*Math.max(.2,d?.traffic[id].capacity_veh_s||.5)))}s`;}
  for(const [id,p] of this.roads){p.setAttribute('stroke',d?(d.traffic[id].usable?'#72d7d7':'#ff8b8b'):'#456273');p.setAttribute('stroke-dasharray',d&&!d.traffic[id].usable?'12 9':'none');}
  for(const [id,p] of this.airMarkers)p.setAttribute('opacity',d?Math.min(.6,(d.physical_state.air[id]?.pm25_ug_m3||0)/150):0);
  for(const [id,p] of this.responderMarkers){
   const unit=d?.physical_state.responders[id],a=this.anchors.get(unit?.at_node_id)||this.anchors.get(id);let point=[a.x,a.y];const line=this.responderRoutes.get(id),label=this.responderLabels.get(id);line.setAttribute('points','');label.textContent='';
   if(unit&&['enroute','blocked'].includes(unit.status)){
    const road=this.layout.roads.find(r=>r.id===unit.route_road_ids[unit.edge_index]);
    if(road){const key=`${frame.run_id}:${unit.mission_ref}:${unit.edge_index}:${unit.at_node_id}`;let track=this.tracks.get(id);if(track?.key!==key||unit.edge_remaining_s>track.initial){track={key,initial:Math.max(unit.edge_remaining_s,1e-9)};this.tracks.set(id,track);}
     const points=road.endpoints[0]===unit.at_node_id?road.points:[...road.points].reverse();const fraction=unit.status==='blocked'&&unit.edge_remaining_s===0?0:1-unit.edge_remaining_s/track.initial;point=pointAlong(points,fraction);line.setAttribute('points',points.map(p=>p.join(',')).join(' '));label.textContent=`${id} ${unit.status} / ${road.id} / ${unit.edge_remaining_s.toFixed(1)}s left`;
    }
   }else this.tracks.delete(id);
   p.setAttribute('visibility',unit?'visible':'hidden');p.setAttribute('cx',point[0]);p.setAttribute('cy',point[1]);label.setAttribute('x',point[0]+16);label.setAttribute('y',point[1]-24);
  }
  this.water.setAttribute('opacity',d?Math.min(.7,Math.max(0,d.flood['R-LOW'].depth_m*2)):0);
  for(const [id,p] of this.powerMarkers){const power=d?.power.feeders[id];p.setAttribute('stroke',power?(power.energized?'#72dfae':'#ffbd6c'):'#456273');p.setAttribute('visibility',power?'visible':'hidden');}
 }
 renderSensors(frame){
  // Observation history includes retired devices. Only an operator registry
  // can identify active sensors; the reported view keeps its historical feed.
  const sensors=frame?.visibility==='PRIVATE_OPERATOR_ONLY'?frame.sensors.filter(s=>s.active):[];
  const zones={DTN:'Z-DOWNTOWN',IND:'Z-INDUSTRIAL',RES:'Z-RESIDENTIAL',PRT:'PRT'};
  const devices=sensors.map(s=>({id:s.sensor_id,family:s.type,location_id:s.zone_id,target_id:zones[s.zone_id]}));
  this.layout={...this.layout,devices:[...this.baseDevices,...devices]};
  const identity=devices.map(d=>d.id).join('|');if(identity===this.sensorIdentity)return;this.sensorIdentity=identity;
  for(const node of this.sensorMarkers)node.remove();this.sensorMarkers=[];
  const counts={};for(const device of devices){const a=this.anchors.get(device.target_id);if(!a)continue;const index=counts[device.location_id]||0;counts[device.location_id]=index+1;
   const p=svgNode('circle',{cx:a.x+20+(index%2)*18,cy:a.y-22-Math.floor(index/2)*18,r:6,fill:'#ffd38a','data-sensor':device.id,'pointer-events':'none'});const title=svgNode('title',{});title.textContent=device.id;p.append(title);this.layers.get('Sensor').append(p);this.sensorMarkers.push(p);
  }
 }
}
