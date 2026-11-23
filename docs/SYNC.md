# Đồng bộ thiết bị và xác minh kênh truyền

Agent phát triển đồng bộ theo danh tính của một lần ghép máy. Dữ liệu gửi đi mang mã thiết bị và mã hồ sơ từ lần ghép này. Máy chủ kiểm tra cả hai trước khi ghi và áp phạm vi sở hữu riêng cho các endpoint của trình duyệt.

## Ghép máy và thu hồi

1. Phụ huynh đăng nhập, chọn hồ sơ, xác nhận phạm vi sử dụng, việc đã giải thích và sự đồng ý của trẻ. Mỗi lần tạo mã sẽ thay mã cũ của hồ sơ.
2. Trong cùng phiên Windows dùng cho agent, chạy lệnh dưới đây. Điền mã tại dấu nhắc ẩn. Mã gồm 8 ký tự, hết hạn sau 10 phút. Một nguồn được thử tối đa 10 lần trong cửa sổ 10 phút. Máy chủ giữ HMAC của mã và địa chỉ nguồn trong các bảng phục vụ ghép đôi.
3. Máy chủ tiêu thụ mã một lần trong giao dịch, cấp UUID thiết bị, access 15 phút, refresh 30 ngày, khóa công khai Ed25519 và khóa xóa riêng. UUID cài đặt ngẫu nhiên dùng để tránh liên kết với serial máy. Agent giữ danh tính dưới DPAPI của tài khoản Windows hiện tại.
4. Trang thiết bị có thao tác thu hồi. Khi agent liên hệ lại, máy chủ từ chối xác thực thông thường. Agent vẫn dùng được khóa xóa để xác nhận đã xóa bộ đệm. Nếu mất phản hồi khi xoay refresh, agent cũ dọn dữ liệu rồi cần ghép lại để tiếp tục hoạt động.

Máy chủ kiểm tra token của thao tác ghi sau khi đã lấy khóa ghi SQLite. Nếu giao dịch thu hồi hoàn tất trước, một yêu cầu đã đến nhưng còn chờ sẽ nhận 401. Áp dụng cho sự kiện, heartbeat, xác nhận lệnh và xoay refresh. Kiểm thử đồng thời đối chiếu cả dữ liệu hoạt động và giá trị băm của token trước/sau.

```powershell
.\.venv\Scripts\python.exe -m agent.client enroll --server http://127.0.0.1:8000 --store var/agent.sqlite3
.\.venv\Scripts\python.exe -m agent.client sync --server http://127.0.0.1:8000 --store var/agent.sqlite3
```

Lệnh `sync` tải chính sách, xử lý lệnh đang chờ trên bộ đếm mô phỏng và in số lệnh đã gửi ACK thành công. Heartbeat của CLI vẫn báo tạm dừng để dashboard biết máy không có phiên tương tác. Đóng khay trước khi chạy `sync` trên cùng kho. Để nhận lệnh liên tục qua WebSocket và qua heartbeat dự phòng, mở khay và bắt đầu phiên mô phỏng có giao diện hiển thị. Dùng cùng `--store` và cùng địa chỉ máy chủ trong suốt lần ghép.

## Chính sách và lệnh

Mỗi lần sửa chính sách đối chiếu phiên bản trên dashboard. Ghi thành công tăng phiên bản lên 1 và ghi nhật ký trong cùng giao dịch. Agent gửi phiên bản đang có trong heartbeat. Nếu phiên bản lệch, agent tải chính sách, xác minh chữ ký Ed25519, mã thiết bị và thời hạn rồi ghi cache. Mỗi bản ký có hiệu lực 7 ngày. Khi sắp hết hạn, agent làm mới cả khi version chưa đổi. Khi cache mất hiệu lực hoặc không xác minh được, cần dừng kiểm soát đến khi có bản hợp lệ. Quyền đọc khóa công khai và khả năng sửa cache trong phiên phát triển còn phụ thuộc vào bảo vệ tài khoản Windows.

