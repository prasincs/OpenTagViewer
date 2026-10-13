import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fixture=spawn(process.env.BRIDGE_PYTHON||'python3',[fileURLToPath(new URL('browser_fixture.py',import.meta.url))],{stdio:['pipe','pipe','pipe']});
const lines=createInterface({input:fixture.stdout});
const pending=[],queued=[];
lines.on('line',line=>{const value=JSON.parse(line);pending.length?pending.shift()(value):queued.push(value);});
// Queue values directly; all fixture output is synthetic and remains inside this test.
const next=()=>queued.length?Promise.resolve(queued.shift()):new Promise(resolve=>pending.push(resolve));
const command=async name=>{fixture.stdin.write(JSON.stringify({command:name})+'\n');return next();};
let browser;
try{
 const first=await next();
 browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{})});
 const context=await browser.newContext({viewport:{width:800,height:1000}});
 const page=await context.newPage();
 let external=false;
 page.on('request',request=>{if(!request.url().startsWith(first.origin))external=true;});
 if(process.env.PAIRING_NEGATIVE_CONTROL==='1')await page.route('**/pairing-ui.js',async route=>{
   const response=await route.fetch();await route.fulfill({response,body:(await response.text()).replaceAll('Pairing failed. Start again to retry.','BROKEN MESSAGE')});
 });
 const waitText=async expected=>{await page.waitForFunction(text=>document.getElementById('status').textContent===text,expected,{timeout:12000});};
 await page.goto(first.origin+'/#'+first.token);
 await waitText('Waiting for your phone…');
 await page.locator('#qr svg').waitFor({state:'visible'});
 const original=await page.locator('#qr').innerHTML();
 const shots=process.env.PAIRING_SCREENSHOTS;
 if(shots){await mkdir(shots,{recursive:true});await page.screenshot({path:shots+'/mac-pairing-pending.png'});}
 await command('expire');
 await page.waitForFunction(old=>document.getElementById('qr').innerHTML!==old,original,{timeout:12000});
 await command('claim');await waitText('Phone connected. Waiting for it to save the connection…');
 await command('negative');await waitText('Pairing failed. Start again to retry.');
 assert.equal(await page.locator('#qr svg').count(),0);
 assert.equal(await page.locator('#cancel').isDisabled(),true);
 const second=await command('new');await page.goto(first.origin+'/#'+second.token);
 await waitText('Waiting for your phone…');await page.locator('#cancel').click();
 await waitText('Pairing cancelled.');assert.equal(await page.locator('#qr svg').count(),0);
 const third=await command('new');await page.goto(first.origin+'/#'+third.token);
 await waitText('Waiting for your phone…');await command('claim');await command('positive');
 await waitText('Phone pairing confirmed.');
 assert.match(await page.locator('#apple').textContent(),/Finish Apple sign-in locally/);
 await command('apple-ready');await waitText('Phone paired and Apple session ready. You can close this window.');
 assert.equal(await page.locator('#qr svg').count(),0);
 if(shots)await page.screenshot({path:shots+'/mac-pairing-success.png'});
 const fourth=await command('new');await page.goto(first.origin+'/#'+fourth.token);
 await waitText('Waiting for your phone…');await command('apple-failed');
 await waitText('Apple sign-in failed. Retry setup-on-mac.command locally on the Mac.');
 assert.equal(await page.locator('#qr svg').count(),0);
 const fifth=await command('new');await page.goto(first.origin+'/#'+fifth.token);await waitText('Waiting for your phone…');
 await command('vault-pending');await page.locator('#vault-approve').waitFor({state:'visible'});
 assert.match(await page.locator('#vault-fingerprint').textContent(),/^[a-f0-9]{64}$/);
 await page.locator('#vault-approve').click();
 await page.waitForFunction(()=>document.getElementById('vault-status').textContent.includes('Vault locked.'),null,{timeout:12000});
 assert.equal(await page.locator('#vault-approve').isVisible(),false);
 if(shots)await page.screenshot({path:shots+'/mac-vault-locked.png'});
 assert.equal(external,false,'UI contacted an external server');
 console.log('Browser checks passed: renewal, claim, negative result, cancellation, confirmation, Apple readiness; no external requests.');
}finally{
 if(browser)await browser.close();
 fixture.stdin.write('{"command":"quit"}\n');fixture.stdin.end();
}
