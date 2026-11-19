#!/bin/sh
# QQ(NapCat) 出口走 mihomo 家宽节点。用法: qq_proxy.sh on|off|status
CG=system.slice/dafeiyu-napcat.service
del() { while iptables -t nat -D OUTPUT -m cgroup --path $CG -j DFY_QQ 2>/dev/null; do :; done; while iptables -D OUTPUT -m cgroup --path $CG -j DFY_QQU 2>/dev/null; do :; done
        iptables -t nat -F DFY_QQ 2>/dev/null; iptables -t nat -X DFY_QQ 2>/dev/null; iptables -F DFY_QQU 2>/dev/null; iptables -X DFY_QQU 2>/dev/null; }
FLAG=/opt/dafeiyu/private/mihomo/qq_proxy_on
[ "$1" = auto ] && { [ -f $FLAG ] && set -- on || exit 0; }
[ "$1" = on ] && touch $FLAG; [ "$1" = off ] && rm -f $FLAG
case "$1" in
on) del; while ip6tables -D OUTPUT -m cgroup --path $CG ! -d ::1 -j REJECT 2>/dev/null; do :; done
  iptables -t nat -N DFY_QQ
  iptables -t nat -A DFY_QQ -d 127.0.0.0/8 -j RETURN
  iptables -t nat -A DFY_QQ -d 100.64.0.0/10 -j RETURN
  iptables -t nat -A DFY_QQ -d 10.0.0.0/8 -j RETURN
  iptables -t nat -A DFY_QQ -p tcp -j REDIRECT --to-ports 7892
  iptables -t nat -A OUTPUT -m cgroup --path $CG -j DFY_QQ
  iptables -N DFY_QQU
  iptables -A DFY_QQU -d 127.0.0.0/8 -j RETURN
  iptables -A DFY_QQU -d 100.64.0.0/10 -j RETURN
  iptables -A DFY_QQU -p udp --dport 53 -j RETURN
  iptables -A DFY_QQU -p udp -j REJECT
  iptables -A OUTPUT -m cgroup --path $CG -j DFY_QQU
  ip6tables -C OUTPUT -m cgroup --path $CG -j REJECT 2>/dev/null || ip6tables -A OUTPUT -m cgroup --path $CG ! -d ::1 -j REJECT ;;
off) del; while ip6tables -D OUTPUT -m cgroup --path $CG ! -d ::1 -j REJECT 2>/dev/null; do :; done ;;
esac
iptables -t nat -S DFY_QQ 2>/dev/null; iptables -S DFY_QQU 2>/dev/null
