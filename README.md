# Print appliance (v1 implementation)

**Hướng dẫn triển khai (tiếng Việt):** [Markdown](docs/technical-report.md) · [HTML offline](docs/technical-report.html) · [PDF](docs/technical-report.pdf) · [Sơ đồ draw.io](docs/diagrams/connection.drawio). Dành cho kỹ thuật viên: kiến trúc, yêu cầu, cài đặt trên Debian 12, cấu hình và in thử, tích hợp API, vận hành cơ bản.

### Cài mới trên Debian 12

```sh
git clone https://github.com/Lvquy/printer_server.git
cd printer_server
sudo ./install.sh
sudo systemctl start print-appliance
```

Script hỏi IP thiết bị, cài thư viện và đăng ký dịch vụ tự khởi động.
Mật khẩu web ban đầu: `admin12345678` — đổi ngay tại **Cấu hình → Đổi mật khẩu quản trị**.
Chỉ dùng HTTP trong LAN tin cậy. Script từ chối bản cài có sẵn; không dùng để cập nhật.
Driver máy in cài riêng. Xem hướng dẫn triển khai ở trên để biết yêu cầu và kiểm tra.


A local, single-node print appliance using FastAPI, SQLite, a durable file spool and the system's **real CUPS service**. It is a new service on port 8081 by default; it does not interact with or replace `pi-print-gateway` on 8080. Odoo and other applications are clients, not dependencies of this service.

All IP addresses/hostnames in documentation are examples, not authoritative device addresses. Obtain the actual LAN URL from the appliance administrator. `127.0.0.1` is loopback on the machine running the command, not the Pi's LAN address.

## Development: local-only, no printer connection

Requires `uv` and Python 3.11+. The checked-in lockfile is `uv.lock`.

```sh
cd /Users/lvquy/dev/printer_server
uv sync --python 3.11 --extra dev
uv run pytest
uv run ruff check .
```

The application default listener is `127.0.0.1:8081`; do not change it to a LAN/public address during local tests. Tests inject an explicit in-memory fake CUPS backend; the executable does not offer a fake mode. Without pycups/CUPS, production status reports unavailable and accepted work is never reported as printed.

For local manual UI smoke testing without any registered printers or real CUPS, bootstrap an admin password and run the app; the actual CUPS panel will correctly show unavailable:

```sh
uv run print-appliance admin-password --data-dir ./.local-data
uv run print-appliance run --data-dir ./.local-data
# open http://127.0.0.1:8081
```

The password is prompted twice and must be at least 12 characters. There is no default credential. Keep `.local-data` private and remove it when finished. Do not use a development data directory for a production service.

## React UI development (build machine only)

Both `/` (admin) and `/client` use React components in `frontend/src/`. The existing Vietnamese interface and CSS are retained. Node.js 20+ and npm are needed **only when changing the UI**, not on the appliance:

```sh
npm ci
npm run build
npm run build:check
```

The pinned `package-lock.json` makes the build reproducible. Commit the JSX sources together with the generated `src/print_appliance/static/app.js`, `client.js` and `THIRD_PARTY_LICENSES.txt`. Do not edit generated bundles by hand. `build:check` fails if a checked-in bundle or license file differs from the current sources/dependencies.

The Python wheel includes the bundles and serves them under the existing self-only CSP. Deployment still uses Python/FastAPI alone: no npm install, Node process, CDN, Internet connection or inline scripts are needed to serve the UI. Runtime licenses are available at `/assets/THIRD_PARTY_LICENSES.txt`.

## Interface

API v1 routes implemented:

- `GET /api/v1/printers` — printers granted to the authenticated client only.
- `POST /api/v1/jobs` — multipart `file`, `printer_id`, `format`, `title`, `copies`, `options`; requires `Idempotency-Key` and `Authorization: Bearer …`.
- `GET /api/v1/jobs?limit=50`, `GET /api/v1/jobs/{job_id}`, and `POST /api/v1/jobs/{job_id}/cancel` — own client jobs only.
- The admin UI is `/`, split into Tổng quan, Máy in, Clients, Lệnh in and Cấu hình tabs. Arrow keys/Home/End navigate the tabs; `#tab=printers` etc. preserves the selected view on reload.
- Printer create/edit controls include a local, case/accent-insensitive driver search across model and PPD ID. All words must match; at most 200 options render at once, so type a more specific query for a large catalogue. Selection is explicit: filtering never silently substitutes a different driver. Search does not install missing drivers. Per-printer options come from that queue's reported driver schema; see [docs/print-options.md](docs/print-options.md) for defaults, client permissions, constraints and known limits.
- Same-origin session routes live under `/admin/api/` (login/session/logout, status, CUPS discovery, printers/import/edit/pause/resume, clients/grants/key rotation/revoke, job list/details/cancel/resume/unknown resolution, and finite admission/retention settings).

API keys are random, stored as SHA-256 hashes and shown once at client creation/rotation. Admin sessions are server-side, expire after eight hours, use `HttpOnly; SameSite=Strict` cookies and CSRF tokens for mutations. `PRINT_APPLIANCE_SECURE_COOKIE=1` enables `Secure` for trusted HTTPS/reverse-proxy deployments. No wildcard CORS or public API docs are enabled. Never expose the default HTTP listener to the Internet.

Multipart request bodies and uploaded files are bounded; PDFs require `%PDF-`, ZPL requires UTF-8 `^XA...^XZ`, and only per-printer allowlisted option values are passed to CUPS. Client-provided paths, URLs and shell commands are not used. API errors do not expose spool paths or tracebacks.

Idempotency digests include the original form fields, filename and content hash. Existing same-client keys are checked before current printer grants/configuration, so a retry returns its original job even after a grant or printer edit; authentication by a currently valid key remains required. Reuse with changed content/fields returns 409. Small tombstones are independent of job-history cleanup.

## CUPS and queue safety

Production uses lazy pycups imports and an explicit local UNIX socket (`/run/cups/cups.sock`, configurable with `PRINT_APPLIANCE_CUPS_SOCKET`). It does not accept a remote CUPS host. It does not connect to printers during tests, and there is no simulated production success. Managed queue creation uses a driver discovered from local CUPS plus a discovered device or an explicitly entered LAN IPP/IPPS/socket/LPD URI. USB/DNS-SD URIs must be discovered. The application creates an appliance-owned `pa_…` queue, enables/accepts jobs on that new queue, and sets error policy `stop-printer`. It never takes over a queue with the same name. Existing queues are never overwritten by managed queue creation. Importing an existing queue is a separate explicit action: registration does not modify CUPS, and the imported printer starts paused. Before resume, the application verifies `stop-printer` error policy. Configure that policy deliberately outside the app or use a new managed queue instead. Once an imported queue is explicitly resumed/paused, the application controls it; never import an old queue shared with another sender during staging. `BROTHER_MFC` is not modified automatically.

The systemd unit runs as the dedicated, non-root `print-appliance` user with `lpadmin` supplementary group for local CUPS admin calls. CUPS authorization still depends on the site's `cupsd.conf` policy: verify local UNIX peer credentials can perform discovery, queue administration, pause/resume and job hold/release; do not assume TCP localhost or passwordless `@SYSTEM`/`@SYSTEM/operator` access. `setJobHoldUntil(job_id, "no-hold")` releases only the chosen held job; CUPS `cancelJob` return value is not treated as evidence. Managed queues stop on error rather than automatically retrying through a transient error. Test this policy on a disposable queue and confirm it matches the installed CUPS version before production.

Snapshot checking compares queue/device URI and the PPD content fingerprint (not just a driver display name). Non-raw queues whose driver fingerprint cannot be read are rejected/blocked rather than assumed unchanged. CUPS 3/PPD-free provisioning is not certified for this version. Web remapping is refused while jobs have been handed off or are unknown. Queued old jobs hold if their mapping changes; restore their original mapping or cancel and submit a new request explicitly. Uploaded payload hashes are rechecked before dispatch. A completed CUPS state does not prove that every page physically emerged.

