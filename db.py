"""База агента (SQLite). Таблиці: users (люди), kv (прапорці й налаштування), events (що люди робили).
Додаткові модулі (наприклад, launch.py) створюють свої таблиці самі у своєму setup()."""
import sqlite3
import time
from contextlib import contextmanager

import config


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, name TEXT, username TEXT, src TEXT,
            joined INTEGER, last_seen INTEGER, blocked INTEGER DEFAULT 0)""")
        c.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS events (user_id INTEGER, ev TEXT, extra TEXT, at INTEGER)")
        c.execute("CREATE TABLE IF NOT EXISTS sources (user_id INTEGER, word TEXT, at INTEGER)")
        c.execute("""CREATE TABLE IF NOT EXISTS quiz_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            quiz TEXT, started INTEGER, finished INTEGER, result TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS ideas (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT,
            chat_id INTEGER, msg_id INTEGER, created INTEGER, done INTEGER DEFAULT 0)""")


def touch_user(uid, name, username, src=""):
    """Записує людину. Повертає True, якщо вона нова."""
    now = int(time.time())
    with conn() as c:
        row = c.execute("SELECT user_id FROM users WHERE user_id=?", (uid,)).fetchone()
        if row:
            c.execute("UPDATE users SET name=?, username=?, last_seen=?, blocked=0 WHERE user_id=?",
                      (name, username, now, uid))
            return False
        c.execute("INSERT INTO users (user_id, name, username, src, joined, last_seen) VALUES (?,?,?,?,?,?)",
                  (uid, name, username, src or "direct", now, now))
        return True


def user(uid):
    with conn() as c:
        r = c.execute("SELECT user_id, name, username, src, joined FROM users WHERE user_id=?", (uid,)).fetchone()
    return dict(r) if r else None


def recent_users(limit=30):
    """Останні люди: найновіші зверху."""
    with conn() as c:
        rows = c.execute("SELECT user_id, name, username, src, joined, blocked FROM users ORDER BY joined DESC LIMIT ?",
                         (limit,)).fetchall()
    return [dict(r) for r in rows]


def add_source(uid, word):
    """Звідки прийшла людина: слово з посилання ?start=СЛОВО і час (кожен перехід)."""
    with conn() as c:
        c.execute("INSERT INTO sources (user_id, word, at) VALUES (?,?,?)", (uid, word, int(time.time())))


def sources_stats(since_today, exclude=()):
    """{слово: (людей сьогодні, за 7 днів, усього)}; людей рахуємо без повторів."""
    week = int(time.time()) - 7 * 86400
    ex = ",".join(str(int(x)) for x in exclude) or "0"
    q = (f"SELECT word, COUNT(DISTINCT CASE WHEN at>=? THEN user_id END), "
         f"COUNT(DISTINCT CASE WHEN at>=? THEN user_id END), COUNT(DISTINCT user_id) "
         f"FROM sources WHERE user_id NOT IN ({ex}) GROUP BY word ORDER BY 4 DESC")
    with conn() as c:
        return {r[0]: (r[1], r[2], r[3]) for r in c.execute(q, (since_today, week)).fetchall()}


def quiz_start(uid, quiz):
    """Новий прохід тесту; повертає його номер."""
    with conn() as c:
        cur = c.execute("INSERT INTO quiz_runs (user_id, quiz, started) VALUES (?,?,?)", (uid, quiz, int(time.time())))
        return cur.lastrowid


def quiz_finish(run_id, result):
    with conn() as c:
        c.execute("UPDATE quiz_runs SET finished=?, result=? WHERE id=?", (int(time.time()), result, run_id))


def quiz_stats(exclude=()):
    """{тест: (почали людей, дійшли до кінця людей, {результат: людей})} без повторів."""
    ex = ",".join(str(int(x)) for x in exclude) or "0"
    out = {}
    with conn() as c:
        for quiz, started, done in c.execute(
                f"SELECT quiz, COUNT(DISTINCT user_id), COUNT(DISTINCT CASE WHEN finished IS NOT NULL THEN user_id END) "
                f"FROM quiz_runs WHERE user_id NOT IN ({ex}) GROUP BY quiz").fetchall():
            out[quiz] = (started, done, {})
        for quiz, result, n in c.execute(
                f"SELECT quiz, result, COUNT(DISTINCT user_id) FROM quiz_runs WHERE finished IS NOT NULL "
                f"AND user_id NOT IN ({ex}) GROUP BY quiz, result").fetchall():
            if quiz in out:
                out[quiz][2][result] = n
    return out


def idea_add(text, chat_id=0, msg_id=0):
    with conn() as c:
        c.execute("INSERT INTO ideas (text, chat_id, msg_id, created) VALUES (?,?,?,?)",
                  (text, chat_id, msg_id, int(time.time())))


def ideas_open():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM ideas WHERE done=0 ORDER BY id").fetchall()]


def idea(idea_id):
    with conn() as c:
        r = c.execute("SELECT * FROM ideas WHERE id=?", (idea_id,)).fetchone()
    return dict(r) if r else None


def idea_done(idea_id):
    with conn() as c:
        c.execute("UPDATE ideas SET done=1 WHERE id=?", (idea_id,))


def active_ids():
    with conn() as c:
        return [r[0] for r in c.execute("SELECT user_id FROM users WHERE COALESCE(blocked,0)=0").fetchall()]


def set_blocked(uid):
    with conn() as c:
        c.execute("UPDATE users SET blocked=1 WHERE user_id=?", (uid,))


def stats():
    week = int(time.time()) - 7 * 86400
    with conn() as c:
        total = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        new = c.execute("SELECT COUNT(*) FROM users WHERE joined>=?", (week,)).fetchone()[0]
        src = [tuple(r) for r in c.execute("SELECT src, COUNT(*) FROM users GROUP BY src ORDER BY 2 DESC LIMIT 10").fetchall()]
    return total, new, src


def kv(key, value=None):
    """kv("ключ") читає, kv("ключ", "значення") записує."""
    with conn() as c:
        if value is None:
            r = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return r[0] if r else None
        c.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?,?)", (key, str(value)))
        return value


def log_ev(uid, ev, extra=""):
    with conn() as c:
        c.execute("INSERT INTO events (user_id, ev, extra, at) VALUES (?,?,?,?)", (uid, ev, extra, int(time.time())))
