// No printing: loopback-only browser regression with every client API mocked.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const crypto=require('node:crypto'),fs=require('node:fs'),path=require('node:path');
const base=process.env.UI_BASE_URL || 'http://127.0.0.1:18081';
assert.ok(['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname));
const credential=crypto.randomBytes(32).toString('hex');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROME_EXECUTABLE?{executablePath:process.env.CHROME_EXECUTABLE}:{})});
 try {
  const page=await browser.newPage({viewport:{width:1280,height:900}});
  const errors=[],external=[],writes=[],seen=new Map();
  let printers=[{id:'p-one',name:'Canon <img src="https://invalid.example/">',formats:['pdf','zpl'],allowed_options:{PageSize:['A4']},status:'paused'},{id:'p-two',name:'Second printer',formats:['pdf'],allowed_options:{MediaType:['Plain']},status:'ready'}];
  const schemas={'p-one':{availability:'available',supported:{paper_size:['A4','A5'],sides:['one-sided','two-sided-long-edge']},defaults:{paper_size:'A4',sides:'one-sided'}},'p-two':{availability:'available',supported:{paper_size:['Letter']},defaults:{paper_size:'Letter'}}};
  let rejectKey=false,loseReply=false,delay=0,failCode=0,schemaDelay=0,unavailable=false,zeroRights=false,failPrinterRead=false;
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith(base+'/'))external.push(r.url());});
  const handler=async route=>{
   const req=route.request(),p=new URL(req.url()).pathname;
   assert.equal(req.headers().authorization,'Bearer '+credential);
   if(rejectKey){await route.fulfill({status:401,json:{detail:'Invalid API key'}});return;}
   if(p==='/api/v1/printers'){await route.fulfill(failPrinterRead?{status:503,json:{detail:'Temporarily unavailable'}}:{json:printers.map(p=>({...p,capabilities:unavailable?{...schemas[p.id],availability:'unavailable',reason:'CUPS unavailable'}:zeroRights?{...schemas[p.id],supported:{}}:schemas[p.id]}))});return;}
   if(p==='/api/v1/jobs'&&req.method()==='GET'){await route.fulfill({json:[...seen.values()].map(x=>({...x,status:'completed',title:'In thử',format:'pdf',copies:1,accepted_at:'2026-10-04'}))});return;}
   assert.equal(p,'/api/v1/jobs');assert.equal(req.method(),'POST');
   const id=req.headers()['idempotency-key'];assert.match(id,/^web-[a-f0-9]{48}$/);
   const body=req.postDataBuffer().toString();writes.push({id,body});
   if(delay)await new Promise(r=>setTimeout(r,delay));
   if(failCode){const code=failCode;failCode=0;await route.fulfill({status:code,json:{detail:'Unsupported options'}});return;}
   const duplicate=seen.has(id),job=seen.get(id)||{job_id:'j-'+(seen.size+1),status:'queued'};seen.set(id,job);
   if(loseReply){loseReply=false;await route.abort('failed');return;}
   await route.fulfill({status:duplicate?200:202,json:{...job,deduplicated:duplicate}});
  };await page.route(`${base}/api/v1/**`,handler);
  const connect=async()=>{await page.locator('#api-key').fill(credential);await page.locator('#key-form button').click();await page.waitForFunction(()=>!document.querySelector('#key-form button').disabled);};
  const idle=()=>page.waitForFunction(()=>!document.querySelector('#disconnect').disabled);
  const file=async(name='sample.pdf')=>page.locator('#client-file').setInputFiles({name,mimeType:name.endsWith('zpl')?'text/plain':'application/pdf',buffer:Buffer.from(name.endsWith('zpl')?'^XA^FO20,20^FDTest^FS^XZ':'%PDF-1.7\nfixture')});
  await page.goto(base+'/client');rejectKey=true;await connect();assert.equal(await page.locator('#key-panel').isVisible(),true);assert.match(await page.locator('#client-notice').textContent(),/không hợp lệ/);
  rejectKey=false;await connect();assert.equal(await page.locator('#api-key').inputValue(),'');assert.equal(await page.locator('#client-workspace').isVisible(),true);
  assert.equal(await page.locator('img').count(),0);assert.match(await page.locator('#printer-state').textContent(),/tạm dừng/);await page.locator('#options-state').getByText(/Chọn tùy chọn phổ thông/).waitFor();
  assert.equal(await page.locator('#client-options img, #client-options script').count(),0);assert.match(await page.locator('#client-options').textContent(),/Khổ giấy/);
  await page.locator('#client-options select[data-option-name="paper_size"]').selectOption('A4');assert.equal(await page.locator('#client-options select[data-option-name="paper_size"]').inputValue(),'A4');
  assert.deepEqual(await page.evaluate(()=>({local:Object.keys(localStorage),session:Object.keys(sessionStorage)})),{local:[],session:['print-appliance.client.api-key']});
  assert.ok(await page.evaluate(key=>sessionStorage.getItem('print-appliance.client.api-key')===key,credential));
  await page.reload();await idle();assert.equal(await page.locator('#client-workspace').isVisible(),true);assert.equal(await page.locator('#api-key').inputValue(),'');assert.equal(await page.locator('#client-file').inputValue(),'');assert.equal(writes.length,0);
  await page.evaluate(()=>{dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}));dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));});await idle();assert.equal(await page.locator('#client-workspace').isVisible(),true);assert.equal(writes.length,0);
  await page.locator('#client-options select[data-option-name="paper_size"]').selectOption('A5');await file();delay=450;loseReply=true;await page.locator('#send-print').click();assert.equal(await page.locator('#send-print').isDisabled(),true);
  await page.locator('#print-form').evaluate(f=>f.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));await idle();
  assert.equal(writes.length,1);assert.equal(seen.size,1);assert.equal(await page.locator('#uncertain-request').isVisible(),true);
  assert.equal(await page.locator('#client-file').isDisabled(),true);assert.equal(await page.locator('#send-print').isDisabled(),true);
  assert.equal(await page.locator('#request-id').textContent(),writes[0].id);
  await page.locator('#retry-print').click();await idle();assert.equal(seen.size,1);assert.equal(writes[1].id,writes[0].id);
  // Multipart boundaries differ, but submitted fields/file must remain identical on retry.
  for(const write of writes)for(const value of ['sample.pdf','%PDF-1.7','p-one','In thử'])assert.ok(write.body.includes(value));
  assert.ok(writes[0].body.includes('A5')&&writes[1].body.includes('A5'));assert.equal(await page.locator('#uncertain-request').isVisible(),false);assert.match(await page.locator('#client-notice').textContent(),/không tạo lệnh trùng/);
  assert.equal(await page.locator('#client-file').inputValue(),'');assert.equal(await page.locator('#client-jobs article').count(),1);
  await file('label.zpl');assert.equal(await page.locator('#client-format').inputValue(),'zpl');
  await page.locator('#client-format').selectOption('zpl');assert.equal(await page.locator('#options-state').textContent(), 'ZPL gửi nguyên bản; tùy chọn driver PDF không áp dụng.');
  await file('label.zpl');failCode=422;await page.locator('#send-print').click();await idle();assert.equal(await page.locator('#uncertain-request').isVisible(),false);assert.equal(await page.locator('#client-file').isDisabled(),false);
  await page.locator('#send-print').click();await idle();assert.equal(seen.size,2);assert.notEqual(writes.at(-1).id,writes[0].id);assert.ok(writes.at(-1).body.includes('^XA'));assert.ok(!writes.at(-1).body.includes('name="options"') && !writes.at(-1).body.includes('name="paper_size"'));
  await page.locator('#client-printer').selectOption('p-two');await page.locator('#options-state').getByText(/Chọn tùy chọn phổ thông/).waitFor();assert.equal(await page.locator('#client-options select').count(),1);assert.equal(await page.locator('#client-options select').inputValue(),'');
  await page.locator('#client-printer').selectOption('p-one');await page.locator('#options-state').getByText(/Chọn tùy chọn phổ thông/).waitFor();assert.equal(await page.locator('#client-options select').first().inputValue(),'');
  zeroRights=true;await page.locator('#refresh-client').click();await idle();assert.equal(await page.locator('#client-options select').count(),0);await file();await page.locator('#send-print').click();await idle();assert.ok(!writes.at(-1).body.includes('name="options"'));zeroRights=false;await page.locator('#refresh-client').click();await idle();
  if(process.env.UI_ARTIFACT_DIR){fs.mkdirSync(process.env.UI_ARTIFACT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.UI_ARTIFACT_DIR,'client-desktop.png'),fullPage:true});}
  const mobile=await browser.newPage({viewport:{width:390,height:844}});await mobile.route(`${base}/api/v1/**`,handler);await mobile.goto(base+'/client');await mobile.locator('#api-key').fill(credential);await mobile.locator('#key-form button').click();await mobile.waitForFunction(()=>!document.querySelector('#disconnect').disabled);
  assert.ok(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.ok((await mobile.locator('#client-notice').boundingBox()).y<30);
  if(process.env.UI_ARTIFACT_DIR)await mobile.screenshot({path:path.join(process.env.UI_ARTIFACT_DIR,'client-mobile.png')});await mobile.close();
  unavailable=true;await page.locator('#refresh-client').click();await idle();await page.locator('#client-options').getByText(/CUPS unavailable/).waitFor();await file();failCode=503;const beforeUnsupported=writes.length;await page.locator('#send-print').click();await idle();assert.equal(writes.length,beforeUnsupported+1);assert.equal(await page.locator('#uncertain-request').isVisible(),true);unavailable=false;
  page.on('dialog', dialog=>dialog.accept());
  await page.locator('#disconnect').click();assert.equal(await page.locator('#client-workspace').isVisible(),false);assert.equal(await page.evaluate(()=>sessionStorage.getItem('print-appliance.client.api-key')),null);
  await page.reload();assert.equal(await page.locator('#key-panel').isVisible(),true);
  printers=[];await connect();assert.equal(await page.locator('#no-printers').isVisible(),true);assert.equal(await page.locator('#send-print').isDisabled(),true);assert.equal(await page.locator('#printer-state').textContent(),'');
  const beforeReload=writes.length;await page.reload();await idle();assert.equal(await page.locator('#client-workspace').isVisible(),true);assert.equal(await page.locator('#no-printers').isVisible(),true);assert.equal(writes.length,beforeReload);
  failPrinterRead=true;await page.reload();await idle();assert.equal(await page.locator('#key-panel').isVisible(),true);assert.ok(await page.evaluate(key=>sessionStorage.getItem('print-appliance.client.api-key')===key,credential));failPrinterRead=false;await page.locator('#key-form button').click();await idle();assert.equal(await page.locator('#client-workspace').isVisible(),true);
  rejectKey=true;await page.locator('#refresh-client').click();await idle();assert.equal(await page.locator('#key-panel').isVisible(),true);assert.equal(await page.evaluate(()=>sessionStorage.getItem('print-appliance.client.api-key')),null);rejectKey=false;
  await connect();rejectKey=true;await page.reload();await idle();assert.equal(await page.locator('#key-panel').isVisible(),true);assert.equal(await page.evaluate(()=>sessionStorage.getItem('print-appliance.client.api-key')),null);rejectKey=false;
  await page.reload();assert.equal(await page.locator('#api-key').inputValue(),'');assert.equal(await page.locator('#key-panel').isVisible(),true);
  await page.evaluate(()=>sessionStorage.setItem('print-appliance.client.api-key',' '));await page.reload();assert.equal(await page.locator('#key-panel').isVisible(),true);assert.equal(await page.evaluate(()=>sessionStorage.getItem('print-appliance.client.api-key')),null);
  const blocked=await browser.newPage();blocked.on('pageerror',e=>errors.push(e.message));await blocked.addInitScript(()=>Object.defineProperty(window,'sessionStorage',{get(){throw new DOMException('Disabled','SecurityError');}}));await blocked.route(`${base}/api/v1/**`,handler);await blocked.goto(base+'/client');await blocked.locator('#api-key').fill(credential);await blocked.locator('#key-form button').click();await blocked.waitForFunction(()=>!document.querySelector('#disconnect').disabled);assert.equal(await blocked.locator('#client-workspace').isVisible(),true);assert.match(await blocked.locator('#client-notice').textContent(),/chặn lưu/);await blocked.reload();assert.equal(await blocked.locator('#key-panel').isVisible(),true);await blocked.close();
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  console.log('PASS: client key authentication/tab session/reload/disconnect/revocation/storage-denied; scoped printer schemas/defaults/grants; PDF/ZPL separation; slow/duplicate guard; lost reply immutable same-ID replay; schema switching/unavailable; safe text; desktop/mobile; no external requests. APIs mocked; no printing.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
