// Delayed real HTTP authentication regressions for D02-R1-01.
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {createRequire}=require('node:module');
const crypto=require('node:crypto');
const fs=require('node:fs');
const path=require('node:path');
const readline=require('node:readline');

async function main(){
  const [python,modules,executable,evidence]=process.argv.slice(2);
  const {chromium}=createRequire(path.join(modules,'playwright/package.json'))('playwright');
  fs.mkdirSync(evidence,{recursive:true});
  const browser=await chromium.launch({executablePath:executable,headless:true,args:['--disable-background-networking']});
  const results=[];
  async function withCity(test){
    const secret=crypto.randomBytes(24).toString('base64url');
    const child=spawn(python,['-X','utf8',path.join(__dirname,'browser_fixture.py')],{stdio:['pipe','pipe','pipe']});
    let stderr='';child.stderr.on('data',data=>{stderr+=data;});
    const lines=readline.createInterface({input:child.stdout});
    const ready=new Promise(resolve=>lines.once('line',line=>resolve(JSON.parse(line))));
    child.stdin.write(secret+'\n');
    const context=await browser.newContext();
    try{
      const {url}=await ready;
      const page=await context.newPage();
      await page.goto(url);
      await test({context,page,url,secret});
    }finally{
      await context.close();
      child.stdin.end(JSON.stringify({op:'quit'})+'\n');
      const exit=await new Promise(resolve=>child.once('exit',resolve));
      assert.equal(exit,0,stderr);
      assert.equal(stderr,'');
    }
  }
  const waitReport=page=>page.waitForFunction(()=>document.getElementById('visibility').textContent==='REPORTED_ONLY');
  const waitAuthIdle=page=>page.waitForFunction(()=>!document.querySelector('#login button').disabled);
  async function login(page,secret){
    await page.locator(".connection-panel").evaluate(n=>n.open=true);
    await page.locator('#secret').fill(secret);
    await page.locator('#login button').click();
  }
  try{
    for(const heldSide of ['request','response']) for(const target of ['report','logout']){
      await withCity(async({context,page,url,secret})=>{
        let release,observed,count=0;
        const held=new Promise(resolve=>release=resolve),seen=new Promise(resolve=>observed=resolve);
        await page.route('**/api/session',async route=>{
          ++count;
          const response=heldSide==='response'?await route.fetch():null;
          observed();await held;
          if(response) await route.fulfill({response}); else await route.continue();
        });
        try{
          await login(page,secret);await seen;
          assert.equal(await page.locator('#login button').isDisabled(),true,'login must serialize auth operations');
          await page.locator('#'+target).click();
          if(target==='report') await waitReport(page);
          else assert.equal(await page.locator('#frame').textContent(),'No data.');
          await page.locator('#login').evaluate(form=>form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
          release();
          await waitAuthIdle(page);
          assert.equal(count,1,'concurrent login must not dispatch another session request');
          if(target==='report') await waitReport(page);
          else assert.equal(await page.locator('#frame').textContent(),'No data.');
          assert.doesNotMatch(await page.locator('#frame').textContent(),/diagnostics|PRIVATE_OPERATOR_ONLY/);
          const response=await context.request.get(url+'/api/operator/frame');
          assert.equal(response.status(),401,'obsolete login session must be revoked');
          results.push(`${heldSide}: pending login then ${target} preserves intent and revokes session`);
        }finally{release();}
      });
    }
    await withCity(async({context,page,url,secret})=>{
      await login(page,secret);
      await page.waitForFunction(()=>document.getElementById('visibility').textContent==='PRIVATE_OPERATOR_ONLY');
      let release,observed;
      const held=new Promise(resolve=>release=resolve),seen=new Promise(resolve=>observed=resolve);
      await page.route('**/api/logout',async route=>{const response=await route.fetch();observed();await held;await route.fulfill({response});});
      try{
        await page.locator('#logout').click();await seen;
        assert.equal(await page.locator('#login button').isDisabled(),true);
        release();await waitAuthIdle(page);
        await login(page,secret);
        await page.waitForFunction(()=>document.getElementById('visibility').textContent==='PRIVATE_OPERATOR_ONLY');
        assert.equal((await context.request.get(url+'/api/operator/frame')).status(),200);
        results.push('held logout response blocks new login until cleanup completes');
      }finally{release();}
    });
    await withCity(async({context,page,url,secret})=>{
      await login(page,secret);
      await page.waitForFunction(()=>document.getElementById('visibility').textContent==='PRIVATE_OPERATOR_ONLY');
      await page.route('**/api/logout',route=>route.abort('failed'));
      await page.locator('#logout').click();
      await page.waitForFunction(()=>document.getElementById('auth-state').textContent.includes('pending'));
      assert.equal(await page.locator('#frame').textContent(),'No data.');
      assert.equal(await page.locator('#login button').isDisabled(),true);
      assert.equal((await context.request.get(url+'/api/operator/frame')).status(),200,'failed transport cannot prove revocation');
      await page.unroute('**/api/logout');
      await waitAuthIdle(page);
      assert.equal((await context.request.get(url+'/api/operator/frame')).status(),401);
      results.push('failed signout stays pending and blocks auth until retry confirms revocation');
    });
    fs.writeFileSync(path.join(evidence,'auth_races.json'),JSON.stringify({passed:true,results},null,2));
    console.log('Delayed authentication regressions passed: '+results.length);
  }finally{await browser.close();}
}
const timer=setTimeout(()=>{console.error('Authentication browser check timeout');process.exit(1);},45000);
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>clearTimeout(timer));
