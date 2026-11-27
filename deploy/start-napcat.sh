#!/bin/bash
# 有 private/bot-qq.txt 就快速登录小号，没有就出二维码
Q=""
[ -s /opt/dafeiyu/private/bot-qq.txt ] && Q="-q $(tr -dc 0-9 < /opt/dafeiyu/private/bot-qq.txt)"
exec xvfb-run -a /home/agentuser/Napcat/opt/QQ/qq --no-sandbox $Q
