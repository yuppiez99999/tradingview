#!/usr/bin/env python
"""代码审查卡点门禁 (review_gate) — v4 审查体系的可执行层.

把 docs/代码审查体系_v4_20260829.md 的 G0/G2/G5 三道卡点落到命令与退出码上。
标准写进文档只是建议, 绑到命令 + 退出码才是门禁。

用法:
    python scripts/review_gate.py                      # G0 分支纪律 + G5 债务状态
    python scripts/review_gate.py --files a.py b.py    # 追加 G2 三维度 (ruff/black/pytest)
    python scripts/review_gate.py --pre-commit         # pre-commit 模式: RED 时按分支类型拦截
    python scripts/review_gate.py --skip-debt          # 跳过 G5 (提速, 用于高频本地检查)

退出码:
    0  通过
    1  P0 阻断 (分支名不合规 / 三维度未过)
    2  债务 RED 且当前分支类型不允许开工 (仅 --pre-commit 与带分支检查时)
    3  门禁自身异常 (fail-close: 门禁挂了必须阻断, 不能静默放行)

设计约束:
    - 兼容 Python 3.8+ (不用 walrus / f-string 调试符 / dict 合并运算符)
    - Windows GBK 环境安全: 不输出 ¥ 等非 GBK 字符, 金额一律写 RMB
    - 门禁自身 fail-close: 任何未预期异常都返回 3, 绝不静默放行
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- 常量配置

REPO_ROOT = Path(__file__).resolve().parent.parent

# G0: 允许的分支前缀
ALLOWED_BRANCH_PREFIXES = ("feat/", "fix/", "refactor/", "hotfix/")
# 受保护分支自身允许直推前的检查豁免 (在 main/master 上不校验分支名)
PROTECTED_BRANCHES = ("main", "master")

# G5: RED 债务期间允许开工的分支类型 (只许还债和救火)
RED_ALLOWED_PREFIXES = ("fix/", "hotfix/")

# 债务等级缓存 (debt_gate 跑一次较慢, 本地高频检查用缓存)
DEBT_CACHE_PATH = REPO_ROOT / "cache" / "review_gate_debt.json"
DEBT_CACHE_TTL_SECONDS = 6 * 3600  # 6 小时

# 工具路径: 优先用项目 venv, 避免系统 site-packages 污染 (GBK 坑)
if os.name == "nt":
    VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    VENV_RUFF = REPO_ROOT / ".venv" / "Scripts" / "ruff.exe"
    VENV_BLACK = REPO_ROOT / ".venv" / "Scripts" / "black.exe"
else:
    VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
    VENV_RUFF = REPO_ROOT / ".venv" / "bin" / "ruff"
    VENV_BLACK = REPO_ROOT / ".venv" / "bin" / "black"

EXIT_OK = 0
EXIT_BLOCK = 1
EXIT_DEBT_RED = 2
EXIT_GATE_ERROR = 3

# 输出符号: 避免 emoji/特殊字符在 GBK 控制台炸掉
SYM_OK = "[OK]"
SYM_NO = "[XX]"
SYM_WARN = "[!!]"


# ---------------------------------------------------------------- 基础工具


def _run(cmd, timeout=300, cwd=None):
    """执行命令, 返回 (returncode, stdout, stderr). 异常时返回 (None, '', str(e))."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd or REPO_ROOT),
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return (
            None,
            "",
            "timeout after {}s: {}".format(timeout, " ".join(str(c) for c in cmd)),
        )
    except OSError as exc:
        return None, "", f"OSError: {exc}"

    def _decode(raw):
        if raw is None:
            return ""
        return raw.decode("utf-8", errors="replace")

    return proc.returncode, _decode(proc.stdout), _decode(proc.stderr)


def _tool(exe_path, fallback_name):
    """优先返回项目 venv 内的工具可执行文件, 缺失则回退到 PATH 上的同名命令."""
    if exe_path and Path(exe_path).exists():
        return str(exe_path)
    return fallback_name


