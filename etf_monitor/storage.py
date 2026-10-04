"""SQLite 存储：每日行情/份额 + 信号 + 元信息。线程安全（每操作独立连接, WAL）。"""
import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "etf.db"
_lock = threading.Lock()  # 只串行化写操作


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS daily (
        code       TEXT NOT NULL,
        date       TEXT NOT NULL,
        close      REAL,
        pct_chg    REAL,
        volume     REAL,
        amount     REAL,
        amount_est REAL,
        shares     REAL,
        scale      REAL,
        source     TEXT,
        PRIMARY KEY (code, date))""")
    c.execute("""CREATE TABLE IF NOT EXISTS signals (
        date       TEXT NOT NULL,
        rule       TEXT NOT NULL,
        level      TEXT,
        message    TEXT,
        evidence   TEXT,
        created_at TEXT,
        PRIMARY KEY (date, rule))""")
    c.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    return c


# ------------------------------------------------------------------ daily
def upsert_daily(rows):
    if not rows:
        return 0
    with _lock, _conn() as c:
        c.executemany(
            """INSERT INTO daily (code,date,close,pct_chg,volume,amount,amount_est,shares,scale,source)
               VALUES (:code,:date,:close,:pct_chg,:volume,:amount,:amount_est,:shares,:scale,:source)
               ON CONFLICT(code,date) DO UPDATE SET
                 close=COALESCE(excluded.close, daily.close),
                 pct_chg=COALESCE(excluded.pct_chg, daily.pct_chg),
                 volume=COALESCE(excluded.volume, daily.volume),
                 amount=COALESCE(excluded.amount, daily.amount),
                 amount_est=COALESCE(excluded.amount_est, daily.amount_est),
                 shares=COALESCE(excluded.shares, daily.shares),
                 scale=COALESCE(excluded.scale, daily.scale),
                 source=COALESCE(excluded.source, daily.source)""",
            rows)
        return len(rows)


def get_daily(code=None, start=None, end=None, limit=None, has_shares=False):
    q = "SELECT * FROM daily WHERE 1=1"
    args = []
    if code:
        q += " AND code=?"
        args.append(code)
    if start:
        q += " AND date>=?"
        args.append(start)
    if end:
        q += " AND date<=?"
        args.append(end)
    if has_shares:
        q += " AND shares IS NOT NULL"
    q += " ORDER BY date"
    rows = _conn().execute(q, args).fetchall()
    if limit:
        rows = rows[-int(limit):]
    return [dict(r) for r in rows]


# ----------------------------------------------------------------- signals
def upsert_signal(sig):
    with _lock, _conn() as c:
        c.execute(
            """INSERT INTO signals (date,rule,level,message,evidence,created_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(date,rule) DO UPDATE SET
                 level=excluded.level, message=excluded.message,
                 evidence=excluded.evidence, created_at=excluded.created_at""",
            (sig["date"], sig["rule"], sig["level"], sig["message"],
             json.dumps(sig.get("evidence", {}), ensure_ascii=False),
             dt.datetime.now().isoformat(timespec="seconds")))


def get_signals(limit=50):
    rows = _conn().execute(
        "SELECT * FROM signals ORDER BY date DESC, rule LIMIT ?", (int(limit),)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["evidence"] = json.loads(d["evidence"] or "{}")
        except json.JSONDecodeError:
            d["evidence"] = {}
        out.append(d)
    return out


# -------------------------------------------------------------------- meta
def get_meta(key, default=None):
    row = _conn().execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


def set_meta(key, value):
    with _lock, _conn() as c:
        c.execute("INSERT INTO meta (k,v) VALUES (?,?) "
                  "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, str(value)))
