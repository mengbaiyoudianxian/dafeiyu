const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function notice(msg, tone = "") {
  const el = $("notice");
  el.hidden = !msg;
  el.textContent = msg || "";
  el.className = "notice" + (tone ? " " + tone : "");
}
function unwrap(r) {
  if (r && typeof r === "object") {
    if (r.status === "error") throw new Error(r.message || "请求失败");
    if ("ok" in r) {
      if (!r.ok) throw new Error(r.error?.message || r.message || "请求失败");
      return r.data || {};
    }
  }
  return r || {};
}
const timeout = (p, ms) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error("请求超时")), ms))]);
const get = async (ep, params = {}) => unwrap(await timeout(bridge.apiGet(ep, params), 60000));
const post = async (ep, body = {}) => unwrap(await timeout(bridge.apiPost(ep, body), 300000));

let enabled = true;
let tab = "home";
let qqTimer = null;
let musicPoll = null;

// ---------- 标签 ----------
function showTab(name) {
  tab = name;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll("section.tab").forEach((s) => (s.hidden = s.dataset.tab !== name));
  try { localStorage.setItem("dfy_tab", name); } catch (e) {}
  refresh();
}

// ---------- QQ 登录 ----------
async function loadQQ() {
  try {
    const d = await get("page/qq");
    $("qqDot").className = "dot" + (d.online ? " ok" : " warn");
    $("qqState").textContent = d.online ? `在线：${d.nick || ""}` : "没登录 / 掉线了";
    $("qqMeta").textContent = d.online ? `QQ ${d.uin}` : "扫下面的码登录";
    $("qqQr").hidden = d.online || !d.qr;
    if (!d.online && d.qr) {
      $("qqQrImg").src = d.qr;
      const left = 120 - (d.qr_age || 0);
      $("qqQrAge").textContent = left > 0 ? `这张码还剩约 ${left} 秒` : "这张码可能过期了，点「换一张码」";
    }
    $("qqSvc").textContent = `NapCat：${d.napcat || "?"} · QQ 走 19224 代理：${d.proxy || "?"}`;
    clearTimeout(qqTimer);
    if (!d.online && tab === "home") qqTimer = setTimeout(loadQQ, 5000);
  } catch (e) {
    $("qqState").textContent = "读不到状态";
    $("qqMeta").textContent = e.message;
  }
}
async function refreshQQ() {
  $("qqRefresh").disabled = true;
  notice("正在重启 QQ，二十几秒后出新码…");
  try { await post("page/qq/refresh"); } catch (e) { notice(e.message, "error"); }
  setTimeout(() => { $("qqRefresh").disabled = false; notice(""); loadQQ(); }, 25000);
}

// ---------- 音乐 ----------
const ST = { valid: ["ok", "已登录"], invalid: ["warn", "登录失效"], not_logged_in: ["", "没登录"], unknown: ["warn", "查不到"] };
const PN = { kugou: "酷狗", netease: "网易云", qq: "QQ 音乐" };
const loginPoll = {}; // 每个平台各自的轮询，互不打扰
let musicData = null;
let order = ["kugou", "netease", "qq"];

