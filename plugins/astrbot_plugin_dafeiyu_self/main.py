"""大肥鱼的「脑子」：知道自己现在用的什么模型、走订阅还是 API、订阅号的套餐/到期/额度，能自己切模型和订阅号。
- 订阅号由本机 codex 桥接（127.0.0.1:18790）管：多个号，额度用完自动换下一个；这里能查状态、指定优先用哪个（立即生效）。
- 主模型写在 cmd_config.json 的 agent_runner.config.model 里，AstrBot 启动时读：切主模型 = 改配置 + 后台重启一次（约半分钟）。
- 主人让切随时切；别人提的她自己拿主意，30 分钟内最多切一次。
"""
import asyncio
import json
import os
import subprocess
import time

import aiohttp

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

BRIDGE = os.environ.get("DAFEIYU_BRIDGE_URL", "http://127.0.0.1:18790").rstrip("/")
CFG = "/opt/dafeiyu/astrbot/data/cmd_config.json"
OWNER = os.environ.get("DAFEIYU_OWNER_QQ", "").strip()
# 可切的大脑：名字 -> provider_id（来源说明）
BRAINS = {
    "GPT订阅": ("chatgpt/gpt-6.2-sol", "ChatGPT 订阅（Codex 通道，按订阅额度，不按量扣钱）"),
    "GPT订阅深度思考": ("chatgpt/gpt-6.2-sol-deep", "ChatGPT 订阅，深度思考档（慢、费额度）"),
    "DeepSeek": ("deepseek/deepseek-flash", "DeepSeek 官方 API（按量扣钱，花的是 DeepSeek 余额）"),
    "Grok": ("grok/grok-4.7", "xAI Grok API"),
    "Grok快速": ("grok/grok-4.20-fast", "xAI Grok API，快速不思考版"),
}
PLAN_NOTE = ("plan 字段是 ChatGPT 官方给的套餐类型：plus=Plus（个人基础付费）；pro=Pro（最高一档个人套餐）；"
             "pro_lite/prolite=Pro Lite；team/business/enterprise=团队企业版。官方字段里没有单独标「20 倍」这种倍数，"
             "额度大小看 rate_limits 里每周窗口用了百分之几。")


def _source_of(pid):
    for name, (p, src) in BRAINS.items():
        if p == pid:
            return name, src
    return pid, "未知来源"


