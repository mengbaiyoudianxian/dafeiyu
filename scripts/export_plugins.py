"""把大肥鱼用到的插件各自导出成独立的 GitHub 仓库（脱敏），方便别人在 AstrBot 里「从链接安装」。可反复运行。

用法：python3 export_plugins.py [--push] [插件目录名 ...]
- 自写插件 → mengbaiyoudianxian/<插件名>，大肥鱼非商业许可。
- 魔改过的第三方插件 → mengbaiyoudianxian/<上游仓库名>（GitHub fork），保留上游原协议和署名，README 顶部写清改了什么。
- 每个仓库用自己的部署钥匙推送（~/.ssh/config 里的 Host gh-<仓库名>）。
- 泄漏扫描不过的仓库不推。
"""
import glob, json, os, re, shutil, subprocess, sys

sys.argv, ARGS = sys.argv[:1] + ['/tmp/_dfy_export_dummy'], sys.argv[1:]
# 借用 export_repo.py 里的脱敏规则（只执行到定义完函数为止）
_src = open('/opt/dafeiyu/github/export_repo.py', encoding='utf-8').read()
exec(_src.split('# ---------- 开始导出 ----------')[0])
_leak_src = _src.split('# ---------- 泄漏扫描 ----------')[1].split('private_literals')[0]
exec(_leak_src)
LEAK.append((r'43\.142\.97\.15|38\.76\.188\.50', '服务器 IP'))
private_literals = [x for x in (owner, bot) if x] + gnames + risky_names

PUSH = '--push' in ARGS
ONLY = [a for a in ARGS if not a.startswith('--')]
GH = 'mengbaiyoudianxian'
ROOT = '/opt/dafeiyu/github/plugin-repos/'
MAIN = f'https://github.com/{GH}/dafeiyu'
LICENSE_SRC = '/opt/dafeiyu/github/templates/LICENSE'

# 魔改过的第三方插件：目录名 → (上游仓库, 改了什么)
THIRD = {
    'astrbot_plugin_better_music': ('Nortea575/astrbot_plugin_better_music', [
        '酷狗取歌曲地址：原来走的 node 接口被酷狗风控拦截（错误码 20028），改成 Python 直连 v5/url，先要 320k、拿不到再要 128k；直连失败才退回原接口',
        '下载核对 Content-Length，下到一半断了自动重下（最多 3 次），不会把半截歌当整首发出去']),
    'astrbot_plugin_bili_scroll': ('GuHan-OvO/astrbot_plugin_bili_scroll', [
        'B站私信因为主人没关注她（21047）发不出去时，改去 QQ 跟主人说，语气按她当时的心情来',
        '刷片失败只算最近 3 小时内的，故障修好后过一阵就会自己接着刷，不会关一整天',
        '给主人分享视频不限字数、不套固定句式，想说什么说什么',
        'B站记忆带进 QQ 时保留 UID、昵称和视频链接，只挡登录凭据']),
    'astrbot_plugin_group_chat_plus': ('Him666233/astrbot_plugin_group_chat_plus', [
        '防复读放宽：很短的回答（数字、「好」「嗯」）被再问一遍时照答，不算复读']),
    'astrbot_plugin_livingmemory': ('lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory', [
        '记忆质量判断：「有人回我」「刷到有人…」这类正常说法不再被判成泛化低质量，只有拿匿名者当主语开头才算']),
    'astrbot_plugin_multi_parser': ('Qfxaile/astrbot_plugin_multi_parser', [
        '链接解析失败时不往群里发插件报错，交给她自己接话']),
    'astrbot_plugin_qzone': ('Zhalslar/astrbot_plugin_qzone', [
        '空间刷不到说说、接口出错时，提示换成她自己的口吻，不把原始报错甩给用户']),
}

SKIP_DIRS = ('data', 'tmp', 'cache', '__pycache__', '.git', 'node_modules')


def meta(d):
    m = {}
    try:
        for line in open(os.path.join(d, 'metadata.yaml'), encoding='utf-8'):
            mm = re.match(r'^(\w+):\s*(.*?)\s*(#.*)?$', line)
            if mm:
                m[mm.group(1)] = mm.group(2).strip('"\'')
    except Exception:
        pass
    return m


