"""全量测试运行器 - 一次性跑所有单元测试和端到端测试"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).parent
TESTS = [
    "test_shadow_account.py",
    "test_factor_committee.py",
    "test_capacity_analyzer.py",
    "test_factor_kill_switch.py",
    "test_pipeline_orchestrator.py",
    "_e2e_test.py",
]


def main():
    print("=" * 70)
    print("全量测试运行器 - Vibe-Trading 因子分析项目 (CIO v1.0)")
    print("=" * 70)
    total_pass = total_fail = 0
    results = []
    for t in TESTS:
        test_path = TESTS_DIR / t
        if not test_path.exists():
            print(f"[SKIP] {t} 文件不存在")
            continue
        print(f"\n>>> 运行 {t}")
        r = subprocess.run(
            [sys.executable, str(test_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        # 找统计行
        output = r.stdout + r.stderr
        last_lines = output.strip().split("\n")[-6:]
        for line in last_lines:
            print(f"  {line}")
        # 解析 "总计: X 通过, Y 失败"
        import re
        m = re.search(r"总计:\s*(\d+)\s*通过,\s*(\d+)\s*失败", output)
        if m:
            p, f = int(m.group(1)), int(m.group(2))
            total_pass += p
            total_fail += f
            results.append((t, p, f))
        else:
            print(f"  ⚠ 无法解析统计，退出码={r.returncode}")
            total_fail += 1
            results.append((t, 0, 1))

    print("\n" + "=" * 70)
    print("测试汇总")
    print("=" * 70)
    print(f"{'测试文件':<40} {'通过':<8} {'失败':<8}")
    print("-" * 56)
    for t, p, f in results:
        status = "✓" if f == 0 else "✗"
        print(f"{t:<40} {p:<8} {f:<8} {status}")
    print("-" * 56)
    print(f"{'总计':<40} {total_pass:<8} {total_fail:<8}")
    print("=" * 70)
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
