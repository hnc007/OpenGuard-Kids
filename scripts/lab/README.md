# Thử DNS trên máy ảo Windows

Chạy trên máy ảo Windows 10/11 có snapshot và quyền truy cập console. Dùng hồ sơ giả, khởi động DNS proxy UDP và TCP trên `127.0.0.1:53` rồi kiểm tra truy vấn trực tiếp tới proxy. Chọn adapter qua `Get-NetAdapter`.

```powershell
# Thay 12 bằng InterfaceIndex thực tế trong VM
.\scripts\lab\dns-adapter.ps1 -InterfaceIndex 12
.\scripts\lab\dns-adapter.ps1 -InterfaceIndex 12 -Action Apply
.\scripts\lab\dns-adapter.ps1 -InterfaceIndex 12 -Action Restore
```

Preview đọc cấu hình. Apply và Restore cần Administrator và cụm xác nhận chứa số adapter. Script ghi bản khôi phục theo GUID vào `.local/lab-dns/` trước khi đổi IPv4. Thư mục này được Git bỏ qua. Khôi phục một adapter dùng DNS tĩnh sẽ đặt lại danh sách theo đúng thứ tự. Với DNS tự động, Windows nhận lại cấu hình qua DHCP. Danh sách do DHCP cấp có thể thay đổi giữa hai lần kiểm tra. Cả hai thao tác giữ cấu hình IPv6 hiện hành, kể cả chế độ cấp tự động và địa chỉ tĩnh. DNS qua IPv6 có thể vẫn phân giải ngoài proxy IPv4.

Chạy các ca IPv4 tĩnh một và nhiều địa chỉ, DHCP, hủy xác nhận và phục hồi sau khi đóng PowerShell trong máy ảo. So sánh nguồn DNS, thứ tự máy chủ tĩnh, tùy chọn đăng ký và cấu hình IPv6 trước/sau. Windows có thể bỏ các mặc định khi xuất cấu hình hoặc dùng cú pháp dump khác. Khi script không nhận diện chắc chắn nguồn DNS, nó dừng trước bước ghi. Nếu khôi phục gặp lỗi, giữ bản JSON để thử lại trên đúng adapter và có thể hoàn nguyên snapshot VM.

## Kiểm thử phục hồi bằng adapter giả

Từ gốc repo, chạy `.\.venv\Scripts\python -m pytest -q tests/test_lab_dns_adapter.py`. Mỗi thao tác mở một tiến trình PowerShell con, thử lại từ tệp JSON ở một repo tạm riêng trong `.local`. Pytest chỉ sao chép script điều phối, nạp mô hình adapter/IPv4 giả vào vị trí helper và để trống PATH trong tiến trình đó. Các ca đi qua chính logic xác nhận, ghi file mới độc quyền, kiểm chứng cấu hình, hoàn nguyên, giữ hoặc lưu trữ bản sao. Tệp lưu trữ mang hậu tố `.restored.json`; GUID adapter và tên máy nằm bên trong JSON.

Các phép thử gồm cấp DNS qua DHCP với danh sách mới sau khi xin lại lease, khôi phục danh sách tĩnh theo thứ tự, giữ cấu hình IPv6, kiểm quyền và cụm xác nhận, thiếu listener UDP/TCP, dump mơ hồ, backup sai máy/adapter hoặc chứa địa chỉ sai họ. Hai ca tạo lỗi sau khi đã thay đổi một phần, xác nhận backup còn nguyên rồi thử Restore lại từ một PowerShell mới. Script chạy thật dùng helper cố định `dns-adapter-windows.ps1` để gọi Windows. Chỉ kết quả trên mô hình đã được xác minh tự động. Việc thay đổi và khôi phục adapter thật cần được nghiệm thu riêng bằng snapshot VM. [Microsoft Learn về cấu hình DNS IPv4 và IPv6 bằng netsh](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/netsh-interface)
