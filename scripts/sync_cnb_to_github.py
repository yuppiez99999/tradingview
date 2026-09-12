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
  1  检测到风险，**未做任何改动**：项目任务运行中（busy-guard）/ 任务状态查不到 /
     脏文件 ∩ 入站改动非空 / 冲突超出白名单 / merge 提交被门禁拦下
  2  本地已同步但 push 失败（远端有新提交或凭证问题）—— 不回滚、不重试、不强推

安全边界
--------
  * 只 `fetch / merge / add <白名单冲突文件> / commit / push`，绝不 `reset --hard`、绝不 `clean`、绝不强推；
  * 冲突**只允许**出现在 append-only 日志 `cairn/LOG.md`（取并集：只删冲突标记行，两侧条目全留）
    与 `specs/*/tasks.md`（复选框并集：勾选状态 OR，保留已勾版本 —— spec-kit 集成 2026-09-11）；
    其余 `specs/` 文件（spec.md/plan.md）冲突一律挂起人工裁决，绝不自动解决；
  * 遇到未提交的他人 WIP 与入站改动相交时，**直接放弃**（不 stash、不提交别人的工作）；
  * **busy-guard**（2026-09-11 新增）：合并会改写工作区文件，故当"运行中的项目任务"存在时
    拒绝合并（`--allow-busy` 可强制）。起因是 Windows 计划任务 `QuantNPC_Sync_0900` 与
    `v84_PreMarketInstructions`（当日唯一产出交易指令的任务）**同在 09:00**；脏文件预检
    防不住该竞态（它只防未提交改动被覆盖，不防运行中进程读到改写中的文件）。

用法
----
  .venv/Scripts/python.exe scripts/sync_cnb_to_github.py --dry-run   # 只体检不改动
  .venv/Scripts/python.exe scripts/sync_cnb_to_github.py             # 正式同步并回流 GitHub
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO = _PROJECT_ROOT  # 可被 --repo 覆盖（便于在临时仓库上做端到端测试）

#: 单条 git 命令超时（秒）：防计划任务被挂死到时限（见 `_run` 说明）。
_GIT_TIMEOUT_SECONDS = 600

#: append-only 日志白名单：冲突出现在这些文件里可按"取并集"自动解决
_LOG_WHITELIST = {"cairn/LOG.md"}

#: specs tasks.md 复选框并集白名单（spec-kit 集成 2026-09-11）：
#: SDD 任务清单冲突 = 两端各自勾了任务 —— 勾选状态取 OR（真做了任务的一端不该被抹掉）。
#: 非 checkbox 行的冲突仍走 marker-strip 后保守保留（同 LOG.md 语义）；
#: specs/ 下其余文件（spec.md/plan.md）冲突不在白名单 → 挂起人工裁决（fail-closed）。
_SPECS_TASKS_RE = re.compile(r"^specs/[^/]+/tasks\.md$")


def _is_union_allowed(path: str) -> bool:
    """冲突可自动解决（取并集）的文件：LOG.md 白名单 或 specs/*/tasks.md。"""
    return path in _LOG_WHITELIST or bool(_SPECS_TASKS_RE.match(path))

#: busy-guard —— 合并会 **改写工作区文件**，若此刻有项目任务正在跑（它可能正在
#: import 这些文件），就会读到半写状态或版本混用。2026-09-11 实测：Windows 计划
#: 任务 `QuantNPC_Sync_0900` 与 `v84_PreMarketInstructions`（唯一产出当日交易指令
#: 的任务）**同在 09:00**，而当日 CNB 的 PR#20 恰好改写了 `daily_trade_executor.py`。
#: 注意：原有的"脏文件∩入站"预检**防不住**这个竞态（它只防未提交改动被覆盖，
#: 不防运行中的进程读到被改写的文件），故单列本守卫。
#: 判据 = 任务名前缀属项目 + 动作命令行含本仓库标记（两条都满足才算"忙"），
#: 以免把常驻服务/无关任务（Clash、输入法…）误判成忙碌。
_BUSY_TASK_PREFIXES = (
    "v84_",
    "V84_",
    "S84_",
    "S12_",
    "GNN_",
    "EOD_",
    "Shadow30Day",
    "DailyShadowSample",
    "System_HealthScore",
    "PhaseB",
    "TDAM_MemoryCore",
    "QuantNPC_",
)

#: 例外：同步任务自身（本脚本此刻正是被它调起的）—— 必须排除，否则自锁。
_SELF_TASK_PREFIXES = ("QuantNPC_Sync",)

#: 任务动作里出现该标记即视为"操作本仓库"。
_BUSY_REPO_MARKER = "28-终极量化交易系统8.4"


def _busy_conflicts(
    running: list[tuple[str, str]], repo_marker: str = _BUSY_REPO_MARKER
) -> list[str]:
    """从"正在运行的任务"里挑出会读写本仓库的项目任务（纯函数，便于单测）。"""
    hits: list[str] = []
    for name, cmd in running:
        name_s, cmd_s = str(name), str(cmd or "")
        if not name_s.startswith(_BUSY_TASK_PREFIXES):
            continue
        if name_s.startswith(_SELF_TASK_PREFIXES):
            continue
        if repo_marker not in cmd_s:
            continue
        hits.append(name_s)
    return sorted(set(hits))


