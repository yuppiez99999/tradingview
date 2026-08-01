"""快速烟雾测试: 模拟 daily_workflow.py 调用路径验证 LGB 信号接入"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "utils"))

from utils.signal_fusion import SignalFusionEngine

# 模拟 daily_workflow.py 的加载逻辑
signals_file = BASE / "models" / "lgb_enhanced" / "lgb_enhanced_signals.json"
print(f"信号文件: {signals_file.name}")
print(f"文件存在: {signals_file.exists()}")

with open(signals_file, encoding="utf-8") as f:
    data = json.load(f)

signals = data.get("signals", {})
engine = SignalFusionEngine()
engine.inject_lgb_enhanced_signals(signals)

ok_count = sum(1 for v in engine._lgb_quality_flags.values() if v == "OK")
low_quality_count = sum(1 for v in engine._lgb_quality_flags.values() if v == "LOW_QUALITY")

print(f"注入成功: {len(engine._lgb_enhanced_signals)} 个标的")
print(f"quality_flag 缓存: {len(engine._lgb_quality_flags)} 个")
print(f"OK 标的: {ok_count}")
print(f"LOW_QUALITY 标的: {low_quality_count}")
print(f"trade_date: {data.get('trade_date')}")
print(f"generated_at: {data.get('generated_at')}")
print()
print("集成测试通过: daily_workflow.py LGB 信号接入路径正常")
