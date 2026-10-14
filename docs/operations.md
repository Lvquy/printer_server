# Operations and rollout

## Current deployment

EDATEC/Raspberry Pi dùng Debian 12 ARM64/Python 3.11, service `print-appliance` trên LAN đã được phép. Mọi IP/hostname trong tài liệu chỉ là ví dụ, không phải địa chỉ đúng của thiết bị. Lấy địa chỉ thực từ quản trị để thay `<APPLIANCE_IP>` trong `http://<APPLIANCE_IP>:8081`; IP/bind là cấu hình triển khai, không hardcode trong sản phẩm. Gateway cũ đã gỡ có phép và sao lưu riêng; giữ nguyên CUPS/driver/queue hiện có. Canon LBP6230dw dùng Canon UFRII LT ARM64/CNRCUPSLBP6230ZNK.ppd; người dùng xác nhận in ra giấy. Đọc được trạng thái/schema không thay thế test duplex/offline/copies/cancel trên phần cứng.

Đổi input macOS sang ABC/U.S. khi nhập mật khẩu SSH. Không đăng credentials, backup/API key lên Git/log/chat. Xem [Git deployment](git-deployment.md): cùng commit và wheel trên Mac/EDATEC.

Mốc chức năng đã kiểm ngày 06/10/2026 là `9edebaa` (sản phẩm 0.1.5): UI React offline, đổi mật khẩu và API Guide theo tác vụ. Commit tài liệu có thể mới hơn mà package runtime không đổi; đọc `/opt/print-appliance/SOURCE_COMMIT` để biết commit đang chạy. Xem [verification](verification.md) cho bằng chứng local và kiểm tra Pi tách biệt.

## Web và hướng dẫn tích hợp

- Trang quản trị `/` có sáu tab: Tổng quan, Máy in, Clients, Lệnh in, Cấu hình, API Guide.
- Trang Client `/client` có **In tài liệu / API Guide**. `/client#api-guide` đọc được trước khi nhập key, chỉ dùng ví dụ giả; không lấy dữ liệu thật hay gọi thử API.
- Hướng dẫn có sáu tác vụ với request/cURL/response, các phần schema/lỗi mở khi cần. Chuyển tab giữ file và yêu cầu chưa xác nhận trong bộ nhớ; tải lại không khôi phục chúng. Không tạo ID mới để thử lại một kết quả chưa rõ.
- Mở [hướng dẫn sử dụng](technical-report.md), [HTML offline](technical-report.html) hoặc [PDF chuẩn](technical-report.pdf). Số trang HTML tham chiếu PDF đi kèm.

## Đổi mật khẩu và quyền truy cập

Vào **Cấu hình → Đổi mật khẩu quản trị**: nhập mật khẩu hiện tại, mật khẩu mới 12–1024 ký tự khác mật khẩu cũ và xác nhận. Thành công đăng xuất mọi session quản trị; API key client và lệnh in giữ nguyên. Năm lần kiểm sai trong năm phút khóa kiểm tra/đăng nhập năm phút. Form xóa nội dung khi gửi hoặc rời tab; nếu mất phản hồi hãy xác minh bằng đăng nhập, không gửi lại tự động. Quên mật khẩu dùng CLI `print-appliance admin-password` như trong README/hướng dẫn, không sửa trực tiếp SQLite.

Giữ deployment SSH key và authorization cho tới khi chủ thiết bị yêu cầu xóa. SSH key, mật khẩu quản trị web và API key client là ba quyền khác nhau. Không ghi key/password vào URL, ví dụ, Git hoặc log.

## Before deployment

Read-only inventory: OS/Python, disk free, CUPS version/device discovery/drivers, service/queue configuration. Check Canon model and supported driver on ARM; do not assume generic Canon support. Save a backup of existing service/config/CUPS before any approved changes. Use a new service and port for staging.

## Development vs real printing

Tests must inject an explicit fake adapter; fake success is never proof of physical printing. Production must use a real local CUPS connection and report missing CUPS/driver clearly. System Python on Linux can access distro-provided python3-cups; installer/venv must account for this rather than silently falling back to fake printing.

## Safe rollout

