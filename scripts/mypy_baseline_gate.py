"""G6 MyPy 基线门禁 (fail-if-increased 模式).

工业级达标计划 G6: 不要求一次性清零 771 个存量类型 error (跨 134 文件,
绝大多数为 Any/None/类型注解缺失, 非逻辑错误), 而是:

1. 冻结基线 error 数到 docs/mypy_baseline_v9.2.txt (纯 error 行).
2. CI/本地门禁只检查 "当前 error 数 > 基线" -> FAIL (阻断新增类型退化).
   error 数 <= 基线 -> PASS (允许逐步消减存量).

这样既不阻塞正常开发 (存量 error 不报错), 又防止 PR 引入新的类型错误.

用法:
    python scripts/mypy_baseline_gate.py            # 比对模式, 超限则 exit 1
    python scripts/mypy_baseline_gate.py --update   # 重新冻结当前 error 数为基线
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILE = PROJECT_ROOT / "docs" / "mypy_baseline_v9.2.txt"
MYPY_INI = PROJECT_ROOT / "mypy.ini"


def _count_current_errors() -> tuple[int, str]:
    """运行 mypy (utils/), 返回 (error 数, 原始输出)."""
    cmd = [
        sys.executable, "-m", "mypy",
        "--config-file", str(MYPY_INI),
        "utils",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = proc.stdout + proc.stderr
    errors = [ln for ln in out.splitlines() if "error:" in ln]
    return len(errors), out


def _read_baseline() -> int:
    if not BASELINE_FILE.exists():
        return 0
    lines = [ln for ln in BASELINE_FILE.read_text(encoding="utf-8").splitlines()
             if "error:" in ln]
    return len(lines)


def _update_baseline() -> int:
    count, out = _count_current_errors()
    # 仅写入纯 error 行 (不含 note/统计行), 保持基线文件可比对
    err_lines = [ln for ln in out.splitlines() if "error:" in ln]
    BASELINE_FILE.write_text("\n".join(err_lines) + "\n", encoding="utf-8")
    print(f"[mypy-baseline] 基线已重新冻结: {count} errors -> {BASELINE_FILE.name}")
    return count


def main() -> int:
    if "--update" in sys.argv:
        _update_baseline()
        return 0

    current, _ = _count_current_errors()
    baseline = _read_baseline()
    delta = current - baseline

    print(f"[mypy-baseline] 当前 error 数: {current} | 基线: {baseline} | 增量: {delta:+d}")

    if delta > 0:
        print(
            f"[mypy-baseline] FAIL: 类型 error 较基线新增 {delta} 个, "
            f"禁止引入新的类型退化. 修复后重跑, 或确认存量已清理后 "
            f"`python scripts/mypy_baseline_gate.py --update` 重新冻结基线."
        )
        return 1
    if delta < 0:
        print(f"[mypy-baseline] PASS: 类型 error 较基线减少 {-delta} 个 (存量收敛中).")
    else:
        print("[mypy-baseline] PASS: error 数与基线持平, 无新增退化.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
