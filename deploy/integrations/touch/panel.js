"use strict";
const el = id => document.getElementById(id);
const api = new URL("../fish-api/", document.baseURI);
let key = "";
let session = 0;

async function request(path, body) {
  const options = {cache: "no-store", referrerPolicy: "no-referrer"};
  if (body !== undefined) {
    options.method = "POST";
    options.headers = {"Content-Type": "application/json"};
    options.body = JSON.stringify(body);
  }
  const response = await fetch(new URL(path, api), options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) throw new Error(data.msg || "请求失败（" + response.status + "）");
  return data;
}

function addLine(author, value) {
  if (!value) return;
  const line = document.createElement("li");
  const who = document.createElement("strong");
  who.textContent = author + "　";
  line.append(who, value);
  el("history").append(line);
}

async function loadScene() {
  const data = await request("scenes");
  const items = data.items || [];
  const current = items.find(item => item.id === data.now) || items.at(-1);
  const image = el("scene");
  image.hidden = true;
  image.removeAttribute("src");
  el("scene-text").textContent = (current && current.hidden) ? "她正在忙自己的事。" :
    (data.doing || current?.doing || "还没有生成场景。");
  if (current && !current.hidden && /^[0-9_-]+$/.test(current.id)) {
    image.src = new URL("scene/" + current.id + ".jpg", api).href;
    image.hidden = false;
  }
}

function fillActions(actions) {
  const select = el("action");
  select.replaceChildren();
  const groups = new Map();
  for (const action of actions) {
    const category = action.c || "互动";
    if (!groups.has(category)) {
      const group = document.createElement("optgroup");
      group.label = category;
      groups.set(category, group);
      select.append(group);
    }
    groups.get(category).append(new Option(action.n || action.k, action.k));
  }
}

function onSubmit(id, handler) {
  el(id).addEventListener("submit", async event => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button");
    if (button.disabled) return;
    const current = session;
    button.disabled = true;
    el("notice").textContent = "";
    try {
      await handler(current);
    } catch (error) {
      if (session === current) el("notice").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
}

onSubmit("login", async current => {
  const supplied = el("key").value.trim();
  const member = await request("login", {key: supplied});
  const [history, actions] = await Promise.all([request("history", {key: supplied}), request("actions")]);
  if (current !== session) return;
  key = supplied;
  el("key").value = "";
  el("member").textContent = member.name || "已登录";
  el("history").replaceChildren();
  for (const line of history.items || []) addLine(line.w === "我" ? "大肥鱼" : "你", line.x);
  fillActions(actions);
  el("login").hidden = true;
  el("account").hidden = false;
});

el("logout").addEventListener("click", () => {
  session++;
  key = "";
  el("member").textContent = "";
  el("notice").textContent = "";
  el("message").value = "";
  el("history").replaceChildren();
  el("account").hidden = true;
  el("login").hidden = false;
});

onSubmit("touch", async current => {
  const result = await request("touch", {key, kind: el("action").value});
  if (session === current) el("notice").textContent = result.line || (result.accept ? "她回应了你的动作。" : "她躲开了。");
});
onSubmit("chat", async current => {
  const text = el("message").value.trim();
  if (!text) return;
  const result = await request("chat", {key, text});
  if (session !== current) return;
  addLine("你", text);
  addLine("大肥鱼", result.reply);
  if (el("message").value.trim() === text) el("message").value = "";
});

function refresh() {
  loadScene().catch(error => { el("scene-text").textContent = error.message; });
}
el("scene").addEventListener("error", () => { el("scene").hidden = true; });
el("refresh").addEventListener("click", refresh);
refresh();
