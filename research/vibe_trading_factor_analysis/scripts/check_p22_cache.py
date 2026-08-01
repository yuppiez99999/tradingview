"""检查 P2.2 历史季度数据缓存"""
import json
from pathlib import Path

fund_dir = Path("cache/fundamentals")
history_files = list(fund_dir.glob("*_history.json"))
print(f"历史季度数据缓存文件数: {len(history_files)}")

if history_files:
    print("前 5 个文件:")
    for f in history_files[:5]:
        print(f"  {f.name}")

    # 抽样
    with open(history_files[0], encoding="utf-8") as fp:
        sample = json.load(fp)
    print()
    print(f"样本 {history_files[0].name}:")
    print(f"  n_valid={sample.get('n_valid')}, data_quality={sample.get('data_quality')}")
    print(f"  quarters 数量: {len(sample.get('quarters', []))}")
    if sample.get("quarters"):
        print(f"  最新季度: {sample['quarters'][0]}")

    # 统计总体情况
    valid_count = 0
    total_count = len(history_files)
    for hf in history_files:
        try:
            with open(hf, encoding="utf-8") as fp:
                d = json.load(fp)
            if d.get("n_valid", 0) >= 4:
                valid_count += 1
        except Exception:
            pass
    print()
    print(f"有效缓存 (n_valid >= 4): {valid_count} / {total_count}")
    print(f"有效率: {valid_count / total_count * 100:.1f}%" if total_count > 0 else "无缓存")
