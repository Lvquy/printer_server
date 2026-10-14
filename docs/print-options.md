# Tùy chọn in theo máy và driver

Tùy chọn không phải danh sách chung cho mọi máy in. Mỗi hàng đợi CUPS có thể dùng driver, PPD và phiên bản khác nhau; chính driver/CUPS quyết định tên tùy chọn và các giá trị có thể dùng. Trang quản trị đọc schema hiện có để tạo điều khiển. Việc đọc schema không thay đổi queue, driver, cấu hình, quyền client hay lệnh đã nhận.

## Ba lớp cấu hình

1. **Khả năng của hàng đợi/driver**: các nhóm, tùy chọn, giá trị và ràng buộc CUPS/driver báo cáo. Trang chỉ hiển thị giá trị thật sự có trong schema. Queue không có PPD hoặc schema không truy cập được không được suy diễn tính năng từ tên hãng/model.
2. **Mặc định quản trị**: giá trị appliance đặt cho lệnh PDF, nếu cần khác mặc định driver/CUPS. Mặc định tùy chọn phải nằm trong allowlist tương ứng. Nếu không chọn giá trị mặc định riêng, driver/CUPS tự áp dụng mặc định của nó.
3. **Quyền ghi đè của client**: tập giá trị cụ thể quản trị cho phép client chọn. Khả năng driver không tự cấp quyền. Nếu chỉ muốn cố định một lựa chọn, đặt nó làm mặc định và chỉ cấp chính giá trị đó; client thấy giá trị cố định dạng chỉ đọc, không có giá trị thay thế khác. `default_options` và `allowed_options` được lưu riêng.

Khi đăng ký queue mới, có thể giữ `default_options` và `allowed_options` rỗng. Sau đó vào **Máy in → Sửa cấu hình (modal)**, tải schema và chọn các giá trị. Không cần tạo lại queue để xem capability. Thay driver/địa chỉ mapping không được mang lựa chọn cũ sang driver mới: giao diện yêu cầu xác nhận xóa defaults/allowlist cũ; sau khi lưu, mở lại và tải schema mới rồi cấu hình lại. Fingerprint mapping/schema giúp nhận biết thay đổi. Nếu UI báo `stale`, `unavailable`, `unknown` hoặc không có lựa chọn, dừng và nhờ quản trị kiểm tra; không xem danh sách rỗng là bằng chứng driver không hỗ trợ.

## Trường thường dùng và nâng cao

Các tên như giấy/khổ giấy (`PageSize`), in hai mặt (`Duplex`), độ phân giải (`Resolution`), loại giấy (`MediaType`) hoặc căn nội dung PDF (`print-scaling`) chỉ xuất hiện nếu driver hiện tại thực sự công bố chúng. Giá trị hiển thị có thể là nhãn thân thiện, còn request sử dụng đúng keyword của driver. Một số driver gộp hai mặt, mép đóng gáy hoặc khay giấy thành các trường tương tác; chọn tổ hợp mà driver cho phép. Không có một bộ trường A4/duplex/resolution áp dụng cho mọi máy.

Trường cơ bản được trình bày trước; tùy chọn riêng của driver nằm trong phần nâng cao thu gọn. Giá trị nâng cao đã chọn vẫn được đưa vào payload, còn trường để mặc định bị bỏ khỏi `options`. Nhãn/help dùng văn bản an toàn và không thực thi markup do driver cung cấp.

`fit` cố gắng co/giãn nội dung để vừa vùng in được; `fill` lấp vùng và có thể cắt một phần nội dung; `none` không yêu cầu co giãn. Tên và khả năng thực tế tùy driver. Fit/fill không có nghĩa là in tràn lề/borderless: không được coi là true borderless nếu driver không có lựa chọn riêng và đã xác minh trên máy thật. Cũng không được suy ra tính năng từ tên Canon hay hãng khác.

## Trang client và định dạng

Trang `/client` lấy capability bằng API key Bearer và chỉ nhận các tùy chọn/giá trị đang được cấp cho client đó. Chọn **Dùng mặc định** sẽ không thêm khóa tương ứng vào `options`; một giá trị thay thế chỉ được gửi khi chọn rõ. Đổi máy in hoặc định dạng tải lại giao diện tùy chọn, không mang lựa chọn ẩn từ máy trước. Không có quyền đổi tùy chọn nhưng schema vẫn đọc được: client có thể in bằng mặc định (`options:{}`). Nếu schema stale/không đọc được, không gửi lệnh mới.

Tùy chọn PPD thường dành cho đường in PDF qua CUPS. ZPL được gửi nguyên bản; giao diện không quảng bá tùy chọn PDF là đang áp dụng cho ZPL và gửi `options` rỗng. Các quyền/định dạng được cấu hình riêng cho từng máy.

CUPS/driver vẫn là authority cuối cùng. Appliance kiểm tra giá trị enum và các constraint nó nhận được; một số driver có thể có quy tắc kết hợp phức tạp mà schema hoặc binding không biểu diễn đầy đủ. Khi server từ chối do tổ hợp không hợp lệ, sửa lựa chọn hoặc nhờ quản trị kiểm tra queue/PPD; không thử lặp lại bằng mã yêu cầu mới. Trước rollout, xác nhận constraint extraction trên đúng pycups/PPD mục tiêu: đã quan sát binding pycups 2.0.1 trả danh sách `constraints` rỗng dù PPD Canon có nhiều `*UIConstraints`. Adapter đọc cú pháp `*UIConstraints` cặp giá trị cụ thể từ PPD khi binding bỏ sót, gộp cặp đối xứng. Đã kiểm tra đọc-only Canon thực: 70 cặp riêng biệt. Wildcard/resolver hoặc vượt giới hạn báo schema không khả dụng, không âm thầm bỏ qua. Chưa chứng minh mọi tổ hợp trên giấy.