## Local Linux installation (manual; does not run here)

Choose a modern distro Python supported by uv and matching the distro CUPS Python binding (for example Python 3.11 plus `python3-cups` on a matching distro). Install CUPS/driver packages and `uv` separately. Do not build pycups against a different interpreter ABI.

```sh
# Example only; run deliberately on the target Linux appliance after review.
sudo useradd --system --home-dir /var/lib/print-appliance --shell /usr/sbin/nologin print-appliance
sudo usermod -a -G lpadmin print-appliance
sudo install -d -o print-appliance -g print-appliance -m 0750 /var/lib/print-appliance
sudo install -d -o root -g print-appliance -m 0750 /opt/print-appliance
# Copy this source tree to /opt/print-appliance, then as an administrator:
cd /opt/print-appliance
uv venv --python /usr/bin/python3.11 --system-site-packages .venv
uv sync --python /usr/bin/python3.11 --no-dev
sudo -u print-appliance /opt/print-appliance/.venv/bin/print-appliance admin-password --data-dir /var/lib/print-appliance
```

The system-site-packages venv lets a matching distro `python3-cups` module be imported. Alternatively install the optional `cups` extra only when its build toolchain and CUPS development headers match that interpreter. Create `/var/lib/print-appliance` at mode 0750; SQLite/config data files are created at mode 0640, spool is 0750, and payloads are 0640. Run `sudo -u print-appliance ... admin-password` to reset the password; that invalidates sessions.

Install `deploy/print-appliance.service` only after checking user/group and CUPS policy. The service binds to loopback, port 8081. Add a reviewed local TLS reverse proxy/firewall only if LAN access is required; set secure cookies in that case. Do not change, stop, or uninstall the old gateway, `BROTHER_MFC`, or CUPS. No deploy/install/systemd/CUPS command was run by this implementation task.

## Operations and data

- One process owns one data directory using an exclusive OS lock; the executable fixes Uvicorn to one worker. A duplicate instance fails instead of starting another print worker. Graceful shutdown retains the lock until outstanding worker/CUPS operations finish.
- FIFO uses a persisted acceptance sequence, not wall-clock timestamps. A bounded pool progresses up to four printers concurrently; one slow CUPS call does not serialize all other printers. Each CUPS handoff has a pre-persisted correlation ID and is created held. CUPS job ID is persisted before release. Restart reconciliation uses `getJobs(which_jobs='all', my_jobs=False, requested_attributes=[...])` and `getJobAttributes`; an unfindable submission becomes `unknown`, never an automatic retry. Unknown blocks that printer until an administrator reconciles a terminal outcome with evidence. A known nonterminal CUPS job must be canceled/verified first. A reconciled held job is not automatically released after restart; an operator must resume it. Job IDs are checked against the unique correlation before tracking/cancel/release.
- App-level cancel is definite before handoff. After handoff it requests CUPS cancellation and polls actual state; a missing/uncertain CUPS result never becomes `canceled` by assumption. CUPS may already have emitted pages.
- Offline/error detection latches a pause and requires operator resume. CUPS status polling is not instantaneous. Whole-printer resume enables the queue and permits FIFO progression. Individual resume preserves the printer pause latch, permits only the first eligible job, and re-pauses the physical queue when it finishes; it cannot skip predecessors or unknown outcomes. Unexpected CUPS holds require manual resume; a stopped job is treated as potentially partial/unknown, never automatically restarted. Validate installed CUPS pause/hold semantics on hardware.
- Queued/held/unknown payloads are retained. Terminal payloads are removed. History defaults to 30 days (configurable); idempotency records remain independently. Upload size, outstanding job count and minimum free disk threshold are finite/configurable. Admission reserves disk during copying, fsyncs the payload directory before 202, and rechecks count/grants inside the SQLite transaction. The HTTP/multipart parser also has a finite body limit; TLS/proxy connection and rate limits are still needed on a shared/untrusted LAN.
- `uv run print-appliance backup --data-dir PATH --destination FILE.sqlite3` uses SQLite's online backup API and is consistent with WAL. It refuses the live database path or an existing destination, uses private file permissions and checks integrity. It backs up SQLite only; for a full recoverable spool backup, stop the service first and copy the complete data directory atomically/consistently. Protect backup files like the source data.
- Passwords/API keys, spool paths and upload content are not logged. Run as a non-root service account.

