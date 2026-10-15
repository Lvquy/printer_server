# Print Appliance

Thiết bị quản lý in chạy trên Linux/Pi trong mạng nội bộ. Ứng dụng nhận PDF qua
API, lưu lệnh in và gửi cho CUPS. Mỗi máy in đăng ký có một `printer_id` và API
key riêng.

Ứng dụng chạy bằng **systemd + Python + CUPS**, không dùng Docker. CUPS quản lý
driver, kết nối máy in và hàng đợi; Print Appliance quản lý giao diện, API và
lịch sử lệnh in.

## Cài lần đầu trên Pi

Ứng dụng được cài và truy cập qua IP, cổng cấu hình trong `.env`; mẫu dùng cổng
`8082`.

### 1. SSH vào Pi

Chạy trên Mac:

```sh
ssh pi@PI_IP
```

Thay `PI_IP` bằng IP của Pi, ví dụ `192.168.0.123`.

### 2. Tải source code

Chạy trên Pi:

```sh
sudo -i
```

```sh
apt-get update
```

```sh
apt-get install -y git
```

```sh
cd /opt
```

```sh
git clone https://github.com/Lvquy/printer_server.git
```

```sh
cd /opt/printer_server
```

### 3. Tạo file cấu hình

```sh
cp .env.example .env
```

```sh
nano .env
```

Sửa bốn giá trị trong file:

```dotenv
PRINT_APPLIANCE_HOST=192.168.0.123
PRINT_APPLIANCE_PORT=8082
PRINT_APPLIANCE_SERVICE_NAME=print-appliance-secondary
PRINT_APPLIANCE_ADMIN_PASSWORD=ĐẶT_MỘT_MẬT_KHẨU_RIÊNG_ÍT_NHẤT_12_KÝ_TỰ
```

`PRINT_APPLIANCE_HOST` là IP LAN của Pi. File `.env` chỉ nằm trên Pi, đã được
Git bỏ qua và không được đưa lên repository. `PRINT_APPLIANCE_SERVICE_NAME` là
tên dùng trong lệnh `systemctl`; không thêm `.service` vào giá trị này.

Vì `.env` có mật khẩu quản trị, giới hạn quyền đọc file:

```sh
chmod 600 .env
```

### 4. Kiểm tra trước khi cài

```sh
./install-secondary.sh --check
```

Nếu thấy `Preflight passed`, tiếp tục bước cài đặt.

### 5. Cài service

```sh
./install-secondary.sh
```

Khi hiện câu hỏi xác nhận, gõ `y` rồi nhấn Enter.

Mật khẩu trang quản trị lấy từ `PRINT_APPLIANCE_ADMIN_PASSWORD` trong `.env`.

### 6. Khởi động và kiểm tra

```sh
systemctl start print-appliance-secondary.service
```

```sh
systemctl is-active print-appliance-secondary.service
```

Kết quả phải là:

```text
active
```

Mở trên trình duyệt trong cùng LAN:

Mở URL theo cấu hình trong `.env`, ví dụ: `http://192.168.0.123:8082/`.

Các lệnh trên dùng tên mẫu trong `.env.example`. Nếu đổi
`PRINT_APPLIANCE_SERVICE_NAME`, thay `print-appliance-secondary` bằng tên đó.

## Cấu hình máy in và API key

1. Đăng nhập trang quản trị bằng mật khẩu trong `.env`.
2. Vào **Máy in** → **Thêm máy in**.
3. Chọn thiết bị và driver đã có trong CUPS, sau đó lưu.
4. Tại thẻ máy in, sao chép **ID máy in** và bấm **Hiện** để lấy **API key**.
5. Dùng hai giá trị này khi cấu hình Odoo hoặc ứng dụng gửi lệnh in.

Mỗi API key chỉ được dùng với đúng máy in đã tạo ra nó. Có thể đổi key tại màn
hình máy in khi cần.

## Kiểm tra khi không vào được trang web

Trên Pi:

```sh
systemctl status print-appliance-secondary.service --no-pager
```

```sh
ss -ltnp 'sport = :8082'
```

```sh
curl -i --max-time 5 http://PI_IP:8082/client
```

Xem log service:

```sh
journalctl -u print-appliance-secondary.service -n 100 --no-pager
```

## Vị trí các thành phần trên Pi

```text
/opt/printer_server/                         Source checkout dùng để cài lần đầu
/opt/PRINT_APPLIANCE_SERVICE_NAME/source/    Source đang chạy
/opt/PRINT_APPLIANCE_SERVICE_NAME/.venv/     Python environment
/var/lib/PRINT_APPLIANCE_SERVICE_NAME/       Database, API key, cấu hình và lệnh in
/etc/systemd/system/PRINT_APPLIANCE_SERVICE_NAME.service
```

Thay `PRINT_APPLIANCE_SERVICE_NAME` bằng giá trị đã đặt trong `.env`.

## Gửi PDF qua API

Lấy danh sách máy in bằng API key của máy in:

```sh
curl -sS "http://PI_IP:8082/api/v1/printers" \
  -H "Authorization: Bearer API_KEY"
```

Gửi PDF:

```sh
curl -sS "http://PI_IP:8082/api/v1/jobs" \
  -H "Authorization: Bearer API_KEY" \
  -H "Idempotency-Key: invoice-001" \
  -F "printer_id=PRINTER_ID" \
  -F "format=pdf" \
  -F "title=Phiếu in" \
  -F "copies=1" \
  -F 'options={"paper_size":"A4","sides":"one-sided","scaling":"fit"}' \
  -F "file=@phieu.pdf;type=application/pdf"
```

`options` là không bắt buộc. Ba lựa chọn hỗ trợ là:

- `paper_size`: khổ giấy, ví dụ `A4`.
- `sides`: `one-sided` hoặc `two-sided-long-edge`.
- `scaling`: `fit`, `fill`, `none` hoặc `auto`.

## Tài liệu chi tiết

- [API](docs/api.md)
- [Tùy chọn PDF](docs/print-options.md)
- [Hướng dẫn kỹ thuật đầy đủ](docs/technical-report.md)
