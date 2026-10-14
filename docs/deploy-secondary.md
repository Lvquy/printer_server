# Cài và cập nhật bản in phụ trên Pi

Áp dụng cho service `print-appliance-secondary.service` (ví dụ cổng 8082).
Các lệnh dưới đây chạy trên Mac, trừ những lệnh có `ssh`. Địa chỉ Pi là tham số,
không nằm cố định trong script. Repo GitHub riêng không cần được đăng nhập trên Pi:
script chuyển đúng commit đang checkout qua SSH.

## Cài lần đầu

1. Trên Mac, commit mã nguồn rồi chuyển một checkout sạch sang Pi. Có thể dùng
   `scp`/SFTP hoặc một Git bundle; không cần token GitHub trên Pi.
2. SSH vào Pi, tại checkout vừa chuyển, chạy:

   ```sh
   sudo ./install-secondary.sh --host 192.168.0.123 --port 8082 --check
   sudo ./install-secondary.sh --host 192.168.0.123 --port 8082
   sudo systemctl start print-appliance-secondary.service
   ```

   Thay IP bằng địa chỉ thật được gán trên Pi. Ghi lại mật khẩu web được in một
   lần rồi đổi trong giao diện. Bản cài chính ở cổng 8081 không bị thay thế.

## Mỗi lần cập nhật sau đó

Trên Mac, commit và đẩy thay đổi lên GitHub:

```sh
git add <các file đã sửa>
git commit -m "Mô tả thay đổi"
git push origin main
```

Sau đó SSH vào Pi và chạy **một script** từ checkout đã cài:

```sh
ssh pi@192.168.0.123
cd /opt/print-appliance-secondary/source
sudo ./update-secondary-from-git.sh --check
sudo ./update-secondary-from-git.sh
```

Script tự lấy nhánh `main` từ remote `origin`, nên không cần gõ `git pull` thủ
công. Nó hỏi xác nhận rồi build một môi trường Python mới, dừng **chỉ** service
phụ, kiểm tra lại lệnh in, sao lưu dữ liệu, kích hoạt môi trường mới và kiểm tra
HTTP. Mật khẩu web, client, máy in và lịch sử vẫn nằm trong
`/var/lib/print-appliance-secondary`.

Ở lần đầu chuyển sang cách này, script chưa có trong checkout trên Pi. Kéo riêng
commit chứa script một lần, sau đó chạy script để build:

```sh
sudo -i
cd /opt/print-appliance-secondary/source
git fetch origin main
git switch main 2>/dev/null || git switch -c main --track origin/main
git pull --ff-only origin main
./update-secondary-from-git.sh
```

Các lần sau chỉ cần hai lệnh `--check` và cập nhật ở trên. Script so sánh cả
commit đang chạy lẫn commit trong checkout, nên lần chuyển đổi đầu tiên vẫn build
đúng phiên bản vừa lấy về.

Nếu cập nhật thất bại khi kích hoạt, script cố khôi phục mã nguồn và môi trường
trước, rồi khởi động lại service cũ. Bản sao dữ liệu được giữ tại
`/var/backups/print-appliance-secondary/<thời điểm>/data`; không tự phục hồi cơ
sở dữ liệu cũ vì có thể làm mất lệnh in phát sinh sau khi service mới chạy.
Kiểm tra log và dữ liệu trước khi phục hồi thủ công.
Mỗi lần cập nhật giữ môi trường cũ và một bản sao dữ liệu; kiểm tra dung lượng đĩa
và chỉ xóa các bản cũ sau khi đã xác nhận không cần khôi phục.

Nếu Pi không được phép truy cập GitHub, dùng phương án thay thế từ Mac:

```sh
./deploy-secondary-ssh.sh --target pi@192.168.0.123 --check
./deploy-secondary-ssh.sh --target pi@192.168.0.123 --apply
```

## Kiểm tra sau deploy

```sh
ssh pi@192.168.0.123
systemctl is-active print-appliance.service print-appliance-secondary.service
sudo cat /opt/print-appliance-secondary/SOURCE_COMMIT
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' http://192.168.0.123:8082/client
```

Hai service dùng chung CUPS. Không import cùng một hàng đợi đang được bản 8081
quản lý vào bản phụ. Một lần cài mới và một lần cập nhật là hai thao tác khác
nhau: `install-secondary.sh` từ chối ghi đè bản đã cài; các lần sau dùng
`deploy-secondary-ssh.sh`.
