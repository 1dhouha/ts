const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {createRequire}=require('node:module');
const path=require('node:path'),fs=require('node:fs'),crypto=require('node:crypto'),readline=require('node:readline');
async function main(){
 const [python,modules,chrome,evidence]=process.argv.slice(2);
 const {chromium}=createRequire(path.join(modules,'playwright/package.json'))('playwright');
 fs.mkdirSync(evidence,{recursive:true});
 const child=spawn(python,['-X','utf8',path.join(__dirname,'browser_fixture.py')]);
 let stderr='';child.stderr.on('data',d=>stderr+=d);
 const queue=[],waiting=[];readline.createInterface({input:child.stdout}).on('line',l=>{const v=JSON.parse(l);waiting.length?waiting.shift()(v):queue.push(v);});
 const next=()=>queue.length?Promise.resolve(queue.shift()):new Promise(r=>waiting.push(r));
 const command=async value=>{child.stdin.write(JSON.stringify(value)+'\n');return next();};
 const secret=crypto.randomBytes(24).toString('hex');child.stdin.write(secret+'\n');
 let browser;const timer=setTimeout(()=>child.kill(),90000);
 try{
  const {url}=await next();browser=await chromium.launch({executablePath:chrome,headless:true});
  const context=await browser.newContext({viewport:{width:1366,height:900}}),page=await context.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);await page.getByLabel('Launch passphrase').fill(secret);await page.getByRole('button',{name:'Connect operator',exact:true}).click();
  await page.locator('#visibility').filter({hasText:'PRIVATE_OPERATOR_ONLY'}).waitFor();
  assert.equal(await page.locator('#city-map svg').count(),1,'D03 interactive SVG missing');
  assert.equal(await page.locator('#asset-list option').count(),60,'35 original entities + Port schematic + 8 actuators + 16 PDF sensors');
  await page.selectOption('#asset-list','R-LOW');assert.match(await page.locator('#selected').innerText(),/R-LOW/);
  const before=await page.locator('#city-map svg').getAttribute('viewBox');await page.getByRole('button',{name:'Zoom in',exact:true}).click();
  assert.notEqual(await page.locator('#city-map svg').getAttribute('viewBox'),before);
  await page.getByRole('button',{name:'Fit city',exact:true}).click();assert.equal(await page.locator('#city-map svg').getAttribute('viewBox'),before);
  await page.getByLabel('Traffic layer', {exact:true}).uncheck();assert.equal(await page.locator('[data-overlay="Traffic"]').isVisible(),false);
  await page.getByLabel('Traffic layer', {exact:true}).check();
  await page.selectOption('#asset-list','HOSP-01');await page.keyboard.press('Escape');assert.match(await page.locator('#selected').innerText(),/Select/);
  assert.match(await page.locator('#metrics').innerText(),/50.0 kW/);
  await page.getByLabel('Power layer',{exact:true}).uncheck();assert.equal(await page.locator('[data-overlay="Power"]').isVisible(),false);await page.getByLabel('Power layer',{exact:true}).check();
  for(const incident of [{kind:'accident',params:{target:'R-LOW'}},{kind:'power_outage'},{kind:'heavy_rain'}]){
    await command({op:'incident',...incident});await command({op:'step'});const committed=await command({op:'frame'});
    await page.waitForFunction(revision=>JSON.parse(document.getElementById('frame').textContent).revision>=revision,committed.revision);
    const shown=await page.locator('#metrics').innerText();assert.ok(shown.includes(committed.diagnostics.physical_state.water.basin_volume_m3.toFixed(1)+' m³'));
    assert.ok(shown.includes(committed.diagnostics.water_levels.basin_m.toFixed(1)+' m'));
    assert.equal(await page.locator('[data-road="R-LOW"]').getAttribute('stroke'),'#ff8b8b');
    if(incident.kind==='power_outage')assert.ok(shown.includes('NO POWER'));
    if(incident.kind==='heavy_rain')assert.ok(shown.includes('180.0 mm/h'));
  }
  await page.screenshot({path:path.join(evidence,'operator.png'),fullPage:true});
  await context.setOffline(true);await page.waitForFunction(()=>document.getElementById('connection').textContent.startsWith('STALE'));
  await context.setOffline(false);await page.waitForFunction(()=>document.getElementById('connection').textContent==='CONNECTED');
  await page.getByRole('button',{name:'Connect reported view',exact:true}).click();await page.locator('#visibility').filter({hasText:'REPORTED_ONLY'}).waitFor();
  assert.equal(await page.locator('#metrics').innerText(),'Physical state requires operator access.');
  assert.doesNotMatch(await page.locator('#selected').innerText(),/50.0|active_faults|physical_state/);
  for(const width of [1920,1366,768,375]){await page.setViewportSize({width,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),JSON.stringify(await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,wide:[...document.querySelectorAll("body *")].filter(e=>e.getBoundingClientRect().right>innerWidth).map(e=>e.tagName+"#"+e.id).slice(0,12)}))));}
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(evidence,'result.json'),JSON.stringify({passed:true,checks:['60 assets including PDF inventory','select/escape','zoom/fit','layers','real metrics','stale/reconnect','privacy switch','four widths','no page errors']}));
  console.log('D03 browser passed');
 }finally{clearTimeout(timer);if(browser)await browser.close();child.stdin.end('{"op":"quit"}\n');await new Promise(r=>child.once('exit',r));assert.equal(stderr,'');}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
