"""SQLite 数据层：连接、建表、通用查询工具。

设计原则：
- 全部使用本地 SQLite，单文件，便于备份 / 导出。
- 时间统一存为本地 ISO 字符串（'YYYY-MM-DDTHH:MM:SS'），可直接字符串比较排序。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "study.db")
MEDIA_DIR = os.path.join(DATA_DIR, "media")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS decks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    subject     TEXT DEFAULT '',
    source_type TEXT DEFAULT 'manual',   -- pptx | pdf | manual
    file_path   TEXT DEFAULT '',
    page_count  INTEGER DEFAULT 0,
    color       TEXT DEFAULT '#6C8CFF',
    exam_date   TEXT DEFAULT '',
    goal_date   TEXT DEFAULT '',
    archived    INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sections (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id    INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    title      TEXT DEFAULT '',
    text       TEXT DEFAULT '',
    image_path TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sections_deck ON sections(deck_id, idx);

CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id     INTEGER REFERENCES decks(id) ON DELETE CASCADE,
    section_id  INTEGER REFERENCES sections(id) ON DELETE SET NULL,
    kind        TEXT DEFAULT 'basic',     -- basic | cloze | image
    front       TEXT NOT NULL,
    back        TEXT DEFAULT '',
    note        TEXT DEFAULT '',          -- 自己的解释（精细加工）
    image_path  TEXT DEFAULT '',
    source      TEXT DEFAULT 'manual',    -- manual | auto | ai
    state       TEXT DEFAULT 'new',       -- new | learning | review | relearning
    due         TEXT NOT NULL,
    stability   REAL DEFAULT 0,
    difficulty  REAL DEFAULT 0,
    reps        INTEGER DEFAULT 0,
    lapses      INTEGER DEFAULT 0,
    last_review TEXT DEFAULT '',
    suspended   INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cards_due ON cards(suspended, due);
CREATE INDEX IF NOT EXISTS idx_cards_deck ON cards(deck_id);

CREATE TABLE IF NOT EXISTS reviews (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id       INTEGER NOT NULL,
    ts            TEXT NOT NULL,
    rating        INTEGER NOT NULL,
    confidence    INTEGER,              -- 1 不会 / 2 模糊 / 3 确定（元认知校准）
    elapsed_ms    INTEGER DEFAULT 0,
    prev_interval REAL DEFAULT 0,
    next_interval REAL DEFAULT 0,
    prev_state    TEXT,
    next_state    TEXT
);
CREATE INDEX IF NOT EXISTS idx_reviews_ts ON reviews(ts);

CREATE TABLE IF NOT EXISTS checkins (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL UNIQUE,
    minutes    INTEGER DEFAULT 0,
    reviews    INTEGER DEFAULT 0,
    note       TEXT DEFAULT '',
    mood       TEXT DEFAULT '',
    makeup     INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS study_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    minutes    REAL DEFAULT 0,
    deck_id    INTEGER,
    kind       TEXT DEFAULT 'focus',
    note       TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id    INTEGER,
    section_id INTEGER,
    kind       TEXT DEFAULT 'note',     -- note | feynman
    content    TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quiz_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    deck_id    INTEGER,
    total      INTEGER DEFAULT 0,
    correct    INTEGER DEFAULT 0,
    detail     TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ai_analyses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id       INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    section_id    INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    engine        TEXT DEFAULT '',
    worth         TEXT DEFAULT 'mid',       -- high | mid | low
    reason        TEXT DEFAULT '',
    summary       TEXT DEFAULT '',
    concepts_json TEXT DEFAULT '[]',
    cards_json    TEXT DEFAULT '[]',
    imported      INTEGER DEFAULT 0,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

DEFAULTS = {
    "daily_new_limit": "20",          # 每日新卡上限（认知负荷控制）
    "daily_goal_minutes": "30",       # 每日目标时长
    "desired_retention": "0.9",       # 目标记忆保持率
    "max_interval_days": "365",       # 最长间隔
    "ai_base": "",
    "ai_key": "",
    "ai_model": "deepseek-chat",
    "ai_mode": "auto",                 # auto | api | dsh | off
    "sleep_hint": "1",                # 睡前巩固提醒
    "user_name": "",
}


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def get_conn() -> sqlite3.Connection:
    if not os.path.isdir(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    for d in (DATA_DIR, MEDIA_DIR, UPLOAD_DIR):
        os.makedirs(d, exist_ok=True)
    conn = get_conn()
    conn.executescript(SCHEMA)
    for k, v in DEFAULTS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
    conn.commit()


def q(sql: str, args=()) -> list[sqlite3.Row]:
    return get_conn().execute(sql, args).fetchall()


def q1(sql: str, args=()):
    return get_conn().execute(sql, args).fetchone()


def ex(sql: str, args=()):
    conn = get_conn()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur


def one(row) -> dict | None:
    return dict(row) if row is not None else None


def settings_all() -> dict:
    out = dict(DEFAULTS)
    for r in q("SELECT key, value FROM settings"):
        out[r["key"]] = r["value"]
    return out


def setting(key: str, default=None):
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else DEFAULTS.get(key, default)


def set_setting(key: str, value) -> None:
    ex("INSERT INTO settings(key, value) VALUES(?, ?) "
       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def num_setting(key: str, default: float) -> float:
    try:
        return float(setting(key, default))
    except (TypeError, ValueError):
        return default


# ---------- 打卡 / 连续天数 ----------

def _day(d: str) -> datetime:
    return datetime.strptime(d, "%Y-%m-%d")


def studied_days(limit: int = 400) -> set[str]:
    """有学习记录的日期集合：打卡 / 复习 / 专注时段 任一即可。"""
    days: set[str] = set()
    for r in q("SELECT date FROM checkins"):
        days.add(r["date"])
    for r in q("SELECT DISTINCT substr(ts,1,10) AS d FROM reviews"):
        days.add(r["d"])
    for r in q("SELECT DISTINCT substr(started_at,1,10) AS d FROM study_sessions WHERE minutes > 0"):
        days.add(r["d"])
    return days


def streak_info() -> dict:
    days = sorted(studied_days())
    day_set = set(days)
    today = today_str()
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    anchor = today if today in day_set else (yesterday if yesterday in day_set else None)
    streak = 0
    if anchor:
        cur = _day(anchor)
        while cur.strftime("%Y-%m-%d") in day_set:
            streak += 1
            cur -= timedelta(days=1)
    best = 0
    run = 0
    prev = None
    for d in days:
        dt = _day(d)
        run = run + 1 if prev and (dt - prev).days == 1 else 1
        best = max(best, run)
        prev = dt
    return {
        "streak": streak,
        "best": best,
        "studied_today": today in day_set,
        "anchor": anchor,
        "total_days": len(days),
        "days": days,
    }


def touch_review() -> None:
    """每复习一张卡就记一笔（自动打卡：不需要额外操作也能留下学习痕迹）。"""
    d = today_str()
    if q1("SELECT id FROM checkins WHERE date=?", (d,)):
        ex("UPDATE checkins SET reviews=reviews+1 WHERE date=?", (d,))
    else:
        ex("INSERT INTO checkins(date, minutes, reviews, created_at) VALUES(?,0,1,?)",
           (d, now_iso()))


def add_minutes(minutes: float, reviews: int = 0, note: str = "", mood: str = "") -> None:
    """把学习量累加到今天的打卡记录（自动打卡：有学习行为即计入）。"""
    d = today_str()
    mins = int(round(minutes))
    row = q1("SELECT id, minutes, reviews FROM checkins WHERE date=?", (d,))
    if row:
        ex("UPDATE checkins SET minutes=?, reviews=? WHERE date=?",
           (row["minutes"] + mins, row["reviews"] + reviews, d))
    else:
        ex("INSERT INTO checkins(date, minutes, reviews, note, mood, created_at) VALUES(?,?,?,?,?,?)",
           (d, mins, reviews, note, mood, now_iso()))
    if note or mood:
        ex("UPDATE checkins SET note=CASE WHEN ?<>'' THEN ? ELSE note END, "
           "mood=CASE WHEN ?<>'' THEN ? ELSE mood END WHERE date=?", (note, note, mood, mood, d))


# ---------- AI 逐页讲解 ----------

def save_analysis(deck_id: int, section_id: int, engine: str, worth: str,
                  reason: str, summary: str, concepts_json: str,
                  cards_json: str) -> int:
    cur = ex("INSERT INTO ai_analyses(deck_id, section_id, engine, worth, reason, "
             "summary, concepts_json, cards_json, created_at) "
             "VALUES(?,?,?,?,?,?,?,?,?)",
             (deck_id, section_id, engine, worth, reason, summary,
              concepts_json, cards_json, now_iso()))
    return int(cur.lastrowid)


def analyses_for_deck(deck_id: int) -> list[dict]:
    return [dict(r) for r in q(
        "SELECT a.*, s.idx AS section_idx, s.title AS section_title "
        "FROM ai_analyses a JOIN sections s ON s.id=a.section_id "
        "WHERE a.deck_id=? ORDER BY s.idx, a.id DESC", (deck_id,))]


def analysis_cards(analysis_id: int) -> tuple[int, int, list[dict]]:
    """返回 (deck_id, section_id, cards_json 解析结果)。"""
    row = q1("SELECT deck_id, section_id, cards_json FROM ai_analyses WHERE id=?",
             (analysis_id,))
    if not row:
        raise ValueError("找不到该分析记录")
    cards = json.loads(row["cards_json"] or "[]")
    if not isinstance(cards, list):
        cards = []
    return int(row["deck_id"]), int(row["section_id"]), cards


def mark_imported(analysis_id: int, n: int) -> None:
    ex("UPDATE ai_analyses SET imported=imported+? WHERE id=?", (n, analysis_id))