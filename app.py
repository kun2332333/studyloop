#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""拾知 StudyLoop —— 本地学习助手服务端（零外部依赖）。

启动：  python app.py            # http://127.0.0.1:8765
        python app.py --open     # 启动后自动打开浏览器
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import traceback
import urllib.parse
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
VENDOR = os.path.join(BASE_DIR, "vendor")
if os.path.isdir(VENDOR):
    sys.path.insert(0, VENDOR)

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from core import ai, analyze, db, extract, sched, stats  # noqa: E402

WEB_DIR = os.path.join(BASE_DIR, "web")
MAX_UPLOAD = 120 * 1024 * 1024

ROUTES: list = []


def route(method: str, pattern: str):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


# ======================================================================
# 卡片 / 队列
# ======================================================================

def card_row_to_dict(c) -> dict:
    d = dict(c)
    deck = db.q1("SELECT title, color FROM decks WHERE id=?", (d.get("deck_id"),))
    d["deck_title"] = deck["title"] if deck else ""
    d["deck_color"] = deck["color"] if deck else "#6C8CFF"
    if d.get("section_id"):
        s = db.q1("SELECT title, idx, image_path FROM sections WHERE id=?", (d["section_id"],))
        if s:
            d["section_title"] = s["title"]
            d["section_idx"] = s["idx"]
            d["section_image"] = s["image_path"]
    d["retention"] = sched.predicted_retention(d)
    ivs = sched.preview_intervals(
        d, db.num_setting("desired_retention", 0.9), db.num_setting("max_interval_days", 365))
    d["intervals"] = ivs
    return d


def insert_card(deck_id, section_id, kind, front, back, note="", image="", source="manual"):
    due = db.now_iso()
    cur = db.ex(
        "INSERT INTO cards(deck_id, section_id, kind, front, back, note, image_path, source, "
        "state, due, created_at) VALUES(?,?,?,?,?,?,?,?, 'new', ?, ?)",
        (deck_id, section_id, kind, front, back, note, image, source, due, due))
    return cur.lastrowid


def review_queue(limit: int = 30, deck_id: int | None = None) -> list[dict]:
    now = db.now_iso()
    today = db.today_str()
    new_limit = int(db.num_setting("daily_new_limit", 20))
    done_today = db.q1(
        "SELECT COUNT(*) c FROM reviews WHERE substr(ts,1,10)=? AND prev_state IN ('new')",
        (today,))["c"]
    allow_new = max(0, new_limit - done_today)

    cond = "suspended=0"
    args: list = []
    if deck_id:
        cond += " AND deck_id=?"
        args.append(deck_id)

    due = [dict(r) for r in db.q(
        f"SELECT * FROM cards WHERE {cond} AND state<>'new' AND due<=? ORDER BY due LIMIT ?",
        tuple(args) + (now, limit * 2))]

    new = []
    if allow_new > 0:
        new = [dict(r) for r in db.q(
            f"SELECT * FROM cards WHERE {cond} AND state='new' ORDER BY section_id, id LIMIT ?",
            tuple(args) + (allow_new,))]
        # 交错：不同讲义轮流出卡（interleaving，比整块刷同一份讲义更利于辨别与迁移）
        buckets: dict = {}
        for c in new:
            buckets.setdefault(c["deck_id"], []).append(c)
        new = []
        while any(buckets.values()):
            for k in list(buckets):
                if buckets[k]:
                    new.append(buckets[k].pop(0))

    ordered = due + new
    ordered = ordered[:limit]
    return [card_row_to_dict(c) for c in ordered]


# ======================================================================
# API：基础
# ======================================================================