async function loadMusic() {
  try {
    const d = (musicData = await get("page/music"));
    const ok = d.accounts.filter((a) => a.status === "valid").map((a) => a.name);
    $("musicBrief").innerHTML = `<div class="account"><span class="dot ${ok.length ? "ok" : "warn"}"></span><div>
      <strong>${ok.length ? "已登录：" + esc(ok.join("、")) : "还没登录音乐账号"}</strong>
      <span>今天放了 ${d.sing_today} 首 · 别人说「唱首 XX」她就整首发语音</span></div></div>`;
    renderAccounts(d.accounts);
    $("singToday").textContent = `今天放了 ${d.sing_today} / ${d.settings.daily_limit} 首`;
    order = d.settings.platform_order.split(",").filter((p) => PN[p]);
    for (const p of Object.keys(PN)) if (!order.includes(p)) order.push(p);
    renderOrder();
    $("maxSec").value = d.settings.max_seconds;
    $("dayLimit").value = d.settings.daily_limit;
    $("plays").innerHTML = d.recent.length
      ? d.recent.map((r) => `<li class="${r.ok ? "" : "bad"}"><span class="t">${esc(r.time)}</span>
          <span class="k">${esc(PN[r.platform] || r.platform || "")}</span>${esc(r.title)}${r.artist ? " - " + esc(r.artist) : ""}
          <span class="th" style="display:block">${r.ok ? `${Math.floor(r.seconds / 60)}分${r.seconds % 60}秒` : "没放成：" + esc(r.error)}
          · ${esc(r.where || "")}${r.who ? "（" + esc(r.who) + "）" : ""}</span></li>`).join("")
      : '<li class="empty">还没放过歌。</li>';
  } catch (e) {
    $("musicBrief").innerHTML = `<p class="muted">${esc(e.message)}</p>`;
    $("musicAccounts").innerHTML = `<p class="muted">${esc(e.message)}</p>`;
  }
}

function renderAccounts(accounts) {
  // 正在扫码的平台不重画，免得二维码被刷掉
  const box = $("musicAccounts");
  if (!box.querySelector(".acct")) box.innerHTML = "";
  for (const a of accounts) {
    let el = box.querySelector(`.acct[data-p="${a.id}"]`);
    if (!el) {
      el = document.createElement("div");
      el.className = "acct";
      el.dataset.p = a.id;
      el.innerHTML = `<div class="top"><span class="dot"></span><div><strong></strong><span class="m"></span></div>
        <button class="ghost small login" type="button"></button><button class="ghost small out" type="button">退出</button></div>
        <div class="qr" hidden><img alt="${esc(a.name)}登录二维码" /><p class="muted msg"></p></div>`;
      el.querySelector(".login").onclick = () => musicLogin(a.id);
      el.querySelector(".out").onclick = () => musicLogout(a.id);
      box.appendChild(el);
    }
    const [tone, label] = ST[a.status] || ["", a.status];
    el.querySelector(".dot").className = "dot " + tone;
    el.querySelector("strong").textContent = `${a.name}：${label}`;
    el.querySelector(".m").textContent = a.status === "valid" ? "" : a.message || "";
    el.querySelector(".login").textContent = a.status === "valid" ? "重新登录" : "扫码登录";
    el.querySelector(".out").hidden = a.status !== "valid";
  }
}

function acctEl(p) { return document.querySelector(`#musicAccounts .acct[data-p="${p}"]`); }

async function musicLogin(p) {
  const el = acctEl(p);
  clearTimeout(loginPoll[p]);
  const btn = el.querySelector(".login");
  btn.disabled = true;
  el.querySelector(".qr").hidden = false;
  el.querySelector("img").removeAttribute("src");
  el.querySelector(".msg").textContent = "正在要二维码…（第一次可能要十几秒）";
  try {
    showLogin(p, await post("page/music/login", { provider: p }));
  } catch (e) {
    el.querySelector(".msg").textContent = "要二维码失败：" + e.message;
  }
  btn.disabled = false;
}

function showLogin(p, r) {
  const el = acctEl(p);
  if (r.qr) el.querySelector("img").src = r.qr;
  el.querySelector(".msg").textContent = r.message || r.status;
  if (r.status === "success") {
    el.querySelector(".qr").hidden = true;
    notice(`${PN[p]}登录好了`, "ok");
    loadMusic();
    return;
  }
  if (["waiting", "scanned", "pending", "confirming", "running"].includes(r.status)) {
    loginPoll[p] = setTimeout(async () => {
      try { showLogin(p, await get("page/music/login_status", { provider: p })); }
      catch (e) { el.querySelector(".msg").textContent = e.message; }
    }, 3000);
  }
}

