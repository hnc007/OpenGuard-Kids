# Chạy agent OpenGuard Kids trên Windows

Từ thư mục gốc repository, sau khi chuẩn bị môi trường theo [hướng dẫn cài đặt](../README.md), chạy phiên minh họa bằng tài khoản thông thường.

```powershell
.\scripts\run-agent.ps1
```

Phiên này dùng đồng hồ giả, tệp thử và DNS loopback. Để xem quy tắc do phụ huynh cấu hình trên dashboard, khởi động backend, tạo hồ sơ thử và lấy mã ghép 8 ký tự. Mở một cửa sổ PowerShell khác rồi chạy hai lệnh sau.

```powershell
.\scripts\run-agent.ps1 -Mode enroll
.\scripts\run-agent.ps1 -Mode tray
```

Nhập mã khi được hỏi. Trong cửa sổ khay, chọn **Bắt đầu phiên mô phỏng trên hồ sơ thử nghiệm**. Đóng cửa sổ để kết thúc. Có thể kiểm bộ quyết định, đồng bộ, named pipe và bộ đếm bằng `.\scripts\run-tests.ps1 -q`.

Các tùy chọn `--lab-input-sid`, `--lab-ipc`, `--lab-dns-upstream`, `--lab-app` và `--lab-rest` dành cho VM Windows dùng riêng. Với `--lab-rest` và đúng SID Standard User, người thử có thể bật cửa sổ nhắc nghỉ, xem lý do, xin thêm giờ và tìm trợ giúp. Hướng dẫn chi tiết có cách cài và gỡ service IPC thủ công, điều kiện tài khoản và phạm vi đã thử. [Quy trình Windows Lab](../docs/AGENT.md) · [Giao thức ghép đôi, heartbeat và xóa cache](../docs/SYNC.md)
