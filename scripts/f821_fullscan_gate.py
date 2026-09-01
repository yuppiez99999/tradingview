"""全量 F821 未定义名门禁 (baseline 覆盖, 2026-08-29)

对仓库全量 .py 文件执行 ruff F821 检查, 与冻结基线 reports/f821_baseline.json 对比:
- 存量违规 (在基线内) 放行, 仅提示
- 新增违规 (不在基线内) 阻断, 打印明细

解决 "增量门禁只扫 PR 改动文件, 存量文件未被近期修改则 F821 漏网" 的问题。
挂在 nightly-regression job; 也可 workflow_dispatch 手动跑。

用法:
    python scripts/f821_fullscan_gate.py            # gate 模式 (CI 用, 新增即阻断)
    python scripts/f821_fullscan_gate.py --update   # 用当前违规集合刷新基线 (本地清零后用)
    python scripts/f821_fullscan_gate.py --path ms_strategy  # 仅扫描指定目录
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = ROOT / "reports" / "f821_baseline.json"

# 扫描范围: 排除归档/虚拟环境/缓存/第三方
_EXCLUDES = (
    "_archive", "__pycache__", "node_modules", ".venv", "venv",
    "qlib_env", ".git", "build", "dist",
)


def _norm(p: str) -> str:
    return p.replace("\\", "/")


def _collect_py_files(target: str | None) -> list[str]:
    """仅用于计数/日志; 实际扫描交给 ruff 自身遍历 (避免 Windows 命令行超长)."""
    if target:
        tp = Path(target)
        if not tp.is_absolute():
            tp = ROOT / target
        if tp.is_file():
            return [str(tp)]
        root = tp
    else:
        root = ROOT
    files = []
    for p in root.rglob("*.py"):
        sp = str(p)
        if any(seg in sp for seg in _EXCLUDES):
            continue
        files.append(str(p))
    return sorted(files)


def _run_ruff_f821(target: str | None) -> list[dict]:
    """跑 ruff F821, 返回违规列表 [{file, line, col, code, message}].

    直接让 ruff 扫描目录 (尊重 pyproject.toml exclude + 显式 --exclude),
    不传文件列表, 规避 Windows 命令行长度上限 (WinError 206).
    """
    scan_target = target if target else str(ROOT)
    cmd = [
        sys.executable, "-m", "ruff", "check",
        "--select", "F821",
        "--output-format", "json",
        "--no-cache",
        "--exclude", "_archive",
        "--exclude", "__pycache__",
        "--exclude", "node_modules",
        "--exclude", ".venv",
        "--exclude", "venv",
        "--exclude", "qlib_env",
        "--exclude", ".git",
        "--exclude", "build",
        "--exclude", "dist",
        scan_target,
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode not in (0, 1):
        print(f"[F821-fullscan] ruff 异常 exit={proc.returncode}", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        return []
    try:
        data = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        print(f"[F821-fullscan] ruff JSON 解析失败: {proc.stdout[:200]}", file=sys.stderr)
        return []
    violations = []
    for item in data:
        for diag in item.get("messages", []):
            if diag.get("code") == "F821":
                violations.append({
                    "file": _norm(item["filename"]),
                    "line": int(item["location"]["row"]),
                    "col": int(item["location"]["column"]),
                    "code": "F821",
                    "message": diag.get("message", ""),
                })
    return violations


def _violation_key(v: dict) -> str:
    return f"{v['file']}:{v['line']}:{v['message']}"


def _load_baseline() -> set[str]:
    if not BASELINE_PATH.exists():
        return set()
    try:
        data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        return set(data.get("violations", []))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_baseline(keys: set[str]) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "description": "全量 F821 未定义名基线 — 存量违规冻结, 新增即阻断 CI",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(keys),
        "violations": sorted(keys),
    }
    BASELINE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="全量 F821 baseline 门禁")
    parser.add_argument("--update", action="store_true", help="用当前违规集合刷新基线")
    parser.add_argument("--path", type=str, default=None, help="仅扫描指定文件/目录")
    args = parser.parse_args(argv)

    files = _collect_py_files(args.path)
    if not files:
        print("[F821-fullscan] 无待扫描 .py 文件, 跳过")
        return 0

    violations = _run_ruff_f821(args.path)
    cur_keys = {_violation_key(v) for v in violations}

    if args.update:
        _save_baseline(cur_keys)
        print(f"[F821-fullscan] 基线已刷新: {BASELINE_PATH} ({len(cur_keys)} 条)")
        return 0

    baseline = _load_baseline()
    new_keys = cur_keys - baseline
    gone_keys = baseline - cur_keys

    print(f"[F821-fullscan] 扫描 {len(files)} 文件, 当前 {len(cur_keys)} 条, 基线 {len(baseline)} 条")

    if gone_keys:
        print(f"[F821-fullscan] 基线中有 {len(gone_keys)} 条已修复 (可运行 --update 收紧基线):")
        for k in sorted(gone_keys)[:10]:
            print(f"    - {k}")

    if new_keys:
        print(f"[F821-fullscan] FAILED — 新增 {len(new_keys)} 条 F821 未定义名 (不在基线内):")
        for k in sorted(new_keys):
            print(f"    + {k}")
        print(
            "[F821-fullscan] 修复: 补 import 或加 `# noqa: F821` 豁免; "
            "若为存量豁免, 运行 `python scripts/f821_fullscan_gate.py --update` 更新基线后提交。",
            file=sys.stderr,
        )
        return 1

    print("[F821-fullscan] PASSED — 无新增 F821 违规")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
