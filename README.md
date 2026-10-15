# Print Appliance

Thiết bị quản lý in chạy trên Linux/Pi trong mạng nội bộ. Ứng dụng nhận PDF qua
API, lưu lệnh in và gửi cho CUPS. Mỗi máy in đăng ký có một `printer_id` và API
key riêng.

Ứng dụng chạy bằng **systemd + Python + CUPS**, không dùng Docker. CUPS quản lý
driver, kết nối máy in và hàng đợi; Print Appliance quản lý giao diện, API và
lịch sử lệnh in.

## Cài lần đầu trên Pi

Ứng dụng được cài và truy cập qua cổng `8082`.

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

### 3. Kiểm tra trước khi cài

```sh
./install-secondary.sh --host PI_IP --port 8082 --check
```

Ví dụ:

```sh
./install-secondary.sh --host 192.168.0.123 --port 8082 --check
```

Nếu thấy `Preflight passed`, tiếp tục bước cài đặt.

### 4. Cài service

```sh
./install-secondary.sh --host PI_IP --port 8082
```

Khi hiện câu hỏi xác nhận, gõ `y` rồi nhấn Enter.

Script tạo mật khẩu quản trị mới và chỉ in mật khẩu đó một lần. Lưu ngay mật
khẩu này.

### 5. Khởi động và kiểm tra

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

```text
http://PI_IP:8082/
```

Ví dụ: `http://192.168.0.123:8082/`.

## Cấu hình máy in và API key

1. Đăng nhập trang quản trị bằng mật khẩu được tạo khi cài.
2. Vào **Máy in** → **Thêm máy in**.
3. Chọn thiết bị và driver đã có trong CUPS, sau đó lưu.
4. Tại thẻ máy in, sao chép **ID máy in** và bấm **Hiện** để lấy **API key**.
5. Dùng hai giá trị này khi cấu hình Odoo hoặc ứng dụng gửi lệnh in.

Mỗi API key chỉ được dùng với đúng máy in đã tạo ra nó. Có thể đổi key tại màn
hình máy in khi cần.

## Cập nhật sau khi sửa code

Trên máy phát triển, commit và push code lên GitHub:

```sh
git add .
```

```sh
git commit -m "Mô tả thay đổi"
```

```sh
git push origin main
```

Sau đó SSH vào Pi và chạy:

```sh
cd /opt/print-appliance-secondary/source
```

```sh
sudo ./update-secondary-from-git.sh --check
```

```sh
sudo ./update-secondary-from-git.sh
```

Script tự lấy nhánh `main`, tạo môi trường Python mới, sao lưu dữ liệu,
khởi động lại **chỉ** service cổng `8082` và kiểm tra HTTP. Không cần gõ `git
pull` thủ công.

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
/opt/print-appliance-secondary/source/       Source đang chạy ở cổng 8082
/opt/print-appliance-secondary/.venv/        Python environment
/var/lib/print-appliance-secondary/          Database, API key, cấu hình và lệnh in
/etc/systemd/system/print-appliance-secondary.service
```

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

## Phát triển trên Mac

Khi sửa giao diện React, build lại asset trước khi commit:

```sh
npm ci
```

```sh
npm run build
```

Kiểm tra source:

```sh
uv run pytest
```

```sh
uv run ruff check .
```

## Tài liệu chi tiết

- [Hướng dẫn cập nhật](docs/deploy-secondary.md)
- [API](docs/api.md)
- [Tùy chọn PDF](docs/print-options.md)
- [Hướng dẫn kỹ thuật đầy đủ](docs/technical-report.md)
