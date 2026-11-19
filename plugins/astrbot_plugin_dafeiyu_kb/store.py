"""知识库的存储：一个 SQLite 文件，全文检索用 FTS5 的 trigram（中文按三字一组切）。

raw    所有群消息原文（永久保留），她自己说的 self=1
items  整理出来的知识：梗 / 话题 / 事件 / 群友 / 贴吧 / 推特 / B站 / 表情包；scope 是群号，刷到的东西是 web，表情包是 meme
memes  见过的表情包（按内容 md5 去重），收没收、为什么
forget 自己说过「别记我」的人：不再给他写印象
kv     各种进度
"""

import json
import re
import sqlite3
import threading
import time

SCHEMA = """
create table if not exists raw(id integer primary key, ts real, gid text, gname text, uid text, uname text,
                               text text, self int default 0, mid text unique);
create index if not exists raw_g on raw(gid, id);
create virtual table if not exists raw_fts using fts5(text, content='raw', content_rowid='id', tokenize='trigram');
create trigger if not exists raw_ai after insert on raw begin
  insert into raw_fts(rowid, text) values (new.id, new.text); end;

create table if not exists items(id integer primary key, kind text, scope text, key text unique, title text, body text,
                                 person text default '', ts real, updated real, meta text default '{}');
create index if not exists items_scope on items(scope, kind);
create virtual table if not exists items_fts using fts5(title, body, content='items', content_rowid='id', tokenize='trigram');
create trigger if not exists items_ai after insert on items begin
  insert into items_fts(rowid, title, body) values (new.id, new.title, new.body); end;
create trigger if not exists items_ad after delete on items begin
  insert into items_fts(items_fts, rowid, title, body) values ('delete', old.id, old.title, old.body); end;
create trigger if not exists items_au after update on items begin
  insert into items_fts(items_fts, rowid, title, body) values ('delete', old.id, old.title, old.body);
  insert into items_fts(rowid, title, body) values (new.id, new.title, new.body); end;

create table if not exists memes(md5 text primary key, status text, cat text, meaning text, text text, path text,
                                 gid text, uid text, uname text, first real, seen int default 1, reason text default '');
create table if not exists forget(uid text primary key, ts real);
create table if not exists kv(k text primary key, v text);
"""

CJK = re.compile(r"[一-鿿\w]+")


def grams(s, n):
    s = "".join(CJK.findall(str(s or "").lower()))
    return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else ({s} if s else set())


def overlap(q, text):
    """两段话有多像：二字组重合比例。"""
    a, b = grams(q, 2), grams(text, 2)
    return len(a & b) / (len(a) or 1)


