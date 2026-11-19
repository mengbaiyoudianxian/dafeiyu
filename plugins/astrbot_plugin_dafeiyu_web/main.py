"""大肥鱼上网：像人一样自己去搜、去看网页，看完再说。

- web_search(query)：Bing 搜索（搜不到退到搜狗），返回标题、链接、摘要。
- fetch_page(url, part)：打开网页，抽出标题、正文（去掉导航、脚本、广告位）和页面里的链接；
  正文长就分段，part=2、3… 接着往下读；她可以顺着链接一页页看下去，像在浏览器里翻。

安全：只让她看公网。localhost、内网、Tailscale 地址（100.64/10）、保留地址一律不开，跳转的每一步都查，
免得有人借她的手去读服务器后台。只用 GET，不填表、不登录、不下载文件。
"""

import asyncio
import base64
import ipaddress
import json
import re
import socket
import time
import urllib.parse

import aiohttp
from bs4 import BeautifulSoup

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
HDR = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
       "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
PART = 5000          # 每段正文多少字
MAX_BYTES = 4_000_000


def _clip(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


def _squash(s):
    return re.sub(r"\s+", " ", s or "").strip()


async def _public(url):
    """只放行公网的 http(s)。"""
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False, "只能打开 http/https 网址"
    host = p.hostname.lower()
    if host in ("localhost",) or host.endswith((".local", ".internal", ".ts.net", ".lan")):
        return False, "这是内网地址，不能开"
    if p.port and p.port not in (80, 443, 8080, 8443):
        return False, "这个端口不开"
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, p.port or (443 if p.scheme == "https" else 80))
    except Exception:
        return False, "域名解析不了，网址可能不对"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            return False, "这是内网地址，不能开"
    return True, ""


def _bing_real(href):
    """Bing 的跳转链接 /ck/a?...&u=a1<base64> 还原成真网址。"""
    if "bing.com/ck/a" in href:
        u = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("u", [""])[0]
        if u.startswith("a1"):
            b = u[2:]
            try:
                return base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)).decode("utf-8", "replace")
            except Exception:
                pass
    return href


