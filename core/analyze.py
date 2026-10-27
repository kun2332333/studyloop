"""AI 逐页讲义分析：判断每页内容价值、提炼概念并草拟学习卡片。

流程（一次一页，前端逐页调用以显示进度）：
    analyze_section(deck, section) → 构造提示词 → 引擎生成 → 严格解析 JSON
    → 规范化（worth/concepts/cards）→ 存 ai_analyses 表 → 返回报告。
导入阶段由用户在界面勾选认可的卡片 draft 后按分析记录 id 批量入库（source='ai'），
模型不直接写卡库——人保留最终取舍权。
"""
from __future__ import annotations

import json
import re

from core import ai, db

MAX_CARDS = 3          # 每页最多草拟卡片数（卡只为复习服务，从严）
MAX_TEXT = 4500        # 每次喂给模型的页内文本上限（A4 一页通常远小于此）


def build_system() -> str:
    return (
        "你是资深大学助教与学习教练，精通主动回忆（active recall）、间隔重复、认知负荷理论，"
        "熟悉中国大学课堂讲义的常见结构。你的核心任务：逐页以老师的视角给学生讲课，"
        "并把真正值得长期记忆的核心考点做成极少量复习卡片。\n"
        "判断价值时遵循：\n"
        "1) 封面、目录、致谢、课程安排、纯过渡页 → low；\n"
        "2) 定义、原理、机制、公式、易混点、重要结论 → high；\n"
        "3) 举例、背景、次要细节、行业新闻、政策介绍、与本课主线无关的延伸 → mid；\n"
        "4) 概念重要性 importance 取 1-5：5=考试/理解的核心支柱，4=高频考点，"
        "3=常见要点，1-2=边缘信息。\n"
        "讲解（teaching）要求——这是最重要的输出，请认真写：\n"
        "1) 用老师给学生讲课的中文口述稿，2~6 句，先讲「是什么」，再讲「为什么重要/怎么用」，"
        "关键处可用生活化类比；\n"
        "2) 页面里的英文术语一律翻译成中文，并保留英文原文，"
        "例如「数据库管理系统（DBMS，Database Management System）」；\n"
        "3) 要用自己的话重组，禁止复制页面句子，禁止「本页主要介绍了……」这类流水账开头；\n"
        "4) 即使页面是封面/目录等低价值页，也要给出一句自然得体的说明。\n"
        "卡片判定（card_worthy）——卡只用于考前的间隔复习，从严再从严：\n"
        "1) 只有同时满足两条才值得制卡：本页内容与这门课程的核心主线直接相关；"
        "并且存在必须长期记住的核心考点；\n"
        "2) 行业新闻、政策背景、企业案例、致谢、目录、页面设计、与本课主线无关的延伸内容"
        "（例如数据库课程里介绍「信创产业政策」）一律 card_worthy=false，一张卡都不许出；\n"
        "3) 只从 importance>=4 的概念出卡，每页不超过 3 张，宁缺毋滥——"
        "一份 100 页的讲义，最终适合制卡的往往只有 30~50 张。\n"
        "卡片格式规则：\n"
        "1) 一张卡只考一个知识点；正面必须自包含，不出现「如上」「该图」「这个」等指代；\n"
        "2) cloze（挖空卡）：front 是被挖空的完整句子，挖空处用 ＿＿＿＿ 占位，"
        "back 只写被挖掉的术语或短语本身；\n"
        "3) basic（问答卡）：front 是具体问题，back 用 1-2 句作答；\n"
        "4) 只依据本页内容出卡，绝不编造讲义没有的信息；card_worthy=false 时 cards 必须为空数组。\n"
        "输出格式：只输出一个 JSON 对象，不要 Markdown 围栏、不要任何解释文字：\n"
        '{"worth":"high|mid|low","card_worthy":true|false,"reason":"一句话价值与相关性判断",'
        '"teaching":"老师视角的中文讲解（英文已翻译）",'
        '"concepts":[{"term":"中文术语","en":"英文原文(可空)","why":"为何重要","importance":4}],'
        '"cards":[{"kind":"cloze|basic","front":"...","back":"...","note":"可选一句补充"}]}'
    )


def build_prompt(deck_title: str, sec: dict) -> str:
    title = (sec.get("title") or f"第 {int(sec.get('idx', 0)) + 1} 页").strip()
    text = (sec.get("text") or "").strip()[:MAX_TEXT]
    if not text:
        text = "（这一页没有提取到文字内容）"
    return (f"讲义：{deck_title}\n\n【本页标题】{title}\n\n【本页内容】\n{text}\n\n"
            "现在按上述要求分析这一页。")


def _parse_json(text: str) -> dict:
    """:严格 JSON：剥围栏 → 优先整体解析 → 从头扫到第一个 { 用 raw_decode。"""
    raw = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if m:
        raw = m.group(1).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    i = raw.find("{")
    if i >= 0:
        try:
            obj, _ = json.JSONDecoder().raw_decode(raw[i:])
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
    raise ValueError("AI 输出无法解析为 JSON，请重试该页：" + text[:120])