async function musicLogout(p) {
  const b = acctEl(p).querySelector(".out");
  if (b.dataset.sure !== "1") {  // 插件页在沙箱里，弹不了确认框：点两次才退出
    b.dataset.sure = "1";
    b.textContent = "再点一次确认退出";
    setTimeout(() => { b.dataset.sure = ""; b.textContent = "退出"; }, 4000);
    return;
  }
  b.dataset.sure = "";
  try { await post("page/music/logout", { provider: p }); notice(`${PN[p]}已退出`, "ok"); } catch (e) { notice(e.message, "error"); }
  loadMusic();
}

function renderOrder() {
  $("orderBox").innerHTML = order.map((p, i) => `<span class="pill">${i + 1}. ${PN[p]}
    <button class="ghost" data-i="${i}" data-d="-1" ${i === 0 ? "disabled" : ""}>↑</button>
    <button class="ghost" data-i="${i}" data-d="1" ${i === order.length - 1 ? "disabled" : ""}>↓</button></span>`).join("");
  document.querySelectorAll("#orderBox button").forEach((b) => (b.onclick = () => {
    const i = +b.dataset.i, j = i + +b.dataset.d;
    [order[i], order[j]] = [order[j], order[i]];
    renderOrder();
  }));
}

async function saveMusic() {
  try {
    await post("page/music/settings", { platform_order: order.join(","), max_seconds: +$("maxSec").value || 0, daily_limit: +$("dayLimit").value || 30 });
    notice("放歌设置保存了", "ok");
    loadMusic();
  } catch (e) { notice(e.message, "error"); }
}

