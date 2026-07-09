import json
from pathlib import Path

base = Path(r"e:\各种PY程序\28-终极量化交易系统7.1\config")
returns_path = base / "returns_history.json"
market_path = base / "market_returns.json"

print("=== returns_history.json ===")
with open(returns_path, "r", encoding="utf-8") as f:
    data = json.load(f)
print(f"keys: {list(data.keys())}")
print(f"index length: {len(data.get('index', []))}")
print(f"data rows: {len(data.get('data', []))}")
print(f"columns count: {len(data.get('columns', []))}")
print(f"first 3 index: {data.get('index', [])[:3]}")
print(f"last 3 index: {data.get('index', [])[-3:]}")
print(f"first 10 columns: {data.get('columns', [])[:10]}")
arr = data.get("data", [])
print(f"data[0] length: {len(arr[0]) if arr else 0}")
print(f"data[0][:5]: {arr[0][:5] if arr else []}")
print(f"data[1][:5]: {arr[1][:5] if len(arr) > 1 else []}")
nonzero = sum(1 for row in arr for v in row if v != 0)
total = sum(len(row) for row in arr)
print(f"nonzero/total: {nonzero}/{total} = {nonzero/total*100:.4f}%")

print("\n=== market_returns.json ===")
with open(market_path, "r", encoding="utf-8") as f:
    mdata = json.load(f)
print(f"keys: {list(mdata.keys())}")
print(f"name: {mdata.get('name')}")
print(f"index length: {len(mdata.get('index', []))}")
print(f"first 3 index: {mdata.get('index', [])[:3]}")
mar = mdata.get("data", [])
print(f"data length: {len(mar)}")
print(f"first 5: {mar[:5]}")
print(f"nonzero/total: {sum(1 for v in mar if v != 0)}/{len(mar)}")
