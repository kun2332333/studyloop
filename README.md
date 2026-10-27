# 拾知 StudyLoop

一个**本地运行、离线可用**的学习助手：

- 导入 PPT / PDF 讲义后，AI 会像老师一样**逐页讲解**（英文术语自动翻译成中文），并只从「与本课主线直接相关、必须长期记住」的核心考点草拟间隔复习卡片，由你勾选导入；
- 复习调度基于 **FSRS-4.5** 遗忘曲线算法（提取练习 + 间隔重复 + 交错练习）；
- 附带学习打卡、专注计时、随堂测、学习数据统计（打卡热力图、记忆保持率曲线、元认知校准）。

技术栈：纯 Python 标准库后端（http.server + SQLite），原生 HTML/CSS/JS 前端，**零依赖、无构建、双击即用**；AI 引擎支持 DeepSeek 直连 API 或本机 [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)（headless 模式）。

## 快速开始

1. 装好 [Python 3.10+](https://www.python.org/)（安装时勾选 *Add python.exe to PATH*）；
2. 双击 `启动拾知.bat`，浏览器会自动打开 `http://127.0.0.1:8765`。

完整说明见 [使用说明.md](使用说明.md)。

## 目录

```
├─ 启动拾知.bat          ← 双击启动
├─ app.py                ← 本地服务（Python 标准库）
├─ core\                 ← 解析、FSRS 调度、统计、AI 引擎（直连 + dsh 桥接 + 逐页讲解）
├─ web\                  ← 前端（原生 HTML/CSS/JS）
├─ vendor\               ← 随包附带的 pypdf（解析 PDF）
└─ 样例讲义\             ← 示例 PPTX，可以先拿它试手
```

> 学习记录、卡片、打卡全部只存本机 `data/` 目录（已在 .gitignore 中排除）。