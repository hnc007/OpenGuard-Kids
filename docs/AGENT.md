# Agent Windows và phạm vi nghiệm thu

Agent có hai điểm vào để giảng viên chạy bằng tài khoản thông thường. Cả hai dùng hoạt động giả. Mở PowerShell ở gốc repository sau bước setup trong README.

```powershell
.\scripts\run-agent.ps1
.\scripts\run-agent.ps1 -Mode enroll
.\scripts\run-agent.ps1 -Mode tray
```

Lệnh đầu chạy nhanh một phiên ba phút bằng đồng hồ giả, giải thích quyết định với `game.exe` và gửi truy vấn DNS thực qua loopback đến upstream mô phỏng. Lệnh ghép đôi hỏi mã 8 ký tự phụ huynh vừa cấp trên hồ sơ **thử nghiệm**. Tùy chọn `-Store`, `-Server`, `-Ca` và `-Pin` phải khớp giữa hai lệnh cuối. Để chạy phiên nhanh theo chính sách đã ghép, dùng `.\.venv\Scripts\python.exe -m agent.simulate --store .local/agent.sqlite3`.

## Cửa sổ của trẻ

Chạy khay trong phiên Windows của người dùng đã ghép. Biểu tượng thông báo và cửa sổ tiếng Việt xuất hiện trước khi bắt đầu. Chọn “Bắt đầu phiên mô phỏng trên hồ sơ thử nghiệm”. Phụ huynh xem phút dùng máy của ứng dụng `openguard-demo.exe`. Trẻ có thể thử một trang mẫu hoặc mở báo cáo bằng tài khoản child-view để xin thêm giờ, phản hồi chặn nhầm và xem lịch sử. Bỏ chọn hoặc thu nhỏ cửa sổ để tạm dừng. Đóng cửa sổ để thoát.

### Cửa sổ giờ nghỉ trong máy ảo

Với tài khoản Standard User lab đã ghép, chạy `.\.venv\Scripts\python -m agent.tray --store .local/agent.sqlite3 --lab-input-sid $childSid --lab-rest`. Bật phiên và chọn **Hiện cửa sổ nhắc nghỉ khi đến giờ trên tài khoản lab này**. Khi hết quota, tới ô lịch nghỉ hoặc phụ huynh gửi lệnh nghỉ, cửa sổ cho biết nguyên nhân, người đặt quy tắc, giờ hiện tại và thời gian còn lại. Nếu bị giới hạn bởi lịch, trẻ thấy cả giờ bắt đầu ô được dùng tiếp theo. Trường hợp nhiều quy tắc cùng áp dụng sẽ có lời giải thích cho từng quy tắc.

Trẻ có thể bấm **Xin thêm giờ trong Góc của em** để mở mục yêu cầu trên dashboard, hoặc đọc số 111 ngay cả khi mất mạng. Nút mở trang Tổng đài chỉ mở trình duyệt khi trẻ chọn. Esc, nút đóng và dấu X cho phép bỏ lời nhắc trong suốt đợt nghỉ hiện tại. Máy tính và các ứng dụng khác vẫn sử dụng được. Khi phụ huynh mở lại, cộng đủ thời gian, đổi lịch, khay mất hiển thị, đổi tài khoản hoặc quy tắc hết hạn, cửa sổ tự thu lại theo trạng thái hợp lệ. Nếu bộ lấy mẫu ngừng cập nhật quá 2,5 giây, UI cũng thu lại cửa sổ.

Các ca trong `tests/test_agent_clock.py` và `tests/test_agent_session.py` kiểm ba nguyên nhân nghỉ đồng thời, lịch qua ranh giới Chủ nhật/thứ Hai, mở và đóng theo lệnh, chuyển phiên, mất khay, hết hạn, bỏ nhắc và thao tác trợ giúp. Test dùng đồng hồ và widget giả. Nghiệm thu hiển thị thực tế, điều hướng bàn phím và việc sử dụng ứng dụng liên lạc song song cần chạy trên VM.

