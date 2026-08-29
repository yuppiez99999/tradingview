"""
把 Wind 获取的沪深300指数数据写入 qlib 二进制格式
补齐 SH000300 在 2020-09-25 ~ 2026-07-08 期间的数据
"""
import csv
import struct
from pathlib import Path

DATA_DIR = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\qlib_data\cn_data")
WIND_CSV = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\reports\wind_csi300_index.csv")
STOCK_DIR = DATA_DIR / "features" / "SH000300"

# 读取日历
cal_path = DATA_DIR / "calendars" / "day.txt"
with open(cal_path, encoding="utf-8") as f:
    calendar = f.read().strip().split("\n")
print(f"日历: {len(calendar)} 天, {calendar[0]} ~ {calendar[-1]}")

# 读取 Wind 数据
wind_data = {}
with open(WIND_CSV, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        # 时间格式: 2020-09-25T00:00:00.000+02:00 -> 取日期部分
        date_str = row["date"][:10]
        wind_data[date_str] = {
            "open": float(row["OPEN"]),
            "close": float(row["MATCH"]),
            "high": float(row["HIGH"]),
            "low": float(row["LOW"]),
            "volume": float(row["VOLUME"]),
        }
print(f"Wind 数据: {len(wind_data)} 天")

# 读取现有 close.day.bin 看当前数据范围
close_bin = STOCK_DIR / "close.day.bin"
existing_data = []
if close_bin.exists():
    with open(close_bin, "rb") as f:
        existing_data = list(struct.unpack(f"{len(f.read())//4}f", open(close_bin, "rb").read()))
print(f"现有 close 数据: {len(existing_data)} 天")

# 构建完整的数据数组（按日历对齐）
fields = {
    "open": [],
    "close": [],
    "high": [],
    "low": [],
    "volume": [],
}
for date in calendar:
    if date in wind_data:
        fields["open"].append(wind_data[date]["open"])
        fields["close"].append(wind_data[date]["close"])
        fields["high"].append(wind_data[date]["high"])
        fields["low"].append(wind_data[date]["low"])
        fields["volume"].append(wind_data[date]["volume"])
    else:
        for k in fields:
            fields[k].append(float("nan"))

# 写入二进制文件
for field_name, values in fields.items():
    bin_path = STOCK_DIR / f"{field_name}.day.bin"
    with open(bin_path, "wb") as f:
        f.write(struct.pack(f"{len(values)}f", *values))
    valid_count = sum(1 for v in values if v == v)  # NaN != NaN
    print(f"已写入 {bin_path.name}: {len(values)} 天, 有效 {valid_count} 天")

# 更新 instruments 文件中 SH000300 的 end_date
for ins_file in ["all.txt", "csi300.txt"]:
    ins_path = DATA_DIR / "instruments" / ins_file
    if not ins_path.exists():
        continue
    with open(ins_path, encoding="utf-8") as f:
        lines = f.read().strip().split("\n")
    changed = False
    new_lines = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) == 3 and parts[0] == "SH000300" and parts[2] < calendar[-1]:
            parts[2] = calendar[-1]
            changed = True
        new_lines.append("\t".join(parts))
    if changed:
        with open(ins_path, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines) + "\n")
        print(f"已更新 {ins_file}: SH000300 end_date -> {calendar[-1]}")

print(f"\n完成! SH000300 数据已补齐到 {calendar[-1]}")
