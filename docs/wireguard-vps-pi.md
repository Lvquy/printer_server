# Kết nối WireGuard giữa VPS Odoo và Pi

Tài liệu này tạo tunnel riêng giữa VPS chạy Odoo và Pi chạy Print Appliance.
Odoo trên VPS gọi Pi qua địa chỉ LAN của Pi; địa chỉ này chỉ đi qua WireGuard,
không mở Print Appliance ra Internet.

Ví dụ trong tài liệu:

```text
VPS public IP:       VPS_PUBLIC_IP
VPS WireGuard IP:    10.77.77.1
Pi WireGuard IP:     10.77.77.2
Pi LAN IP:           192.168.0.123
Print Appliance:     http://192.168.0.123:8085
WireGuard UDP port:  51820
```

Thay `VPS_PUBLIC_IP` bằng public IP thực tế của VPS. Không đưa private key vào
Git, Odoo hoặc chat.

## 1. Cài WireGuard trên VPS và Pi

Trên **VPS Odoo**, chạy từng lệnh:

```sh
sudo -i
```

```sh
apt-get update
```

```sh
apt-get install -y wireguard
```

Trên **Pi**, chạy đúng ba lệnh trên. Sau đó mới làm các bước cấu hình bên dưới.

## 2. Tạo key trên mỗi máy

Chạy riêng từng lệnh trên **VPS** và **Pi** nếu máy chưa có key:

```sh
sudo -i
```

```sh
umask 077
```

```sh
wg genkey | tee /etc/wireguard/wg0.key | wg pubkey > /etc/wireguard/wg0.pub
```

```sh
cat /etc/wireguard/wg0.pub
```

Ghi public key của VPS và Pi để điền vào cấu hình ở hai bước kế tiếp. Không
chia sẻ nội dung `wg0.key`.

## 3. Cấu hình VPS Odoo

Trên VPS, tạo hoặc sửa `/etc/wireguard/wg0.conf`:

```ini
[Interface]
Address = 10.77.77.1/24
ListenPort = 51820
PrivateKey = VPS_PRIVATE_KEY

[Peer]
PublicKey = PI_PUBLIC_KEY
AllowedIPs = 10.77.77.2/32, 192.168.0.123/32
```

`192.168.0.123/32` là route để VPS gửi yêu cầu tới Print Appliance qua Pi.

Nếu VPS dùng UFW, mở UDP port WireGuard:

```sh
ufw allow 51820/udp
```

Mở cùng port UDP trong firewall của nhà cung cấp VPS nếu có.

## 4. Cấu hình Pi

Trên Pi, tạo hoặc sửa `/etc/wireguard/wg0.conf`:

```ini
[Interface]
Address = 10.77.77.2/24
PrivateKey = PI_PRIVATE_KEY

[Peer]
PublicKey = VPS_PUBLIC_KEY
Endpoint = VPS_PUBLIC_IP:51820
AllowedIPs = 10.77.77.1/32
PersistentKeepalive = 25
```

`PersistentKeepalive = 25` giúp Pi duy trì tunnel khi nằm sau router/NAT.

## 5. Bật tunnel và tự khởi động cùng máy

Chạy lệnh này một lần trên **VPS**:

```sh
systemctl enable --now wg-quick@wg0
```

Chạy cùng lệnh này một lần trên **Pi**:

```sh
systemctl enable --now wg-quick@wg0
```

Kiểm tra trên mỗi máy:

```sh
systemctl is-active wg-quick@wg0
```

Kết quả phải là `active`.

## 6. Kiểm tra kết nối

Trên Pi:

```sh
ping -c 3 10.77.77.1
```

Trên VPS:

```sh
wg show wg0
```

Phải có `latest handshake` mới.

Trên VPS, kiểm tra ứng dụng in sau khi Print Appliance đã chạy:

```sh
curl -i --max-time 5 http://192.168.0.123:8085/client
```

Kết quả cần có `HTTP/1.1 200 OK`.

## 7. Điền URL vào Odoo

Trong cấu hình máy in của Direct Print trên Odoo, dùng:

```text
http://192.168.0.123:8085
```

VPS sẽ đi tới địa chỉ này bằng route `192.168.0.123/32` của WireGuard. Máy
người dùng không cần cài WireGuard và không gọi trực tiếp tới Pi.
