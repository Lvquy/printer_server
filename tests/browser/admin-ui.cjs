// Loopback-only regression: every admin API is mocked, never physical printing.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const key=require('node:crypto').randomBytes(32).toString('hex');
const base=process.env.UI_BASE_URL||'http://127.0.0.1:18081';assert.ok(['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname));
const printer={id:'p-one',name:'Canon văn phòng',queue:'pa_test',managed:true,paused:false,formats:['pdf'],device_uri:'socket://192.0.2.21:9100',driver:'test/canon.ppd',default_options:{},allowed_options:{}};
const drivers=Array.from({length:16362},(_,i)=>({id:`test/model-${i}.ppd`,make_model:`Sample ${i}`}));drivers.push({id:printer.driver,make_model:'Canon LBP6230 UFRII LT'},{id:'test/accent.ppd',make_model:'Máy Thử nghiệm'},{id:'test/unsafe.ppd',make_model:'<img src="https://invalid.example/">'});
const schema={availability:'available',source:'ppd',schema_fingerprint:'schema-one',mapping_fingerprint:'mapping-one',constraints:[],options:[{name:'PageSize',group:'common',default:'A4',choices:[{value:'A4',label:'A4'},{value:'A5',label:'A5'}]},{name:'CustomMode',label:'<img src="https://invalid.example/">',group:'advanced',default:'Off',choices:[{value:'Off',label:'Off'},{value:'On',label:'On'}]}]};
(async()=>{const browser=await chromium.launch({headless:true,...(process.env.CHROME_EXECUTABLE?{executablePath:process.env.CHROME_EXECUTABLE}:{})});try{
 const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[],external=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith(base+'/'))external.push(r.url());});
 const state={printers:[{...printer},{...printer,id:'p-two',name:'Máy kho',queue:'pa_second'}],clients:[],writes:[],delay:0,fail:0,reads:0};
 let approve=false;page.on('dialog',d=>approve?d.accept():d.dismiss());
 const handler=async route=>{
  const req=route.request(),p=new URL(req.url()).pathname,method=req.method();let data,status=200;
  if(method!=='GET'){
   const body=req.postDataJSON();state.writes.push({p,method,body});if(state.delay)await new Promise(r=>setTimeout(r,state.delay));
   if(state.fail){status=state.fail;state.fail=0;data={detail:status===409?'Resource has nonterminal jobs':'CUPS unavailable'};}
   else if(p==='/admin/api/printers'){data={...printer,...body,id:'created',queue:'pa_created'};state.printers.push(data);}
   else if(p.startsWith('/admin/api/printers/')&&method==='PUT'){data=state.printers.find(x=>x.id===p.split('/').at(-1));Object.assign(data,body);}
   else if(p.startsWith('/admin/api/printers/')&&method==='DELETE'){state.printers=state.printers.filter(x=>x.id!==p.split('/').at(-1));data={deleted:true};}
   else if(p==='/admin/api/clients'){data={id:'client-created',...body,revoked:false,api_key:key};state.clients.push({...data});}
   else if(p.startsWith('/admin/api/clients/')&&method==='PUT'){data=state.clients.find(x=>x.id===p.split('/').at(-1));Object.assign(data,body);}
   else if(p.startsWith('/admin/api/clients/')&&method==='DELETE'){state.clients=state.clients.filter(x=>x.id!==p.split('/').at(-1));data={deleted:true};}
   else if(p==='/admin/api/settings')data=body;
   else if(p==='/admin/api/logout')data={ok:true};
   else throw Error(`Unexpected write ${method} ${p}`);
  }else if(p==='/admin/api/session')data={authenticated:true,csrf_token:'mock'};
  else if(p==='/admin/api/status')data={cups:{available:true},disk:{free_bytes:500000000,total_bytes:1000000000},jobs:{queued:0,held:0,submitted:0,unknown:0}};
  else if(p==='/admin/api/printers')data=state.printers;
  else if(p.endsWith('/capabilities')){state.reads++;data={...schema,printer_id:p.split('/')[4]};}
  else if(p.startsWith('/admin/api/printers/'))data=state.printers.find(x=>x.id===p.split('/').at(-1));
  else if(p==='/admin/api/clients')data=state.clients;
  else if(p.startsWith('/admin/api/clients/'))data=state.clients.find(x=>x.id===p.split('/').at(-1));
  else if(p==='/admin/api/jobs')data=[];
  else if(p==='/admin/api/settings')data={max_upload_bytes:10485760,max_pending_jobs:100,min_free_bytes:104857600,history_retention_days:30};
  else if(p==='/admin/api/discovery'){await new Promise(r=>setTimeout(r,200));data={devices:[{uri:printer.device_uri,info:'Canon mock'}],drivers,queues:{existing:{}}};}
  else throw Error(`Unexpected read ${p}`);
  await route.fulfill({status,json:data});
 };await page.route(`${base}/admin/api/**`,handler);
 const idle=()=>page.locator('#busy-status').waitFor({state:'hidden'}),modal=page.locator('#entity-modal');
 await page.goto(base);await page.locator('#printers article[data-id]').first().waitFor({state:'attached'});await idle();
 assert.equal(await page.getByRole('tab').count(),6);await page.getByRole('tab',{name:'Máy in',exact:true}).click();await page.keyboard.press('ArrowRight');assert.equal(await page.locator('#tab-clients').getAttribute('aria-selected'),'true');await page.keyboard.press('End');assert.equal(await page.locator('#tab-api-guide').getAttribute('aria-selected'),'true');await page.keyboard.press('Home');
 await page.getByRole('tab',{name:'Máy in',exact:true}).click();await page.reload();await idle();
 await page.locator('#discover').click();assert.equal(await page.locator('#discover').isDisabled(),true);await idle();assert.equal(await modal.isVisible(),true);assert.equal(await page.locator('#import-form').isVisible(),false);
 const search=page.locator('#driver-search'),hidden=page.locator('#driver');await search.click();assert.ok(await page.locator('#driver-list [role="option"]').count()<=200);assert.equal(await hidden.inputValue(),'');
 await search.fill('CANON 6230');assert.equal(await page.locator('#driver-list [role="option"]').count(),1);await search.press('Enter');assert.equal(await hidden.inputValue(),'');assert.equal(state.writes.length,0);await search.press('ArrowDown');await search.press('Enter');assert.equal(await hidden.inputValue(),printer.driver);
 await search.fill('not-real');assert.equal(await hidden.inputValue(),'');assert.equal(await search.evaluate(x=>x.validity.valid),false);await search.press('Escape');assert.equal(await hidden.inputValue(),printer.driver);
 await search.fill('may thu');assert.equal(await page.locator('#driver-list [role="option"]').count(),1);await search.fill('test/unsafe');assert.equal(await modal.locator('img').count(),0);
 await search.fill('canon');await search.press('End');await search.press('Enter');await page.locator('#driver-picker').getByRole('button',{name:'Xóa lựa chọn driver'}).click();assert.equal(await hidden.inputValue(),'');await search.fill('canon');await page.locator('#driver-list [role="option"]').click();
 const help=modal.getByRole('button',{name:'Giải thích: Driver',exact:true});await help.click();await page.waitForFunction(()=>!!document.querySelector('.help-popover:popover-open'));await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('.help-popover:popover-open'));assert.equal(await modal.isVisible(),true);
 await page.locator('#import-printer-mode').click();assert.equal(await page.locator('#import-form').isVisible(),true);await page.locator('#new-printer-mode').click();
 await page.locator('#printer-name').fill('Máy kiểm thử');await page.locator('#device-uri').selectOption(printer.device_uri);state.delay=400;state.fail=503;
 const create=page.locator('#managed-printer-form button[type="submit"]');await create.click();assert.equal(await create.isDisabled(),true);await page.locator('#managed-printer-form').evaluate(f=>f.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));await idle();assert.equal(state.writes.length,1);assert.equal(await search.isDisabled(),false);assert.match(await page.locator('#notice').textContent(),/CUPS unavailable/);await page.locator('#notice .toast-close').click();assert.equal(await page.locator('#notice .toast').count(),0);
 await modal.getByRole('button',{name:'Đóng modal'}).click();assert.equal(await modal.isVisible(),true);approve=true;await create.click();await idle();assert.equal(await modal.isVisible(),false);assert.equal(state.writes.length,2);assert.equal(state.writes.at(-1).body.driver,printer.driver);
 const card=page.locator('#printers article[data-id="p-one"]');await card.getByRole('button',{name:'Xem',exact:true}).click();await idle();assert.match(await modal.textContent(),/pa_test/);await modal.getByRole('button',{name:'Đóng modal'}).click();
 await card.getByRole('button',{name:'Sửa cấu hình'}).click();await modal.locator('.option-common select').waitFor();assert.equal(state.reads,1);assert.equal(await modal.locator('img').count(),0);
 await modal.locator('.option-common select').selectOption('A5');await modal.getByRole('button',{name:'Lưu cấu hình',exact:true}).click();await idle();assert.deepEqual(state.writes.at(-1).body.default_options,{PageSize:'A5'});assert.deepEqual(state.writes.at(-1).body.allowed_options,{PageSize:['A5']});assert.ok(!Object.hasOwn(state.writes.at(-1).body,'driver'));
 await card.getByRole('button',{name:'Sửa cấu hình'}).click();await modal.locator('input').first().fill('Chưa lưu');await modal.getByRole('button',{name:'Cập nhật danh sách driver'}).click();await idle();assert.equal(await modal.locator('input').first().inputValue(),'Chưa lưu');await modal.getByRole('button',{name:'Hủy',exact:true}).click();
 const remove=page.locator('#printers article[data-id="created"]').getByRole('button',{name:'Gỡ đăng ký'});approve=false;const before=state.writes.length;await remove.click();await idle();assert.equal(state.writes.length,before);approve=true;state.fail=409;await remove.click();await idle();assert.equal(await remove.isVisible(),true);await remove.click();await idle();assert.equal(await page.locator('#printers article[data-id="created"]').count(),0);
 await page.getByRole('tab',{name:'Clients',exact:true}).click();assert.equal(await page.locator('#client-form').isVisible(),false);await page.locator('#add-client').click();await page.locator('#client-name').fill('Odoo kiểm thử');
 await page.locator('#client-printers-toggle').click();await modal.getByRole('searchbox',{name:'Tìm máy được cấp'}).fill('may kho');await modal.getByRole('checkbox',{name:'Máy kho',exact:true}).check();await modal.getByRole('searchbox',{name:'Tìm máy được cấp'}).fill('');await modal.getByRole('checkbox',{name:'Canon văn phòng',exact:true}).check();assert.equal(await modal.locator('.grant-chip').count(),2);await page.keyboard.press('Escape');assert.equal(await modal.isVisible(),true);await modal.getByRole('button',{name:'Bỏ cấp: Máy kho'}).click();assert.equal(await modal.locator('.grant-chip').count(),1);
 await page.locator('#client-printers-toggle').click(); // Submit while dropdown remains open: must not shift under the pointer.
 await page.locator('#client-form button[type="submit"]').click();await idle();assert.deepEqual(state.writes.at(-1).body.printer_ids,['p-one']);assert.equal(await modal.locator('textarea[aria-label="API key mới"]').inputValue(),key);assert.ok(!(await page.locator('#notice').textContent()).includes(key));await modal.getByRole('button',{name:'Đóng modal'}).click();assert.equal(await page.locator('textarea[aria-label="API key mới"]').count(),0);
 const client=page.locator('#clients article');await client.getByRole('button',{name:'Sửa client'}).click();state.clients[0].printer_ids=[];await modal.locator('input').first().fill('Client đã sửa');await modal.getByRole('button',{name:'Lưu client'}).click();await idle();assert.ok(!Object.hasOwn(state.writes.at(-1).body,'printer_ids'));assert.deepEqual(state.clients[0].printer_ids,[]);
 await client.getByRole('button',{name:'Sửa client'}).click();await modal.locator('.grant-toggle').click();await modal.getByRole('searchbox',{name:'Tìm máy được cấp'}).press('ArrowDown');await page.keyboard.press('Space');await page.keyboard.press('Escape');await modal.getByRole('button',{name:'Lưu client'}).click();await idle();assert.deepEqual(state.writes.at(-1).body.printer_ids,['p-one']);
 state.fail=409;await client.getByRole('button',{name:'Xóa client'}).click();await idle();assert.equal(await client.count(),1);await client.getByRole('button',{name:'Xóa client'}).click();await idle();assert.equal(await page.locator('#clients article').count(),0);
 await page.getByRole('tab',{name:'Cấu hình',exact:true}).click();await page.locator('[name="max_pending_jobs"]').fill('111');await page.locator('#settings-form button[type="submit"]').click();await idle();assert.equal(state.writes.at(-1).body.max_pending_jobs,111);
 await page.mouse.move(20,500);await page.waitForFunction(()=>!document.querySelector('#notice .toast'),{},{timeout:7000});
 await page.getByRole('tab',{name:'Clients',exact:true}).click();await page.locator('#add-client').click();await page.locator('#client-printers-toggle').click();
 if(process.env.UI_ARTIFACT_DIR){fs.mkdirSync(process.env.UI_ARTIFACT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.UI_ARTIFACT_DIR,'admin-desktop.png'),fullPage:true});}
 await modal.getByRole('button',{name:'Đóng modal'}).click();
 const mobile=await browser.newPage({viewport:{width:390,height:844}});await mobile.route(`${base}/admin/api/**`,handler);await mobile.goto(base+'/#tab=clients');await mobile.locator('#busy-status').waitFor({state:'hidden'});await mobile.locator('#add-client').click();await mobile.locator('#client-printers-toggle').click();
 assert.ok(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.ok(await mobile.locator('#entity-modal').evaluate(x=>x.scrollWidth<=x.clientWidth+1));
 if(process.env.UI_ARTIFACT_DIR)await mobile.screenshot({path:path.join(process.env.UI_ARTIFACT_DIR,'admin-mobile.png')});await mobile.close();
 await page.locator('#logout').click();await idle();assert.equal(await page.locator('#dashboard').isVisible(),false);assert.equal(await page.locator('#notice').isVisible(),false);assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 console.log('PASS: tabs/16k driver/help/create-import/busy/errors; modal view/edit/dirty/focus; schema defaults+rights; multi-grant search/chips/keyboard; concurrent revocation; key modal; CRUD; toast; mobile; logout. All APIs mocked.');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1);});
