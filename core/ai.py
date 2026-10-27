"""大模型增强：AI 引擎调度（直连 DeepSeek API / 本机 dsh）+ 讲义问答。

引擎（设置 ai_mode）：
- api：直连 OpenAI 兼容接口（默认 DeepSeek 开放平台：base+key 在设置里填）
- dsh：调用本机 DeepSeek Harness 的 headless 一次性任务（零配置，密钥归 dsh 管）
- auto：api 配置了就优先 api，否则有 dsh 就用 dsh
- off：关闭
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from core import db, dsh


def _cfg():
    return (
        (db.setting("ai_base") or "").strip().rstrip("/"),
        (db.setting("ai_key") or "").strip(),
        (db.setting("ai_model") or "deepseek-chat").strip(),
    )


def configured() -> bool:
    base, key, _ = _cfg()
    return bool(base and key)


def resolve_engine():
    """按设置解析出可用引擎。返回 ("api", model) 或 ("dsh", None)。不可用抛 ValueError。"""
    mode = (db.setting("ai_mode") or "auto").strip()
    api_ok = configured()
    dsh_ok = dsh.available()
    if mode == "off":
        raise ValueError("AI 已在设置中关闭，去「设置 → AI 引擎」打开")
    if mode == "api":
        if not api_ok:
            raise ValueError("直连 API 未配置：请在设置里填 API 地址和 Key")
        return "api", _cfg()[2]
    if mode == "dsh":
        if not dsh_ok:
            raise ValueError(dsh.info().get("error") or "未找到本机 DeepSeek Harness")
        return "dsh", None
    # auto
    if api_ok:
        return "api", _cfg()[2]
    if dsh_ok:
        return "dsh", None
    raise ValueError(
        "还没有可用的 AI 引擎：去「设置 → AI 引擎」二选一——"
        "① 填入 DeepSeek 开放平台 API Key（直连）；"
        "② 本机装好 DeepSeek Harness（自动检测 dsh headless）。")

def chat(messages: list[dict], temperature: float = 0.4, max_tokens: int = 6000) -> str:
    """直连 API 的一次对话（仅在 api 引擎下使用）。"""
    base, key, model = _cfg()
    if not base or not key:
        raise ValueError("还没有配置 AI 接口（设置 → AI 增强）")
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    payload = json.dumps({
        "model": model, "messages": messages,
        "temperature": temperature, "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise ValueError(f"AI 接口返回 {e.code}：{detail}") from e
    except Exception as e:
        raise ValueError(f"AI 接口连接失败：{e}") from e
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise ValueError("AI 返回格式无法识别") from e


def _json_block(text: str):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    raw = m.group(1) if m else text
    start = min([i for i in (raw.find("["), raw.find("{")) if i >= 0], default=-1)
    if start >= 0:
        raw = raw[start:]
    return json.loads(raw)


def complete(prompt: str, system: str | None = None, temperature: float = 0.4,
             max_tokens: int = 6000, timeout: int = 360) -> tuple[str, str]:
    """按当前引擎完成一次生成。返回 (文本, 引擎名)。

    api 引擎走 chat；dsh 引擎把 system+prompt 拼成一次 headless 任务，
    任务文本自包含，模型不需要任何文件系统访问。
    """
    engine, model = resolve_engine()
    if engine == "api":
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return chat(messages, temperature=temperature, max_tokens=max_tokens), \
            f"api:{model}"
    # dsh
    task = (system + "\n\n" + prompt) if system else prompt
    res = dsh.run(task, timeout=timeout)
    if not dsh.check_ok(res):
        code = res.get("code")
        tail = (res.get("stderr_tail") or "").replace("\n", " ").strip()
        raise ValueError(f"dsh 执行失败（退出码 {code}）：{tail}")
    return res["answer"].strip(), "dsh:headless"


def retrieve(question: str, deck_id: int | None = None, k: int = 4) -> list[dict]:
    """轻量检索：按关键词重合度挑出最相关的讲义小节。"""
    terms = [t for t in re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z]{3,}", question)][:14]
    sql = "SELECT s.id, s.deck_id, s.idx, s.title, s.text, d.title AS deck_title " \
          "FROM sections s JOIN decks d ON d.id=s.deck_id"
    args: list = []
    if deck_id:
        sql += " WHERE s.deck_id=?"
        args.append(deck_id)
    rows = [dict(r) for r in db.q(sql + " LIMIT 2000", tuple(args))]
    for r in rows:
        r["score"] = sum(r["text"].count(t) for t in terms) + 3 * sum(
            r["title"].count(t) for t in terms)
    rows = [r for r in rows if r["score"] > 0]
    rows.sort(key=lambda r: -r["score"])
    return rows[:k]


def make_cards(text: str, title: str = "", count: int = 8) -> list[dict]:
    prompt = (
        "你是学习科学专家，擅长把讲义改写成高质量主动回忆卡片。\n"
        "规则：\n"
        "1. 一张卡只考一个知识点，正面必须是能独立回答的问题或挖空句，不要出现「如上」「这个」等指代。\n"
        "2. 挖空卡用 ____ 表示空缺，正面给出足够上下文，背面是被挖掉的术语。\n"
        "3. 优先考理解与因果，而非死记字面。\n"
        f"4. 输出 {count} 张，纯 JSON 数组，元素形如 "
        '{"kind":"basic|cloze","front":"...","back":"...","note":"补充解释或例子"}。\n'
        "5. 只输出 JSON，不要任何解释文字。"
    )
    content = f"讲义小节标题：{title}\n\n内容：\n{text[:6000]}"
    out, _ = complete(content, system=prompt, temperature=0.5)
    data = _json_block(out)
    if isinstance(data, dict):
        data = data.get("cards") or data.get("items") or []
    cards = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        front = str(item.get("front") or "").strip()
        back = str(item.get("back") or "").strip()
        if len(front) < 4 or not back:
            continue
        cards.append({
            "kind": item.get("kind") if item.get("kind") in ("basic", "cloze") else "basic",
            "front": front, "back": back, "note": str(item.get("note") or "")[:400],
        })
    return cards[:count]


def ask(question: str, deck_id: int | None = None) -> dict:
    ctx = retrieve(question, deck_id)
    if not ctx:
        raise ValueError("讲义里没找到相关内容，换个问法或先上传对应讲义")
    context = "\n\n".join(
        f"[{c['deck_title']} · {c['title']}]\n{c['text'][:1200]}" for c in ctx)
    system = ("你是耐心的学习教练。只依据给定讲义内容回答，用中文，先给结论再用 2-3 条要点解释；"
              "讲义里没有的信息要明确说「讲义未涉及」，不要编造。最后用一句话给出可执行的记忆建议。")
    answer, _ = complete(f"讲义片段：\n{context}\n\n问题：{question}",
                         system=system, temperature=0.3)
    return {
        "answer": answer,
        "sources": [{"deck": c["deck_title"], "section": c["title"], "idx": c["idx"],
                     "deck_id": c["deck_id"], "section_id": c["id"]} for c in ctx],
    }


def status() -> dict:
    """引擎状态总览，供设置页展示与前端决定走哪条链路。"""
    base, _, model = _cfg()
    d = dsh.info()
    mode = (db.setting("ai_mode") or "auto").strip()
    effective = None
    try:
        effective, _ = resolve_engine()
    except ValueError:
        pass
    return {
        "mode": mode,
        "effective": effective,
        "api": {
            "configured": bool(base and db.setting("ai_key")),
            "base": base or "https://api.deepseek.com/v1",
            "model": model,
        },
        "dsh": d,
    }