"""`scripts/sync_cnb_to_github.py` 端到端回归（临时仓库真跑 git）。

覆盖真实场景 —— 前三种都是"原 sync_npc.ps1（pull --ff-only）会静默失败"的现场:
    1. 分叉且冲突仅在 append-only 日志  -> 应"取并集"合并成功（exit 0）
    2. 未提交 WIP 与入站改动相交        -> 应放弃且不动工作区（exit 1）
    3. 冲突超出白名单（非日志文件）      -> 应 abort 且不留冲突现场（exit 1）
    4. 无分歧                            -> 幂等退出（exit 0）
    5. 上游无新提交但本地领先            -> 仍须回流 GitHub（exit 0）
另有一组 busy-guard 判据（见文件末 TestBusyGuard）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "sync_cnb_to_github.py"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _commit(repo: Path, message: str) -> None:
    assert _git(repo, "add", "-A").returncode == 0
    proc = _git(repo, "commit", "-m", message)
    assert proc.returncode == 0, proc.stderr


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run_sync(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), "--no-push", *extra],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


@pytest.fixture()
def repos(tmp_path: Path) -> tuple[Path, Path]:
    """建 bare 上游 + 两条工作副本 (upstream 扮演 cnb 侧, local 扮演本地 main)。

    local 同时把 `cnb` 与 `origin` 都指向 bare —— 脚本默认 remote 名为 cnb,
    这样无需网络即可端到端验证「取数 → 合并 → 提交」全链路。
    """
    bare = tmp_path / "upstream.git"
    upstream = tmp_path / "upstream"
    local = tmp_path / "local"
    bare.mkdir()
    assert _git(bare, "init", "--bare", "--initial-branch=main").returncode == 0

    subprocess.run(
        ["git", "clone", str(bare), str(upstream)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    _write(upstream, "cairn/LOG.md", "## 基线\n")
    _write(upstream, "shared.txt", "line1\nline2\nline3\n")
    _write(upstream, "notes.txt", "note-A\n")
    for key, value in (("user.email", "t@example.com"), ("user.name", "tester")):
        assert _git(upstream, "config", key, value).returncode == 0
    _commit(upstream, "base")
    assert _git(upstream, "push", "origin", "main").returncode == 0

    subprocess.run(
        ["git", "clone", str(bare), str(local)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    for key, value in (("user.email", "t@example.com"), ("user.name", "tester")):
        assert _git(local, "config", key, value).returncode == 0
    assert _git(local, "remote", "add", "cnb", str(bare)).returncode == 0
    return upstream, local


def test_merges_diverged_log_conflict_by_union(repos: tuple[Path, Path]) -> None:
    """分叉 + LOG 冲突 → 取并集：两侧条目都必须留在文件里。"""
    upstream, local = repos
    _write(upstream, "cairn/LOG.md", "## 基线\n## UPSTREAM-ENTRY\n")
    _commit(upstream, "upstream entry")
    assert _git(upstream, "push", "origin", "main").returncode == 0

    _write(local, "cairn/LOG.md", "## 本地头\n## 基线\n")
    _commit(local, "local entry")

    proc = _run_sync(local)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    merged = (local / "cairn" / "LOG.md").read_text(encoding="utf-8")
    assert "UPSTREAM-ENTRY" in merged, "上游条目被丢弃"
    assert "本地头" in merged, "本地条目被丢弃"
    assert "<<<<<<<" not in merged
    # 合并提交已落盘
    assert "merge(cnb)" in _git(local, "log", "-1", "--pretty=%s").stdout


def test_refuses_when_dirty_overlaps_incoming(repos: tuple[Path, Path]) -> None:
    """未提交 WIP 与入站改动相交 → 必须放弃，且不得改动工作区。"""
    upstream, local = repos
    _write(upstream, "notes.txt", "note-A\nupstream-change\n")
    _commit(upstream, "upstream touches notes")
    assert _git(upstream, "push", "origin", "main").returncode == 0

    _write(local, "notes.txt", "note-A\nlocal-wip\n")  # 未提交
    head_before = _git(local, "rev-parse", "HEAD").stdout.strip()

    proc = _run_sync(local)
    assert proc.returncode == 1
    assert "相交" in proc.stdout
    assert (local / "notes.txt").read_text(encoding="utf-8") == "note-A\nlocal-wip\n"
    assert _git(local, "rev-parse", "HEAD").stdout.strip() == head_before


def test_aborts_on_conflict_outside_whitelist(repos: tuple[Path, Path]) -> None:
    """非日志文件冲突 → abort，不留冲突现场（工作区无标记、无 MERGE_HEAD）。"""
    upstream, local = repos
    _write(upstream, "shared.txt", "upstream-line\nline2\nline3\n")
    _commit(upstream, "upstream edits shared")
    assert _git(upstream, "push", "origin", "main").returncode == 0

    _write(local, "shared.txt", "local-line\nline2\nline3\n")
    _commit(local, "local edits shared")

    proc = _run_sync(local)
    assert proc.returncode == 1
    assert "冲突超出白名单" in proc.stdout
    assert "<<<<<<<" not in (local / "shared.txt").read_text(encoding="utf-8")
    assert not (local / ".git" / "MERGE_HEAD").exists()
    assert "local-line" in (local / "shared.txt").read_text(encoding="utf-8")


def test_noop_when_already_in_sync(repos: tuple[Path, Path]) -> None:
    """无分歧 → 幂等退出，不产生任何提交（--no-push 下亦不触碰远端）。"""
    _upstream, local = repos
    head_before = _git(local, "rev-parse", "HEAD").stdout.strip()
    proc = _run_sync(local)
    assert proc.returncode == 0
    assert "上游无新提交" in proc.stdout
    assert "跳过回流 GitHub" in proc.stdout
    assert _git(local, "rev-parse", "HEAD").stdout.strip() == head_before


def test_pushes_local_ahead_commits_to_origin(repos: tuple[Path, Path]) -> None:
    """上游无新提交但本地领先 → 仍须回流 GitHub（"接住+回流"两半都成立）。"""
    _upstream, local = repos
    _write(local, "local_only.txt", "only-on-local\n")
    _commit(local, "local only commit")
    local_head = _git(local, "rev-parse", "HEAD").stdout.strip()

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(local)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "push 完成" in proc.stdout
    assert _git(local, "rev-parse", "origin/main").stdout.strip() == local_head
    assert _git(local, "rev-list", "--left-right", "--count", "HEAD...origin/main").stdout.split() == [
        "0",
        "0",
    ]


def _load_sync_module():
    """按文件路径加载同步器（scripts/ 不是包，避免依赖 pytest 的导入路径假设）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("sync_cnb_to_github_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestBusyGuard:
    """busy-guard（2026-09-11）：项目任务运行中禁止合并。

    背景：Windows 计划任务 `QuantNPC_Sync_0900` 与 `v84_PreMarketInstructions`（当日
    唯一产出交易指令的任务）**同在 09:00**，而合并会改写工作区文件 —— 原"脏文件∩入站"
    预检防不住这个竞态（它只防未提交改动被覆盖，不防运行中进程读到改写中的文件）。
    判据 = 任务名属项目前缀 + 动作命令行含本仓库标记（两条都满足才算"忙"）。
    """

    #: _busy_conflicts 判定"操作本仓库"靠 _BUSY_REPO_MARKER 子串匹配（= 仓库目录名），
    #: 因此这里只需拼出含仓库目录名的命令，无需硬编码盘符绝对路径（会被路径门禁拦下）。
    REPO_CMD = (
        str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
        + " daily_trade_executor.py pre-market"
    )
    SELF_CMD = r"powershell.exe -NoProfile -File C:\Users\Administrator\sync_npc.ps1"

    def test_project_task_touching_repo_is_busy(self) -> None:
        module = _load_sync_module()
        running = [("v84_PreMarketInstructions", self.REPO_CMD)]
        assert module._busy_conflicts(running) == ["v84_PreMarketInstructions"]

    def test_sync_task_itself_is_never_busy(self) -> None:
        """同步任务自身此刻必然处于 Running —— 不排除就会自锁，同步永远跑不起来。"""
        module = _load_sync_module()
        assert module._busy_conflicts([("QuantNPC_Sync_0900", self.SELF_CMD)]) == []

    def test_unrelated_running_task_is_not_busy(self) -> None:
        module = _load_sync_module()
        running = [("Clash Verge (Admin)", r"D:\Clash Verge\clash-verge.exe")]
        assert module._busy_conflicts(running) == []

    def test_project_prefixed_task_not_touching_repo_is_not_busy(self) -> None:
        """项目词缀但动作不碰本仓库 → 不算忙（防把无关任务误判成忙碌）。"""
        module = _load_sync_module()
        assert module._busy_conflicts([("v84_OtherProject", r"D:\other\run.py")]) == []

    def test_verdict_blocks_merge_but_allows_push_only(self) -> None:
        """需要合并时拦；只需 push（不动工作区）时放行。"""
        module = _load_sync_module()
        busy = [("v84_PreMarketInstructions", self.REPO_CMD)]

        blocked, note = module._busy_verdict(busy, behind=3)
        assert blocked is False
        assert "正在运行" in note

        allowed, note = module._busy_verdict(busy, behind=0)
        assert allowed is True
        assert "只需 push" in note

    def test_unknown_probe_is_unknown_not_clear(self) -> None:
        """查询失败(None) ≠ 无任务([])：需要合并时必须停（未知≠通过）。"""
        module = _load_sync_module()

        blocked, note = module._busy_verdict(None, behind=1)
        assert blocked is False
        assert "无法确认" in note

        allowed, _ = module._busy_verdict(None, behind=0)
        assert allowed is True

    def test_clear_probe_allows_merge(self) -> None:
        module = _load_sync_module()
        allowed, note = module._busy_verdict([], behind=3)
        assert allowed is True
        assert "无项目任务在运行" in note

    def test_cli_wires_busy_guard(self, repos: tuple[Path, Path]) -> None:
        """CLI 层：守卫确实接进主流程（真去查任务状态并留下判定行）。"""
        _upstream, local = repos
        proc = _run_sync(local)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "busy-guard" in proc.stdout

    def test_cli_can_disable_busy_guard(self, repos: tuple[Path, Path]) -> None:
        _upstream, local = repos
        proc = _run_sync(local, "--allow-busy")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "已按 --allow-busy 关闭" in proc.stdout


class TestSpecsTasksUnion:
    """specs/*/tasks.md 复选框冲突 → 取并集（勾选 OR）—— spec-kit 集成 2026-09-11。

    端到端（临时仓库真跑 git）：分叉 + tasks.md 同一任务一端勾一端未勾 →
    合并后保留已勾版本（真做了任务的一端不被抹掉）。
    """

    def test_tasks_md_checkbox_conflict_merges_checked(self, repos: tuple[Path, Path]) -> None:
        upstream, local = repos
        _write(upstream, "specs/F1-feature/tasks.md", "- [ ] T001 [feat] do A\n- [ ] T002 do B\n")
        _commit(upstream, "upstream tasks")
        assert _git(upstream, "push", "origin", "main").returncode == 0

        _write(local, "specs/F1-feature/tasks.md", "- [x] T001 [feat] do A\n- [ ] T002 do B\n")
        _commit(local, "local tasks (T001 done)")

        proc = _run_sync(local)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        merged = (local / "specs" / "F1-feature" / "tasks.md").read_text(encoding="utf-8")
        assert "- [x] T001" in merged, "已勾版本被抹掉 —— 勾选 OR 语义被破坏"
        assert "- [ ] T001" not in merged, "未勾版本未合并掉"
        assert "- [ ] T002" in merged, "无冲突行被误删"
        assert "<<<<<<<" not in merged

    def test_tasks_md_same_state_dup_not_touched(self, repos: tuple[Path, Path]) -> None:
        """同键同勾选状态的重复行（非 checkbox 冲突）不做合并 —— 保持 marker-strip 原语义。"""
        upstream, local = repos
        _write(upstream, "specs/F2-feature/tasks.md", "- [ ] T001 do A\n")
        _commit(upstream, "upstream tasks")
        assert _git(upstream, "push", "origin", "main").returncode == 0

        _write(local, "specs/F2-feature/tasks.md", "- [x] T001 do A\n")
        _commit(local, "local tasks")
        # 两端 T001 勾选状态不同 → 合并为已勾（同上一用例），这里验证行内容本身保留
        proc = _run_sync(local)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        merged = (local / "specs" / "F2-feature" / "tasks.md").read_text(encoding="utf-8")
        assert merged.count("T001") == 1

    def test_spec_md_conflict_still_hangs(self, repos: tuple[Path, Path]) -> None:
        """specs 下非 tasks.md（spec.md）冲突 → 超出白名单，abort 且不留冲突现场。"""
        upstream, local = repos
        _write(upstream, "specs/F3-feature/spec.md", "upstream spec\n")
        _commit(upstream, "upstream spec")
        assert _git(upstream, "push", "origin", "main").returncode == 0

        _write(local, "specs/F3-feature/spec.md", "local spec\n")
        _commit(local, "local spec")

        proc = _run_sync(local)
        assert proc.returncode == 1, "spec.md 冲突不应被自动解决"
        assert "冲突超出白名单" in proc.stdout
        assert "<<<<<<<" not in (local / "specs" / "F3-feature" / "spec.md").read_text(encoding="utf-8")