## Verification limits

The fake backend tests cover API/auth/CSRF, validation/admission/idempotency, coordinator sequencing/recovery/cancel evidence, and the single-worker lock. They are not proof of pycups availability, CUPS authorization or real printing. Subsequent EDATEC rollout verified real discovery/schema/private job metadata and LAN interfaces; the user reports Canon PDF paper output. Offline/paper-out, duplex/copies/cancel and physical ZPL acceptance remain separate hardware gates. See the technical report for current evidence. Before production, test on a disposable/test queue: URI/driver support on ARM, local UNIX socket permissions, held submission/release, stop-printer behavior, queue pause/resume, offline/error, cancellation, CUPS history purge/restart, disk-full handling and rollback. Never use a real production queue for first validation.

## Minimal client example

Read a granted `printer_id` using `GET /api/v1/printers`, then:

```sh
# Use environment variables; do not put real keys into checked-in scripts.
curl -sS "$PRINT_APPLIANCE_URL/api/v1/jobs" \
  -H "Authorization: Bearer $PRINT_APPLIANCE_KEY" \
  -H 'Idempotency-Key: invoice-unique-operation-001' \
  -F "printer_id=$PRINT_APPLIANCE_PRINTER_ID" \
  -F 'format=pdf' -F 'title=Invoice' -F 'copies=1' \
  -F 'file=@invoice.pdf;type=application/pdf'
```

Reuse the same key/form/file for a transport retry; use a new operation ID only for a genuinely new print request. Poll `/api/v1/jobs/JOB_ID` with the same Bearer key. Authentication remains required for duplicate replay. Filename/title/copies are part of request identity; Public print fields are part of request identity. Do not send a new operation ID to bypass an unknown outcome.

See [docs/verification.md](docs/verification.md) for the measured local checks and outstanding hardware gates.

## Browser regression and Git deployment

`tests/browser/admin-ui.cjs` checks the six tabs, integrated driver combobox against a mocked 16k-driver catalogue, field-help popovers, printer/client CRUD, delayed requests, duplicate prevention and 409/503 recovery. It intercepts all admin API calls and refuses non-loopback base URLs. Use an externally available Playwright installation; it is test tooling, not a frontend/CDN dependency:

```sh
# Serve the local application on loopback with a disposable data directory first.
PLAYWRIGHT_MODULE=/absolute/path/to/playwright \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
UI_BASE_URL=http://127.0.0.1:8081 node tests/browser/admin-ui.cjs
```

Run `tests/browser/admin-safety.cjs` with the same environment variables for additional auth/CSRF, unsaved-settings, legacy-option/remapping, job-control and stale-session regressions. `tests/browser/client-safety.cjs` covers immutable 503/408 retries, submission-time 401, BFCache request clearing and keeping files/pending requests intact when switching to the guide. Both intercept every API and never print. `tests/browser/api-guide.cjs` tests all five task views, request fields, offline schemas/errors, cURL syntax/copy fallback, safe base URLs, search and admin/client tab navigation at 320–1280px, refusing all client calls/mutations. For shared React component changes, run these five scripts plus the isolated password test below, `npm run build:check` and the Python tests. Optional `UI_ARTIFACT_DIR` captures desktop/mobile screenshots in the main admin/client suites.

For the password form's real FastAPI/SQLite browser test, start a disposable loopback server from the repo root:

```sh
uv run python -m tests.browser.serve_password
```

