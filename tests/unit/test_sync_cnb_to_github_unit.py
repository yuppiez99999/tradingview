"""`scripts/sync_cnb_to_github.py` 端到端回归（临时仓库真跑 git）。

覆盖四种真实场景 —— 前三种都是"原 sync_npc.ps1（pull --ff-only）会静默失败"的现场:
    1. 分叉且冲突仅在 append-only 日志  -> 应"取并集"合并成功（exit 0）
    2. 未提交 WIP 与入站改动相交        -> 应放弃且不动工作区（exit 1）
    3. 冲突超出白名单（非日志文件）      -> 应 abort 且不留冲突现场（exit 1）
    4. 无分歧                            -> 幂等退出（exit 0）
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
    """无分歧 → 幂等退出，不产生任何提交。"""
    _upstream, local = repos
    head_before = _git(local, "rev-parse", "HEAD").stdout.strip()
    proc = _run_sync(local)
    assert proc.returncode == 0
    assert "无需同步" in proc.stdout
    assert _git(local, "rev-parse", "HEAD").stdout.strip() == head_before
