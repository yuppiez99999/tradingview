# -*- coding: utf-8 -*-
"""气象因子引擎 E2E 验证 — 精简版 (v8.6.13).

测试范围:
    1. 模块导入 (WeatherDataAdapter, WeatherFactorEngine, WeatherAgent)
    2. 数据适配器 (apizero → Open-Meteo 降级链)
    3. 气象因子引擎 (3 个代表性标的因子计算)
    4. WeatherAgent (analyze 决策)
    5. 信号融合集成

作者: 28 系统 PM
日期: 2026-08-01
"""
import json
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")
os.environ["PYTHONIOENCODING"] = "utf-8"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "utils"))

os.environ["NO_PROXY"] = "*"
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""

SUCCESS = 0
FAIL = 0


def check(name, ok, detail=""):
    global SUCCESS, FAIL
    if ok:
        SUCCESS += 1
        print(f"  ✅ {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


print("=" * 60)
print("  气象因子引擎 E2E 验证 (v8.6.13)")
print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

# -----------------------------------------------------------
# 1. 模块导入
# -----------------------------------------------------------
print("\n[1/5] 模块导入")
try:
    from utils.weather_data_adapter import WeatherDataAdapter
    check("WeatherDataAdapter 导入", True)
except Exception as e:
    check("WeatherDataAdapter 导入", False, str(e))

try:
    from utils.weather_factor_engine import WeatherFactorEngine, _ensure_config
    check("WeatherFactorEngine 导入", True)
    _ensure_config()
    from utils.weather_factor_engine import _stocks_cache
except Exception as e:
    check("WeatherFactorEngine 导入", False, str(e))
    _stocks_cache = []

try:
    from utils.finance_agents.weather_agent import WeatherAgent
    check("WeatherAgent 导入", True)
except Exception as e:
    check("WeatherAgent 导入", False, str(e))

# -----------------------------------------------------------
# 2. 数据适配器 (降级链验证)
# -----------------------------------------------------------
print("\n[2/5] WeatherDataAdapter")
adapter = WeatherDataAdapter()
check("适配器实例化", True)

print("  测试北京坐标 (116.4,39.9) ...")
rt = adapter.get_realtime(116.4, 39.9)
check("实时天气获取", rt.temperature > -50,
      f"温度={rt.temperature}°C, 来源={adapter.source}")
print(f"    → 温度 {rt.temperature}°C, 湿度 {rt.humidity}%, "
      f"风 {rt.wind_speed}km/h, 云量 {rt.cloudrate}%")

# 降级测试: 用不可能的坐标 (触发 apizero 失败 → Open-Meteo)
rt2 = adapter.get_realtime(0.0, 0.0)
check("降级到 Open-Meteo", adapter.source == "openmeteo",
      f"source={adapter.source}")

# -----------------------------------------------------------
# 3. 气象因子引擎
# -----------------------------------------------------------
print("\n[3/5] WeatherFactorEngine")
try:
    engine = WeatherFactorEngine()
    check("引擎实例化", engine.is_available,
          f"available={engine.is_available}")

    stock_count = len(_stocks_cache or [])
    check("配置已加载标的", stock_count > 0, f"共 {stock_count} 个股票标的")

    # 只测 3 个代表性标的以控制 API 调用
    test_symbols = ["600900.SH", "601088.SH", "300750.SZ"]
    for sym in test_symbols:
        result = engine.evaluate_symbol(sym)
        if result is None:
            check(f"因子计算 {sym}", False, "未在配置中找到")
            continue
        # WeatherFactorResult 是 dataclass
        composite = result.composite_score
        n_factors = len(result.factors)
        data_source = result.data_source
        check(f"因子计算 {sym}", True,
              f"composite={composite:.2f}, 因子数={n_factors}, 源={data_source}")
except Exception as e:
    import traceback
    check("引擎测试", False, f"{e}")
    traceback.print_exc()

# -----------------------------------------------------------
# 4. WeatherAgent
# -----------------------------------------------------------
print("\n[4/5] WeatherAgent")
try:
    agent = WeatherAgent()
    check("WeatherAgent 实例化", True)

    context = {
        "symbol": "600900.SH",
        "weather_data": None,  # 让 agent 调 engine 获取真实数据
        "signal_context": {},
    }
    available = agent.is_available(context)
    check("Agent is_available", available)

    decision = agent.analyze("600900.SH", context)
    check("Agent analyze 返回", decision is not None)
    check("Agent 有 composite_score",
          "composite_score" in decision.key_metrics if decision else False)
    check("Agent 有理由", bool(decision.reasoning) if decision else False,
          decision.reasoning[:100] if decision and decision.reasoning else "无 reasoning")
    if decision:
        print(f"    → 动作: {decision.action}, "
              f"强度: {decision.strength:.2f}, "
              f"置信度: {decision.confidence:.2f}, "
              f"composite: {decision.key_metrics.get('composite_score', 'N/A')}")
except Exception as e:
    import traceback
    check("WeatherAgent 测试", False, str(e))
    traceback.print_exc()

# -----------------------------------------------------------
# 5. 信号融合集成
# -----------------------------------------------------------
print("\n[5/5] 信号融合集成")
try:
    from utils.signal_fusion import SignalFusionEngine
    fuse = SignalFusionEngine()
    has_weather = hasattr(fuse, "_weather_layer")
    check("SignalFusionEngine 有 weather_layer", has_weather)
except Exception as e:
    check("信号融合检查", False, str(e))

# -----------------------------------------------------------
# 汇总
# -----------------------------------------------------------
print("\n" + "=" * 60)
total = SUCCESS + FAIL
rate = SUCCESS / total * 100 if total > 0 else 0
print(f"  测试通过: {SUCCESS}/{total} ({rate:.1f}%)")
print(f"  测试失败: {FAIL}")
print(f"  最终数据源: {adapter.source}")
print("=" * 60)

# 保存快照
snapshot = {
    "timestamp": datetime.now().isoformat(),
    "source": adapter.source,
    "total_symbols": len(_stocks_cache or []),
    "success_rate": f"{SUCCESS}/{total}",
    "failures": FAIL,
}
out_path = PROJECT_ROOT / "reports" / "weather_e2e_result.json"
out_path.parent.mkdir(parents=True, exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(snapshot, f, ensure_ascii=False, indent=2)
print(f"\n快照已保存: {out_path}")

sys.exit(0 if FAIL == 0 else 1)