Lệnh khẩn đi qua WSS có header Bearer. Máy chủ kiểm tra token và truy vấn hàng đợi mỗi giây khi kết nối. Lệnh chưa ACK được gửi lại sau 5 giây và còn có trong heartbeat. Mỗi lệnh có UUID, hành động cố định và hạn dùng. Bộ đếm lưu UUID và hiệu lực lệnh trong cùng giao dịch cục bộ. ACK đã gửi lại vẫn hợp lệ. Độ trễ HTTP, chờ heartbeat, mất mạng và Session 0 là các yếu tố riêng trong nghiệm thu thực địa.

## Mất mạng và xóa dữ liệu

- Hàng đợi SQLite dùng UUID sự kiện và chứa bản tin đã tối thiểu hóa, mã hóa qua DPAPI. Mỗi nhịp gửi tối đa 100 mục, tối đa 10.000 mục trong bộ đệm. Khi đầy, bỏ mục cũ nhất. Sự kiện quá 90 ngày được dọn lúc ghi hoặc đồng bộ.
- Ngay trước khi gửi, agent kiểm tra danh tính của nhịp đồng bộ, rồi đối chiếu bản tin với UUID, thời điểm, thiết bị và hồ sơ. Sự kiện vượt quá dung sai 120 giây ở tương lai sẽ chờ. Bản ghi hỏng hoặc sai phạm vi được đánh dấu cách ly ngay trên máy. Bản ghi hợp lệ trong lô vẫn được gửi. Nhật ký chỉ chứa số mục bị cách ly.
- Phản hồi thất lạc sau khi server commit dẫn đến gửi lại lô ở nhịp sau. Ràng buộc duy nhất theo thiết bị và UUID ngăn đếm hai lần. Một mục sai phạm vi làm hủy cả giao dịch.
- Trước khi upload, agent hỏi về yêu cầu xóa bằng khóa xóa. Khi có lệnh, agent xóa cache, hàng đợi, bộ đếm và danh tính, chỉ giữ một biên nhận DPAPI để gửi ACK. Nếu mạng mất ngay trước ACK, agent gửi lại khi khởi động. Thiết bị đã xác nhận xóa cục bộ trong lúc bị thu hồi được tính là đã xong khi phụ huynh xóa hồ sơ sau đó. Biên nhận đang chờ có thể kéo dài nếu máy mất vĩnh viễn.
- Nếu lần gửi ACK bị gián đoạn, khay mở màn hình chỉ gửi biên nhận, thử lại sau mỗi phút khi chưa liên lạc được. CLI thử lại ở chế độ `sync` hoặc trước khi `enroll`. Các luồng này chỉ tiếp tục ghép máy sau khi xử lý xong biên nhận của lần cài đặt cũ. Trường hợp server đã dọn biên nhận sau 7 ngày, agent thông báo đã xóa trên máy và trạng thái từ xa chưa xác minh được. Màn hình mở lại ô nhập mã mới, tránh giữ một máy đã dọn sạch trong trạng thái chờ vô thời hạn.
- Xóa hồ sơ làm mất quyền upload ngay vì thiết bị và tài khoản trẻ thuộc hồ sơ đã bị xóa trong giao dịch. Đây là xóa logic trong kho đang vận hành. Bản sao lưu ngoài máy cần quy trình riêng.

## TLS và thay chứng chỉ lab

Máy khách kiểm tra chuỗi tin cậy, tên máy, TLS 1.2 trở lên và SHA-256 của chứng chỉ lá DER trước khi gửi bí mật. HTTP phát triển chỉ kết nối tới địa chỉ số `127.0.0.1` hoặc `::1`. Script tạo chứng chỉ lab trong thư mục riêng. Chứng chỉ mặc định chỉ chứa tên localhost và địa chỉ loopback.

```powershell
.\.venv\Scripts\python.exe -m scripts.lab_certificate
.\scripts\run-server.ps1 -TLS -Port 8443
# Thay PIN_HEX bằng SHA-256 được in ở máy chủ, đối chiếu qua kênh tin cậy.
# Trong terminal thứ hai, lấy mã ghép mới. Dùng lại kho TLS này khi sync hoặc mở tray.
.\.venv\Scripts\python.exe -m agent.client enroll --server https://127.0.0.1:8443 --ca .local/tls/ca.pem --pin PIN_HEX --store .local/agent-tls.sqlite3
```

