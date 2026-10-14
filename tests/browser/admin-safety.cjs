// Loopback-only React migration regressions. Every API is mocked; no CUPS/printing.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:18081';
assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname));
const printer = { id: 'p-one', name: 'Test printer', queue: 'pa_test', managed: true, paused: false, formats: ['pdf'], device_uri: 'socket://192.0.2.21:9100', driver: 'test.ppd', default_options: { PageSize: 'A4', Vendor: 'opaque' }, allowed_options: { PageSize: ['A4'], Vendor: ['opaque', 'other'] } };
const schema = { availability: 'available', source: 'ppd', options: [{ name: 'PageSize', group: 'common', default: 'A4', choices: [{ value: 'A4', label: 'A4' }, { value: 'A5', label: 'A5' }] }], constraints: [] };
const job = { job_id: 'job-one', title: '<img src="https://invalid.example/">', format: 'pdf', copies: 1, client_name: 'Test client', printer_name: printer.name, status: 'unknown', accepted_at: '2026-10-06', printer_snapshot: { queue: 'pa_test' }, events: [{ message: 'No automatic reprint' }] };
(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_EXECUTABLE ? { executablePath: process.env.CHROME_EXECUTABLE } : {}) });
  try {
    const page = await browser.newPage(), errors = [], external = [], writes = [];
    let approve = true, expire = false, authenticated = false;
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (!request.url().startsWith(base + '/')) external.push(request.url()); });
    page.on('dialog', dialog => approve ? dialog.accept() : dialog.dismiss());
    await page.route(`${base}/admin/api/**`, async route => {
      const request = route.request(), p = new URL(request.url()).pathname, method = request.method();
      let data;
      if (method !== 'GET') {
        const body = request.postDataJSON(); writes.push({ p, method, body });
        if (p === '/admin/api/login') { authenticated = true; data = { csrf_token: 'mock-csrf' }; }
        else {
          assert.equal(request.headers()['x-csrf-token'], 'mock-csrf');
          if (p === '/admin/api/settings') data = body;
          else if (p === '/admin/api/printers/p-one') { Object.assign(printer, body); data = printer; }
          else if (p === '/admin/api/jobs/job-one/resolve') { job.status = body.outcome; data = job; }
          else if (p === '/admin/api/jobs/job-one/resume') { job.status = 'submitted'; data = job; }
          else if (p === '/admin/api/jobs/job-one/cancel') { job.status = 'canceled'; data = job; }
          else throw Error(`Unexpected mutation ${p}`);
        }
      } else if (p === '/admin/api/session') data = { authenticated, csrf_token: authenticated ? 'mock-csrf' : '' };
      else if (p === '/admin/api/printers') data = [printer];
      else if (p.endsWith('/capabilities')) data = schema;
      else if (p === '/admin/api/clients') { if (expire) await new Promise(resolve => setTimeout(resolve, 350)); data = [{ id: 'c-one', name: 'Stale client', revoked: false, printer_ids: ['p-one'] }]; }
      else if (p === '/admin/api/status') data = { cups: { available: true }, disk: { free_bytes: 1e9, total_bytes: 2e9 }, jobs: { queued: 0, held: 0, submitted: 0, unknown: 1 } };
      else if (p === '/admin/api/settings') data = { max_upload_bytes: 10485760, max_pending_jobs: 100, min_free_bytes: 104857600, history_retention_days: 30 };
      else if (p === '/admin/api/jobs') {
        if (expire) { authenticated = false; await route.fulfill({ status: 401, json: { detail: 'Expired session' } }); return; }
        data = [job];
      } else if (p === '/admin/api/jobs/job-one') data = job;
      else throw Error(`Unexpected read ${p}`);
      await route.fulfill({ json: data });
    });
    const idle = () => page.locator('#busy-status').waitFor({ state: 'hidden' });
    const modal = page.locator('#entity-modal');
    await page.goto(base); await idle();
    await page.locator('#password').fill('isolated-mock-password'); await page.locator('#login-form button[type="submit"]').click(); await idle();
    assert.equal(await page.locator('#password').inputValue(), '');
    await page.getByRole('tab', { name: 'Cấu hình', exact: true }).click(); await page.locator('[name="max_pending_jobs"]').fill('777');
    await page.getByRole('tab', { name: 'Lệnh in', exact: true }).click(); await page.locator('#refresh').click(); await idle();
    await page.getByRole('tab', { name: 'Cấu hình', exact: true }).click(); assert.equal(await page.locator('[name="max_pending_jobs"]').inputValue(), '777');
    await page.locator('#settings-form button[type="submit"]').click(); await idle(); assert.equal(writes.at(-1).body.max_pending_jobs, 777);
    await page.getByRole('tab', { name: 'Máy in', exact: true }).click(); const edit = page.locator('#printers').getByRole('button', { name: 'Sửa cấu hình' });
    await edit.click(); await modal.locator('.option-common select').waitFor(); await modal.locator('.option-common select').selectOption('A5');
    await modal.getByRole('button', { name: 'Lưu cấu hình', exact: true }).click(); await idle();
    assert.deepEqual(writes.at(-1).body.default_options, { Vendor: 'opaque', PageSize: 'A5' });
    assert.deepEqual(writes.at(-1).body.allowed_options, { Vendor: ['opaque', 'other'], PageSize: ['A4', 'A5'] });
    await edit.click(); await modal.locator('.option-common select').waitFor(); await modal.getByRole('checkbox', { name: 'Xóa các giá trị cũ không còn trong schema khi lưu' }).check();
    await modal.locator('.option-common select').selectOption('A4'); await modal.getByRole('button', { name: 'Lưu cấu hình', exact: true }).click(); await idle();
    assert.deepEqual(writes.at(-1).body.default_options, { PageSize: 'A4' }); assert.deepEqual(writes.at(-1).body.allowed_options, { PageSize: ['A4', 'A5'] });
    await edit.click(); await modal.locator('.option-common select').waitFor(); await modal.locator('#edit-uri-p-one').fill('socket://192.0.2.22:9100');
    approve = false; const before = writes.length; await modal.getByRole('button', { name: 'Lưu cấu hình', exact: true }).click(); await idle(); assert.equal(writes.length, before);
    approve = true; await modal.getByRole('button', { name: 'Lưu cấu hình', exact: true }).click(); await idle();
    assert.deepEqual(writes.at(-1).body.default_options, {}); assert.deepEqual(writes.at(-1).body.allowed_options, {}); assert.equal(writes.at(-1).body.device_uri, 'socket://192.0.2.22:9100');
    await page.getByRole('tab', { name: 'Lệnh in', exact: true }).click(); const view = page.locator('#jobs').getByRole('button', { name: 'Xem', exact: true });
    await view.click(); await idle(); assert.equal(await modal.locator('img').count(), 0); assert.equal(await modal.getByRole('button', { name: 'Tiếp tục riêng lệnh này' }).count(), 0);
    await modal.locator('#resolve-reason').fill('Checked physical output and CUPS evidence'); await modal.getByRole('button', { name: 'Ghi nhận xác minh (không gửi lại)' }).click(); await idle();
    assert.equal(writes.at(-1).p, '/admin/api/jobs/job-one/resolve'); assert.equal(writes.at(-1).body.outcome, 'completed');
    await modal.getByRole('button', { name: 'Đóng modal' }).click(); assert.ok(await view.evaluate(element => element === document.activeElement));
    job.status = 'held'; await page.locator('#refresh').click(); await idle(); await view.click(); await idle(); await modal.getByRole('button', { name: 'Tiếp tục riêng lệnh này' }).click(); await idle();
    assert.equal(writes.at(-1).p, '/admin/api/jobs/job-one/resume'); await modal.getByRole('button', { name: 'Hủy lệnh', exact: true }).click(); await idle(); assert.equal(writes.at(-1).p, '/admin/api/jobs/job-one/cancel');
    await modal.getByRole('button', { name: 'Đóng modal' }).click();
    expire = true; await page.locator('#refresh').click(); await idle(); assert.equal(await page.locator('#dashboard').isVisible(), false); await page.waitForTimeout(450);
    assert.equal(await page.locator('#printers article, #clients article, #jobs tbody tr').count(), 0); assert.equal(await modal.count(), 0); assert.equal(await page.locator('#password').inputValue(), '');
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    console.log('PASS: React admin auth/CSRF; dirty settings refresh; legacy defaults/rights preservation and explicit removal; remap confirmation/reset; unknown resolve/manual resume/cancel; focus restoration; 401 invalidation rejects stale data. All APIs mocked.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
