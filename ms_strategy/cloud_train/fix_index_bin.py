"""
修复 SH000300 二进制数据文件
qlib 格式: 前4字节=start_index(float32, 日历索引), 后面=数据数组
update_index_data.py 的错误: 没写 start_index 头部, 且无数据日期填了 NaN
修复: 写入 start_index=0.0, 无数据日期填 0.0
"""
import csv
import struct
from pathlib import Path

DATA_DIR = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\qlib_data\cn_data")
WIND_CSV = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\reports\wind_csi300_index.csv")
STOCK_DIR = DATA_DIR / "features" / "SH000300"

with open(DATA_DIR / "calendars" / "day.txt", encoding="utf-8") as f:
    calendar = f.read().strip().split("\n")
print(f"日历: {len(calendar)} 天, {calendar[0]} ~ {calendar[-1]}")

wind_data = {}
with open(WIND_CSV, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        date_str = row["date"][:10]
        wind_data[date_str] = {
            "open": float(row["OPEN"]),
            "close": float(row["MATCH"]),
            "high": float(row["HIGH"]),
            "low": float(row["LOW"]),
            "volume": float(row["VOLUME"]),
        }
print(f"Wind 数据: {len(wind_data)} 天, {min(wind_data)} ~ {max(wind_data)}")

first_valid_idx = None
for i, date in enumerate(calendar):
    if date in wind_data:
        first_valid_idx = i
        break
print(f"日历中第一个有数据的日期: {calendar[first_valid_idx]} (索引 {first_valid_idx})")

fields = {"open": [], "close": [], "high": [], "low": [], "volume": []}
for date in calendar:
    if date in wind_data:
        d = wind_data[date]
        fields["open"].append(d["open"])
        fields["close"].append(d["close"])
        fields["high"].append(d["high"])
        fields["low"].append(d["low"])
        fields["volume"].append(d["volume"])
    else:
        for k in fields:
            fields[k].append(0.0)

for field_name, values in fields.items():
    bin_path = STOCK_DIR / f"{field_name}.day.bin"
    with open(bin_path, "wb") as f:
        f.write(struct.pack("<f", 0.0))
        f.write(struct.pack(f"<{len(values)}f", *values))
    valid_count = sum(1 for v in values if v != 0.0)
    print(f"已写入 {bin_path.name}: start_index=0, {len(values)} 天, 非零 {valid_count} 天")

print("\n完成! SH000300 数据已修复")
