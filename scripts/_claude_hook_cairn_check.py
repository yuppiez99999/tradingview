#!/usr/bin/env python3
"""Claude Code Cairn 上下文 Hook (SessionStart / Stop).

本脚本服务于 Wave 0 开发工作流自动化, 实现 AGENTS.md 知识沉淀规则的自动化:

  - SessionStart: 输出 cairn/LOG.md 最近 3 条进展 + ROADMAP 当前焦点,
    作为会话初始上下文注入 (符合 AGENTS.md "进入项目后的阅读顺序").
  - Stop: 校验本次会话是否修改了代码但未追加 cairn/LOG.md 条目,
    信息性警告 (不阻断会话结束, 遵循 AGENTS.md "知识沉淀规则").

调用模式:
  1. SessionStart hook (从 stdin 读取 JSON):
       python scripts/_claude_hook_cairn_check.py --mode session-start --stdin
  2. Stop hook (从 stdin 读取 JSON):
       python scripts/_claude_hook_cairn_check.py --mode stop --stdin
  3. 独立查看上下文:
       python scripts/_claude_hook_cairn_check.py --mode session-start

退出码:
  0 = 正常 (hook 放行; SessionStart 的 stdout 会被注入为上下文)
  2 = 脚本错误 (放行, 不阻断)

设计参考: AGENTS.md "进入项目后的阅读顺序" + "知识沉淀规则"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ============================================================
# 路径定位
# ============================================================


def _find_project_root() -> Path:
    """向上查找项目根目录 (通过 AGENTS.md / cairn/ 识别)."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if (current / "AGENTS.md").exists() and (current / "cairn").is_dir():
            return current
        if current.parent == current:
            break
        current = current.parent
    # 回退: 脚本所在目录的父目录
    return Path(__file__).resolve().parent.parent


_PROJECT_ROOT = _find_project_root()
_CAIRN_DIR = _PROJECT_ROOT / "cairn"
_LOG_FILE = _CAIRN_DIR / "LOG.md"
_ROADMAP_FILE = _CAIRN_DIR / "ROADMAP.md"


# ============================================================
# SessionStart: 输出 cairn 上下文
# ============================================================


def _read_log_recent(n: int = 3) -> str:
    """读取 cairn/LOG.md 最近的 n 条进展 (以 ## 标题分条)."""
    if not _LOG_FILE.exists():
        return "(cairn/LOG.md 不存在, 可能尚未初始化 Project Cairn)"
    try:
        content = _LOG_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        return f"(读取 cairn/LOG.md 失败: {exc})"

    # 按 ## 标题切分 (保留标题)
    lines = content.split("\n")
    sections: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if current:
                sections.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current))

    # 跳过文件头 (第一个 section 通常是文件说明), 取后续 n 条
    if len(sections) <= 1:
        return "(cairn/LOG.md 暂无进展条目)"
    recent = sections[1 : 1 + n]
    return "\n\n".join(recent) if recent else "(cairn/LOG.md 暂无进展条目)"


def _read_roadmap_focus() -> str:
    """读取 cairn/ROADMAP.md 的当前焦点 (查找 "当前焦点" / "Current Focus" 段落)."""
    if not _ROADMAP_FILE.exists():
        return "(cairn/ROADMAP.md 不存在)"
    try:
        content = _ROADMAP_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        return f"(读取 cairn/ROADMAP.md 失败: {exc})"

    # 查找当前焦点段落 (## 当前焦点 / ## Current Focus / ## 焦点)
    lines = content.split("\n")
    in_focus = False
    focus_lines: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if in_focus:
                break  # 下一个 ## 段落, 结束
            if any(kw in line for kw in ("当前焦点", "Current Focus", "焦点", "Focus")):
                in_focus = True
                focus_lines.append(line)
        elif in_focus:
            focus_lines.append(line)

    if not focus_lines:
        return "(ROADMAP 未标记当前焦点段落)"
    return "\n".join(focus_lines[:30])  # 限制行数


