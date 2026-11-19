"""给「更好的点歌」打补丁：酷狗取歌地址不走 KuGouMusicApi（它的请求被酷狗风控 20028 拦了），改成 Python 直连 gateway.kugou.com/v5/url。
直连失败才退回原来的 node 接口。插件升级后重跑：python3 /opt/dafeiyu/kit/patch_kugou_direct.py
"""
import re, shutil, sys, time
R = '/opt/dafeiyu/astrbot/data/plugins/astrbot_plugin_better_music/runtime.py'


def patch_runtime():
    """下载补丁：核对 Content-Length，下到一半断了就重下（最多 3 次），不再把半截歌当整首。"""
    s = open(R, encoding='utf-8').read()
    if '_download_http_once' in s:
        print('runtime already patched'); return
    sig = '    def _download_http(url: str, headers: dict[str, str], destination: Path) -> tuple[str, int]:\n'
    assert s.count(sig) == 1, 'runtime _download_http not found'
    chk = '        if total == 0:\n            destination.unlink(missing_ok=True)\n            raise AudioError("音频下载内容为空")\n        return content_type, total\n'
    assert s.count(chk) == 1, 'runtime total check not found'
    s = s.replace(chk, '        if length_header and total != int(length_header):\n'
                       '            destination.unlink(missing_ok=True)\n'
                       '            raise AudioError(f"音频下载不完整（{total}/{length_header} 字节）")\n' + chk, 1)
    wrapper = ('    def _download_http(url: str, headers: dict[str, str], destination: Path) -> tuple[str, int]:\n'
               '        last = None\n'
               '        for attempt in range(3):\n'
               '            try:\n'
               '                return MusicRuntime._download_http_once(url, headers, destination)\n'
               '            except Exception as exc:  # 大肥鱼补丁：断线/不完整就重下\n'
               '                last = exc\n'
               '                destination.unlink(missing_ok=True)\n'
               '                time.sleep(2 * (attempt + 1))\n'
               '        raise last\n\n'
               '    @staticmethod\n')
    s = s.replace(sig, wrapper + sig.replace('_download_http(', '_download_http_once('), 1)
    if not re.search(r'^import time$', s, re.M):
        s = s.replace('\nimport ', '\nimport time\nimport ', 1)
    shutil.copy2(R, R + '.bak-direct-' + time.strftime('%Y%m%d%H%M%S'))
    open(R, 'w', encoding='utf-8').write(s)
    compile(s, R, 'exec')
    print('runtime patched')


P = '/opt/dafeiyu/astrbot/data/plugins/astrbot_plugin_better_music/providers.py'
src = open(P, encoding='utf-8').read()
if '_kugou_direct_song_url_q' in src:
    print('providers already patched'); patch_runtime(); sys.exit(0)
if '_kugou_direct_song_url' in src:  # 旧版补丁：换成新版函数
    src = re.sub(r'\n\ndef _kugou_direct_song_url\(.*?\n    return json\.loads\(raw\.decode\("utf-8", "replace"\) or "\{\}"\)\n', '\n', src, count=1, flags=re.S)
    assert '_kugou_direct_song_url(' not in src.split('async def _resolve_kugou')[0], 'old func not removed'
    FUNC_ONLY = True
else:
    FUNC_ONLY = False

FUNC = '''

def _kugou_direct_song_url(cookie: str, song_hash: str, album_id: Any = None, album_audio_id: Any = None) -> dict:
    """大肥鱼补丁：先要 320k，拿不到再要 128k。"""
    r = {}
    for q in (320, 128):
        r = _kugou_direct_song_url_q(cookie, song_hash, album_id, album_audio_id, q)
        u = r.get("url") if isinstance(r, dict) else None
        if any(_has_audio_url(x) for x in (u if isinstance(u, list) else [u])):
            return r
    return r


def _kugou_direct_song_url_q(cookie: str, song_hash: str, album_id: Any = None, album_audio_id: Any = None, quality: int = 128) -> dict:
    """大肥鱼补丁：直连酷狗 v5/url 取播放地址（模拟安卓 App 20889 的请求）。"""
    import hashlib
    import random
    import string
    md5 = lambda s: hashlib.md5(s.encode()).hexdigest()
    ck = {}
    for part in str(cookie or "").split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            ck.setdefault(k, v)
    token, userid = ck.get("token", ""), ck.get("userid", "0")
    mid = ck.get("KUGOU_API_MID") or str(int(md5(str(uuid.uuid4())), 16))
    dfid = ck.get("dfid") or "".join(random.choices(string.ascii_letters + string.digits, k=24))
    ver = "20889"
    p = {
        "album_id": int(album_id or 0), "area_code": 1, "hash": str(song_hash).lower(), "ssa_flag": "is_fromtrack",
        "version": ver, "page_id": 151369488, "quality": quality, "album_audio_id": int(album_audio_id or 0),
        "behavior": "play", "pid": 2, "cmd": 26, "pidversion": 3001, "IsFreePart": 0,
        "ppage_id": "463467626,350369493,788954147", "cdnBackup": 1, "module": "", "clientver": ver,
        "dfid": dfid, "mid": mid, "uuid": "-", "appid": 1005, "clienttime": int(time.time()),
    }
    if token:
        p["token"] = token
        p["userid"] = userid
    p["key"] = md5(f"{p['hash']}57ae12eb6890223e355ccfcb74edf70d{p['appid']}{p['mid']}{p.get('userid', 0)}")
    salt = "OIlwieks28dk2k092lksi2UIkp"
    p["signature"] = md5(salt + "".join(f"{k}={p[k]}" for k in sorted(p)) + salt)
    req = urllib.request.Request(
        "https://gateway.kugou.com/v5/url?" + urllib.parse.urlencode(p),
        headers={"User-Agent": f"Android11-AndroidPhone-{ver}-201-0-NetMusic-wifi", "x-router": "tracker.kugou.com"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        raw = opener.open(req, timeout=20).read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
    return json.loads(raw.decode("utf-8", "replace") or "{}")
'''

anchor = '\ndef _has_audio_url(url: Any) -> bool:'
assert src.count(anchor) == 1, 'anchor _has_audio_url not found'
src = src.replace(anchor, FUNC + '\n' + anchor, 1)

old = '        response, _ = await self._kugou_request("/song/url", params, authenticated=False)\n'
if FUNC_ONLY:
    old = 'NO-SUCH-LINE'
    src_backup_check = src
assert FUNC_ONLY or src.count(old) == 1, 'song/url call not found'
new = '''        response = {}
        with contextlib.suppress(Exception):
            response = await asyncio.to_thread(
                _kugou_direct_song_url, cookie, song_hash, link.get("album_id"), link.get("album_audio_id")
            )
        _urls = response.get("url") if isinstance(response, dict) else None
        if not any(_has_audio_url(u) for u in (_urls if isinstance(_urls, list) else [_urls])):
            response, _ = await self._kugou_request("/song/url", params, authenticated=False)
'''
src = src.replace(old, new, 1)
bak = P + '.bak-direct-' + time.strftime('%Y%m%d%H%M%S')
patch_runtime()
shutil.copy2(P, bak)
open(P, 'w', encoding='utf-8').write(src)
compile(src, P, 'exec')
print('patched, backup', bak)