class Main(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._last_switch = 0.0

    def _cfg(self):
        return json.load(open(CFG, encoding="utf-8-sig"))

    async def _bridge(self, method, path, body=None):
        async with aiohttp.ClientSession() as s:
            async with s.request(method, BRIDGE + path, json=body, timeout=aiohttp.ClientTimeout(total=20)) as r:
                return await r.json(content_type=None)

    async def _deepseek_balance(self, cfg):
        try:
            src = next(s for s in cfg.get("provider_sources", []) if s.get("id") == "deepseek")
            key = src.get("key")
            key = key[0] if isinstance(key, list) else key
            async with aiohttp.ClientSession() as s:
                async with s.get("https://api.deepseek.com/user/balance", headers={"Authorization": f"Bearer {key}"},
                                 timeout=aiohttp.ClientTimeout(total=15)) as r:
                    j = await r.json(content_type=None)
            b = (j.get("balance_infos") or [{}])[0]
            return f"{b.get('total_balance')} {b.get('currency', '')}".strip()
        except Exception as e:
            return f"查不到（{type(e).__name__}）"

    @filter.llm_tool(name="my_brain_status")
    async def brain_status(self, event: AstrMessageEvent):
        """查你自己的「脑子」：现在用的哪个模型、是订阅还是 API、备用是谁；ChatGPT 订阅号各是什么套餐、订阅到哪天、额度用了多少、限额什么时候恢复；DeepSeek 余额。
        别人问你用的什么模型、是不是 GPT、订阅什么档、什么时候到期，或者你自己想知道时用。说的时候用你自己的口吻，别念 JSON。"""
        cfg = self._cfg()
        m = (cfg.get("agent_runner") or {}).get("config", {}).get("model", {})
        main = m.get("provider_id", "")
        name, src = _source_of(main)
        out = {"现在主用": {"名字": name, "provider": main, "来源": src},
               "备用": [dict(zip(("名字", "来源"), _source_of(p))) for p in m.get("fallback_provider_ids") or []],
               "可切换的": {k: v[1] for k, v in BRAINS.items()}}
        try:
            st = await self._bridge("GET", "/accounts")
            accts = []
            for a in st.get("accounts", []):
                rl = a.get("rate_limits") or {}
                accts.append({"号": a["name"], "邮箱": a.get("email"), "套餐": a.get("plan"), "plan字段": a.get("plan_raw"),
                              "订阅到期(UTC)": (a.get("subscription_until") or "")[:16],
                              "现在能用": not a.get("limited"), "限额恢复": a.get("limit_resets_at") or "",
                              "本周额度已用%": rl.get("primary-used-percent"),
                              "额度窗口(分钟)": rl.get("primary-window-minutes"), "优先用": a.get("preferred")})
            out["ChatGPT订阅号"] = accts
            out["订阅号说明"] = PLAN_NOTE + " 一个号额度用完会自动换下一个号。"
        except Exception as e:
            out["ChatGPT订阅号"] = f"桥接查不到（{type(e).__name__}）"
        out["DeepSeek余额"] = await self._deepseek_balance(cfg)
        return json.dumps(out, ensure_ascii=False)

    def _may_switch(self, event):
        if str(event.get_sender_id()) == OWNER:
            return None
        if time.time() - self._last_switch < 1800:
            return f"{int((time.time() - self._last_switch) / 60)} 分钟前刚换过，先别来回换。"
        return None

    @filter.llm_tool(name="switch_brain")
    async def switch_brain(self, event: AstrMessageEvent, target: str, reason: str = ""):
        """换你自己的主模型（换完你会后台重启一次，大约半分钟说不了话）。主人让你换就换；别人让你换你自己拿主意，你的脑子你说了算。
        比如订阅额度用完了想换 DeepSeek、想省钱、觉得现在这个太笨想换好的。换之前可以先用 my_brain_status 看看。

        Args:
            target(string): 换成哪个：GPT订阅 / GPT订阅深度思考 / DeepSeek / Grok / Grok快速
            reason(string): 为什么换（一句话）
        """
        target = str(target or "").strip()
        hit = next((k for k in BRAINS if k.lower() == target.lower()), None) or next(
            (k for k in BRAINS if target.lower() in k.lower()), None)
        if not hit:
            return json.dumps({"ok": False, "note": "没有这个，可选：" + " / ".join(BRAINS)}, ensure_ascii=False)
        why = self._may_switch(event)
        if why:
            return json.dumps({"ok": False, "note": why}, ensure_ascii=False)
        pid = BRAINS[hit][0]
        cfg = self._cfg()
        m = cfg["agent_runner"]["config"]["model"]
        if m.get("provider_id") == pid:
            return json.dumps({"ok": True, "note": f"本来就在用 {hit}"}, ensure_ascii=False)
        old = m.get("provider_id")
        backup = CFG + ".bak-brain-" + time.strftime("%Y%m%d-%H%M%S")
        open(backup, "w", encoding="utf-8-sig").write(open(CFG, encoding="utf-8-sig").read())
        m["provider_id"] = pid
        fb = [p for p in (m.get("fallback_provider_ids") or []) if p != pid]
        if old and old not in fb:
            fb.insert(0, old)
        m["fallback_provider_ids"] = fb[:2]
        tmp = CFG + ".tmp"
        json.dump(cfg, open(tmp, "w", encoding="utf-8-sig"), ensure_ascii=False, indent=2)
        os.replace(tmp, CFG)
        self._last_switch = time.time()
        logger.info(f"[dafeiyu_self] 主模型 {old} -> {pid}（{event.get_sender_name()}：{reason}）")
        # 发完这句再重启：脱离当前进程组，延迟 10 秒
        subprocess.Popen(["setsid", "bash", "-c", "sleep 10; sudo -n systemctl restart dafeiyu-astrbot"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return json.dumps({"ok": True, "from": _source_of(old)[0], "to": hit, "source": BRAINS[hit][1],
                           "note": "已改好，10 秒后你会重启一次（半分钟左右回来）。先跟对方说一声要去换个脑子。"}, ensure_ascii=False)

    @filter.llm_tool(name="switch_subscription")
    async def switch_subscription(self, event: AstrMessageEvent, account: str, reason: str = ""):
        """指定 ChatGPT 订阅优先用哪个号（立即生效，不用重启；别的号照样当备用，额度用完会自动换）。号的名字用 my_brain_status 查。

        Args:
            account(string): 订阅号名字，比如 main、acct2
            reason(string): 为什么换（一句话）
        """
        why = self._may_switch(event)
        if why:
            return json.dumps({"ok": False, "note": why}, ensure_ascii=False)
        try:
            r = await self._bridge("POST", "/accounts/switch", {"account": str(account or "").strip()})
        except Exception as e:
            return json.dumps({"ok": False, "note": f"桥接连不上（{type(e).__name__}）"}, ensure_ascii=False)
        if r.get("ok"):
            self._last_switch = time.time()
            logger.info(f"[dafeiyu_self] 订阅优先号 -> {account}（{event.get_sender_name()}：{reason}）")
        return json.dumps(r, ensure_ascii=False)
