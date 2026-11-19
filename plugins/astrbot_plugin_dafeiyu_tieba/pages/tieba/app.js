const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);
let pollTimer = null;
let lastThread = null;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

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

function timeout(p, ms) {
  return Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error("请求超时，刷新页面再试")), ms))]);
}
const get = async (ep, params = {}) => unwrap(await timeout(bridge.apiGet(ep, params), 30000));
const post = async (ep, body = {}) => unwrap(await timeout(bridge.apiPost(ep, body), 30000));

async function loadStatus() {
  try {
    const s = await get("page/status");
    const dot = $("accountDot");
    if (!s.lib) {
      $("accountName").textContent = "依赖没装好";
      $("accountMeta").textContent = "aiotieba 缺失，看不了贴吧";
      dot.className = "dot";
    } else if (s.logged_in) {
      $("accountName").textContent = s.user || "已登录";
      $("accountMeta").textContent = "已登录 · 来自" + (s.source || "未知");
      dot.className = "dot ok";
    } else if (s.has_cookie) {
      $("accountName").textContent = "登录信息失效";
      $("accountMeta").textContent = "可能过期了，重新扫码登录";
      dot.className = "dot warn";
    } else {
      $("accountName").textContent = "未登录";
      $("accountMeta").textContent = "匿名也能看公开内容";
      dot.className = "dot";
    }
    $("logoutBtn").hidden = !(s.has_cookie && s.source === "本插件");
    $("loginBtn").disabled = !s.login_supported;
    if (!s.login_supported) $("accountMeta").textContent += "（扫码登录要先装链接解析插件）";
  } catch (e) {
    notice(e.message, "error");
  }
}

function showQr(d) {
  $("qrBox").hidden = false;
  if (d.qr) $("qrImg").src = d.qr;
  $("qrMsg").textContent = d.message || "";
  $("qrLeft").textContent = d.remaining ? `二维码还剩 ${d.remaining} 秒` : "";
}

function stopPoll() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

async function pollLogin() {
  try {
    const d = await get("page/login/poll");
    if (d.state === "waiting" || d.state === "scanned") {
      showQr(d);
      return;
    }
    stopPoll();
    $("qrBox").hidden = true;
    $("loginBtn").disabled = false;
    if (d.state === "success") notice(d.message || "登录成功", "ok");
    else if (d.state === "failed" || d.state === "expired") notice(d.message || "登录没完成", "error");
    loadStatus();
  } catch (e) {
    stopPoll();
    notice(e.message, "error");
  }
}

async function startLogin() {
  notice("");
  $("loginBtn").disabled = true;
  try {
    const d = await post("page/login/start");
    showQr(d);
    stopPoll();
    pollTimer = setInterval(pollLogin, 2000);
  } catch (e) {
    $("loginBtn").disabled = false;
    notice("生成二维码失败：" + e.message, "error");
  }
}

function busy(text = "加载中…") {
  $("results").innerHTML = `<div class="empty">${esc(text)}</div>`;
}

function pager(page, total, cb) {
  if (!total || total <= 1) return "";
  setTimeout(() => {
    const p = $("prevPage"), n = $("nextPage");
    if (p) p.onclick = () => cb(page - 1);
    if (n) n.onclick = () => cb(page + 1);
  });
  return `<div class="pager"><button id="prevPage" class="ghost small" ${page <= 1 ? "disabled" : ""}>上一页</button>
    <span class="muted">${page} / ${total}</span>
    <button id="nextPage" class="ghost small" ${page >= total ? "disabled" : ""}>下一页</button></div>`;
}

function bindItems() {
  document.querySelectorAll(".item[data-tid]").forEach((el) => (el.onclick = () => openThread(el.dataset.tid, 1)));
}

