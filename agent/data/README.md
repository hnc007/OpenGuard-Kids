# Danh mục tên miền và bộ nghiệm thu

`domains.csv` phân loại tên miền theo sáu nhóm. 240 mục dưới `example.org` có trạng thái `synthetic`, dùng để tạo tình huống ổn định trong phòng thực hành. 23 mục `publisher_identified` nối với nguồn của đơn vị công bố, gồm 20 địa chỉ giáo dục và ba địa chỉ mạng xã hội/trò chơi. Trạng thái này xác nhận loại dịch vụ do nhà cung cấp mô tả, không chứng nhận mọi trang con phù hợp mọi lứa tuổi. Nhóm chưa xây dựng một danh sách kiểm duyệt toàn bộ Internet.

`safety.csv` chứa ngoại lệ ưu tiên cao hơn quy tắc của phụ huynh, gồm kênh 111 và cổng giáo dục. Mỗi dòng ghi tên miền, mục đích và nguồn chính thức. Khi thêm một cổng trường địa phương, kiểm tra nguồn, phạm vi tên con và quyền biên tập trước khi đóng gói bản agent mới. Danh sách website giáo dục thông thường và danh sách trợ giúp được miễn chặn có hai mục đích khác nhau.

`tests/fixtures/dns_cases.csv` có 50 miền cần chặn theo cấu hình thử và 20 miền giáo dục mô phỏng cần cho phép. `scripts/check_dns.py` còn thử 20 miền giáo dục công khai từ danh mục qua cùng upstream giả, không tải trang. Các phản hồi RCODE thực tế nằm ở `docs/evidence/dns-acceptance.csv`. Bản kiểm tra SafeSearch bằng nslookup dùng cổng 53 trên loopback. Bản DNS client chạy với cổng tạm là phương án mặc định.

Nguồn cho nhóm giáo dục gồm cổng tuyển sinh Đại học Quốc gia TP.HCM và Hà Nội, cổng của Bộ GDĐT, NASA Space Place, Scratch và Khan Academy. Từng hostname có nguồn riêng ở cột `source`. [ĐHQG-HCM](https://tuyensinh.vnuhcm.edu.vn/index.php?route=catalog%2Ftintuc%2Fnoidung&tintuc_id=92) · [ĐHQG-HN](https://tuyensinh.vnu.edu.vn/index.php/Home/camnang/125) · [NASA Space Place](https://spaceplace.nasa.gov/) · [Scratch](https://scratch.mit.edu/about) · [Khan Academy](https://www.khanacademy.org/about)