In another terminal, set `UI_TEST_FIXTURE` to the printed `/tmp/print-password-test-.../fixture.json` path and run `tests/browser/password-ui.cjs` with the same `PLAYWRIGHT_MODULE` / `CHROME_EXECUTABLE` settings. Its default base URL is `http://127.0.0.1:18086`. The server uses random test credentials, explicit FakeCups and a disabled worker; it never reads production data. Stop it afterward to remove the fixture/database.

### Password and client API guide

Use **Cấu hình → Đổi mật khẩu quản trị** with the current password, a new password of at least 12 characters and confirmation. Success signs out all admin sessions; client keys and print jobs remain intact. Five incorrect current-password checks in five minutes temporarily lock both login and password changes for five minutes. If the response is lost, verify by logging in rather than automatically resending. The existing CLI remains available for a forgotten password.

Open **Client → API Guide** (`/client#api-guide`) without an API key, or use the same guide in the admin tab. Choose one of five tasks to see required fields, copyable cURL and example responses; full schemas/errors expand on demand. On mobile, request fields start collapsed. It is offline/read-only: no API execution or real-key entry. Switching between printing and the guide keeps the selected file and any unconfirmed request in memory; reloading still discards them. See [the API contract](docs/api.md) for authentication, idempotency and safety boundaries.

The production Mac checkout and EDATEC source must be at the same deployed Git commit. See [docs/git-deployment.md](docs/git-deployment.md). Credentials, backups and runtime data remain outside Git even though the repository is public.

### Admin workflow (0.1.2)

- **Máy in → Thêm máy in** shows one form at a time: configure a new queue, or register an existing CUPS queue.
- **Driver** is one searchable combobox. Type a model/PPD ID and explicitly choose a result; unmatched text cannot be submitted. Escape restores the previous selection, while the clear button removes it. Only installed drivers are listed; this does not install Canon drivers.
- The **?** beside each field explains its purpose and examples. Open by click/touch/keyboard; close with Escape, the close button or an outside click. Advanced URI settings are collapsed; registered-printer defaults and client override permissions are generated from the queue's reported driver schema. See [docs/print-options.md](docs/print-options.md).
- Requests display a pending state and disable competing controls to prevent duplicate submissions. Failures keep form values; mutations are never automatically retried.
- Printer **Gỡ đăng ký** removes only the application's registration and grants, never the CUPS queue. Client **Sửa client / Xóa client** supports rename, grants and deletion. Both deletions require confirmation and are rejected while nonterminal or unknown jobs exist. Historical jobs and deduplication records remain; a deleted client's key stops working.

### Client test page (0.1.5)

Open `/client` on the appliance, enter the API key issued from the admin Clients tab, select an assigned printer and upload PDF/ZPL. The page uses only existing scoped client APIs, never the admin password/session. It submits real print jobs; do not send to a physical printer until its driver is verified. The key is saved only in tab-scoped sessionStorage: reload reconnects automatically; disconnect or API 401 clears it. Never stored in localStorage or URLs. Files/pending requests remain memory-only and are not restored or automatically resubmitted. Printer-specific controls reflect only schema choices and client grants; see [docs/print-options.md](docs/print-options.md).

A lost/5xx submission response freezes the original request and offers explicit same-ID replay. It never automatically resubmits. Unique IDs use `crypto.getRandomValues`, including on LAN HTTP. History updates every four seconds while jobs are active; unknown outcomes need operator investigation. See [client guide](docs/client-guide.md).

Run `tests/browser/client-ui.cjs` with the same `PLAYWRIGHT_MODULE`, `CHROME_EXECUTABLE`, `UI_BASE_URL` and `UI_ARTIFACT_DIR` environment variables as the admin regression. All client APIs are mocked and loopback-only; no physical printing occurs.

Client API hiện có 5 endpoint; GET printers kèm capabilities. Gửi tùy chọn bằng field multipart paper_size/sides/color_mode/scaling; xem docs/api.md để chuyển đổi client cũ.
