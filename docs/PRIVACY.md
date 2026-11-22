# Dữ liệu gia đình và quyền của trẻ

## Ứng dụng này ghi nhận gì về em?

Em và bố mẹ có thể mở trang **Hôm nay em đã dùng gì?** để xem cùng một báo cáo. Ứng dụng ghi tên chương trình, số phút và tên miền như `example.org`. Những gì em viết trong bài tập, tin nhắn hay ô tìm kiếm thuộc về em. Khi có điều chưa hợp lý, em có thể bấm **Xin thêm 15 phút** hoặc **Em nghĩ trang này bị chặn nhầm**. Em xem được câu trả lời ngay trong mục Ý kiến của em.

Nếu em cần người giúp, em có thể gọi Tổng đài quốc gia bảo vệ trẻ em **111**. Danh sách an toàn được kiểm tra trước các luật chặn. [Giới thiệu Tổng đài 111](https://tongdai111.vn/sites/gioi-thieu)

## Sổ dữ liệu

|Data field|Purpose|Storage|Retention|Access|Protection|
|---|---|---|---|---|---|
|users.id, username, role, child_id|Đăng nhập và xác định quyền|SQLite máy chủ|Trong vòng đời tài khoản|Chủ tài khoản và người vận hành được ủy quyền|Tên đăng nhập riêng, kiểm tra phạm vi truy vấn|
|password_hash, failures, locked_until|Kiểm tra mật khẩu, khóa tạm sau 5 lần sai|Máy chủ|Theo tài khoản, bộ đếm đặt lại sau đăng nhập thành công|Bộ xác thực|scrypt với salt độc lập, trả lỗi chung|
|sessions.token_hash, user_id, expires_at|Phiên đăng nhập|Máy chủ và cookie của trình duyệt|12 giờ hoặc đăng xuất/đổi mật khẩu|Bộ xác thực|Băm token, cookie HttpOnly và SameSite; HTTPS đặt Secure|
|login_windows.bucket, attempts, expires_at; pair_attempts.source_hash, started, count|Hạn chế thử mã và mật khẩu|Máy chủ|Cửa sổ tính theo phút, dọn khi dùng hoặc định kỳ|Bộ xác thực|HMAC địa chỉ nguồn, không cần lưu IP rõ ở bộ đếm|
|children.id, parent_id, name_cipher|Chọn đúng hồ sơ gia đình|Máy chủ|Tới khi xóa|Phụ huynh và tài khoản của trẻ|Tên gọi mã hóa AES-GCM, khóa ngoại|
|pair_codes.digest, child_id, parent_id, created_at, expires_at|Ghép đúng hồ sơ một lần|Máy chủ|10 phút tối đa cho hiệu lực, dọn sau dùng/hết hạn|Phụ huynh nhận mã một lần|Tra HMAC, tiêu thụ trong giao dịch|
|devices.id, child_id, label, fingerprint_hash|Nhận diện một lần cài đặt|Máy chủ và agent|Tới khi xóa|Gia đình thấy nhãn máy|Nhãn mã hóa, fingerprint là HMAC của UUID cài đặt ngẫu nhiên|
|access_hash, access_until, refresh_hash, refresh_until, revoked|Xác thực và thu hồi|Hash trên máy chủ; bí mật trong kho agent|Access 15 phút; refresh có hạn và quay vòng|Agent của máy|DPAPI khi dùng client Windows, HTTPS và pin khi kết nối qua mạng|
|erasure_hash, local_wiped_at|Xác nhận thiết bị đã xóa cache cả khi thông tin đăng nhập hết hiệu lực|Máy chủ và biên nhận DPAPI của agent|Theo thiết bị, sau đó theo vòng đời biên nhận|Bộ đồng bộ|Bí mật độc lập chỉ dùng cho luồng xóa, máy chủ lưu SHA-256|
|consent_at, consent_parent_id, consent_scope|Bằng chứng về phạm vi ghép máy|Máy chủ|Theo vòng đời thiết bị|Phụ huynh, kiểm toán có quyền|Thao tác phụ huynh có CSRF và bản ghi kiểm toán|
|last_seen, last_status, used_seconds, remaining_seconds, policy_version|Báo trạng thái và số giây còn lại theo phép đo mới nhất, kể cả giờ đã được cộng|Máy chủ|Ghi đè mỗi nhịp; xóa số còn lại khi tray tạm dừng|Gia đình|Token gắn thiết bị|
|policies.child_id, version, body|Lịch, quota, luật ứng dụng/tên miền|Máy chủ và cache agent|Bản hiện hành tới khi thay/xóa; chữ ký cache hết hạn sau 7 ngày|Gia đình; agent đã ghép|Thân chính sách mã hóa ở server, cache ký Ed25519|
|usage_events.ts, device_id, child_id, policy_id, type|Gắn quyết định với thời điểm, thiết bị và luật|Máy chủ; hàng đợi agent|90 ngày; agent tối đa 10.000 mục|Gia đình xem báo cáo tổng hợp|Phân quyền theo hồ sơ; chỉ chín loại sự kiện|
|subject, duration_sec|Đếm theo ứng dụng và tên miền|Cùng sự kiện|90 ngày|Cùng nguồn báo cáo cho trẻ và phụ huynh|Tên miền chuẩn hóa theo PSL cục bộ; tên .exe không có đường dẫn; subject mã hóa|
|event_key và khóa nội bộ|Gửi lại lô an toàn|Máy chủ; vỏ sự kiện trong hàng đợi|Theo sự kiện|Bộ đồng bộ|Khóa duy nhất theo thiết bị và UUID|
|outbox_quarantine.event_id|Cách ly sự kiện không xác minh được trước khi gửi|SQLite agent|Dọn cùng bản ghi gốc khi hết hạn, vượt giới hạn hoặc xóa dữ liệu|Bộ đồng bộ|Chỉ giữ khóa tham chiếu đến bản tin đã mã hóa tại máy, log chỉ ghi số lượng|
|family_requests.id, child_id, kind, domain_cipher, status, answer_cipher, created_at, resolved_at|Trẻ gửi ý kiến và đọc phản hồi|Máy chủ|90 ngày từ khi gửi, hoặc xóa hồ sơ|Trẻ và phụ huynh của hồ sơ|Miền rút gọn, câu trả lời mã hóa; giới hạn độ dài, chỉ phụ huynh duyệt|
|audits.id, child_id, actor_id, ts, action, before_cipher, after_cipher, ip_cipher|Giải thích ai đã sửa gì|Máy chủ|90 ngày hoặc xóa hồ sơ|Gia đình; IP quản trị chỉ cho phụ huynh|Mã hóa IP và giá trị cũ/mới; không ghi mã ghép hoặc mật khẩu|
|commands.id, action, device_id, created_at, expires_at, acknowledged_at|Gửi lệnh, tránh phát lại|Máy chủ; danh sách ID đã áp ở agent|Lệnh hết hiệu lực sau 120 giây, dọn bản ghi server sau 1 ngày|Thiết bị và phụ huynh liên quan|Token, kiểm tra thời hạn, ACK|
|erasures.device_id, parent_id, proof_hash, created_at, acknowledged_at|Nhận yêu cầu xóa, chứng minh đã dọn thiết bị|Máy chủ|Chờ xác nhận; dọn sau ACK 7 ngày|Phụ huynh thấy trạng thái, thiết bị chứng minh quyền|Giữ hash của khóa xóa; biên nhận chỉ chứa định danh kỹ thuật|
|public_id_counters.resource, last_id|Ngăn tab cũ sửa nhầm hồ sơ hoặc yêu cầu vừa tạo|Máy chủ|Theo vòng đời cơ sở dữ liệu|Backend|Chỉ giữ giá trị thứ tự theo bảng, không lưu danh tính đã xóa|
|screen_clock và applied_time_commands|Giữ quota, cảnh báo lùi đồng hồ, lệnh đã nhận sau restart|SQLite agent|Theo ngày hoặc hạn lệnh; xóa khi nhận yêu cầu xóa|Bộ đếm và UI cùng phiên|Tổng cộng, không có lịch sử phím/chuột. ACL sản xuất cần lab xác minh|

Máy chủ chạy dọn dữ liệu lúc khởi động và mỗi giờ. Một bản ghi vừa vượt ngưỡng có thể chờ tới lần dọn kế tiếp. Trình mô phỏng chỉ dùng danh tính giả. Các dữ liệu mẫu thử nghiệm được đánh dấu trong tên hồ sơ. SQLite có thể còn trang cũ trong WAL, bản sao lưu hoặc snapshot hệ thống; việc xóa qua ứng dụng là xóa logic trong kho đang vận hành. Không phục hồi bản sao cũ như một máy chủ đang chạy trước khi đối chiếu lại yêu cầu xóa và thông tin thu hồi.

Dashboard xóa dữ liệu đang hiển thị ở các tab cùng phiên trình duyệt khi một tab đăng xuất, đổi mật khẩu hoặc xóa hồ sơ. Khi trở lại một tab đã ẩn, dashboard xác minh lại phiên trước khi hiện dữ liệu. Mã ghép được đưa vào đúng biểu mẫu của hồ sơ đã tạo mã.

Tới hạn lưu, API ngừng trả lịch sử yêu cầu, lệnh và kiểm toán theo thời hạn tương ứng trong bảng, kể cả khi tiến trình dọn SQLite chưa tới lượt chạy. Trẻ và phụ huynh đều chịu cùng giới hạn. Biên nhận xóa cho máy ngoại tuyến vẫn ở trạng thái chờ tới khi có xác nhận.

## Phạm vi sử dụng hợp pháp

Trước khi ghép máy, người giám hộ và trẻ cùng đọc phạm vi. Phụ huynh xác nhận quyền đại diện, việc đã giải thích và sự đồng ý của trẻ. Giới hạn mục đích là hỗ trợ thói quen dùng máy, xem lại một quyết định và bảo vệ thời gian nghỉ. Khi rút đồng ý, đóng agent, thu hồi quyền thiết bị và dùng chức năng xóa hồ sơ. Thiết bị ngoại tuyến chỉ xóa được bộ đệm khi nhận yêu cầu. Không cài trên máy của người khác để theo dõi họ. Dùng tài khoản Windows độc lập cho phiên của trẻ.

Đề gốc viện dẫn Nghị định 13/2023. Khi đối chiếu tháng 11/2026, nhóm còn phải xét Luật Bảo vệ dữ liệu cá nhân số 91/2025/QH15 và Nghị định 356/2025/NĐ-CP, cùng có hiệu lực từ 01/01/2026. Luật Trẻ em đặt cơ sở cho quyền riêng tư và quyền bày tỏ ý kiến. Các lựa chọn sản phẩm tương ứng là thu thập tối thiểu, báo cáo hai chiều, xin xét lại và xóa theo hồ sơ. Luồng xác nhận cho mọi trẻ là quyết định thiết kế thận trọng. Các ô xác nhận của phụ huynh không tự chứng minh quan hệ giám hộ ngoài đời. Cần đánh giá pháp lý độc lập trước khi triển khai trên dữ liệu thật. [Luật Trẻ em](https://vanban.chinhphu.vn/?docid=215227&pageid=27160) · [Nghị định 13 trong bối cảnh của đề](https://xaydungchinhsach.chinhphu.vn/xu-ly-du-lieu-ca-nhan-cua-tre-em-the-nao-119230418165321666.htm) · [Luật 91/2025](https://vanban.chinhphu.vn/?docid=214590&pageid=27160&typegroupid=3) · [Nghị định 356/2025](https://vanban.chinhphu.vn/?docid=216387&pageid=27160)
