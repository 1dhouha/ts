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
  assert.equal(await page.locator('#operator-controls').count(),1,'missing D04 controls');
  await page.getByRole('button',{name:'Step 1 second',exact:true}).click();
  await page.waitForFunction(()=>JSON.parse(document.getElementById('frame').textContent).sim_tick===11);
  await page.locator('#incident-drawer').evaluate(n=>n.open=true);
  await page.selectOption('#incident-kind','accident');await page.getByRole('button',{name:'Apply incident',exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('command-result').textContent.includes('COMPLETED'));
  await page.waitForFunction(()=>JSON.parse(document.getElementById('frame').textContent).diagnostics.physical_state.roads['R-LOW'].obstruction_fraction===1);
  await page.getByRole('button',{name:'Start / resume',exact:true}).click();await page.waitForFunction(()=>document.getElementById('clock').textContent.includes('RUNNING'));
  await page.getByRole('button',{name:'Pause city',exact:true}).click();await page.waitForFunction(()=>document.getElementById('clock').textContent.includes('PAUSED'));
  page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'Reset city',exact:true}).click();
  await page.waitForFunction(()=>JSON.parse(document.getElementById('frame').textContent).sim_tick===0);
  assert.match(await page.locator('#selected').innerText(),/Select/);
  await context.setOffline(true);await page.waitForFunction(()=>document.getElementById('connection').textContent.startsWith('STALE'));
  assert.equal(await page.getByRole('button',{name:'Step 1 second',exact:true}).isDisabled(),true);
  await context.setOffline(false);await page.waitForFunction(()=>document.getElementById('connection').textContent==='CONNECTED');
  await page.getByRole('button',{name:'Connect reported view',exact:true}).click();
  assert.equal(await page.locator('#operator-controls').isVisible(),false);
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(evidence,'result.json'),JSON.stringify({passed:true,checks:['single step','real accident effect','start/pause','confirmed reset','selection clears','stale disables writes','report hides controls']}));
  console.log('D04 controls browser passed');
 }finally{clearTimeout(timer);if(browser)await browser.close();child.stdin.end('{"op":"quit"}\n');await new Promise(r=>child.once('exit',r));assert.equal(stderr,'');}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
