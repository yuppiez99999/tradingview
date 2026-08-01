"""检查关键月份缓存文件的修改时间"""
import datetime
from pathlib import Path

dates = ['2024-08-01', '2024-09-02', '2024-10-01', '2024-11-01',
         '2025-01-01', '2025-09-01', '2025-10-01']
print("日期         缓存文件修改时间            大小(KB)")
print("-" * 55)
for d in dates:
    p = Path(f"output/institutional_pipeline/{d}/pipeline_backtest.json")
    if p.exists():
        mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime)
        size_kb = p.stat().st_size / 1024
        print(f"{d:<12} {mtime.strftime('%Y-%m-%d %H:%M:%S'):<25} {size_kb:.1f}")
    else:
        print(f"{d:<12} NOT FOUND")

# 检查 V6/V6.1/V6.2 回测结果文件的生成时间
print()
print("=== 回测结果文件生成时间 ===")
patterns = [
    ("V6", "lgb_backtest_v6_alpha_quality*.json"),
    ("V6.1", "lgb_backtest_v6_1*.json"),
    ("V6.2", "lgb_backtest_v6_2*.json"),
]
for name, pat in patterns:
    files = sorted(Path("output/validation_reports").glob(pat), key=lambda p: p.stat().st_mtime)
    for f in files:
        mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime)
        print(f"{name}: {f.name}  生成于 {mtime.strftime('%Y-%m-%d %H:%M:%S')}")

# 检查 lgb_enhanced_trainer.py 修改时间
print()
print("=== 关键源文件修改时间 ===")
src_files = ["lgb_enhanced_trainer.py", "institutional_pipeline_runner.py", "research/backtest_runner.py"]
for f in src_files:
    p = Path(f)
    if p.exists():
        mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime)
        print(f"{f}: {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
