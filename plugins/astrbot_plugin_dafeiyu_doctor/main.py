"""大肥鱼自查：她自己的功能出状况时，能看自己的报错日志、插件开没开、音乐账号登没登，搞清楚问题出在哪。

只读：不改配置、不重启、不执行命令。日志里的聊天内容、QQ 号、token/cookie 一律不往外给。
"""

import json
import os
import re
import time
from collections import deque

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

# 关于什么 -> 日志里认哪些模块
TOPICS = {
    "歌": ["dafeiyu_sing", "better_music", "kugou", "netease"],
    "音乐": ["dafeiyu_sing", "better_music", "kugou", "netease"],
    "图": ["memesearch", "groupimage", "meme", "image", "grok"],
    "表情": ["memesearch", "meme_manager", "memelog"],
    "贴吧": ["tieba"],
    "推特": ["dafeiyu_x"],
    "b站": ["bili"], "B站": ["bili"], "视频": ["dafeiyu_video", "bili", "multi_parser"],
    "语音": ["tts", "mimo", "asr", "Record"],
    "记忆": ["livingmemory"],
    "空间": ["qzone"], "签名": ["signature", "qqprofile"],
    "群": ["group_chat_plus", "groupimage"],
}
LEVEL = re.compile(r"\[(WARN|ERRO|ERROR|CRIT)\]")
TS = re.compile(r"^\[(\d\d:\d\d:\d\d)")
SKIP = ("core.event_bus", "message_str", "faiss.swigfaiss", "fs_util", "ModuleNotFoundError(\"No module named 'faiss")


def _redact(s: str) -> str:
    s = re.sub(r"(?i)(token|cookie|key|sig(nature)?|auth|password|secret|qrsig|ticket)([\"'=:\s]+)[^\s&;,\"']+", r"\1\3***", s)
    s = re.sub(r"\b\d{6,}\b", "***", s)                      # QQ 号、用户 ID
    s = re.sub(r"[A-Za-z0-9+/=_-]{32,}", "…", s)              # 长串（哈希、密文）
    s = re.sub(r"https?://[^\s?]+\?\S+", lambda m: m.group(0).split("?")[0] + "?…", s)
    return s


