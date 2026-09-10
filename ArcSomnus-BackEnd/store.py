"""somnus.db：表一次建全，功能后补。"""
import os
import sqlite3
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "somnus.db")
SOULS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "souls")


def load_soul(soul_id):
    """读魂：精确→前缀→包含，和 ViGil 同思路的轻量版。"""
    try:
        files = [f for f in os.listdir(SOULS_DIR)
                 if os.path.isfile(os.path.join(SOULS_DIR, f))]
    except Exception:
        return ""
    for f in files:
        if os.path.splitext(f)[0] == soul_id:
            return open(os.path.join(SOULS_DIR, f), encoding="utf-8", errors="replace").read()
    for f in files:
        if os.path.splitext(f)[0].startswith(soul_id) or soul_id in os.path.splitext(f)[0]:
            return open(os.path.join(SOULS_DIR, f), encoding="utf-8", errors="replace").read()
    return ""


def soul_ids():
    try:
        return sorted(os.path.splitext(f)[0] for f in os.listdir(SOULS_DIR)
                      if os.path.isfile(os.path.join(SOULS_DIR, f)))
    except Exception:
        return []


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    return conn


def init_db():
    conn = db()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("""CREATE TABLE IF NOT EXISTS events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        soul_id TEXT DEFAULT '', chat_file TEXT DEFAULT '', round_id TEXT DEFAULT '',
        summary TEXT DEFAULT '', status TEXT DEFAULT 'todo', created_at REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY, soul_id TEXT DEFAULT '',
        trigger TEXT DEFAULT '', status TEXT DEFAULT 'running', started_at REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS tool_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT DEFAULT '',
        tool TEXT DEFAULT '', args TEXT DEFAULT '', result TEXT DEFAULT '',
        status TEXT DEFAULT 'ok', created_at REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS letters (
        id INTEGER PRIMARY KEY AUTOINCREMENT, soul_id TEXT DEFAULT '',
        subject TEXT DEFAULT '', content TEXT DEFAULT '', summary TEXT DEFAULT '',
        status TEXT DEFAULT 'draft', run_id TEXT DEFAULT '', created_at REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS diary (
        id INTEGER PRIMARY KEY AUTOINCREMENT, soul_id TEXT DEFAULT '',
        content TEXT DEFAULT '', run_id TEXT DEFAULT '', created_at REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS wardrobe (
        id INTEGER PRIMARY KEY AUTOINCREMENT, soul_id TEXT DEFAULT '',
        name TEXT DEFAULT '', description TEXT DEFAULT '', ref_url TEXT DEFAULT '',
        occasions TEXT DEFAULT '', is_current INTEGER DEFAULT 0,
        locked INTEGER DEFAULT 0, created_at REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS context_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT, soul_id TEXT DEFAULT '',
        chat_file TEXT DEFAULT '', messages TEXT DEFAULT '', created_at REAL)""")
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_snap_chat ON context_snapshots (chat_file)")
    except Exception:
        pass
    conn.commit()
    conn.close()


def config_get(key, default=""):
    conn = db()
    try:
        row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row[0] if row else default
    finally:
        conn.close()


def config_set(key, value):
    conn = db()
    try:
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()


def now():
    return time.time()


def snapshot_upsert(chat_file, messages):
    import json
    conn = db()
    try:
        conn.execute("INSERT INTO context_snapshots (chat_file, messages, created_at)"
                     " VALUES (?,?,?)"
                     " ON CONFLICT (chat_file) DO UPDATE SET messages=excluded.messages,"
                     " created_at=excluded.created_at",
                     (chat_file or "_default_",
                       json.dumps((messages or [])[-100:], ensure_ascii=False), now()))
        # 顺手清 7 天前的旧快照
        conn.execute("DELETE FROM context_snapshots WHERE created_at < ?", (now() - 604800,))
        conn.commit()
    finally:
        conn.close()


def snapshot_latest(chat_file=""):
    import json
    conn = db()
    try:
        if chat_file:
            row = conn.execute("SELECT messages FROM context_snapshots WHERE chat_file=?",
                               (chat_file,)).fetchone()
        else:
            row = None
        if not row:
            row = conn.execute("SELECT messages FROM context_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return []
        return json.loads(row[0] or "[]")
    except Exception:
        return []
    finally:
        conn.close()


def event_add(soul_id, chat_file, summary):
    """每角色只留 1 条 todo：有就覆盖，没就新增。扫表消费标 done 后下一条才能进。"""
    conn = db()
    try:
        row = conn.execute("SELECT event_id FROM events WHERE soul_id=? AND status='todo'",
                           (soul_id,)).fetchone()
        if row:
            conn.execute("UPDATE events SET summary=?, chat_file=?, created_at=? WHERE event_id=?",
                         (summary[:500], chat_file or "", now(), row[0]))
            conn.commit()
            return row[0]
        cur = conn.execute("INSERT INTO events (soul_id, chat_file, summary, status, created_at)"
                           " VALUES (?,?,?,'todo',?)",
                           (soul_id, chat_file or "", summary[:500], now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def todo_events(soul_id, limit=1):
    conn = db()
    try:
        rows = conn.execute("SELECT event_id, summary, chat_file FROM events"
                            " WHERE soul_id=? AND status='todo' ORDER BY event_id LIMIT ?",
                            (soul_id, limit)).fetchall()
        return [{"event_id": r[0], "summary": r[1], "chat_file": r[2]} for r in rows]
    finally:
        conn.close()


def events_mark(run_id, event_ids, status="done"):
    if not event_ids:
        return
    conn = db()
    try:
        conn.execute("UPDATE events SET status=? WHERE event_id IN (%s)" % ",".join("?" * len(event_ids)),
                     [status] + list(event_ids))
        conn.commit()
    finally:
        conn.close()


def diary_add(soul_id, content, run_id=""):
    conn = db()
    try:
        conn.execute("INSERT INTO diary (soul_id, content, run_id, created_at) VALUES (?,?,?,?)",
                     (soul_id, content[:2000], run_id, now()))
        conn.commit()
    finally:
        conn.close()