Phát hành chứng chỉ mới, lấy dấu vân tay qua kênh đã xác minh và cấp cho agent danh sách `--pin PIN_CU,PIN_MOI` trước khi chuyển máy chủ sang chứng chỉ mới. Khi CA đổi, cung cấp tệp PEM chứa cả hai CA trong giai đoạn chuyển. Khởi động lại kết nối, xác minh HTTPS và WSS thành công, rồi gỡ chứng chỉ và pin cũ khỏi cấu hình agent. Chuyển origin HTTP sang HTTPS cần một lần ghép mới với kho riêng vì mỗi danh tính ràng buộc với origin lúc cấp. Agent trả lỗi ngay khi pin sai. TLS máy chủ chỉ chấp nhận kết nối LAN khi triển khai riêng đã được cho phép. Bảo vệ khóa TLS và `OGK_SECRET_KEY` trong cấu hình dịch vụ của người quản trị. Thay khóa chính của máy chủ cần kế hoạch mã hóa lại dữ liệu và cấp lại khóa xác minh chính sách.

## Đo kênh đồng bộ

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_sync --samples 30 --out docs/evidence
```

Script tạo SQLite, CA và máy chủ Uvicorn riêng, chọn cổng loopback ngẫu nhiên, dùng hồ sơ giả và dọn thư mục tạm khi hoàn tất. Script thử sai pin ở HTTPS/WSS và sai CA trước khi đo. CSV ghi từng mẫu. Đồng hồ hiệu năng bắt đầu ngay trước thao tác PUT chính sách của dashboard, kết thúc khi agent đọc lại đúng phiên bản từ cache sau nhịp chủ động. Với lệnh, đồng hồ bắt đầu trước POST, ghi hai mốc khi nhận WSS và khi server ACK. Không có hành động lên Windows trong phép đo này. Chu kỳ chờ heartbeat tự nhiên và độ trễ mạng LAN cần đo riêng.

## Phục hồi sau gián đoạn

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_recovery --samples 30 --out docs/evidence
```

Mỗi kịch bản sử dụng hồ sơ giả và máy chủ TLS trên cổng loopback tạm. Mỗi mẫu phải giữ được bản ghi trong hàng đợi lúc gián đoạn, sau đó ghi chính xác một sự kiện trên server và dọn hàng đợi sau ACK. CSV lưu số mili giây đo bằng đồng hồ đơn điệu. Đồng hồ bắt đầu từ lúc chủ động phục hồi. Khoảng thời gian máy chủ ngừng và thời gian chờ lần heartbeat tiếp theo nằm ngoài phép đo.

|Gián đoạn|Cách tạo lỗi|Khoảng đo|
|---|---|---|
|Mất phản hồi mạng|Lớp transport bỏ phản hồi sau khi server đã commit lô sự kiện thật. Agent còn giữ lô.|Từ lúc cho gửi lại đến khi đồng bộ thành công và xóa lô cục bộ.|
|Máy chủ dừng|Dừng Uvicorn của phép đo, ghi sự kiện giả vào bộ đệm và kiểm tra kết nối thất bại.|Từ lúc tạo lại app trên cùng DB, cổng và chứng chỉ đến khi agent nhận ACK.|
|Agent dừng|Một tiến trình Python thử ghi SQLite bằng DPAPI rồi tự thoát ngay sau commit. Tiến trình mới dùng cùng kho và tài khoản Windows.|Từ lúc khởi tạo tiến trình thứ hai đến khi nó xác minh phiên bản chính sách, đồng bộ lô, kiểm tra hàng đợi rỗng và thoát.|

Quy trình này đo khả năng phục hồi dữ liệu của ứng dụng. Việc Windows SCM tự khởi động lại dịch vụ, hỏng ổ đĩa và mất mạng LAN kéo dài cần kiểm thử trên máy lab riêng.

