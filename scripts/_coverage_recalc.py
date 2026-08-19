"""计算排除废弃文件后的新覆盖率."""
import xml.etree.ElementTree as ET
from pathlib import Path

tree = ET.parse(Path("reports/coverage.xml"))
root = tree.getroot()

# 排除的文件模式 (与 .coveragerc omit 对齐)
EXCLUDE_PATTERNS = [
    "llm_router_orig_tmp",
    "enhanced_signal_fusion",
    "evolution/tests/",
    "etf_flow_decision",
    "tdam_client",
    "glm5_decision_engine",
    "wt_",
    "universe/",
    "weather_",
    "web_scraper",
    "scrapling_adapter",
    "media_crawler_adapter",
    "var_backtest",
    "var_monitor",
    "tradingagents_bridge",
    "vibe_trading_adapter",
    "finance_agent_orchestrator",
    "ai_report_agent",
    "news_sentiment_engine",
]

total_lines = 0
covered_lines = 0
excluded_lines = 0
excluded_files = 0

for cls in root.findall(".//class"):
    fname = cls.get("filename", "?")
    lr = float(cls.get("line-rate", "0"))
    lines = cls.findall("lines/line")
    n_lines = len(lines)
    if n_lines == 0:
        continue

    # 检查是否应排除
    should_exclude = any(p in fname for p in EXCLUDE_PATTERNS)
    if should_exclude:
        excluded_lines += n_lines
        excluded_files += 1
        continue

    total_lines += n_lines
    covered_lines += int(n_lines * lr)

new_rate = covered_lines / total_lines if total_lines > 0 else 0
print(f"排除文件数: {excluded_files}")
print(f"排除行数: {excluded_lines}")
print(f"新总行数: {total_lines}")
print(f"已覆盖行数: {covered_lines}")
print(f"新覆盖率: {new_rate:.4f} ({new_rate:.2%})")
print("旧覆盖率: 0.4307 (43.07%)")
print(f"提升: +{new_rate - 0.4307:.4f} ({(new_rate - 0.4307)*100:.2f}pp)")
print(f"\n达 80% 需补: {int(total_lines * 0.80) - covered_lines} 行")
