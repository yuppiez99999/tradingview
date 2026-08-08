"""510050 Covered Call 备兑担保核对工具

核对规则:
- 1 张 Covered Call = 5 手 = 50000 份 ETF
- 当前 Covered Call 计划: 510050 卖出 5 张，需 >= 50000 份
- 若实际持仓 < 50000，给出精确补仓数量
"""

import json
from pathlib import Path

BASE_DIR = Path(r"E:\各种PY程序\28-终极量化交易系统8.4")
POSITIONS_FILE = BASE_DIR / "config" / "positions.json"


def check_510050_collateral() -> dict:
    with open(POSITIONS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    pos = data["positions"]["510050.SH"]
    system_shares = int(pos["shares"])
    est_price = float(pos["est_price"])

    # 1 张 Covered Call = 5 手 = 50000 份
    required_shares = 50000
    gap = required_shares - system_shares

    result = {
        "code": "510050.SH",
        "name": pos.get("name", ""),
        "system_shares": system_shares,
        "required_shares": required_shares,
        "gap": gap,
        "est_price": est_price,
        "is_sufficient": system_shares >= required_shares,
        "estimated_topup_cost": round(max(gap, 0) * est_price, 2) if gap > 0 else 0.0,
    }

    return result


if __name__ == "__main__":
    r = check_510050_collateral()
    print("510050 Covered Call 备兑担保核对结果:")
    print(f"  系统记录持仓: {r['system_shares']} 份")
    print(f"  备兑要求:     {r['required_shares']} 份")
    print(f"  缺口:         {r['gap']} 份")
    print(f"  是否满足:     {'是' if r['is_sufficient'] else '否'}")
    if not r["is_sufficient"]:
        print(f"  预估补仓成本: {r['estimated_topup_cost']} 元 (按 est_price={r['est_price']})")
