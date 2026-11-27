"""大肥鱼的知识图谱（带时间的知识图谱 / temporal knowledge graph）。

分层：
  原始层  kb.db 的 raw 表（群聊原文，只增不改，永久保存）——这里只读
  抽取层  每段聊天 → 话题段（episode）、实体、事实、人物观察；每条都记来源消息 id
  整合层  实体消歧合并；事实去重 / 更新 / 冲突（旧的标过时不删）；人物档案结构化合并
  索引层  关键词（FTS5 trigram）+ 语义向量（bge-m3）+ 实体关系图，三路 RRF 融合
  复盘层  每群每日 / 每周小结；出题测检索命中率

实体类型：人、群、设备、软件工具、游戏、渠道服务、领域话题、其他
事实：subject —predicate→ object + 一句可独立理解的陈述；
     kind 知识/经验/事件/观点/偏好/个人情况/关系；status 实测/说法/争议/过时；privacy 公开/私密；
     valid_from（说出的时间）、invalid_at（被新说法取代的时间）、superseded_by、sources（原始消息 id）
"""
import asyncio
import json
import random
import re
import sqlite3
import time
from datetime import datetime

import numpy as np

ETYPES = ["人", "群", "设备", "软件工具", "游戏", "渠道服务", "领域话题", "其他"]
FKINDS = ["知识", "经验", "事件", "观点", "偏好", "个人情况", "关系"]

SCHEMA = """
create table if not exists kv(k text primary key, v text);
create table if not exists entities(
  id text primary key, type text, name text, aliases text default '[]', descr text default '',
  profile text default '', scope text default '', created real, updated real, emb blob);
create table if not exists facts(
  id integer primary key autoincrement, subj text, pred text, obj text, statement text,
  kind text, status text, privacy text, scope text, valid_from real, invalid_at real,
  superseded_by integer, confirms integer default 1, sources text, created real, emb blob);
create table if not exists episodes(
  id integer primary key autoincrement, gid text, first_id integer, last_id integer,
  t0 real, t1 real, title text, summary text, people text, ents text, emb blob);
create table if not exists digests(gid text, period text, key text, body text, updated real, primary key(gid, period, key));
create table if not exists evalset(id integer primary key autoincrement, episode integer, q text, a text, created real);
create index if not exists f_subj on facts(subj);
create index if not exists f_obj on facts(obj);
create index if not exists e_name on entities(name);
create index if not exists ep_gid on episodes(gid, t0);
create virtual table if not exists facts_fts using fts5(statement, content='facts', content_rowid='id', tokenize='trigram');
create virtual table if not exists eps_fts using fts5(title, summary, content='episodes', content_rowid='id', tokenize='trigram');
create trigger if not exists f_ai after insert on facts begin insert into facts_fts(rowid, statement) values (new.id, new.statement); end;
create trigger if not exists f_au after update of statement on facts begin
  insert into facts_fts(facts_fts, rowid, statement) values('delete', old.id, old.statement);
  insert into facts_fts(rowid, statement) values (new.id, new.statement); end;
create trigger if not exists f_ad after delete on facts begin insert into facts_fts(facts_fts, rowid, statement) values('delete', old.id, old.statement); end;
create trigger if not exists e_ai after insert on episodes begin insert into eps_fts(rowid, title, summary) values (new.id, new.title, new.summary); end;
create trigger if not exists e_ad after delete on episodes begin insert into eps_fts(eps_fts, rowid, title, summary) values('delete', old.id, old.title, old.summary); end;
"""

EXTRACT = """你在给「大肥鱼」（群里的 AI 群友，当前设定的成年角色人设）做专业的知识整理。下面是 QQ 群「{gname}」的一段聊天，
每行：#序号 [时间] 名字(QQ号)：内容；标「大肥鱼(我)」的是她自己说的（她说的不一定对，只当上下文，不当事实来源）。
{chat}

这段里已经认识的人（QQ号：名字）：{people}
已有的部分实体（类型/名字）：{ents}

按知识工程的方法整理，只输出一个 JSON：
{{"episodes":[{{"title":"这一段在聊什么（15 字内）","summary":"谁、说了什么、结论或结果，2~4 句，人名写清楚","from":起始序号,"to":结束序号,"people":["参与者QQ号"],"topics":["涉及的实体名"]}}],
 "entities":[{{"name":"规范名（设备型号、软件名、游戏名、渠道、领域写全称）","type":"{etypes}","aliases":["别称、简称"],"desc":"一句话说明它是什么"}}],
 "facts":[{{"subject":"实体名，或者人写成 p:QQ号","predicate":"关系或属性（短，如 解锁需要/价格是/擅长/在用/推荐/踩坑/结论）","object":"实体名、p:QQ号 或具体值","statement":"一句能单独看懂的完整陈述，带上主语、条件、数字、版本，像「小米 17 Pro Max 申请解锁 BL 要等 7 天（主人实测）」","kind":"{fkinds}","status":"实测/说法/争议","privacy":"公开/私密","src":[支撑它的消息序号]}}],
 "people":[{{"uid":"QQ号","name":"名字","obs":["这段里新看出来的、关于这个人的具体信息：擅长/在用的设备/常聊的领域/说话风格/对大肥鱼的态度/他自己说的个人情况"],"src":[序号]}}]}}
规则：
1. 只写记录里真有的，不编，不加你自己的知识；没有就给空列表。
2. episodes 把这段按话题切开，覆盖有内容的部分；纯表情、刷屏可以跳过。
3. facts 要有信息量：技术结论、步骤、参数、版本、价格、实测结果、踩坑、谁擅长什么、谁用什么、发生了什么事；闲聊情绪和玩梗不算事实。
4. 个人情况（年龄、学校、工作、城市、身体、家庭、感情）privacy 写「私密」；别人的手机号、住址、证件号一律不写。
5. 不收：政治、色情、未成年相关、外挂作弊盗号、诈骗洗钱等违法门路。
6. 大肥鱼自己说的话不能当事实来源，但别人对她的态度、跟她发生的事可以写。"""