Trên Windows build 26200 với Python 3.11.9, cả 90 lần thử đều khôi phục đúng một sự kiện. Bảng dùng phân vị hạng gần nhất từ 30 mẫu cho mỗi dòng.

|Tình huống|p50 (ms)|p95 (ms)|
|---|---:|---:|
|Gửi lại sau mất phản hồi|61,923|91,467|
|Khởi động lại máy chủ và đồng bộ|851,392|1.018,039|
|Khởi động lại tiến trình agent và đồng bộ|1.597,852|1.697,183|

Các số của phép đo kênh trước đó trên cùng môi trường là 112,817 ms ở p95 từ PUT đến cache tại nhịp chủ động, 944,465 ms từ POST đến khi nhận lệnh WSS, và 960,883 ms từ POST đến ACK. [Số đo từng mẫu đồng bộ](evidence/sync-samples.csv) · [Số đo từng mẫu phục hồi](evidence/recovery-samples.csv)

## Nhận policy và lệnh trong tray

Luồng tray hiện có thông báo version qua WSS. Bộ đếm tiếp tục chạy trong khi HTTPS chờ mạng, dùng chính sách đã xác minh còn hạn. Trước khi ghi lệnh, agent kiểm lại hạn và trạng thái phiên sau khi lấy được quyền ghi SQLite. “Hết hạn chờ xác nhận từ máy” có nghĩa là server chưa nhận ACK đúng hạn. Đối chiếu số dư trên máy nếu kết nối bị ngắt giữa lúc ghi bộ đếm và gửi ACK. [30 chu kỳ policy/lệnh với bộ đếm mô phỏng](evidence/full-scope-2026-11-23/tray-worker/live-sync-summary.json)

## Diễn tập xoay chứng chỉ máy chủ trên cùng CA (S3)

Trước khi thay chứng chỉ, phân phối hai mã SHA-256 của chứng chỉ máy chủ theo dạng `--pin <ghim_cũ>,<ghim_mới>` và giữ nguyên CA. Sau khi máy chủ đã dùng chứng chỉ mới, kiểm tra agent đọc được chính sách qua HTTPS và kết nối lại được kênh lệnh WSS. Khi các agent đã chuyển xong, bỏ ghim cũ. Từ thời điểm đó, nếu máy chủ vô tình phục hồi chứng chỉ cũ, cần đưa chứng chỉ mới trở lại để kết nối được với các agent chỉ giữ ghim mới.

| Chứng chỉ máy chủ | Agent chỉ giữ ghim cũ | Agent giữ cả hai | Agent chỉ giữ ghim mới |
|---|---|---|---|
| Cũ, trước khi đổi | Kết nối được | Kết nối được | Từ chối ghim |
| Mới, sau khi đổi | Từ chối ghim | Kết nối được | Kết nối được |

Ngày 23/11/2026, hai ca tích hợp HTTPS và WSS đều đạt trên `127.0.0.1`. Mỗi ca cấp hai chứng chỉ khác khóa từ cùng một CA tạm, ghép đôi thiết bị giả một lần, rồi lần lượt khởi động máy chủ với chứng chỉ cũ, mới, cũ và mới trên cùng cổng. Trong mỗi lần kết nối thành công, agent dùng lại cùng token để đọc chính sách của thiết bị hoặc nhận thông báo phiên bản đầu tiên qua WSS. Cả hai kênh đều từ chối chứng chỉ mới khi thiếu CA tin cậy dù ghim mới đúng. Sau khi bỏ ghim cũ, cả hai cũng từ chối lần phục hồi chứng chỉ cũ và kết nối lại khi máy chủ nạp lại chứng chỉ mới.

Chạy lại phép thử sau từ thư mục dự án. Chứng chỉ, khóa máy chủ và SQLite của mỗi ca nằm trong thư mục tạm được dọn khi kết thúc. Phạm vi đã kiểm là các kết nối mới sau mỗi lần máy chủ khởi động lại, với cùng CA và cùng địa chỉ. [Mã kiểm thử](../tests/test_tls_rotation.py)

```powershell
.\scripts\run-tests.ps1 -q tests/test_tls_rotation.py
```