def _busy_verdict(running: list[tuple[str, str]] | None, behind: int) -> tuple[bool, str]:
    """busy-guard 判定 -> (是否继续, 说明)。`running=None` 表示查询失败（未知≠通过）。

    只在"本轮需要合并"（`behind>0`，合并会改写工作区）时拦截；仅需 push 时不动
    工作区，故放行 —— 这样守卫不会因为"某个长任务在跑"就把回流 GitHub 也堵死。
    """
    if running is None:
        if behind:
            return False, (
                "busy-guard: 无法确认计划任务状态（未知≠通过）"
                " —— 查 PowerShell/任务计划服务，或手动加 --allow-busy"
            )
        return True, "busy-guard: 状态未知（本轮只需 push，不动工作区，放行）"
    busy = _busy_conflicts(running)
    if busy and behind:
        return False, (
            f"busy-guard: 项目任务正在运行 {busy}"
            " —— 合并会改写其正在读取的代码（确认无碍可加 --allow-busy）"
        )
    if busy:
        return True, f"busy-guard: {busy} 在运行，但本轮只需 push（不动工作区），放行"
    return True, f"busy-guard: 无项目任务在运行（已排除 {len(running)} 个运行中任务）"


def _query_running_tasks() -> list[tuple[str, str]] | None:
    """查询正在运行的计划任务 -> [(name, 命令行)]；**查询失败返回 None**（≠ 无任务）。

    返回值必须区分"没有任务在跑"([]) 与"查不到"（None）：前者可放行，后者按
    严格侧处理（未知≠通过），否则任务计划服务异常时守卫会静默失效。
    """
    ps = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "try {"
        "  $t = Get-ScheduledTask | Where-Object { $_.State -eq 'Running' } | ForEach-Object {"
        "    $n = $_.TaskName;"
        "    $c = (($_.Actions | ForEach-Object { \"$($_.Execute) $($_.Arguments)\" }) -join ' || ');"
        "    [PSCustomObject]@{ name = $n; cmd = $c }"
        "  };"
        "  [PSCustomObject]@{ ok = $true; tasks = @($t) } | ConvertTo-Json -Compress -Depth 4"
        "} catch {"
        "  [PSCustomObject]@{ ok = $false; error = \"$($_.Exception.Message)\" }"
        "  | ConvertTo-Json -Compress"
        "}"
    )
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[sync] busy-guard: 任务状态查询失败 ({exc})", file=sys.stderr)
        return None
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        print(
            f"[sync] busy-guard: 任务状态查询无输出 (rc={proc.returncode})",
            file=sys.stderr,
        )
        return None
    try:
        payload = json.loads(out)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[sync] busy-guard: 任务状态解析失败 ({exc})", file=sys.stderr)
        return None
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    tasks = payload.get("tasks") or []
    if isinstance(tasks, dict):  # 单条时 ConvertTo-Json 会退化为对象
        tasks = [tasks]
    rows: list[tuple[str, str]] = []
    for item in tasks:
        if isinstance(item, dict):
            rows.append((str(item.get("name") or ""), str(item.get("cmd") or "")))
    return rows


