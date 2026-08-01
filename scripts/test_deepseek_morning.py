import json
import sys

sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

from deepseek_investment_summary import generate_summary

# Load the actual market data from today's report
with open(r"E:\各种PY程序\每日报告归档\2026-07-17\morning_market_data_20260717.json", encoding="utf-8") as f:
    market_data = json.load(f)

anomalies = []  # No anomalies detected

print("=== 测试 generate_summary(morning) ===")
result = generate_summary("morning", market_data, True, "2026-07-17")
print(f"结果长度: {len(result)}")
print(f"结果前800字符:\n{result[:800]}")
