"""Claude 巡检中转：Claude 每小时看一次日志，发现她哪里不像人就直接修（修前备份），修完用她的 QQ 私聊主人列出编号；
主人在 QQ 里回「撤回 #编号」（可一次写多个），这里记下来交给 Claude，Claude 按备份退回。

- 发：Claude 往 /opt/dafeiyu/private/claude_relay/outbox/ 放 .txt，这里每 15 秒扫一次，私聊发给主人，挪到 sent/。
- 收：只认主人私聊、且带「#编号」的回复，写进 inbox.jsonl，回一句「收到」，并拦下不让她接话。
  别人发的、群里发的一律不算。
"""

import asyncio
import json
import os
import re
import shutil
import time

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

ROOT = "/opt/dafeiyu/private/claude_relay"
OUT, SENT, INBOX = f"{ROOT}/outbox", f"{ROOT}/sent", f"{ROOT}/inbox.jsonl"
YES = r"同意|好的?|可以|行|改吧|改(?=\s*$)|ok|OK|Ok"
NO = r"不同意|不行|不要|别改|拒绝|算了|no|NO"
REVERT = r"撤回|撤销|退回|回滚|还原|改回|恢复"
REPLY = re.compile(rf"^\s*(?P<verb>{NO}|{YES})\s*[#＃]\s*(?P<id>\d{{1,6}})\s*(?P<rest>.*)$", re.S)
REPLY2 = re.compile(rf"^\s*[#＃]\s*(?P<id>\d{{1,6}})\s*(?P<verb>{NO}|{YES})?\s*(?P<rest>.*)$", re.S)


def _owner_qq():
    path = "/opt/dafeiyu/astrbot/data/config/astrbot_plugin_bili_scroll_config.json"
    try:
        with open(path, encoding="utf-8-sig") as f:
            return str(json.load(f).get("OWNER_QQ_UMO") or "").strip().rsplit(":", 1)[-1]
    except Exception:
        return ""


class DafeiyuClaude(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        for d in (OUT, SENT):
            os.makedirs(d, exist_ok=True)
        self._task = asyncio.create_task(self._loop())

    async def terminate(self):
        self._task.cancel()

    def _api(self):
        inst = self.context.get_platform_inst("大肥鱼")
        if inst is None:
            for p in self.context.platform_manager.platform_insts:
                if p.meta().name == "aiocqhttp":
                    inst = p
                    break
        return inst.get_client().api

    async def _loop(self):
        await asyncio.sleep(20)
        while True:
            try:
                await self._flush()
            except Exception as e:
                logger.warning(f"[dafeiyu_claude] 发巡检消息失败：{e}")
            try:
                await self._probe()
            except Exception as e:
                logger.warning(f"[dafeiyu_claude] 调试调用失败：{e}")
            await asyncio.sleep(15)

    async def _probe(self):
        """巡检排查用：服务器本地 probe/ 下放 {"action", "params"} 的 .json，这里调 NapCat，结果写成 .out。
        只有能登服务器的人（Claude）能放文件，QQ 消息碰不到这里。"""
        d = f"{ROOT}/probe"
        if not os.path.isdir(d):
            return
        for n in sorted(x for x in os.listdir(d) if x.endswith(".json")):
            p = os.path.join(d, n)
            try:
                with open(p, encoding="utf-8") as f:
                    j = json.load(f)
                res = await self._api().call_action(j["action"], **(j.get("params") or {}))
                out = {"ok": True, "data": res}
            except Exception as e:
                out = {"ok": False, "error": str(e)[:2000]}
            with open(p[:-5] + ".out", "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, default=str)
            os.remove(p)

    async def _flush(self):
        names = sorted(n for n in os.listdir(OUT) if n.endswith(".txt"))
        if not names:
            return
        owner = _owner_qq()
        if not owner:
            return
        for n in names:
            p = os.path.join(OUT, n)
            with open(p, encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                for i in range(0, len(text), 1500):
                    await self._api().call_action("send_private_msg", user_id=int(owner), message=text[i:i + 1500])
                    await asyncio.sleep(1)
            shutil.move(p, os.path.join(SENT, n))
            logger.info(f"[dafeiyu_claude] 发给主人：{n}")

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=1000)
    async def on_owner_reply(self, event: AstrMessageEvent):
        owner = _owner_qq()
        if not owner or str(event.get_sender_id()) != owner:
            return
        text = (event.message_str or "").strip()
        ids = [int(x) for x in re.findall(r"[#＃]\s*(\d{1,6})", text)]
        if not ids:
            return
        if re.search(REVERT, text):
            decision = "revert"
        else:
            m = REPLY.match(text) or REPLY2.match(text)
            verb = (m.group("verb") if m else "") or ""
            decision = "reject" if verb and re.fullmatch(NO, verb) else "approve" if verb else "comment"
        t = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(INBOX, "a", encoding="utf-8") as f:
            for i in dict.fromkeys(ids):
                f.write(json.dumps({"t": t, "id": i, "decision": decision, "text": text[:2000]}, ensure_ascii=False) + "\n")
        tag = "、".join(f"#{i}" for i in dict.fromkeys(ids))
        logger.info(f"[dafeiyu_claude] 主人回复 {tag}：{decision}")
        word = {"approve": "同意", "reject": "不改", "comment": "补充意见", "revert": "撤回"}[decision]
        event.stop_event()
        await event.send(event.plain_result(f"【Claude 巡检】收到 {tag}（{word}），下次巡检（一小时内）处理。"))