let lastResults = { platform: "", results: [] };
async function searchSong() {
  const q = $("songQ").value.trim();
  if (!q) return;
  $("songResults").innerHTML = '<p class="muted">搜索中…</p>';
  try {
    lastResults = await get("page/music/search", { q, platform: $("songP").value });
    const dur = (s) => (s ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` : "");
    $("songResults").innerHTML = lastResults.results.length
      ? `<p class="muted">在${PN[lastResults.platform]}找到：</p>` + lastResults.results.map((r, i) => `<div class="song"><div>
          <div class="title">${esc(r.title)}</div><div class="meta">${esc(r.artist)} · ${esc(r.album)} ${dur(r.duration)}</div></div>
          <button class="ghost small" data-i="${i}">放到我私聊</button></div>`).join("")
      : '<p class="empty">没搜到</p>';
    document.querySelectorAll("#songResults button").forEach((b) => (b.onclick = () => playSong(+b.dataset.i, b)));
  } catch (e) {
    $("songResults").innerHTML = `<p class="muted">${esc(e.message)}</p>`;
  }
}

async function playSong(i, btn) {
  const pick = lastResults.results[i];
  btn.disabled = true;
  btn.textContent = "下载发送中…";
  try {
    const r = await post("page/music/play", { pick, platform: lastResults.platform, song: pick.title });
    if (r.sent) notice(`发到你私聊了：《${r.title}》${r.artist}，${Math.floor(r.seconds / 60)}分${r.seconds % 60}秒` + (r.maybe_preview ? "（只有试听片段，可能账号没有这首的权限）" : ""), "ok");
    else notice(r.reason || "没放成", "error");
  } catch (e) { notice(e.message, "error"); }
  btn.disabled = false;
  btn.textContent = "放到我私聊";
  loadMusic();
}

// ---------- 念头 ----------
async function loadMind() {
  try {
    const d = await get("page/status");
    enabled = d.enabled;
    $("dot").className = "dot" + (d.running ? " run" : d.enabled ? " ok" : "");
    $("state").textContent = d.running ? "正在想…" : d.enabled ? (d.quiet ? "在睡觉（免打扰时段）" : "念头开着") : "念头关着";
    $("meta").textContent = d.enabled
      ? `每 ${d.interval[0]}~${d.interval[1]} 分钟冒一个念头 · 免打扰 ${d.quiet_hours} 点` + (d.next_at ? ` · 下一个大约 ${d.next_at}` : "")
      : "不会自己冒念头";
    $("toggle").textContent = d.enabled ? "关掉念头" : "打开念头";
    $("chips").innerHTML = ['<button class="primary small" data-k="">随便</button>']
      .concat(d.events.map((e) => `<button class="ghost small" data-k="${e.key}">${esc(e.label)}</button>`)).join("");
    document.querySelectorAll("#chips button").forEach((b) => (b.onclick = () => fire(b.dataset.k)));
    $("events").innerHTML = "<tr><th>念头</th><th>权重</th><th>今天</th><th>上限</th></tr>" +
      d.events.map((e) => `<tr><td>${esc(e.label)}</td><td>${e.weight}</td><td>${e.today}</td><td>${e.cap ?? "-"}</td></tr>`).join("");
    $("groups").innerHTML = d.groups.length
      ? "<tr><th>群</th><th>冒泡</th><th>有反馈</th><th>权重</th></tr>" +
        d.groups.map((g) => `<tr><td>${esc(g.name)}</td><td>${g.posts}</td><td>${g.replied}</td><td>${g.weight}</td></tr>`).join("")
      : '<tr><td class="muted">还没有群</td></tr>';
    $("log").innerHTML = d.log.length
      ? d.log.map((i) => `<li class="${i.ok ? "" : "bad"}"><span class="t">${esc(i.time)}</span><span class="k">${esc(i.label)}</span>${esc(i.target)}
          ${i.answered ? '<span class="sh" style="color:var(--ok);font-size:12px;margin-left:4px">有人回了</span>' : ""}
          <span class="th" style="display:block">${esc(i.text)}</span></li>`).join("")
      : '<li class="empty">还没冒过念头。点「随便」让她想一个试试。</li>';
  } catch (e) {
    notice(e.message, "error");
  }
}
async function fire(kind) {
  notice("她在想…（可能要几十秒）");
  document.querySelectorAll("#chips button").forEach((b) => (b.disabled = true));
  try {
    const r = await post("page/run", { kind });
    notice(r.ok ? `做了：${r.target ? r.target + " — " : ""}${r.text}` : `想了想，算了${r.text ? "：" + r.text : ""}`, r.ok ? "ok" : "");
  } catch (e) {
    notice(e.message, "error");
  }
  loadMind();
}

// ---------- 钱包 / 资料 ----------
async function loadMoney() {
  try {
    const b = await get("page/balance");
    $("money").innerHTML = b.value == null ? '<span class="muted">还没查到</span>'
      : `<strong style="font-size:22px">¥${Number(b.value).toFixed(2)}</strong>`;
    $("moneyMeta").textContent = (b.brief || "") +
      ` · 低于 ¥${b.low} 开始找你要，低于 ¥${b.critical} 每小时要一次` + (b.last_ask ? ` · 上次要钱 ${b.last_ask}` : "");
  } catch (e) {
    $("money").textContent = e.message;
  }
}
async function loadProfiles() {
  $("profiles").innerHTML = '<p class="muted">她在看…</p>';
  try {
    const d = await get("page/profiles");
    const row = (label, p, id, link) => p
      ? `<div class="p"><img src="${esc(p.face)}" referrerpolicy="no-referrer" alt=""><div><strong>${label}：${esc(p.name)}</strong>
          <div class="d">${esc(id)}${link ? ` · <a href="${esc(link)}" target="_blank">主页</a>` : ""}</div>
          <div class="d">签名：${esc(p.sign || "（空）")}</div>${p.face_desc ? `<div class="d">她看到的头像：${esc(p.face_desc)}</div>` : ""}</div></div>`
      : `<div class="p"><div><strong>${label}</strong><div class="d">没登录</div></div></div>`;
    $("profiles").innerHTML =
      row("QQ", d.qq, `QQ ${d.qq?.uin}`) +
      row("B站", d.bili, d.bili ? `UID ${d.bili.uid} · LV${d.bili.level}` : "", d.bili ? `https://space.bilibili.com/${d.bili.uid}` : "") +
      row("贴吧", d.tieba, d.tieba ? `用户名 ${d.tieba.user_name || "无"}` : "");
  } catch (e) {
    $("profiles").innerHTML = `<p class="muted">${esc(e.message)}</p>`;
  }
}

// ---------- 表情包 ----------
async function loadMemes() {
  try {
    const d = await get("page/memes");
    $("memeSum").textContent = `共 ${d.total} 张（其中 ${d.web} 张是她上网找来的）· 配图概率 ${d.probability ?? "?"}% · 今天上网找了 ${d.search_today} 次`;
    $("memeCats").innerHTML = "<tr><th>分类</th><th>张数</th><th>用途</th></tr>" +
      d.cats.map((c) => `<tr><td>${esc(c.cat)}</td><td>${c.n}${c.web ? ` <span class="muted">(+${c.web} 网上)</span>` : ""}</td><td class="muted">${esc(c.desc)}</td></tr>`).join("");
    $("memeRecent").innerHTML = d.recent.length
      ? d.recent.map((r) => `<li><span class="k">${esc(r.cat)}</span>${esc(r.text || "（没字）")}<span class="th" style="display:block">${esc(r.scene)}</span></li>`).join("")
      : '<li class="empty">还没上网找过。有人点名要某种表情包时她会去找。</li>';
  } catch (e) {
    $("memeSum").textContent = e.message;
  }
}

// ---------- 推特 ----------
async function loadX() {
  try {
    const d = await get("page/x");
    $("xDot").className = "dot" + (d.me ? " ok" : d.logged ? " warn" : "");
    $("xState").textContent = d.me ? (d.me.screen_name ? `已登录：${d.me.name}` : "已登录") : d.logged ? "Cookie 存了，但登录不上" : "还没登录";
    $("xMeta").textContent = d.me ? (d.me.screen_name ? `@${d.me.screen_name} · 关注 ${d.me.following}` : "推特没返回账号资料，不影响刷推") : d.error || "填下面两个 Cookie";
    $("xLogin").hidden = !!d.me;
    $("xClear").hidden = !d.logged;
    $("xEnable").checked = d.enabled;
    $("xComment").checked = d.comment_enable;
    $("xNsfw").checked = d.nsfw.enable;
    $("xNsfwMeta").textContent = `门槛：信任和好感都 ≥ ${d.nsfw.threshold}。你现在${d.nsfw.owner_ok ? "够" : "还不够"}；群里永远不发。`;
    const t = d.today || {};
    $("xToday").textContent = `今天：刷了 ${t.sessions || 0}/${d.limits.sessions} 轮 · 评论 ${t.comments || 0}/${d.limits.comments} · 发你 ${t.shares || 0}/${d.limits.shares}`
      + (d.running ? " · 正在刷…" : d.next_at ? ` · 下次大约 ${d.next_at} 想想要不要刷` : "");
    $("xItems").innerHTML = d.items.length
      ? d.items.map((i) => `<li><span class="t">${esc(i.time)}</span><span class="k">@${esc(i.who)}</span>${esc(i.gist)}
          ${i.nsfw ? '<span class="tag">色情</span>' : ""}${i.shared ? '<span class="tag">发过</span>' : ""}
          <span class="th" style="display:block">兴趣 ${i.interest}/10${i.media ? ` · ${i.media} 个图/视频` : ""}${i.commented ? " · 她回了：" + esc(i.commented) : ""}
          · <a href="${esc(i.url)}" target="_blank">原帖</a></span></li>`).join("")
      : '<li class="empty">还没刷过。</li>';
    $("xLog").innerHTML = d.log.length
      ? d.log.map((l) => `<li><span class="t">${esc(l.time)}</span><span class="k">${esc(l.kind)}</span>${esc(l.text)}
          ${l.url ? `<span class="th" style="display:block"><a href="${esc(l.url)}" target="_blank">原帖</a></span>` : ""}</li>`).join("")
      : '<li class="empty">还没动静。</li>';
  } catch (e) {
    $("xState").textContent = "读不到";
    $("xMeta").textContent = e.message;
  }
}
async function xSubmit(body, btn) {
  btn.disabled = true;
  notice("正在登录推特…（可能要半分钟）");
  try {
    const r = await post("page/x/cookies", body);
    notice(r.me ? (r.me.screen_name ? `登录好了：${r.me.name}（@${r.me.screen_name}）` : "登录好了") : r.warn || "存好了", r.me ? "ok" : "error");
    $("xPw").value = $("xPaste").value = "";
  } catch (e) { notice(e.message, "error"); }
  btn.disabled = false;
  loadX();
}
const saveX = () => xSubmit({ paste: $("xPaste").value }, $("xSave"));
const loginX = () => xSubmit({ user: $("xUser").value, password: $("xPw").value, extra: $("xExtra").value }, $("xLoginPw"));
let xWebTimer = null;
async function xWebLogin() {
  $("xWeb").disabled = true;
  notice("正在服务器上开浏览器…");
  try {
    const r = await post("page/x/browser");
    $("xWebBox").hidden = false;
    setTimeout(() => { $("xWebFrame").src = r.url; notice(""); }, 6000);
    $("xWebMsg").textContent = "浏览器开好后，在里面输入推特账号密码登录。登好了这里会自动变成「已登录」。";
    clearInterval(xWebTimer);
    xWebTimer = setInterval(async () => {
      try {
        const d = await get("page/x");
        if (d.login_state === "done" || d.me) {
          clearInterval(xWebTimer);
          $("xWebBox").hidden = true;
          $("xWebFrame").removeAttribute("src");
          notice(d.me ? (d.me.screen_name ? `推特登录好了：${d.me.name}（@${d.me.screen_name}）` : "推特登录好了") : "推特登录好了", "ok");
          loadX();
        } else if (d.login_state === "timeout") {
          clearInterval(xWebTimer);
          $("xWebMsg").textContent = "等了 20 分钟没登录，浏览器关了。要登录再点一次按钮。";
        }
      } catch (e) {}
    }, 4000);
  } catch (e) { notice(e.message, "error"); }
  $("xWeb").disabled = false;
}
function xLoginTab(which) {
  document.querySelectorAll("[data-xl]").forEach((b) => b.classList.toggle("on", b.dataset.xl === which));
  document.querySelectorAll("[data-xp]").forEach((d) => (d.hidden = d.dataset.xp !== which));
}
async function clearX() {
  const b = $("xClear");
  if (b.dataset.sure !== "1") { b.dataset.sure = "1"; b.textContent = "再点一次确认"; setTimeout(() => { b.dataset.sure = ""; b.textContent = "退出（删掉 Cookie）"; }, 4000); return; }
  try { await post("page/x/cookies", { clear: true }); notice("推特 Cookie 删掉了", "ok"); } catch (e) { notice(e.message, "error"); }
  loadX();
}
async function runX() {
  $("xRun").disabled = true;
  notice("她在刷推特…（一两分钟）");
  try {
    const r = await post("page/x/run");
    notice(`刷完了：看了 ${r.seen || 0} 条，留下 ${r.kept || 0} 条` + (r.comment ? `，评论了「${r.comment}」` : "") + (r.share ? `，分享：${r.share}` : ""), "ok");
  } catch (e) { notice(e.message, "error"); }
  $("xRun").disabled = false;
  loadX();
}
async function toggleX(key, el) {
  try { await post("page/x/toggle", { [key]: el.checked }); } catch (e) { notice(e.message, "error"); }
  loadX();
}

// ---------- 插件推荐 ----------
const RS = { pending: "等你批", installed: "已装", rejected: "没装" };
async function loadPlugins() {
  try {
    const d = await get("page/plugins");
    $("recs").innerHTML = d.recs.length
      ? d.recs.map((r) => `<div class="post"><div><span class="who">#${r.n} ${esc(r.name)}</span>
          <span class="tag">${RS[r.status] || r.status}</span>${r.market ? "" : '<span class="tag">不在官方市场</span>'}
          <span class="muted" style="float:right">${esc(r.time)}</span></div>
          <div class="body"><a href="https://github.com/${esc(r.repo)}" target="_blank">github.com/${esc(r.repo)}</a></div>
          <div class="body">${esc(r.why)}</div>
          ${r.status === "pending" ? `<div class="actions"><button class="primary small" data-n="${r.n}" data-a="install">装</button>
            <button class="ghost small" data-n="${r.n}" data-a="reject">不装</button></div>` : ""}</div>`).join("")
      : '<p class="empty">她还没推荐过插件。问她「有没有能 XX 的插件」她会去找。</p>';
    document.querySelectorAll("#recs button").forEach((b) => (b.onclick = () => actPlugin(+b.dataset.n, b.dataset.a)));
  } catch (e) {
    $("recs").innerHTML = `<p class="muted">${esc(e.message)}</p>`;
  }
}
async function actPlugin(n, action) {
  document.querySelectorAll("#recs button").forEach((b) => (b.disabled = true));
  notice(action === "install" ? `正在装 #${n}，可能要一两分钟…` : "");
  try {
    const r = await post("page/plugins/act", { n, action });
    notice(r.message, /没装上|找不到|没有/.test(r.message) ? "error" : "ok");
  } catch (e) {
    notice(e.message, "error");
  }
  loadPlugins();
}

// ---------- 总 ----------
function refresh() {
  if (tab === "home") { loadQQ(); loadMusic(); loadMind(); loadMoney(); }
  else if (tab === "mind") loadMind();
  else if (tab === "music") loadMusic();
  else if (tab === "x") loadX();
  else if (tab === "memes") loadMemes();
  else if (tab === "plugins") loadPlugins();
}

async function main() {
  try { await timeout(bridge.ready(), 8000); } catch (e) { notice("初始化失败，请刷新：" + e.message, "error"); return; }
  $("refresh").onclick = refresh;
  $("loadProfiles").onclick = loadProfiles;
  $("qqRefresh").onclick = refreshQQ;
  $("toggle").onclick = async () => {
    try { await post("page/toggle", { enabled: !enabled }); } catch (e) { notice(e.message, "error"); }
    loadMind();
  };
  document.querySelectorAll("#tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
  document.querySelectorAll("[data-goto]").forEach((b) => (b.onclick = () => showTab(b.dataset.goto)));
  $("songGo").onclick = searchSong;
  $("songQ").onkeydown = (e) => { if (e.key === "Enter") searchSong(); };
  $("saveMusic").onclick = saveMusic;
  $("xSave").onclick = saveX;
  $("xLoginPw").onclick = loginX;
  $("xWeb").onclick = xWebLogin;
  $("xPw").onkeydown = (e) => { if (e.key === "Enter") loginX(); };
  document.querySelectorAll("[data-xl]").forEach((b) => (b.onclick = () => xLoginTab(b.dataset.xl)));
  $("xClear").onclick = clearX;
  $("xRun").onclick = runX;
  $("xEnable").onchange = (e) => toggleX("enable", e.target);
  $("xComment").onchange = (e) => toggleX("comment_enable", e.target);
  $("xNsfw").onchange = (e) => toggleX("nsfw_share_enable", e.target);
  let saved = "home";
  try { saved = localStorage.getItem("dfy_tab") || "home"; } catch (e) {}
  showTab(saved);
}
main();
