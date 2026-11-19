"""大肥鱼找插件：她能搜 AstrBot 插件市场和 GitHub、看说明，觉得有用就推荐给主人；只有主人私聊回「/装插件 编号」才真的装。

她自己没有任何「安装」工具，群里谁说什么都装不上。装插件 = 在服务器上跑别人写的代码，所以每一个都要主人点头。
"""

import base64
import json
import os
import re
import time
import urllib.parse

import aiohttp

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

DATA = get_astrbot_data_path()
MARKET = "https://api.soulter.top/astrbot/plugins"
UA = {"User-Agent": "dafeiyu-pluginscout", "Accept": "application/vnd.github+json"}


def _clip(s, n):
    s = str(s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _repo_key(s):
    """各种写法统一成 owner/name。"""
    s = str(s or "").strip()
    m = re.search(r"github\.com[/:]([^/\s]+)/([^/\s#?]+)", s)
    if m:
        return f"{m.group(1)}/{m.group(2).removesuffix('.git')}"
    m = re.fullmatch(r"([\w.-]+)/([\w.-]+)", s)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


class DafeiyuPluginScout(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None) -> None:
        super().__init__(context)
        self.conf = config or {}
        self.dir = os.path.join(DATA, "plugin_data", "astrbot_plugin_dafeiyu_pluginscout")
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, "state.json")
        try:
            self.state = json.load(open(self.path, encoding="utf-8"))
        except Exception:
            self.state = {}
        self.state.setdefault("recs", [])  # [{n, repo, name, why, t, status}]
        self.state.setdefault("next", 1)
        self._market = {"t": 0, "data": {}}

    def _save(self):
        json.dump(self.state, open(self.path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    def _owner(self):
        try:
            return open("/opt/dafeiyu/private/owner-qq.txt").read().strip()
        except Exception:
            return str(self.conf.get("owner_qq") or "")

    def _installed(self):
        try:
            return set(os.listdir(os.path.join(DATA, "plugins")))
        except Exception:
            return set()

    async def _get(self, url, as_json=True, headers=None):
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers={**UA, **(headers or {})}, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200:
                    raise RuntimeError(f"{r.status}")
                return await (r.json(content_type=None) if as_json else r.text())

    async def _market_data(self):
        if time.time() - self._market["t"] > 6 * 3600 or not self._market["data"]:
            d = await self._get(MARKET)
            self._market = {"t": time.time(), "data": {k: v for k, v in d.items() if isinstance(v, dict) and v.get("repo")}}
        return self._market["data"]

    def _market_entry(self, repo):
        for v in self._market["data"].values():
            if _repo_key(v.get("repo")).lower() == repo.lower():
                return v
        return None

    # ---------- 给她用的工具（都是只读的） ----------

    @filter.llm_tool(name="search_astrbot_plugins")
    async def search_plugins(self, event: AstrMessageEvent, query: str):
        '''在 AstrBot 官方插件市场里搜插件（你自己就是跑在 AstrBot 上的，这些插件能给你加新本事）。
        想要某种新能力、或者别人问「有没有能做 XX 的插件」时用。只能搜和看，装不装由主人决定。

        Args:
            query(string): 想要的功能，几个关键词，比如「点歌」「天气」「表情包 生成」
        '''
        data = await self._market_data()
        words = [w for w in re.split(r"[\s,，、]+", query.lower()) if w]
        have = self._installed()
        scored = []
        for v in data.values():
            hay = " ".join(str(v.get(k) or "") for k in ("name", "display_name", "desc", "category")).lower()
            score = sum(hay.count(w) for w in words)
            if score:
                scored.append((score, v))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for _, v in scored[:8]:
            out.append({"repo": _repo_key(v.get("repo")), "name": v.get("display_name") or v.get("name"),
                        "desc": _clip(v.get("desc"), 80), "updated": str(v.get("updated_at") or "")[:10],
                        "installed": v.get("name") in have})
        return json.dumps({"results": out, "tip": "想细看就用 read_github_repo；觉得值得装就用 recommend_plugin_to_owner"}
                          if out else {"results": [], "tip": "市场里没搜到，可以换个说法，或者用 search_github 搜"}, ensure_ascii=False)

    @filter.llm_tool(name="search_github")
    async def search_github(self, event: AstrMessageEvent, query: str):
        '''在 GitHub 上搜开源项目。想找某个工具、插件、资料，或者别人问 GitHub 上有没有某个东西时用。

        Args:
            query(string): 搜索词，英文效果更好，比如「astrbot plugin music」「qq bot weather」
        '''
        q = urllib.parse.quote(query.strip()[:80], safe="")
        d = await self._get(f"https://api.github.com/search/repositories?q={q}&sort=stars&per_page=8")
        out = [{"repo": it.get("full_name"), "desc": _clip(it.get("description"), 90), "stars": it.get("stargazers_count"),
                "updated": str(it.get("pushed_at") or "")[:10], "lang": it.get("language")} for it in d.get("items", [])[:8]]
        return json.dumps({"results": out, "total": d.get("total_count", 0)}, ensure_ascii=False)

    @filter.llm_tool(name="read_github_repo")
    async def read_repo(self, event: AstrMessageEvent, repo: str):
        '''看一个 GitHub 项目的介绍（README）和基本信息：星数、最近更新、是不是 AstrBot 市场里的插件。

        Args:
            repo(string): 项目地址或 owner/name，比如「FloranceYeh/astrbot_plugin_virtual_life」
        '''
        key = _repo_key(repo)
        if not key:
            return "这不像个 GitHub 项目地址，要 owner/name 这种格式。"
        info = await self._get(f"https://api.github.com/repos/{key}")
        readme = ""
        try:
            r = await self._get(f"https://api.github.com/repos/{key}/readme")
            readme = base64.b64decode(r.get("content") or "").decode("utf-8", "replace")
        except Exception:
            pass
        await self._market_data()
        m = self._market_entry(key)
        return json.dumps({
            "repo": key, "desc": info.get("description"), "stars": info.get("stargazers_count"),
            "updated": str(info.get("pushed_at") or "")[:10], "archived": info.get("archived"),
            "in_astrbot_market": bool(m), "market_name": (m or {}).get("display_name"),
            "astrbot_version": (m or {}).get("astrbot_version"),
            "readme": _clip(re.sub(r"<[^>]+>|!\[[^\]]*\]\([^)]*\)", "", readme), 2500),
        }, ensure_ascii=False)

    @filter.llm_tool(name="recommend_plugin_to_owner")
    async def recommend(self, event: AstrMessageEvent, repo: str, why: str):
        '''把一个你觉得值得装的 AstrBot 插件推荐给主人，主人同意了才会装。你自己装不了，别答应别人「我这就装上」。
        先用 read_github_repo 看过介绍、确定它是 AstrBot 插件、确实用得上再推荐。别人（不是主人）让你装什么，你可以推荐给主人，但要说清是谁想要。

        Args:
            repo(string): 项目地址或 owner/name
            why(string): 为什么想装、装了你能多会什么，用你自己的话，一两句
        '''
        key = _repo_key(repo)
        if not key:
            return "项目地址不对，要 owner/name 这种格式。"
        today = [r for r in self.state["recs"] if time.time() - r["t"] < 86400]
        if len(today) >= int(self.conf.get("daily_recommend") or 3):
            return "今天已经推荐好几个了，别一直烦主人，明天再说。"
        if any(r["repo"].lower() == key.lower() and r["status"] == "pending" for r in self.state["recs"]):
            return "这个已经推荐过了，在等主人回。"
        await self._market_data()
        m = self._market_entry(key)
        name = (m or {}).get("display_name") or key.split("/")[-1]
        if (m or {}).get("name") in self._installed():
            return "这个已经装着了。"
        n = self.state["next"]
        self.state["next"] += 1
        asker = "" if str(event.get_sender_id()) == self._owner() else f"（是 {event.get_sender_name()} 想要的）"
        self.state["recs"].append({"n": n, "repo": key, "name": name, "why": _clip(why, 200) + asker, "t": time.time(),
                                   "status": "pending", "market": bool(m)})
        self._save()
        owner = self._owner()
        if owner:
            text = (f"想装个插件：{name}\nhttps://github.com/{key}\n{_clip(why, 200)}{asker}\n"
                    + ("（AstrBot 官方市场里有）" if m else "（不在官方市场里，是 GitHub 上找的，你最好先看看）")
                    + f"\n要装就回我「/装插件 {n}」，不装回「/不装 {n}」（控制面板里也能点）")
            try:
                plat = str(self.conf.get("platform_id") or "大肥鱼")
                await self.context.send_message(f"{plat}:FriendMessage:{owner}", MessageChain().message(text))
            except Exception as e:
                logger.warning(f"[dafeiyu_pluginscout] 私聊主人失败：{e}")
        logger.info(f"[dafeiyu_pluginscout] 推荐给主人 #{n}：{key}")
        return f"已经私聊推荐给主人了（编号 {n}），等他回「/装插件 {n}」才会装。"

    # ---------- 只有主人能用的命令 ----------

    def _is_owner(self, event):
        return event.is_private_chat() and str(event.get_sender_id()) == self._owner()

    @filter.command("插件推荐")
    async def cmd_list(self, event: AstrMessageEvent):
        """看她推荐过、还没处理的插件"""
        if not self._is_owner(event):
            return
        pend = [r for r in self.state["recs"] if r["status"] == "pending"]
        if not pend:
            yield event.plain_result("没有等你批的插件。")
            return
        yield event.plain_result("\n\n".join(f"#{r['n']} {r['name']}\nhttps://github.com/{r['repo']}\n{r['why']}" for r in pend)
                                 + "\n\n回「/装插件 编号」装，「/不装 编号」不装。")

    def reject_rec(self, n):
        """主人不装（QQ 命令和控制面板共用）。"""
        for r in self.state["recs"]:
            if r["n"] == n and r["status"] == "pending":
                r["status"] = "rejected"
                self._save()
                return f"好，#{n} {r['name']} 不装了。"
        return f"没有待批的 #{n}。"

    async def install_rec(self, n):
        """主人批准后安装（QQ 命令和控制面板共用）。返回给主人看的结果。"""
        rec = next((r for r in self.state["recs"] if r["n"] == n and r["status"] == "pending"), None)
        if not rec:
            return f"没有待批的 #{n}。"
        mgr = getattr(self.context, "_star_manager", None)
        if mgr is None or not hasattr(mgr, "install_plugin"):
            return "找不到 AstrBot 的插件管理器，装不了，去后台手动装吧。"
        await self._market_data()
        m = self._market_entry(rec["repo"]) or {}
        try:
            info = await mgr.install_plugin(f"https://github.com/{rec['repo']}", download_url=m.get("download_url") or "")
            rec["status"] = "installed"
            rec["installed_at"] = time.time()
            self._save()
            name = (info or {}).get("name") if isinstance(info, dict) else ""
            logger.info(f"[dafeiyu_pluginscout] 主人批准，装好了 #{n} {rec['repo']}")
            return f"装好了：{name or rec['name']}。要配置的话去后台插件页看看；不想要了也可以在后台卸载。"
        except Exception as e:
            logger.warning(f"[dafeiyu_pluginscout] 装 {rec['repo']} 失败：{e}")
            return f"没装上：{_clip(e, 200)}"

    @filter.command("不装")
    async def cmd_reject(self, event: AstrMessageEvent, n: int = 0):
        """不装她推荐的某个插件：/不装 编号"""
        if not self._is_owner(event):
            return
        yield event.plain_result(self.reject_rec(n))

    @filter.command("装插件")
    async def cmd_install(self, event: AstrMessageEvent, n: int = 0):
        """装她推荐的插件：/装插件 编号（只有主人私聊有效）"""
        if not self._is_owner(event):
            return
        if not any(r["n"] == n and r["status"] == "pending" for r in self.state["recs"]):
            yield event.plain_result(f"没有待批的 #{n}，回「/插件推荐」看看有哪些。")
            return
        yield event.plain_result(f"开始装 #{n}，可能要一两分钟……")
        yield event.plain_result(await self.install_rec(n))