async function openForum(page = 1) {
  const name = $("forumName").value.trim();
  if (!name) return;
  busy();
  try {
    const d = await get("page/forum", { name, sort: $("forumSort").value, page });
    $("results").innerHTML =
      `<div class="muted">「${esc(d.forum)}吧」第 ${d.page} 页</div>` +
      (d.items.length
        ? d.items.map((t) => `<div class="item" data-tid="${t.tid}">
            <div class="title">${t.top ? '<span class="tag">置顶</span>' : ""}${t.good ? '<span class="tag">精</span>' : ""}${esc(t.title)}</div>
            <div class="meta">${esc(t.author)} · 回复 ${t.replies} · 最后回复 ${esc(t.last)}</div>
            ${t.text ? `<div class="text">${esc(t.text)}</div>` : ""}</div>`).join("")
        : '<div class="empty">这一页没有帖子</div>') +
      pager(d.page, d.total_page, openForum);
    bindItems();
  } catch (e) {
    busy(e.message);
  }
  loadHistory();
}

async function openThread(tid, page = 1) {
  if (!tid) return;
  lastThread = tid;
  busy();
  try {
    const d = await get("page/thread", { tid, page });
    $("results").innerHTML =
      `<div class="title" style="font-weight:600;font-size:15px">【${esc(d.forum)}吧】${esc(d.title || d.tid)}</div>
       <div class="muted">帖子号 ${d.tid} · 第 ${d.page} / ${d.total_page || "?"} 页</div>` +
      d.posts.map((p) => `<div class="post">
          <div><span class="who">#${p.floor} ${esc(p.author)}</span>${p.op ? '<span class="op">楼主</span>' : ""}
            <span class="muted"> · ${esc(p.time)} · 赞 ${p.agree}</span></div>
          <div class="body">${esc(p.text)} <span class="muted">${esc(p.extras)}</span></div>
          ${p.comments.map((c) => `<div class="cm">↳ ${esc(c.author)}：${esc(c.text)}</div>`).join("")}
          ${p.replies > p.comments.length ? `<div class="cm">（共 ${p.replies} 条楼中楼）</div>` : ""}
        </div>`).join("") +
      pager(d.page, d.total_page, (pg) => openThread(lastThread, pg));
  } catch (e) {
    busy(e.message);
  }
  loadHistory();
}

async function search() {
  const q = $("searchQ").value.trim();
  if (!q) return;
  busy();
  try {
    const d = await get("page/search", { q, forum: $("searchForum").value.trim() });
    $("results").innerHTML =
      `<div class="muted">搜索「${esc(d.query)}」${d.forum ? "（" + esc(d.forum) + "吧内）" : ""}</div>` +
      (d.items.length
        ? d.items.map((r) => `<div class="item" data-tid="${r.tid}">
            <div class="title">${esc(r.title)}</div>
            <div class="meta">【${esc(r.forum)}吧】 · ${esc(r.time)}</div>
            <div class="text">${esc(r.text)}</div></div>`).join("")
        : '<div class="empty">没搜到</div>');
    bindItems();
  } catch (e) {
    busy(e.message);
  }
  loadHistory();
}

async function loadHistory() {
  try {
    const d = await get("page/history");
    $("history").innerHTML = d.items.length
      ? d.items.map((i) => `<li><span class="t">${esc(i.time)}</span><span class="k">${esc(i.kind)}</span>${esc(i.target)}
          ${i.who && i.who !== "她" ? `<span class="muted">（${esc(i.who)}）</span>` : ""}</li>`).join("")
      : '<li class="empty">还没逛过</li>';
  } catch (e) {
    $("history").innerHTML = `<li class="empty">${esc(e.message)}</li>`;
  }
}

let selfTimer = null;

