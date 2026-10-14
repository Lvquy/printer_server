# Local Print Appliance

EDATEC là thiết bị quản lý in tại chỗ có API. Nhiều ứng dụng gửi yêu cầu tới cùng thiết bị, còn người vận hành quản lý máy in và lệnh in qua web.

## Language

**Thiết bị quản lý in (Print appliance)**:
Máy tính tại địa điểm in, tiếp nhận yêu cầu của client và điều phối việc in trên các máy được đăng ký.
_Avoid_: Web server nghiệp vụ, hệ thống quản lý kho.

**Client**:
Một ứng dụng hoặc tích hợp được cấp danh tính riêng để gửi lệnh và xem các lệnh của chính mình; không nhất thiết là một người dùng hay một PDA.
_Avoid_: Tài khoản quản trị, máy in.

**Máy in đăng ký (Registered printer)**:
Máy in mà thiết bị quản lý in đã đăng ký với một định danh ổn định. Địa chỉ kết nối, khả năng in và cấu hình của nó có thể thay đổi.
_Avoid_: IP máy in, tên hãng, điểm in nghiệp vụ.

**Lệnh in (Print job)**:
Một yêu cầu in được nhận và lưu an toàn, gồm nội dung, máy được chọn, tùy chọn và thông tin client.
_Avoid_: Lần gửi HTTP, trang giấy.

**Lần gửi (Submission attempt)**:
Một lần chuyển lệnh xuống hệ thống in. Nhận lại cùng yêu cầu từ client không mặc nhiên tạo một lần gửi mới.
_Avoid_: Lệnh in mới, in lại.

**Ảnh chụp cấu hình (Printer snapshot)**:
Bản cấu hình máy và tùy chọn được giữ cho một lệnh tại thời điểm nhận, không tự đổi theo cấu hình hiện tại.
_Avoid_: Cấu hình mới nhất.

**Hệ thống in (Print system)**:
Phần quản lý driver, truyền dữ liệu, hàng đợi và phản hồi xử lý của máy in tại địa điểm.
_Avoid_: Client nghiệp vụ.

**Hoàn thành (Completed)**:
Hệ thống in báo lệnh hoàn thành. Mức xác nhận này không mặc nhiên chứng minh giấy đã ra khỏi mọi model máy in.
_Avoid_: Đã nhận, đã giao, đã ra giấy chắc chắn.

**Chưa rõ kết quả (Unknown outcome)**:
Không có đủ bằng chứng xác định lần giao lệnh đã xảy ra hoặc kết thúc thế nào. Không được tự gửi lại một lệnh như vậy.
_Avoid_: Chắc chắn thất bại, an toàn để in lại.

**Tiếp tục (Resume)**:
Người vận hành cho phép tiếp tục một lệnh đang giữ hoặc hàng đợi của máy. Không phải yêu cầu tự in lại một lệnh chưa rõ kết quả.
_Avoid_: Retry mọi lỗi.

**Hủy (Cancel)**:
Hủy chắc chắn một lệnh chưa giao, hoặc yêu cầu hệ thống in hủy một lệnh đã giao và ghi nhận kết quả thực tế. Không thể thu hồi phần giấy đã in.
_Avoid_: Xóa lịch sử, cam kết dừng giấy tức thì.