# ---------------------------------------------------------------- G0 分支纪律


def get_current_branch():
    code, out, _err = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], timeout=30)
    if code != 0:
        return None
    return out.strip() or None


def check_branch(branch):
    """G0: 分支名必须匹配 feat|fix|refactor|hotfix 之一.

    返回 (passed, message).
    """
    if branch is None:
        return False, "无法获取当前分支 (git 不可用或不在仓库内)"
    if branch in PROTECTED_BRANCHES:
        return True, f"当前在受保护分支 {branch} 上"
    if branch == "HEAD":
        return True, "当前处于 detached HEAD, 跳过分支名校验"
    for prefix in ALLOWED_BRANCH_PREFIXES:
        if branch.startswith(prefix):
            return True, f"分支名合规: {branch}"
    return False, (
        "分支名 {!r} 不合规, 必须为 {} 之一. 分支名是回溯唯一锚点, "
        "实盘定位变更来源时无意义的名字提供不了任何信息.".format(
            branch, ", ".join(ALLOWED_BRANCH_PREFIXES)
        )
    )


# ---------------------------------------------------------------- G5 债务状态


def _load_debt_cache():
    if not DEBT_CACHE_PATH.exists():
        return None
    try:
        with open(DEBT_CACHE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError):
        return None
    if time.time() - float(data.get("ts", 0)) > DEBT_CACHE_TTL_SECONDS:
        return None
    return data


def _save_debt_cache(level, code):
    try:
        DEBT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(DEBT_CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "ts": time.time(),
                    "level": level,
                    "exit_code": code,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                fh,
                ensure_ascii=False,
                indent=2,
            )
    except OSError:
        pass  # 缓存写失败不影响门禁判定


