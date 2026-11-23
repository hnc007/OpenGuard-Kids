"use strict";
const app = document.querySelector("#app");
const notice = document.querySelector("#notice");
const session = document.querySelector("#session");
const state = { user: null, child: null, children: [], tab: "overview", csrf: "", revision: 0, day: 0, offset: 0 };
let sessionGeneration = 0;
const sessionChanges = "BroadcastChannel" in window ? new BroadcastChannel("openguard-session") : null;
const days = ["Thứ Hai","Thứ Ba","Thứ Tư","Thứ Năm","Thứ Sáu","Thứ Bảy","Chủ nhật"];
const categories = {education:"Giáo dục",entertainment:"Giải trí",social:"Mạng xã hội",games:"Trò chơi",inappropriate:"Chưa phù hợp lứa tuổi",unknown:"Chưa phân loại"};
const parent = () => state.user?.role === "parent";
const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const date = ts => new Date(typeof ts==="number"?ts*1000:ts).toLocaleString("vi-VN",{timeZone:"Asia/Ho_Chi_Minh"});
const duration = seconds => seconds < 60 ? `${seconds} giây` : `${Math.floor(seconds/60)} phút`;
const childURL = suffix => `/api/children/${state.child}${suffix}`;
const card = (title, content) => `<section class="card"><h2>${esc(title)}</h2>${content}</section>`;
const empty = text => `<p class="empty">${esc(text)}</p>`;
const list = (items, noItems) => items.length ? `<ul class="list">${items.map(item=>`<li>${item}</li>`).join("")}</ul>` : empty(noItems);
const button = (action,label,cls="secondary") => `<button type="button" data-act="${action}" class="${cls}">${esc(label)}</button>`;
const setNotice = text => {notice.textContent = text;};
const childPages = new Map([["time","requests"],["appeal","requests"],["report","overview"],["privacy","privacy"],
                            ["requests","requests"],["policy","policy"],["devices","devices"],["audit","audit"]]);
