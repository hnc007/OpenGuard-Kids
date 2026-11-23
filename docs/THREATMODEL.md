# Mô hình mối đe dọa của OpenGuard Kids

Phụ huynh có quyền quyết định trong gia đình mình. Trẻ có quyền nhìn thấy và phản hồi quyết định liên quan đến mình. Máy chủ, tài khoản Windows của trẻ và người quản trị máy chủ là ba ranh giới tin cậy riêng. Bản phát triển chạy với dữ liệu giả trên loopback. Việc đưa service đặc quyền hoặc bộ lọc lên máy thật cần bằng chứng nghiệm thu trong VM.

## Tài sản và ranh giới

| Ranh giới | Tài sản | Thành phần ra quyết định |
|---|---|---|
| Trình duyệt → API gia đình | Phiên, quyền sở hữu, biệt danh, báo cáo, yêu cầu | Máy chủ kiểm tra cookie, vai trò, quan hệ với hồ sơ và CSRF cho thao tác ghi. |
| Agent → API thiết bị | Mã ghép, access/refresh token, bằng chứng xóa, phiên bản chính sách | Máy chủ ràng buộc thông tin xác thực với thiết bị. Agent kiểm chứng TLS và chính sách. |
| Phiên trẻ → agent | Hiển thị trạng thái, đồng ý, quyết định chặn, yêu cầu trợ giúp | Giới hạn theo phiên trẻ và trạng thái hiển thị. Tách phần có đặc quyền khỏi UI khi nghiệm thu Windows. |
| SQLite, bản sao lưu, kho bí mật | Dữ liệu gia đình, khóa server, token của thiết bị | AES-GCM và khóa ngoài SQLite ở server, DPAPI trên thiết bị Windows. Người vận hành quản lý ACL và bản sao lưu. |

## Phân tích STRIDE

Khả năng và tác động dưới đây là nhận định thiết kế cho bối cảnh máy gia đình có kết nối LAN. S là giả mạo, T là sửa dữ liệu, R là phủ nhận hành động, I là lộ dữ liệu, D là mất tính sẵn sàng, E là nâng quyền. Cột kiểm tra chỉ ra bằng chứng tự động hoặc phép thử còn cần thực hiện.

