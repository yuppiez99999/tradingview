#!/usr/bin/env python
"""CNB → 本地 main → GitHub(origin) 确定性同步器（跨线合并 playbook 的可执行版）。

背景
----
`C:\\Users\\Administrator\\sync_npc.ps1` 原先是 3 行：`git pull --ff-only cnb main` +
`git push origin main`。**分叉时 ff-only 必然失败并静默跳过** —— 而"CNB 自动线前进 + 本地也有提交"
恰恰是常态，于是同步长期退化为人工操作。本脚本把那套人工流程（见
`cairn/merge-and-gate-playbook-20260911.md`）固化为确定性步骤。

判定与退出码（**fail-stop，绝不覆盖任何人的工作**）
--------------------------------------------------
  0  已同步 / 本次同步成功（含 push）
  1  检测到风险，**未做任何改动**：脏文件 ∩ 入站改动非空 / 冲突超出白名单 / merge 提交被门禁拦下
  2  本地已同步但 push 失败（远端有新提交或凭证问题）—— 不回滚、不重试、不强推

安全边界
--------
  * 只 `fetch / merge / add <白名单冲突文件> / commit / push`，绝不 `reset --hard`、绝不 `clean`、绝不强推；
  * 冲突**只允许**出现在 append-only 日志 `cairn/LOG.md`，且解法固定为"取并集"（只删冲突标记行，两侧条目全留）；
  * 遇到未提交的他人 WIP 与入站改动相交时，**直接放弃**（不 stash、不提交别人的工作）。

用法
----
  .venv/Scripts/python.exe scripts/sync_cnb_to_github.py --dry-run   # 只体检不改动
  .venv/Scripts/python.exe scripts/sync_cnb_to_github.py             # 正式同步并回流 GitHub
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO = _PROJECT_ROOT  # 可被 --repo 覆盖（便于在临时仓库上做端到端测试）

#: append-only 日志白名单：冲突出现在这些文件里可按"取并集"自动解决
_LOG_WHITELIST = {"cairn/LOG.md"}


def _run(args: list[str], *, check: bool = False) -> subprocess.CompletedProcess:
    """执行 git 命令（统一 UTF-8 解码；不抛异常，由调用方判 returncode）。"""
    proc = subprocess.run(
        ["git", *args],
        cwd=_REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败: {proc.stderr.strip()}")
    return proc


def _lines(text: str) -> list[str]:
    return [x for x in (text or "").splitlines() if x.strip()]


def _divergence(remote_ref: str) -> tuple[int, int]:
    """返回 (本地领先, 本地落后)。"""
    proc = _run(["rev-list", "--left-right", "--count", f"HEAD...{remote_ref}"])
    if proc.returncode != 0:
        raise SystemExit(f"无法比对与 {remote_ref} 的分歧: {proc.stderr.strip()}")
    ahead, behind = (int(x) for x in proc.stdout.split())
    return ahead, behind


def _strip_conflict_markers(path: str) -> int:
    """取并集：删除冲突标记行，冲突块两侧的普通行全部保留。返回删除的标记数。"""
    abs_path = os.path.join(_REPO, path)
    with open(abs_path, encoding="utf-8", newline="") as fh:
        raw = fh.read()
    out: list[str] = []
    in_conflict = 0
    removed = 0
    for line in raw.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if stripped.startswith("<<<<<<< "):
            in_conflict += 1
            removed += 1
            continue
        if in_conflict and set(stripped) == {"="} and len(stripped) >= 7:
            removed += 1
            continue
        if stripped.startswith(">>>>>>> "):
            in_conflict -= 1
            removed += 1
            continue
        out.append(line)
    if in_conflict != 0:
        raise SystemExit(f"{path} 冲突标记不配对，拒绝自动解决")
    with open(abs_path, "w", encoding="utf-8", newline="") as fh:
        fh.write("".join(out))
    return removed


def main(argv: list[str] | None = None) -> int:
    global _REPO

    parser = argparse.ArgumentParser(description="CNB → 本地 → GitHub 确定性同步")
    parser.add_argument("--repo", default=_PROJECT_ROOT, help="目标仓库路径 (默认本项目根)")
    parser.add_argument("--remote", default="cnb", help="上游 remote 名 (默认 cnb)")
    parser.add_argument("--branch", default="main", help="分支 (默认 main)")
    parser.add_argument("--push-remote", default="origin", help="回流 remote 名 (默认 origin)")
    parser.add_argument("--dry-run", action="store_true", help="只体检不改动")
    parser.add_argument("--no-push", action="store_true", help="只同步到本地, 不回流 GitHub")
    args = parser.parse_args(argv)

    _REPO = os.path.abspath(args.repo)
    remote_ref = f"{args.remote}/{args.branch}"
    print(f"[sync] {args.remote}:{args.branch} -> 本地 -> {args.push_remote}")

    _run(["fetch", args.remote, args.branch], check=True)
    ahead, behind = _divergence(remote_ref)
    head = _run(["rev-parse", "--short", "HEAD"]).stdout.strip()
    print(f"[sync] HEAD={head} 领先={ahead} 落后={behind}")
    if behind == 0:
        print("[sync] 无需同步（本地已包含上游全部提交）")
        return 0

    # ---- 预检 1：脏文件 ∩ 入站改动 ----
    dirty = {x[3:].strip().strip('"') for x in _lines(_run(["status", "--porcelain"]).stdout)}
    incoming = set(_lines(_run(["diff", "--name-only", f"HEAD...{remote_ref}"]).stdout))
    overlap = sorted(dirty & incoming)
    if overlap:
        print(f"[sync] 停止：未提交改动与入站改动相交 {overlap}（不 stash 他人 WIP）")
        return 1

    if args.dry_run:
        print(f"[sync] DRY-RUN：{behind} 个提交待合并，预检通过（脏文件无相交）")
        return 0

    # ---- 合并 ----
    subject = _run(["log", "-1", "--pretty=%s", remote_ref]).stdout.strip()
    proc = _run(["merge", remote_ref, "-m", f"merge({args.remote}): 同步 {subject}"])
    if proc.returncode != 0:
        conflicted = set(_lines(_run(["diff", "--name-only", "--diff-filter=U"]).stdout))
        if not conflicted or not conflicted <= _LOG_WHITELIST:
            _run(["merge", "--abort"])
            print(f"[sync] 停止：冲突超出白名单 {sorted(conflicted)}，已 abort（未留冲突现场）")
            return 1
        for path in sorted(conflicted):
            removed = _strip_conflict_markers(path)
            _run(["add", path])
            print(f"[sync] 冲突取并集：{path} 删除 {removed} 个标记行，两侧条目全留")
        proc = _run(["commit", "--no-edit"])
        if proc.returncode != 0:
            _run(["merge", "--abort"])
            print("[sync] 停止：合并提交被 pre-commit 门禁拦下，已 abort —— 需人工处理")
            return 1

    new_head = _run(["rev-parse", "--short", "HEAD"]).stdout.strip()
    print(f"[sync] 合并完成 HEAD={new_head}")

    if args.no_push:
        print("[sync] 按参数跳过回流 GitHub")
        return 0

    # ---- 回流 GitHub ----
    push = _run(["-c", "http.version=HTTP/1.1", "push", args.push_remote, args.branch])
    if push.returncode != 0:
        print(f"[sync] push 失败（未强推）：{_lines(push.stderr)[-1] if _lines(push.stderr) else ''}")
        return 2
    final = _run(["rev-parse", "--short", f"{args.push_remote}/{args.branch}"]).stdout.strip()
    print(f"[sync] push 完成 {args.push_remote}/{args.branch}={final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