def get_debt_level(use_cache=True):
    """运行 engineering_debt_gate.py 取债务等级.

    退出码约定 (见 skills/AGENT_SKILLS_ADAPTER.md §1): 0=GREEN 1=YELLOW 2=RED
    返回 (level, exit_code, detail)
    """
    if use_cache:
        cached = _load_debt_cache()
        if cached is not None:
            return (
                cached.get("level", "UNKNOWN"),
                cached.get("exit_code", -1),
                "缓存于 {}".format(cached.get("time", "?")),
            )

    script = REPO_ROOT / "scripts" / "engineering_debt_gate.py"
    if not script.exists():
        return "UNKNOWN", -1, "engineering_debt_gate.py 不存在, 债务状态未知"

    python_exe = _tool(VENV_PYTHON, sys.executable)
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        proc = subprocess.run(
            [python_exe, str(script)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            timeout=900,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return "UNKNOWN", -2, "debt_gate 执行超时 (>900s)"
    except OSError as exc:
        return "UNKNOWN", -2, f"debt_gate 无法执行: {exc}"

    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    code = proc.returncode

    level = {0: "GREEN", 1: "YELLOW", 2: "RED"}.get(code, "UNKNOWN")

    # 从输出里抓一句最有信息量的原因
    detail = ""
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("[XX]"):
            detail = stripped
            break
    if not detail:
        detail = "无失败项详情"

    if code in (0, 1, 2):
        _save_debt_cache(level, code)

    return level, code, detail


def check_debt_gate(branch):
    """G5: 债务 RED 期间, feat/refactor 分支不得开工.

    返回 (exit_code, message)
    """
    level, code, detail = get_debt_level()
    if level == "UNKNOWN":
        return EXIT_GATE_ERROR, f"债务状态未知 ({detail}), 按 fail-close 阻断"

    if level != "RED":
        return EXIT_OK, f"债务等级 {level} — 允许开工 ({detail})"

    # RED: 只允许 fix/ 与 hotfix/
    if branch in PROTECTED_BRANCHES or branch == "HEAD":
        return EXIT_DEBT_RED, (
            f"债务等级 RED 但当前在 {branch} 上, 无法判断变更意图 — 请切到 fix/* 分支还债"
        )
    for prefix in RED_ALLOWED_PREFIXES:
        if branch and branch.startswith(prefix):
            return EXIT_OK, f"债务 RED 期间允许 {prefix} 还债/救火分支"
    return EXIT_DEBT_RED, (
        "债务等级 RED — 冻结 feat/* 与 refactor/* 开工, 仅允许 fix/* 与 hotfix/*. "
        f"失败项: {detail}"
    )


# ---------------------------------------------------------------- G2 三维度


def check_three_dimensions(files):
    """G2: ruff (工程) + black (格式) + pytest (语义). 三维度缺一不可.

    来源: 08-29 CTX-A 教训 — 94 个用例全绿就宣布完成, ruff/black 根本没跑.
    返回 (exit_code, messages)
    """
    messages = []
    failed = False

    existing = []
    for raw in files:
        path = Path(raw)
        if not path.is_absolute():
            path = REPO_ROOT / raw
        if not path.exists():
            messages.append(f"{SYM_WARN} 跳过不存在的文件: {raw}")
            continue
        if path.suffix != ".py":
            continue
        existing.append(str(path))

    if not existing:
        return EXIT_OK, ["没有需要检查的 Python 文件"]

    # 1) 工程维度: ruff
    ruff = _tool(VENV_RUFF, "ruff")
    code, out, err = _run([ruff, "check"] + existing, timeout=300)
    if code is None:
        messages.append(f"{SYM_NO} ruff 执行失败: {err} (按 fail-close 视为未通过)")
        failed = True
    elif code == 0:
        messages.append(f"{SYM_OK} ruff: 0 告警 ({len(existing)} 文件)")
    else:
        failed = True
        messages.append(f"{SYM_NO} ruff 发现问题 ({len(existing)} 文件):")
        for line in (out + err).splitlines():
            if line.strip():
                messages.append(f"      {line.strip()}")

    # 2) 格式维度: black --check
    black = _tool(VENV_BLACK, "black")
    code, out, err = _run([black, "--check"] + existing, timeout=300)
    if code is None:
        messages.append(f"{SYM_NO} black 执行失败: {err} (按 fail-close 视为未通过)")
        failed = True
    elif code == 0:
        messages.append(f"{SYM_OK} black: 格式已合规")
    else:
        failed = True
        messages.append(
            "{} black: 需要格式化 (跑 black {})".format(SYM_NO, " ".join(files))
        )
        for line in (out + err).splitlines():
            if "would reformat" in line:
                messages.append(f"      {line.strip()}")

    # 3) 语义维度: pytest 收集 + 执行相关测试
    #    只跑与改动文件同名的测试, 全量 4888 用例太慢; 找不到相关测试时提示但不阻断
    python_exe = _tool(VENV_PYTHON, sys.executable)
    related = _find_related_tests(existing)
    if related:
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        try:
            proc = subprocess.run(
                [python_exe, "-m", "pytest", "-q"] + related,
                cwd=str(REPO_ROOT),
                capture_output=True,
                timeout=900,
                env=env,
            )
        except subprocess.TimeoutExpired:
            messages.append(
                f"{SYM_NO} pytest 执行超时 (>900s), 按 fail-close 视为未通过"
            )
            failed = True
        else:
            out = (proc.stdout or b"").decode("utf-8", errors="replace")
            if proc.returncode == 0:
                tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1:]
                messages.append(
                    "{} pytest: PASS ({})".format(SYM_OK, tail[0] if tail else "ok")
                )
            else:
                failed = True
                messages.append(
                    f"{SYM_NO} pytest: FAILED ({len(related)} 个相关测试文件)"
                )
                for line in out.strip().splitlines()[-15:]:
                    messages.append(f"      {line.strip()}")
    else:
        messages.append(
            f"{SYM_WARN} 未找到与改动文件对应的测试, 语义维度未覆盖 — 若改动涉及资金/数据/风控路径, "
            "必须补契约测试 (docs/代码审查体系_v4 §3)"
        )

    return (EXIT_BLOCK if failed else EXIT_OK), messages


def _find_related_tests(changed_files):
    """按文件名推断相关测试文件 (tests/**/test_<stem>.py)."""
    tests_root = REPO_ROOT / "tests"
    if not tests_root.exists():
        return []
    related = []
    for raw in changed_files:
        stem = Path(raw).stem
        if stem.startswith("test_"):
            continue
        pattern = f"test_{stem}.py"
        for found in tests_root.rglob(pattern):
            if str(found) not in related:
                related.append(str(found))
    return related[:20]  # 防止误匹配到过多文件拖慢检查


# ---------------------------------------------------------------- 主流程


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="代码审查卡点门禁 (G0 分支纪律 / G2 三维度 / G5 债务状态)"
    )
    parser.add_argument(
        "--files", nargs="*", default=None, help="改动文件列表, 触发 G2 三维度检查"
    )
    parser.add_argument(
        "--pre-commit",
        action="store_true",
        help="pre-commit 模式: RED 债务按分支类型拦截提交",
    )
    parser.add_argument(
        "--skip-debt", action="store_true", help="跳过 G5 债务检查 (提速)"
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="忽略债务等级缓存, 强制重跑 debt_gate"
    )
    args = parser.parse_args(argv)

    if args.no_cache and DEBT_CACHE_PATH.exists():
        try:
            DEBT_CACHE_PATH.unlink()
        except OSError:
            pass

    print("=" * 60)
    print("代码审查卡点门禁 (review_gate) — 体系 v4")
    print("=" * 60)

    results = []

    # ---- G0 分支纪律
    branch = get_current_branch()
    ok, msg = check_branch(branch)
    print(f"{SYM_OK if ok else SYM_NO} G0 分支纪律: {msg}")
    results.append(("G0", EXIT_OK if ok else EXIT_BLOCK))

    # ---- G5 债务状态
    if args.skip_debt:
        print(f"{SYM_WARN} G5 债务状态: 已跳过 (--skip-debt)")
    else:
        code, msg = check_debt_gate(branch)
        sym = (
            SYM_OK
            if code == EXIT_OK
            else (SYM_NO if code == EXIT_DEBT_RED else SYM_WARN)
        )
        print(f"{sym} G5 债务状态: {msg}")
        # G5 的 RED 只在 pre-commit 模式下阻断提交, 平时只告警 (避免打断只读检查)
        results.append(("G5", code if args.pre_commit else EXIT_OK))

    # ---- G2 三维度
    if args.files:
        code, msgs = check_three_dimensions(args.files)
        print(
            "%s G2 三维度 (ruff/black/pytest):"
            % (SYM_OK if code == EXIT_OK else SYM_NO)
        )
        for line in msgs:
            print(f"   {line}")
        results.append(("G2", code))

    # ---- 汇总
    print("-" * 60)
    worst = EXIT_OK
    for name, code in results:
        if code == EXIT_OK:
            status = "PASS"
        elif code == EXIT_BLOCK:
            status = "BLOCK"
        elif code == EXIT_DEBT_RED:
            status = "RED-FREEZE"
        else:
            status = "ERROR"
        print(f"  {name:<4} {status}")
        if code > worst:
            worst = code

    print("-" * 60)
    if worst == EXIT_OK:
        print("  判定: PASS")
    elif worst == EXIT_BLOCK:
        print("  判定: BLOCK — 存在 P0 阻断项, 修复后才能前进")
    elif worst == EXIT_DEBT_RED:
        print("  判定: RED-FREEZE — 债务 RED, 冻结新功能开工 (见 v4 §G5)")
    else:
        print("  判定: ERROR — 门禁自身异常, 按 fail-close 阻断")
    print("=" * 60)

    return worst


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — 门禁顶层必须兜底, 异常时 fail-close
        print(f"{SYM_NO} review_gate 自身异常, 按 fail-close 阻断: {exc}")
        sys.exit(EXIT_GATE_ERROR)
