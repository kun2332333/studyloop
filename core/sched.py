"""间隔重复调度器 —— FSRS-4.5 公式（Free Spaced Repetition Scheduler）。

为什么要用它：
- 艾宾浩斯遗忘曲线告诉我们记忆按指数衰减，但"每个人的衰减速度"不同。
- FSRS 用两个状态量刻画一张卡：
    * 记忆稳定性 S（stability）：记住多久后回忆概率降到 90%（单位：天）
    * 难度 D（difficulty）：这张卡对这个人的固有难度，1~10
- 回忆概率 R(t, S) = (1 + FACTOR * t / S) ^ DECAY
- 每次评分（1 完全忘了 / 2 模糊 / 3 记得 / 4 秒答）都会更新 S、D，
  再反解出"目标保持率 r 对应的时间间隔"，得到下次复习日期。
即：把"统一的固定复习表"换成"按你的实际表现个性化安排的复习表"。
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

# FSRS-4.5 默认参数（w0..w14 为拟合权重，w15 = 困难惩罚 <1，w16 = 简单奖励 >1）
W = (0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975, 0.031,
     1.6474, 0.1367, 1.0461, 2.1072, 0.0793, 0.3246, 1.587, 0.8, 1.3)
DECAY = -0.5
FACTOR = 0.9 ** (1 / DECAY) - 1  # ≈ 0.2345
HARD_PENALTY = W[15]
EASY_BONUS = W[16]

MIN_STABILITY = 0.1
MAX_INTERVAL = 365.0


def _clamp_d(d: float) -> float:
    return min(max(d, 1.0), 10.0)


def init_stability(rating: int) -> float:
    return max(W[max(0, min(3, rating - 1))], MIN_STABILITY)


def init_difficulty(rating: int) -> float:
    return _clamp_d(W[4] - (rating - 3) * W[5])


def retrievability(elapsed_days: float, stability: float) -> float:
    if stability <= 0:
        return 0.0
    return (1 + FACTOR * max(elapsed_days, 0) / stability) ** DECAY


def next_difficulty(d: float, rating: int) -> float:
    nd = d - W[6] * (rating - 3)
    # 均值回归，避免难度漂移到极端
    nd = W[7] * init_difficulty(4) + (1 - W[7]) * nd
    return _clamp_d(nd)


def next_recall_stability(d: float, s: float, r: float, rating: int) -> float:
    hard_penalty = HARD_PENALTY if rating == 2 else 1.0
    easy_bonus = EASY_BONUS if rating == 4 else 1.0
    inc = math.exp(W[8]) * (11 - d) * (s ** -W[9]) * (math.exp(W[10] * (1 - r)) - 1)
    return max(s * (1 + inc * hard_penalty * easy_bonus), MIN_STABILITY)


def next_forget_stability(d: float, s: float, r: float) -> float:
    return max(W[11] * (d ** -W[12]) * ((s + 1) ** W[13] - 1) * math.exp(W[14] * (1 - r)),
               MIN_STABILITY)


def interval_for(stability: float, desired_retention: float, max_interval: float) -> float:
    """反解：希望回忆概率保持在 desired_retention 时，可以隔多少天再复习。"""
    r = min(max(desired_retention, 0.7), 0.98)
    iv = stability / FACTOR * (r ** (1 / DECAY) - 1)
    return min(max(iv, 1.0), max_interval)


def fuzz(interval: float) -> float:
    """长间隔加一点随机抖动，避免同一天生成的卡永远撞在一起。"""
    if interval < 2.5:
        return interval
    spread = max(0.05, 0.07 - 0.0012 * min(interval, 60))
    return interval * (1 + random.uniform(-spread, spread))


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(ts[:19], fmt)
        except ValueError:
            continue
    return None


def review(card: dict, rating: int, now: datetime | None = None,
           desired_retention: float = 0.9, max_interval: float = 365.0) -> dict:
    """根据一次评分更新卡片调度状态。

    返回：state / due / stability / difficulty / interval_days / retrievability
    """
    now = now or datetime.now()
    rating = min(max(int(rating), 1), 4)
    state = card.get("state") or "new"
    s = float(card.get("stability") or 0)
    d = float(card.get("difficulty") or 0)
    last = _parse(card.get("last_review"))

    elapsed = 0.0
    if last:
        elapsed = max((now - last).total_seconds() / 86400.0, 0.0)
    r_before = retrievability(elapsed, s) if s > 0 else 0.0

    if state in ("new", "learning") and s <= 0:
        # 第一次见到这张卡
        s = init_stability(rating)
        d = init_difficulty(rating)
        new_state = "review"
        if rating == 1:
            # 完全想不起来：走 relearning，10 分钟后再来一次
            new_state = "relearning"
            due = now + timedelta(minutes=10)
            interval = 10 / 1440
        else:
            # 毕业间隔：新卡第一次复习按评分给 1 / 2 / 4 天，
            # 让"刚背完就 4 天后见"不至于太激进；之后完全交给 FSRS 个性化。
            interval = min({2: 1.0, 3: 2.0, 4: 4.0}.get(rating, 2.0), max_interval)
            due = now + timedelta(days=interval)
    elif rating == 1:
        # 遗忘：稳定性大幅下降，进入重学
        s = next_forget_stability(d, s, r_before)
        d = next_difficulty(d, rating)
        new_state = "relearning"
        interval = 10 / 1440
        due = now + timedelta(minutes=10)
    else:
        s = next_recall_stability(d, s, r_before, rating)
        d = next_difficulty(d, rating)
        new_state = "review"
        interval = interval_for(s, desired_retention, max_interval)
        # 首次复习给一个地板值，防止"刚背完就 1 天后到期"的挫败感
        if state == "relearning" or (card.get("reps") or 0) <= 1:
            interval = max(interval, 1.0)
        interval = fuzz(interval)
        due = now + timedelta(days=interval)

    # 同一批生成的卡：把"当天刚建卡"的到期时间对齐到早上，避免深夜到期
    if interval >= 1:
        due = due.replace(hour=min(max(due.hour, 8), 22), minute=0, second=0, microsecond=0)

    return {
        "state": new_state,
        "due": due.replace(microsecond=0).isoformat(),
        "stability": round(s, 4),
        "difficulty": round(d, 4),
        "interval_days": round(interval, 3),
        "retrievability": round(retrievability(elapsed, max(card.get("stability") or 0, MIN_STABILITY)), 3),
        "next_retention": round(desired_retention, 3),
    }


def describe_interval(days: float) -> str:
    if days < 1:
        mins = max(1, int(round(days * 1440)))
        return f"{mins} 分钟后"
    if days < 30:
        return f"{round(days)} 天后"
    if days < 365:
        return f"{round(days / 30, 1)} 个月后"
    return f"{round(days / 365, 1)} 年后"


def preview_intervals(card: dict, desired_retention: float, max_interval: float) -> dict:
    """给前端显示 4 个评分按钮各自对应的下次间隔。"""
    out = {}
    now = datetime.now()
    for rating, label in ((1, "忘了"), (2, "模糊"), (3, "记得"), (4, "秒答")):
        res = review(dict(card), rating, now, desired_retention, max_interval)
        out[rating] = {"label": label, "text": describe_interval(res["interval_days"])}
    return out


def predicted_retention(card: dict, now: datetime | None = None) -> float:
    """当前这张卡"此刻还记得"的概率，用于复习队列排序与遗忘曲线。"""
    now = now or datetime.now()
    s = float(card.get("stability") or 0)
    if s <= 0:
        return 0.0
    last = _parse(card.get("last_review")) or _parse(card.get("due"))
    if not last:
        return 0.0
    elapsed = max((now - last).total_seconds() / 86400.0, 0.0)
    return round(retrievability(elapsed, s), 3)