@route("GET", r"/api/bootstrap")
def api_bootstrap(h, q, body):
    settings = db.settings_all()
    if settings.get("ai_key"):
        settings["ai_key"] = "******"
    return {
        "today": db.today_str(),
        "now": db.now_iso(),
        "settings": settings,
        "overview": stats.today_overview(),
        "decks": [dict(r) for r in db.q(
            "SELECT id, title, subject, color, page_count, source_type, exam_date, "
            "(SELECT COUNT(*) FROM cards c WHERE c.deck_id=decks.id) AS cards "
            "FROM decks WHERE archived=0 ORDER BY id")],
        "ai_ready": ai.status()["effective"] is not None,
        "ai_engine": ai.status()["effective"],
        "dsh_available": ai.status()["dsh"]["available"],
        "version": "1.3",
    }


@route("GET", r"/api/today")
def api_today(h, q, body):
    return stats.today_overview()


@route("GET", r"/api/settings")
def api_settings_get(h, q, body):
    s = db.settings_all()
    s["ai_key_set"] = bool(s.get("ai_key"))
    s["ai_key"] = "******" if s.get("ai_key") else ""
    return s


@route("PUT", r"/api/settings")
def api_settings_put(h, q, body):
    for k, v in (body or {}).items():
        if k == "ai_key" and v == "******":
            continue
        if k in db.DEFAULTS:
            db.set_setting(k, v)
    s = db.settings_all()
    s["ai_key"] = "******" if s.get("ai_key") else ""
    s["ai_key_set"] = bool(s.get("ai_key"))
    return {"ok": True, "settings": s}


# ======================================================================
# API：讲义
# ======================================================================

@route("GET", r"/api/decks")
def api_decks(h, q, body):
    rows = db.q(
        "SELECT d.*, "
        "(SELECT COUNT(*) FROM sections s WHERE s.deck_id=d.id) AS sections, "
        "(SELECT COUNT(*) FROM cards c WHERE c.deck_id=d.id) AS cards, "
        "(SELECT COUNT(*) FROM cards c WHERE c.deck_id=d.id AND c.state='new') AS new_cards, "
        "(SELECT COUNT(*) FROM cards c WHERE c.deck_id=d.id AND c.suspended=0 "
        " AND c.state<>'new' AND c.due<=?) AS due_cards "
        "FROM decks d WHERE d.archived=0 ORDER BY d.id DESC", (db.now_iso(),))
    out = []
    for r in rows:
        d = dict(r)
        d["retention"] = stats.actual_retention(90, d["id"])
        out.append(d)
    return out


@route("GET", r"/api/decks/(\d+)")
def api_deck_detail(h, q, body, deck_id):
    deck = db.one(db.q1("SELECT * FROM decks WHERE id=?", (deck_id,)))
    if not deck:
        raise ValueError("讲义不存在")
    deck["sections"] = [dict(r) for r in db.q(
        "SELECT s.*, (SELECT COUNT(*) FROM cards c WHERE c.section_id=s.id) AS cards "
        "FROM sections s WHERE s.deck_id=? ORDER BY idx", (deck_id,))]
    deck["stats"] = db.one(db.q1(
        "SELECT COUNT(*) n, SUM(CASE WHEN state='new' THEN 1 ELSE 0 END) new, "
        "SUM(CASE WHEN suspended=1 THEN 1 ELSE 0 END) susp FROM cards WHERE deck_id=?",
        (deck_id,)))
    deck["retention"] = stats.actual_retention(90, deck_id)
    return deck


@route("PATCH", r"/api/decks/(\d+)")
def api_deck_patch(h, q, body, deck_id):
    fields = ("title", "subject", "color", "exam_date", "archived")
    sets, args = [], []
    for f in fields:
        if f in (body or {}):
            sets.append(f"{f}=?")
            args.append(body[f])
    if sets:
        db.ex(f"UPDATE decks SET {','.join(sets)} WHERE id=?", tuple(args) + (deck_id,))
    return {"ok": True, "deck": db.one(db.q1("SELECT * FROM decks WHERE id=?", (deck_id,)))}


