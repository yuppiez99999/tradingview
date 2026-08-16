"""第 10 批覆盖率冲刺候选模块分析."""
import xml.etree.ElementTree as ET
from pathlib import Path

tree = ET.parse(Path("reports/coverage.xml"))
root = tree.getroot()
total_rate = root.get("line-rate")
print(f"总体 line-rate: {total_rate}")

files = []
for p in root.findall(".//package"):
    for c in p.findall("classes/class"):
        name = c.get("filename", "?")
        rate = float(c.get("line-rate", "0"))
        lines = int(c.get("lines", "0"))
        files.append((name, rate, lines))

files.sort(key=lambda x: x[1])

print("\n=== 0% 覆盖文件 (优先攻) ===")
zero = [(n, r, l) for n, r, l in files if r == 0.0]
for n, r, l in zero:
    print(f"  {n}: {r:.1%} ({l} lines)")
print(f"  小计: {len(zero)} 文件")

print("\n=== 0-30% 覆盖文件 ===")
low = [(n, r, l) for n, r, l in files if 0.0 < r < 0.3]
for n, r, l in low:
    print(f"  {n}: {r:.1%} ({l} lines)")
print(f"  小计: {len(low)} 文件")

print("\n=== 30-60% 覆盖文件 ===")
mid = [(n, r, l) for n, r, l in files if 0.3 <= r < 0.6]
for n, r, l in mid:
    print(f"  {n}: {r:.1%} ({l} lines)")
print(f"  小计: {len(mid)} 文件")

print("\n=== 60-80% 覆盖文件 (第10批候选) ===")
high = [(n, r, l) for n, r, l in files if 0.6 <= r < 0.8]
for n, r, l in high:
    print(f"  {n}: {r:.1%} ({l} lines)")
print(f"  小计: {len(high)} 文件")