async function loadSelf() {
  try {
    const d = await get("page/proactive");
    const dot = $("selfDot");
    if (d.running) {
      $("selfState").textContent = "正在逛…";
      $("selfMeta").textContent = d.stage || "";
      dot.className = "dot run";
    } else {
      $("selfState").textContent = d.enabled ? "开着" : "关着";
      $("selfMeta").textContent = d.enabled
        ? `每天 ${d.times_per_day} 次 · ${d.hours} 点之间` + (d.share ? ` · 每天最多分享 ${d.share_per_day} 次${d.owner_set ? "" : "（没设主人，分享不了）"}` : " · 不分享")
        : "只在别人叫她时才去看";
      dot.className = "dot" + (d.enabled ? " ok" : "");
    }
    if (d.last_error && !d.running) notice("上次自己逛没成：" + d.last_error, "error");
    $("runBtn").disabled = d.running;
    $("toggleBtn").textContent = d.enabled ? "关掉" : "打开";
    $("toggleBtn").dataset.on = d.enabled ? "1" : "";
    $("selfPlan").textContent = d.enabled && d.slots.length
      ? "今天的计划：" + d.slots.map((s) => (d.done.includes(s) ? `${s}✓` : s)).join("、") + `；今天已分享 ${d.shared_today} 次`
      : "";
    $("selfForums").textContent = "感兴趣的吧：" + d.forums.join("、");
    $("diary").innerHTML = d.diary.length
      ? d.diary.map((i) => `<li data-tid="${i.tid}"><span class="t">${esc(i.time)}</span><span class="k">${esc(i.forum)}吧</span>${esc(i.title)}${i.shared ? '<span class="sh">已分享</span>' : ""}
          <span class="th">「${esc(i.thought)}」</span></li>`).join("")
      : '<li class="empty">还没自己逛过。点上面的按钮让她去一次试试。</li>';
    document.querySelectorAll("#diary li[data-tid]").forEach((el) => (el.onclick = () => openThread(el.dataset.tid, 1)));
    if (d.running && !selfTimer) selfTimer = setInterval(loadSelf, 3000);
    if (!d.running && selfTimer) { clearInterval(selfTimer); selfTimer = null; loadHistory(); }
  } catch (e) {
    $("selfState").textContent = "读不到";
    $("selfMeta").textContent = e.message;
  }
}

async function main() {
  try {
    await timeout(bridge.ready(), 8000);
  } catch (e) {
    notice("初始化失败，请刷新页面：" + e.message, "error");
    return;
  }
  $("loginBtn").onclick = startLogin;
  $("cancelBtn").onclick = async () => {
    stopPoll();
    try { await post("page/login/cancel"); } catch (_) {}
    $("qrBox").hidden = true;
    $("loginBtn").disabled = false;
  };
  $("logoutBtn").onclick = async () => {
    if (!confirm("退出本插件的贴吧登录？（会删掉服务器上存的登录信息）")) return;
    try { await post("page/logout"); notice("已退出", "ok"); } catch (e) { notice(e.message, "error"); }
    loadStatus();
  };
  $("refreshAll").onclick = () => { loadStatus(); loadHistory(); loadSelf(); };
  $("runBtn").onclick = async () => {
    notice("");
    try {
      const r = await post("page/proactive/run");
      notice(r.message, r.started ? "ok" : "");
    } catch (e) { notice(e.message, "error"); }
    loadSelf();
  };
  $("toggleBtn").onclick = async () => {
    try { await post("page/proactive/toggle", { enabled: !$("toggleBtn").dataset.on }); } catch (e) { notice(e.message, "error"); }
    loadSelf();
  };
  $("forumForm").onsubmit = (e) => { e.preventDefault(); openForum(1); };
  $("searchForm").onsubmit = (e) => { e.preventDefault(); search(); };
  $("threadForm").onsubmit = (e) => { e.preventDefault(); openThread($("threadId").value.trim(), 1); };
  await loadStatus();
  loadHistory();
  loadSelf();
  try {
    const d = await get("page/login/poll");
    if (d.state === "waiting" || d.state === "scanned") {
      showQr(d);
      $("loginBtn").disabled = true;
      pollTimer = setInterval(pollLogin, 2000);
    }
  } catch (_) {}
}

main();
