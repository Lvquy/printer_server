# API contract (v1)

## Client API — hợp đồng hiện tại

Có 5 endpoint: `GET /api/v1/printers`, `POST /api/v1/jobs`, `GET /api/v1/jobs`, `GET /api/v1/jobs/{job_id}`, `POST /api/v1/jobs/{job_id}/cancel`.

Xem [hướng dẫn tùy chọn hiện tại](print-options.md) và schema trong `frontend/src/client-api.json`, được kiểm tra với response qua FakeCups.

- Bearer API key chỉ cho phép truy cập máy được cấp và lệnh của client.
- Danh sách máy trả `capabilities` gồm `availability`, `reason`, `supported`, `defaults`. Không trả keyword/constraints driver cho client. Endpoint client capabilities riêng đã bỏ (404); endpoint quản trị giữ nguyên.
- Multipart gửi in gồm `file`, `printer_id`, `format`, `title`; `copies` mặc định 1. Thêm field `options` chứa JSON object với `paper_size`, `sides`, `scaling` nếu muốn tùy chỉnh PDF. Bỏ qua dùng mặc định, không cần gọi danh sách trước mỗi lần in. Ba field riêng lẻ tương ứng vẫn được nhận để tương thích client cũ; không trộn với `options`.
- Ba tùy chọn PDF phổ thông được chọn theo khả năng ánh xạ chắc chắn, không phụ thuộc allowlist driver cũ. Các giới hạn quyền truy cập máy vẫn giữ nguyên. Giá trị bỏ qua dùng mặc định CUPS/driver.
- Chỉ công bố enum ánh xạ chắc chắn từ descriptor driver; không đoán theo tên máy hoặc chỉ dựa vào IPP. `supported` rỗng không có nghĩa máy vật lý thiếu tính năng. `defaults` null nghĩa là chưa biểu diễn chắc chắn mặc định đó. Mảng từng field không chứng minh mọi tổ hợp đều hợp lệ.
- Hệ thống kiểm tra tổ hợp với cấu hình hiệu lực, lưu snapshot keyword driver vào lệnh. Đổi cấu hình sau này không đổi snapshot cũ.
- 422 cho field/giá trị/tổ hợp không hợp lệ; chi tiết có `field`, `message` và `allowed` khi là lỗi lựa chọn. Unknown/repeated/empty field bị từ chối. 503 khi không đọc được capability hoặc mapping thay đổi. Không tự hạ cấp tùy chọn.
- ZPL gửi nguyên bản, không áp dụng các tùy chọn PDF.
- `Idempotency-Key` bắt buộc; 202 là đã lưu bền vững, 200 là replay. Giữ nguyên file/tên file/mọi trường khi gửi lại. `unknown` cần đối soát, không tự in lại.
- `options` mới chỉ nhận ba tên phổ thông ở trên; keyword driver cũ bị từ chối cho yêu cầu mới. Replay đúng yêu cầu đã nhận từ trước vẫn được đối chiếu theo `Idempotency-Key` trước khi kiểm tra cấu hình hiện tại.
- GET jobs nhận `limit` (mặc định 50, kẹp 1–100), GET chi tiết trả events. Poll 3–5 giây. Hủy không thu hồi giấy và HTTP 200 không luôn là canceled.
- Các lỗi khác: 401 key sai, 403 máy chưa cấp, 404 lệnh không thuộc client, 409 mã trùng nhưng nội dung khác, 413 file lớn, 429 đầy hàng đợi, 507 thiếu đĩa.

## Admin interface

`/` là trang quản trị cùng nguồn với app; OpenAPI/docs routes bị tắt. Admin routes dưới đây dùng session cookie riêng, không dùng API key client. Login nhận JSON `{"password":"…"}`, trả `csrf_token` và cookie `pa_admin` (HttpOnly, SameSite=Strict, 8 giờ). Mọi POST/PUT mutation ngoài login cần `X-CSRF-Token`; nếu Origin có mặt phải cùng origin. `PRINT_APPLIANCE_SECURE_COOKIE=1` bật cờ Secure. Năm lần sai mật khẩu trong cửa sổ 5 phút sẽ khóa login trong 5 phút. CLI bootstrap/reset yêu cầu mật khẩu; install.sh cài mới đặt admin12345678 và yêu cầu người quản trị đổi sau đăng nhập.

