# Cài và gỡ service IPC trên Windows VM

Service cung cấp trạng thái qua named pipe cho một Standard User được chọn. Bản cài lab gắn với một thiết bị thử nghiệm đã ghép trong một gia đình giả. Quản trị viên VM thao tác tại console. Phụ huynh đăng nhập riêng qua HTTPS để xác nhận đúng thiết bị. Mật khẩu được nhập tại dấu nhắc ẩn, cookie nằm trong bộ nhớ của tiến trình quản trị.

## Chuẩn bị

1. Tạo snapshot Windows 10/11 x64 và Standard User cục bộ, xác định SID qua `whoami /user` trong phiên thử. Tài khoản phải đang bật và nằm ngoài nhóm Administrators cả trực tiếp lẫn qua nhóm khác.
2. Cài Python x64 cho toàn máy ở Program Files. Dùng bản mã đã kiểm tra tại `C:\Program Files\OpenGuardKidsLab`. Tạo **venv mới tại đây** bằng Python từ Program Files và cài `requirements.txt` vào venv. Chỉ cần bản mã ứng dụng, script và các gói. Giữ database, secret và kho DPAPI của tray ở vị trí riêng. Các cây thư mục chương trình/Python phải do SYSTEM, Administrators hoặc TrustedInstaller sở hữu, với quyền ghi giới hạn cho các chủ thể này. Helper kiểm tra DACL, các thư mục cha dưới Program Files và reparse point trước khi chạy Python với `-I -B`. Sửa quyền của bản cài VM hoặc chuẩn bị lại bản sạch nếu kiểm tra thất bại.
3. Khởi động server thử qua HTTPS, ghép máy từ tray bằng đúng Standard User. Ghi lại child ID và device UUID của hồ sơ tại dashboard/API hoặc nhật ký enrollment. Đối chiếu SHA-256 của chứng chỉ máy chủ qua kênh tin cậy. Với CA riêng, đặt bản PEM công khai trong thư mục chương trình được bảo vệ.

## Cài và khởi động

```powershell
$root = 'C:\Program Files\OpenGuardKidsLab'
Set-Location -LiteralPath $root
.\scripts\lab\service.ps1 -Action Preview

# Chỉ trong VM, dùng PowerShell x64 Administrator
$sid = Read-Host 'SID của Standard User thử nghiệm'
$child = [int](Read-Host 'Child ID')
$device = Read-Host 'Device UUID'
$pin = Read-Host 'SHA-256 của chứng chỉ TLS'
.\scripts\lab\service.ps1 -Action Install -ChildSid $sid -ChildId $child -DeviceId $device -Server https://127.0.0.1:8443 -Pin $pin -Ca "$root\ca.pem"
.\scripts\lab\service.ps1 -Action Start
```

Install tạo mới `%ProgramData%\OpenGuardKidsLab` với owner Administrators và DACL được bảo vệ ngay lúc tạo. SYSTEM và Administrators được ghi, LocalService được đọc/chạy. Tệp cấu hình giữ SID, child ID, UUID thiết bị, mã tài khoản phụ huynh đã đăng nhập và chứng minh quyền quản lý thiết bị, địa chỉ HTTPS, pin và CA công khai. Nếu đường dẫn hoặc tên SCM có sẵn, Install dừng trước khi tiếp quản. Start yêu cầu xác nhận riêng. Bản phát triển chạy dưới LocalService dạng demand-start. Khi service bị crash, SCM được cấu hình thử lại sau 60 giây, 120 giây, rồi dừng, reset bộ đếm sau 24 giờ. Đọc lại cấu hình qua SCM là một phần của Install. [Microsoft về cấu hình khôi phục service](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-changeserviceconfig2w)

Trong phiên Standard User đã chọn, chạy lệnh sau từ thư mục chương trình. Kết quả exit 0 xác nhận client đã đối chiếu PID của SCM và nhận phản hồi từ đúng pipe.

