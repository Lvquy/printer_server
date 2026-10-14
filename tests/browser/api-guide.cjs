// Loopback-only reference checks. All admin reads mocked; client calls/writes forbidden.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const { spawnSync } = require('node:child_process');
const contract = require('../../frontend/src/client-api.json');
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:18081';
assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname));
const taskNames = { printers: 'Chọn máy in', submit: 'Gửi file', job: 'Theo dõi lệnh', jobs: 'Xem lịch sử', cancel: 'Hủy lệnh' };
(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_EXECUTABLE ? { executablePath: process.env.CHROME_EXECUTABLE } : {}) });
  try {
    const errors = [], clientRequests = [], writes = [], external = [];
    let reads = 0;
    const handler = async route => {
      const request = route.request(), p = new URL(request.url()).pathname;
      if (!request.url().startsWith(base + '/')) { external.push(request.url()); return route.abort(); }
      if (p.startsWith('/api/v1/')) { clientRequests.push(p); return route.abort(); }
      if (request.method() !== 'GET') { writes.push(p); return route.abort(); }
      if (!p.startsWith('/admin/api/')) return route.continue();
      reads++;
      let data;
      if (p.endsWith('/session')) data = { authenticated: true, csrf_token: 'mock' };
      else if (p.endsWith('/status')) data = { cups: { available: true }, disk: { free_bytes: 1e9, total_bytes: 2e9 }, jobs: {} };
      else if (p.endsWith('/settings')) data = { max_upload_bytes: 10485760, max_pending_jobs: 100, min_free_bytes: 0, history_retention_days: 30 };
      else data = [];
      return route.fulfill({ json: data });
    };
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.on('pageerror', error => errors.push(error.message)); await page.route('**/*', handler);
    await page.goto(base); await page.locator('#busy-status').waitFor({ state: 'hidden' });
    const before = reads;
    await page.getByRole('tab', { name: 'API Guide', exact: true }).click();
    assert.equal(new URL(page.url()).hash, '#tab=api-guide');
    assert.equal(await page.locator('.api-task').count(), 5); assert.equal(await page.locator('.api-endpoint').count(), 1);
    assert.equal(await page.locator('.api-endpoint h3').textContent(), taskNames.printers);
    assert.equal(await page.locator('.api-disclosure[open]').count(), 0, 'Advanced content starts collapsed');
    assert.equal(await page.locator('.api-setup').getAttribute('open'), null);
    // All five operations remain documented, including full standalone schemas/errors.
    for (const endpoint of contract.endpoints) {
      await page.locator('.api-task').filter({ hasText: taskNames[endpoint.id] }).click();
      const panel = page.locator('.api-endpoint');
      assert.equal(await panel.locator('.api-route code').textContent(), endpoint.path);
      const curl = await panel.locator('pre[aria-label^="cURL "]').textContent();
      assert.match(curl, /Authorization: Bearer/); assert.ok(!curl.includes('--retry'));
      assert.equal(spawnSync('bash', ['-n'], { input: curl, encoding: 'utf8' }).status, 0);
      if (endpoint.method === 'GET' || endpoint.id === 'cancel') assert.ok(!curl.includes('-F '));
      await panel.getByText('Mã lỗi ·', { exact: false }).click();
      for (const error of endpoint.errors) assert.ok((await panel.locator('table').first().textContent()).includes(String(error.status)));
      assert.deepEqual(JSON.parse(await panel.locator('pre[aria-label="Ví dụ response lỗi"]').textContent()), { detail: 'Invalid API key' });
      await panel.getByText('Schema & chi tiết kỹ thuật', { exact: true }).click();
      if (endpoint.bodySchema) {
        const schema = JSON.parse(await panel.locator('pre[aria-label="JSON schema request"]').textContent());
        assert.deepEqual(schema.required, contract.$defs[endpoint.bodySchema].required);
      }
      for (const response of endpoint.responses) {
        const schema = JSON.parse(await panel.locator(`pre[aria-label="Schema response ${response.status}"]`).textContent());
        const checkRefs = value => {
          if (!value || typeof value !== 'object') return;
          if (value.$ref) assert.ok(schema.$defs[value.$ref.split('/').at(-1)]);
          Object.values(value).forEach(checkRefs);
        };
        checkRefs(schema);
        if (response.status === 200 && endpoint.id === 'submit') await panel.getByRole('button', { name: '200 · Gửi lại', exact: true }).click();
        assert.deepEqual(JSON.parse(await panel.locator(`pre[aria-label="Ví dụ response ${response.status}"]`).textContent()), response.example);
      }
    }
    await page.locator('.api-task').filter({ hasText: taskNames.submit }).click();
    const submit = page.locator('.api-endpoint'), curl = submit.locator('pre[aria-label="cURL POST /api/v1/jobs"]');
    assert.equal(await submit.locator('.api-field').count(), 10);
    for (const name of ['file', 'printer_id', 'format', 'title', 'copies', 'paper_size', 'sides', 'color_mode', 'scaling', 'Idempotency-Key']) assert.ok((await submit.locator('.api-field-list').textContent()).includes(name));
    assert.match(await submit.textContent(), /multipart\/form-data/); assert.match(await submit.textContent(), /có thể in thật/);
    await submit.getByLabel('Định dạng ví dụ').selectOption('zpl'); assert.match(await curl.textContent(), /format=zpl/); assert.match(await curl.textContent(), /label.zpl/);
    assert.equal(spawnSync('bash', ['-n'], { input: await curl.textContent(), encoding: 'utf8' }).status, 0);
    await submit.getByLabel('Định dạng ví dụ').selectOption('pdf'); assert.match(await curl.textContent(), /invoice.pdf/);
    const idCode = await submit.locator('pre[aria-label="Tạo REQUEST_ID một lần"]').textContent();
    assert.equal(spawnSync('bash', ['-n'], { input: idCode, encoding: 'utf8' }).status, 0);
    assert.ok(!(await curl.textContent()).includes('secrets.token_hex'), 'Retry example never regenerates request ID');
    await submit.getByRole('button', { name: '200 · Gửi lại', exact: true }).click(); assert.equal(JSON.parse(await submit.locator('pre[aria-label="Ví dụ response 200"]').textContent()).deduplicated, true);
    await submit.getByRole('button', { name: '202 · Lệnh mới', exact: true }).click();
    await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true }));
    await submit.getByRole('button', { name: 'Sao chép cURL POST /api/v1/jobs', exact: true }).click();
    assert.equal(await page.evaluate(() => getSelection().toString()), await curl.textContent());
    await page.evaluate(() => getSelection().removeAllRanges());
    await submit.getByRole('button', { name: 'Thiết lập cURL', exact: true }).click();
    assert.equal(await page.locator('.api-setup').getAttribute('open'), '');
    const setup = page.locator('pre[aria-label="Biến môi trường (thay placeholder trên máy client)"]');
    await page.getByLabel('Base URL', { exact: true }).fill('https://example.invalid'); assert.match(await setup.textContent(), /https:\/\/example.invalid/);
    assert.equal(spawnSync('bash', ['-n'], { input: await setup.textContent(), encoding: 'utf8' }).status, 0);
    await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { value: { writeText: async text => { window.copiedExample = text; } }, configurable: true }));
    await page.getByRole('button', { name: 'Sao chép Biến môi trường (thay placeholder trên máy client)', exact: true }).click();
    assert.equal(await page.evaluate(() => window.copiedExample), await setup.textContent());
    for (const invalid of ['http://user:secret@example.invalid', 'javascript:alert(1)', 'https://example.invalid/path', 'https://example.invalid/?key=x', 'https://example.invalid/#x']) {
      await page.getByLabel('Base URL', { exact: true }).fill(invalid);
      await page.getByRole('alert').filter({ hasText: 'Nhập origin' }).waitFor();
      assert.equal(await page.getByRole('button', { name: 'Sao chép Biến môi trường (thay placeholder trên máy client)', exact: true }).isDisabled(), true);
      assert.equal(await submit.getByRole('button', { name: 'Sao chép cURL POST /api/v1/jobs', exact: true }).isDisabled(), true);
    }
    await page.getByLabel('Base URL', { exact: true }).fill(base); await page.locator('.api-setup > summary').click();
    const save = async (p, name) => { if (process.env.UI_ARTIFACT_DIR) { fs.mkdirSync(process.env.UI_ARTIFACT_DIR, { recursive: true }); await p.screenshot({ path: path.join(process.env.UI_ARTIFACT_DIR, name), fullPage: true }); } };
    await save(page, 'api-guide-desktop.png');
    for (const width of [768, 390, 320]) { await page.setViewportSize({ width, height: 844 }); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true); }
    await page.getByLabel('Tìm API', { exact: true }).fill('huy'); assert.equal(await page.locator('.api-task').count(), 1); assert.match(await page.locator('.api-route code').textContent(), /\/cancel$/);
    await page.getByLabel('Tìm API', { exact: true }).fill('no-match'); assert.equal(await page.locator('.api-task').count(), 0); assert.equal(await page.locator('.api-endpoint').count(), 0); await page.getByRole('status').filter({ hasText: 'Không có API phù hợp' }).waitFor();
    assert.equal(reads, before, 'Admin guide interactions do not fetch');
    await page.reload(); await page.locator('#busy-status').waitFor({ state: 'hidden' }); assert.equal(await page.locator('#tab-api-guide').getAttribute('aria-selected'), 'true'); assert.equal(await page.locator('.api-task').count(), 5);
    // Client reference is accessible without credentials or any admin/client calls.
    const client = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    client.on('pageerror', error => errors.push(error.message)); await client.route('**/*', handler);
    const clientBefore = reads;
    await client.goto(base + '/client'); assert.equal(await client.locator('#key-panel').isVisible(), true);
    await client.getByRole('tab', { name: 'API Guide', exact: true }).click(); assert.equal(new URL(client.url()).hash, '#api-guide');
    assert.equal(await client.locator('#key-panel').isVisible(), false); assert.equal(await client.locator('.api-task').count(), 5);
    assert.deepEqual(await client.evaluate(() => ({ local: Object.keys(localStorage), tab: Object.keys(sessionStorage) })), { local: [], tab: [] });
    await client.locator('.api-task').filter({ hasText: taskNames.submit }).click();
    await save(client, 'client-api-guide-desktop.png');
    await client.reload(); assert.equal(await client.locator('#client-tab-api-guide').getAttribute('aria-selected'), 'true');
    await client.locator('#client-tab-api-guide').focus(); await client.keyboard.press('Home'); assert.equal(await client.locator('#key-panel').isVisible(), true);
    await client.keyboard.press('ArrowRight'); assert.equal(await client.locator('#client-tab-api-guide').getAttribute('aria-selected'), 'true');
    await client.keyboard.press('ArrowRight'); assert.equal(await client.locator('#client-tab-print').getAttribute('aria-selected'), 'true');
    await client.keyboard.press('End'); assert.equal(await client.locator('#client-tab-api-guide').getAttribute('aria-selected'), 'true');
    await client.evaluate(() => { location.hash = '#print'; }); await client.locator('#key-panel').waitFor({ state: 'visible' });
    const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
    mobile.on('pageerror', error => errors.push(error.message)); await mobile.route('**/*', handler);
    await mobile.goto(base + '/client#api-guide'); await mobile.locator('.api-task').filter({ hasText: taskNames.submit }).click();
    assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(await mobile.getByRole('button', { name: 'Tham số request · 10', exact: false }).getAttribute('aria-expanded'), 'false');
    assert.ok((await mobile.locator('.api-content').boundingBox()).y < 100, 'Mobile task selection scrolls to the request');
    await save(mobile, 'api-guide-mobile.png');
    await mobile.getByRole('button', { name: 'Tham số request · 10', exact: false }).click();
    assert.equal(await mobile.locator('.api-field:visible').count(), 10);
    await mobile.getByRole('button', { name: 'Tham số request · 10', exact: false }).click();
    await mobile.locator('.api-setup > summary').click(); await mobile.locator('.api-endpoint').getByText('Schema & chi tiết kỹ thuật', { exact: true }).click();
    await mobile.locator('.api-endpoint').getByText('Mã lỗi ·', { exact: false }).click(); assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(reads, clientBefore, 'Client guide never calls admin APIs');
    assert.deepEqual(errors, []); assert.deepEqual(clientRequests, []); assert.deepEqual(writes, []); assert.deepEqual(external, []);
    console.log('PASS: task-first API Guide; all five operations/fields/schemas/errors/responses; PDF/ZPL cURL syntax; immutable request ID; clipboard/fallback; safe base URL; search; admin/client hash/keyboard navigation; 320–1280px; zero client calls/writes/external requests.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