JUDGE = """下面每组是知识库里一条已有事实（旧）和一条新整理出的事实（新），判断它们的关系：
{pairs}
关系只能是：same（说的是同一件事，新的没有新信息）、update（同一件事但新的更新/更正了旧的，旧的该作废）、
contradict（同一件事说法互相矛盾，分不清谁对）、different（其实是两件事）。
只输出 JSON：{{"r":["same/update/contradict/different", ...]}}（顺序和组号一致）"""

PROFILE = """你在给「大肥鱼」维护群友的结构化档案。下面每个人有：现有档案（JSON，可能为空）、旧印象（以前写的一段话，可能为空）、这次新观察到的信息。
{items}
把每个人的档案合并更新（保留旧的有效信息，加上新的，矛盾的以新的为准），只输出 JSON：
{{"profiles":[{{"uid":"QQ号","p":{{"一句话":"这个人是谁（20 字内）","领域":["常聊/懂的领域"],"技能":["擅长什么"],"设备":["在用的设备、软件"],
"风格":"说话风格（30 字内）","和大肥鱼":"他怎么对她、关系怎样（30 字内）","个人情况":[{{"text":"他自己说的个人情况","privacy":"私密"}}],"原话":["最能代表他的 1~3 句原话"]}}}}]}}
规则：只写观察里真有的；数组每项尽量短，每个数组最多 8 项，按重要程度排、最重要的放前面，旧的不重要的就合并或删掉；个人情况只写本人说的，别人的手机号住址不写；不收政治、色情、未成年相关。"""

DIGEST = """下面是 QQ 群「{gname}」{label}的话题段摘要（按时间）：
{eps}
写一份{label}小结（4~8 句）：聊了哪些主要话题、得出了什么结论或有用信息、发生了什么值得记的事、谁比较活跃。只写上面有的。只输出 JSON：{{"body":"..."}}"""

QGEN = """下面是 QQ 群「{gname}」里的一段聊天：
{chat}
假设一周后有群友想起这段，会怎么问大肥鱼？出 1 个具体的问题（不要照抄原话，换个说法问），并给出依据这段的标准答案。
只输出 JSON：{{"q":"问题","a":"答案（1~2 句）"}}；这段没什么可问的就输出 {{"q":""}}"""


def _now():
    return time.time()


def _f32(v):
    return np.asarray(v, dtype=np.float32).tobytes() if v is not None else None


def _norm(s):
    return re.sub(r"\s+", "", str(s or "")).lower()


