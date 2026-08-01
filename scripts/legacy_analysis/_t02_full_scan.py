"""T02: 完整扫描 utils/ 全目录的 broad-except"""
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run_pylint(target):
    cmd = [
        sys.executable, "-m", "pylint",
        "--rcfile=.pylintrc",
        "--disable=all",
        "--enable=broad-except",
        "--output-format=text",
        target,
    ]
    r = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=str(ROOT),
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    print("=" * 60)
    print("T02 完整扫描 utils/ broad-except")
    print("=" * 60)

    print("运行 pylint utils/ ...")
    rc, output = run_pylint("utils/")
    print(f"pylint 退出码: {rc}")

    # 解析 broad-except
    by_file = defaultdict(list)
    total = 0
    for line in output.splitlines():
        if "broad-except" not in line:
            continue
        if ".py:" not in line:
            continue
        try:
            file_part, rest = line.split(".py:", 1)
            file_path = file_part + ".py"
            line_no = int(rest.split(":")[0])
            by_file[file_path].append({"line": line_no, "raw": line})
            total += 1
        except (ValueError, IndexError):
            continue

    print(f"发现 {total} 处 broad-except, 分布在 {len(by_file)} 个文件")
    print()
    if by_file:
        print("按文件统计:")
        for fp, errs in sorted(by_file.items(), key=lambda x: -len(x[1])):
            print(f"  {fp}: {len(errs)} 处")
            for e in errs[:3]:
                print(f"    line {e['line']}")
    else:
        print("✅ 无 broad-except 警告")

    # 保存完整输出
    raw_path = ROOT / "pylint_broad_except_full.txt"
    raw_path.write_text(output, encoding="utf-8")
    print(f"\n原始输出: {raw_path}")

    # 也检查所有 broad-except 模式 (try/except Exception)
    print()
    print("=" * 60)
    print("补充扫描: try/except Exception 模式 (grep)")
    print("=" * 60)


if __name__ == "__main__":
    main()