| Mối đe dọa | Bề mặt | Khả năng / tác động | Biện pháp | Kiểm tra | Rủi ro còn lại |
|---|---|---|---|---|---|
| S. Chiếm tài khoản phụ huynh | Login, đổi mật khẩu, cookie | Vừa / cao | scrypt có salt, khóa tài khoản 15 phút sau 5 lần sai, hạn mức theo IP băm HMAC. Đổi mật khẩu thu hồi các phiên. | `tests/test_foundation.py` kiểm tra khóa, hạn mức, hết phiên, logout. | Kẻ có mật khẩu đúng vẫn đăng nhập được. TOTP là phần mở rộng. |
| S. Giả thiết bị khi ghép | Mã ghép ngắn và endpoint enrollment | Vừa / cao | Mã ngẫu nhiên 8 ký tự, hạn 10 phút, một lần, giới hạn thử theo nguồn. Ghi phạm vi đồng ý và liên kết hồ sơ. | `tests/test_sync.py` kiểm tra hết hạn, giới hạn và hai lần dùng đồng thời. | Người nhìn thấy mã trong 10 phút có thể ghép trước. Phụ huynh cần xem danh sách thiết bị. |
| S/I. Đánh cắp token máy trẻ | Cache cục bộ, đường truyền | Vừa / cao | DPAPI theo tài khoản, TLS có ghim chứng chỉ ở agent, access 15 phút, refresh xoay vòng, thu hồi ở máy chủ. | Test binding, token hết hạn, từ chối cấu hình transport và refresh trong `tests/test_sync.py`. | Access token bị sao chép còn giá trị đến khi thu hồi hoặc hết hạn. DPAPI phụ thuộc độ an toàn của tài khoản Windows. |
| T. Chỉnh nội dung hoặc phát lại policy | Cache SQLite, server response | Vừa / cao | Chữ ký Ed25519, kiểm tra device_id, hạn và phiên bản; hết 7 ngày thì dừng áp dụng policy cũ. | Test chữ ký, phiên bản cũ và cache hết hạn trong `tests/test_sync.py`. | Kẻ can thiệp được runtime đặc quyền hoặc nắm khóa chính của server có thể làm sai quyết định. |
| E/I. Truy cập sang gia đình khác | URL trẻ, thiết bị, yêu cầu, audit | Vừa / cao | Ràng buộc quyền sở hữu trong truy vấn SQL. Tài khoản child-view gắn đúng 1 trẻ. Admin chỉ có số lượng tổng hợp tại endpoint quản trị. | Test phạm vi child/parent/admin, IDOR và báo cáo chung trong các bộ foundation, family. | Endpoint mới cần tiếp tục dùng bộ lọc quyền sở hữu và có kiểm thử chéo hai gia đình. |
| T/R. Thay luật hoặc ra lệnh ngoài ý muốn | Phiên web và thao tác trình duyệt | Vừa / cao | Cookie HttpOnly, SameSite=Lax, Secure trong HTTPS, CSRF gắn phiên, kiểm tra Origin và nhật ký thay đổi có actor, địa chỉ mã hóa, cũ/mới. | Test CSRF cũ, sai Origin, lịch sử thay đổi và địa chỉ ẩn với trẻ trong foundation. | XSS cùng origin hoặc tài khoản Windows bị chiếm có thể thao tác trong phiên. |
| I. Rò database | Tệp SQLite, WAL, ảnh đĩa | Vừa / cao | Mã hóa biệt danh, nhãn máy, nội dung luật, subject và phản hồi bằng AES-GCM. Băm thông tin xác thực. Server tách khóa khỏi DB. | Test kiểm tra dữ liệu lưu, xác thực AES-GCM khi khóa sai, xóa dây chuyền. | ID, loại sự kiện, thời điểm và quan hệ vẫn lộ qua cấu trúc bảng. Mất cả khóa và DB làm mất bảo mật. |
| I. Log hay backup giữ dữ liệu đã xóa | Log HTTP và WebSocket, lỗi kiểm tra dữ liệu, bản sao lưu | Vừa / cao | Bộ lọc tại máy chủ loại nhật ký truy cập thô. Phản hồi lỗi đầu vào chung, SQL ẩn tham số. API lọc lịch sử khi hết hạn và dọn DB định kỳ. | TestClient dùng DB tạm và Uvicorn thật qua loopback kiểm log HTTP/WS, thời hạn và thao tác xóa. | Reverse proxy, dịch vụ sao lưu và snapshot do người vận hành quản lý cần lịch xóa và quy trình khôi phục riêng. |
| T/D. Trẻ tìm cách vượt lịch hoặc chặn | Đồng hồ, DNS, tài khoản Windows, Safe Mode | Cao / vừa | Bộ đếm monotonic, mốc server và trạng thái lưu. Kiểm tra bằng tài khoản chuẩn trong lab; giữ trợ giúp và fail-open khi không xác minh được điều kiện an toàn. | `tests/test_agent_clock.py` mô phỏng lùi 3 giờ, ngủ, restart. Đo trên Windows thật còn chờ lab. | DNS mã hóa, VPN, IP trực tiếp, tài khoản khác hay quyền admin có thể vượt bộ lọc. |
| E. Lợi dụng giao tiếp tray–service | Lệnh gửi từ tiến trình user tới LocalSystem | Vừa / cao | Named pipe lab chỉ phục vụ trạng thái, có DACL cho SID đã chỉ định và service, xác minh SID client và đối chiếu PID server với SCM. | Xem `tests/test_agent_ipc.py` cho ACL và tập lệnh; VM cần thử mất tray, phiên khác và quyền thư mục service. | Thiết bị chưa được nghiệm thu cho triển khai service đặc quyền. |
| T/D. Lệnh WebSocket lặp lại | Reconnect, heartbeat fallback, ACK bị mất | Vừa / cao | Bearer gắn thiết bị, WSS, ID và hạn lệnh, sổ lệnh đã áp dụng lưu với hiệu ứng mô phỏng, ACK gắn thiết bị. | Test WS/ACK trong family; test idempotency qua restart trong agent clock. | Độ trễ trên hai máy Windows và hiệu ứng OS cần đo riêng. |
| I/E. Lạm dụng để theo dõi người lớn hoặc trẻ không biết | Enrollment, máy dùng chung, tray bị tắt | Vừa / rất cao | Yêu cầu đồng ý cụ thể, cung cấp màn hình minh bạch; giới hạn dữ liệu và phạm vi theo phiên. Agent phát triển dùng đầu vào giả. | Test thiếu đồng ý, cổng visibility của DNS và clock; cần kiểm tra độc lập biểu tượng/session thực ở lab. | Checkbox chỉ chứng minh đã ghi nhận thao tác, không tự xác minh quan hệ gia đình hoặc sự đồng ý tự nguyện. |
| T/D. Hàng đợi ngoại tuyến cũ/trùng | Batch upload, thiết bị bị xóa, server gián đoạn | Vừa / cao | Ràng buộc thiết bị/hồ sơ ở hai đầu, cách ly mục hỏng tại máy, UUID chống trùng, giới hạn hàng đợi và xóa theo hạn. | Kiểm gửi lại, cách ly bản ghi sai hồ sơ trước khi gửi, mất phản hồi refresh và xác nhận xóa trong sync/family. | Máy mất vĩnh viễn vẫn giữ trạng thái chờ. Dữ liệu đã xuất ra ngoài cần xử lý riêng. |
| D. Khóa nhầm và mất đường trợ giúp | DNS, hệ điều hành, lịch, chế độ ngoại tuyến | Vừa / rất cao | Ưu tiên ngoại lệ an toàn, tách quyết định mô phỏng và thao tác thật. Khi không đủ bằng chứng, giữ quyền tiếp cận hỗ trợ. | `tests/test_agent_dns.py` kiểm tra ngoại lệ và phạm vi. VM cần kiểm tra đầu-cuối trên từng máy, mạng, trình duyệt. | Danh mục miền phải được duy trì; cuộc gọi khẩn cấp phụ thuộc vào thiết bị và dịch vụ có sẵn. |