@route("DELETE", r"/api/decks/(\d+)")
def api_deck_delete(h, q, body, deck_id):
    db.ex("DELETE FROM cards WHERE deck_id=?", (deck_id,))
    db.ex("DELETE FROM sections WHERE deck_id=?", (deck_id,))
    db.ex("DELETE FROM decks WHERE id=?", (deck_id,))
    return {"ok": True}


@route("POST", r"/api/decks")
def api_deck_create(h, q, body):
    title = (body or {}).get("title") or "未命名讲义"
    cur = db.ex(
        "INSERT INTO decks(title, subject, source_type, color, exam_date, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (title, (body or {}).get("subject", ""), "manual",
         (body or {}).get("color", "#6C8CFF"), (body or {}).get("exam_date", ""), db.now_iso()))
    return {"ok": True, "id": cur.lastrowid}


@route("POST", r"/api/decks/upload")
def api_deck_upload(h, q, body):
    fields, files = body
    if not files:
        raise ValueError("没有收到文件")
    deck_ids = []
    for fname, blob in files:
        if len(blob) > MAX_UPLOAD:
            raise ValueError(f"{fname} 超过 120MB 上限")
        safe = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", os.path.basename(fname))[:80]
        if not safe:
            safe = "upload.bin"
        stored = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{safe}"
        path = os.path.join(db.UPLOAD_DIR, stored)
        with open(path, "wb") as f:
            f.write(blob)
        try:
            parsed = extract.extract(path, safe)
        except ValueError:
            try:
                os.remove(path)
            except OSError:
                pass
            raise
        title = fields.get("title") or os.path.splitext(safe)[0]
        subject = fields.get("subject", "")
        exam_date = fields.get("exam_date", "")
        colors = ["#6C8CFF", "#5FD3A6", "#F5A65B", "#E0719B", "#9B8CFF", "#4FC1E9"]
        color = colors[db.q1("SELECT COUNT(*) c FROM decks")["c"] % len(colors)]
        cur = db.ex(
            "INSERT INTO decks(title, subject, source_type, file_path, page_count, color, "
            "exam_date, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (title[:120], subject, parsed["source_type"], f"uploads/{stored}",
             parsed["page_count"], color, exam_date, db.now_iso()))
        deck_id = cur.lastrowid
        for s in parsed["sections"]:
            db.ex("INSERT INTO sections(deck_id, idx, title, text, image_path) VALUES(?,?,?,?,?)",
                  (deck_id, s["idx"], s["title"][:200], s["text"], s["image_path"]))
        deck_ids.append(deck_id)

    # 不再自动生成卡片：上传后由「AI 逐页讲解」按内容重要性出卡
    return {"ok": True, "deck_ids": deck_ids}


@route("POST", r"/api/decks/(\d+)/ai-cards")
def api_deck_ai_cards(h, q, body, deck_id):
    body = body or {}
    per = max(2, min(int(body.get("per_section", 4)), 10))
    max_sections = max(1, min(int(body.get("max_sections", 6)), 40))
    sections = [dict(r) for r in db.q(
        "SELECT * FROM sections WHERE deck_id=? AND length(text)>60 ORDER BY idx LIMIT ?",
        (deck_id, max_sections))]
    if not sections:
        raise ValueError("这份讲义没有足够的文字内容")
    created = 0
    for s in sections:
        try:
            cards = ai.make_cards(s["text"], s["title"], per)
        except ValueError as e:
            return {"ok": False, "error": str(e), "created": created}
        existing = {re.sub(r"\s+", "", r["front"]) for r in
                    db.q("SELECT front FROM cards WHERE section_id=?", (s["id"],))}
        for c in cards:
            if re.sub(r"\s+", "", c["front"]) in existing:
                continue
            insert_card(deck_id, s["id"], c["kind"], c["front"], c["back"], c["note"],
                        s.get("image_path", ""), "ai")
            created += 1
    return {"ok": True, "created": created}


# ======================================================================
# API：卡片
# ======================================================================