def clean_dir(out):
    os.makedirs(out, exist_ok=True)
    for name in os.listdir(out):
        if name == '.git':
            continue
        p = os.path.join(out, name)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)


def copy_plugin(src, out):
    for f in glob.glob(src + '/**/*', recursive=True):
        rel = os.path.relpath(f, src)
        parts = rel.split(os.sep)
        if (not os.path.isfile(f) or any(p in SKIP_DIRS for p in parts) or '.bak' in rel
                or rel.endswith(('.pyc', '.log', '.db', '.sqlite', '.orig'))):
            continue
        copy_text(f, os.path.join(out, rel))


def set_repo_field(out, url):
    p = os.path.join(out, 'metadata.yaml')
    s = open(p, encoding='utf-8').read()
    if re.search(r'^repo:.*$', s, re.M):
        s = re.sub(r'^repo:.*$', f'repo: {url}', s, flags=re.M)
    else:
        s = s.rstrip('\n') + f'\nrepo: {url}\n'
    s = re.sub(r'^author:\s*dafeiyu-local\s*$', f'author: {GH}', s, flags=re.M)
    open(p, 'w', encoding='utf-8').write(s)


def conf_items(d):
    p = os.path.join(d, '_conf_schema.json')
    if not os.path.exists(p):
        return []
    try:
        j = json.load(open(p, encoding='utf-8-sig'))
    except Exception:
        return []
    rows = []
    for k, v in j.items():
        if isinstance(v, dict):
            desc = scrub(str(v.get('description') or '')).replace('|', '/').replace('\n', ' ')
            hint = scrub(str(v.get('hint') or '')).replace('|', '/').replace('\n', ' ')
            rows.append(f'| `{k}` | {desc} | {hint[:100]} |')
    return rows


def leak_scan(out):
    problems = []
    for f in glob.glob(out + '/**/*', recursive=True):
        if not os.path.isfile(f) or '/.git/' in f or not f.endswith(TEXT_EXT):
            continue
        s = open(f, encoding='utf-8', errors='ignore').read()
        for p, what in LEAK:
            m = re.search(p, s)
            if m and not (what == 'BDUSS' and f.endswith('.md')):
                problems.append(f'{os.path.relpath(f, out)}: {what}（{m.group(0)[:6]}…）')
        for lit in private_literals:
            if lit and lit in s:
                problems.append(f'{os.path.relpath(f, out)}: 私人信息「{lit[:2]}…」')
    return problems


def git(out, *a, check=True):
    return subprocess.run(['git', '-C', out, *a], check=check, capture_output=True, text=True)


def ensure_repo(out, repo_name, fork=False):
    remote = f'git@gh-{repo_name}:{GH}/{repo_name}.git'
    if not os.path.isdir(os.path.join(out, '.git')):
        if fork:  # 从自己的 fork 克隆，保留上游历史
            subprocess.run(['git', 'clone', '-q', remote, out], check=True)
        else:
            os.makedirs(out, exist_ok=True)
            git(out, 'init', '-q', '-b', 'main')
            git(out, 'remote', 'add', 'origin', remote)
    git(out, 'config', 'user.name', 'mengbaiyoudianxian')
    git(out, 'config', 'user.email', f'{GH}@users.noreply.github.com')


SELF = [os.path.basename(d) for d in sorted(glob.glob(P + 'astrbot_plugin_dafeiyu_*')) if os.path.basename(d) not in EXCLUDE_PLUGINS]
ALL_SELF = set(SELF)
index = []  # 给主仓库和主页用的清单

