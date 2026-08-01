import os

QLIB_DATA_DIR = r"E:\各种PY程序\28-终极量化交易系统8.4\qlib_data\cn_data"
import qlib
qlib.init(provider_uri=QLIB_DATA_DIR, region="cn")

from qlib.data import D

print("=== 示例数据 (sh600519):")
df = D.features(["sh600519"], ["$close", "$volume", "$factor", "$pe", "$pb", "$roeq"], start_time="2024-01-01")
print(f"  行数: {len(df)}")
print(f"  列: {list(df.columns)}")
print(df.head(5))
print()

print("=== 检查所有可用字段:")
feature_dir = os.path.join(QLIB_DATA_DIR, "features", "sh600519")
if os.path.exists(feature_dir):
    bins = os.listdir(feature_dir)
    print(f"  {len(bins)} 个特征文件:")
    for b in sorted(bins):
        print(f"    {b}")
