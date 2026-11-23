# Kế hoạch bàn giao đầy đủ

Đề gốc quy định 02 sinh viên và 06 tuần. Chỉ thị ngày 23/11/2026 đưa tất cả điểm cộng và E1–E12 vào phạm vi. Sáu tuần là thời lượng học phần, chưa phải ước lượng đã đo cho toàn bộ phạm vi mới. [Ma trận chi tiết](REQUIREMENTS_TRACEABILITY.md) giữ ID của các tiêu chí và tách ba nhóm. Không tính một bản mô phỏng thành bằng chứng tác động Windows.

## CORE

1. Chạy đường chính từ đăng nhập đến ghép máy, sửa luật, áp dụng tại thiết bị, ghi sự kiện, xác nhận lệnh và hiển thị cùng báo cáo cho hai vai trò. Thử nhiều hồ sơ và chuyển phiên. Máy thử chỉ sử dụng dữ liệu giả.
2. Ở phiên trẻ, liên kết trực tiếp tới xin giờ, báo nhầm, báo cáo và quyền riêng tư. Dừng khi mất khay, đổi SID hay vào desktop bảo mật. Chỉ tính phần tương tác thuộc phiên. Tách tên chương trình khỏi đường dẫn và không nhận tiêu đề cửa sổ. Bộ luật ở agent là nguồn quyết định cho quota, lịch, ứng dụng, DNS và ngoại lệ.
3. Hoàn chỉnh đường đi từ tray tới service qua IPC giới hạn lệnh, xác thực SID, phiên và service. Service giữ khóa và kho chính sách với ACL đúng đặc tả. Kiểm cài, nâng cấp, gỡ, dừng và khởi động lại bằng tài khoản quản trị của VM. Tách thao tác tác động phiên trẻ ra khỏi Session 0.
4. Đo F1 bằng đồng hồ độc lập một giờ với idle, khóa, sleep, chuyển phiên và rollback. Đo F2 bằng tệp thử đã đổi tên, hai SID, ngoại lệ trợ giúp, resolver thật trên cổng 53 và trình duyệt. Chỉ đánh dấu tác động khóa/ứng dụng khi thử được đúng phiên, có giải thích và vẫn truy cập trợ giúp. Chốt độ trễ F4 từ ghi policy tới áp luật, từ gửi lệnh tới tác động và ACK.
5. Ghi bằng chứng M1–M6 theo từng môi trường. M1 cần ảnh phân tích cặp truy vấn/trả lời thật. M2 so bốn phương án trên năm tiêu chí. M3 ghi thực nghiệm hoặc blocker mỗi cấu hình. M4 cần ít nhất 30 mẫu từng đại lượng. M5 tách thời gian ngắt và thời gian phục hồi. M6 đối chiếu mốc máy chủ và monotonic. Giữ riêng số đo loopback, mô hình thời gian và phép thử trên VM.

Máy phát triển chỉ chạy luồng không đặc quyền. Nghiệm thu trên VM cần hai tài khoản, snapshot, chương trình thử có thể bỏ, quyền quản trị và đồng ý riêng cho các thay đổi service/mạng/phiên. Cài đặt trong 15 phút, đường trợ giúp khi khóa và sai số một giờ cần quan sát thực tế. Tên, MSSV và tỷ lệ đóng góp do nhóm xác nhận.

## SPEC-EXTENDED

Phụ lục dữ liệu cũng được giữ trong kế hoạch nghiệm thu. Dùng 20 mẫu giáo dục để kiểm dương tính giả, 10 giải trí phù hợp tuổi, 8 mạng xã hội, 7 trò chơi, 5 điểm cuối DoH và 10 miền chặn trung tính. Tách fixture, tên miền có nguồn công khai và số ca đã đo. Hai bộ 50 miền chặn và 20 miền giáo dục của F2.5 có tiêu chí riêng. Kết quả phân loại tên miền trò chơi chưa chứng minh bộ đếm ngân sách ứng dụng F1.6.