@route("GET", r"/api/cards")
def api_cards(h, q, body):
    where, args = ["1=1"], []
    if q.get("deck_id"):
        where.append("deck_id=?")
        args.append(int(q["deck_id"]))
    if q.get("state"):
        where.append("state=?")
        args.append(q["state"])
    if q.get("source"):
        where.append("source=?")
        args.append(q["source"])
    if q.get("q"):
        where.append("(front LIKE ? OR back LIKE ?)")
        args.extend([f"%{q['q']}%"] * 2)
    if q.get("suspended") in ("0", "1"):
        where.append("suspended=?")
        args.append(int(q["suspended"]))
    limit = min(int(q.get("limit", 100)), 500)
    rows = db.q(f"SELECT * FROM cards WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
                tuple(args) + (limit,))
    return [card_row_to_dict(dict(r)) for r in rows]


@route("POST", r"/api/cards")
def api_card_create(h, q, body):
    body = body or {}
    front = (body.get("front") or "").strip()
    if not front:
        raise ValueError("卡片正面不能为空")
    cid = insert_card(
        body.get("deck_id"), body.get("section_id"),
        body.get("kind", "basic"), front, (body.get("back") or "").strip(),
        (body.get("note") or "")[:1000], body.get("image_path", ""), "manual")
    return {"ok": True, "id": cid}


@route("PATCH", r"/api/cards/(\d+)")
def api_card_patch(h, q, body, card_id):
    body = body or {}
    sets, args = [], []
    for f in ("front", "back", "note", "image_path", "suspended", "kind", "deck_id",
              "section_id"):
        if f in body:
            sets.append(f"{f}=?")
            args.append(body[f])
    if "due" in body:
        sets.append("due=?")
        args.append(body["due"])
    if sets:
        db.ex(f"UPDATE cards SET {','.join(sets)} WHERE id=?", tuple(args) + (card_id,))
    return {"ok": True}


@route("DELETE", r"/api/cards/(\d+)")
def api_card_delete(h, q, body, card_id):
    db.ex("DELETE FROM cards WHERE id=?", (card_id,))
    return {"ok": True}


@route("POST", r"/api/cards/bulk")
def api_cards_bulk(h, q, body):
    body = body or {}
    ids = [int(i) for i in body.get("ids", [])][:1000]
    if not ids:
        return {"ok": True, "affected": 0}
    marks = ",".join("?" * len(ids))
    action = body.get("action")
    if action == "delete":
        db.ex(f"DELETE FROM cards WHERE id IN ({marks})", tuple(ids))
    elif action == "suspend":
        db.ex(f"UPDATE cards SET suspended=1 WHERE id IN ({marks})", tuple(ids))
    elif action == "unsuspend":
        db.ex(f"UPDATE cards SET suspended=0 WHERE id IN ({marks})", tuple(ids))
    elif action == "reset":
        db.ex(f"UPDATE cards SET state='new', stability=0, difficulty=0, reps=0, "
              f"lapses=0, due=? WHERE id IN ({marks})", (db.now_iso(),) + tuple(ids))
    return {"ok": True, "affected": len(ids)}


# ======================================================================
# API：复习
# ======================================================================

@route("GET", r"/api/review/queue")
def api_queue(h, q, body):
    deck_id = int(q["deck_id"]) if q.get("deck_id") else None
    limit = max(1, min(int(q.get("limit", 30)), 100))
    cards = review_queue(limit, deck_id)
    return {"cards": cards, "count": len(cards)}


