# -*- coding: utf-8 -*-
"""T02: 测量 pylint broad-except 基线"""
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run_pylint(targets):
    """运行 pylint, 返回 (exit_code, output)"""
    cmd = [
        sys.executable, "-m", "pylint",
        "--rcfile=.pylintrc",
        "--disable=all",
        "--enable=broad-except,W0703",  # broad-except (old W0703)
        "--output-format=text",
    ] + targets
    r = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=str(ROOT),
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    print("=" * 60)
    print("T02 pylint broad-except 基线测量")
    print("=" * 60)

    # CI 配置中检查的目录
    targets = [
        "utils/infra/",
        "utils/risk/",
        "utils/execution/",
        "utils/data/",
    ]

    # 先确认目录存在
    for t in targets:
        if not (ROOT / t).exists():
            print(f"  ⚠ 目录不存在: {t}")

    print(f"运行 pylint {targets} ...")
    rc, output = run_pylint(targets)
    print(f"pylint 退出码: {rc}")
    print()

    # 解析 broad-except 错误
    by_file = defaultdict(list)
    total = 0
    for line in output.splitlines():
        # 格式: utils/xxx.py:LINE: [W0703(broad-except), ...] message
        # 或:   utils/xxx.py:LINE:COL: W0703: message
        if "broad-except" not in line and "W0703" not in line:
            continue
        if ".py:" not in line:
            continue
        try:
            file_part, rest = line.split(".py:", 1)
            file_path = file_part + ".py"
            line_no = int(rest.split(":")[0])
            by_file[file_path].append({
                "line": line_no,
                "raw": line,
            })
            total += 1
        except (ValueError, IndexError):
            continue

    print(f"发现 {total} 处 broad-except, 分布在 {len(by_file)} 个文件")
    print()
    print("按文件统计:")
    for fp, errs in sorted(by_file.items(), key=lambda x: -len(x[1])):
        print(f"  {fp}: {len(errs)} 处")

    # 保存完整输出
    raw_path = ROOT / "pylint_broad_except_baseline.txt"
    raw_path.write_text(output, encoding="utf-8")
    print(f"\n原始输出已保存: {raw_path}")

    # 保存按文件分组结果
    report_path = ROOT / "docs" / "工程化达标_8.4" / "T02_PYLINT_BASELINE.md"
    lines = [
        "# T02 Pylint broad-except 基线报告",
        "",
        "- 生成时间: 2026-07-27",
        f"- broad-except 总数: {total}",
        f"- 受影响文件数: {len(by_file)}",
        "",
        "## 按文件统计",
        "",
        "| 文件 | broad-except 数 |",
        "|------|----------------|",
    ]
    for fp, errs in sorted(by_file.items(), key=lambda x: -len(x[1])):
        lines.append(f"| {fp} | {len(errs)} |")
    lines.append("")
    lines.append("## 修复策略")
    lines.append("")
    lines.append("### P0 风控路径 (硬约束: 禁止 broad exception)")
    lines.append("- utils/risk/* (风控核心)")
    lines.append("- utils/execution/* (执行核心)")
    lines.append("- utils/kill_switch.py (核心风控)")
    lines.append("")
    lines.append("### P1 重要模块 (优先修复)")
    lines.append("- utils/infra/* (基础设施)")
    lines.append("- utils/data/* (数据层)")
    lines.append("")
    lines.append("### 修复方式")
    lines.append("- 改为具体异常类型 (KeyError/ValueError/ConnectionError 等)")
    lines.append("- 风控路径: 必须明确异常类型 + 日志记录 + fail-closed")
    lines.append("")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已生成: {report_path}")


if __name__ == "__main__":
    main()