1. Test package locally without connecting to printers.
2. Install a separate `print-appliance` service on an unused port (8081 suggested), with its own data and least-privilege service user. Preserve existing CUPS queues and application data.
3. Bootstrap an admin password locally; create a scoped client key. Do not use default credentials.
4. Register a test printer/queue deliberately. Avoid enabling two senders on the same physical printer during acceptance testing.
5. Run print/queue/restart/failure acceptance checks. Only migrate Odoo URL/key after validation and explicit permission.
6. Rollback: stop/disable new service and revert client URL/key to saved values; do not uninstall CUPS or delete old queues.

## Pause and uncertain outcomes

Offline conditions latch a pause and require an operator to resume. CUPS has its own retry/queue behavior; verify that actual CUPS queues follow application pause policy. Stopping after an error cannot prevent pages already emitted. When outcome is unknown, inspect CUPS job history/printer/operator evidence and record a resolution; never blindly submit the same content again.

## Retention and backups

Completed/failed/canceled terminal files are removed; waiting/held/unknown payloads are retained. History default: 30 days. Minimal idempotency records remain after history cleanup. Backup the database/config while consistent; for live SQLite use its backup interface, not just copy the main file while WAL is active. Keep spool files and their metadata consistent. Monitor disk free and reject new work before losing accepted jobs.

## Verification status

See README for actual local run/test/install commands, CLI and implemented routes. The checked-in service unit is `deploy/print-appliance.service`; it binds to loopback:8081, runs as non-root `print-appliance`, and grants `lpadmin` as a supplementary group. `cups.service` is ordered before the app but is not a hard systemd dependency, so the UI can report unavailable CUPS rather than simulate success. Match the distro pycups binding to the Python ABI used for the venv.

PyCUPS handoff uses `printFile` with `job-hold-until=indefinite` and a correlation job name, persists the returned ID before release through `setJobHoldUntil(job_id, 'no-hold')`, reconciles using `getJobs(which_jobs='all', my_jobs=False, requested_attributes=[...])` / `getJobAttributes`, and verifies cancel through state polling. `cancelJob` returning `None` is not evidence of cancellation. Managed queues request `stop-printer` error policy. Confirm the local UNIX socket authorization permits discovery, queue administration, enable/disable, job hold/release and cancel with the service account; a TCP localhost connection or lpadmin membership alone is not an assumption of passwordless `@SYSTEM/operator` access.

Automated tests inject fake CUPS. Real EDATEC discovery/schema/job metadata and service-account local authorization have been checked; the user reports Canon paper output. Hardware fault/duplex/copies/cancel acceptance is not inferred from these checks. The implementation fails safe for unreconciled submissions by retaining an `unknown` outcome and never automatically resending. Do not mark the appliance production-ready until the hardware acceptance gate is satisfied.

## Implemented runtime details

The local socket defaults to `/run/cups/cups.sock`; use `PRINT_APPLIANCE_CUPS_SOCKET` if the target distro uses another Unix path. No TCP CUPS admin assumption. The new loopback service must be deliberately exposed to LAN via reviewed reverse proxy/TLS or a specific LAN listener/firewall; Internet exposure remains out of scope.

An imported old queue is initially paused in the application without modifying CUPS at registration. Resume is blocked unless error policy is `stop-printer`. If the queue is dedicated and changing it is approved, a technician can set `lpadmin -p QUEUE -o printer-error-policy=stop-printer`, then verify. Do not run this on `BROTHER_MFC` or another existing sender's queue merely to make staging pass. Prefer a new managed test queue.

Snapshot mismatch is not auto-migration: restore the original queue mapping or cancel affected queued jobs and ask the client for a new explicitly intended request. For a CUPS `stopped` job, possible partial paper output makes the result unknown; cancel/verify before recording evidence. No restarting/reprocessing of stopped jobs. Raw ZPL may have device-specific copy/label settings (`^PQ` etc.); test physical counts on the actual Zebra-compatible model.

Modern Python and pycups must match the target ABI. If the existing Raspberry Pi OS only provides Python 3.9, do not silently upgrade the appliance OS: inventory first and plan a separate runtime/driver installation. Existing hardware/gateway remains untouched by this implementation.

## Private CUPS job metadata

Fresh unauthenticated connections can hide `job-name` even for the Unix owner/root. The adapter establishes local certificate auth with read-only `adminGetServerSettings()` on the same connection before job metadata/reconciliation. Failure stays unavailable: never weaken correlation checks or globally disable job privacy. Missing/mismatched identity cannot authorize release or resubmit. Operator resolution records matching terminal CUPS evidence and a reason, without a new job.