@route("POST", r"/api/review")
def api_review_submit(h, q, body):
    body = body or {}
    card_id = int(body.get("card_id") or 0)
    rating = int(body.get("rating") or 0)
    if rating not in (1, 2, 3, 4):
        raise ValueError("评分必须是 1-4")
    card = db.q1("SELECT * FROM cards WHERE id=?", (card_id,))
    if not card:
        raise ValueError("卡片不存在")
    card = dict(card)
    prev_state = card["state"]
    prev_interval = 0.0
    if card["last_review"]:
        try:
            elapsed = (datetime.now() - datetime.strptime(
                card["last_review"][:19], "%Y-%m-%dT%H:%M:%S")).total_seconds() / 86400
            prev_interval = round(elapsed, 3)
        except Exception:
            pass

    res = sched.review(card, rating,
                       desired_retention=db.num_setting("desired_retention", 0.9),
                       max_interval=db.num_setting("max_interval_days", 365))
    lapses = (card["lapses"] or 0) + (1 if rating == 1 else 0)
    db.ex("UPDATE cards SET state=?, due=?, stability=?, difficulty=?, reps=reps+1, "
          "lapses=?, last_review=? WHERE id=?",
          (res["state"], res["due"], res["stability"], res["difficulty"], lapses,
           db.now_iso(), card_id))
    db.ex("INSERT INTO reviews(card_id, ts, rating, confidence, elapsed_ms, prev_interval, "
          "next_interval, prev_state, next_state) VALUES(?,?,?,?,?,?,?,?,?)",
          (card_id, db.now_iso(), rating, body.get("confidence"),
           int(body.get("elapsed_ms") or 0), prev_interval, res["interval_days"],
           prev_state, res["state"]))
    db.touch_review()

    remaining = db.q1(
        "SELECT COUNT(*) c FROM cards WHERE suspended=0 AND state<>'new' AND due<=?",
        (db.now_iso(),))["c"]
    return {
        "ok": True,
        "due": res["due"],
        "state": res["state"],
        "interval_days": res["interval_days"],
        "interval_text": sched.describe_interval(res["interval_days"]),
        "stability": res["stability"],
        "difficulty": res["difficulty"],
        "remaining_due": remaining,
    }


@route("GET", r"/api/review/preview")
def api_review_preview(h, q, body):
    card_id = int(q.get("card_id") or 0)
    card = db.q1("SELECT * FROM cards WHERE id=?", (card_id,))
    if not card:
        raise ValueError("卡片不存在")
    return sched.preview_intervals(dict(card), db.num_setting("desired_retention", 0.9),
                                   db.num_setting("max_interval_days", 365))


# ======================================================================
# API：打卡 / 专注
# ======================================================================

@route("POST", r"/api/checkin")
def api_checkin(h, q, body):
    body = body or {}
    today = db.today_str()
    minutes = int(body.get("minutes") or 0)
    note = (body.get("note") or "")[:500]
    mood = (body.get("mood") or "")[:16]
    row = db.q1("SELECT * FROM checkins WHERE date=?", (today,))
    if row:
        db.ex("UPDATE checkins SET minutes=minutes+?, note=?, mood=? WHERE date=?",
              (minutes, note, mood, today))
    else:
        db.ex("INSERT INTO checkins(date, minutes, reviews, note, mood, created_at) "
              "VALUES(?,?,0,?,?,?)", (today, minutes, note, mood, db.now_iso()))
    return {"ok": True, "streak": db.streak_info(), "today": stats.today_overview()}


@route("POST", r"/api/checkin/makeup")
def api_makeup(h, q, body):
    body = body or {}
    target = (body.get("date") or "").strip()
    if not target:
        target = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        t = datetime.strptime(target, "%Y-%m-%d").date()
    except ValueError as e:
        raise ValueError("日期格式应为 YYYY-MM-DD") from e
    today = datetime.now().date()
    if t >= today or (today - t).days > 3:
        raise ValueError("只能补签最近 3 天内、且不是今天的日期")
    key = target
    if db.q1("SELECT id FROM checkins WHERE date=?", (key,)):
        raise ValueError("这一天已经有记录")
    if key in db.studied_days():
        raise ValueError("这一天已经有学习记录")
    used = db.q1("SELECT COUNT(*) c FROM checkins WHERE makeup=1 AND date>=?",
                 ((today - timedelta(days=7)).strftime("%Y-%m-%d"),))["c"]
    if used >= 1:
        raise ValueError("本周的补签机会已经用掉了（每周 1 次，防止「全有或全无」心态）")
    db.ex("INSERT INTO checkins(date, minutes, reviews, note, mood, makeup, created_at) "
          "VALUES(?,?,0,?,?,1,?)", (key, int(body.get("minutes") or 10),
                                    (body.get("note") or "补签")[:200], "", db.now_iso()))
    return {"ok": True, "streak": db.streak_info()}