for name in SELF + list(THIRD):
    if ONLY and name not in ONLY:
        continue
    src = P + name
    m = meta(src)
    third = name in THIRD
    repo_name = THIRD[name][0].split('/')[1] if third else name
    url = f'https://github.com/{GH}/{repo_name}'
    out = ROOT + repo_name
    ensure_repo(out, repo_name, fork=third)
    clean_dir(out)
    copy_plugin(src, out)
    set_repo_field(out, url)
    desc = scrub(m.get('desc', '')).strip()
    disp = m.get('display_name') or name
    if third:
        upstream, changes = THIRD[name]
        rd = os.path.join(out, 'README.md')
        old = open(rd, encoding='utf-8').read() if os.path.exists(rd) else ''
        head = (f'> ## 🐟 大肥鱼魔改版\n>\n> 这是 [{upstream}](https://github.com/{upstream}) 的 fork，'
                f'基于上游 {m.get("version", "")}，是 [大肥鱼]({MAIN}) 实际在跑的版本。原作者 **{m.get("author", "")}**，'
                f'协议沿用上游的 `LICENSE`，跟大肥鱼主仓库的非商业许可无关。\n>\n> 改了什么：\n>\n'
                + ''.join(f'> - {c}\n' for c in changes)
                + f'>\n> 安装：AstrBot 后台 → 插件 → 从链接安装 → `{url}`。想要原版请装上游。\n\n---\n\n')
        open(rd, 'w', encoding='utf-8').write(head + re.sub(r'^> ## 🐟 大肥鱼魔改版.*?\n---\n\n', '', old, flags=re.S))
    else:
        shutil.copy2(LICENSE_SRC, os.path.join(out, 'LICENSE'))
        deps = sorted({r for r in re.findall(r'astrbot_plugin_dafeiyu_[a-z]+', ' '.join(
            open(f, encoding='utf-8', errors='ignore').read() for f in glob.glob(src + '/*.py'))) if r != name and r in ALL_SELF})
        req = os.path.join(src, 'requirements.txt')
        reqs = [l.strip() for l in open(req)] if os.path.exists(req) else []
        conf = conf_items(src)
        lines = [f'# {disp}', '', f'> `{name}` · {m.get("version", "")} · [大肥鱼]({MAIN}) 的自写插件之一', '',
                 desc, '', '## 安装', '',
                 f'AstrBot（4.x）后台 → 插件 → 右上角「+」→ 从链接安装，填：', '', '```', url, '```', '',
                 '装完在插件列表里点开它的配置，按需改。平台目前只适配 aiocqhttp（NapCat / OneBot v11）。', '']
        if m.get('help'):
            lines += ['## 怎么用', '', scrub(m['help']), '']
        if reqs:
            lines += ['## Python 依赖', '', 'AstrBot 安装时会自动装 `requirements.txt`：', '', '```', *reqs, '```', '']
        if deps:
            lines += ['## 搭配', '', '这个插件会读写下面这些大肥鱼插件的数据，单独装也能跑，但相关功能会少一块：', '',
                      *[f'- [`{d}`](https://github.com/{GH}/{d})' for d in deps], '']
        if conf:
            lines += ['## 配置项', '', '| 键 | 说明 | 提示 |', '|---|---|---|', *conf, '']
        lines += ['## 许可', '', '[大肥鱼 非商业许可协议 v1.0](LICENSE)：可以看、可以自己用、可以改，**不得商用**——'
                  '不得收费代部署 / 代托管 / 做成 SaaS，不得改成别家 Agent 或产品卖。整套部署方法见 '
                  f'[大肥鱼主仓库]({MAIN})。', '']
        open(os.path.join(out, 'README.md'), 'w', encoding='utf-8').write(scrub('\n'.join(lines)))
    probs = leak_scan(out)
    index.append({'name': name, 'repo': repo_name, 'url': url, 'display_name': disp, 'version': m.get('version', ''),
                  'desc': desc, 'third_party': third, 'upstream': THIRD[name][0] if third else '',
                  'author': m.get('author', '') if third else '主人'})
    if probs:
        print(f'✗ {repo_name} 泄漏扫描没过，不推：')
        for p in probs[:20]:
            print('   -', p)
        continue
    git(out, 'add', '-A')
    if git(out, 'diff', '--cached', '--quiet', check=False).returncode == 0:
        print(f'· {repo_name} 没变化')
        continue
    msg = '大肥鱼魔改版：同步线上实际运行的代码' if third else f'同步 {disp} {m.get("version", "")}'
    git(out, 'commit', '-q', '-m', msg)
    if PUSH:
        r = git(out, 'push', '-q', 'origin', 'HEAD:main', check=False)
        print(f'{"✓" if r.returncode == 0 else "✗"} {repo_name} {"推送成功" if r.returncode == 0 else "推送失败：" + r.stderr.strip()[-200:]}')
    else:
        print(f'○ {repo_name} 已提交（没加 --push）')

if not ONLY:
    json.dump(index, open(ROOT + 'index.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'清单 {len(index)} 个 → {ROOT}index.json')
