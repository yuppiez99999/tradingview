# -*- coding: utf-8 -*-
"""验证 QLib 数据完整性 — 检查命名格式和标的覆盖"""
import os

d = r"E:\各种PY程序\28-终极量化交易系统7.1\qlib_data\cn_data"

# 查看日历范围
with open(os.path.join(d, "calendars", "day.txt")) as f:
    lines = f.read().strip().split("\n")
print(f"日历: {lines[0]} ~ {lines[-1]} ({len(lines)} 交易日)")

# 查看 features 目录命名格式
feat_dir = os.path.join(d, "features")
all_stocks = sorted(os.listdir(feat_dir))
print(f"\n总标的数: {len(all_stocks)}")
print(f"前10个标的: {all_stocks[:10]}")
print(f"后10个标的: {all_stocks[-10:]}")

# 用户持仓标的（QLib 格式: sh/sz 前缀）
user_stocks_qlib = [
    ("sh510300", "沪深300ETF"), ("sh510500", "中证500ETF"),
    ("sh512100", "中证1000ETF"), ("sh588000", "科创50ETF"),
    ("sh688041", "海光信息"), ("sz300308", "中际旭创"),
    ("sz002371", "北方华创"), ("sh688981", "中芯国际"),
    ("sh601088", "中国神华"), ("sh600276", "恒瑞医药"),
    ("sh600900", "长江电力"), ("sz000425", "徐工机械"),
    ("sh603019", "中科曙光"), ("sh600089", "特变电工"),
    ("sz300274", "阳光电源"), ("sh600019", "宝钢股份"),
    ("sh600219", "南山铝业"), ("sh518880", "黄金ETF"),
    ("sz159915", "创业板ETF"), ("sh515180", "红利ETF"),
    ("sh600036", "招商银行"),
]

print(f"\n用户持仓标的检查 (QLib sh/sz 格式):")
found = 0
for code, name in user_stocks_qlib:
    stock_dir = os.path.join(feat_dir, code)
    if os.path.isdir(stock_dir):
        files = os.listdir(stock_dir)
        print(f"  {code} {name}: ✅ 存在 ({len(files)} 个文件)")
        found += 1
    else:
        print(f"  {code} {name}: ❌ 不存在")
print(f"\n覆盖率: {found}/{len(user_stocks_qlib)}")

# 查看 instruments 文件内容
print("\n--- instruments/all.txt 前5行 ---")
with open(os.path.join(d, "instruments", "all.txt")) as f:
    for i, line in enumerate(f):
        if i >= 5:
            break
        print(f"  {line.strip()}")
