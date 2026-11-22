# Kịch bản và mốc nội dung của video demo

[Video OpenGuard Kids](video.mp4) dài **5 phút 25 giây**. Bản ghi lấy trực tiếp từ một cửa sổ Edge riêng với dashboard FastAPI và một agent ghép đôi qua loopback. Hồ sơ An, các ứng dụng mẫu và lịch sử 10 ngày đều là dữ liệu giả. Mật khẩu và mã ghép được che trước khi ghi. Các trang kết quả agent hiển thị đầu ra của chương trình vừa chạy.

| Thời gian | Thao tác trong video |
|---|---|
| 00:00–00:21 | Giới thiệu nguyên tắc hai chiều, đăng nhập phụ huynh và xem hồ sơ mới có báo cáo trống. |
| 00:21–00:54 | Đọc phạm vi, xác nhận đồng ý, cấp mã 8 ký tự, ghép agent và kiểm tra heartbeat đầu tiên. |
| 00:54–01:21 | Đặt quota ngày thường và cuối tuần 3 phút, đổi ô 10 giờ thứ Hai trong lịch 7 × 48. |
| 01:21–01:47 | Cấu hình tên miền mẫu và mã SHA-256, lưu rồi tải quy tắc có chữ ký. |
| 01:47–02:12 | Quan sát kết quả bộ đếm tăng tốc, cảnh báo còn một phút, ân hạn và quyết định nghỉ ở giây 180. |
| 02:12–02:38 | Kiểm tra DNS loopback với phản hồi NOERROR, NXDOMAIN và đích SafeSearch. Upstream của bài thử trả địa chỉ dùng cho tài liệu. |
| 02:38–02:56 | Đổi tên một tệp mẫu thành game.exe. Mã băm giữ nguyên, bộ so khớp giải thích quyết định chặn. |
| 02:56–03:14 | Mở biểu đồ thực từ sự kiện giả đã nạp cho đúng thiết bị và so sánh với báo cáo của trẻ. |
| 03:14–03:49 | Trẻ xin thêm 15 phút, gửi yêu cầu xem lại tên miền và đọc màn hình quyền riêng tư. |
| 03:49–04:09 | Phụ huynh chọn thiết bị, gửi phản hồi và duyệt. Agent lưu thêm 900 giây vào bộ đếm mô phỏng rồi xác nhận về server. |
| 04:09–04:35 | Đối chiếu giá trị cũ và mới trong lịch sử. Trẻ xem trạng thái yêu cầu, phản hồi và báo cáo. |
| 04:35–05:13 | Xóa riêng hồ sơ demo dùng một lần. Máy chủ ghi trạng thái chờ. Agent kết nối lại, dọn kho cục bộ và xác nhận. |
| 05:13–05:25 | Tổng kết phạm vi. Service, khóa phiên và khả năng liên hệ khẩn cấp khi khóa cần nghiệm thu trên VM Windows. |

Video có phụ đề tiếng Việt trong hình và track văn bản tiếng Việt. Nếu trình phát chồng thêm một dòng chữ, tắt track văn bản. FFmpeg đã giải mã toàn bộ video; định dạng H.264 1280 × 906, 25 khung hình/giây. Mã SHA-256 của bản nộp là `87a7370d6e0b9ddf76b1a5505b0840199dfe5ce9e0891130720843d0acae3e76`.

## Tái lập buổi quay

Dùng Windows, MS Edge, Node.js, npm và môi trường Python đã thiết lập theo README. Các gói dưới đây chỉ phục vụ ghi hình. Chạy lệnh tại thư mục gốc repository.

```powershell
.\.venv\Scripts\python.exe -m pip install imageio-ffmpeg
npm install --prefix .local/record-tools playwright
$env:NODE_PATH = (Resolve-Path .local/record-tools/node_modules).Path
.\.venv\Scripts\python.exe -m demo.record rehearse
.\.venv\Scripts\python.exe -m demo.record record
```

Lệnh cuối chụp bản sao mã trong thư mục tạm dưới `.local`, chạy thử toàn bộ luồng, sau đó ghi phiên mới theo nhịp đọc phụ đề. Mỗi lần chạy dùng database, thông tin đăng nhập, cache và listener loopback riêng. Khi mã hóa thành công, công cụ ghi kết quả vào `demo/video.mp4`. Các quyết định liên quan tới ứng dụng và khóa phiên dùng đầu vào mô phỏng.
