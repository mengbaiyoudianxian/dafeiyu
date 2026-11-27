import json
import sqlite3
import time


class Shelf:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS books (
                site TEXT, id TEXT, title TEXT, tags TEXT, author TEXT,
                next_page INTEGER DEFAULT 0, next_chapter INTEGER DEFAULT 0, state TEXT DEFAULT 'new',
                PRIMARY KEY (site, id)
            );
            CREATE TABLE IF NOT EXISTS diary (
                site TEXT, id TEXT, chapter INTEGER, first_page INTEGER, last_page INTEGER,
                note TEXT, reason TEXT, taste TEXT, verdict TEXT, created INTEGER,
                PRIMARY KEY (site, id, chapter, first_page)
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        """)

    def catalog(self, site, works):
        with self.db:
            self.db.executemany("""
                INSERT INTO books(site,id,title,tags,author) VALUES(?,?,?,?,?)
                ON CONFLICT(site,id) DO UPDATE SET title=excluded.title,
                tags=excluded.tags, author=excluded.author
            """, [(site, str(w['id']), w['title'], json.dumps(w.get('tags', []),
                  ensure_ascii=False), w.get('author') or '') for w in works])

    def book(self, site, work_id):
        row = self.db.execute('SELECT * FROM books WHERE site=? AND id=?',
                              (site, str(work_id))).fetchone()
        return dict(row) if row else None

    def continuing(self, site):
        return [dict(r) for r in self.db.execute("""
            SELECT b.* FROM books b JOIN diary d USING(site,id)
            WHERE b.site=? AND b.state IN ('continue','pause') GROUP BY b.id
            ORDER BY MAX(d.created) DESC LIMIT 4
        """, (site,))]

    def record(self, site, work_id, chapter, start, stop, result, chapter_ended, ended):
        verdict = result['verdict']
        state = 'finished' if ended and verdict != 'drop' else verdict
        with self.db:
            self.db.execute('INSERT INTO diary VALUES(?,?,?,?,?,?,?,?,?,?)', (
                site, str(work_id), chapter, start, stop - 1, result['note'], result['reason'],
                result['taste'], verdict, int(time.time())))
            self.db.execute('UPDATE books SET next_page=?,next_chapter=?,state=? WHERE site=? AND id=?',
                            (0 if chapter_ended else stop, chapter + int(chapter_ended),
                             state, site, str(work_id)))

    def notes(self, query='', limit=8):
        term = '%' + query.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
        return [dict(row) for row in self.db.execute("""
            SELECT d.*, b.title, b.author FROM diary d JOIN books b USING(site,id)
            WHERE (b.title || b.tags || b.author || d.note || d.reason || d.taste)
                LIKE ? ESCAPE '!'
            ORDER BY d.created DESC, d.rowid DESC LIMIT ?
        """, (term, limit))]

    def preferences(self):
        # A long book counts once so its page count cannot outweigh other books.
        weights = {}
        for book in self.db.execute("""
            SELECT b.tags,b.author,d.verdict FROM books b JOIN diary d USING(site,id)
            WHERE d.rowid=(SELECT MAX(last.rowid) FROM diary last
                           WHERE last.site=b.site AND last.id=b.id)
        """):
            tags = json.loads(book['tags'])
            labels = list(tags) if isinstance(tags, list) else [tags]
            if book['author']:
                labels.append('作者 ' + book['author'])
            for label in set(str(label) for label in labels if label):
                weights[label] = weights.get(label, 0) + {'continue': 1, 'pause': 0, 'drop': -1}[book['verdict']]
        return sorted(((k,v) for k,v in weights.items() if v),
                      key=lambda item: abs(item[1]), reverse=True)[:20]

    def get(self, key):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else ''

    def set(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, str(value)))
