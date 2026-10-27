"""学习分析：记忆保留、打卡热力图、元认知校准、每日计划与预测。"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from core import db, sched


def _d(s: str) -> datetime:
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------- 今日

def today_overview() -> dict:
    today = db.today_str()
    now = datetime.now().isoformat(timespec="seconds")
    due = db.q1(
        "SELECT COUNT(*) c FROM cards WHERE suspended=0 AND state<>'new' AND due<=?", (now,)
    )["c"]
    new_total = db.q1("SELECT COUNT(*) c FROM cards WHERE suspended=0 AND state='new'")["c"]
    new_done_today = db.q1(
        "SELECT COUNT(*) c FROM reviews WHERE substr(ts,1,10)=?", (today,)
    )["c"]
    reviewed_today = db.q1(
        "SELECT COUNT(*) c, COUNT(DISTINCT card_id) d FROM reviews WHERE substr(ts,1,10)=?", (today,)
    )
    mins_today = db.q1("SELECT minutes FROM checkins WHERE date=?", (today,))
    goal = int(db.num_setting("daily_goal_minutes", 30))
    new_limit = int(db.num_setting("daily_new_limit", 20))
    streak = db.streak_info()

    # 未来 7 天到期预测
    forecast = []
    for i in range(7):
        day = (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")
        cnt = db.q1(
            "SELECT COUNT(*) c FROM cards WHERE suspended=0 AND state<>'new' "
            "AND substr(due,1,10)=?", (day,)
        )["c"]
        forecast.append({"date": day, "weekday": "一二三四五六日"[
            (datetime.strptime(day, "%Y-%m-%d").weekday())], "count": cnt})

    retention = actual_retention(days=30)
    return {
        "date": today,
        "due": due,
        "new_available": new_total,
        "new_limit": new_limit,
        "new_today": new_done_today,
        "reviewed_today": reviewed_today["c"] or 0,
        "minutes_today": (mins_today["minutes"] if mins_today else 0),
        "goal_minutes": goal,
        "streak": streak,
        "forecast": forecast,
        "retention_30d": retention,
        "total_cards": db.q1("SELECT COUNT(*) c FROM cards")["c"],
        "total_decks": db.q1("SELECT COUNT(*) c FROM decks WHERE archived=0")["c"],
    }


def actual_retention(days: int = 30, deck_id: int | None = None) -> float | None:
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    sql = "SELECT COUNT(*) n, SUM(CASE WHEN rating>=3 THEN 1 ELSE 0 END) g FROM reviews WHERE ts>=?"
    args: list = [since]
    if deck_id:
        sql += " AND card_id IN (SELECT id FROM cards WHERE deck_id=?)"
        args.append(deck_id)
    row = db.q1(sql, tuple(args))
    if not row or not row["n"]:
        return None
    return round((row["g"] or 0) / row["n"], 3)


# ---------------------------------------------------------------- 统计面板

def stats(days: int = 120) -> dict:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    # 每日复习量
    daily = {r["d"]: {"reviews": r["c"], "minutes": 0}
             for r in db.q("SELECT substr(ts,1,10) d, COUNT(*) c FROM reviews "
                           "WHERE substr(ts,1,10)>=? GROUP BY d", (since,))}
    for r in db.q("SELECT date d, minutes FROM checkins WHERE date>=?", (since,)):
        daily.setdefault(r["d"], {"reviews": 0, "minutes": 0})["minutes"] = r["minutes"]
    for r in db.q("SELECT substr(started_at,1,10) d, SUM(minutes) m FROM study_sessions "
                  "WHERE minutes>0 GROUP BY d"):
        if r["d"] >= since:
            daily.setdefault(r["d"], {"reviews": 0, "minutes": 0})["minutes"] = max(
                daily[r["d"]]["minutes"], int(r["m"] or 0))

    heat = []
    today = datetime.now()
    start = today - timedelta(days=days - 1)
    start -= timedelta(days=(start.weekday() + 1) % 7)   # 对齐到周日
    cur = start
    while cur <= today:
        key = cur.strftime("%Y-%m-%d")
        v = daily.get(key, {"reviews": 0, "minutes": 0})
        heat.append({"date": key, "reviews": v["reviews"], "minutes": v["minutes"]})
        cur += timedelta(days=1)

    # 卡组维度
    decks = []
    for d in db.q("SELECT * FROM decks WHERE archived=0 ORDER BY id"):
        c = db.q1("SELECT COUNT(*) n, SUM(CASE WHEN state='new' THEN 1 ELSE 0 END) new, "
                  "SUM(CASE WHEN state='review' THEN 1 ELSE 0 END) rev, "
                  "SUM(CASE WHEN suspended=1 THEN 1 ELSE 0 END) susp "
                  "FROM cards WHERE deck_id=?", (d["id"],))
        decks.append({
            "id": d["id"], "title": d["title"], "subject": d["subject"], "color": d["color"],
            "cards": c["n"] or 0, "new": c["new"] or 0, "review": c["rev"] or 0,
            "suspended": c["susp"] or 0,
            "retention": actual_retention(60, d["id"]),
        })

    # 元认知校准：自评信心 vs 实际评分
    conf = {1: {"n": 0, "good": 0}, 2: {"n": 0, "good": 0}, 3: {"n": 0, "good": 0}}
    for r in db.q("SELECT confidence, rating FROM reviews WHERE confidence IS NOT NULL "
                  "AND ts>=?", (since,)):
        c = int(r["confidence"])
        if c in conf:
            conf[c]["n"] += 1
            if r["rating"] >= 3:
                conf[c]["good"] += 1
    calibration = []
    labels = {1: "以为不会", 2: "不太确定", 3: "很有把握"}
    for c, v in conf.items():
        calibration.append({
            "confidence": c, "label": labels[c], "n": v["n"],
            "actual": round(v["good"] / v["n"], 3) if v["n"] else None,
        })
    overconfident = None
    if conf[3]["n"] >= 8:
        overconfident = round(1 - conf[3]["good"] / conf[3]["n"], 3)

    # 未来 30 天记忆保持率曲线（对所有已学卡的期望值）
    curve = []
    for i in range(0, 31):
        t = datetime.now() + timedelta(days=i)
        rows = db.q("SELECT stability, last_review FROM cards WHERE suspended=0 "
                    "AND stability>0 AND state<>'new'")
        if not rows:
            curve.append({"day": i, "retention": None})
            continue
        vals = []
        for r in rows:
            lr = r["last_review"] or ""
            try:
                elapsed = max((t - _d(lr)).total_seconds() / 86400, 0)
            except Exception:
                continue
            vals.append(sched.retrievability(elapsed, r["stability"] or 0.1))
        curve.append({"day": i,
                      "retention": round(sum(vals) / len(vals), 3) if vals else None})

    # 学习时段分布（几点到几点学习，用来提醒生物节律）
    hours = [0] * 24
    for r in db.q("SELECT substr(ts,12,2) h, COUNT(*) c FROM reviews GROUP BY h"):
        try:
            hours[int(r["h"])] = r["c"]
        except (TypeError, ValueError):
            continue
    peak = max(range(24), key=lambda h: hours[h]) if any(hours) else None

    totals = db.q1("SELECT COUNT(*) n FROM reviews")["n"]
    return {
        "heatmap": heat,
        "decks": decks,
        "calibration": calibration,
        "overconfidence": overconfident,
        "retention_curve": curve,
        "hour_hist": hours,
        "peak_hour": peak,
        "total_reviews": totals,
        "retention_7d": actual_retention(7),
        "retention_30d": actual_retention(30),
        "total_minutes": db.q1("SELECT SUM(minutes) m FROM checkins")["m"] or 0,
        "streak": db.streak_info(),
    }


# ---------------------------------------------------------------- 计划 & 预测

def plan() -> dict:
    """按考试日期倒推每天需要学多少新卡（实现意图：把大目标拆成今天可执行的动作）。"""
    decks = []
    for d in db.q("SELECT * FROM decks WHERE archived=0 ORDER BY exam_date='' , exam_date"):
        c = db.q1("SELECT COUNT(*) n, SUM(CASE WHEN state='new' THEN 1 ELSE 0 END) new "
                  "FROM cards WHERE deck_id=?", (d["id"],))
        total, new = c["n"] or 0, c["new"] or 0
        per_day = None
        days_left = None
        if d["exam_date"]:
            try:
                left = (datetime.strptime(d["exam_date"], "%Y-%m-%d").date()
                        - datetime.now().date()).days
                days_left = max(left, 0)
                per_day = math.ceil(new / days_left) if days_left > 0 and new else (new or 0)
            except ValueError:
                pass
        decks.append({
            "id": d["id"], "title": d["title"], "exam_date": d["exam_date"],
            "days_left": days_left, "new_cards": new, "total_cards": total,
            "per_day": per_day,
            "retention": actual_retention(60, d["id"]),
        })
    due = db.q1("SELECT COUNT(*) c FROM cards WHERE suspended=0 AND state<>'new' AND due<=?",
                (datetime.now().isoformat(timespec="seconds"),))["c"]
    new_limit = int(db.num_setting("daily_new_limit", 20))
    return {
        "decks": decks,
        "due_today": due,
        "suggested_new": min(new_limit, max(0, sum(max(0, d["new_cards"]) for d in decks))),
        "advice": _advice(due, new_limit),
    }


def _advice(due: int, new_limit: int) -> list[str]:
    tips = []
    if due == 0:
        tips.append("今天没有到期卡片：适合学新内容，或者做一次 10 分钟的阶段自测。")
    elif due > 80:
        tips.append(f"到期 {due} 张，任务偏重：先用 25 分钟只清「忘了/模糊」的卡，"
                    "清醒后再补新卡，避免认知过载。")
    else:
        tips.append(f"到期 {due} 张：先复习旧卡再学新卡（先固化再扩展），大约 "
                    f"{max(10, due * 0.6):.0f} 分钟。")
    tips.append(f"每天新卡上限 {new_limit} 张是刻意设的限制：一次性塞太多会显著降低留存率。")
    return tips


def quiz_bank(deck_id: int | None, n: int = 10) -> list[dict]:
    """从已有卡片生成小测：一半选择题（干扰项取自同讲义其它卡），一半填空。"""
    import random

    where = "suspended=0 AND length(back) BETWEEN 1 AND 40"
    args: list = []
    if deck_id:
        where += " AND deck_id=?"
        args.append(deck_id)
    rows = [dict(r) for r in db.q(f"SELECT * FROM cards WHERE {where}", tuple(args))]
    if len(rows) < 3:
        return []
    random.shuffle(rows)
    pool = rows[:max(n * 2, 12)]
    out = []
    for i, card in enumerate(pool):
        if len(out) >= n:
            break
        others = [r["back"] for r in pool if r["id"] != card["id"]
                  and r["back"] and r["back"] != card["back"]]
        # 一半选择题（干扰项取自同池其它卡），一半填空；
        # 填空题对挖空卡最自然，选择题则用来练"辨别"，两者交错出现。
        if i % 2 == 0 and len(others) >= 2:
            options = random.sample(others, 2) + [card["back"]]
            random.shuffle(options)
            out.append({"type": "choice", "card_id": card["id"], "question": card["front"],
                        "options": options, "answer": card["back"], "kind": card["kind"]})
        else:
            out.append({"type": "fill", "card_id": card["id"], "question": card["front"],
                        "answer": card["back"], "kind": card["kind"]})
    return out