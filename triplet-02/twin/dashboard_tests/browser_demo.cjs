const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {createRequire}=require('node:module');
const path=require('node:path'),fs=require('node:fs'),crypto=require('node:crypto'),readline=require('node:readline');
async function main(){
 const [python,modules,chrome,evidence]=process.argv.slice(2);
 const {chromium}=createRequire(path.join(modules,'playwright/package.json'))('playwright');
 fs.mkdirSync(evidence,{recursive:true});
 const child=spawn(python,['-X','utf8',path.join(__dirname,'visual_fixture.py')]);
 let stderr='';child.stderr.on('data',d=>stderr+=d);
 const queue=[],waiting=[];readline.createInterface({input:child.stdout}).on('line',l=>{const v=JSON.parse(l);waiting.length?waiting.shift()(v):queue.push(v);});
 const next=()=>queue.length?Promise.resolve(queue.shift()):new Promise(r=>waiting.push(r));
 const command=async value=>{child.stdin.write(JSON.stringify(value)+'\n');return next();};
 const secret=crypto.randomBytes(24).toString('hex');child.stdin.write(secret+'\n');
 let browser;const timer=setTimeout(()=>child.kill(),180000);
 try{
  const {url}=await next();browser=await chromium.launch({executablePath:chrome,headless:true});
  const context=await browser.newContext({viewport:{width:1366,height:900}}),page=await context.newPage();
  const errors=[],external=[];await context.route("**/*",route=>{if(!route.request().url().startsWith(url+"/")){external.push(route.request().url());return route.abort();}return route.continue();});page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);await page.getByLabel('Launch passphrase').fill(secret);await page.getByRole('button',{name:'Connect operator',exact:true}).click();
  await page.locator('#visibility').filter({hasText:'PRIVATE_OPERATOR_ONLY'}).waitFor();
  async function submit(kind,args={}){
   const result=await page.evaluate(async({kind,args})=>{const {requestJSON}=await import('/api.js');const f=await requestJSON('/api/operator/frame');return requestJSON('/api/operator/command',{command_id:'demo-'+crypto.randomUUID(),run_id:f.run_id,expected_revision:['start','pause','resume','stop','speed'].includes(kind)?null:f.revision,kind,args});},{kind,args});
   await page.waitForFunction(state=>{const f=JSON.parse(document.getElementById('frame').textContent);return f.run_id===state.run_id&&f.revision>=state.revision;},result.state);
   return result;
  }
  async function advance(ticks){await command({op:'advance',ticks});const frame=await command({op:'frame'});await page.waitForFunction(n=>JSON.parse(document.getElementById('frame').textContent).sim_tick>=n,frame.sim_tick);return frame;}
  const checkpoint=frame=>({tick:frame.sim_tick,basin:frame.diagnostics.physical_state.water.basin_volume_m3,surface:frame.diagnostics.physical_state.water.surface_volume_m3,queue:frame.diagnostics.physical_state.roads['R-LOW'].queue_vehicles,powered:frame.diagnostics.power.hospital_powered,air:frame.diagnostics.physical_state.air['Z-INDUSTRIAL'].pm25_ug_m3});
  async function picture(name){await page.locator('.workspace').screenshot({path:path.join(evidence,name+'.png')});}
  async function drive(run,record){
   await submit('reset',{new_run_id:run});const points={};let f=await advance(60);points.normal=checkpoint(f);
   await submit('traffic',{name:'morning_rush'});await advance(40);await submit('incident',{operation:'accident',params:{target:'R-LOW'}});f=await advance(20);points.accident=checkpoint(f);assert.ok(points.accident.queue>points.normal.queue);
   for(const operation of ['power_outage','heavy_rain','pollution'])await submit('incident',{operation,params:{}});
   f=await advance(600);points.flood=checkpoint(f);assert.ok(points.flood.surface>200);assert.equal(points.flood.powered,false);
   assert.ok((await page.locator('#metrics').innerText()).includes(points.flood.surface.toFixed(1)+' m³'));if(record)await picture('flood');
   await submit('sensor_fault',{sensor_id:'DTN-WLV-01',fault_type:'FALSE_READING',parameters:{value:180}});f=await advance(5);const spoof=f.comparisons.find(r=>r.sensor_id==='DTN-WLV-01');assert.equal(spoof.reported_value,180);assert.notEqual(spoof.actual_value,180);points.spoof={actual:spoof.actual_value,reported:spoof.reported_value};
   if(record){await page.screenshot({path:path.join(evidence,'private-spoof.png'),fullPage:true});await page.locator('#report').click();await page.locator('#visibility').filter({hasText:'REPORTED_ONLY'}).waitFor();const publicFrame=JSON.parse(await page.locator('#frame').textContent());assert.doesNotMatch(JSON.stringify(publicFrame),/actual_value|delta|comparisons|fault|diagnostics/);await picture('reported-only');await page.locator('.connection-panel').evaluate(n=>n.open=true);await page.locator('#secret').fill(secret);await page.locator('#login button').click();await page.locator('#visibility').filter({hasText:'PRIVATE_OPERATOR_ONLY'}).waitFor();}
   await submit('sensor_clear',{sensor_id:'DTN-WLV-01'});await submit('sensor_fault',{sensor_id:'DTN-TRF-01',fault_type:'DEAD',parameters:{}});f=await advance(5);assert.equal(f.comparisons.find(r=>r.sensor_id==='DTN-TRF-01').status,'FAILED');points.failedSensor=true;
   await submit('sensor_repair',{sensor_id:'DTN-TRF-01'});await submit('sensor_fault',{sensor_id:'DTN-TRF-01',fault_type:'NOISE',parameters:{amplitude:2}});await advance(10);
   for(const operation of ['power_restore','rain_clear','pollution_clear','pump_on'])await submit('incident',{operation,params:{}});
   await submit('incident',{operation:'road_clear',params:{target:'R-LOW'}});await submit('traffic',{name:'low'});f=await advance(600);points.recovery=checkpoint(f);
   assert.ok(points.recovery.surface<points.flood.surface);assert.ok(points.recovery.air<points.flood.air);assert.equal(points.recovery.powered,true);if(record)await picture('recovery');
   return {points,physical:f.diagnostics.physical_state,reports:f.readings.map(({run_id,...record})=>record)};
  }
  const first=await drive('demo-first',true),second=await drive('demo-repeat',false);assert.deepEqual(second,first);
  await submit('reset',{new_run_id:'demo-clean'});await page.waitForFunction(()=>JSON.parse(document.getElementById('frame').textContent).sim_tick===0);assert.equal(await page.locator('#chart-series option[value="DTN-WLV-01"]').count(),0);await picture('dashboard-ready');
  assert.deepEqual(external,[]);fs.writeFileSync(path.join(evidence,'demonstration.json'),JSON.stringify({visibility:'PRIVATE_SUPERVISOR_EVIDENCE',passed:true,seededRepeatEqual:true,checkpoints:first.points,externalRequests:external,blocked:['new eight actuator meanings: no models','Brain/Guardian decisions and cross-layer timeline: no partner integration']},null,2));
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(evidence,'result.json'),JSON.stringify({passed:true,checks:['real rush/accident/outage/rain/pollution','committed flood metrics','reported spoof vs private truth','DEAD and recovery','physical recovery','fresh reset','two seeded runs identical','offline assets/no external app requests']}));
  console.log('D07 full visual demonstration passed; seeded repeat equal');
 }finally{clearTimeout(timer);if(browser)await browser.close();child.stdin.end('{"op":"quit"}\n');await new Promise(r=>child.once('exit',r));assert.equal(stderr,'');}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