|Thứ tự phụ thuộc|Phần việc bắt buộc|Điều kiện bàn giao|
|---|---|---|
|F1.6 sau nguồn hoạt động tin cậy|Bảng app → nhóm trong policy ký, bộ đếm độc lập theo ngày, ngoại lệ học tập không giới hạn|Hai nhóm đổi qua lại, qua nửa đêm, idle, restart, offline, không cộng đôi hay trừ giờ học sai|
|F1.7 sau lệnh cộng giờ|Phụ huynh giao nhiệm vụ và minh bạch số phút, trẻ báo hoàn tất, phụ huynh xác nhận, lưu quyết định|Hai lần duyệt đồng thời chỉ phát một phần thưởng đúng thiết bị. Không có trường tải ảnh/video|
|F2.7 sau proxy DNS|Danh sách điểm cuối DoH, canary, chính sách trình duyệt được hỗ trợ, tài khoản Windows thường và quyền sửa adapter|Thử 5 điểm cuối an toàn, Firefox/Chromium và DNS thay tay trong VM. Lưu kết quả và giới hạn của DoT/VPN/IPv6|
|F2.8 sau xác minh quyền và ngoại lệ|Chọn WFP theo metadata ứng dụng/đích, đánh giá HTTP(S) proxy bằng bảng M2|Phạm vi SID, dừng/lỗi thì gỡ filter, miễn hỗ trợ, hoàn tác sau crash. Không giải mã TLS hoặc lưu URL/cookie|
|F3.5 sau báo cáo|Cả PDF và CSV dùng chung truy vấn đã phân quyền và mốc tuần Việt Nam|Parent và child của cùng hồ sơ có tổng khớp dashboard; từ chối hồ sơ khác, tài khoản hết phiên và hồ sơ đã xóa. Kiểm font PDF và công thức CSV|
|F3.6 sau đồng hồ hoạt động|Mỗi 20 phút dùng máy mời trẻ nhìn xa khoảng 6 m trong 20 giây, có thể đóng|Kiểm idle, pause, resume, đổi phiên, không lặp thông báo hoặc dùng camera|
|F4.8 sau bảo vệ service|Phụ huynh xác thực trên máy trẻ, ủy quyền đúng thiết bị, thu hồi, gỡ và ghi kết quả|Thử sai mật khẩu, sai gia đình, hủy, mất mạng và retry. Phân biệt đã thu hồi với đã gỡ|
|F4.9 sau đồng hồ và luật ký|Nghỉ hai giờ theo thiết bị/hồ sơ đã chọn, có kiểm toán, hiển thị thời điểm kết thúc ở hai đầu|Hết hai giờ tự phục hồi kể cả restart. Thời gian dùng mốc tin cậy; bảo vệ dữ liệu, consent và trợ giúp luôn hoạt động|
|S9 sau xác thực|TOTP từ ứng dụng xác thực, xác minh mã đầu tiên trước kích hoạt, bí mật được mã hóa, mã khôi phục ngẫu nhiên dùng một lần, thu hồi phiên sau đổi|Kiểm replay/cửa sổ thời gian, brute force, dùng lại mã cứu hộ, yêu cầu xác thực lại khi tắt hoặc tạo mã mới|

## PRODUCT-EXTRAS

|ID|Phụ thuộc và cách thực hiện|Nghiệm thu bảo mật và chức năng|
|---|---|---|
|E1|Sau audit và RBAC. Lưu bản đề xuất, ý kiến, phiên bản hiệu lực và yêu cầu sửa riêng|Trẻ có thể góp ý hoặc không đồng ý. Kiểm lịch sử và xung đột khi sửa|
|E2|Sau F1.6. Phiên tập trung có giờ bắt đầu/kết thúc, nghỉ và danh sách app học|Mỗi vai trò khởi tạo được, không đọc nội dung, giới hạn không ghi đè trợ giúp|
|E3|Sau F3.4. Hiện sổ dữ liệu dễ hiểu cùng lần đồng bộ và tình trạng xóa|Kiểm hai vai trò bao gồm tình huống máy offline, không lộ máy gia đình khác|
|E4|Sau F1/F2. Dùng trực tiếp ScreenClock, DomainRules và app_decision trên dữ liệu giả do người dùng nhập|Đối chiếu ngày/giờ Việt Nam, tên/hash, miền con, whitelist, quota. Không gửi sự kiện hoặc sửa bộ đếm thật|
|E5|Sau NT6. Hiển thị kênh trợ giúp, phạm vi và nguồn xác minh cả khi offline|Thử luật xung đột và mất mạng; chỉ đưa chỉ dẫn rõ cho thiết bị không có chức năng gọi|
|E6|Sau NT3. Gắn trao đổi bổ sung với yêu cầu và thời hạn cùng hồ sơ|Hai vai trò đọc đúng lịch sử. Không chấm điểm tần suất kiến nghị; xóa và cascade|
|E7|Sau F1.6/F3.6. Thống kê nhóm, xu hướng, quota theo phiên bản từng ngày và số lần nghỉ|Dữ liệu đến muộn, qua tuần, nhiều máy, thiếu mẫu số được ghi rõ. Không suy diễn tâm lý|
|E8|Sau quy trình cài/gỡ và S9. Hướng dẫn giới thiệu → riêng tư → tạo phụ huynh → hồ sơ → ghép → policy mặc định → xem lại → heartbeat|Chỉ bootstrap máy chủ chưa có chủ bằng quy trình cục bộ; chống tranh chấp và tiếp tục khi bị gián đoạn. Bấm giờ máy sạch|
|E9|Sau đồng bộ và E6. Hiện lần sync thành công, version, hạn cache và đề nghị chờ gửi|Rút mạng, khởi động lại, gửi lại không trùng. Chưa ACK thì chưa hiện đã gửi|
|E10|Áp dụng cho từng giao diện. Kịch bản bàn phím, focus, tương phản, đọc màn hình và viewport hẹp|Kiểm login, lịch 7×48, gửi/duyệt, thông báo lỗi và gỡ. Không phụ thuộc màu hay chuột|
|E11|Tách kho demo, khóa, tài khoản và cổng loopback; tạo toàn bộ gia đình mẫu và nhãn cố định|Chạy độc lập khi DB thật có dữ liệu; kiểm không đổi adapter, SCM hoặc khóa máy|
|E12|Sau versioning và xóa. Xuất chính sách mã hóa xác thực, nhập có xem trước và xác nhận đúng hồ sơ còn tồn tại|Từ chối sửa tệp, nhầm gia đình, phát lại ngoài ý muốn và hồ sơ đã xóa. Phục hồi bằng version mới có audit; không xuất lịch sử hay secret|

Sau mỗi phần việc, dùng database và tài khoản tạm để kiểm đường thành công, sai quyền, mất kết nối và khôi phục. Rà dữ liệu mới trong PRIVACY.md và mối đe dọa trong THREATMODEL.md trước khi đánh dấu VERIFIED. Cập nhật báo cáo, phụ lục và quay lại những cảnh thay đổi sau khi chốt bằng chứng chính thức.
