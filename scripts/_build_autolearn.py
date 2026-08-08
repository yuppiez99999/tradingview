"""生成 autolearn_trainer.py 的 POSITION_SYMBOLS 数据"""
import json
from pathlib import Path

data = json.loads(Path("config/positions.json").read_text(encoding="utf-8"))
positions = data.get("positions", {})
# 输出格式: (code, name, shares, style, sector)
lines = []
for k, v in sorted(positions.items()):
    if not isinstance(v, dict):
        continue
    name = v.get("name", "")
    shares = v.get("shares", 0) or v.get("total_shares", 0) or 0
    style = v.get("style", "") or v.get("type", "") or ""
    sector = v.get("sector", "") or v.get("style", "") or ""
    lines.append(f'    ({json.dumps(k, ensure_ascii=False)}, '
                 f'{json.dumps(name, ensure_ascii=False)}, '
                 f'{shares}, '
                 f'{json.dumps(style, ensure_ascii=False)}, '
                 f'{json.dumps(sector, ensure_ascii=False)}),')

print(f"# {len(lines)} 个持仓标的")
print("POSITION_SYMBOLS = [")
print("\n".join(lines))
print("]")
