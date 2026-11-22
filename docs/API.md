# Hợp đồng API và mô hình dữ liệu

API phiên bản hiện tại phục vụ JSON UTF-8 tại cùng nguồn với dashboard. Mọi thời điểm epoch tính bằng giây UTC, lịch hiển thị theo Asia/Ho_Chi_Minh. Thời điểm kiểm toán được trả dạng ISO 8601 UTC. Dữ liệu seed và mô phỏng chỉ dùng cho phát triển.

|Tác nhân|Phương thức và đường dẫn|Dữ liệu và kết quả|
|---|---|---|
|Công khai|GET /health, GET /|Trạng thái migration và HTML đăng nhập|
|Trình duyệt|GET /api/auth/csrf → POST /api/auth/login|Cookie khách và csrf_token, sau đó username/password và header X-CSRF-Token. Đăng nhập cấp cookie phiên mới và CSRF mới.|
|Phiên|GET /api/auth/me, POST /api/auth/logout|Trả vai trò/phạm vi hoặc hủy phiên. Việc ghi tài nguyên gia đình đều cần CSRF.|
|Phụ huynh|POST /api/auth/password|Đổi mật khẩu hiện tại và hủy các phiên cũ.|
|Gia đình|GET /api/children; GET /api/children/{c}/devices, /policy, /reports, /requests, /audit|Truy vấn giới hạn theo quan hệ sở hữu hoặc chính tài khoản trẻ. /audit hỗ trợ offset, limit. /requests trả trang tối đa 50 bản ghi.|
|Phụ huynh|POST /api/children; POST /api/children/{c}/account|Tạo biệt danh và tài khoản child-view gắn duy nhất với trẻ.|
|Phụ huynh|POST /api/children/{c}/pairing|Ba cờ consent, child_informed, child_agrees đều phải đúng. Trả code (8 ký tự) và expires_at.|
|Agent|POST /api/enroll|Gửi code, installation_id (UUID ngẫu nhiên), label. Nhận device_id, child_id, khóa công khai xác minh, access/refresh, hạn access và erasure_token riêng cho việc xóa.|
|Agent|POST /api/devices/{d}/refresh|Refresh token chỉ dùng cho đúng thiết bị. Khi thành công thay cả hai token.|
|Agent|POST /api/devices/{d}/heartbeat; GET /api/devices/{d}/policy|Bearer access token. Heartbeat gồm status, used_seconds, policy_version; nhận server_time, phiên bản mới và lệnh chờ. GET trả thân policy và chữ ký.|
|Phụ huynh|PUT /api/children/{c}/policy|Gửi version đang xem và các thay đổi. 409 khi có người đã sửa. Lịch là bảy chuỗi 48 ký tự 0/1.|
|Agent|POST /api/devices/{d}/events|events là mảng 1–100 mục {event_id, payload}. payload gồm đúng ts, device_id, child_id, type, subject, duration_sec, policy_id. Trả số nhận và số thêm mới.|
|Trẻ|POST /api/children/{c}/requests|kind=time hoặc kind=domain kèm domain. Chỉ giữ miền đăng ký.|
|Phụ huynh|POST /api/children/{c}/requests/{r}|status approved/declined và answer. Duyệt thời gian cần device_id đang ghép, tạo đúng một lệnh cộng 15 phút trong cùng giao dịch.|
|Phụ huynh|POST /api/children/{c}/devices/{d}/commands|action thuộc lock, unlock, add_15. 201 xác nhận xếp hàng.|
|Agent|WSS /api/devices/{d}/commands/ws; POST /api/devices/{d}/commands/{id}/ack|WebSocket xác thực bằng header Bearer. Frame có commands chứa ID, action, expires_at. Agent xác nhận sau khi lưu tác động.|
|Phụ huynh|DELETE /api/children/{c}/devices/{d}|Thu hồi cả access và refresh.|
|Phụ huynh|DELETE /api/children/{c}; GET /api/erasures|Xóa cần {confirm:true,child_id:c}. Trả server_erased và devices_pending. Trang quyền riêng tư đọc các biên nhận.|
|Agent|POST /api/devices/{d}/erasure|Gửi erasure_token và acknowledge. Khóa xóa vẫn dùng được sau khi refresh đã xoay hoặc bị thu hồi. Máy chủ ghi nhận ACK sau khi agent đã xóa SQLite cục bộ.|
|Quản trị|GET /api/admin/status|Số lượng tài khoản và hồ sơ. Endpoint không cung cấp báo cáo của gia đình.|

Các giá trị nghiệp vụ trùng thời điểm được giải quyết bằng giao dịch SQLite, điều kiện phiên bản và khóa duy nhất ở DB. Một batch sự kiện có mục sai thiết bị bị hủy cả lô. HTTP 401 yêu cầu xác thực lại; 403 bác vai trò hoặc CSRF; 404 che hồ sơ ngoài phạm vi; 409 yêu cầu tải lại bản mới; 422 chỉ ra dữ liệu không hợp lệ; 429 yêu cầu chờ. Máy chủ tránh đưa giá trị bí mật hoặc URL lỗi vào nội dung phản hồi.

## Quan hệ chính

Trên heartbeat, thiết bị đang hiển thị có thể gửi `remaining_seconds` từ bộ đếm cục bộ. Bản ghi gần nhất được thay thế trong cùng giao dịch, và số còn lại trở về null khi trạng thái chuyển sang paused. Dashboard xem `GET /api/children/{c}/commands` để phân biệt lệnh đang đợi, đã được ACK và hết hạn.

Khi thu hồi và thao tác ghi của thiết bị diễn ra đồng thời, SQLite xác định thứ tự bằng khóa ghi. Thao tác xếp sau thu hồi nhận HTTP 401. API đọc/duyệt yêu cầu và đọc lịch sử cũng kiểm thời hạn tại lúc truy vấn, độc lập với giờ chạy tác vụ dọn dữ liệu.

- `users(parent) → children → devices → usage_events`, khóa ngoại cascade khi xóa hồ sơ. Child-view trỏ ngược về hồ sơ, phiên trỏ về tài khoản. Biến thể admin chỉ đọc thống kê vận hành.
- `children → policies` có một bản hiện hành, nhật ký lưu giá trị khi thay đổi. Cấu hình lịch và luật nằm cùng tài liệu để cập nhật nguyên tử. Một cặp ứng dụng gồm tên tiến trình và SHA-256.
- `children → family_requests` và `devices → commands`. Việc phụ huynh trả lời nguyên tử với tạo lệnh khi duyệt thời gian. Xét lại website trả lời trực tiếp; thay luật là hành động riêng có kiểm toán.
- `erasures → users(parent)` giữ trạng thái sau khi các khóa ngoại của trẻ đã được xóa. Bảng này không tái lập hồ sơ hoạt động. Chi tiết bảo vệ, mục đích và thời gian lưu theo trường có trong `PRIVACY.md`.
- Kể từ migration 0006, hồ sơ trẻ và yêu cầu mới lấy ID từ bộ đếm bền vững. Xóa bản ghi cuối cùng rồi tạo bản ghi mới vẫn cho ID mới, để yêu cầu đến từ tab cũ không thể áp dụng lên hồ sơ vừa tạo. Máy chủ kiểm tra quyền sở hữu riêng cho mỗi thao tác.
