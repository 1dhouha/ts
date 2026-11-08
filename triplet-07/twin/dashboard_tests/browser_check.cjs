// Real offline Chromium E2E using installed Playwright, no package download.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const {createRequire} = require('node:module');

async function main() {
  const [python, modules, executable, evidence] = process.argv.slice(2);
  const {chromium} = createRequire(path.join(modules, 'playwright/package.json'))('playwright');
  fs.mkdirSync(evidence,{recursive:true});
  const secret=crypto.randomBytes(24).toString('base64url');
  const child=spawn(python,['-X','utf8',path.join(__dirname,'browser_fixture.py')],{stdio:['pipe','pipe','pipe']});
  let stderr=''; child.stderr.on('data',data=>{stderr+=data;});
  const lines=readline.createInterface({input:child.stdout});
  const pending=[], waiting=[];
  lines.on('line',line=>{ if(waiting.length) waiting.shift()(JSON.parse(line)); else pending.push(JSON.parse(line)); });
  const next=()=>pending.length?Promise.resolve(pending.shift()):new Promise(resolve=>waiting.push(resolve));
  const command=async value=>{child.stdin.write(JSON.stringify(value)+'\n'); return next();};
  child.stdin.write(secret+'\n');
  const deadline=setTimeout(()=>{child.kill(); process.exitCode=1; throw new Error('Browser check timeout');},45000);
  let browser;
  try {
    const {url}=await next();
    browser=await chromium.launch({executablePath:executable,headless:true,args:['--disable-background-networking']});
    const context=await browser.newContext({viewport:{width:1366,height:768}});
    const external=[], errors=[], reports=[];
    await context.route('**/*',route=>{
      const requestURL=route.request().url();
      if(!requestURL.startsWith(url+'/')) {external.push(requestURL); return route.abort();}
      return route.continue();
    });
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(error.message));
    page.on('response',async response=>{
      if(response.url().endsWith('/api/report/frame') && response.ok()) reports.push(await response.json());
    });
    await page.goto(url);
    await page.getByRole('button',{name:'Connect reported view',exact:true}).click();
    await page.locator('#visibility').filter({hasText:'REPORTED_ONLY'}).waitFor();
    const reported=JSON.parse(await page.locator('#frame').textContent());
    assert.equal(reported.run_id,'browser-a');
    const water=reported.records.filter(r=>r.sensor_id==='DTN-WLV-01').at(-1);
    assert.equal(water.reported_value,180);
    assert.ok(reported.records.some(r=>r.reported_value===null));
    assert.doesNotMatch(JSON.stringify(reported),/actual_value|delta|fault|diagnostics|revision|FALSE_READING/);
    await page.getByLabel('Launch passphrase').fill(secret);
    await page.getByRole('button',{name:'Connect operator',exact:true}).click();
    await page.locator('#visibility').filter({hasText:'PRIVATE_OPERATOR_ONLY'}).waitFor();
    assert.equal(await page.locator('#secret').inputValue(),'');
    const privateFrame=JSON.parse(await page.locator('#frame').textContent());
    assert.ok(privateFrame.diagnostics.active_faults['DTN-WLV-01']);
    const second=await context.newPage();
    await second.goto(url);
    await second.getByLabel('Launch passphrase').fill(secret);
    await second.getByRole('button',{name:'Connect operator',exact:true}).click();
    await second.locator('#visibility').filter({hasText:'PRIVATE_OPERATOR_ONLY'}).waitFor();
    assert.equal(JSON.parse(await second.locator('#frame').textContent()).run_id,'browser-a');
    await command({op:'reset',run_id:'browser-b'});
    await page.waitForFunction(()=>document.getElementById('identity').textContent.includes('browser-b'));
    assert.equal(JSON.parse(await page.locator('#frame').textContent()).sim_tick,0);
    await command({op:'run'});
    await page.waitForFunction(()=>document.getElementById('frame').textContent.includes('BUDGET_EXHAUSTED'),null,{timeout:10000});
    await context.setOffline(true);
    await page.waitForFunction(()=>document.getElementById('connection').textContent.startsWith('STALE'));
    await context.setOffline(false);
    await page.waitForFunction(()=>document.getElementById('connection').textContent==='CONNECTED');
    await page.getByRole('button',{name:'Connect reported view',exact:true}).click();
    await page.locator('#visibility').filter({hasText:'REPORTED_ONLY'}).waitFor();
    assert.doesNotMatch(await page.locator('#frame').textContent(),/actual_value|delta|fault|diagnostics|revision/);
    await page.getByRole('button',{name:'Disconnect / sign out',exact:true}).click();
    assert.equal(await page.locator('#frame').textContent(),'No data.');
    await page.waitForFunction(()=>document.getElementById('auth-state').textContent==='Signed out.');
    const denied=await context.request.get(url+'/api/operator/frame');
    assert.equal(denied.status(),401);
    assert.deepEqual(await page.evaluate(()=>Object.keys(localStorage)),[]);
    assert.deepEqual(await page.evaluate(()=>Object.keys(sessionStorage)),[]);
    assert.equal(await page.evaluate(async()=>(await navigator.serviceWorker.getRegistrations()).length),0);
    await page.getByRole('button',{name:'Connect reported view',exact:true}).click();
    await page.locator('#visibility').filter({hasText:'REPORTED_ONLY'}).waitFor();
    for(const width of [1366,768,375]) {
      await page.setViewportSize({width,height:768});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      await page.screenshot({path:path.join(evidence,`bridge-${width}.png`),fullPage:true});
    }
    await second.close();
    await command({op:'reset',run_id:'browser-c'});
    await page.waitForFunction(()=>document.getElementById('identity').textContent.includes('browser-c'));
    assert.deepEqual(JSON.parse(await page.locator('#frame').textContent()).records,[]);
    assert.deepEqual(errors,[]);
    assert.deepEqual(external,[]);
    assert.ok(reports.length>1);
    fs.writeFileSync(path.join(evidence,'browser.json'),JSON.stringify({passed:true,
      browser:browser.version(),checks:['reported spoof/null/privacy','operator login','two tabs one run',
      'reset clears frame','budget stops honestly','offline stale/reconnect','logout revokes session',
      'no browser persistence/service worker','three viewport widths','zero external requests/page errors']},null,2));
    console.log('Real browser D02 E2E passed');
  } finally {
    clearTimeout(deadline);
    if(browser) await browser.close();
    child.stdin.end(JSON.stringify({op:'quit'})+'\n');
    const exit=await new Promise(resolve=>child.once('exit',resolve));
    assert.equal(exit,0,stderr);
    assert.equal(stderr,'');
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
