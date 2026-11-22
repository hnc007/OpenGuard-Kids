# Tiến độ OpenGuard Kids

Nguồn chấm là `QuanLyMayTinhTreEm.docx` tại gốc repo. Đề có các mục 1, 2, 4, 5, 6, 8 và phụ lục. Sơ đồ kiến trúc nằm trong văn bản. Prompt bổ sung yêu cầu chạy an toàn trên máy phát triển, TCP DNS, ma trận trạng thái, bộ đếm qua reboot và xóa thiết bị ngoại tuyến.

|Giai đoạn|Trạng thái|Bằng chứng hoặc điều kiện kết thúc|
|---|---|---|
|0. Đọc đề, kiểm kê|VERIFIED|Nhánh main sạch lúc bắt đầu. Git theo dõi README, .gitignore và đề DOCX. Đã đọc nội dung bảng, sơ đồ, tệp quan hệ và phụ lục. Python 3.11.9 x64.|
|1. Backend và phiên|IN PROGRESS|Kiểm tra migration từ DB trống, seed, login và cách ly gia đình.|
|2. Agent ↔ server|IN PROGRESS|Ghép đôi, phiên bản và cache được kiểm bằng thiết bị giả.|
|3. Đồng hồ|IN PROGRESS|Tách chấm công mô phỏng và sai số đối chiếu đồng hồ ngoài VM.|
|4. Chính sách ứng dụng và DNS|IN PROGRESS|Ưu tiên quyết định an toàn, UDP/TCP cục bộ và bộ miền giả.|
|5. Gia đình|IN PROGRESS|Báo cáo, ý kiến của trẻ, xóa và phản hồi từ thiết bị.|
|6. Windows và hiệu năng|IN PROGRESS|Nghiệm thu hệ điều hành cần máy lab riêng có quyền.|
|7. Hồ sơ nộp|IN PROGRESS|Kiểm tra từng tệp, ghi rõ bằng chứng chưa có.|

## Kiểm tra lại ngày 22/11/2026

Bảng trên ghi điểm xuất phát của từng giai đoạn. Lượt trước có [92 ca pytest đạt](docs/evidence/pytest.txt). Bản ghi dưới đây cập nhật theo mã nguồn, sử dụng thử nghiệm tổng hợp và lưu bằng chứng mới trong [thư mục riêng](docs/evidence/verification-2026-11-22).

|Giai đoạn|Trạng thái hiện tại|Kết quả và phạm vi|
|---|---|---|
|0. Kiểm kê|VERIFIED|Lịch sử Git, đề gốc, danh sách tệp và bộ bằng chứng cũ đã được đối chiếu.|
|1. Backend và phiên|VERIFIED|Đăng nhập, phân vai, CSRF, khóa tạm, thu hồi phiên, migration và seed đã chạy trên SQLite. Đã kiểm dữ liệu hiển thị giữa hai tab Edge.|
|2. Agent và đồng bộ|IN PROGRESS|Enrollment, heartbeat, chữ ký, WSS, retry và xóa ngoại tuyến đã qua kiểm thử. Named pipe chạy trên Windows; service mới trao đổi trạng thái, cần VM để kiểm hai tài khoản và SCM.|
|3. Đồng hồ và quy tắc nghỉ|IN PROGRESS|Trong mô phỏng, phút lưu bài bắt đầu ở giây 120 và quyết định nghỉ ở giây 180. Lời nhắc lab giải thích quyết định và cho trẻ đóng hoặc xin thêm giờ. Thiếu số đo một giờ trên phiên Windows và thực thi khóa OS.|
|4. Ứng dụng và DNS|IN PROGRESS|Đối chiếu tên/hash, bảo vệ ứng dụng thiết yếu và SID. Lab có phép thử Notepad giới hạn. Truy vấn DNS thật dùng upstream cục bộ giả. Chưa nghiệm thu bộ kiểm soát ứng dụng tổng quát, DNS adapter hoặc SafeSearch từ trình duyệt trên VM.|
|5. Gia đình và quyền riêng tư|VERIFIED|Hai vai trò xem cùng báo cáo, xét yêu cầu, đọc lịch sử, xóa hồ sơ và theo dõi biên nhận. Có kiểm thử hết hạn, sự kiện sai hồ sơ và mã ghép trả về chậm sau khi chuyển hồ sơ.|
|6. Windows và độ ổn định|IN PROGRESS|Có số liệu loopback cho DNS, TLS/WSS, ba tình huống phục hồi. Còn kiểm tra quyền thư mục, chuyển phiên, mất mạng thật, đường trợ giúp khi giới hạn và khôi phục cấu hình trên VM dành riêng.|
|7. Hồ sơ nộp|IN PROGRESS|Báo cáo Word 20 trang, phụ lục M1–M6 6 trang và video có phụ đề 5 phút 25 giây đã kiểm. Còn kết quả Windows Lab và thông tin đóng góp do hai sinh viên xác nhận.|

Trình tự chạy trên tài khoản Windows thường, tạo dữ liệu mẫu, ghép agent, gỡ và thử lại có trong [README](README.md). Ca cần thay đổi adapter, service hoặc tác động tới phiên Windows thuộc [hướng dẫn lab](docs/AGENT.md).

### Kết quả kiểm thử

- Lượt chạy toàn bộ dùng Windows build 26200 và Python 3.11.9. **120 đạt, 0 lỗi, 0 bỏ qua**, 75,87 giây. Có một cảnh báo ngừng hỗ trợ API TestClient của thư viện. [Log pytest](docs/evidence/verification-2026-11-22/pytest.txt) · [JUnit XML](docs/evidence/verification-2026-11-22/pytest-results.xml) · [Ảnh log](docs/evidence/verification-2026-11-22/pytest.png)
- Trong lượt trên có 110 ca ngoài mô-đun adapter và 10 ca adapter. Sau khi bổ sung hai trường hợp biên ở tệp test, cả **12/12 ca adapter** đạt trên PowerShell 5.1. Các lượt chạy bao phủ **122 trường hợp hiện tại**, giữ nguyên cấu hình mạng của máy. [Kết quả adapter](docs/evidence/verification-2026-11-22/pytest-adapter.txt)
- Edge headless kiểm luồng gia đình, mã ghép về chậm trong lúc đổi hồ sơ, đăng xuất khi còn tab khác, mở đường dẫn xin thêm giờ và xác minh phiên ở callback khôi phục lịch sử. [Log Edge](docs/evidence/verification-2026-11-22/browser.txt)
- DNS dùng socket thật trên cổng loopback tạm và upstream trả dữ liệu giả. 50/50 ca chặn đúng, 20/20 miền giáo dục giả và 20/20 miền giáo dục công khai được cho phép. Mô phỏng quota 3 phút ghi nhận cảnh báo và quyết định nghỉ ở giây 120 và 180. [CSV DNS](docs/evidence/verification-2026-11-22/dns-acceptance.csv) · [Đầu ra mô phỏng](docs/evidence/verification-2026-11-22/simulation.txt)
