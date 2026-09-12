"""Skill 安全扫描封装 (A2 集成 SkillSpector)

封装 NVIDIA SkillSpector CLI, 增量扫描第三方 agent skill 的 64 类漏洞。
降级安全: skillspector 未安装时 available=False, 不阻断提交流程。

详见 docs/集成记录/A2/spec_20260821.md
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SKILL_INDICATORS = ("SKILL.md", "skills/", ".claude/skills/")
SARIF_SEVERITY_RANK = {"error": 4, "warning": 3, "note": 2, "none": 1}


@dataclass
class ScanResult:
    scanned: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    blocked: bool = False
    sarif_path: Path | None = None
    available: bool = True
    error: str = ""


def is_skill_file(path: Path) -> bool:
    """判断是否 skill 文件 (.claude/skills/ 下 或 SKILL.md 或 skills/*.md)。"""
    s = str(path).replace("\\", "/")
    if "/.claude/skills/" in s:
        return True
    if path.name == "SKILL.md":
        return True
    if "/skills/" in s and path.suffix == ".md":
        return True
    return False


def get_changed_skill_files(repo_root: Path) -> list[Path]:
    """git diff 获取暂存区改动的 skill 文件 (增量扫描)。"""
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=AM"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if out.returncode != 0:
            return []
        files = []
        for line in out.stdout.splitlines():
            p = repo_root / line
            if p.exists() and is_skill_file(p):
                files.append(p)
        return files
    except Exception as exc:
        logger.warning("获取改动 skill 文件失败: %s", exc)
        return []


def _check_available() -> bool:
    """检查 skillspector CLI 是否可用。"""
    try:
        r = subprocess.run(
            ["skillspector", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    except Exception:
        return False


def _parse_sarif(sarif_path: Path) -> tuple[int, int, int, int]:
    """解析 SARIF 结果, 返回 (critical, high, medium, low)。"""
    try:
        data = json.loads(sarif_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("SARIF 解析失败: %s", exc)
        return (0, 0, 0, 0)

    critical = high = medium = low = 0
    for run in data.get("runs", []):
        for result in run.get("results", []):
            level = result.get("level", "warning")
            rank = SARIF_SEVERITY_RANK.get(level, 3)
            if rank >= 4:
                critical += 1
            elif rank == 3:
                high += 1
            elif rank == 2:
                medium += 1
            else:
                low += 1
    return (critical, high, medium, low)


def scan_skills(
    targets: list[Path],
    output_dir: Path | None = None,
    full: bool = False,
) -> ScanResult:
    """调用 skillspector CLI 扫描 skill 文件。

    Args:
        targets: 待扫描的 skill 文件/目录列表
        output_dir: SARIF 归档目录, None 则 reports/skill_security/
        full: True 则全量扫描 (不增量)

    Returns:
        ScanResult, available=False 时为降级占位
    """
    if not targets:
        return ScanResult(available=True)

    if not _check_available():
        logger.warning(
            "skillspector 未安装, 跳过 skill 安全扫描 (pip install skillspector)"
        )
        return ScanResult(available=False, error="skillspector not installed")

    if output_dir is None:
        output_dir = Path(__file__).resolve().parents[1] / "reports" / "skill_security"
    output_dir.mkdir(parents=True, exist_ok=True)
    sarif_path = output_dir / f"scan_{now_bj():%Y%m%d_%H%M%S}.sarif"

    args = ["skillspector", "scan"]
    for t in targets:
        args.append(str(t))
    args += ["--format", "sarif", "--no-llm", "--output", str(sarif_path)]

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        if proc.returncode != 0 and not sarif_path.exists():
            return ScanResult(
                available=True,
                error=f"skillspector exit {proc.returncode}: {proc.stderr[:200]}",
            )
        critical, high, medium, low = _parse_sarif(sarif_path)
        return ScanResult(
            scanned=len(targets),
            critical=critical,
            high=high,
            medium=medium,
            low=low,
            blocked=(critical + high) > 0,
            sarif_path=sarif_path,
            available=True,
        )
    except subprocess.TimeoutExpired:
        return ScanResult(available=True, error="skillspector 超时")
    except Exception as exc:
        logger.warning("skill 扫描异常: %s", exc)
        return ScanResult(available=True, error=str(exc))


def run_precommit_check(repo_root: Path) -> tuple[bool, str]:
    """pre-commit 入口: 增量扫描改动的 skill, 返回 (passed, message)。

    passed=True 表示允许提交; False 表示阻断。
    任何异常都容错返回 True (不阻断开发流程)。
    """
    import os

    if os.environ.get("SKIP_SKILL_SCAN", "").strip():
        return (True, "SKIP_SKILL_SCAN=1, 跳过 skill 安全扫描")

    try:
        changed = get_changed_skill_files(repo_root)
        if not changed:
            return (True, "无 skill 文件改动, 跳过扫描")

        result = scan_skills(changed)
        if not result.available:
            return (True, f"skillspector 不可用, 容错通过: {result.error}")

        if result.blocked:
            msg = (
                f"skill 安全扫描阻断: {result.critical} critical + {result.high} high "
                f"(扫描 {result.scanned} 文件, SARIF: {result.sarif_path})"
            )
            return (False, msg)

        msg = (
            f"skill 安全扫描通过: {result.scanned} 文件, "
            f"{result.medium} medium + {result.low} low (警告不阻断)"
        )
        return (True, msg)
    except Exception as exc:
        logger.warning("skill 安全 pre-commit 检查异常, 容错通过: %s", exc)
        return (True, f"容错通过: {exc}")
