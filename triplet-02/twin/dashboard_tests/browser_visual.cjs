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
 let browser;const timer=setTimeout(()=>child.kill(),90000);
 try{
  const {url}=await next();browser=await chromium.launch({executablePath:chrome,headless:true});
  const context=await browser.newContext({viewport:{width:1366,height:900}}),page=await context.newPage();
  const polls=[];page.on('request',r=>{if(r.url().endsWith('/api/operator/frame'))polls.push(Date.now());});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);await page.getByLabel('Launch passphrase').fill(secret);await page.getByRole('button',{name:'Connect operator',exact:true}).click();
  await page.locator('#visibility').filter({hasText:'PRIVATE_OPERATOR_ONLY'}).waitFor();
  const map=page.locator('#city-map svg');assert.equal(await map.getAttribute('tabindex'),'0','map needs keyboard pan/zoom');
  await map.focus();const box=await map.getAttribute('viewBox');await page.keyboard.press('ArrowRight');assert.notEqual(await map.getAttribute('viewBox'),box);await page.locator('#fit').click();
  const rect=await map.boundingBox();const panBefore=await map.getAttribute('viewBox');await page.mouse.move(rect.x+20,rect.y+20);await page.mouse.down();await page.mouse.move(rect.x+100,rect.y+60,{steps:5});await page.mouse.up();assert.notEqual(await map.getAttribute('viewBox'),panBefore);await page.locator('#fit').click();await page.locator('#zoom-in').click();
  await page.locator('#city-map [aria-label="J-I1"]').click();assert.match(await page.locator('#selected').innerText(),/J-I1/);
  await page.setViewportSize({width:768,height:1024});await page.locator('#city-map [aria-label="J-I1"]').click();assert.match(await page.locator('#selected').innerText(),/J-I1/);await page.setViewportSize({width:1366,height:900});await page.locator('#fit').click();
  await page.locator('#start').click();await page.waitForFunction(()=>document.getElementById('clock').textContent.includes('RUNNING'));
  assert.equal(await page.locator('.traffic-pulse').count(),5);
  assert.equal(await page.locator('.traffic-pulse').first().evaluate(e=>getComputedStyle(e).animationPlayState),'running');
  await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await page.locator('.traffic-pulse').first().evaluate(e=>getComputedStyle(e).animationName),'none');
  await page.emulateMedia({reducedMotion:'no-preference'});await page.locator('#pause').click();await page.waitForFunction(()=>document.getElementById('clock').textContent.includes('PAUSED'));
  assert.equal(await page.locator('.traffic-pulse').first().evaluate(e=>getComputedStyle(e).animationPlayState),'paused');
  await page.selectOption('#speed','10');await page.locator('#set-speed').click();await page.waitForFunction(()=>document.getElementById('clock').textContent.includes('10×'));
  const tick=JSON.parse(await page.locator('#frame').textContent()).sim_tick;await page.locator('#start').click();await page.waitForFunction(t=>JSON.parse(document.getElementById('frame').textContent).sim_tick>=t+8,tick);await page.locator('#pause').click();await page.waitForFunction(()=>document.getElementById('clock').textContent.includes('PAUSED'));
  const second=await context.newPage();await second.goto(url);await second.locator('#secret').fill(secret);await second.locator('#login button').click();await second.locator('#visibility').filter({hasText:'PRIVATE_OPERATOR_ONLY'}).waitFor();assert.equal(JSON.parse(await second.locator('#frame').textContent()).run_id,'browser-a');await second.close();
  await page.route('**/api/operator/frame',r=>r.abort());await page.waitForFunction(()=>document.getElementById('connection').textContent.startsWith('STALE'));
  await command({op:'advance',ticks:400});await page.unroute('**/api/operator/frame');await page.waitForFunction(()=>JSON.parse(document.getElementById('frame').textContent).sim_tick>=410);await page.waitForFunction(()=>document.getElementById('history-notice').textContent.includes('HISTORY_GAP'));
  await command({op:'incident',kind:'dispatch',params:{target:'AMB-01',mission:'visual-mission',destination:'J-D1',route:['R-LOW']}});
  await page.waitForFunction(()=>JSON.parse(document.getElementById('frame').textContent).diagnostics.physical_state.responders['AMB-01'].status==='enroute');
  const marker=page.locator('[data-responder="AMB-01"]');const oldPoint=[await marker.getAttribute('cx'),await marker.getAttribute('cy')];await command({op:'advance',ticks:3});
  await page.waitForFunction(old=>{const p=document.querySelector('[data-responder="AMB-01"]');return p.getAttribute('cx')!==old[0]||p.getAttribute('cy')!==old[1];},oldPoint);
  assert.match(await page.locator('[data-overlay="Emergency"]').textContent(),/AMB-01 enroute.*R-LOW.*s left/);
  await command({op:'incident',kind:'dispatch',params:{target:'AMB-02',mission:'blocked-next-edge',destination:'J-D2',route:['R-LOW','R-DOWN']}});
  await command({op:'incident',kind:'accident',params:{target:'R-DOWN'}});await command({op:'advance',ticks:100});
  await page.waitForFunction(()=>{const u=JSON.parse(document.getElementById('frame').textContent).diagnostics.physical_state.responders['AMB-02'];return u.status==='blocked'&&u.at_node_id==='J-D1'&&u.edge_remaining_s===0;});
  const junction=await page.evaluate(async()=>{const l=await fetch('/map-layout.json').then(r=>r.json());return l.anchors.find(a=>a.id==='J-D1');});const blockedMarker=page.locator('[data-responder="AMB-02"]');
  assert.equal(await blockedMarker.getAttribute('cx'),String(junction.x),'unentered blocked edge must remain at confirmed start node');assert.equal(await blockedMarker.getAttribute('cy'),String(junction.y));
  await command({op:'incident',kind:'road_clear',params:{target:'R-DOWN'}});await command({op:'advance',ticks:1});
  await page.waitForFunction(()=>JSON.parse(document.getElementById('frame').textContent).diagnostics.physical_state.responders['AMB-02'].status==='enroute');
  await command({op:'advance',ticks:2});await page.waitForFunction(x=>document.querySelector('[data-responder="AMB-02"]').getAttribute('cx')!==String(x),junction.x);
  for(let i=1;i<polls.length;i++)assert.ok(polls[i]-polls[i-1]>=220,`poll interval ${polls[i]-polls[i-1]}`);
  await page.screenshot({path:path.join(evidence,'dashboard-1366.png'),fullPage:true});
  for(const [width,height] of [[1920,1080],[1366,768],[768,1024],[375,812]]){await page.setViewportSize({width,height});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(evidence,`dashboard-${width}.png`),fullPage:true});}
  await page.setViewportSize({width:1366,height:900});
  assert.ok(await page.locator('#chart-data').count());
  await page.getByRole('button',{name:'Connect reported view',exact:true}).click();await page.locator('#visibility').filter({hasText:'REPORTED_ONLY'}).waitFor();const anchor=await page.evaluate(async()=>{const l=await fetch('/map-layout.json').then(r=>r.json());return l.anchors.find(a=>a.id==='AMB-01');});assert.equal(await marker.getAttribute('cx'),String(anchor.x));assert.doesNotMatch(await page.locator('[data-overlay="Emergency"]').textContent(),/visual-mission|enroute|s left/);
  const renderMs=await page.evaluate(()=>performance.getEntriesByName('city-frame-render').at(-1).duration);assert.ok(renderMs<250,`frame render ${renderMs}ms`);
  for(const id of ['zoom-in','zoom-out','fit','report','logout']){const r=await page.locator('#'+id).boundingBox();assert.ok(r.width>=40&&r.height>=40);}
  const measurements=await page.evaluate(async()=>{const {DashboardHistory}=await import('/history.js');const {Timeline}=await import('/timeline.js');const h=new DashboardHistory();h.run='perf';h.visibility='REPORTED_ONLY';h.series.set('load',Array.from({length:1000},(_,i)=>({sim_tick:i,reported_value:i%30,seq:i,unit:'kW'})));h.events=Array.from({length:512},(_,i)=>({seq:i,timestamp:'sample',sensor_id:'load',reported_value:i,unit:'kW',status:'OK'}));h.publicSeq=512;const timeline=new Timeline(h,{});const t=performance.now();timeline.render({run_id:'perf',visibility:'REPORTED_ONLY'});return {ms:performance.now()-t,circles:document.querySelectorAll('#history-chart circle').length,events:document.querySelectorAll('#timeline li').length};});
  fs.writeFileSync(path.join(evidence,'performance.json'),JSON.stringify({measurements,renderMs,pollIntervals:polls.slice(1).map((t,i)=>t-polls[i])},null,2));
  assert.equal(measurements.circles,1000);assert.equal(measurements.events,512);assert.ok(measurements.ms<250,JSON.stringify(measurements));
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(evidence,'result.json'),JSON.stringify({passed:true,checks:['keyboard and pointer pan/zoom/select/resize','five illustrative traffic paths','1x/10x pace','two tabs one owner','400ticks history gap','real edge responder progress','blocked-next-edge zero remaining/recovery','public map attributes clear','running/pause','reduced motion','four widths','chart text alternative','1000point/512event render under250ms']}));
  console.log('D06 visual browser passed');
 }finally{clearTimeout(timer);if(browser)await browser.close();child.stdin.end('{"op":"quit"}\n');await new Promise(r=>child.once('exit',r));assert.equal(stderr,'');}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
