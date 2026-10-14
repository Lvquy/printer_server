// Real loopback FastAPI/SQLite, disposable credentials, explicit FakeCups; never printing.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict'), fs = require('node:fs'), crypto = require('node:crypto'), path = require('node:path');
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:18086';
assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname));
assert.ok(process.env.UI_TEST_FIXTURE, 'Start python -m tests.browser.serve_password; use its printed fixture path');
const fixture = JSON.parse(fs.readFileSync(process.env.UI_TEST_FIXTURE));
assert.equal(fixture.test_only, true); assert.equal(fixture.worker_enabled, false);
(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_EXECUTABLE ? { executablePath: process.env.CHROME_EXECUTABLE } : {}) });
  try {
    const context = await browser.newContext(), otherContext = await browser.newContext();
    const page = await context.newPage(), other = await otherContext.newPage(), errors = [], writes = [];
    page.on('pageerror', e => errors.push(e.message));
    await context.route('**/*', route => {
      const request = route.request(); assert.ok(request.url().startsWith(base + '/'), 'No external resources');
      assert.ok(!request.url().includes(fixture.password), 'No password in URL');
      if (request.method() !== 'GET') {
        assert.ok(['/admin/api/login', '/admin/api/password'].includes(new URL(request.url()).pathname), 'No print/config mutations');
        if (request.url().endsWith('/password')) writes.push(request.postDataJSON());
      }
      return route.continue();
    });
    const idle = p => p.locator('#busy-status').waitFor({ state: 'hidden' });
    const login = async (p, password) => {
      await p.goto(base); await idle(p); await p.locator('#password').fill(password);
      await p.locator('#login-form button[type="submit"]').click(); await idle(p);
    };
    const form = page.locator('#change-password-form');
    const fill = async (current, next, confirm = next) => {
      await form.locator('[name="current_password"]').fill(current);
      await form.locator('[name="new_password"]').fill(next);
      await form.locator('[name="confirm_password"]').fill(confirm);
    };
    await login(page, fixture.password); await login(other, fixture.password);
    await page.getByRole('tab', { name: 'Cấu hình', exact: true }).click();
    const next = 'Isolated-new-' + crypto.randomBytes(16).toString('hex');
    await fill(fixture.password, next, 'Mismatch-but-long'); await form.getByRole('button', { name: 'Đổi mật khẩu', exact: true }).click();
    assert.equal(writes.length, 0); assert.match(await page.locator('#change-password-error').textContent(), /không khớp/);
    await fill(fixture.password, fixture.password); await form.getByRole('button', { name: 'Đổi mật khẩu', exact: true }).click(); assert.equal(writes.length, 0);
    await fill('incorrect-current', next); await form.getByRole('button', { name: 'Đổi mật khẩu', exact: true }).click(); await idle(page);
    assert.equal(writes.length, 1); assert.match(await page.locator('#change-password-error').textContent(), /hiện tại không đúng/); assert.equal(await page.locator('#dashboard').isVisible(), true);
    assert.equal(await form.locator('[name="current_password"]').inputValue(), '');
    // Passwords are destroyed when leaving the settings tab, not restored by refresh/storage.
    await fill(fixture.password, next); await page.getByRole('tab', { name: 'Lệnh in', exact: true }).click(); await page.getByRole('tab', { name: 'Cấu hình', exact: true }).click();
    assert.equal(await form.locator('[name="new_password"]').inputValue(), '');
    await page.setViewportSize({ width: 390, height: 844 }); await fill(fixture.password, next);
    if (process.env.UI_ARTIFACT_DIR) { fs.mkdirSync(process.env.UI_ARTIFACT_DIR, { recursive: true }); await page.screenshot({ path: path.join(process.env.UI_ARTIFACT_DIR, 'password-mobile.png'), fullPage: true }); }
    await form.getByRole('button', { name: 'Đổi mật khẩu', exact: true }).click(); await idle(page);
    assert.equal(writes.length, 2); assert.equal(await page.locator('#dashboard').isVisible(), false); assert.equal(await form.count(), 0); assert.equal(await page.locator('#password').inputValue(), '');
    assert.match(await page.locator('#notice').textContent(), /Đã đổi mật khẩu/);
    assert.equal((await context.cookies()).some(cookie => cookie.name === 'pa_admin'), false);
    await other.getByRole('tab', { name: 'Lệnh in', exact: true }).click(); await other.locator('#refresh').click(); await idle(other); assert.equal(await other.locator('#dashboard').isVisible(), false);
    await login(page, fixture.password); assert.equal(await page.locator('#dashboard').isVisible(), false);
    await login(page, next); assert.equal(await page.locator('#dashboard').isVisible(), true);
    assert.deepEqual(await page.evaluate(() => ({ local: { ...localStorage }, tab: { ...sessionStorage } })), { local: {}, tab: {} });
    assert.deepEqual(errors, []);
    console.log('PASS: real isolated FastAPI password form; mismatch/same-password validation, incorrect current recovery, secret clearing, desktop/mobile, all sessions revoked, old rejected/new accepted; no storage or print requests.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
