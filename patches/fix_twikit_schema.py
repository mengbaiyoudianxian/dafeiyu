#!/usr/bin/env python3
"""把 twikit 的 User 解析器改成『字段缺了就给默认值』。
推特不断精简返回的 legacy 用户对象，twikit 2.x 会一个字段一个字段地 KeyError。
twikit 若被重装，重跑一次本脚本即可。幂等。
用 astrbot 的 python 跑：
  /opt/dafeiyu/uv/tools/astrbot/bin/python /opt/dafeiyu/kit/fix_twikit_schema.py
"""
import importlib.util, re, os, sys

spec = importlib.util.find_spec("twikit.user")
if not spec or not spec.origin:
    print("twikit 没找到，跳过"); sys.exit(0)
f = spec.origin
s = open(f, encoding="utf-8").read()
orig = s

# 1) 嵌套 description urls
s = s.replace(
    "legacy['entities']['description']['urls']",
    "legacy.get('entities', {}).get('description', {}).get('urls', [])",
)
# 2) 其余所有硬取 legacy['key'] -> 容错
s = re.sub(r"legacy\['([a-zA-Z_]+)'\]", r"legacy.get('\1')", s)
# 3) data 上的硬取
s = s.replace("data['is_blue_verified']", "data.get('is_blue_verified', False)")

if s != orig:
    bak = f + ".bak_schema"
    if not os.path.exists(bak):
        open(bak, "w", encoding="utf-8").write(orig)
    open(f, "w", encoding="utf-8").write(s)
    print("twikit user.py: 已打容错补丁 ->", f)
else:
    print("twikit user.py: 已是容错版，无需改动")