class Store:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode=wal")
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---------- 小工具 ----------
    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def x(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    def get(self, k, default=None):
        r = self.q("select v from kv where k=?", (k,))
        return json.loads(r[0]["v"]) if r else default

    def put(self, k, v):
        self.x("insert into kv(k,v) values(?,?) on conflict(k) do update set v=excluded.v", (k, json.dumps(v, ensure_ascii=False)))

    # ---------- 原始消息 ----------
    def add_raw(self, ts, gid, gname, uid, uname, text, me, mid):
        if not text:
            return False
        try:
            self.x("insert into raw(ts,gid,gname,uid,uname,text,self,mid) values(?,?,?,?,?,?,?,?)",
                   (ts, str(gid), gname or "", str(uid), uname or "", text, 1 if me else 0, mid))
            return True
        except sqlite3.IntegrityError:
            return False

    def raw_after(self, gid, after_id, limit):
        return self.q("select * from raw where gid=? and id>? order by id limit ?", (str(gid), after_id, limit))

    def raw_count_after(self, gid, after_id):
        return self.q("select count(*) n, max(id) m from raw where gid=? and id>?", (str(gid), after_id))[0]

    def voice_samples(self, gid, n=24, skip_uids=()):
        """群友说过的短句（不含她自己），一半是最近的、一半从全部历史里随机抽，每次都不一样；
        每人最多 3 句，免得只学一个人。"""
        sql = "select uid, uname, text from raw where gid=? and self=0 and length(text) between 2 and 60 "
        rows = self.q(sql + "order by id desc limit 300", (str(gid),))[:n * 2]
        rows += self.q(sql + "order by random() limit 400", (str(gid),))
        out, per = [], {}
        for r in rows:
            t = re.sub(r"@\S*?\(\d+\)|@\d+", "", r["text"]).strip()
            if (len(t) < 2 or r["uid"] in skip_uids or t.startswith(("/", "#", "http"))
                    or re.search(r"https?://|\[CQ:|^\[(图片|表情包?|\w+)\]$|验证成功|欢迎.{0,6}加入|入群|撤回了", t)):
                continue
            if per.get(r["uid"], 0) >= 3 or any(t == o for _, o in out):
                continue
            per[r["uid"]] = per.get(r["uid"], 0) + 1
            out.append((r["uname"], t))
            if len(out) >= n:
                break
        return out

    def random_items(self, scope, kind, n):
        return self.q("select * from items where scope=? and kind=? order by random() limit ?", (str(scope), kind, n))

    def groups(self):
        return self.q("select gid, max(gname) gname, count(*) n, max(id) last from raw group by gid")

    def search_raw(self, query, gids=None, limit=12):
        terms = sorted(grams(query, 3), key=len, reverse=True)[:24]
        if not terms:
            return []
        match = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        sql = "select raw.* from raw_fts join raw on raw.id=raw_fts.rowid where raw_fts match ?"
        args = [match]
        if gids is not None:
            sql += f" and raw.gid in ({','.join('?' * len(gids))})"
            args += [str(g) for g in gids]
        rows = self.q(sql + " order by rank limit 200", args)
        rows.sort(key=lambda r: overlap(query, r["text"]), reverse=True)
        return rows[:limit]

    # ---------- 知识 ----------
    def upsert(self, kind, scope, key, title, body, person="", ts=None, meta=None):
        now = time.time()
        m = json.dumps(meta or {}, ensure_ascii=False)
        old = self.q("select id,title,body,meta from items where key=?", (key,))
        if old:
            o = old[0]
            if o["title"] == title and o["body"] == body and o["meta"] == m:
                return "same"
            self.x("update items set title=?, body=?, person=?, updated=?, meta=?, kind=?, scope=? where id=?",
                   (title, body, person, now, m, kind, str(scope), o["id"]))
            return "updated"
        self.x("insert into items(kind,scope,key,title,body,person,ts,updated,meta) values(?,?,?,?,?,?,?,?,?)",
               (kind, str(scope), key, title, body, person, ts or now, now, m))
        return "new"

    def item(self, key):
        r = self.q("select * from items where key=?", (key,))
        return r[0] if r else None

    def items_of(self, scope, kind, limit=200):
        return self.q("select * from items where scope=? and kind=? order by updated desc limit ?", (str(scope), kind, limit))

    def search_items(self, query, scopes, kinds=None, limit=8, exclude_flags=()):
        terms = sorted(grams(query, 3), key=len, reverse=True)[:24]
        rows = []
        if terms:
            match = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
            sql = (f"select items.* from items_fts join items on items.id=items_fts.rowid where items_fts match ? "
                   f"and items.scope in ({','.join('?' * len(scopes))})")
            args = [match] + [str(s) for s in scopes]
            if kinds:
                sql += f" and items.kind in ({','.join('?' * len(kinds))})"
                args += list(kinds)
            rows = self.q(sql + " order by rank limit 120", args)
        if len(grams(query, 3)) < 2:  # 两个字的词，trigram 搜不到，退回 LIKE
            word = "".join(CJK.findall(query))[:8]
            if word:
                sql = f"select * from items where scope in ({','.join('?' * len(scopes))}) and (title like ? or body like ?)"
                args = [str(s) for s in scopes] + [f"%{word}%", f"%{word}%"]
                if kinds:
                    sql += f" and kind in ({','.join('?' * len(kinds))})"
                    args += list(kinds)
                rows += self.q(sql + " limit 60", args)
        seen, out = set(), []
        for r in sorted(rows, key=lambda r: overlap(query, r["title"] + " " + r["body"]), reverse=True):
            if r["id"] in seen:
                continue
            meta = json.loads(r.get("meta") or "{}")
            if any(meta.get(f) for f in exclude_flags):
                continue
            seen.add(r["id"])
            out.append(r)
            if len(out) >= limit:
                break
        return out

    # ---------- 表情包 ----------
    def meme(self, md5):
        r = self.q("select * from memes where md5=?", (md5,))
        return r[0] if r else None

    def meme_seen(self, md5):
        self.x("update memes set seen=seen+1 where md5=?", (md5,))

    def meme_add(self, **kw):
        self.x("insert or ignore into memes(md5,status,cat,meaning,text,path,gid,uid,uname,first,reason) "
               "values(:md5,:status,:cat,:meaning,:text,:path,:gid,:uid,:uname,:first,:reason)", kw)

    # ---------- 别记我 ----------
    def forgotten(self):
        return {r["uid"] for r in self.q("select uid from forget")}

    def forget(self, uid):
        self.x("insert or replace into forget(uid,ts) values(?,?)", (str(uid), time.time()))
        return self.x("delete from items where kind='群友' and person=?", (str(uid),)).rowcount
