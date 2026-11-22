// This recorder operates only on a private loopback demo started by record.py.
const {chromium} = require("playwright");
const {spawnSync} = require("node:child_process");
const {pathToFileURL} = require("node:url");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const env = process.env, work = env.OGK_FILM_TEMP;
const rehearse = env.OGK_REHEARSE === "1";
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const esc = s => s.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
function command(action, input = "") {
  const child = spawnSync(env.OGK_FILM_PYTHON, ["-m", "demo.record", action], {encoding:"utf8", input, timeout:20000});
  if (child.status !== 0) throw Error(action + " failed: " + child.stderr);
  return child.stdout.trim();
}
function srt(ms) {
  return new Date(ms).toISOString().slice(11,23).replace(".", ",");
}
(async () => {
  const browser = await chromium.launch({channel:"msedge", headless:true});
  const ctx = await browser.newContext({viewport:{width:1280,height:800}, bypassCSP:true,
    recordVideo:rehearse ? undefined : {dir:work,size:{width:1280,height:800}}});
  // Presentation-only redaction takes effect before passwords or pairing codes are displayed.
  await ctx.addInitScript(() => {
    document.addEventListener("DOMContentLoaded", () => {
      const style = document.createElement("style");
      style.textContent = "#code strong{font-size:0!important}#code strong::after{content:'••••••••';font-size:19px!important}" +
        "#login input[name=username],#login input[type=password]{-webkit-text-security:disc!important;color:transparent!important;caret-color:transparent!important}" +
        "html{scroll-behavior:smooth}*:focus-visible{outline:3px solid #cb7d22!important;outline-offset:3px}";
      document.head.append(style);
    });
  });
  const start = Date.now();
  const page = await ctx.newPage();
  page.setDefaultTimeout(6000);
  const errors = [], captions = [];
  page.on("pageerror", e=>errors.push(e.message));
  const caption = (line) => captions.push({start:Date.now()-start, line});
  const hold = seconds => pause(rehearse ? 60 : seconds*1000);
  const tell = async (line, seconds) => {caption(line); await hold(seconds);};
  const nav = async id => {await page.locator(`[data-tab="${id}"]`).click();};
  const button = name => page.getByRole("button",{name,exact:true});
  const top = () => page.evaluate(()=>window.scrollTo({top:0,behavior:"instant"}));
  const focus = async element => {await element.scrollIntoViewIfNeeded(); await element.focus();};
  async function signIn(role) {
    await page.locator("#login").waitFor();
    await page.locator("#login input[name=username]").fill("demo."+role);
    await page.locator("#login input[type=password]").fill(env.OGK_CHECK_PASSWORD);
    await button("Vào không gian gia đình").click();
    await page.getByRole("heading",{name:"7 ngày gần đây"}).waitFor();
  }
  async function output(title, lines) {
    const file = path.join(work,"agent-output.html");
    fs.writeFileSync(file,`<!doctype html><html lang="vi"><meta charset="utf-8"><title>${esc(title)}</title>
      <style>body{max-width:1110px;margin:40px auto;padding:0 28px;font:20px/1.45 'Segoe UI',sans-serif;color:#162f32}
      h1{font-size:30px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:17px/1.6 Consolas,monospace;background:#f1f5f4;padding:20px}
      .line{display:block}.active{background:#fff0c8;color:#172a30}</style>
      <h1>${esc(title)}</h1><pre>${lines.split(/\r?\n/).map(x=>`<span class="line">${esc(x)}</span>`).join("")}</pre></html>`);
    await page.goto(pathToFileURL(file).toString());
  }
  async function point(text) {
    const target = page.locator(".line").filter({hasText:text}).first();
    await target.evaluate(el=>{document.querySelectorAll(".active").forEach(x=>x.classList.remove("active"));
      el.classList.add("active"); el.scrollIntoView({block:"center",behavior:"instant"});});
  }
  try {
    await page.goto(env.OGK_CHECK_ORIGIN);
    await tell("OpenGuard Kids • Trình diễn bằng hồ sơ giả trên Windows. Phụ huynh và trẻ cùng xem thời gian sử dụng và các quy tắc.", 9);
    caption("Phụ huynh mở phiên riêng. Bản ghi che mật khẩu và mã ghép thiết bị.");
    await focus(page.getByLabel("Tên đăng nhập",{exact:true})); await hold(3); await signIn("parent");
    await tell("Hồ sơ An vừa khởi tạo. Biểu đồ đang trống vì máy chủ chưa nhận sự kiện.", 7);
    await nav("devices");
    await tell("Mỗi thiết bị được ghép với một hồ sơ. Gia đình đọc phạm vi xử lý và xác nhận đã giải thích cho trẻ.", 10);
    caption("Ba xác nhận ghi nhận quyền giám hộ, việc giải thích và sự đồng ý của trẻ.");
    for (const box of await page.locator("#pair input[type=checkbox]").all()) {await box.check();await hold(2);}
    await button("Tạo mã ghép").click();
    const code = await page.locator("#code strong").innerText();
    assert.match(code,/^[A-Z0-9]{8}$/);
    await tell("Mã ghép có 8 ký tự, dùng một lần trong 10 phút. Mã đã được che trước khi ghi hình.", 7);
    command("enroll",code+"\n");
    await nav("overview"); await nav("devices");
    await page.getByText("Máy mô phỏng",{exact:true}).waitFor();
    await tell("AgentClient vừa ghép đôi và gửi heartbeat qua loopback. Máy mô phỏng đã xuất hiện trong hồ sơ An.", 8);
    await nav("policy"); await focus(page.getByLabel("Ngày thường (phút)"));
    await tell("Phụ huynh có thể đặt hai hạn mức riêng. Bài thử dùng 3 phút cho cả ngày thường và cuối tuần.", 8);
    await page.getByLabel("Ngày thường (phút)").fill("3"); await hold(2);
    await page.getByLabel("Cuối tuần (phút)").fill("3"); await hold(2);
    await focus(page.locator("#schedule-day"));
    await tell("Lịch có 7 ngày, mỗi ngày 48 ô. Một ô tương ứng với 30 phút theo giờ Việt Nam.", 8);
    await page.locator("#schedule-day").selectOption("0");
    const cell = page.locator('[data-slot="20"]'); await cell.click();
    assert.equal(await cell.getAttribute("aria-pressed"),"false");
    await tell("Trong lịch thứ Hai, ô 10 giờ vừa chuyển sang trạng thái giới hạn.", 7);
    await focus(page.locator("textarea[name=blocked_domains]"));
    await page.locator("textarea[name=blocked_domains]").fill("north-maze.example.org");
    await tell("Đây là miền thử trung tính. Quy tắc DNS áp dụng cho cả tên miền và miền con.", 8);
    await page.locator("textarea[name=blocked_apps]").fill("notepad.exe "+env.OGK_FIXTURE_HASH);
    await tell("Quy tắc ứng dụng dùng tên tệp và SHA-256. Tệp trong bài thử là dữ liệu giả, có thể đổi tên để kiểm tra nhận diện.", 9);
    await button("Lưu quy tắc").click();
    await page.getByText("Đã ghi nhận thay đổi.").waitFor();
    await top(); await tell("Máy chủ đã lưu phiên bản quy tắc mới. Agent tải và kiểm tra chữ ký trước khi chạy mô phỏng.", 7);
    const log = command("simulate");
    assert(log.includes("NXDOMAIN") && log.includes("Giây 180") && log.includes("game.exe bị chặn"));
    await output("Kết quả chạy mô phỏng trên thiết bị thử",log);
    await point("Quy tắc phiên bản");
    await tell("Đây là đầu ra thực tế của lần chạy. Đồng hồ mô phỏng tăng tốc để minh họa hạn mức 3 phút.", 8);
    await point("Em còn 1 phút");
    await tell("Khi còn 1 phút, agent nhắc em lưu bài. Phút cuối nằm trong hạn mức 3 phút đã cấu hình.", 9);
    await point("Giây 180");
    await tell("Ở giây 180, bộ đếm đưa ra quyết định nghỉ. Lần chạy này không khóa màn hình Windows.", 8);
    await point("north-school.example.org →");
    await tell("Proxy vừa nhận gói DNS qua loopback. Miền giáo dục trong bộ thử nhận phản hồi NOERROR từ upstream mô phỏng.", 9);
    await point("north-maze.example.org →");
    await tell("Miền đã giới hạn nhận NXDOMAIN. Dòng tiếp theo giải thích chính sách và quyền gửi yêu cầu xem lại.", 8);
    await point("forcesafesearch.google");
    await tell("Truy vấn Google trả về đích SafeSearch. Hành vi của trình duyệt khi bật DNS mã hóa còn cần kiểm tra trong lab.", 9);
    await point("Tệp mẫu sau khi đổi tên");
    await tell("Cùng tệp thử được đổi từ notepad.exe thành game.exe. Hai giá trị SHA-256 trùng nhau.", 8);
    await point("game.exe bị chặn");
    await tell("Bộ so khớp vẫn nhận diện ứng dụng bị giới hạn. Bài thử chỉ đánh giá quyết định và lời giải thích.", 8);
    command("history");
    await page.goto(env.OGK_CHECK_ORIGIN);
    await page.getByText("demo-reader.exe",{exact:false}).waitFor();
    await tell("Báo cáo hiện dùng 10 ngày sự kiện giả vừa nạp vào máy đã ghép. Biểu đồ đọc số liệu từ SQLite.", 8);
    await focus(button("Đăng xuất")); await button("Đăng xuất").click(); await signIn("child");
    await tell("Đây là Góc của em. Trẻ thấy các tổng ngày, tuần và ứng dụng của chính mình trên cùng bộ dữ liệu.", 9);
    await nav("requests");
    await tell("Em có hai cách phản hồi. Em có thể xin thêm 15 phút, hoặc đề nghị xem lại một miền bị giới hạn.", 8);
    await button("Xin thêm 15 phút").click(); await page.getByText("Đang chờ",{exact:true}).first().waitFor();
    await tell("Yêu cầu thêm giờ đã được ghi nhận. Em có thể xem trạng thái trong danh sách bên dưới.", 7);
    await page.getByLabel("Tên miền",{exact:true}).fill("north-maze.example.org");
    await button("Nhờ bố mẹ xem lại").click();
    await page.getByText("Xem lại example.org").waitFor();
    await tell("Yêu cầu xem lại giữ tên miền đăng ký example.org. Trang không lưu đường dẫn hay nội dung em đã đọc.", 8);
    await nav("privacy");
    await tell("Màn hình quyền riêng tư giải thích bằng lời dễ hiểu dữ liệu được ghi nhận, quyền phản hồi và số trợ giúp 111.", 10);
    await button("Đăng xuất").click(); await signIn("parent"); await nav("requests");
    const timeForm = page.locator("form[data-decision]").filter({has:page.locator("select[name=device_id]")});
    await timeForm.locator("select").selectOption({index:1});
    await timeForm.locator("input[name=answer]").fill("Bố mẹ đồng ý. Em có thêm 15 phút để hoàn thành bài nhé.");
    await tell("Phụ huynh chọn đúng máy để nhận 15 phút và viết lời giải thích để trẻ đọc được.", 9);
    await timeForm.getByRole("button",{name:"Đồng ý",exact:true}).click();
    await page.getByText("Đã đồng ý",{exact:true}).waitFor();
    const ack = command("ack"); await output("Đồng bộ quyết định thêm giờ",ack);
    await tell("Agent đã nhận lệnh. Bộ đếm mô phỏng lưu thêm 900 giây, rồi gửi ACK về API của thiết bị.", 9);
    await page.goto(env.OGK_CHECK_ORIGIN); await nav("audit");
    await page.getByText("Đổi quy tắc",{exact:true}).click();
    await tell("Nhật ký ghi người thay đổi và thời điểm. Mở một mục để đối chiếu giá trị trước và sau.", 9);
    await button("Đăng xuất").click(); await signIn("child"); await nav("requests");
    await page.getByText("Đã đồng ý",{exact:true}).waitFor();
    await tell("Trở lại tài khoản của An, em thấy quyết định đã đồng ý và đọc được lời nhắn của bố mẹ.", 8);
    await nav("overview"); await page.getByText("demo-reader.exe",{exact:false}).waitFor();
    await tell("Hai vai trò nhìn thấy cùng kết quả cho An. Máy khác và gia đình khác có phạm vi truy cập riêng.", 8);
    await nav("privacy");
    await tell("Chỉ phụ huynh quản lý hồ sơ được xác nhận xóa. Bước cuối sử dụng gia đình demo dùng một lần, trong database tạm.", 9);
    await button("Đăng xuất").click(); await signIn("parent"); await nav("privacy");
    await focus(page.locator("#erase input"));
    await tell("Phụ huynh kiểm tra hồ sơ đang chọn và đọc tác động của việc xóa. Thiết bị đang ngoại tuyến sẽ chờ đồng bộ.", 9);
    await page.locator("#erase input").check(); page.once("dialog", dialog=>dialog.accept());
    await page.locator("#erase button").click();
    await page.getByText("Đang chờ thiết bị",{exact:false}).waitFor();
    await tell("Dữ liệu của hồ sơ đã bị xóa ở máy chủ. Biên nhận vẫn ghi đang chờ thiết bị vì bộ đệm chưa được xác nhận xóa.", 9);
    command("wipe"); await nav("account"); await nav("privacy");
    await page.getByText("Đã xác nhận xóa",{exact:false}).waitFor();
    await tell("Khi máy mô phỏng liên lạc lại, agent dọn kho cục bộ và gửi biên nhận. Dashboard vừa cập nhật trạng thái hoàn tất.", 8);
    await tell("Phiên quay dùng một máy giả và DNS loopback. Cài service, lọc DNS adapter, khóa phiên và đường trợ giúp khi khóa còn cần Windows Lab.", 12);
    assert.deepEqual(errors,[]);
    const end = Date.now()-start;
    const subtitle = captions.map((c,i)=>`${i+1}\n${srt(c.start)} --> ${srt((captions[i+1]?.start ?? end)-1)}\n${c.line}\n`).join("\n");
    if (!rehearse) {
      assert(end >= 300000 && end <= 420000,`Duration ${end}ms`);
      fs.writeFileSync(path.join(work,"timing.srt"),subtitle,"utf8");
      fs.writeFileSync(path.join(work,"duration.txt"),String(end/1000),"ascii");
      const video=page.video(); await ctx.close(); await video.saveAs(path.join(work,"capture.webm"));
    }
    console.log("Verified walkthrough; duration", Math.round(end/1000),"seconds.");
  } finally {await browser.close();}
})().catch(err=>{console.error(err.message);process.exitCode=1;});