Tab **API Guide** có trong trang Client (`/client#api-guide`, không cần nhập key) và trang quản trị. Tài liệu offline trình bày theo năm tác vụ: chọn máy → đọc tùy chọn → gửi file → theo dõi, cùng lịch sử và hủy. Mỗi tác vụ có trường request, cURL và ví dụ response; mã lỗi/schema đầy đủ mở khi cần. Trên mobile, nhóm tham số bắt đầu thu gọn. Không có “Try it out”, không thực thi API hay in/hủy lệnh. Base URL lấy từ thiết bị hiện tại và có thể sửa cho ví dụ; chỉ dùng API key placeholder. cURL gửi in giữ `REQUEST_ID` đã tạo một lần, không có auto-retry. Base URL hiển thị origin của trang đang mở; các địa chỉ được ghi trong tài liệu vẫn chỉ là ví dụ. Sao chép biến môi trường, thay placeholder trên máy client, lấy PRINTER_ID từ bước chọn máy và JOB_ID từ response; không nhập key thật vào guide. Trên HTTP không có Clipboard API, nút sao chép chọn code để dùng Ctrl+C/⌘C. Schemas được giữ trong `frontend/src/client-api.json` và kiểm tra với response thật qua FakeCups.

### POST /admin/api/password

Đổi mật khẩu trong **Cấu hình → Đổi mật khẩu quản trị**. Yêu cầu session quản trị, cùng origin và `X-CSRF-Token`; body JSON có `current_password`, `new_password`, `confirm_password`. Mật khẩu mới 12–1024 ký tự, phải khác mật khẩu nhập hiện tại và khớp confirmation; không tự cắt khoảng trắng. Server xác minh mật khẩu hiện tại rồi lưu salt/hash scrypt mới và thu hồi **mọi** session quản trị trong cùng transaction. Thành công trả 200 `{"authenticated":false}`, xóa cookie hiện tại và yêu cầu đăng nhập lại. API key client, máy và lệnh in không đổi.

Sai mật khẩu hiện tại trả 400 nhưng giữ session; lỗi field/confirmation trả 422; chưa đăng nhập hoặc session đã bị thu hồi trả 401; sai CSRF/Origin trả 403. Dùng chung bộ giới hạn với login: năm lần sai trong cửa sổ 5 phút khóa kiểm tra mật khẩu 5 phút; khi bị khóa trả 429. Response và audit không chứa mật khẩu. Form không lưu mật khẩu vào browser storage, xóa nội dung khi gửi hoặc rời tab và không tự retry. Nếu mất phản hồi sau khi đổi, hãy đăng nhập lại để xác minh bằng mật khẩu mới/cũ hoặc reset bằng CLI; không tự gửi lại. Chỉ dùng LAN đáng tin cậy/TLS; HTTP không mã hóa mật khẩu.

Admin routes đã triển khai:

- `POST /admin/api/password` — đổi mật khẩu hiện tại và thu hồi mọi session quản trị theo hợp đồng phía trên.
- `POST /admin/api/login`, `GET /admin/api/session`, `POST /admin/api/logout`.
- `GET /admin/api/status` — trạng thái CUPS, dung lượng và số lệnh.
- `GET /admin/api/discovery` — CUPS devices, drivers, existing queues.
- `GET /admin/api/printers` và `GET /admin/api/printers/{id}`; `POST /admin/api/printers` tạo queue managed từ URI/driver đã khám phá; `POST /admin/api/printers/import` nhập queue hiện có nguyên trạng; `PUT /admin/api/printers/{id}` sửa; `DELETE /admin/api/printers/{id}` gỡ đăng ký; `POST /admin/api/printers/{id}/pause|resume` điều khiển CUPS.
- `GET /admin/api/clients` và `GET /admin/api/clients/{id}`; `POST /admin/api/clients` body `{"name":"…","printer_ids":[…]}`; key chỉ xuất hiện một lần. `PUT /admin/api/clients/{id}` đổi tên và/hoặc thay toàn bộ grant list; `DELETE /admin/api/clients/{id}` xóa mềm; `PUT /admin/api/clients/{id}/printers` đổi grant; `POST /admin/api/clients/{id}/rotate-key|revoke` đổi/thu hồi key.
- Xóa máy in/client trả 409 nếu còn job chưa terminal (`queued`, `held`, `submitting`, `submitted`, `unknown`). Xóa máy in chỉ gỡ đăng ký khỏi ứng dụng; không xóa, pause hay sửa queue/driver/job trong CUPS.
- `GET /admin/api/jobs?limit=100`, `GET /admin/api/jobs/{id}`, `POST /admin/api/jobs/{id}/cancel|resume`; `POST /admin/api/jobs/{id}/resolve` body `{"outcome":"completed|failed|canceled","reason":"evidence …"}`. Resolve unknown không submit lại.
- `GET /admin/api/settings`; `PUT /admin/api/settings` nhận `max_upload_bytes`, `max_pending_jobs`, `min_free_bytes`, `history_retention_days` với giới hạn hữu hạn.

Hợp đồng client đã thay đổi như mục đầu. API keys đã revoke trả 401; client chỉ thấy printers được cấp và jobs của mình. Một retry idempotent cùng client/key/nội dung được xử lý trước khi kiểm tra quyền printer hiện tại, nhưng request vẫn phải xác thực. `history_expired` đánh dấu replay tombstone không còn lịch sử. Các response job không chứa file path.

