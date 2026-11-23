# Danh mục tên miền và bộ nghiệm thu

`domains.csv` phân loại 440 tên miền theo sáu nhóm. Trong 200 địa chỉ công khai có 197 địa chỉ giáo dục, hai mạng xã hội và một trò chơi. 23 địa chỉ mang trạng thái `publisher_identified` với nguồn của đơn vị công bố, 177 địa chỉ mang trạng thái `official_directory` với nguồn là danh bạ trường tiểu học. 240 mục `synthetic` dưới `example.org` dùng làm dữ liệu thử. Danh mục ghi loại dịch vụ ở cấp hostname. Phụ huynh vẫn có thể giới hạn một website trường cụ thể.

## Nguồn dữ liệu trường tiểu học Singapore

Bản phân phối này sử dụng dữ liệu của Ministry of Education, Singapore từ *General information of schools* (2026), cập nhật ngày 17/04/2026. Nhóm trích dữ liệu ngày 23/11/2026 theo Singapore Open Data Licence v1.0. [Bộ dữ liệu trên data.gov.sg](https://data.gov.sg/datasets/d_688b934f82c1059ed0a6993d2a829089/view) · [Giấy phép hiện hành](https://data.gov.sg/open-data-licence)

Để tái lập bản trích, đọc `mainlevel_code` và `url_address` của toàn bộ 337 dòng. Chọn 179 dòng có cấp học đúng bằng `PRIMARY`, tách hostname của URL, viết thường và giữ nguyên tiền tố `www.` nếu có. Chỉ nhận tên có dạng `<trường>.moe.edu.sg` hoặc `www.<trường>.moe.edu.sg`. Đối chiếu với các cấp học còn lại sau khi bỏ `www.` **chỉ để so trùng**. Hai trường dùng chung địa chỉ với cấp khác là `www.mgs.moe.edu.sg` và `www.scgs.moe.edu.sg`. Sau khi loại hai địa chỉ này, kết quả có 177 hostname riêng biệt. Các cột được lưu gồm hostname, nhóm, đường dẫn nguồn và trạng thái. Phép trích không gửi truy vấn đến website trường. [Truy vấn danh bạ](https://data.gov.sg/api/action/datastore_search?resource_id=d_688b934f82c1059ed0a6993d2a829089&limit=500&fields=url_address%2Cmainlevel_code)

Bản trích có SHA-256 `2aa3b8755536afd7a22460e20f5bf6184b86d0b8ba539c55273f807cbb884bd8` khi sắp xếp các hostname theo ASCII, mã hóa UTF-8 và kết thúc từng dòng bằng LF. Có thể chạy `.\scripts\run-tests.ps1 -q tests/test_domain_catalogue.py` để kiểm tra bản trích ngoại tuyến. Quy tắc lọc dùng các hostname trường như những mục giáo dục thông thường. Nội dung từng trang và độ tuổi phù hợp cần đánh giá riêng.

## Bộ nghiệm thu DNS

`safety.csv` chứa ngoại lệ ưu tiên cao hơn quy tắc của phụ huynh, gồm kênh 111 và cổng giáo dục. Mỗi dòng ghi tên miền, mục đích và nguồn chính thức. Khi thêm một cổng trường địa phương, kiểm tra nguồn, phạm vi tên con và quyền biên tập trước khi đóng gói bản agent mới. Danh sách website giáo dục thông thường và danh sách trợ giúp được miễn chặn có hai mục đích khác nhau.

`tests/fixtures/dns_cases.csv` có 50 miền cần chặn theo cấu hình thử và 20 miền giáo dục mô phỏng cần cho phép. `scripts/check_dns.py` còn thử 20 miền giáo dục công khai từ danh mục qua cùng upstream giả, không tải trang. Các phản hồi RCODE thực tế nằm ở `docs/evidence/dns-acceptance.csv`. Bản kiểm tra SafeSearch bằng nslookup dùng cổng 53 trên loopback. Bản DNS client chạy với cổng tạm là phương án mặc định.

Nguồn cho nhóm giáo dục gồm cổng tuyển sinh Đại học Quốc gia TP.HCM và Hà Nội, cổng của Bộ GDĐT, NASA Space Place, Scratch và Khan Academy. Từng hostname có nguồn riêng ở cột `source`. [ĐHQG-HCM](https://tuyensinh.vnuhcm.edu.vn/index.php?route=catalog%2Ftintuc%2Fnoidung&tintuc_id=92) · [ĐHQG-HN](https://tuyensinh.vnu.edu.vn/index.php/Home/camnang/125) · [NASA Space Place](https://spaceplace.nasa.gov/) · [Scratch](https://scratch.mit.edu/about) · [Khan Academy](https://www.khanacademy.org/about)
