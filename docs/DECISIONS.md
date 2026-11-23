# Những quyết định cần bảo vệ khi nghiệm thu

|Quyết định|Lý do và hệ quả kiểm thử|
|---|---|
|Giữ ba nhóm yêu cầu và toàn bộ điểm cộng|CORE là điều kiện chuyển giai đoạn. Những mục còn thiếu có ID trong ma trận và điều kiện nghiệm thu trong kế hoạch.|
|IPC chỉ chấp nhận thao tác định nghĩa trước, kiểm đúng người dùng và phiên hiển thị|Service có nhiều quyền hơn tray. Không nhận đường dẫn tùy ý, câu lệnh shell hoặc PID chưa xác minh để tác động hệ điều hành.|
|Đánh dấu phiên bản thay đổi trên WSS, lấy policy qua endpoint HTTPS hiện có|Gói WSS chỉ là tín hiệu đánh thức. Mã ghép, kiểm tra chữ ký, hạn cache, so phiên bản và chống lùi vẫn ở đường đồng bộ hiện hành. Agent cũ tiếp tục dùng heartbeat.|
|Ưu tiên WFP cho F2.8|Mục tiêu là giới hạn ứng dụng và đích kết nối. Proxy lọc đường dẫn HTTPS cần truy cập nội dung vượt phạm vi riêng tư. WFP phải thử quyền, ngoại lệ, vòng đời filter và rollback trong VM trước khi bàn giao.|
|Tách hai báo cáo tuần khỏi bản sao cấu hình|PDF/CSV chứa tổng hợp có phân quyền. Bản sao E12 chỉ chứa cấu hình, phải kiểm tính xác thực và quyền nhập lại.|
|Dữ liệu ngoài thiết bị không đủ để xác nhận cưỡng chế thật|ACK, sự kiện và truy vấn DNS có thể xác minh ứng xử phần mềm. Kiểm screen lock, ứng dụng đóng và liên lạc khẩn cấp cần quan sát gốc ở phiên Windows lab.|
