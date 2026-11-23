# Tiến độ OpenGuard Kids

## Phạm vi và tiến độ ngày 23/11/2026

Toàn bộ CORE, SPEC-EXTENDED và E1–E12 có mã truy vết, phụ thuộc, cách kiểm và trạng thái riêng. Các mốc cũ phía dưới giữ nguyên để so sánh. [Ma trận yêu cầu](docs/REQUIREMENTS_TRACEABILITY.md) · [Kế hoạch](docs/IMPLEMENTATION_PLAN.md) · [Quyết định](docs/DECISIONS.md)

Trong 52 tiêu chí CORE, 31 có trạng thái VERIFIED và 21 còn IN PROGRESS. Trong 9 tiêu chí SPEC-EXTENDED, 7 chưa bắt đầu, 1 đang triển khai và 1 đã có mã chờ nghiệm thu. Trong 12 PRODUCT-EXTRAS, 9 có thành phần từ chức năng gốc và 3 chưa bắt đầu. Bảng BLOCKED trong ma trận ghi riêng những phép thử phụ thuộc môi trường hoặc người xác nhận. Các số đếm phản ánh tiêu chí, mỗi tiêu chí có khối lượng khác nhau.

Lượt cuối trên Windows build 26200 và Python 3.11.9 đạt **197/197 pytest** trong **109,42 giây**, 0 lỗi, 0 bỏ qua và 1 cảnh báo deprecation từ Starlette TestClient. Edge headless dùng hai tài khoản trẻ và kiểm CLI/tray cộng tổng cộng 1.800 giây trên đúng máy giả. Khi cookie thuộc hồ sơ khác, trang từ khay dừng trước mọi truy vấn dữ liệu gia đình. [Nhật ký](docs/evidence/full-scope-2026-11-23/verification-final.txt) · [JUnit](docs/evidence/full-scope-2026-11-23/verification-final.xml) · [Ảnh chụp log](docs/evidence/full-scope-2026-11-23/verification-final.png) · [Biên bản Edge](docs/evidence/full-scope-2026-11-23/browser-final.txt)

|Phần việc|Trạng thái|Kết quả hoặc bước tiếp|
|---|---|---|
|Nhận và áp dụng policy/lệnh trong tray mô phỏng|VERIFIED|WSS thông báo phiên bản, nạp policy đã ký trước lệnh cùng heartbeat, thử lại sau gián đoạn và hiện ACK theo UUID. 30 lần từ PUT tới áp policy và từ gửi +15 phút tới phụ huynh thấy ACK. [CSV](docs/evidence/full-scope-2026-11-23/tray-worker/live-sync-samples.csv)|
|Ổn định dữ liệu khi offline hoặc bị thu hồi|VERIFIED|Bộ đếm vẫn tính khi đồng bộ bị treo. Kiểm ghi đúng lần ghép máy ngay trong giao dịch SQLite để một tác vụ cũ không ghi lại dữ liệu đã xóa. [Kiểm thử](tests/test_agent_session.py)|
|Dữ liệu F2 và ngoại lệ an toàn|VERIFIED|Đủ 200 hostname công khai và 240 fixture, 6 nhóm. Hai danh sách riêng cho phân loại và ngoại lệ. [Nguồn danh mục](agent/data/README.md)|
|Ghi thời lượng app trên Windows, NT1/NT6|IN PROGRESS|Code lấy mẫu chỉ đúng console/SID và có đồng ý. Input tổng hợp và báo cáo hai vai trò đã thử. Còn kiểm desktop, sai số một giờ, trợ giúp và hành động native trên VM. [Agent và phép đo](docs/AGENT.md)|
|TLS, CERT pinning và luồng service lab|IN PROGRESS|HTTPS/WSS đã qua đổi chứng chỉ cùng CA. Cài/gỡ/recovery đang ở mức mock, IPC có phép thử cục bộ. [Diễn tập TLS](docs/SYNC.md) · [Service lab](scripts/lab/SERVICE.md)|
|Báo cáo kỹ thuật|IN PROGRESS|Phụ lục mới có ảnh Wireshark mở PCAP, dữ liệu CPU/RAM, độ trễ, suy giảm khi lỗi và thử đồng hồ. Còn phép thử trên Windows VM và LAN. [PDF có đủ M1–M6](docs/BaoCaoKyThuatMang-2026-11-23.pdf) · [CSV và phương pháp](docs/NETWORK_EVIDENCE.md)|
|Nghiệm thu máy đích|BLOCKED|Cần VM sạch, hai tài khoản, snapshot và quyền riêng cho từng thử nghiệm SCM, adapter, khóa/ứng dụng. Mã service hiện chỉ cung cấp IPC trạng thái, cần tích hợp phần thực thi. Cần nhóm sinh viên xác nhận đóng góp.|
|SPEC-EXTENDED và E1–E12|IN PROGRESS|Đã tích hợp một phần quy trình gỡ và dùng lại được một số thành phần gốc. Quota nhóm, nhiệm vụ thưởng, DoH/WFP, PDF/CSV gia đình, 20-20-20, pause hai giờ, TOTP và các luồng sản phẩm vẫn có mục nghiệm thu riêng trong ma trận.|

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
