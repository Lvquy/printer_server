# Thiết kế phiên bản đầu

## Mục tiêu và giới hạn

EDATEC là **thiết bị quản lý in tại chỗ có API**, không phải server nghiệp vụ. Một thiết bị phục vụ nhiều client và nhiều máy in USB/mạng. Odoo là một client tích hợp đầu tiên, không phải điều kiện để lõi in chạy.

- Local/LAN trước. Không webhook, cloud, Internet exposure, Redis hoặc hệ thống nhiều node trong bản đầu.
- Quản trị qua web với một tài khoản quản trị. Mỗi client dùng API key riêng và chỉ xem lệnh của mình, dùng các máy được cấp.
- PDF và ZPL; không tự chuyển PDF thành ZPL, không hứa hỗ trợ mọi model. ZPL cần máy hiểu ZPL, PDF cần driver/khả năng phù hợp.
- CUPS sở hữu driver, spooling và giao tiếp máy in. Ứng dụng sở hữu quyền truy cập, tiếp nhận bền vững, chính sách điều phối, cấu hình và tracking.
- Không dùng RS232/RS485/CAN chỉ để tận dụng phần cứng.

## Hình dạng kiến trúc

```text
Client ── API key / Idempotency-Key ──> FastAPI
                                          │
                                  SQLite + file spool
                                          │
                                 bộ điều phối lệnh
                                          │
                                         CUPS ──> USB / máy in mạng
                                          │
Admin ── session + CSRF ──> Web quản trị ───┘
```

Python/FastAPI, SQLite và systemd cho một thiết bị. Trang web cùng nguồn với API, không phụ thuộc CDN/Internet. Không tự viết driver hoặc spooler thay CUPS. Một process điều phối duy nhất; ngăn chạy hai worker trên cùng thư mục dữ liệu.

## Tiếp nhận và chống gửi trùng

1. Xác thực client, quyền máy in, giới hạn file/hàng đợi/dung lượng và tùy chọn được phép.
2. Nhận PDF/ZPL trực tiếp bằng multipart; không nhận URL file từ Internet hoặc lệnh shell.
3. Lưu file an toàn và commit lệnh trước khi trả HTTP 202 với `job_id`.
4. Cùng client + `Idempotency-Key` + cùng nội dung yêu cầu trả cùng lệnh. Đổi nội dung với cùng mã trả 409.
5. So sánh dựa trên dữ liệu client đã gửi và hash file, không dựa trên cấu hình hiện tại; retry sau khi đổi cấu hình vẫn trả đúng lệnh cũ.
6. Dữ liệu chống trùng tối thiểu lưu riêng, không bị xóa khi dọn lịch sử. Không lưu API key dạng rõ hoặc đưa vào log.

Không cam kết exactly-once tới thiết bị vật lý. Khoảng mất điện giữa giao lệnh và lưu phản hồi được đối soát hoặc chuyển `unknown`; tuyệt đối không tự in lại.

## Lưu trữ và restart

- SQLite lưu client, quyền, phiên bản cấu hình, jobs, sự kiện và mã yêu cầu. Transactions, foreign keys và chế độ đồng bộ phù hợp cho thiết bị đơn node.
- File chỉ chứa nội dung đang cần xử lý, không có chức năng in lại file cũ.
- Lệnh đã nhận không bị mất khi service restart; upload chưa nhận thành công có thể dọn. Không dựa vào dict trong RAM như repo tham khảo.
- Worker khóa độc quyền dữ liệu. Không bật nhiều Uvicorn workers.
- Khi khởi động, đối soát lệnh đã giao với CUPS bằng mã job và dấu tương quan riêng. Không tìm thấy không đồng nghĩa an toàn để gửi lại: CUPS có thể đã dọn lịch sử.
- File thiếu/hỏng hoặc cấu hình CUPS không khớp snapshot phải được hiện lỗi/giữ, không âm thầm đổi đích.

## Trạng thái và điều phối

| Trạng thái | Ý nghĩa |
| --- | --- |
| `queued` | Đã lưu an toàn, chưa giao hệ thống in |
| `held` | Chờ người vận hành/cấu hình/khả năng máy |
| `submitting` | Đã ghi ý định giao; có cửa sổ chưa biết kết quả |
| `submitted` | CUPS đã nhận, đang được theo dõi |
| `completed` | CUPS báo hoàn thành, không bảo đảm tuyệt đối giấy đã ra |
| `failed` | Có lỗi xác định |
| `unknown` | Chưa đủ bằng chứng; không tự gửi lại |
| `canceled` | Đã hủy với mức xác nhận được ghi rõ |

Có thể thêm trạng thái nội bộ để mô tả CUPS job đang được giữ, nhưng không gộp nhận/giao/hoàn thành.

