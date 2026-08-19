import sqlite3, json, secrets
from pathlib import Path
from datetime import datetime, timezone

DB=Path("data/app.db")
DB.parent.mkdir(parents=True,exist_ok=True)

def conn():
    c=sqlite3.connect(DB,timeout=30)
    c.row_factory=sqlite3.Row
    return c

def now(): return datetime.now(timezone.utc).isoformat()

def init_db():
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute('''CREATE TABLE IF NOT EXISTS users(
            telegram_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_seen TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS websites(
            id TEXT PRIMARY KEY,
            telegram_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            template_id TEXT NOT NULL,
            data_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS broadcasts(
            id TEXT PRIMARY KEY,
            admin_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            sent INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL)''')

def upsert_user(user):
    with conn() as c:
        c.execute('''INSERT INTO users VALUES(?,?,?,?,?,?)
        ON CONFLICT(telegram_id) DO UPDATE SET
        username=excluded.username,first_name=excluded.first_name,
        active=1,last_seen=excluded.last_seen''',
        (user.id,user.username or "",user.first_name or "",1,now(),now()))

def deactivate_user(tid):
    with conn() as c:c.execute("UPDATE users SET active=0 WHERE telegram_id=?",(tid,))

def get_users(active_only=True):
    with conn() as c:
        q="SELECT * FROM users"+(" WHERE active=1" if active_only else "")
        return [dict(x) for x in c.execute(q).fetchall()]

def create_website(tid,category,template_id,data):
    wid=secrets.token_hex(5).upper()
    with conn() as c:
        c.execute("INSERT INTO websites VALUES(?,?,?,?,?,?,?)",
            (wid,tid,category,template_id,json.dumps(data,ensure_ascii=False),now(),now()))
    return wid

def get_website(wid):
    with conn() as c:
        x=c.execute("SELECT * FROM websites WHERE id=?",(wid,)).fetchone()
        return dict(x) if x else None

def get_all_websites():
    with conn() as c:return [dict(x) for x in c.execute("SELECT * FROM websites ORDER BY created_at").fetchall()]

def get_user_websites(tid):
    with conn() as c:return [dict(x) for x in c.execute(
        "SELECT * FROM websites WHERE telegram_id=? ORDER BY created_at DESC",(tid,)).fetchall()]

def delete_website(wid,tid):
    with conn() as c:
        r=c.execute("DELETE FROM websites WHERE id=? AND telegram_id=?",(wid,tid))
        return r.rowcount>0

def stats():
    with conn() as c:
        return tuple(c.execute("SELECT COUNT(*),SUM(active), (SELECT COUNT(*) FROM websites) FROM users").fetchone())

def add_broadcast(admin_id,text):
    bid=secrets.token_hex(5).upper()
    with conn() as c:c.execute("INSERT INTO broadcasts VALUES(?,?,?,?,?,?)",(bid,admin_id,text,0,0,now()))
    return bid

def finish_broadcast(bid,sent,failed):
    with conn() as c:c.execute("UPDATE broadcasts SET sent=?,failed=? WHERE id=?",(sent,failed,bid))