class DafeiyuDoctor(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None) -> None:
        super().__init__(context)
        self.conf = config or {}

    @filter.llm_tool(name="check_myself")
    async def check_myself(self, event: AstrMessageEvent, about: str = ""):
        '''你自己哪里不对劲的时候用：某个工具报错了、功能不灵（放不了歌、发不出图、语音没声）、别人说你坏了/卡了/怎么不回。
        它会看你最近的报错日志、各个插件开没开、音乐账号登没登，帮你搞清楚问题出在哪。
        看完用人话跟对方说：是哪儿坏了、为什么、你接下来打算怎么办（换个办法再试，或者得主人来处理）。别把日志原样贴出去。

        Args:
            about(string): 关于哪块出的问题，比如「放歌」「表情包」「语音」「贴吧」；不知道就留空看全部
        '''
        about = (about or "").strip()[:20]
        keys = []
        for word, mods in TOPICS.items():
            if word.lower() in about.lower():
                keys += mods
        out = {"about": about or "全部", "checked_at": time.strftime("%H:%M")}
        try:
            out["recent_problems"] = self._scan_log(keys)
        except Exception as e:
            out["recent_problems"] = f"日志读不了：{e}"
        out["plugins_off"] = self._plugins_off()
        if not keys or any(k in keys for k in ("dafeiyu_sing", "better_music")):
            out["music"] = await self._music_status()
        out["how_to_read"] = ("recent_problems 是最近的报错（同类合并，带次数和最后一次时间「今天/昨天 时:分」）；"
                              "很早以前的可能早就修好了，以离现在最近的为准，别把旧问题当成现在的。"
                              "plugins_off 是被关掉的插件。music 是音乐账号状态和最近几次放歌结果。"
                              "看不出问题就照实说「日志里没看到相关报错」。需要改代码/改配置的事你自己做不了，告诉主人。")
        return json.dumps(out, ensure_ascii=False)

    def _scan_log(self, keys):
        path = str(self.conf.get("log_path") or "/opt/dafeiyu/logs/astrbot.log")
        if not os.path.exists(path):
            return f"找不到日志文件 {path}"
        n = int(self.conf.get("scan_lines") or 6000)
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = deque(f, maxlen=n)
        problems = {}
        last_ts, day = "", 0   # 日志只有时分秒：时间倒退一大截就算过了一天
        in_tb, tb_last = False, ""
        for raw in lines:
            line = raw.rstrip("\n")
            m = TS.match(line)
            if in_tb and m:  # Traceback 结束：它的最后一行顶格文字就是真正的异常
                if tb_last and not any(s in tb_last for s in SKIP):
                    self._add(problems, tb_last, (day, last_ts), keys, force=not keys)
                in_tb, tb_last = False, ""
            if m:
                if last_ts and m.group(1) < last_ts and last_ts[:2] >= "20" and m.group(1)[:2] <= "06":
                    day += 1
                last_ts = m.group(1)
            if line.startswith("Traceback"):
                in_tb, tb_last = True, ""
                continue
            if in_tb:
                if line and not line[0].isspace() and line[0] not in "│└─┌├":
                    tb_last = line
                continue
            if not LEVEL.search(line) or any(s in line for s in SKIP):
                continue
            self._add(problems, line, (day, last_ts), keys)
        items = sorted(problems.values(), key=lambda x: x["last"], reverse=True)[:12]
        names = {0: "今天", 1: "昨天", 2: "前天"}
        for x in items:
            ago = day - x["last"][0]
            x["last"] = f"{names.get(ago, f'{ago}天前')} {x['last'][1]}"
        return items or "最近日志里没有相关报错"

    @staticmethod
    def _add(problems, line, ts, keys, force=False):
        if keys and not force and not any(k.lower() in line.lower() for k in keys):
            return
        body = re.sub(r"^\[[^\]]*\]\s*(\[[^\]]*\]\s*)*:?\s*", "", line)   # 去掉时间、级别、模块前缀
        mod = re.search(r"\[([\w.]+):\d+\]", line)
        text = _redact(body)[:180]
        sig = re.sub(r"\d+", "#", text)[:80]
        p = problems.setdefault(sig, {"what": text, "where": mod.group(1) if mod else "", "count": 0, "last": ts})
        p["count"] += 1
        p["last"] = ts
        p["what"] = text

    def _plugins_off(self):
        off = []
        try:
            for s in self.context.get_all_stars():
                if not getattr(s, "activated", True):
                    off.append(getattr(s, "display_name", None) or getattr(s, "name", "?"))
        except Exception as e:
            return f"查不了：{e}"
        return off or "插件都开着"

    async def _music_status(self):
        res = {}
        meta = self.context.get_registered_star("astrbot_plugin_better_music")
        bm = getattr(meta, "star_cls", None) if meta else None
        if bm is None:
            res["accounts"] = "「更好的点歌」插件没装或没加载"
        else:
            acc = {}
            for p, name in (("kugou", "酷狗"), ("netease", "网易云"), ("qq", "QQ音乐")):
                try:
                    st = await bm.providers.chat_account_status(p)
                    acc[name] = st.get("status")
                except Exception as e:
                    acc[name] = f"查不了：{str(e)[:40]}"
            res["accounts"] = acc
        try:
            path = os.path.join(get_astrbot_data_path(), "plugin_data", "astrbot_plugin_dafeiyu_sing", "plays.json")
            plays = json.load(open(path, encoding="utf-8"))[-6:]
            res["recent_plays"] = [
                {"time": time.strftime("%m-%d %H:%M", time.localtime(x.get("t", 0))), "song": x.get("title"),
                 "ok": x.get("ok"), "cause": x.get("cause") or (_redact(x.get("error", ""))[:80] if not x.get("ok") else "")}
                for x in plays]
        except Exception:
            res["recent_plays"] = "没有放歌记录"
        return res