- FIFO cho từng máy; các máy khác nhau được xử lý độc lập. Một lệnh đang được giao/theo dõi cho mỗi máy trong bản đầu.
- Máy offline hoặc thiếu giấy: giữ/latch pause, không tự chạy lại chỉ vì kết nối trở lại.
- Phối hợp với CUPS: giao job ở trạng thái hold trước khi ghi mã CUPS bền vững, chỉ release khi được phép. Quản lý pause/resume thực tế, không chỉ đổi nhãn trên UI.
- Cho phép tiếp tục toàn máy hoặc riêng một lệnh đã giữ. Tiếp tục riêng không vô tình mở các lệnh khác hoặc `unknown`.
- Hủy lệnh chưa giao là chắc chắn. Hủy lệnh đã giao phải kiểm tra CUPS; hoàn thành trước khi hủy vẫn là hoàn thành, lỗi/không có bằng chứng thì không báo đã hủy.
- `unknown` cần quản trị đối soát và ghi lý do khi xác nhận kết quả; không có nút gửi lại âm thầm.

Việc phát hiện offline và dừng máy không tức thời. Một phần giấy có thể đã in; UI/runbook phải nói rõ giới hạn này.

## Cấu hình động

Client chỉ gửi `printer_id`. Máy đăng ký chứa tên, queue CUPS, kết nối, định dạng hỗ trợ, tùy chọn mặc định, danh sách tùy chọn cho phép ghi đè và trạng thái vận hành.

- Đổi IP/USB/driver/defaults trên web, không sửa code tích hợp.
- Lệnh lưu snapshot queue/URI/driver/định dạng/options lúc nhận. Khi CUPS hiện tại khác snapshot, giữ để xử lý; không tự áp dụng cấu hình mới lên lệnh cũ.
- CUPS queue quản lý bởi ứng dụng phải phân biệt với queue hiện có. Không tự xóa/thay `BROTHER_MFC` của hệ thống cũ.
- Web hỗ trợ khám phá thiết bị/driver mà CUPS biết và tạo/sửa queue được hỗ trợ. Driver riêng có thể cần kỹ thuật viên cài ngoài ứng dụng.
- Không chạy web process bằng root. Quyền quản trị CUPS được cấp rõ ràng cho user dịch vụ trên Linux; không thực thi shell từ request.
- Thao tác cấu hình và vận hành có sự kiện ghi lại, dù bản đầu chỉ một quản trị viên.

## File, lịch sử và dung lượng

File xóa khi lệnh thực sự kết thúc; `unknown` và lệnh chờ không bị dọn. Client gửi yêu cầu mới nếu muốn in thêm. Lịch sử/lỗi mặc định 30 ngày, chỉnh được qua web. Giữ tombstone/idempotency tối thiểu lâu dài, cảnh báo dung lượng; không hứa tăng trưởng vô hạn.

Giới hạn file, số lệnh chưa kết thúc và dung lượng trống được cấu hình. Khi đầy: từ chối rõ ràng lệnh mới, không xóa lệnh chưa xử lý. Cleanup phải an toàn với worker/acceptance và không cắt bằng chứng CUPS đang cần.

## An toàn

- API key random, hiển thị một lần, chỉ lưu hash. Thu hồi riêng từng client.
- Password quản trị được hash có salt; session phía server, HttpOnly/SameSite cookie, CSRF cho mutation. Không có password mặc định.
- Không bật CORS wildcard. Không mở API docs chứa chức năng quản trị không xác thực. Không expose public Internet ở bản đầu.
- LAN cũng không miễn xác thực. HTTP chỉ phù hợp LAN tin cậy; hỗ trợ cấu hình TLS/reverse proxy và Secure cookie khi vận hành.
- Validate tên queue, URI/protocol, options, số bản, nội dung/loại file; chặn path traversal. Không driver command/raw shell từ request.
- CUPS không sẵn sàng phải hiển thị rõ. Backend giả chỉ dùng để test/development có nhãn rõ, không cho production báo giả là in thành công.

## Kế hoạch và acceptance gates

1. Ghi glossary, thiết kế, API và runbook.
2. Làm acceptance bền vững/auth/idempotency trước; kiểm thử qua public interface với CUPS adapter giả.
3. Làm adapter CUPS thật + đối soát, queue control/cancel; bổ sung restart/unknown tests.
4. Trang quản trị hoạt động offline, loading/empty/error states, không lộ API key qua lịch sử.
5. Package/systemd/installer có backup/rollback, không ảnh hưởng gateway cũ trước khi được cho phép.
6. Chạy tests, lint/static checks và browser smoke. Báo rõ phần chưa kiểm chứng Linux/CUPS/Canon thật.

Trước production trên EDATEC bắt buộc thử: in PDF thật, ZPL trên thiết bị phù hợp, offline/hết giấy, pause/resume/cancel, gửi trùng, restart sau nhận, mất phản hồi lúc giao, giới hạn dung lượng, quyền client và rollback. Không coi fake backend tests là bằng chứng in vật lý.
