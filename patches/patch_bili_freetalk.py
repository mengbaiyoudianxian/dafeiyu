#!/usr/bin/env python3
"""大肥鱼定制：B站给主人分享视频时不限字数，爱说什么说什么。

B站插件升级会覆盖，升级后重跑：python3 /opt/dafeiyu/kit/patch_bili_freetalk.py（重复运行安全）。
"""
import re
import sys
from pathlib import Path

P = Path("/opt/dafeiyu/astrbot/data/plugins/astrbot_plugin_bili_scroll/core/proactive.py")
CP = Path("/opt/dafeiyu/astrbot/data/plugins/astrbot_plugin_bili_scroll/core/content_protocol.py")

EDITS = [
    (P, "请写一句自然的随手分享，不必假定对方也有相同爱好。",
        "想说什么就说什么，不必假定对方也有相同爱好。"),
    (P, "只写推荐时附带的那句话。要求：", "只写推荐时附带的话。参考："),
    (P, "- 12-42字，通常一句，说完自然收住",
        "- 长短完全随你：想多聊就多聊（看点、吐槽、哪段好笑、你怎么看都能讲），想一句带过也行，像平时跟熟人聊天那样"),
    (P, "- 长短完全随你：想多聊就多聊（看点、吐槽、哪段好笑、你怎么看都能讲），想一句带过也行，像平时跟熟人聊天那样",
        "- 长短随你，但要让对方不点开、光看你的话就知道这视频讲了什么：具体发生了啥、哪段好笑或让你在意、你怎么看。别只丢一句感想让人猜"),
    (P, "- 不复述完整标题，不要堆感叹号、连续撒娇或以问题句催对方回应",
        "- 不复述完整标题"),
    (P, "- 不照搬“推荐理由”字段，不总结整部视频，不写“我为你找到了”或“给你推荐一个”",
        "- 不照搬“推荐理由”字段，不写“我为你找到了”或“给你推荐一个”"),
    (P, "max_tokens=180)", "max_tokens=1500)"),
    (P, "rec_text = re.sub(r'[\\r\\n]+', ' ', rec_text).strip(' \"“”\\'')[:48]",
        "rec_text = rec_text.strip(' \"“”\\'')[:900]  # dafeiyu: 不限字数（B站私信单条上限约1000）"),
    (P, "你刚刷 B站 看到一个值得分享的视频，想随手跟他说一句。", "你刚刷 B站 看到一个值得分享的视频，想跟他聊聊。"),
    (P, "规则：一句话，不超过 60 字；像随口分享，不要罗列要点；", "长短随你，想说多少说多少，像平时跟熟人聊天那样；"),
    (CP, '"text":"随手分享附言"', '"text":"想跟对方说的话，长短随意"'),
    (CP, 'text = _public_text(value.get("text"), field="text", limit=80, required=decision == "share")',
         'text = _public_text(value.get("text"), field="text", limit=900, required=decision == "share")'),
]

changed = 0
for path, old, new in EDITS:
    s = path.read_text(encoding="utf-8")
    if new in s:
        continue
    if old not in s:
        sys.exit(f"找不到要改的地方：{path.name}：{old[:30]}（插件版本变了，需要人工看一下）")
    s = s.replace(old, new)
    path.write_text(s, encoding="utf-8")
    changed += 1

# 解析器里如果有长度上限也提示一下
for m in re.finditer(r".*len\(.*\).*", CP.read_text(encoding="utf-8")):
    print("content_protocol 里的长度判断：", m.group().strip())
print(f"不限字数补丁：改动 {changed} 处")
