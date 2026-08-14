"""覆盖率分析临时脚本 — 解析 coverage.xml 识别低覆盖模块."""
import xml.etree.ElementTree as ET
import sys
from pathlib import Path

tree = ET.parse(Path("reports/coverage.xml"))
root = tree.getroot()
print(f"总体 line-rate: {root.get('line-rate')}")
print(f"分支率: {root.get('branch-rate')}")
packages = root.findall(".//package")
print(f"包数: {len(packages)}")

# 收集所有包的覆盖率
pkg_rates = []
for p in packages:
    name = p.get("name", "?")
    lr = float(p.get("line-rate", "0"))
    # 统计类数量
    classes = p.findall("classes/class")
    pkg_rates.append((name, lr, len(classes)))

# 按覆盖率排序
pkg_rates.sort(key=lambda x: x[1])

# 打印 0% 覆盖的包
zero = [x for x in pkg_rates if x[1] == 0.0]
low = [x for x in pkg_rates if 0.0 < x[1] < 0.3]
mid = [x for x in pkg_rates if 0.3 <= x[1] < 0.6]
high = [x for x in pkg_rates if 0.6 <= x[1] < 0.8]
full = [x for x in pkg_rates if x[1] >= 0.8]

print(f"\n0% 覆盖: {len(zero)} 包")
for n, r, c in zero[:20]:
    print(f"  {n}: {r:.2%} ({c} classes)")

print(f"\n0-30% 覆盖: {len(low)} 包")
for n, r, c in low[:15]:
    print(f"  {n}: {r:.2%} ({c} classes)")

print(f"\n30-60% 覆盖: {len(mid)} 包")
for n, r, c in mid[:10]:
    print(f"  {n}: {r:.2%} ({c} classes)")

print(f"\n60-80% 覆盖: {len(high)} 包")
print(f"80%+ 覆盖: {len(full)} 包")

# 识别高价值低覆盖模块 (utils/ 下的大文件)
print("\n=== 高行数低覆盖文件 (Top 30) ===")
# 提取每个 class 的覆盖率 + 文件名 + 行数
file_rates = []
for cls in root.findall(".//class"):
    fname = cls.get("filename", "?")
    lr = float(cls.get("line-rate", "0"))
    # 统计行数
    lines = cls.findall("lines/line")
    total_lines = len(lines)
    if total_lines > 0:
        file_rates.append((fname, lr, total_lines))

# 按行数降序排序
file_rates.sort(key=lambda x: -x[2])

# 打印 Top 30 高行数文件的覆盖率
print(f"{'文件':<60} {'覆盖率':>8} {'行数':>6}")
for fname, lr, lines in file_rates[:30]:
    short = fname if len(fname) <= 60 else "..." + fname[-57:]
    print(f"  {short:<60} {lr:>7.2%} {lines:>6}")

# 统计 0% 覆盖的高行数文件
zero_high = [(f, l) for f, r, l in file_rates if r == 0.0 and l > 50]
print(f"\n=== 0% 覆盖且行数>50 的文件 ({len(zero_high)} 个) ===")
for fname, lines in zero_high[:20]:
    short = fname if len(fname) <= 60 else "..." + fname[-57:]
    print(f"  {short:<60} {lines:>6} 行")

# 计算要达到 80% 需要补多少行
total_lines = sum(l for _, _, l in file_rates)
covered_lines = sum(int(l * r) for _, r, l in file_rates)
target_lines = int(total_lines * 0.80)
need_lines = target_lines - covered_lines
print(f"\n=== 达标 80% 需求 ===")
print(f"总行数: {total_lines}")
print(f"已覆盖: {covered_lines} ({covered_lines/total_lines:.2%})")
print(f"目标80%: {target_lines}")
print(f"需补覆盖: {need_lines} 行")