@route("POST", r"/api/sessions/start")
def api_session_start(h, q, body):
    body = body or {}
    cur = db.ex("INSERT INTO study_sessions(started_at, deck_id, kind) VALUES(?,?,?)",
                (db.now_iso(), body.get("deck_id"), body.get("kind", "focus")))
    return {"ok": True, "id": cur.lastrowid, "started_at": db.now_iso()}


@route("POST", r"/api/sessions/stop")
def api_session_stop(h, q, body):
    body = body or {}
    sid = int(body.get("id") or 0)
    minutes = float(body.get("minutes") or 0)
    row = db.q1("SELECT * FROM study_sessions WHERE id=?", (sid,))
    if not row:
        raise ValueError("专注记录不存在")
    db.ex("UPDATE study_sessions SET ended_at=?, minutes=? WHERE id=?",
          (db.now_iso(), minutes, sid))
    db.add_minutes(minutes, note="专注", mood="")
    return {"ok": True, "minutes": minutes, "streak": db.streak_info()}


# ======================================================================
# API：测验
# ======================================================================

@route("GET", r"/api/quiz")
def api_quiz(h, q, body):
    deck_id = int(q["deck_id"]) if q.get("deck_id") else None
    n = max(3, min(int(q.get("n", 10)), 30))
    items = stats.quiz_bank(deck_id, n)
    if not items:
        raise ValueError("卡片太少，先导入讲义或手动添加几张卡再来测验")
    return {"items": items, "deck_id": deck_id}


@route("POST", r"/api/quiz/log")
def api_quiz_log(h, q, body):
    body = body or {}
    db.ex("INSERT INTO quiz_logs(ts, deck_id, total, correct, detail) VALUES(?,?,?,?,?)",
          (db.now_iso(), body.get("deck_id"), int(body.get("total") or 0),
           int(body.get("correct") or 0), json.dumps(body.get("detail") or [], ensure_ascii=False)[:8000]))
    return {"ok": True}


# ======================================================================
# API：笔记 / 费曼
# ======================================================================

@route("GET", r"/api/notes")
def api_notes(h, q, body):
    where, args = "1=1", []
    if q.get("deck_id"):
        where += " AND deck_id=?"
        args.append(int(q["deck_id"]))
    if q.get("kind"):
        where += " AND kind=?"
        args.append(q["kind"])
    rows = db.q(f"SELECT * FROM notes WHERE {where} ORDER BY id DESC LIMIT 200", tuple(args))
    return [dict(r) for r in rows]


@route("POST", r"/api/notes")
def api_note_create(h, q, body):
    body = body or {}
    content = (body.get("content") or "").strip()
    if not content:
        raise ValueError("笔记内容不能为空")
    cur = db.ex("INSERT INTO notes(deck_id, section_id, kind, content, created_at) "
                "VALUES(?,?,?,?,?)",
                (body.get("deck_id"), body.get("section_id"), body.get("kind", "note"),
                 content[:8000], db.now_iso()))
    return {"ok": True, "id": cur.lastrowid}


@route("DELETE", r"/api/notes/(\d+)")
def api_note_delete(h, q, body, note_id):
    db.ex("DELETE FROM notes WHERE id=?", (note_id,))
    return {"ok": True}


# ======================================================================
# API：统计 / 计划 / AI / 导出
# ======================================================================

@route("GET", r"/api/stats")
def api_stats(h, q, body):
    return stats.stats(int(q.get("days", 120)))


@route("GET", r"/api/plan")
def api_plan(h, q, body):
    return stats.plan()