def handle_session_start() -> int:
    """SessionStart hook: 输出 cairn 上下文到 stdout (会被注入为会话上下文).

    Returns:
        0 = 正常
    """
    print("=== Project Cairn 上下文 (SessionStart 自动加载) ===\n")
    print("--- cairn/ROADMAP.md 当前焦点 ---")
    print(_read_roadmap_focus())
    print("\n--- cairn/LOG.md 最近 3 条进展 ---")
    print(_read_log_recent(n=3))
    print("\n=== 提示: 实质性推进后, 在 cairn/LOG.md 顶部追加条目 (AGENTS.md 知识沉淀规则) ===")
    return 0


# ============================================================
# Stop: 校验 cairn/LOG.md 是否更新
# ============================================================


def _read_stdin_json() -> dict[str, Any] | None:
    """读取 stdin JSON, 失败返回 None."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return None
        return json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return None


def _get_log_top_mtime() -> float:
    """获取 cairn/LOG.md 的修改时间; 不存在返回 0."""
    if not _LOG_FILE.exists():
        return 0.0
    try:
        return _LOG_FILE.stat().st_mtime
    except OSError:
        return 0.0


def handle_stop() -> int:
    """Stop hook: 校验本次会话是否修改了代码但未追加 cairn/LOG.md.

    策略: 信息性警告, 不阻断会话结束 (返回 0).
    若会话修改了 .py 文件但 LOG.md 未在最近 30 分钟内更新, 输出提醒.

    Returns:
        0 = 正常 (始终放行, 仅信息提醒)
    """
    data = _read_stdin_json()
    # Stop hook 的 stdin JSON 可能包含 session 信息, 但我们主要靠文件 mtime 判断

    log_mtime = _get_log_top_mtime()
    now = datetime.now().timestamp()
    log_stale = (now - log_mtime) > 1800  # 30 分钟

    # 简单检查: 是否有最近 30 分钟内修改的 .py 文件 (抽样 ai_decision/ 和 v8.3_institutional/src/)
    recent_py_changes = False
    check_dirs = [
        _PROJECT_ROOT / "ai_decision",
        _PROJECT_ROOT / "v8.3_institutional" / "src",
        _PROJECT_ROOT / "utils",
    ]
    for d in check_dirs:
        if not d.exists():
            continue
        try:
            for py in d.rglob("*.py"):
                try:
                    if py.stat().st_mtime > now - 1800:
                        recent_py_changes = True
                        break
                except OSError:
                    continue
        except OSError:
            continue
        if recent_py_changes:
            break

    if recent_py_changes and log_stale:
        print(
            "[cairn 提醒] 检测到最近 30 分钟内有 .py 文件修改, "
            "但 cairn/LOG.md 未更新. 若本次为实质性推进, "
            "请在 LOG.md 顶部追加条目 (AGENTS.md 知识沉淀规则).",
            file=sys.stderr,
        )

    return 0


# ============================================================
# 主入口
# ============================================================


def main(argv: list[str]) -> int:
    """主入口: 按模式路由.

    Args:
        argv: 命令行参数

    Returns:
        0 = 正常, 2 = 脚本错误
    """
    parser = argparse.ArgumentParser(
        description="Claude Code Cairn 上下文 Hook (SessionStart / Stop)",
    )
    parser.add_argument(
        "--mode",
        choices=["session-start", "stop"],
        required=True,
        help="hook 模式: session-start (输出上下文) / stop (校验 LOG.md)",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="从 stdin 读取 Claude Code hook JSON (可选, 当前实现主要靠文件 mtime)",
    )
    args = parser.parse_args(argv[1:])

    try:
        if args.mode == "session-start":
            return handle_session_start()
        if args.mode == "stop":
            return handle_stop()
    except (OSError, ValueError, TypeError) as exc:
        print(f"[cairn hook] 错误, 放行: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
