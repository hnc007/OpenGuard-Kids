// Requires Playwright and Chromium or MS Edge. The Python launcher supplies only temporary test credentials.
const {chromium} = require("playwright");
const fs=require("node:fs");
const assert=require("node:assert/strict");
const path=require("node:path");
const {randomUUID}=require("node:crypto");
const {spawnSync}=require("node:child_process");
(async()=>{
  const browser=await chromium.launch({headless:true,channel:process.env.OGK_BROWSER_CHANNEL || "msedge"});
  const errors=[];
  try {
    const context=await browser.newContext({viewport:{width:1180,height:850}});
    const page=await context.newPage();
    page.on("pageerror",error=>errors.push(error.message));
    async function login(user){
      await page.goto(process.env.OGK_CHECK_ORIGIN);
      await page.getByLabel("Tên đăng nhập",{exact:true}).fill("browser."+user);
      await page.getByLabel("Mật khẩu",{exact:true}).fill(process.env.OGK_CHECK_PASSWORD);
      await page.getByRole("button",{name:"Vào không gian gia đình"}).click();
      await page.getByRole("heading",{name:"7 ngày gần đây"}).waitFor();
    }
    const capture=async name=>{
      const directory=process.env.OGK_BROWSER_EVIDENCE;
      if(directory){
        fs.mkdirSync(directory,{recursive:true});
        await page.screenshot({path:path.join(directory,name+".png"),fullPage:true});
      }
    };
    await login("parent");
    await page.getByRole("button",{name:"Quy tắc",exact:true}).click();
    await page.getByLabel("Ngày thường (phút)").fill("3");
    const cell=page.locator('[data-slot="20"]');
    await cell.click();
    await page.getByRole("button",{name:"Lưu quy tắc"}).click();
    await page.getByText("Đã ghi nhận thay đổi.").waitFor();
    assert.equal(await cell.getAttribute("aria-pressed"),"false");
    await page.getByRole("button",{name:"Thiết bị",exact:true}).click();
    await page.locator('#pair input[type=checkbox]').first().waitFor();
    assert.equal(await page.locator('#pair input[type=checkbox]').count(),3);
    for(const box of await page.locator('#pair input[type=checkbox]').all()) await box.check();
    await page.getByRole("button",{name:"Tạo mã ghép"}).click();
    await page.locator("#code strong").waitFor();
    const code=await page.locator("#code strong").textContent();
    assert.match(code,/^[A-Z0-9]{8}$/);
    const origin=process.env.OGK_CHECK_ORIGIN;
    const enrollment=await page.request.post(origin+"/api/enroll",{data:{code,installation_id:randomUUID(),label:"Máy học mẫu"}});
    assert.equal(enrollment.status(),201);
    const device=await enrollment.json();
    if(process.platform==="win32"){
      const store=process.env.OGK_CHECK_STORE;
      function agent(args,input){
        const result=spawnSync(process.env.OGK_CHECK_PYTHON,args,{input,encoding:"utf8",timeout:15000,
          env:{...process.env,PYTHONUTF8:"1"}});
        assert.equal(result.status,0,result.stderr || String(result.error));
        return result.stdout;
      }
      agent(["-c","import json,sys; from agent.store import AgentStore; AgentStore(sys.argv[1]).save_identity(json.load(sys.stdin))",store],
        JSON.stringify({...device,origin}));
      await page.getByRole("button",{name:"Làm mới trạng thái"}).click();
      for(const mode of ["sync","tray"]){
        const history=page.locator("#command-history");
        await page.getByRole("button",{name:"Thêm 15 phút",exact:true}).click();
        await history.getByText(/Đang chờ máy/).waitFor();
        if(mode==="sync"){
          const output=agent(["-m","agent.client","sync","--server",origin,"--store",store]);
          assert.match(output,/Đã xác nhận 1 lệnh mô phỏng/);
        }else{
          agent(["-c",`
import sys
from agent.client import AgentClient
from agent.coordinator import DemoSession
from agent.store import AgentStore
store, origin = sys.argv[1:]
client = AgentClient(AgentStore(store), origin)
session = DemoSession(client, lambda: True, lambda message: None)
client.listen_commands(session.command, session.ready, seconds=2)
assert session.clock.state.extra_seconds == 1800
`,store,origin]);
        }
        await history.getByText(/Máy đã xác nhận/).nth(mode==="sync"?0:1).waitFor();
      }
      assert.equal(agent(["-c","import sys; from agent.screen_time import CounterStore; print(CounterStore(sys.argv[1],sys.argv[2]).load().extra_seconds)",store,device.device_id]).trim(),"1800");
    }
    const now=Math.floor(Date.now()/1000);
    const record=(age,type,subject,seconds=0)=>({event_id:randomUUID(),payload:{ts:now-age*86400,
      device_id:device.device_id,child_id:device.child_id,policy_id:2,type,subject,duration_sec:seconds}});
    const events=[...Array.from({length:7},(_,i)=>record(i,"app_stop",i%2?"khoahoc.exe":"hocve.exe",(22+7*(i%4))*60)),
      record(0,"domain_query","class.example.org"),record(0,"blocked_domain","denied.example.net")];
    const delivery=await page.request.post(origin+"/api/devices/"+device.device_id+"/events",
      {headers:{Authorization:"Bearer "+device.access_token},data:{events}});
    assert.equal(delivery.status(),200);
    await page.getByRole("button",{name:"Tổng quan",exact:true}).click();
    await page.getByText("hocve.exe",{exact:false}).waitFor();
    await capture("dashboard-parent");
    await page.getByRole("button",{name:"Đăng xuất"}).click();
    await page.getByRole("heading",{name:"Đăng nhập"}).waitFor();
    await page.setViewportSize({width:390,height:844});
    await login("child");
    await page.getByRole("button",{name:"Ý kiến của em"}).click();
    await page.getByLabel("Tên miền",{exact:true}).fill("class.example.org");
    await page.getByRole("button",{name:"Nhờ bố mẹ xem lại"}).click();
    await page.getByText("Xem lại example.org").waitFor();
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await capture("dashboard-child-mobile");
    await page.getByRole("button",{name:"Đăng xuất"}).click();
    await page.getByRole("heading",{name:"Đăng nhập"}).waitFor();
    await login("parent");
    await page.getByRole("button",{name:"Yêu cầu",exact:true}).click();
    await page.getByLabel("Phản hồi cho em").fill("Bố mẹ sẽ cùng em xem lại.");
    await page.getByRole("button",{name:"Đồng ý",exact:true}).click();
    await page.getByText("Đã đồng ý",{exact:true}).waitFor();
    // Delay an actual issued code across a profile switch. Only the original
    // form may ever display it, even if the second profile also has a code box.
    const firstProfile=await page.locator("#child").inputValue();
    await page.getByRole("button",{name:"Tài khoản",exact:true}).click();
    await page.getByLabel("Biệt danh").fill("Hồ sơ thử thứ hai");
    await page.getByRole("button",{name:"Tạo hồ sơ",exact:true}).click();
    await page.getByText("Đã ghi nhận thay đổi.").waitFor();
    const secondProfile=await page.locator("#child").inputValue();
    assert.notEqual(firstProfile,secondProfile);
    await page.locator("#child").selectOption(firstProfile);
    await page.getByRole("button",{name:"Thiết bị",exact:true}).click();
    let issued,releaseCode;
    const codeIssued=new Promise(resolve=>{issued=resolve;});
    const delay=new Promise(resolve=>{releaseCode=resolve;});
    await page.route("**/pairing",async route=>{
      const response=await route.fetch();
      assert.equal(response.status(),201);
      issued();await delay;await route.fulfill({response});
    },{times:1});
    await page.locator("#pair input").first().waitFor();
    for(const box of await page.locator("#pair input").all()) await box.check();
    await page.getByRole("button",{name:"Tạo mã ghép"}).click();
    await codeIssued;
    await page.locator("#child").selectOption(secondProfile);
    await page.locator("#pair button").waitFor();
    const received=page.waitForEvent("requestfinished",{predicate:r=>r.url().endsWith("/pairing")});
    releaseCode();await received;
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    assert.equal(await page.locator("#code").innerText(),"");

    // Logout invalidates an open tab in the same browser profile even when the
    // second tab has already received family data and makes no new request.
    const sibling=await context.newPage();
    sibling.on("pageerror",error=>errors.push(error.message));
    await sibling.goto(origin);
    await sibling.getByRole("heading",{name:"7 ngày gần đây"}).waitFor();
    await page.getByRole("button",{name:"Đăng xuất"}).click();
    await sibling.getByRole("heading",{name:"Đăng nhập"}).waitFor();
    assert.equal(await sibling.locator("#child").count(),0);
    await sibling.goto(origin+"/#requests");
    await sibling.getByLabel("Tên đăng nhập",{exact:true}).fill("browser.child");
    await sibling.getByLabel("Mật khẩu",{exact:true}).fill(process.env.OGK_CHECK_PASSWORD);
    await sibling.getByRole("button",{name:"Vào không gian gia đình"}).click();
    await sibling.getByRole("button",{name:"Xin thêm 15 phút"}).waitFor();
    await sibling.getByRole("button",{name:"Quy tắc",exact:true}).click();
    await sibling.getByLabel("Ngày thường (phút)").waitFor();
    await sibling.evaluate(()=>{location.hash="requests";});
    await sibling.getByRole("button",{name:"Xin thêm 15 phút"}).waitFor();

    // Invalidate the session directly on the server. Exercise the history
    // restore callback without depending on Edge's automatic BFCache policy.
    const profile=await (await context.request.get(origin+"/api/auth/me")).json();
    const ended=await context.request.post(origin+"/api/auth/logout",{headers:{"X-CSRF-Token":profile.csrf_token}});
    assert.equal(ended.status(),204);
    await sibling.evaluate(()=>window.dispatchEvent(new PageTransitionEvent("pageshow",{persisted:true})));
    await sibling.getByRole("heading",{name:"Đăng nhập"}).waitFor();
    assert.deepEqual(errors,[]);
    console.log("Passed browser flows: family dashboard; delayed pairing across two profiles; logout across tabs; child requests deep link; server invalidation on history restore callback.");
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