@route("GET", r"/api/ai/status")
def api_ai_status(h, q, body):
    return ai.status()


@route("GET", r"/api/ai/providers")
def api_ai_providers(h, q, body):
    st = ai.status()
    return {
        **st,
        "hint": "auto：优先直连 API，未配置时自动用本机 dsh；"
                "直连需要 DeepSeek 开放平台 Key；dsh 密钥由 dsh 自己管理，无需填写。",
    }


@route("POST", r"/api/ai/ask")
def api_ai_ask(h, q, body):
    body = body or {}
    question = (body.get("question") or "").strip()
    if not question:
        raise ValueError("问题不能为空")
    return ai.ask(question, body.get("deck_id"))


@route("POST", r"/api/ai/test")
def api_ai_test(h, q, body):
    body = body or {}
    mode = (body.get("mode") or "").strip()
    # 显式测某个引擎时临时切 mode 设置，测完复原（不改用户保存的选择）
    saved_mode = db.setting("ai_mode", "auto")
    if mode in ("api", "dsh", "auto"):
        db.set_setting("ai_mode", mode)
    try:
        out, engine = ai.complete("回复两个字：正常", temperature=0, max_tokens=20)
    finally:
        if mode in ("api", "dsh", "auto"):
            db.set_setting("ai_mode", saved_mode)
    return {"ok": True, "reply": out.strip()[:80], "engine": engine}


@route("GET", r"/api/decks/(\d+)/analysis")
def api_deck_analysis(h, q, body, deck_id):
    return {"analyses": analyze.list_analyses(int(deck_id))}


@route("POST", r"/api/decks/(\d+)/analyze")
def api_deck_analyze(h, q, body, deck_id):
    body = body or {}
    section_id = body.get("section_id")
    if section_id is None:
        raise ValueError("section_id 不能为空")
    max_cards = int(body.get("max_cards") or 3)
    return analyze.analyze_section(int(deck_id), int(section_id),
                                   max(1, min(3, max_cards)))


@route("POST", r"/api/decks/(\d+)/analysis/clear")
def api_deck_analysis_clear(h, q, body, deck_id):
    return analyze.clear_analyses(int(deck_id))


@route("POST", r"/api/decks/(\d+)/analysis/import")
def api_deck_analysis_import(h, q, body, deck_id):
    body = body or {}
    ids = body.get("analysis_ids") or []
    if not isinstance(ids, list) or not ids:
        raise ValueError("没有选中的分析记录")
    return analyze.import_cards(int(deck_id), [int(i) for i in ids])


@route("GET", r"/api/export")
def api_export(h, q, body):
    dump = {}
    for table in ("decks", "sections", "cards", "reviews", "checkins", "study_sessions",
                  "notes", "quiz_logs", "ai_analyses"):
        dump[table] = [dict(r) for r in db.q(f"SELECT * FROM {table}")]
    dump["settings"] = {k: v for k, v in db.settings_all().items() if k != "ai_key"}
    dump["exported_at"] = db.now_iso()
    return dump


# ======================================================================
# 静态文件 & 服务
# ======================================================================

def serve_static(handler, path: str) -> bool:
    if path == "/":
        path = "/index.html"
    rel = urllib.parse.unquote(path.lstrip("/"))
    root = WEB_DIR
    if rel.startswith("media/"):
        root = db.DATA_DIR
    full = os.path.normpath(os.path.join(root, rel))
    if not full.startswith(os.path.normpath(root)):
        return False
    if not os.path.isfile(full):
        return False
    ctype, _ = mimetypes.guess_type(full)
    ctype = ctype or "application/octet-stream"
    with open(full, "rb") as f:
        data = f.read()
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(data)
    return True


