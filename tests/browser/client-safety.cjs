// Loopback-only uncertain-submission/session regressions; APIs mocked, never printing.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict'), crypto = require('node:crypto');
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:18081';
assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname));
const credential = crypto.randomBytes(32).toString('hex');
(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_EXECUTABLE ? { executablePath: process.env.CHROME_EXECUTABLE } : {}) });
  try {
    const page = await browser.newPage(), errors = [], external = [], writes = [], reads = [];
    let status = 503, delay = 0;
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (!request.url().startsWith(base + '/')) external.push(request.url()); });
    await page.route(`${base}/api/v1/**`, async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      assert.equal(request.headers().authorization, 'Bearer ' + credential);
      if (request.method() === 'GET') reads.push(path);
      if (path === '/api/v1/printers') return route.fulfill({ json: [{ id: 'p-one', name: 'Isolated printer', formats: ['pdf'], status: 'ready', capabilities: {availability:'available',supported:{},defaults:{}} }] });
      if (request.method() === 'GET') return route.fulfill({ json: [] });
      assert.equal(path, '/api/v1/jobs');
      writes.push({ id: request.headers()['idempotency-key'], body: request.postDataBuffer().toString() });
      if (delay) await new Promise(resolve => setTimeout(resolve, delay));
      return route.fulfill({ status, json: status === 200 ? { job_id: 'j-one', status: 'queued', deduplicated: true } : { detail: 'Isolated failure ' + status } });
    });
    const idle = () => page.waitForFunction(() => !document.querySelector('#disconnect').disabled);
    const connect = async () => {
      await page.locator('#api-key').fill(credential); await page.locator('#key-form button[type="submit"]').click(); await idle();
      await page.locator('#options-state').getByText(/Chọn tùy chọn phổ thông/).waitFor();
    };
    const chooseFile = () => page.locator('#client-file').setInputFiles({ name: 'isolated.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.7\nOriginal immutable content') });
    await page.goto(base + '/client'); await connect(); await chooseFile(); await page.locator('#client-title').fill('Immutable title'); await page.locator('#client-copies').fill('2');
    const beforeGuide = reads.length;
    await page.getByRole('tab', { name: 'API Guide', exact: true }).click();
    assert.equal(await page.locator('.api-task').count(), 5); assert.equal(writes.length, 0); assert.equal(reads.length, beforeGuide);
    assert.ok(!(await page.locator('.api-reference').textContent()).includes(credential), 'Connected key never enters reference examples');
    await page.getByRole('tab', { name: 'In tài liệu', exact: true }).click();
    assert.equal(await page.locator('#client-file').evaluate(element => element.files[0].name), 'isolated.pdf');
    assert.equal(await page.locator('#client-title').inputValue(), 'Immutable title'); assert.equal(await page.locator('#client-copies').inputValue(), '2');
    delay = 600; await page.locator('#send-print').click(); assert.equal(await page.locator('#send-print').isDisabled(), true);
    // Navigation and reference controls work while a submission is outstanding.
    await page.getByRole('tab', { name: 'API Guide', exact: true }).click();
    await page.locator('.api-task').filter({ hasText: 'Gửi file' }).click();
    await page.locator('.api-endpoint').getByLabel('Định dạng ví dụ').selectOption('zpl');
    await page.getByRole('button', { name: 'Quay lại kiểm tra lệnh in', exact: true }).click();
    await idle(); delay = 0; assert.equal(writes.length, 1); assert.equal(await page.locator('#uncertain-request').isVisible(), true);
    const id = writes[0].id; assert.equal(await page.locator('#client-title').isDisabled(), true);
    await page.getByRole('tab', { name: 'API Guide', exact: true }).click();
    await page.getByRole('status').filter({ hasText: 'chưa xác nhận kết quả' }).waitFor();
    await page.locator('.api-task').filter({ hasText: 'Gửi file' }).click();
    await page.locator('.api-endpoint').getByLabel('Định dạng ví dụ').selectOption('zpl');
    assert.equal(writes.length, 1); assert.equal(reads.length, beforeGuide);
    await page.getByRole('button', { name: 'Quay lại kiểm tra lệnh in', exact: true }).click();
    assert.equal(await page.locator('#request-id').textContent(), id);
    assert.equal(await page.locator('#client-file').evaluate(element => element.files[0].name), 'isolated.pdf');
    // DOM tampering after uncertainty must not change the stored request on explicit replay.
    await page.locator('#client-title').evaluate(element => { element.value = 'Changed title'; });
    status = 408; await page.locator('#retry-print').click(); await idle(); assert.equal(writes[1].id, id); assert.ok(writes[1].body.includes('Immutable title')); assert.ok(!writes[1].body.includes('Changed title')); assert.equal(await page.locator('#uncertain-request').isVisible(), true);
    await page.waitForTimeout(800); assert.equal(writes.length, 2, 'No automatic mutation retry');
    status = 401; await page.locator('#retry-print').click(); await idle(); assert.equal(writes[2].id, id); assert.equal(await page.locator('#client-workspace').isVisible(), false); assert.equal(await page.evaluate(() => sessionStorage.getItem('print-appliance.client.api-key')), null); assert.equal(await page.locator('#uncertain-request').isVisible(), false); assert.equal(await page.locator('#client-file').inputValue(), '');
    await connect(); await chooseFile(); status = 503; await page.locator('#send-print').click(); await idle(); assert.notEqual(writes[3].id, id);
    // BFCache restoration keeps only the tab credential, never the pending file/request.
    await page.evaluate(() => { dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })); dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })); });
    await idle(); await page.locator('#options-state').getByText(/Chọn tùy chọn phổ thông/).waitFor(); assert.equal(writes.length, 4); assert.equal(await page.locator('#client-file').inputValue(), ''); assert.equal(await page.locator('#uncertain-request').isVisible(), false); assert.equal(await page.locator('#client-workspace').isVisible(), true);
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    console.log('PASS: client guide preserves chosen file/options/pending ID without reads, writes or key leakage; 503/408 immutable same-ID explicit retries; no auto resend; submission 401 clears credential/file/pending; BFCache restores credential only. APIs mocked; no printing.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