class DafeiyuWeb(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None) -> None:
        super().__init__(context)
        self.conf = config or {}
        self.cache = {}  # url -> (时间, 解析结果)

    async def _get(self, sess, url, hops=5):
        for _ in range(hops):
            ok, why = await _public(url)
            if not ok:
                raise RuntimeError(why)
            async with sess.get(url, headers=HDR, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=25)) as r:
                if r.status in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                    url = urllib.parse.urljoin(url, r.headers["Location"])
                    continue
                ctype = r.headers.get("Content-Type", "")
                raw = b""
                async for chunk in r.content.iter_chunked(65536):
                    raw += chunk
                    if len(raw) > MAX_BYTES:
                        break
                return r.status, str(r.url), ctype, raw, r.charset
        raise RuntimeError("跳转太多次了")

    @staticmethod
    def _decode(raw, charset):
        for enc in [charset, re.search(rb'charset=["\']?([\w-]+)', raw[:3000]) and
                    re.search(rb'charset=["\']?([\w-]+)', raw[:3000]).group(1).decode(), "utf-8", "gb18030"]:
            if not enc:
                continue
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", "replace")

    # ---------- 搜索 ----------
    async def _bing(self, sess, q, n):
        url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": q, "count": n})
        _, _, _, raw, cs = await self._get(sess, url)
        soup = BeautifulSoup(self._decode(raw, cs), "lxml")
        out = []
        for li in soup.select("li.b_algo"):
            a = li.select_one("h2 a")
            if not a or not a.get("href"):
                continue
            snip = li.select_one(".b_caption p") or li.select_one("p") or li.select_one(".b_lineclamp2")
            out.append({"title": _squash(a.get_text()), "url": _bing_real(a["href"]),
                        "snippet": _clip(_squash(snip.get_text() if snip else ""), 220)})
        return out

    async def _sogou(self, sess, q, n):
        url = "https://www.sogou.com/web?" + urllib.parse.urlencode({"query": q})
        _, _, _, raw, cs = await self._get(sess, url)
        soup = BeautifulSoup(self._decode(raw, cs), "lxml")
        out = []
        for box in soup.select("div.vrwrap, div.rb"):
            a = box.select_one("h3 a")
            if not a or not a.get("href"):
                continue
            href = urllib.parse.urljoin("https://www.sogou.com", a["href"])
            snip = box.select_one(".space-txt, .str-text-info, .str_info, p")
            out.append({"title": _squash(a.get_text()), "url": href, "snippet": _clip(_squash(snip.get_text() if snip else ""), 220)})
        return out[:n]

    async def _grok_research(self, task):
        """让 Grok 用 xAI 官方的联网搜索（web_search + x_search）去查：它会自己多轮搜索、打开网页核对。"""
        # 2026-11-19 grok 欠费 402，改用 本机 Responses 接口自带的联网搜索；返回格式和 xAI 一样
        body = {"model": "gpt-6.2-sol", "input": task, "effort": "medium", "tools": [{"type": "web_search"}],
                "instructions": "你是一个认真的中文资料员：多搜几个来源交叉核对，必要时打开网页看原文，写清事实和出处。"}
        async with aiohttp.ClientSession() as sess:
            async with sess.post("http://127.0.0.1:18790/v1/codex/raw", json=body,
                                 timeout=aiohttp.ClientTimeout(total=3600)) as r:
                data = await r.json(content_type=None)
                if r.status >= 400:
                    raise RuntimeError(f"HTTP {r.status} {_clip(json.dumps(data, ensure_ascii=False), 120)}")
        text, cites, steps = [], [], []
        for o in data.get("output") or []:
            if o.get("type") == "message":
                for c in o.get("content") or []:
                    text.append(c.get("text") or "")
                    cites += [a.get("url") for a in c.get("annotations") or [] if a.get("url")]
            elif o.get("type") in ("web_search_call", "x_search_call"):
                act = o.get("action") or {}
                steps.append(f"{'搜' if act.get('type') == 'search' else '看'}：{act.get('query') or act.get('url') or ''}")
        return "\n".join(text).strip(), list(dict.fromkeys(cites)), steps

    @filter.llm_tool(name="web_search")
    async def web_search(self, event: AstrMessageEvent, query: str):
        '''上网查东西（也能查推特/X 上的动态）。别人问你不知道的、最新的、需要核实的事（新闻、价格、版本更新、某个人/作品/梗的来历、
        教程、怎么做某件事、某个说法真不真），或者你自己好奇，就用这个。它会自己多搜几轮、打开网页交叉核对，再把查到的告诉你，带出处链接。
        查完用你自己的口吻说重点，可以附一两个链接；查到的跟你以为的不一样，以查到的为准；真查不到就照实说。
        想细看某个链接的原文，再用 fetch_page 打开。

        Args:
            query(string): 想查什么，直接用一句话说清楚，比如「过桥米线的起源」「周杰伦 2026 年有没有新专辑」「Python 3.13 有什么新特性」
        '''
        q = (query or "").strip()[:300]
        if not q:
            return "没说要查什么。"
        started = time.time()
        task = (f"帮我上网查：{q}\n要求：多搜几个来源交叉核对，必要时打开网页看原文；把查到的具体事实、数字、时间、出处讲清楚，"
                f"说明哪些是确定的、哪些有争议或查不到；用中文回答，最后列出参考链接。")
        try:
            text, cites, steps = await self._grok_research(task)
            if text:
                logger.info(f"[dafeiyu_web] 查「{_clip(q, 40)}」：{len(steps)} 步，{len(cites)} 个出处，{int(time.time() - started)} 秒")
                return json.dumps({"ok": True, "findings": _clip(text, 3500), "sources": cites[:10], "how": steps[:12],
                                   "note": "这是你刚上网查到的，用自己的话说重点，别整段照搬，可以附一两个链接"}, ensure_ascii=False)
            err = "没拿到结果"
        except Exception as e:
            err = str(e)
            logger.warning(f"[dafeiyu_web] 联网查询失败，退回搜狗：{_clip(e, 120)}")
        try:  # 备用：搜狗搜索结果列表
            async with aiohttp.ClientSession() as sess:
                res = await self._sogou(sess, q[:60], 8)
        except Exception as e:
            res, err = [], f"{err}；搜狗也失败：{e}"
        if not res:
            return json.dumps({"ok": False, "reason": f"没查成（{_clip(err, 80)}），照实说现在查不了"}, ensure_ascii=False)
        return json.dumps({"ok": True, "results": res, "next": "这是搜索结果列表，挑靠谱的用 fetch_page 打开看正文再回答"},
                          ensure_ascii=False)

    # ---------- 打开网页 ----------
    def _parse(self, html, base):
        soup = BeautifulSoup(html, "lxml")
        for t in soup(["script", "style", "noscript", "svg", "iframe", "form", "button", "nav", "footer", "aside", "header"]):
            t.decompose()
        title = _squash(soup.title.get_text()) if soup.title else ""
        main = soup.select_one("article") or soup.select_one("main") or soup.select_one("#content, .content, #main, .post, .article") or soup.body or soup
        lines = []
        for el in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "td", "blockquote", "div"], recursive=True):
            if el.name == "div" and el.find(["p", "div", "li", "h1", "h2", "h3"]):
                continue
            t = _squash(el.get_text(" "))
            if len(t) < 2:
                continue
            if el.name in ("h1", "h2", "h3", "h4"):
                t = "## " + t
            elif el.name == "li":
                t = "- " + t
            if not lines or lines[-1] != t:
                lines.append(t)
        text = "\n".join(lines) or _squash(main.get_text(" "))
        links, seen = [], set()
        for a in main.find_all("a", href=True):
            href = urllib.parse.urljoin(base, a["href"])
            label = _squash(a.get_text())
            if not href.startswith("http") or href in seen or len(label) < 2:
                continue
            seen.add(href)
            links.append({"text": _clip(label, 40), "url": href})
            if len(links) >= 40:
                break
        return title, text, links

    @filter.llm_tool(name="fetch_page")
    async def fetch_page(self, event: AstrMessageEvent, url: str, part: int = 1):
        '''打开一个网页读内容：返回标题、正文和页面里的链接。正文很长会分段，part=2、3… 接着往下读。
        搜索结果、别人发的链接、页面里的链接都能开；想深入就顺着链接继续 fetch_page，像在浏览器里一页页翻。
        看完用自己的话说重点，别整段照抄。

        Args:
            url(string): 网址（http/https）
            part(number): 读第几段，默认 1
        '''
        url = (url or "").strip()
        if not re.match(r"^https?://", url):
            url = "https://" + url
        part = max(1, int(part or 1))
        try:
            hit = self.cache.get(url)
            if hit and time.time() - hit[0] < 900:
                final, title, text, links = hit[1]
            else:
                async with aiohttp.ClientSession() as sess:
                    status, final, ctype, raw, cs = await self._get(sess, url)
                    if "sogou.com/link" in final:  # 搜狗结果先过一道跳转页
                        m = re.search(rb'(?:replace|href)\(["\'](https?://[^"\']+)', raw) or re.search(rb'URL=\'?(https?://[^\'">]+)', raw)
                        if m:
                            status, final, ctype, raw, cs = await self._get(sess, m.group(1).decode())
                if status >= 400:
                    return json.dumps({"ok": False, "reason": f"网页打不开（HTTP {status}）"}, ensure_ascii=False)
                if not any(k in ctype for k in ("html", "xml", "text", "json")) and ctype:
                    return json.dumps({"ok": False, "reason": f"这不是网页（{ctype.split(';')[0]}），没法读"}, ensure_ascii=False)
                html = self._decode(raw, cs)
                if "json" in ctype:
                    title, text, links = "", html, []
                else:
                    title, text, links = await asyncio.to_thread(self._parse, html, final)
                self.cache[url] = (time.time(), (final, title, text, links))
                if len(self.cache) > 200:
                    self.cache.pop(next(iter(self.cache)))
        except Exception as e:
            logger.info(f"[dafeiyu_web] 打不开 {_clip(url, 60)}：{e}")
            return json.dumps({"ok": False, "reason": f"打不开：{_clip(e, 80)}"}, ensure_ascii=False)
        parts = max(1, -(-len(text) // PART))
        seg = text[(part - 1) * PART: part * PART]
        logger.info(f"[dafeiyu_web] 看了 {_clip(final, 60)} 第 {part}/{parts} 段")
        out = {"ok": True, "url": final, "title": title, "part": f"{min(part, parts)}/{parts}", "text": seg or "（这一段没有内容了）"}
        if part == 1:
            out["links"] = links
        if part < parts:
            out["more"] = f"还有后面的，part={part + 1} 接着读"
        if len(text) < 200:
            out["note"] = "正文很少，可能是要登录、要跑脚本才显示的页面；换个结果看看"
        return json.dumps(out, ensure_ascii=False)