def _clip(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


class VecIndex:
    """内存里的向量矩阵（归一化后点积 = 余弦）。"""

    def __init__(self):
        self.ids, self.rows = [], []
        self.mat = None

    def add(self, i, v):
        if v is None:
            return
        a = np.frombuffer(v, dtype=np.float32) if isinstance(v, (bytes, bytearray)) else np.asarray(v, dtype=np.float32)
        n = np.linalg.norm(a)
        if not n:
            return
        self.ids.append(i)
        self.rows.append(a / n)
        self.mat = None

    def remove(self, i):
        if i in self.ids:
            k = self.ids.index(i)
            self.ids.pop(k)
            self.rows.pop(k)
            self.mat = None

    def top(self, q, k=20, allow=None):
        if not self.rows:
            return []
        if self.mat is None:
            self.mat = np.vstack(self.rows)
        qv = np.asarray(q, dtype=np.float32)
        qv = qv / (np.linalg.norm(qv) or 1)
        s = self.mat @ qv
        order = np.argsort(-s)
        out = []
        for j in order:
            i = self.ids[j]
            if allow is None or i in allow:
                out.append((i, float(s[j])))
                if len(out) >= k:
                    break
        return out


class KG:
    def __init__(self, path, raw_db, llm, embed, log=None, me=""):
        """raw_db: 知识库原文存储（有 q()）；llm(prompt) -> dict；embed(list[str]) -> list[vec]；me：大肥鱼自己的 QQ"""
        self.me = str(me or "")
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode=wal")
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.raw, self.llm, self.embed = raw_db, llm, embed
        self.log = log or (lambda *a: None)
        self.fvec, self.evec, self.pvec, self.ppl = VecIndex(), VecIndex(), VecIndex(), VecIndex()
        self.valid = set()
        self.lock = asyncio.Lock()
        self._ecache = {}  # 文本 -> 向量（本轮抽取预先批量算好）
        for r in self.db.execute("select id, emb, invalid_at from facts"):
            self.fvec.add(r["id"], r["emb"])
            if not r["invalid_at"]:
                self.valid.add(r["id"])
        for r in self.db.execute("select id, emb from episodes"):
            self.evec.add(r["id"], r["emb"])
        self.x("insert or ignore into entities(id,type,name,aliases,created,updated) values('self','人','大肥鱼','[\"肥鱼\",\"鱼\"]',?,?)",
               (_now(), _now()))
        self.ents = {}  # id -> (type, name, aliases)
        for r in self.db.execute("select id, type, name, aliases, emb from entities"):
            self.ents[r["id"]] = (r["type"], r["name"], json.loads(r["aliases"] or "[]"))
            if r["type"] != "人":
                self.pvec.add(r["id"], r["emb"])
            else:
                self.ppl.add(r["id"], r["emb"])

    # ---------- 小工具 ----------
    def q(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def x(self, sql, args=()):
        cur = self.db.execute(sql, args)
        self.db.commit()
        return cur

    def get(self, k, d=None):
        r = self.db.execute("select v from kv where k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else d

    def put(self, k, v):
        self.x("insert or replace into kv values(?,?)", (k, json.dumps(v, ensure_ascii=False)))

    async def _emb(self, texts):
        out = []
        for i in range(0, len(texts), 16):
            part = [_clip(t, 500) for t in texts[i:i + 16]]
            for attempt in range(4):
                try:
                    out += await self.embed(part)
                    break
                except Exception:
                    if attempt == 3:
                        raise
                    await asyncio.sleep(3 * (attempt + 1))
        return out

    async def _embc(self, texts):
        """先查预先算好的，缺的再算"""
        miss = [t for t in dict.fromkeys(texts) if t not in self._ecache]
        if miss:
            for t, v in zip(miss, await self._emb(miss)):
                self._ecache[t] = v
        return [self._ecache[t] for t in texts]

    def ename(self, eid):
        if eid in self.ents:
            return self.ents[eid][1]
        return eid[2:] if str(eid).startswith("v:") else str(eid)

    # ---------- 实体消歧 ----------
    async def _resolve(self, name, etype="其他", aliases=(), descr="", scope="", names=None):
        name = str(name or "").strip()
        if not name:
            return None
        if name in (f"p:{self.me}", self.me, "大肥鱼", "大肥鱼(我)", "肥鱼", "我"):
            return "self"  # 她自己只有一个实体
        if name.startswith("p:"):
            uid = name[2:].strip()
            return await self._person(uid, (names or {}).get(uid, uid), scope) if uid.isdigit() else None
        n = _norm(name)
        for eid, (t, nm, al) in self.ents.items():
            if t != "人" and (n == _norm(nm) or n in {_norm(a) for a in al}):
                self._add_alias(eid, [name, *aliases])
                return eid
        etype = etype if etype in ETYPES else "其他"
        key = f"{name}：{descr}"
        v = self._ecache.get(key) or (await self._emb([key]))[0]
        for eid, s in self.pvec.top(v, 3):
            if s >= 0.90 and self.ents.get(eid, ("",))[0] == etype:
                self._add_alias(eid, [name, *aliases])
                return eid
        eid = f"e:{etype}:{name}"
        if eid in self.ents:
            return eid
        self.x("insert or ignore into entities(id,type,name,aliases,descr,scope,created,updated,emb) values(?,?,?,?,?,?,?,?,?)",
               (eid, etype, name, json.dumps(list(dict.fromkeys(a for a in aliases if a)), ensure_ascii=False),
                _clip(descr, 120), scope, _now(), _now(), _f32(v)))
        self.ents[eid] = (etype, name, list(aliases))
        self.pvec.add(eid, v)
        return eid

    def _add_alias(self, eid, names):
        t, nm, al = self.ents[eid]
        new = [a for a in names if a and _norm(a) != _norm(nm) and a not in al]
        if new:
            al = (al + new)[-12:]
            self.ents[eid] = (t, nm, al)
            self.x("update entities set aliases=?, updated=? where id=?", (json.dumps(al, ensure_ascii=False), _now(), eid))

    async def _person(self, uid, name, scope=""):
        eid = f"p:{uid}"
        if eid not in self.ents:
            self.x("insert or ignore into entities(id,type,name,aliases,scope,created,updated) values(?,?,?,?,?,?,?)",
                   (eid, "人", name, "[]", scope, _now(), _now()))
            self.ents[eid] = ("人", name, [])
        elif name and name != self.ents[eid][1] and not name.isdigit():
            old = self.ents[eid][1]
            self.x("update entities set name=?, updated=? where id=?", (name, _now(), eid))
            self.ents[eid] = ("人", name, self.ents[eid][2])
            self._add_alias(eid, [old])
        return eid

    # ---------- 抽取 + 整合：一段聊天 ----------
    async def extract(self, gid, gname, rows, forgot=(), me=""):
        """rows：raw 表的行（按时间）。只做抽取和预算向量，返回给 commit 的上下文。"""
        rows = [r for r in rows if str(r["uid"]) not in forgot]
        if len(rows) < 5:
            return None
        idx = {i + 1: r for i, r in enumerate(rows)}
        names = {str(r["uid"]): r["uname"] for r in rows if not r["self"]}
        chat = "\n".join(f"#{i} [{datetime.fromtimestamp(r['ts']).strftime('%m-%d %H:%M')}] "
                         f"{'大肥鱼(我)' if r['self'] else r['uname']}({'' if r['self'] else r['uid']})：{_clip(r['text'], 300)}"
                         for i, r in idx.items())
        ents = "、".join(f"{t}/{nm}" for t, nm, _ in list(self.ents.values())[-150:] if t != "人") or "（还没有）"
        people = "、".join(f"{u}：{n}" for u, n in names.items())
        res = await self.llm(EXTRACT.format(gname=gname, chat=chat, people=people, ents=ents,
                                            etypes="/".join(ETYPES), fkinds="/".join(FKINDS)))
        # 向量在排队前批量算好（入库那段要排队，别在里面一个个等网络）
        keys = []
        for e in res.get("entities") or []:
            if e.get("name"):
                keys.append(f"{str(e['name']).strip()}：{e.get('desc') or ''}")
        for e in res.get("episodes") or []:
            keys += [f"{str(t).strip()}：" for t in (e.get("topics") or [])[:8]]
            if e.get("title"):
                keys.append(f"{e['title']}：{e.get('summary', '')}")
        for f in res.get("facts") or []:
            keys += [f"{str(f.get('subject') or '').strip()}：", str(f.get("statement") or "")]
        keys = [k for k in dict.fromkeys(keys) if k.strip("：") and k not in self._ecache]
        if keys:
            for k, v in zip(keys, await self._emb(keys)):
                self._ecache[k] = v
        return (gid, rows, idx, names, res)

    async def ingest(self, gid, gname, rows, forgot=(), me=""):
        ctx = await self.extract(gid, gname, rows, forgot, me)
        return await self.commit(ctx) if ctx else {}

    async def commit(self, ctx):
        """按时间顺序入库（先抽取完的不能抢先，更新/过时判断要按先后）"""
        gid, rows, idx, names, res = ctx
        async with self.lock:  # 抽取可以并行，合并入库一次一段
            st = await self._integrate(gid, rows, idx, names, res)
        obs = [p for p in res.get("people") or [] if str(p.get("uid")) in names and p.get("obs")]
        if obs:  # 人物档案各管各的，不用排队
            await self._update_profiles(obs, names, gid)
            st["people"] = len(obs)
        if len(self._ecache) > 20000:
            self._ecache.clear()
        return st

    async def _integrate(self, gid, rows, idx, names, res):
        st = {"episodes": 0, "entities": 0, "facts_new": 0, "facts_dup": 0, "facts_upd": 0, "facts_conf": 0, "people": 0}

        def src_ids(lst):
            out = []
            for n in lst or []:
                try:
                    out.append(idx[int(n)]["id"])
                except Exception:
                    pass
            return out

        def src_ts(lst):
            ts = [idx[int(n)]["ts"] for n in (lst or []) if str(n).isdigit() and int(n) in idx]
            return max(ts) if ts else rows[-1]["ts"]

        # 实体
        for e in res.get("entities") or []:
            if await self._resolve(e.get("name"), e.get("type"), e.get("aliases") or [], e.get("desc") or "", gid, names):
                st["entities"] += 1
        # 话题段
        eps = [e for e in res.get("episodes") or [] if e.get("title")]
        if eps:
            vs = await self._embc([f"{e['title']}：{e.get('summary', '')}" for e in eps])
            for e, v in zip(eps, vs):
                a, b = e.get("from"), e.get("to")
                try:
                    a, b = int(a), int(b)
                except Exception:
                    a, b = 1, len(rows)
                a, b = max(1, min(a, len(rows))), max(1, min(b, len(rows)))
                ppl = [str(u) for u in e.get("people") or [] if str(u) in names]
                tids = [x for x in [await self._resolve(t, scope=gid, names=names) for t in (e.get("topics") or [])[:8]] if x]
                cur = self.x("insert into episodes(gid,first_id,last_id,t0,t1,title,summary,people,ents,emb) values(?,?,?,?,?,?,?,?,?,?)",
                             (gid, idx[a]["id"], idx[b]["id"], idx[a]["ts"], idx[b]["ts"], _clip(e["title"], 40),
                              _clip(e.get("summary", ""), 500), json.dumps(ppl), json.dumps(tids, ensure_ascii=False), _f32(v)))
                self.evec.add(cur.lastrowid, _f32(v))
                st["episodes"] += 1
        # 事实：去重 / 更新 / 冲突
        facts = [f for f in res.get("facts") or [] if str(f.get("statement") or "").strip()]
        if facts:
            vs = await self._embc([str(f["statement"]) for f in facts])
            pending, judge = [], []
            for f, v in zip(facts, vs):
                subj = await self._resolve(f.get("subject"), scope=gid, names=names)
                if not subj:
                    continue
                ob = str(f.get("object") or "").strip()
                obj = await self._resolve(ob, scope=gid, names=names) if (ob.startswith("p:") or any(
                    _norm(ob) == _norm(nm) for _, nm, _ in self.ents.values())) else (f"v:{_clip(ob, 60)}" if ob else "")
                rec = {"subj": subj, "pred": _clip(f.get("predicate") or "", 20), "obj": obj or "",
                       "statement": _clip(f["statement"], 300),
                       "kind": f.get("kind") if f.get("kind") in FKINDS else "知识",
                       "status": f.get("status") if f.get("status") in ("实测", "说法", "争议") else "说法",
                       "privacy": "私密" if f.get("privacy") == "私密" or f.get("kind") == "个人情况" else "公开",
                       "scope": gid, "valid_from": src_ts(f.get("src")), "sources": src_ids(f.get("src")), "v": v}
                near = self.fvec.top(v, 3, allow=self.valid)
                if near and near[0][1] >= 0.95:
                    self._confirm(near[0][0], rec)
                    st["facts_dup"] += 1
                elif near and near[0][1] >= 0.82:
                    judge.append((near[0][0], rec))
                else:
                    pending.append(rec)
            if judge:
                old = {r["id"]: r for r in self.q(f"select id, statement from facts where id in ({','.join(str(j[0]) for j in judge)})")}
                pairs = "\n".join(f"{i + 1}. 旧：{old[o]['statement']}\n   新：{r['statement']}" for i, (o, r) in enumerate(judge) if o in old)
                try:
                    verdict = (await self.llm(JUDGE.format(pairs=pairs))).get("r") or []
                except Exception:
                    verdict = []
                for i, (o, r) in enumerate(judge):
                    vd = verdict[i] if i < len(verdict) else "different"
                    if vd == "same":
                        self._confirm(o, r)
                        st["facts_dup"] += 1
                    elif vd == "update":
                        nid = self._insert_fact(r)
                        self.x("update facts set invalid_at=?, superseded_by=?, status=CASE WHEN status='争议' THEN status ELSE '过时' END where id=?",
                               (r["valid_from"], nid, o))
                        self.valid.discard(o)
                        st["facts_upd"] += 1
                    elif vd == "contradict":
                        r["status"] = "争议"
                        self._insert_fact(r)
                        self.x("update facts set status='争议' where id=?", (o,))
                        st["facts_conf"] += 1
                    else:
                        pending.append(r)
            for r in pending:
                self._insert_fact(r)
                st["facts_new"] += 1
        return st

    def _insert_fact(self, r):
        cur = self.x("insert into facts(subj,pred,obj,statement,kind,status,privacy,scope,valid_from,sources,created,emb) "
                     "values(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (r["subj"], r["pred"], r["obj"], r["statement"], r["kind"], r["status"], r["privacy"], r["scope"],
                      r["valid_from"], json.dumps(r["sources"]), _now(), _f32(r["v"])))
        self.fvec.add(cur.lastrowid, _f32(r["v"]))
        self.valid.add(cur.lastrowid)
        return cur.lastrowid

    def _confirm(self, fid, r):
        row = self.db.execute("select sources, status from facts where id=?", (fid,)).fetchone()
        if not row:
            return
        src = list(dict.fromkeys(json.loads(row["sources"] or "[]") + r["sources"]))[-40:]
        status = "实测" if r["status"] == "实测" and row["status"] == "说法" else row["status"]
        self.x("update facts set sources=?, confirms=confirms+1, status=? where id=?", (json.dumps(src), status, fid))

    async def _update_profiles(self, obs, names, gid, old_imp=None):
        items = []
        for p in obs[:12]:
            uid = str(p["uid"])
            eid = await self._person(uid, names.get(uid) or p.get("name") or uid, gid)
            cur = self.db.execute("select profile from entities where id=?", (eid,)).fetchone()
            old = (old_imp or {}).get(uid) or self._old_impression(uid)
            items.append(f"QQ {uid}（{names.get(uid) or p.get('name')}）\n现有档案：{(cur['profile'] if cur else '') or '{}'}\n"
                         f"旧印象：{_clip(old, 300) or '无'}\n新观察：{'；'.join(map(str, p['obs']))[:600]}")
        try:
            res = await self.llm(PROFILE.format(items="\n\n".join(items)))
        except Exception as e:
            self.log(f"档案合并失败：{e!r}")
            return
        done = []
        for pr in res.get("profiles") or []:
            uid = str(pr.get("uid") or "")
            if uid and isinstance(pr.get("p"), dict) and f"p:{uid}" in self.ents:
                for kk, cap in (("领域", 8), ("技能", 8), ("设备", 8), ("个人情况", 8), ("原话", 3)):
                    if isinstance(pr["p"].get(kk), list):
                        pr["p"][kk] = pr["p"][kk][:cap]
                self.x("update entities set profile=?, updated=? where id=?",
                       (json.dumps(pr["p"], ensure_ascii=False)[:3000], _now(), f"p:{uid}"))
                done.append(uid)
        # 档案也做向量，问「谁会…」「谁用…」时能找到人（只用公开字段）
        if done:
            texts = [self.profile_text(u, public_only=True) or self.ents[f"p:{u}"][1] for u in done]
            try:
                vs = await self._emb(texts)
                for u, v in zip(done, vs):
                    self.x("update entities set emb=? where id=?", (_f32(v), f"p:{u}"))
                    self.ppl.remove(f"p:{u}")
                    self.ppl.add(f"p:{u}", _f32(v))
            except Exception as e:
                self.log(f"档案向量失败：{e!r}")

    def _old_impression(self, uid):
        try:
            rows = self.raw.q("select body from items where kind='群友' and person=? order by updated desc limit 2", (uid,))
            return "；".join(r["body"] for r in rows)
        except Exception:
            return ""

    # ---------- 小结 ----------
    async def digest_due(self, gid, gname):
        days = self.q("select distinct date(t0,'unixepoch','localtime') d from episodes where gid=? order by d", (gid,))
        today = datetime.now().strftime("%Y-%m-%d")
        n = 0
        for d in [r["d"] for r in days]:
            if d == today:
                continue
            have = self.db.execute("select 1 from digests where gid=? and period='日' and key=?", (gid, d)).fetchone()
            if have:
                continue
            eps = self.q("select title, summary from episodes where gid=? and date(t0,'unixepoch','localtime')=? order by t0", (gid, d))
            if len(eps) < 2:
                continue
            res = await self.llm(DIGEST.format(gname=gname, label=f" {d} 这一天",
                                               eps="\n".join(f"- {e['title']}：{_clip(e['summary'], 200)}" for e in eps[:60])))
            if res.get("body"):
                self.x("insert or replace into digests values(?,?,?,?,?)", (gid, "日", d, _clip(res["body"], 800), _now()))
                n += 1
                wk = datetime.strptime(d, "%Y-%m-%d").strftime("%G-W%V")
                ds = self.q("select key, body from digests where gid=? and period='日' order by key", (gid,))
                ds = [x for x in ds if datetime.strptime(x["key"], "%Y-%m-%d").strftime("%G-W%V") == wk]
                if len(ds) >= 2:
                    w = await self.llm(DIGEST.format(gname=gname, label=f" {wk} 这一周",
                                                     eps="\n".join(f"- {x['key']}：{x['body']}" for x in ds)))
                    if w.get("body"):
                        self.x("insert or replace into digests values(?,?,?,?,?)", (gid, "周", wk, _clip(w["body"], 1000), _now()))
        return n

    # ---------- 检索：关键词 + 向量 + 实体图，RRF 融合 ----------
    def _mentioned(self, text):
        t = _norm(text)
        hits = []
        for eid, (typ, nm, al) in self.ents.items():
            for n in [nm, *al]:
                nn = _norm(n)
                if len(nn) >= 2 and nn in t:
                    hits.append((eid, len(nn)))
                    break
        hits.sort(key=lambda x: -x[1])
        return [h[0] for h in hits[:6]]

    STOP = re.compile(r"大肥鱼|肥鱼|上周|上次|上回|之前|那天|群里|有人|你还记得|记不记得|你当时|当时|来着|是什么|怎么回|你说|你觉得|为什么|为啥|哪个|哪两个|什么|怎么|的时候|那个|这个|一下|吗|呢|啊|吧")

    @classmethod
    def _fts_query(cls, text):
        """关键词检索只用有区分度的词：去掉称呼、时间词、疑问词，剩下 ≥3 字的片段"""
        t = cls.STOP.sub(" ", str(text or ""))
        segs = [s for s in re.split(r"[\s，。！？、,.!?；;：:（）()\[\]「」《》\"'“”]+", t) if len(s) >= 3]
        segs = sorted(dict.fromkeys(segs), key=len, reverse=True)[:6]
        return " OR ".join('"' + s.replace('"', "")[:30] + '"' for s in segs)

    async def search(self, text, scopes=None, viewer="", owner=False, k=6, k_ep=3, qv=None):
        text = str(text or "").strip()
        if not text:
            return {"facts": [], "episodes": [], "ents": [], "people": []}
        if qv is None:  # 聊天时最多等 8 秒向量（接口 p90 约 7 秒），超时就只用关键词和实体图
            try:
                qv = (await asyncio.wait_for(self.embed([text[:300]]), 8))[0]
            except Exception:
                qv = None
        scope_ok = (lambda s: True) if scopes is None else (lambda s: s in scopes)

        def allowed(r):
            if not scope_ok(r["scope"]):
                return False
            if r["privacy"] == "私密" and not owner and r["subj"] != f"p:{viewer}":
                return False
            return True

        ranks, strong = {}, set()  # strong：语义够近，或者关键词/实体命中——只有这些才算相关

        def vote(lst, w=1.0, mark=True):
            for rank, fid in enumerate(lst):
                ranks[fid] = ranks.get(fid, 0) + w / (60 + rank)
                if mark:
                    strong.add(fid)

        if qv is not None:
            vh = self.fvec.top(qv, 40, allow=self.valid)
            vote([i for i, s in vh if s >= 0.45], w=1.5, mark=False)
            strong.update(i for i, s in vh if s >= 0.55)
        fq = self._fts_query(text)
        if fq:
            try:
                vote([r["rowid"] for r in self.q("select rowid from facts_fts where facts_fts match ? order by rank limit 20", (fq,))], w=0.7)
            except Exception:
                pass
        ments = self._mentioned(text)
        if ments:
            ph = ",".join("?" * len(ments))
            rows = self.q(f"select id from facts where invalid_at is null and (subj in ({ph}) or obj in ({ph})) order by valid_from desc limit 40",
                          (*ments, *ments))
            vote([r["id"] for r in rows], 1.2)
        facts = []
        if ranks:
            ids = sorted(ranks, key=lambda i: -ranks[i])[:60]
            rows = {r["id"]: r for r in self.q(f"select * from facts where id in ({','.join(map(str, ids))})")}
            for i in ids:
                r = rows.get(i)
                if r and i in strong and not r["invalid_at"] and allowed(r):
                    facts.append(r)
                if len(facts) >= k:
                    break
        eranks, estrong = {}, set()
        if qv is not None:
            for rank, (i, s) in enumerate(self.evec.top(qv, 30)):
                if s >= 0.45:
                    eranks[i] = eranks.get(i, 0) + 1.5 / (60 + rank)
                if s >= 0.52:
                    estrong.add(i)
        if fq:
            try:
                for rank, r in enumerate(self.q("select rowid from eps_fts where eps_fts match ? order by rank limit 20", (fq,))):
                    eranks[r["rowid"]] = eranks.get(r["rowid"], 0) + 0.7 / (60 + rank)
                    estrong.add(r["rowid"])
            except Exception:
                pass
        eranks = {i: v for i, v in eranks.items() if i in estrong}
        # 人：名字被提到的 + 档案语义接近的（问「谁会刷机」「谁用小米」）
        people = [e[2:] for e in ments if e.startswith("p:")]
        if qv is not None:
            for eid, s in self.ppl.top(qv, 6):
                if s >= 0.55 and eid[2:] not in people:
                    people.append(eid[2:])
        if scopes is not None:  # 只说在这些群里说过话的人
            gs = [s for s in scopes if s and s != "web"]
            ph = ",".join("?" * len(gs))
            people = [u for u in people if gs and self.raw.q(f"select 1 from raw where uid=? and gid in ({ph}) limit 1", (u, *gs))]
        people = people[:3]
        eps = []
        if eranks:
            ids = sorted(eranks, key=lambda i: -eranks[i])[:20]
            rows = {r["id"]: r for r in self.q(f"select * from episodes where id in ({','.join(map(str, ids))})")}
            for i in ids:
                r = rows.get(i)
                if r and scope_ok(r["gid"]):
                    eps.append(r)
                if len(eps) >= k_ep:
                    break
        return {"facts": facts, "episodes": eps, "ents": ments, "people": people}

    def fact_line(self, r, gnames=None):
        when = datetime.fromtimestamp(r["valid_from"]).strftime("%m-%d") if r.get("valid_from") else ""
        where = (gnames or {}).get(r["scope"], "")
        tag = {"实测": "有人实测", "说法": "群友说法", "争议": "有争议"}.get(r["status"], r["status"])
        return f"{r['statement']}（{when}{('，' + where) if where else ''}，{tag}）"

    def profile_text(self, uid, viewer="", owner=False, public_only=False):
        r = self.db.execute("select name, profile from entities where id=?", (f"p:{uid}",)).fetchone()
        if not r or not r["profile"]:
            return ""
        try:
            p = json.loads(r["profile"])
        except Exception:
            return ""
        parts = []
        for k in ("一句话", "领域", "技能", "设备", "风格", "和大肥鱼"):
            v = p.get(k)
            if v:
                parts.append(f"{k}：{'、'.join(map(str, v[:5])) if isinstance(v, list) else v}")
        if public_only:
            pass
        elif (owner or viewer == str(uid)) and p.get("个人情况"):
            parts.append("他说过的个人情况：" + "；".join(str(x.get("text") if isinstance(x, dict) else x) for x in p["个人情况"][:5]))
        elif p.get("个人情况"):
            pub = [x.get("text") for x in p["个人情况"] if isinstance(x, dict) and x.get("privacy") == "公开"]
            if pub:
                parts.append("个人情况：" + "；".join(pub[:3]))
        if p.get("原话"):
            parts.append("原话：" + " / ".join(f"「{x}」" for x in p["原话"][:2]))
        return f"{r['name']}：" + "；".join(parts)

    # ---------- 忘记某人 ----------
    def forget(self, uid):
        eid = f"p:{uid}"
        ids = [r["id"] for r in self.q("select id from facts where subj=? or obj=?", (eid, eid))]
        for i in ids:
            self.fvec.remove(i)
            self.valid.discard(i)
        self.x("delete from facts where subj=? or obj=?", (eid, eid))
        self.x("delete from entities where id=?", (eid,))
        self.ents.pop(eid, None)

    # ---------- 质量检验 ----------
    async def build_eval(self, n=40):
        have = self.db.execute("select count(*) from evalset").fetchone()[0]
        eps = self.q("select * from episodes where last_id-first_id>=3 order by random() limit ?", (max(0, n - have) * 2,))
        made = 0
        for e in eps:
            if have + made >= n:
                break
            rows = self.raw.q("select uname, text, self from raw where gid=? and id between ? and ? order by ts limit 60",
                              (e["gid"], e["first_id"], e["last_id"]))
            if len(rows) < 3:
                continue
            chat = "\n".join(f"{'大肥鱼' if r['self'] else r['uname']}：{_clip(r['text'], 200)}" for r in rows)
            try:
                res = await self.llm(QGEN.format(gname=e["gid"], chat=chat))
            except Exception:
                continue
            if str(res.get("q") or "").strip():
                self.x("insert into evalset(episode,q,a,created) values(?,?,?,?)", (e["id"], res["q"], res.get("a", ""), _now()))
                made += 1
        return made

    async def run_eval(self, k=6):
        tests = self.q("select t.*, e.gid, e.first_id, e.last_id from evalset t join episodes e on e.id=t.episode")
        hit, rows = 0, []
        for t in tests:
            try:
                qv = (await self._emb([t["q"]]))[0]  # 自测带重试，不让网络慢拖低分数
            except Exception:
                qv = None
            r = await self.search(t["q"], scopes=None, owner=True, k=k, qv=qv)
            # 命中：返回的话题段和题目那段聊天的消息范围有重叠，或返回的事实出处落在那段里
            ok = any(e["gid"] == t["gid"] and e["first_id"] <= t["last_id"] and e["last_id"] >= t["first_id"] for e in r["episodes"]) or any(
                any(t["first_id"] <= s <= t["last_id"] for s in json.loads(f["sources"] or "[]")) for f in r["facts"])
            hit += ok
            rows.append({"q": t["q"], "ok": ok})
        rate = hit / len(tests) if tests else 0
        self.put("eval_last", {"t": _now(), "n": len(tests), "hit": hit, "rate": rate})
        return rate, rows

    def stats(self):
        g = lambda sql: self.db.execute(sql).fetchone()[0]
        return {"实体": g("select count(*) from entities where type!='人'"), "人物档案": g("select count(*) from entities where type='人' and profile!=''"),
                "事实": g("select count(*) from facts where invalid_at is null"), "过时": g("select count(*) from facts where invalid_at is not null"),
                "争议": g("select count(*) from facts where status='争议'"), "话题段": g("select count(*) from episodes"),
                "日小结": g("select count(*) from digests where period='日'"), "周小结": g("select count(*) from digests where period='周'")}