Tray dùng Shell_NotifyIcon; mỗi 400 ms cửa sổ kiểm tra hình chữ nhật biểu tượng do Windows cung cấp. Quyền ghi nhận hết hiệu lực sau 1,5 giây nếu giao diện đứng. Khi mất biểu tượng trong phiên, ứng dụng dừng mô phỏng. Ở một số cấu hình Windows, biểu tượng nằm trong vùng biểu tượng tràn. Hãy ghim biểu tượng khi thử NT1 trên VM. Cần kiểm thử trực quan cả trường hợp Explorer khởi động lại và tự ẩn taskbar. [Microsoft Learn về vùng thông báo](https://learn.microsoft.com/en-us/windows/win32/shell/notification-area)

Mỗi lượt mô phỏng tương ứng một phiên tương tác giả trên thiết bị đã ghép. Khi có mạng, tiến trình lấy thời gian và phiên bản quy tắc ở heartbeat mỗi 60 giây, nghe lệnh qua WebSocket giữa các heartbeat và chuyển lệnh vào bộ đếm. Trong giao dịch SQLite, mỗi mã lệnh được gắn với hiệu ứng khóa mô phỏng, mở khóa hoặc cộng 15 phút. SQLite ghi tổng thời gian khoảng 5 giây/lần. Báo cáo gửi các đoạn từ 30 giây và chốt phần lẻ khi đóng bình thường trong lúc khay khả dụng. Dừng đột ngột có thể mất đoạn cuối chưa ghi hoặc chưa xếp hàng.

## Đồng hồ và quyết định ứng dụng

Lịch dùng giờ Việt Nam UTC+7, bắt đầu tuần từ thứ Hai, mỗi ô 30 phút. Chương trình cộng khoảng chênh của đồng hồ đơn điệu. Mẫu ở trạng thái khóa, trang khay tạm dừng, sai phiên hoặc idle quá 300 giây dừng tính thời gian. Khoảng trống vượt 5 giây thiết lập mốc đo mới để tránh cộng thời gian ngủ. Mười, năm và một phút là các mốc cảnh báo. Phút cuối nằm trong quota để ca 3 phút có 60 giây lưu bài và đưa ra quyết định nghỉ ở giây 180. Lịch cấm hoặc lệnh khóa có hiệu lực ở lần đánh giá tiếp theo. Lệnh mở khóa chỉ gỡ cờ khóa thủ công. Quota, lịch và các ngoại lệ vẫn áp dụng.

Mốc logic dùng heartbeat và tiến triển đơn điệu trong cùng phiên, được lưu cùng tổng dùng theo ngày. Chênh đồng hồ máy quá 90 giây ghi bộ đếm bất thường tại chỗ. Sau khởi động lại, nếu đồng hồ máy lùi quá ngưỡng so với mốc lưu, tray chờ một heartbeat tin cậy trước khi tiếp tục. Hết 7 ngày kể từ lúc server cấp quy tắc, mô phỏng tạm dừng. Khi thiết bị tắt hoàn toàn và người dùng đổi cả đồng hồ, hai đầu không thể xác định chính xác thời gian đã trôi cho đến khi có mạng.

Trong VM riêng, có thể chọn dùng tín hiệu idle của tài khoản lab thay cho tín hiệu tương tác giả. Chạy `.\.venv\Scripts\python -m agent.tray --store .local/agent.sqlite3 --lab-input-sid $childSid` bằng đúng Standard User có SID đó, rồi bật mô phỏng trong cửa sổ. Chế độ này đối chiếu SID và token nhóm, bỏ qua phiên ngoài console vật lý, desktop khóa và desktop bảo mật. GetLastInputInfo trả số mili giây từ lần tương tác gần nhất của phiên. Chỉ tổng thời gian của phiên thử đi vào báo cáo. Ca giả lập kiểm tra trường hợp timer DWORD quay vòng, khay ẩn, phiên bị khóa và idle, còn tính chính xác trên Windows thật cần đo ở VM. [Microsoft Learn về GetLastInputInfo](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo)

Quy tắc ứng dụng của dashboard có tên tệp và SHA-256. Khi có hash, bộ quyết định so sánh nội dung executable; tên đổi vẫn khớp, tệp khác mượn cùng tên chưa đủ điều kiện được phép. Trước khi xét luật, bộ quyết định kiểm tra SID chủ tiến trình, trạng thái bảo vệ và thành phần thiết yếu. Các bộ kiểm thử dùng tệp văn bản mang đuôi `.exe` để xác nhận so sánh hash sau khi đổi tên.

VM có phép thử đóng ứng dụng theo phạm vi hẹp. Trong dashboard, thêm luật chặn `notepad.exe` với kết quả `(Get-FileHash "$env:SystemRoot\System32\notepad.exe" -Algorithm SHA256).Hash.ToLowerInvariant()`. Chạy tray với `--lab-input-sid $childSid --lab-app`, bật phiên, chờ đồng bộ rồi bấm “Mở Notepad trống và thử quy tắc đóng ứng dụng”. Chương trình khởi chạy đúng tệp Notepad do Windows cung cấp, giữ handle tiến trình và handle tệp, kiểm tra SID, session, đường dẫn, hash và luật đã ký. Hộp thoại giải thích lý do và hỏi xác nhận. Khi đồng ý, chương trình kiểm tra lại rồi gửi WM_CLOSE tới đúng một cửa sổ Notepad thuộc PID vừa tạo. Hủy xác nhận, đổi luật, mất khay hoặc đổi người dùng sẽ bỏ qua thao tác. Sự kiện blocked_app chỉ ghi sau khi xác nhận chính tiến trình ấy đã thoát. Khi Notepad hỏi lưu hoặc chuyển công việc sang một tiến trình Windows Store khác, chương trình không đóng cưỡng bức hoặc đuổi theo tiến trình thay thế. Phép thử này chưa bao phủ chặn mọi ứng dụng trên máy.

## DNS và các ngoại lệ

`agent/dns_proxy.py` nhận câu hỏi IN qua UDP/TCP trên 127.0.0.1. Mỗi yêu cầu xét luật trước khi dùng cache DNS, giới hạn 256 mục trong RAM, TTL tối đa 60 giây. Upstream thật chỉ được sử dụng khi caller truyền địa chỉ resolver. Có thời hạn chờ và chuyển TCP khi UDP bị cắt ngắn. Phép thử và demo dùng upstream cục bộ giả. Địa chỉ trả về 192.0.2.1 thuộc dải dành cho tài liệu. Không cấu hình địa chỉ đó làm dịch vụ thật.

An toàn có ưu tiên cao nhất. Danh sách ngoại lệ nằm trong `agent/data/safety.csv`, gồm kênh 111, cổng HCMUS, Bộ Giáo dục và trang thông tin cứu hộ. Nguồn đối chiếu nằm ngay từng dòng. Bản ghi SafeSearch trỏ hai tên tìm kiếm `www.google.com` và `www.google.com.vn` sang `forcesafesearch.google.com`. Khi cần địa chỉ A/AAAA, proxy tìm địa chỉ của đích bằng resolver đã chọn. DNS mã hóa hoặc trình duyệt dùng resolver khác có thể bỏ qua. [Hướng dẫn SafeSearch của Google](https://support.google.com/websearch/answer/186669)

`agent/data/domains.csv` cung cấp 240 mục thuộc sáu nhóm trên miền example.org được dành cho tài liệu. Cột trạng thái đánh dấu toàn bộ dữ liệu phân loại là synthetic. Trong `tests/fixtures/dns_cases.csv` có 50 quyết định chặn và 20 miền giáo dục cần cho phép. Đây là số liệu kiểm tra logic trên tập mô phỏng; độ bao phủ nội dung web thật còn cần một tập danh mục đã duyệt.

Để thử proxy liên tục, dùng VM riêng có duy nhất tài khoản thử đang hoạt động, hồ sơ đã ghép và resolver do nhóm quản lý trong mạng RFC1918. Chạy `.\.venv\Scripts\python -m agent.tray --store .local/agent.sqlite3 --lab-input-sid $childSid --lab-dns-upstream $resolverIp`. Proxy lắng nghe UDP/TCP trên 127.0.0.1:53; có thể dùng `--lab-dns-port 1053` và `nslookup -port=1053 north-forum.example.org 127.0.0.1` để thử trước mà không cấu hình adapter. Khi bật phiên trong UI, proxy dùng quy tắc đã ký và gửi lý do chặn đến khay. Nếu mất quyền hiển thị hoặc không giao được lời giải thích, proxy chuyển tiếp truy vấn. Chỉ miền đăng ký vào hàng đợi; các miền an toàn được miễn ghi nhận. Dùng resolver lab đã có quyền xử lý DNS thử nghiệm. DNS loopback nhận gói từ các tiến trình trên VM và không xác định được SID của từng gói. Chỉ thử với tài khoản, truy vấn và hệ thống dành riêng cho lab. Khôi phục adapter trước khi đóng tray nếu đã chọn cấu hình adapter, vì đóng ứng dụng sẽ giải phóng cổng 53.

## Service và named pipe trong máy ảo

`agent/lab_service.py` là một Windows Service lab độc lập. Service chỉ cung cấp trạng thái IPC cho một SID thử nghiệm qua `\\.\pipe\OpenGuardKidsLab-v1`. DACL cho SYSTEM, LocalService và SID được chỉ định, từ chối yêu cầu từ máy khác. Server đối chiếu SID gắn với token của người mở pipe, nhận duy nhất thông điệp STATUS. Client ràng buộc PID server với service đã đăng ký qua SCM và dùng mức định danh cho token. Service xử lý mỗi yêu cầu trong thời gian giới hạn và tránh ghi SID vào nhật ký.

Chỉ dùng các lệnh sau trên Windows VM dùng riêng có snapshot. Đặt thư mục chạy, toàn bộ môi trường Python và thư viện ở vị trí được bảo vệ để tài khoản trẻ chỉ có quyền đọc/chạy. Ví dụ dưới đây giả định quản trị viên đã đặt mã tại `C:\Program Files\OpenGuardKidsLab` và chuẩn bị venv tại đó. Trước khi cài, kiểm tra cả quyền ghi của nhóm Users và Authenticated Users trên các thư mục cha, tệp Python và venv. `sc.exe` và SCM cần quyền Administrator để tạo, chạy, dừng và gỡ service. Chạy `whoami /user` trong tài khoản Standard User lab để lấy SID riêng của tài khoản đó.

```powershell
$labRoot = 'C:\Program Files\OpenGuardKidsLab'
$labImage = '"{0}\.venv\Scripts\python.exe" -I "{0}\agent\lab_service.py" --scm' -f $labRoot
sc.exe create OpenGuardKidsLab binPath= $labImage start= demand obj= 'NT AUTHORITY\LocalService' DisplayName= 'OpenGuard Kids Lab IPC'
$childSid = Read-Host 'SID Standard User trong VM'
sc.exe start OpenGuardKidsLab $childSid
# Chạy dòng tiếp theo từ tài khoản Standard User có SID đã chọn
& "$labRoot\.venv\Scripts\python.exe" -I "$labRoot\agent\lab_service.py" --status
# Trong PowerShell Administrator của VM sau khi thử xong
sc.exe stop OpenGuardKidsLab
sc.exe delete OpenGuardKidsLab
```

Service chạy thủ công dưới LocalService vì chức năng IPC không cần LocalSystem. Không có bước tự cài hoặc tự khởi động trong setup. Khởi chạy `python -m agent.tray --lab-ipc --store .local/agent.sqlite3` để hiện nút kiểm tra IPC từ cửa sổ. Tray phát triển thực hiện đồng bộ trong tiến trình người dùng. Chuyển cưỡng chế thật vào service theo kiến trúc đề gốc còn cần triển khai và nghiệm thu.

## Kiểm thử và giới hạn

Chạy `.\scripts\run-tests.ps1 -q` để kiểm tra quy tắc, cache ký, tray lease, đồng hồ, lệnh, DNS và dữ liệu bền. Chạy `.\.venv\Scripts\python.exe -m scripts.check_dns` để lưu transcript nslookup và kết quả từng miền vào `.local/dns-check`. Test cần loopback UDP/TCP trên máy thử và bộ test native pipe cần Windows.

Trong VM phải thử ca hiển thị khay/Explorer, hai tài khoản cho IPC, cài và gỡ service, độ trễ khi mất mạng, DNS thật ở cổng 53 và SafeSearch qua trình duyệt. Với F1/F2, dùng tài khoản giả, đồng hồ bấm tay và Notepad trống để đánh giá độ chính xác, nhận biết khóa/idle Windows, chặn tiến trình trong đúng tài khoản và lối liên hệ khẩn cấp. Chưa có cơ chế cưỡng chế khóa máy hoặc bộ thực thi giám sát mọi tiến trình Windows. Hướng dẫn lab adapter nằm trong `scripts/lab/README.md`.
