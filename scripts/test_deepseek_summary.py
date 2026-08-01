# -*- coding: utf-8 -*-
import sys

sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

from deepseek_investment_summary import generate_summary

market_data = {
    "global": {
        "usd_index": {"data": {"data": [{"columns": [{"name": "价格", "unit": ""}], "rows": [[100.72]]}]}, "source": "Wind MCP"},
        "wti_crude": {"data": {"data": [{"columns": [{"name": "价格", "unit": "美元/桶"}], "rows": [[79.6]]}]}, "source": "Wind MCP"},
    },
    "coal": {},
    "copper": {},
    "carbon": {},
}

print("=== 测试 deepseek_investment_summary ===")
result = generate_summary("morning", market_data, True, "2026-07-17")
print(f"结果长度: {len(result)}")
print(f"结果前500字符:\n{result[:500]}")