## Checklist kiểm chứng bảo mật ứng dụng

Trong luồng gỡ lab, phụ huynh xác thực lại qua HTTPS có ghim, backend ghi hai mốc cho phép gỡ và báo đã gỡ. Người có quyền quản trị máy vẫn có thể tác động service ngoài ứng dụng. Cần đối chiếu với SCM trong VM. Bộ lấy mẫu native chỉ phục vụ một SID ở phiên console đang hiển thị. Ứng dụng liên lạc và trình duyệt được miễn trong engine và trong bộ lấy mẫu. Danh sách này cần được rà khi bàn giao máy vì mỗi gia đình có thể dùng phần mềm trợ giúp khác nhau.

Phiên bản WSS chỉ đánh thức đường lấy policy đã ký. Bản tin đến cùng lệnh phải áp policy thành công trước khi ACK. Cache DNS trong RAM chỉ giữ câu trả lời địa chỉ đủ điều kiện, bỏ qua miền trợ giúp và bản ghi văn bản. Dừng giao diện vô hiệu hóa các yêu cầu đang chờ điền cache.

Các nhóm dưới đây áp dụng OWASP ASVS 5.0.0 cho phần web và API. Trạng thái mô tả phạm vi kiểm tra của đồ án. [OWASP ASVS 5.0.0](https://asvs.owasp.org/)

| Hạng mục | Bằng chứng có thể chạy lại | Việc kiểm tra tiếp |
|---|---|---|
| Xác thực và phiên | `tests/test_foundation.py` xác minh băm, lockout, rate limit, CSRF, logout, thời hạn và thuộc tính cookie | Rà vòng đời khôi phục tài khoản và việc bàn giao bí mật tại máy chủ. |
| Kiểm soát truy cập | Test theo vai trò và child_id tại foundation, kiểm tra thiết bị/refresh tại sync, kiểm tra quyết định và xóa tại family | Thêm test chéo gia đình vào mỗi endpoint mới. |
| Kiểm tra dữ liệu đầu vào và mã hóa đầu ra | Pydantic giới hạn schema, `shared/protocol.py` thu gọn domain; dashboard đặt dữ liệu người dùng qua DOM text; CSP chỉ cho tài nguyên tại chỗ | Rà các template và chỗ dựng DOM sau mỗi thay đổi UI. |
| Bảo vệ thông tin và mật mã | AES-GCM với nonce ngẫu nhiên, khóa phân mục đích, TLS/certificate pinning, DPAPI, thời hạn lưu | Kiểm tra đổi chứng chỉ và khóa mã hóa trên bản sao lab; xác minh ACL, backup và quy trình thu hồi. |
| WebSocket và API thiết bị | Chặn WebSocket trình duyệt có Origin, kiểm tra lại bearer khi poll, kiểm tra hạn và device khi ACK | Đo tải, độ trễ, ngắt ngang mạng thật và tính sẵn sàng của endpoint khi backend bị quá tải. |
| Ghi log, xử lý lỗi, cấu hình | Lịch sử cũ/mới được mã hóa, IP chỉ hiển thị cho phụ huynh; lỗi 422 không phản chiếu payload. Script tắt access log và proxy headers. | Kiểm tra logger hệ thống, reverse proxy và backup thực tế với dữ liệu giả. |

Để tái lập, chạy `.\scripts\run-tests.ps1` từ gốc repository. Các phép thử thay đổi cấu hình Windows được tách trong `scripts/lab/` và cần biên bản riêng.