def _run(args: list[str], *, check: bool = False) -> subprocess.CompletedProcess:
    """执行 git 命令（统一 UTF-8 解码；不抛异常，由调用方判 returncode）。

    两道防挂死（针对计划任务里"被时限杀掉"的历史故障 QuantNPC_Sync_0900
    LastRC=267014 = 0x41306）:
      * `GIT_TERMINAL_PROMPT=0` / `GCM_INTERACTIVE=never` —— 凭证缺失时**立即失败**
        而不是弹出交互提示把任务挂到时限；
      * `timeout=_GIT_TIMEOUT_SECONDS` —— 超时按失败返回，绝不让同步进程长期占着
        `.git` 锁（曾观察到 1800/2000 的时限被设成 PT72H，挂死可达 3 天）。
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=_REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=124,
            stdout="",
            stderr=f"git {' '.join(args)} 超时 (>{_GIT_TIMEOUT_SECONDS}s)",
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


def _union_checkboxes(path: str) -> int:
    """specs tasks.md 复选框并集：同一任务 ID 的 [ ] 与 [x] 并存时保留 [x]。

    marker-strip 之后，checkbox 冲突会留下同键的两行（一端勾了、一端没勾）。
    键 = checkbox 行描述的第一个空白分隔 token（任务 ID，如 T001）。
    保守规则：仅当同键 ≥2 行且勾选状态**不一致**时才合并（删未勾版本）；
    状态一致/键不重复的一律不动。返回被合并掉的未勾选行数。
    """
    abs_path = os.path.join(_REPO, path)
    with open(abs_path, encoding="utf-8", newline="") as fh:
        lines = fh.readlines()
    box_re = re.compile(r"^(\s*)- \[([ xX])\] (\S+)(.*)$")
    by_key: dict[str, list[tuple[int, re.Match[str]]]] = {}
    for i, line in enumerate(lines):
        m = box_re.match(line)
        if m:
            by_key.setdefault(m.group(3), []).append((i, m))
    drop: set[int] = set()
    for entries in by_key.values():
        if len(entries) < 2:
            continue
        states = {m.group(2).lower() for _, m in entries}
        if len(states) == 1:
            continue  # 勾选状态一致（纯重复）—— 不动，保持 marker-strip 原语义
        drop.update(i for i, m in entries if m.group(2) == " ")
    if not drop:
        return 0
    with open(abs_path, "w", encoding="utf-8", newline="") as fh:
        fh.writelines(line for i, line in enumerate(lines) if i not in drop)
    return len(drop)


def main(argv: list[str] | None = None) -> int:
    global _REPO

    parser = argparse.ArgumentParser(description="CNB → 本地 → GitHub 确定性同步")
    parser.add_argument("--repo", default=_PROJECT_ROOT, help="目标仓库路径 (默认本项目根)")
    parser.add_argument("--remote", default="cnb", help="上游 remote 名 (默认 cnb)")
    parser.add_argument("--branch", default="main", help="分支 (默认 main)")
    parser.add_argument("--push-remote", default="origin", help="回流 remote 名 (默认 origin)")
    parser.add_argument("--dry-run", action="store_true", help="只体检不改动")
    parser.add_argument("--no-push", action="store_true", help="只同步到本地, 不回流 GitHub")
    parser.add_argument(
        "--allow-busy",
        action="store_true",
        help="关闭 busy-guard（默认开启：有项目任务在跑时拒绝合并，避免改写其正在读的代码）",
    )
    args = parser.parse_args(argv)

    _REPO = os.path.abspath(args.repo)
    remote_ref = f"{args.remote}/{args.branch}"
    print(f"[sync] {args.remote}:{args.branch} -> 本地 -> {args.push_remote}")

    _run(["fetch", args.remote, args.branch], check=True)
    ahead, behind = _divergence(remote_ref)
    head = _run(["rev-parse", "--short", "HEAD"]).stdout.strip()
    print(f"[sync] HEAD={head} 领先={ahead} 落后={behind}")

    # ---- busy-guard: 别在项目任务运行中改写它们正在 import 的文件 ----
    if args.allow_busy:
        print("[sync] busy-guard: 已按 --allow-busy 关闭")
    else:
        ok, note = _busy_verdict(_query_running_tasks(), behind)
        print(f"[sync] {note}" if ok else f"[sync] 停止：{note}")
        if not ok:
            return 1

    if behind == 0:
        print("[sync] 上游无新提交（无需合并）；继续检查是否需要回流 GitHub")
    else:
        # ---- 预检：脏文件 ∩ 入站改动 ----
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
            if not conflicted or not all(_is_union_allowed(p) for p in conflicted):
                _run(["merge", "--abort"])
                print(f"[sync] 停止：冲突超出白名单 {sorted(conflicted)}，已 abort（未留冲突现场）")
                return 1
            for path in sorted(conflicted):
                removed = _strip_conflict_markers(path)
                merged_boxes = _union_checkboxes(path) if _SPECS_TASKS_RE.match(path) else 0
                _run(["add", path])
                if merged_boxes:
                    print(
                        f"[sync] 冲突取并集：{path} 删除 {removed} 个标记行，"
                        f"复选框合并 {merged_boxes} 行（勾选 OR，保留已勾版本）"
                    )
                else:
                    print(f"[sync] 冲突取并集：{path} 删除 {removed} 个标记行，两侧条目全留")
            proc = _run(["commit", "--no-edit"])
            if proc.returncode != 0:
                _run(["merge", "--abort"])
                print("[sync] 停止：合并提交被 pre-commit 门禁拦下，已 abort —— 需人工处理")
                return 1

        new_head = _run(["rev-parse", "--short", "HEAD"]).stdout.strip()
        print(f"[sync] 合并完成 HEAD={new_head}")

    if args.dry_run:
        print("[sync] DRY-RUN：跳过回流 GitHub")
        return 0

    if args.no_push:
        print("[sync] 按参数跳过回流 GitHub")
        return 0

    # ---- 回流 GitHub（本地领先于远端即推，不限于"刚合并过"）----
    _run(["fetch", args.push_remote, args.branch])
    push_ref = f"{args.push_remote}/{args.branch}"
    ahead_push, _behind_push = _divergence(push_ref)
    if ahead_push == 0:
        print(f"[sync] {push_ref} 已是最新（无需推送）")
        return 0

    push = _run(["-c", "http.version=HTTP/1.1", "push", args.push_remote, args.branch])
    if push.returncode != 0:
        print(f"[sync] push 失败（未强推）：{_lines(push.stderr)[-1] if _lines(push.stderr) else ''}")
        return 2
    final = _run(["rev-parse", "--short", push_ref]).stdout.strip()
    print(f"[sync] push 完成 {push_ref}={final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