function childRoute(hash=location.hash) {
  if (!hash || hash==="#requests") return null;
  const match=/^#child-([a-z]+)\/([1-9]\d*)$/.exec(hash);
  const id=match ? Number(match[2]) : null;
  if (!match || !childPages.has(match[1]) || !Number.isSafeInteger(id)) return {id:null};
  return {id,page:match[1],tab:childPages.get(match[1])};
}
function matchesChildRoute(user, route=childRoute()) {
  return !route || (route.id!==null && user?.role==="child-view" && user.child_id===route.id);
}
function refuseChildRoute() {
  state.child=null;state.children=[];state.policy=null;state.revision++;
  setNotice("");session.replaceChildren();
  app.innerHTML=`<main class="shell"><h1>Góc của em</h1><p>Cần liên kết từ máy đã ghép và tài khoản riêng đúng với hồ sơ trên máy đó. Em hãy mở lại liên kết từ khay hoặc đăng nhập đúng tài khoản.</p>${button("logout","Đăng xuất để đổi tài khoản")}</main>`;
}
async function api(path,method="GET",body) {
  const generation = sessionGeneration;
  const hash = location.hash;
  if (!path.startsWith("/api/auth/") && !matchesChildRoute(state.user))
    throw new DOMException("Hồ sơ của thiết bị đã thay đổi.", "AbortError");
  const headers = {"Accept":"application/json"};
  if (method !== "GET") headers["X-CSRF-Token"] = state.csrf;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path,{method,headers,credentials:"same-origin",cache:"no-store",body:body===undefined?undefined:JSON.stringify(body)});
  const result = response.status === 204 ? null : await response.json();
  if (generation !== sessionGeneration || (hash!==location.hash && (childRoute(hash) || childRoute())))
    throw new DOMException("Phiên đã thay đổi.", "AbortError");
  if (!response.ok) {
    if (response.status === 401 && state.user) loginScreen();
    throw new Error(typeof result.detail === "string" ? result.detail : "Thao tác chưa thành công.");
  }
  return result;
}
function loginScreen() {
  sessionGeneration++;
  Object.assign(state,{user:null,csrf:"",child:null,children:[],policy:null,day:0,offset:0,tab:"overview"});
  state.revision++;
  setNotice("");
  maskFamily(false);
  session.replaceChildren();
  app.innerHTML = `<main class="shell login"><section class="intro"><div class="motif" aria-hidden="true"><i></i><i></i><i></i><i></i></div><h1>Không gian gia đình</h1><p>Cùng xem thời gian dùng máy, thống nhất quy tắc và lắng nghe những điều em muốn thay đổi.</p></section><form class="login-form" id="login"><h2>Đăng nhập</h2><label>Tên đăng nhập<input name="username" required autocomplete="username" autocapitalize="none"></label><label>Mật khẩu<input type="password" name="password" required autocomplete="current-password"></label><button>Vào không gian gia đình</button></form></main>`;
}
function maskFamily(mask) {
  app.hidden=mask;
  session.hidden=mask;
  notice.hidden=mask;
}
async function verifyVisibleSession() {
  if (!state.user || document.hidden) return;
  const generation=sessionGeneration;
  maskFamily(true);
  try {
    const current=await api("/api/auth/me");
    if (current.id!==state.user.id || current.csrf_token!==state.csrf || current.role!==state.user.role
        || current.child_id!==state.user.child_id) {
      loginScreen();
      return;
    }
    if (!matchesChildRoute(current)) refuseChildRoute();
    if (!document.hidden) maskFamily(false);
  } catch {
    if (generation===sessionGeneration) loginScreen();
  }
}
if (sessionChanges) sessionChanges.onmessage=()=>{loginScreen();};
document.addEventListener("visibilitychange",()=>{
  if (document.hidden && state.user) maskFamily(true);
  else if (!document.hidden) void verifyVisibleSession();
});
window.addEventListener("pagehide",()=>{if(state.user) maskFamily(true);});
window.addEventListener("pageshow",event=>{if(event.persisted) void verifyVisibleSession();});
window.addEventListener("hashchange",event=>{
  if(childRoute() || childRoute(new URL(event.oldURL).hash)){
    sessionGeneration++;state.revision++;maskFamily(true);
    location.reload();
    return;
  }
  if(location.hash==="#requests" && state.user && state.user.role!=="admin" && state.tab!=="requests") {
    state.tab="requests";state.offset=0;setNotice("");void render();
  }
});
const tabs = () => ({overview:parent()?"Tổng quan":"Hôm nay em đã dùng gì?",policy:"Quy tắc",devices:"Thiết bị",requests:parent()?"Yêu cầu":"Ý kiến của em",audit:"Lịch sử quy tắc",privacy:"Quyền riêng tư",...(parent()?{account:"Tài khoản"}:{})});
async function loadChildren(preferred) {
  const revision=state.revision;
  const children=[];
  for (let offset=0;;offset+=100) {
    const page=await api(`/api/children?offset=${offset}&limit=100`); children.push(...page);
    if(page.length<100) break;
  }
  if(revision!==state.revision) return;
  state.children=children;
  state.child=children.find(x=>x.id===preferred)?.id ?? children[0]?.id ?? null;
}
async function openSession(user) {
  sessionGeneration++;
  state.user=user;state.csrf=user.csrf_token;
  const route=childRoute();
  state.tab=route?.tab || (location.hash==="#requests"?"requests":"overview");
  if (!matchesChildRoute(user,route)) {refuseChildRoute();return;}
  session.innerHTML=`<span class="account-label">${esc(user.username)}</span> ${button("logout","Đăng xuất")}`;
  if (user.role==="admin") {
    const data=await api("/api/admin/status");
    app.innerHTML=`<main class="shell"><h1>Quản trị hệ thống</h1><p>${esc(data.accounts)} tài khoản · ${esc(data.children)} hồ sơ trẻ.</p></main>`;
    return;
  }
  if (route) {
    // A tray link has one authorized target. Never fall back to the first family profile.
    state.children=[];state.child=route.id;
  } else await loadChildren();
  await render();
}
function frame() {
  const selector=state.children.length?`<label>Hồ sơ<select id="child">${state.children.map(c=>`<option value="${c.id}" ${c.id===state.child?"selected":""}>${esc(c.name)}</option>`).join("")}</select></label>`:"";
  app.innerHTML=`<main class="shell"><header class="heading"><div><h1>${parent()?"Không gian gia đình":"Góc của em"}</h1><p>${parent()?"Quản lý thời gian và quy tắc sử dụng máy tính.":"Em có thể xem quy tắc và nhắn ý kiến cho bố mẹ."}</p></div>${selector}</header><nav class="tabs" aria-label="Các trang">${Object.entries(tabs()).map(([id,title])=>`<button data-tab="${id}" ${id===state.tab?'aria-current="page"':""}>${esc(title)}</button>`).join("")}</nav><div id="view" class="stack" aria-live="polite"><p>Đang tải…</p></div></main>`;
}
async function render() {
  const route=childRoute();
  if (!matchesChildRoute(state.user,route) || (route && state.child!==route.id)) {refuseChildRoute();return;}
  const revision=++state.revision;
  frame();
  const view=document.querySelector("#view");
  try {
    let html;
    if (state.tab==="account") html=accountPage();
    else if (state.tab==="privacy") html=await privacyPage();
    else if (!state.child) html=card("Hồ sơ gia đình",empty("Chưa có hồ sơ trẻ.")+(parent()?button("account","Tạo hồ sơ"):""));
    else html=await ({overview:overviewPage,policy:policyPage,devices:devicesPage,requests:requestsPage,audit:auditPage}[state.tab])();
    if(revision===state.revision) {
      view.innerHTML=html;
      if(state.tab==="requests" && route?.page==="appeal") view.querySelector("#ask-domain input")?.focus();
      if(state.tab==="requests" && route?.page==="time") view.querySelector("#ask-time button")?.focus();
    }
  } catch(error) {
    if(revision===state.revision) view.innerHTML=card("Chưa tải được dữ liệu",`<p>${esc(error.message)}</p>${button("retry","Thử lại")}`);
  }
}
function chart(items) {
  const max=Math.max(1,...items.map(d=>d.seconds));
  return items.map(d=>`<div class="bar"><span>${esc(d.date.slice(8)+"/"+d.date.slice(5,7))}</span><progress aria-label="${esc(d.date)}" value="${d.seconds}" max="${max}"></progress><span>${duration(d.seconds)}</span></div>`).join("");
}
async function overviewPage() {
  const [r,devices]=await Promise.all([api(childURL("/reports")),api(childURL("/devices"))]);
  const stats=[[parent()?"Ứng dụng hôm nay":"Em đã dùng hôm nay",duration(r.today_seconds)],["Tuần này",duration(r.week_seconds)],["Lượt bị chặn trong tuần",r.blocked]];
  const metrics=`<div class="metrics">${stats.map(([k,v])=>`<div class="metric"><small>${esc(k)}</small><b>${esc(v)}</b></div>`).join("")}</div>`;
  const status=devices.filter(d=>!d.revoked).map(d=>{
    const fresh=d.last_seen && Date.now()/1000-d.last_seen<120;
    let message="Đang chờ thiết bị báo thời gian";
    if(fresh && d.status==="paused") message="Đang tạm dừng";
    if(fresh && d.remaining_seconds!=null) message=`Còn ${duration(d.remaining_seconds)} tại lần báo gần nhất`;
    return `<strong>${esc(d.label)}</strong> · ${message}${d.last_seen?`<br><small>${date(d.last_seen)}</small>`:""}`;
  });
  return metrics+`<div class="grid">${card("7 ngày gần đây",chart(r.days))}${card("4 tuần gần đây",chart(r.weeks))}${card("Ứng dụng trong tuần",list(r.apps.map(([a,s])=>`${esc(a)} · ${duration(s)}`),"Chưa có phiên ứng dụng kết thúc."))}${card("Tên miền trong tuần",list(r.domains.map(([d,n])=>`${esc(d)} · ${n} lượt`),"Chưa có lượt truy vấn tên miền."))}</div>`+card("Thời gian trên từng máy",list(status,"Chưa ghép thiết bị.")+"<p>Biểu đồ cộng thời lượng các phiên ứng dụng đã gửi. Bộ đếm quota do từng máy báo về.</p>");
}
const textarea=(title,name,value)=>`<label>${esc(title)}<textarea name="${name}" ${parent()?"":"readonly"}>${esc(value)}</textarea></label>`;
function slots(day,schedule) {
  return Array.from({length:48},(_,i)=>{
    const t=`${String(Math.floor(i/2)).padStart(2,"0")}:${i%2?"30":"00"}`;
    return `<button class="slot" type="button" data-slot="${i}" aria-disabled="${!parent()}" aria-label="${days[day]} ${t}, cho phép" aria-pressed="${schedule[day][i]==="1"}">${t}</button>`;
  }).join("");
}
async function policyPage(){
  const revision=state.revision;
  const p=await api(childURL("/policy"));
  if(revision===state.revision) state.policy=p;
  const ro=parent()?"":"readonly";
  const appRules=key=>p[key].map(r=>`${r.name} ${r.sha256}`).join("\n");
  return `<form id="policy"><div class="grid">${card("Hạn mức mỗi ngày",`<div class="stack"><label>Ngày thường (phút)<input name="weekday_minutes" type="number" min="0" max="1440" required value="${p.weekday_minutes}" ${ro}></label><label>Cuối tuần (phút)<input name="weekend_minutes" type="number" min="0" max="1440" required value="${p.weekend_minutes}" ${ro}></label><small>Chính sách phiên bản ${p.version}</small></div>`)}${card("Nhóm website cần giới hạn",`<div class="stack">${Object.entries(categories).map(([id,t])=>`<label class="check"><input type="checkbox" name="category" value="${id}" ${p.blocked_categories.includes(id)?"checked":""} ${parent()?"":"disabled"}>${t}</label>`).join("")}<label class="check"><input type="checkbox" name="safe_search" ${p.safe_search?"checked":""} ${parent()?"":"disabled"}>Bật Google SafeSearch qua DNS</label></div>`)}</div>`+
  card("Lịch dùng máy trong tuần",`<div class="stack"><p>Mỗi ô là 30 phút theo giờ Việt Nam. Ô xanh cho phép dùng máy.</p><label>Ngày trong tuần<select id="schedule-day">${days.map((d,i)=>`<option value="${i}" ${state.day===i?"selected":""}>${d}</option>`).join("")}</select></label><div class="slots" id="slots">${slots(state.day,p.schedule)}</div></div>`)+
  `<div class="grid">${card("Quy tắc tên miền",`<div class="stack"><p>Mỗi dòng một tên miền.</p>${textarea("Cho phép","allowed_domains",p.allowed_domains.join("\n"))}${textarea("Chặn","blocked_domains",p.blocked_domains.join("\n"))}</div>`)}${card("Quy tắc ứng dụng",`<div class="stack"><p>Mỗi dòng gồm tên .exe và SHA-256, cách nhau bằng dấu cách.</p>${textarea("Cho phép","allowed_apps",appRules("allowed_apps"))}${textarea("Chặn","blocked_apps",appRules("blocked_apps"))}</div>`)}</div>${parent()?'<button>Lưu quy tắc</button>':""}</form>`;
}
async function devicesPage(){
  const [devices,commands]=await Promise.all([api(childURL("/devices")),api(childURL("/commands"))]);
  return deviceCards(devices)+card("Lệnh gần đây",`<div id="command-history">${commandRows(commands)}</div>`+button("retry","Làm mới trạng thái"));
}
function commandRows(commands){
  const actions={lock:"Khóa",unlock:"Mở khóa",add_15:"Thêm 15 phút"};
  const statuses={queued:"Đang chờ máy",expired:"Hết hạn chờ xác nhận từ máy",acknowledged:"Máy đã xác nhận"};
  return list(commands.map(c=>`${esc(c.device)} · ${actions[c.action]} · ${statuses[c.status]} <small>${date(c.created_at)}</small>`),"Chưa gửi lệnh nào.");
}
async function refreshCommandHistory(){
  const history=document.querySelector("#command-history");
  if(!history || document.hidden || app.hidden) return;
  const commands=await api(childURL("/commands"));
  if(history.isConnected) history.innerHTML=commandRows(commands);
}
setInterval(()=>{void refreshCommandHistory().catch(()=>{});},3000);
function deviceCards(devices){
  return card("Thiết bị của hồ sơ",list(devices.map(d=>`<div class="stack"><div><strong>${esc(d.label)}</strong> · ${d.revoked?"Đã thu hồi":d.last_seen?`Lần liên lạc ${date(d.last_seen)}`:"Chờ nhịp tim đầu tiên"}</div>${parent()&&!d.revoked?`<div class="actions" data-device="${esc(d.id)}">${button("lock","Yêu cầu khóa")}${button("unlock","Mở khóa")}${button("add_15","Thêm 15 phút")}${button("revoke","Thu hồi thiết bị")}</div>`:""}</div>`),"Chưa ghép thiết bị nào."))+(parent()?card("Ghép thiết bị",`<form id="pair"><p>Thiết bị sẽ áp dụng lịch, giới hạn ứng dụng và DNS. Gia đình xem được tên ứng dụng, tên miền rút gọn, thời điểm và thời lượng trong 90 ngày.</p><label class="check"><input name="consent" type="checkbox" required>Tôi là phụ huynh hoặc người giám hộ và đồng ý phạm vi này.</label><label class="check"><input name="child_informed" type="checkbox" required>Tôi đã giải thích cho trẻ bằng lời dễ hiểu.</label><label class="check"><input name="child_agrees" type="checkbox" required>Trẻ đã đồng ý tham gia.</label><button>Tạo mã ghép</button><output id="code"></output></form>`):"");
}
function pager(size) {
  return `<div class="actions">${state.offset?button("prev","Trang trước"):""}${size===50?button("next","Trang tiếp"):""}</div>`;
}
async function requestsPage(){
  const [items,devices]=await Promise.all([api(childURL(`/requests?offset=${state.offset}`)),api(childURL("/devices"))]);
  const choices=devices.filter(d=>!d.revoked).map(d=>`<option value="${esc(d.id)}">${esc(d.label)}</option>`).join("");
  const statuses={pending:"Đang chờ",approved:"Đã đồng ý",declined:"Chưa đồng ý"};
  const rows=items.map(r=>`<div class="row"><strong>${r.kind==="time"?"Xin thêm 15 phút":`Xem lại ${esc(r.domain)}`}</strong><span class="tag">${statuses[r.status]}</span></div><small>${date(r.created_at)}</small>${r.answer?`<p class="feedback">${esc(r.answer)}</p>`:""}${parent()&&r.status==="pending"?`<form class="subform" data-decision="${r.id}"><label>Phản hồi cho em<input name="answer" maxlength="240" required></label>${r.kind==="time"?`<label>Máy nhận thời gian<select name="device_id"><option value="">Chọn thiết bị</option>${choices}</select></label>`:"<small>Sau khi xem lại, điều chỉnh danh sách tên miền trong trang Quy tắc nếu cần.</small>"}<div class="actions"><button value="approved">Đồng ý</button><button class="secondary" value="declined">Chưa đồng ý</button></div></form>`:""}`);
  return (parent()?"":`<div class="grid">${card("Xin thêm giờ",`<form id="ask-time"><p>Em muốn có thêm 15 phút để làm xong việc đang làm?</p><button>Xin thêm 15 phút</button></form>`)}${card("Em nghĩ trang này bị chặn nhầm",`<form id="ask-domain"><label>Tên miền<input name="domain" placeholder="example.org" required></label><button>Nhờ bố mẹ xem lại</button></form>`)}</div>`)+card("Yêu cầu và phản hồi",list(rows,"Chưa có yêu cầu nào.")+pager(items.length));
}
async function auditPage(){
  const rows=await api(childURL(`/audit?offset=${state.offset}`));
  const titles={"policy.update":"Đổi quy tắc","child.created":"Tạo hồ sơ","device.enroll":"Ghép thiết bị","device.revoke":"Thu hồi thiết bị","command.queued":"Gửi lệnh tới thiết bị","request.decision":"Trả lời yêu cầu","pairing.consent":"Xác nhận phạm vi ghép máy","child.account.created":"Tạo tài khoản của trẻ"};
  titles["lab.removal_authorized"]="Phụ huynh cho phép gỡ service";
  titles["lab.removal_reported"]="Quản trị viên báo đã gỡ service";
  return card("Lịch sử thay đổi",list(rows.map(r=>`<details><summary><strong>${esc(titles[r.action]||r.action)}</strong> · ${esc(r.actor)} · ${date(r.ts)}</summary>${r.action==="policy.update"?policyDiff(r.before,r.after):""}<h3>Trước khi đổi</h3><pre>${esc(JSON.stringify(r.before,null,2))}</pre><h3>Sau khi đổi</h3><pre>${esc(JSON.stringify(r.after,null,2))}</pre>${r.ip?`<small>Địa chỉ quản trị ${esc(r.ip)}</small>`:""}</details>`),"Chưa có thay đổi.")+pager(rows.length));
}
function policyDiff(before,after) {
  const changes=[];
  for(const [key,label] of [["weekday_minutes","Ngày thường"],["weekend_minutes","Cuối tuần"]]){
    if(before[key]!==after[key])changes.push(`${label}: từ ${before[key]} thành ${after[key]} phút.`);
  }
  if(before.schedule&&after.schedule) {
    let cells=0;
    after.schedule.forEach((row,day)=>[...row].forEach((value,i)=>{if(value!==before.schedule[day][i])cells++;}));
    if(cells)changes.push(`Bố mẹ đã chỉnh ${cells} ô nửa giờ trong lịch của em.`);
  }
  for(const [key,label] of [["allowed_domains","trang được phép"],["blocked_domains","trang cần giới hạn"],["allowed_apps","ứng dụng được phép"],["blocked_apps","ứng dụng cần giới hạn"],["blocked_categories","nhóm trang cần giới hạn"]]){
    if(JSON.stringify(before[key])!==JSON.stringify(after[key]))changes.push(`Bố mẹ đã đổi danh sách ${label}.`);
  }
  if(before.safe_search!==after.safe_search)changes.push(`Tìm kiếm an toàn qua DNS đã ${after.safe_search?"bật":"tắt"}.`);
  return list(changes.map(esc),"Các quy tắc của em vẫn như cũ.");
}
async function privacyPage(){
  let html=card("Ứng dụng này ghi nhận gì về em?",`<div class="stack"><p>Khi em dùng máy đã ghép, ứng dụng có thể đếm số phút, tên ứng dụng và tên miền như example.org. Bố mẹ và em cùng xem một báo cáo.</p><p>Nội dung em gõ, tin nhắn, ảnh màn hình và bài tập thuộc về em. Em có thể xin thêm giờ hoặc nhờ bố mẹ xem lại một trang.</p><p>Khi cần người giúp, em có thể gọi Tổng đài bảo vệ trẻ em <strong>111</strong>.</p></div>`);
  if(!parent()) return html;
  if(state.child) html+=card("Xóa toàn bộ dữ liệu của con tôi",`<p>Hồ sơ đã chọn, tài khoản của trẻ, quy tắc và báo cáo sẽ bị xóa. Máy đang ngoại tuyến sẽ nhận yêu cầu xóa khi liên lạc lại.</p><form id="erase" class="subform"><label class="check"><input type="checkbox" required>Tôi đã kiểm tra hồ sơ đang chọn và hiểu rằng không thể hoàn tác.</label><button class="danger">Xóa toàn bộ dữ liệu của con tôi</button></form>`);
  const receipts=await api("/api/erasures");
  return html+card("Xác nhận xóa trên thiết bị",list(receipts.map(r=>`Máy ${esc(r.device_id.slice(0,8))} · ${date(r.created_at)} · ${r.acknowledged?"Đã xác nhận xóa":"Đang chờ thiết bị"}`),"Chưa có yêu cầu xóa."));
}
function accountPage() {
  return `<div class="grid">${card("Tạo hồ sơ cho trẻ",`<form id="new-child"><label>Biệt danh<input name="name" autocomplete="off" maxlength="48" required></label><button>Tạo hồ sơ</button></form>`)}${state.child?card("Tạo tài khoản riêng cho trẻ",`<form id="child-account"><p>Tài khoản gắn với hồ sơ đang chọn.</p><label>Tên đăng nhập<input name="username" required autocomplete="off"></label><label>Mật khẩu từ 10 ký tự<input name="password" minlength="10" type="password" required autocomplete="new-password"></label><button>Tạo tài khoản của trẻ</button></form>`):""}${card("Đổi mật khẩu phụ huynh",`<form id="password"><label>Mật khẩu hiện tại<input name="current_password" type="password" autocomplete="current-password" required></label><label>Mật khẩu mới<input name="new_password" type="password" minlength="10" autocomplete="new-password" required></label><button>Đổi mật khẩu</button></form>`)}</div>`;
}
document.addEventListener("change",async e=>{
  if(e.target.id==="child"){state.child=Number(e.target.value);state.offset=0;setNotice("");await render();}
  if(e.target.id==="schedule-day"){state.day=Number(e.target.value);document.querySelector("#slots").innerHTML=slots(state.day,state.policy.schedule);}
});
document.addEventListener("click",async e=>{
  const b=e.target.closest("button");if(!b)return;
  if(b.dataset.tab){
    const route=childRoute();
    if (!matchesChildRoute(state.user,route)) {refuseChildRoute();return;}
    state.tab=b.dataset.tab;state.offset=0;setNotice("");
    if(route) history.replaceState(null,"",`#child-${state.tab==="overview"?"report":state.tab}/${route.id}`);
    else if(location.hash==="#requests" && state.tab!=="requests")
      history.replaceState(null,"",location.pathname+location.search);
    await render();return;
  }
  if(b.dataset.slot!==undefined && parent()){
    const i=Number(b.dataset.slot),day=state.policy.schedule[state.day],v=day[i]==="1"?"0":"1";
    state.policy.schedule[state.day]=day.slice(0,i)+v+day.slice(i+1);b.setAttribute("aria-pressed",String(v==="1"));return;
  }
  const action=b.dataset.act;if(!action)return;
  b.disabled=true;
  try{
    if(action==="next"||action==="prev"){state.offset=Math.max(0,state.offset+(action==="next"?50:-50));await render();return;}
    if(action==="logout"){await api("/api/auth/logout","POST");loginScreen();sessionChanges?.postMessage("changed");return;}
    if(action==="account"){state.tab="account";await render();return;}
    if(action==="retry"){await render();return;}
    const id=b.closest("[data-device]")?.dataset.device;
    if(!id)return;
    if(action==="revoke"){
      if(!confirm("Thu hồi quyền liên lạc của thiết bị này?")) return;
      await api(childURL(`/devices/${id}`),"DELETE");await render();setNotice("Đã thu hồi thiết bị.");
    } else {
      if(action==="lock"&&!confirm("Gửi yêu cầu khóa tới thiết bị này?"))return;
      await api(childURL(`/devices/${id}/commands`),"POST",{action});setNotice("Đã gửi yêu cầu. Lệnh hết hạn sau 2 phút.");
    }
  }catch(error){if(b.isConnected && error.name!=="AbortError")setNotice(error.message);}finally{b.disabled=false;}
});
document.addEventListener("submit",async e=>{
  e.preventDefault();const form=e.target,submitter=e.submitter,data=new FormData(form),fields=Object.fromEntries(data);
  if(submitter) submitter.disabled=true;
  try {
    setNotice("");
    switch(form.id){
      case "login":{
        state.csrf=(await api("/api/auth/csrf")).csrf_token;
        await api("/api/auth/login","POST",fields);
        sessionChanges?.postMessage("changed");await openSession(await api("/api/auth/me"));return;}
      case "new-child":{
        const c=await api("/api/children","POST",fields);await loadChildren(c.id);break;}
      case "password":
        await api("/api/auth/password","POST",fields);loginScreen();sessionChanges?.postMessage("changed");setNotice("Đã đổi mật khẩu. Hãy đăng nhập lại.");return;
      case "child-account": await api(childURL("/account"),"POST",fields);break;
      case "policy":{
        const policy={...state.policy,weekday_minutes:Number(fields.weekday_minutes),weekend_minutes:Number(fields.weekend_minutes),
          safe_search:data.has("safe_search"),blocked_categories:data.getAll("category")};
        for(const k of ["allowed_domains","blocked_domains","allowed_apps","blocked_apps"]){
          const lines=fields[k].split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
          policy[k]=k.endsWith("apps")?lines.map(line=>{
            const parts=line.match(/^(.+)\s+([a-f0-9]{64})$/i);
            if(!parts) throw new Error("Quy tắc ứng dụng cần tên .exe và mã SHA-256 đủ 64 ký tự.");
            return {name:parts[1].trim(),sha256:parts[2].toLowerCase()};
          }):lines;
        }
        await api(childURL("/policy"),"PUT",policy);break;}
      case "pair":{
        const code=await api(childURL("/pairing"),"POST",{consent:true,child_informed:true,child_agrees:true});
        if(form.isConnected) form.querySelector("#code").innerHTML=`<strong class="code">${esc(code.code)}</strong><p>Hiệu lực đến ${date(code.expires_at)}. Nhập mã trên máy cần ghép.</p>`;return;}
      case "ask-time":await api(childURL("/requests"),"POST",{kind:"time"});break;
      case "ask-domain":await api(childURL("/requests"),"POST",{kind:"domain",domain:fields.domain});break;
      case "erase":{
        if(!confirm("Xóa vĩnh viễn hồ sơ đã chọn và yêu cầu tất cả thiết bị xóa bộ đệm?"))return;
        await api(childURL(""),"DELETE",{confirm:true,child_id:state.child});sessionChanges?.postMessage("changed");await loadChildren();break;}
      default:
        if(!form.dataset.decision)return;
        await api(childURL(`/requests/${form.dataset.decision}`),"POST",{...fields,device_id:fields.device_id||null,status:submitter.value});
    }
    if(form.isConnected){await render();setNotice("Đã ghi nhận thay đổi.");}
  }catch(error){if(form.isConnected && error.name!=="AbortError")setNotice(error.message);}finally{if(submitter)submitter.disabled=false;}
});
(async()=>{try{await openSession(await api("/api/auth/me"));}catch(error){if(error.name!=="AbortError")loginScreen();}})();