def parse_multipart(body: bytes, content_type: str):
    m = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not m:
        raise ValueError("上传格式不正确（不是 multipart/form-data）")
    boundary = b"--" + m.group(1).encode()
    fields: dict = {}
    files: list = []
    for part in body.split(boundary):
        if not part or part.startswith(b"--"):
            continue
        part = part.lstrip(b"\r\n")
        if b"\r\n\r\n" not in part:
            continue
        head, data = part.split(b"\r\n\r\n", 1)
        if data.endswith(b"\r\n"):
            data = data[:-2]
        header = head.decode("utf-8", "replace")
        nm = re.search(r'name="([^"]*)"', header)
        if not nm:
            continue
        fn = re.search(r'filename="([^"]*)"', header)
        if fn and fn.group(1):
            files.append((fn.group(1), data))
        else:
            fields[nm.group(1)] = data.decode("utf-8", "replace")
    return fields, files


class Handler(BaseHTTPRequestHandler):
    server_version = "StudyLoop/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("STUDY_DEBUG"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- helpers ------------------------------------------------------
    def _send(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            # 客户端中途断开（例如 AI 分析等待太久点了刷新）是正常现象，静默处理
            pass

    def _dispatch(self, method: str):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        try:
            if method == "GET" and not path.startswith("/api/"):
                if serve_static(self, path):
                    return
                self._send({"error": "not found"}, 404)
                return

            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" in ctype:
                body = parse_multipart(raw, ctype)
            elif raw:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    body = {}
            else:
                body = {}

            for m, pattern, fn in ROUTES:
                if m != method:
                    continue
                match = pattern.match(path)
                if not match:
                    continue
                args = [int(g) if g.isdigit() else g for g in match.groups()]
                result = fn(self, query, body, *args)
                self._send(result)
                return
            self._send({"error": f"未知接口 {method} {path}"}, 404)
        except ValueError as e:
            self._send({"error": str(e)}, 400)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._send({"error": f"服务器内部错误：{e}"}, 500)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")


class StudyServer(ThreadingHTTPServer):
    """本地服务。

    Windows 上必须关掉 SO_REUSEADDR：它和 Linux 语义不同，会让两个进程
    同时绑到同一端口（第二个实例看似启动成功，实际请求随机落到其中一个）。
    关掉之后第二个实例会拿到 OSError，于是自动顺延到下一个端口。
    """

    allow_reuse_address = False
    daemon_threads = True

    def handle_error(self, request, client_address):
        """客户端断开导致的异常很常见（长时间 AI 请求中用户刷新页面），不打印吓人堆栈。"""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError,
                            ConnectionAbortedError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def _open_browser(url: str) -> None:
    """打开浏览器；失败也不影响服务本身，提示手动访问即可。"""
    try:
        webbrowser.open(url)
    except Exception:
        print(f"  （没能自动打开浏览器，请手动访问 {url}）")


def main():
    ap = argparse.ArgumentParser(description="拾知 StudyLoop 本地学习助手")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    db.init_db()

    # 端口被占用时自动顺延，避免「双击后一闪就没了」
    server, port = None, args.port
    for p in range(args.port, args.port + 20):
        try:
            server = StudyServer((args.host, p), Handler)
            port = p
            break
        except OSError:
            continue
    if server is None:
        print(f"启动失败：{args.port}~{args.port + 19} 这些端口都被占用了。")
        print(f"可以手动指定端口：python app.py --port 9000")
        sys.exit(1)

    server.daemon_threads = True
    url = f"http://{args.host}:{port}"
    print("=" * 60)
    print("  拾知 StudyLoop · 本地学习助手")
    if port != args.port:
        print(f"  （{args.port} 端口被占用，已自动改用 {port}）")
    print(f"  打开这个地址：{url}")
    print(f"  数据目录：{db.DATA_DIR}")
    print("  这个窗口要留着，关掉就等于退出软件；按 Ctrl+C 也可以停止。")
    print("=" * 60)
    if args.open and not args.no_open:
        threading.Timer(1.2, lambda: _open_browser(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("\n已停止。你的数据都保存在 data/ 目录里，下次打开还在。")


if __name__ == "__main__":
    main()