```powershell
& '.\.venv\Scripts\python.exe' -I -B '.\agent\lab_service.py' --status
```

## Gỡ có xác nhận

```powershell
.\scripts\lab\service.ps1 -Action Remove
```

Remove cần xác nhận tên service và đăng nhập mới của phụ huynh sở hữu thiết bị. Server nhận CSRF và kiểm tra phiên tối đa 5 phút tuổi, ghi sự kiện ủy quyền rồi thu hồi token. Script xác nhận tên, đường dẫn và tài khoản của service, vô hiệu hóa khởi động, yêu cầu dừng qua SCM rồi xóa đăng ký. Sau khi SCM xác nhận đăng ký đã hết, script gửi một báo cáo hoàn tất riêng đến server. Phụ huynh và trẻ phân biệt được hai mốc qua nhật ký. Mốc thứ hai là báo cáo từ công cụ cục bộ. Server không trực tiếp quan sát SCM trên VM.

Nếu mạng hỏng sau ủy quyền hoặc không dừng được service, giữ ProgramData rồi chạy lại Remove. Trình gỡ xác thực lại và tiếp tục. Việc gọi lại cùng một giai đoạn chỉ ghi một audit. Sau khi hoàn tất, script dọn các tệp cấu hình và marker do nó tạo. Mục lạ hoặc reparse point khiến script dừng. Mở lại tray để xử lý việc thu hồi và xóa cache của Standard User. Thao tác xóa toàn bộ hồ sơ trên dashboard có biên nhận riêng cho bộ đệm này.

Nếu hồ sơ đã xóa trước hoặc trong lúc Remove, đăng nhập lại bằng cùng tài khoản phụ huynh trên server HTTPS đã ghim. Khi còn biên nhận xóa, server dùng UUID và chủ sở hữu của biên nhận để xác minh quyền gỡ cục bộ. Khi biên nhận hết hạn, công cụ đối chiếu mã tài khoản tại lần đăng nhập mới với bản cài được bảo vệ và cần phản hồi xác thực của đúng API về việc thiếu hồ sơ. Sau khi SCM đã gỡ xong, terminal báo kết quả cục bộ. Nhật ký hồ sơ vẫn đã xóa. Trạng thái xóa bộ đệm DPAPI của tray chỉ do tray xác nhận theo luồng riêng.

Bản cài cũ thiếu mã phụ huynh cần thiết bị hoặc biên nhận còn tra được trên server. Sau lần xác minh quyền gỡ thành công, marker được bảo vệ lưu mã phụ huynh để tiếp tục nếu thao tác gián đoạn. Nếu cả hồ sơ và biên nhận đã mất trước lần xác minh đầu tiên, công cụ dừng vì không xác định được chủ sở hữu. Lỗi mạng, lỗi đăng nhập, hết hạn phiên và trang 404 chung cũng dừng thao tác. Quản trị viên Windows có thể dùng công cụ SCM chuẩn tại console để cứu hộ VM khi không còn đủ dữ kiện xác minh.

Rollback dành cho lần Install bị gián đoạn trước khi có marker hoàn tất. Script thử hoàn tác ngay khi Install gặp lỗi. Nếu cần phục hồi thủ công, kiểm tra trạng thái rồi dùng `.\scripts\lab\service.ps1 -Action Rollback`.

## Nghiệm thu

Chạy `.\scripts\run-tests.ps1 -q tests/test_lab_service_setup.py` với thư mục tạm. Các bài kiểm tra dựng descriptor trong bộ nhớ, dùng SCM giả và hai phía API thử nghiệm. Để nghiệm thu trên VM, kiểm tra quyền ghi ProgramData từ hai tài khoản, IPC đúng/sai SID, recovery sau một sự cố của service thử, việc dừng có chủ đích, hủy từng xác nhận, mất kết nối lúc gỡ và hai bản ghi audit. Ghi riêng cấu hình SCM, kết quả và thời gian đo của chính VM được phép.