## Khi capability thay đổi hoặc không đọc được

`available` nghĩa là có schema driver dùng được; `partial` nghĩa là CUPS chỉ báo một phần thuộc tính, không phải chứng nhận hỗ trợ đầy đủ. `unknown`/`unavailable` nghĩa là không có schema đáng tin hoặc CUPS không đọc được. `stale` nghĩa là mapping hàng đợi khác mapping đã đăng ký. Không tự sửa cấu hình, không cấp quyền và không đổi queue khi đọc schema. Hãy kiểm tra driver/queue với quản trị hệ thống; giữ nguyên cấu hình cho tới khi mapping mới được xác minh.

Trên thiết bị Linux có thể xem các lựa chọn hàng đợi bằng lệnh đọc-only:

```sh
lpoptions -p QUEUE -l
```

Thay `QUEUE` bằng tên hàng đợi CUPS thực tế. Lệnh này xem danh sách lựa chọn do CUPS báo, không thay đổi queue; không chạy lệnh cấu hình như `lpadmin` để chẩn đoán. `lpoptions -l` cũng không thay thế kiểm tra PPD, constraint hoặc thử nghiệm driver/máy thật.

## Gửi lệnh và retry an toàn

`options` trong API là object chỉ chứa các lựa chọn client được phép thay đổi; `{}` dùng cấu hình mặc định. Mỗi thao tác in mới có `Idempotency-Key` mới. Nếu mất phản hồi hoặc gặp lỗi 5xx, `/client` giữ nguyên file, máy, định dạng, tùy chọn, nội dung biểu mẫu và mã yêu cầu trong bộ nhớ rồi chỉ cung cấp nút **Gửi lại cùng yêu cầu**. Bấm nút đó gửi lại chính xác cùng mã/nội dung để nhận kết quả ban đầu nếu server đã lưu; không sửa tùy chọn hay tạo mã mới để vượt qua kết quả chưa rõ. Sau khi đóng/tải lại tab, kiểm tra lịch sử hoặc nhờ quản trị xác minh trước khi gửi lệnh khác. Lệnh `unknown` không được tự gửi lại.

Lệnh đã nhận lưu ảnh chụp cấu hình của thời điểm nhận; sửa defaults/allowlist sau này không đổi lệnh cũ. Từ 0.1.5, API key lưu trong sessionStorage theo tab để reload tự kết nối; Ngắt kết nối hoặc API 401 xóa key. Không ghi vào localStorage, URL hoặc tài liệu/ảnh chụp. File và request chưa xác nhận vẫn chỉ trong bộ nhớ, không được phục hồi khi reload.

## Ví dụ Canon LBP6230/6240

1. Mở **Máy in → Sửa cấu hình**. Khổ giấy chọn **A4** nếu muốn cố định. Trong “Giá trị client được phép chọn”, tích A4/A5 chỉ khi muốn cấp quyền đổi.
2. **In hai mặt**: một mặt, lật cạnh dài, hoặc lật cạnh ngắn. Với driver này, cạnh dài dùng **Cạnh đóng gáy: Trái**; cạnh ngắn dùng **Trên** trong nhóm nâng cao. Tổ hợp trái quy tắc hiện lỗi, không tự đổi thay bạn. A5 và một số khổ/loại giấy khác không dùng được duplex theo PPD.
3. **Căn nội dung PDF** chỉ hiện khi CUPS báo `print-scaling-supported`. Thiết bị mục tiêu báo auto/auto-fit/fit/fill/none. Fit giữ đủ nội dung trong vùng in; fill có thể cắt nội dung. Không hứa in sát mép giấy trên Canon laser.
4. Mặc định tự được tích trong quyền được phép. Muốn client chỉ dùng mặc định thì không tích thêm giá trị khác. **Lưu cấu hình** mới thay đổi server; Hủy không áp dụng.
5. Client làm mới danh sách ở `/client`, chọn giá trị đã cấp hoặc **Dùng mặc định**. Lệnh đã nhận giữ ảnh chụp cấu hình riêng.

## Modal, quyền máy và thông báo

**Clients → Thêm client / Sửa client** mở modal. **Máy được cấp** là dropdown có checkbox: gõ tên, tích từng máy; các thẻ bên ngoài thể hiện máy đã chọn, bấm × để bỏ. Không cần giữ Ctrl/Cmd. Không chọn máy thì client chưa được gửi tới máy nào. Đổi tên riêng không ghi đè quyền bị thu hồi đồng thời.

**Thêm/Xem/Sửa** máy in/client dùng modal. **Lệnh in → Xem** mở chi tiết và thao tác vận hành được phép; không sửa nội dung/lịch sử đã nhận. Đóng khi chưa lưu sẽ hỏi xác nhận; trong lúc gửi, khóa thao tác để tránh trùng.

Thông báo thao tác là toast nhỏ: thành công tự ẩn khoảng 5 giây (tạm dừng hover/focus), có nút đóng. API key mới hiển thị riêng trong modal: sao chép thủ công nếu HTTP không hỗ trợ clipboard. Đóng modal là không xem lại key. Cảnh báo chưa rõ kết quả và mã retry vẫn giữ trong trang, không biến mất theo toast.
