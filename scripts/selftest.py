# -*- coding: utf-8 -*-
"""后端自检：解析样例讲义、看自动生成的卡片质量、验证调度器行为。"""
import io
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "vendor"))

from core import extract, sched, db  # noqa: E402

out = io.StringIO()
PPTX = os.path.join(BASE, "样例讲义", "记忆与学习_认知科学讲义.pptx")

db.init_db()
parsed = extract.extract(PPTX, "demo.pptx")
out.write(f"=== 解析结果：{parsed['source_type']} / {parsed['page_count']} 页 ===\n")
for s in parsed["sections"]:
    out.write(f"\n[{s['idx']}] {s['title']}  (text {len(s['text'])} 字, img={s['image_path']})\n")
    out.write(s["text"][:300] + "\n")

sections = []
for s in parsed["sections"]:
    sections.append({"id": s["idx"] + 1, "idx": s["idx"], "title": s["title"],
                     "text": s["text"], "image_path": s["image_path"]})
cards = extract.generate_cards(sections, 5)
out.write(f"\n=== 自动生成 {len(cards)} 张候选卡 ===\n")
for c in cards:
    out.write(f"[{c['kind']}] Q: {c['front']}\n        A: {c['back']}\n")

out.write("\n=== FSRS 调度模拟（新卡，连续评 3 分）===\n")
card = {"state": "new", "stability": 0, "difficulty": 0, "reps": 0, "last_review": ""}
import datetime
now = datetime.datetime.now()
for i in range(5):
    res = sched.review(card, 3, now, 0.9, 365)
    out.write(f"第{i+1}次: S={res['stability']} D={res['difficulty']} "
              f"间隔={res['interval_days']:.2f}天 -> {res['due']}  ({sched.describe_interval(res['interval_days'])})\n")
    card.update(state=res["state"], stability=res["stability"], difficulty=res["difficulty"],
                last_review=now.isoformat(timespec="seconds"), reps=i + 1)
    now = datetime.datetime.fromisoformat(res["due"])

out.write("\n=== 评分 1 的效果（遗忘）===\n")
res = sched.review(card, 1, now, 0.9, 365)
out.write(f"S={res['stability']} 状态={res['state']} due={res['due']}\n")

out.write("\n=== 四档按钮预览 ===\n")
for k, v in sched.preview_intervals({"state": "review", "stability": 12, "difficulty": 5,
                                     "reps": 4, "last_review": (
                                         datetime.datetime.now() - datetime.timedelta(days=9)
                                     ).isoformat(timespec="seconds")}, 0.9, 365).items():
    out.write(f"{v['label']}: {v['text']}\n")

# --- AI 引擎 + 逐页讲解管线自检（有引擎才跑，没有则跳过） ---
out.write("\n=== AI 引擎与逐页讲解 ===\n")
from core import ai, analyze  # noqa: E402
st = ai.status()
out.write(f"mode={st['mode']} effective={st['effective']} "
          f"dsh_available={st['dsh']['available']} api_configured={st['api']['configured']}\n")
if st["effective"]:
    try:
        reply, engine = ai.complete("回复两个字：正常", temperature=0, max_tokens=20)
        out.write(f"连通性：engine={engine} reply={reply.strip()}\n")
        cur = db.ex("INSERT INTO decks(title, subject, color, source_type, page_count, created_at) "
                    "VALUES('selftest', '测试', '#6C8CFF', 'pptx', 2, ?)", (db.now_iso(),))
        deck_id = cur.lastrowid
        cur = db.ex(
            "INSERT INTO sections(deck_id, idx, title, text) VALUES(?, 0, '长时程增强', ?)",
            (deck_id, "长时程增强（LTP）是指高频刺激后突触传递效率的持久提高，"
                      "是学习和记忆的细胞基础；长时程抑制（LTD）则相反。"))
        sec_id1 = cur.lastrowid
        cur = db.ex(  # 与课程主线无关的边缘内容：应当 card_worthy=False、0 张卡
            "INSERT INTO sections(deck_id, idx, title, text) VALUES(?, 1, '信创产业政策', ?)",
            (deck_id, "我国近年大力推进信创工程：基础软硬件国产化替代，数据库、"
                      "操作系统等关键领域涌现一批自主厂商，政策支持力度持续加大。"))
        sec_id2 = cur.lastrowid
        for sec_id, expect in ((sec_id1, "重点页"), (sec_id2, "无关页")):
            rep = analyze.analyze_section(deck_id, sec_id, 3)
            out.write(f"[{expect}] worth={rep['worth']} card_worthy={rep['card_worthy']} "
                      f"concepts={len(rep['concepts'])} cards={len(rep['cards'])} "
                      f"engine={rep['engine']}\n")
            out.write(f"  讲解: {rep['teaching'][:110]}...\n")
            for c in rep["concepts"]:
                out.write(f"  概念: {c['term']} (importance {c['importance']}) - {c['why'][:50]}\n")
            for c in rep["cards"]:
                out.write(f"  [{c['kind']}] {c['front']}\n        => {c['back']}\n")
            if sec_id == sec_id2 and rep["cards"]:
                out.write("  !! 无关页不应出卡，提示词/归一化需检查\n")
        rep1 = analyze.list_analyses(deck_id)[-1]
        out.write(f"import_cards(无关页, 应=0) -> ")
        imp_none = analyze.import_cards(deck_id, [rep1["id"]])
        out.write(f"{imp_none}\n")
        imp = analyze.import_cards(deck_id, [a["id"] for a in analyze.list_analyses(deck_id)
                                             if a["cards"]])
        out.write(f"import_cards(重点页) -> {imp}\n")
        rep2 = analyze.analyze_section(deck_id, sec_id1, 2)
        out.write(f"覆盖重分析 -> 记录数 {len(analyze.list_analyses(deck_id))}（应为 2，第 {rep2['id']} 号覆盖）\n")
        analyze.clear_analyses(deck_id)
        out.write(f"clear_analyses -> 记录数 {len(analyze.list_analyses(deck_id))}（应为 0）\n")
        db.ex("DELETE FROM decks WHERE id=?", (deck_id,))
        out.write("AI 自检全部通过\n")
    except Exception as e:  # noqa: BLE001
        out.write(f"AI 自检失败：{e}\n")
else:
    out.write("没有可用 AI 引擎，跳过。设置页可填 DeepSeek Key（直连）或安装 dsh。\n")

with open(os.path.join(BASE, "自检报告.txt"), "w", encoding="utf-8") as f:
    f.write(out.getvalue())
print("done, cards:", len(cards))