## Tình trạng xác minh

Routes trên đã được cài đặt và test với adapter giả tường minh. Đã đọc schema/job metadata thực trên EDATEC ARM64/pycups 2.0.1/CUPS 2.4.2 và driver Canon; người dùng báo in được. Các test offline/duplex/copies/cancel/power-loss phần cứng vẫn riêng biệt; không coi test giả là chứng minh mọi chức năng phần cứng. Xem README/runbook để chạy cục bộ và các giới hạn đã biết.

## Admin routes implemented

- `POST /admin/api/password`: authenticated current-password change; JSON fields `current_password`, `new_password`, `confirm_password`. New password is 12–1024 characters. Success invalidates all admin sessions and expires the caller's cookie. See the password-change contract above.
- `POST /admin/api/login`, `GET /admin/api/session`, `POST /admin/api/logout`. Login returns `csrf_token`; include `X-CSRF-Token` on all subsequent mutations.
- `GET /admin/api/status`, `/discovery`, `/printers`, `/printers/{id}`, `/clients`, `/clients/{id}`, `/jobs?limit=100`, `/jobs/{job_id}`, `/settings`, `/audit?limit=100`.
- `POST /admin/api/printers`: `name`, `device_uri`, `driver`, `formats`, `default_options`, `allowed_options`. Driver must be in CUPS discovery; discovered USB/DNS-SD or manually entered local network URI.
- `POST /admin/api/printers/import`: `queue`, `name`, `formats`; imported queue starts paused and must have verified stop-printer policy before resume.
- `PUT /admin/api/printers/{id}`: changed fields from the create payload. Mapping edits are refused while handed-off/unknown jobs exist. Imported queue device/driver changes are not allowed here. `GET` the same resource returns its active record; missing or deleted IDs return 404.
- `DELETE /admin/api/printers/{id}` takes no body and returns `{"id":"<printer-id>","deleted":true}`. It returns 409 while any `queued`, `held`, `submitting`, `submitted` or `unknown` job exists; otherwise it unregisters only the application record and grants. It never deletes, pauses or changes a CUPS queue, driver or job. The old ID/history remains; registering the same existing CUPS queue later creates a new printer ID.
- `POST /admin/api/printers/{id}/pause` and `/resume`. Resume is refused for unresolved unknowns and unsafe error policy.
- `POST /admin/api/clients`: `name`, `printer_ids`; returns a one-time `api_key`. `GET /admin/api/clients/{id}` returns `{id,name,revoked,created_at,printer_ids}`. `PUT /admin/api/clients/{id}` accepts `{"name":"New name"}`, `{"printer_ids":["<active-printer-id>"]}` or both; `printer_ids` replaces the complete grant list. It returns the same public client record (never the key). `PUT /admin/api/clients/{id}/printers` remains the grant-only form. `DELETE /admin/api/clients/{id}` takes no body and returns `{"id":"<client-id>","deleted":true}`. `POST .../{id}/rotate-key` and `/revoke` affect only active clients.
- `POST /admin/api/jobs/{id}/cancel`, `/resume`; `POST .../{id}/resolve` takes `outcome` (completed/failed/canceled) and `reason` (8–1000 chars). A matching CUPS job must be terminal before resolution; another correlation is not evidence for this job.
- `PUT /admin/api/settings`: finite integer limits `max_upload_bytes`, `max_pending_jobs`, `min_free_bytes`, `history_retention_days`.

Admin APIs return 200 on successful mutations; keys are never returned by list/history/audit routes. In-use deletes return 409, missing/deleted resource mutations return 404, invalid fields 422 and unavailable CUPS 503. Deleted printers/clients are omitted from lists and grants; deleted clients' keys cannot authenticate or be rotated back to active. Successful deletions and configuration/control changes have a minimal credential-free audit entry. All mutations require the admin session and `X-CSRF-Token`.

## Client test page

`GET /client` serves an offline Vietnamese client UI using the existing `/api/v1` contract. The page itself is public, but all printer/job data still requires a scoped Bearer API key. It never calls admin APIs or sends admin session cookies. Since 0.1.5, keys use tab-scoped sessionStorage: reload reconnects; disconnect or API 401 removes the saved key. No localStorage/URL credentials; files and unresolved request parameters remain in memory only. Unknown submission responses expose the original request ID and an explicit immutable replay, not a new job. Reload/disconnect can discard an unconfirmed request; inspect history rather than creating a new print request. The public **API Guide** tab is also available before connecting. It contains only bundled fake examples and placeholder keys, not live printer/job data. Switching tabs keeps the print form/file and immutable pending request mounted; it does not reconnect or resend. No new client privileges, CORS changes or printing backend are introduced.