def _normalize(data: dict) -> dict:
    worth = str(data.get("worth") or "").strip().lower()
    if worth not in ("high", "mid", "low"):
        worth = "mid"
    reason = str(data.get("reason") or "").strip()[:300]
    teaching = re.sub(r"\s+", " ", str(data.get("teaching") or data.get("summary") or "")).strip()
    if not teaching:
        teaching = "（AI 没有给出讲解，请重新分析这一页）"
    card_worthy = bool(data.get("card_worthy")) and worth != "low"
    concepts: list[dict] = []
    for c in (data.get("concepts") or []) if isinstance(data.get("concepts"), list) else []:
        if isinstance(c, dict) and c.get("term"):
            try:
                imp = int(c.get("importance") or 3)
            except (TypeError, ValueError):
                imp = 3
            concepts.append({
                "term": str(c["term"]).strip()[:60],
                "en": str(c.get("en") or "").strip()[:60],
                "why": str(c.get("why") or "").strip()[:200],
                "importance": max(1, min(5, imp)),
            })
    cards: list[dict] = []
    if card_worthy:
        for c in (data.get("cards") or []) if isinstance(data.get("cards"), list) else []:
            if not isinstance(c, dict):
                continue
            kind = str(c.get("kind") or "").strip().lower()
            kind = kind if kind in ("basic", "cloze") else "basic"
            front = re.sub(r"\s+", " ", str(c.get("front") or "")).strip()
            back = re.sub(r"\s+", " ", str(c.get("back") or "")).strip()
            if len(front) < 4 or not back:
                continue
            cards.append({
                "kind": kind,
                "front": front[:200],
                "back": back[:300],
                "note": str(c.get("note") or "").strip()[:200],
            })
    return {"worth": worth, "card_worthy": card_worthy, "reason": reason,
            "teaching": teaching[:1200],
            "concepts": concepts[:8], "cards": cards[:MAX_CARDS]}


def analyze_section(deck_id: int, section_id: int, max_cards: int = MAX_CARDS) -> dict:
    """分析讲义的一页，结果落库并返回报告 dict。"""
    deck = db.one(db.q1("SELECT id, title FROM decks WHERE id=?", (deck_id,)))
    if not deck:
        raise ValueError("讲义不存在")
    sec = db.one(db.q1("SELECT id, deck_id, idx, title, text FROM sections WHERE id=?",
                       (section_id,)))
    if not sec or int(sec["deck_id"]) != int(deck_id):
        raise ValueError("该页不属于这份讲义")
    out, engine = ai.complete(build_prompt(deck["title"], sec),
                              system=build_system(), temperature=0.3)
    data = _normalize(_parse_json(out))
    data["cards"] = data["cards"][:max_cards]
    # 同一页重复分析 = 覆盖旧记录（已导入卡片的卡不受影响）
    db.ex("DELETE FROM ai_analyses WHERE deck_id=? AND section_id=?",
          (int(deck_id), int(section_id)))
    analysis_id = db.save_analysis(
        int(deck_id), int(section_id), engine, data["worth"], data["reason"],
        data["teaching"], json.dumps(data["concepts"], ensure_ascii=False),
        json.dumps(data["cards"], ensure_ascii=False))
    data.update({
        "id": analysis_id,
        "deck_id": int(deck_id),
        "section_id": int(section_id),
        "section_idx": int(sec["idx"]),
        "section_title": sec["title"] or f"第 {int(sec['idx']) + 1} 页",
        "engine": engine,
        "imported": 0,
    })
    return data


def list_analyses(deck_id: int) -> list[dict]:
    out = []
    for r in db.analyses_for_deck(int(deck_id)):
        try:
            r["concepts"] = json.loads(r.pop("concepts_json") or "[]")
        except (json.JSONDecodeError, ValueError):
            r["concepts"] = []
        try:
            r["cards"] = json.loads(r.pop("cards_json") or "[]")
        except (json.JSONDecodeError, ValueError):
            r["cards"] = []
        if not isinstance(r["concepts"], list):
            r["concepts"] = []
        if not isinstance(r["cards"], list):
            r["cards"] = []
        # teaching 存在 summary 列；旧记录（v1.1 初期）的 summary 是摘要，直接当讲解兜底显示
        r["teaching"] = r.get("summary") or ""
        r["card_worthy"] = bool(r.get("card_worthy")) or bool(r["cards"])
        out.append(r)
    return out


def clear_analyses(deck_id: int) -> dict:
    """清空一个讲义的全部分析记录（不影响已导入卡库的卡片）。"""
    if not db.one(db.q1("SELECT id FROM decks WHERE id=?", (int(deck_id),))):
        raise ValueError("讲义不存在")
    db.ex("DELETE FROM ai_analyses WHERE deck_id=?", (int(deck_id),))
    return {"ok": True, "cleared": 1}


def import_cards(deck_id: int, analysis_ids: list[int]) -> dict:
    """把选中的分析记录里的卡片草案写入卡库（source='ai'，state='new'）。"""
    deck = db.one(db.q1("SELECT id FROM decks WHERE id=?", (int(deck_id),)))
    if not deck:
        raise ValueError("讲义不存在")
    imported = 0
    for aid in analysis_ids:
        a_deck, a_section, cards = db.analysis_cards(int(aid))
        if a_deck != int(deck_id):
            raise ValueError(f"分析记录 #{aid} 不属于这份讲义")
        n_this = 0
        for c in cards:
            front = str(c.get("front") or "").strip()
            back = str(c.get("back") or "").strip()
            kind = str(c.get("kind") or "").strip()
            if kind not in ("basic", "cloze"):
                kind = "basic"
            if len(front) < 4 or not back:
                continue
            # 去重：同讲义内 front 完全相同的 ai 卡不重复导入
            dup = db.q1("SELECT id FROM cards WHERE deck_id=? AND front=? AND source='ai'",
                        (int(deck_id), front))
            if dup:
                continue
            db.ex(
                "INSERT INTO cards(deck_id, section_id, kind, front, back, note, image_path, "
                "source, state, due, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (int(deck_id), a_section, kind, front, back,
                 str(c.get("note") or "")[:400], "", "ai", "new", db.now_iso(),
                 db.now_iso()))
            imported += 1
            n_this += 1
        # imported 列是累计值：这里传「本次为该页导入的张数」
        db.mark_imported(int(aid), n_this)
    return {"imported": imported}