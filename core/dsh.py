"""DeepSeek Harness（dsh）桥接：把本机安装的 dsh 当作 AI 引擎调用。

dsh 的 headless profile 是"一次性任务"模式：
    一次调用 = 提交一段任务文本 → 模型完成 → 标准输出打印最终答案 → 进程退出。
适合批量/无人值守分析；API key 由 dsh 自己的凭据链解决（环境变量 → cwd/.env →
$DSH_HOME/.env → 凭据库），拾知不需要也不应该经手该密钥。
"""
from __future__ import annotations

import os
import shutil
import subprocess

BIN_REL = os.path.join("apps", "cli", "lib", "bin.js")  # 相对 dsh 检出根目录


def find_checkout() -> str | None:
    """定位 dsh 仓库检出目录（含 apps/cli/lib/bin.js）。"""
    candidates: list[str] = []
    for key in ("DSH_ROOT", "DSH_CHECKOUT"):
        v = os.environ.get(key)
        if v:
            candidates.append(v)
    home = os.path.expanduser("~")
    candidates += [
        os.path.join(home, "deepseek-harness"),
        os.path.join(home, "dsh"),
        os.path.join(home, "DeepSeek", "deepseek-harness"),
        r"D:\deepseek-harness",
        r"C:\deepseek-harness",
    ]
    seen: set[str] = set()
    for c in candidates:
        c = os.path.normpath(c)
        if c in seen:
            continue
        seen.add(c)
        if os.path.isfile(os.path.join(c, BIN_REL)):
            return c
    return None


def find_node() -> str:
    return shutil.which("node") or shutil.which("nodejs") or "node"


def available() -> bool:
    return find_checkout() is not None


def info() -> dict:
    c = find_checkout()
    if not c:
        return {"available": False,
                "error": "未找到 DeepSeek Harness 检出目录（apps/cli/lib/bin.js）。"
                         "可通过环境变量 DSH_ROOT 指定。"}
    return {"available": True, "checkout": c,
            "bin": os.path.join(c, BIN_REL), "node": find_node()}


def run(task: str, timeout: int = 360, extra_env: dict | None = None) -> dict:
    """执行一次 dsh headless 任务。

    返回 dict(code, answer, stderr_tail)。code==0 且 answer 非空才算成功。
    task 必须自包含：文本原样内联进提示词，模型不依赖也不应依赖拾知的文件。
    """
    c = find_checkout()
    if not c:
        raise RuntimeError(
            "未找到 DeepSeek Harness 检出目录（apps/cli/lib/bin.js）。"
            "请把 dsh 克隆到 D:\\deepseek-harness 或设置环境变量 DSH_ROOT。")
    env = dict(os.environ)
    env.pop("DSH_WEB_PORT", None)  # 防止与正在运行的 web GUI 冲突
    if extra_env:
        env.update(extra_env)
    argv = [
        find_node(), os.path.join(c, BIN_REL),
        "--profile", "headless",
        "--approval-policy", "never",   # 无人值守：需要审批的工具直接拒绝，不挂起
        task,
    ]
    try:
        p = subprocess.run(argv, input=task.encode("utf-8"),
                           cwd=c, env=env, capture_output=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"code": -2, "answer": "", "stderr_tail": "dsh 超时未完成（长时间无响应）"}
    except OSError as e:
        return {"code": -3, "answer": "", "stderr_tail": f"无法启动 node/dsh：{e}"}
    out = p.stdout.decode("utf-8", "replace").strip()
    err = p.stderr.decode("utf-8", "replace")
    # 用最后的错误行做提示（stderr 里还混着 reasoning 流，取行末错误段）
    tail = "\n".join(err.splitlines()[-6:])[-1600:]
    return {"code": p.returncode, "answer": out, "stderr_tail": tail}


def check_ok(res: dict) -> bool:
    return res.get("code") == 0 and bool(res.get("answer", "").strip())