# Tùy chọn in đơn giản

## Cấu hình máy in

Trong **Máy in → Thêm máy in**, chọn thiết bị và driver, đặt tên rồi lưu. Nếu thiết bị không xuất hiện khi quét, có thể nhập địa chỉ mạng trong phần thu gọn. Có thể đăng ký hàng đợi CUPS đã cấu hình từ trước bằng **Dùng cấu hình có sẵn**. Máy được nhập theo cách này bắt đầu ở trạng thái tạm dừng để người quản trị kiểm tra trước khi cho in.

**Sửa cấu hình** chỉ dùng để đổi tên, địa chỉ thiết bị hoặc driver. Mặc định khay giấy, loại giấy, màu và các tùy chọn riêng do CUPS/driver quyết định; trang quản trị không bắt người dùng thiết lập chúng. Các mặc định cũ trong cơ sở dữ liệu được giữ khi chỉ đổi tên. Khi đổi địa chỉ hoặc driver, ứng dụng yêu cầu xác nhận xóa mặc định cũ để không áp giá trị của driver cũ lên máy mới.

## Tùy chọn của lệnh PDF

Client lấy `capabilities.supported` và `capabilities.defaults` từ `GET /api/v1/printers`. Ba trường được công bố là:

| Khóa trong `options` | Ý nghĩa |
| --- | --- |
| `paper_size` | Khổ giấy, ví dụ `A4` hoặc `Letter` |
| `sides` | Một mặt hoặc hai mặt: `one-sided`, `two-sided-long-edge`, `two-sided-short-edge` |
| `scaling` | Cách căn PDF: `auto`, `auto-fit`, `fit`, `fill`, `none` nếu driver/CUPS báo hỗ trợ |

Chỉ gửi giá trị nằm trong danh sách `supported` của máy được chọn. Bỏ qua trường nào thì dùng mặc định CUPS/driver cho trường đó. `fit` giữ toàn bộ nội dung trong vùng in, `fill` có thể cắt mép, còn `none` giữ kích thước gốc và cũng có thể cắt mép. Các chế độ này không bảo đảm in sát mép giấy. [Tài liệu tùy chọn CUPS](https://openprinting.github.io/cups/doc/options.html) mô tả hành vi phụ thuộc vào kích thước khai báo trong file.

Ví dụ: `POST /api/v1/jobs` dạng `multipart/form-data` cùng file PDF, `printer_id` và `Idempotency-Key`, thêm field `options` với giá trị `{"paper_size":"A4","sides":"two-sided-long-edge","scaling":"fit"}` khi cần. Trang `/client` chỉ hiện ba lựa chọn trên và chỉ hiện giá trị mà máy công bố. ZPL được gửi nguyên bản; không áp dụng các tùy chọn PDF.

Ứng dụng vẫn kiểm tra lựa chọn và ràng buộc mà CUPS/driver cung cấp khi nhận lệnh. Nếu mapping máy hoặc driver thay đổi ngoài ứng dụng, cần kiểm tra và đăng ký lại trước khi gửi lệnh mới. Nếu phản hồi của lệnh in bị mất, gửi lại **cùng** `Idempotency-Key` và cùng nội dung để tránh tạo lệnh trùng; không tự in lại lệnh có kết quả chưa rõ.
