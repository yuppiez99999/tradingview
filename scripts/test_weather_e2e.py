# -*- coding: utf-8 -*-
"""气象因子集成端到端验证脚本 (v8.6.13)

验证链路:
    1. WeatherDataAdapter → apizero.cn / Open-Meteo 数据获取
    2. WeatherFactorEngine → 7 因子计算 + 信号生成
    3. WeatherAgent → AgentDecision 标准化输出
    4. signal_fusion.PostMixLayer → 气象信号叠加
    5. FinanceAgentOrchestrator → WeatherAgent 注册 + 多Agent协同

运行方式:
    python scripts/test_weather_e2e.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from typing import Dict, List


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 强制绕过代理
os.environ.setdefault(
    "NO_PROXY", "apizero.cn,open-meteo.com,caiyunapp.com"
)


class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors: List[str] = []

    def ok(self, name: str):
        self.passed += 1
        print(f"  ✅ PASS: {name}")

    def fail(self, name: str, detail: str = ""):
        self.failed += 1
        msg = f"  ❌ FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        self.errors.append(msg)

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*60}")
        print(f"  总计: {total} 项 | 通过: {self.passed} | 失败: {self.failed}")
        if self.failed > 0:
            print(f"\n  失败详情:")
            for e in self.errors:
                print(f"    {e.strip()}")
        print(f"{'='*60}")
        return self.failed == 0


results = TestResult()


# ============================================================
# T1: WeatherDataAdapter 模块导入
# ============================================================
print("\n[测试 1] WeatherDataAdapter 模块导入")
try:
    from utils.weather_data_adapter import (
        WeatherRealtime,
        WeatherForecast,
        get_adapter,
    )
    results.ok("WeatherDataAdapter 导入")
except Exception as e:
    results.fail("WeatherDataAdapter 导入", str(e))
    traceback.print_exc()


# ============================================================
# T2: WeatherDataAdapter 数据获取
# ============================================================
print("\n[测试 2] WeatherDataAdapter 数据获取")
try:
    adapter = get_adapter()
    results.ok("适配器实例化")

    # 测试北京坐标 (116.4, 39.9)
    lon, lat = 116.4, 39.9

    # 实时天气
    snap = adapter.get_realtime(lon, lat)
    assert isinstance(snap, WeatherRealtime), "返回类型错误"
    results.ok(f"实时天气获取 (温度={snap.temperature}℃, 来源={adapter.source})")

    # 基本属性
    _ = snap.is_rainy
    _ = snap.is_extreme_heat
    _ = snap.is_frozen
    _ = snap.low_visibility
    _ = snap.strong_wind
    results.ok("WeatherRealtime 属性访问")

    # 小时预报
    hourly = adapter.get_hourly(lon, lat, hours=24)
    assert isinstance(hourly, list), "小时预报应为列表"
    results.ok(f"小时预报获取 ({len(hourly)} 个数据点)")

    # 天预报
    daily = adapter.get_daily(lon, lat, days=7)
    assert isinstance(daily, list), "天预报应为列表"
    results.ok(f"天预报获取 ({len(daily)} 天)")

    # 综合预报
    forecast = adapter.get_forecast(lon, lat, days=7)
    assert isinstance(forecast, WeatherForecast), "综合预报类型错误"
    results.ok(f"综合预报获取 (来源={forecast.source})")

    # 预警
    alerts = adapter.get_alerts(lon, lat)
    assert isinstance(alerts, list), "预警应为列表"
    results.ok(f"预警获取 ({len(alerts)} 条)")

except Exception as e:
    results.fail("数据获取", str(e))
    traceback.print_exc()


# ============================================================
# T3: WeatherFactorEngine 因子计算
# ============================================================
print("\n[测试 3] WeatherFactorEngine 因子计算")
try:
    from utils.weather_factor_engine import (
        WeatherFactorResult,
        get_engine,
    )
    results.ok("WeatherFactorEngine 导入")

    engine = get_engine()
    results.ok("引擎实例化")

    # 检查配置加载
    assert engine.is_available, "引擎应标记为可用"
    results.ok("引擎可用性检测")

    # 评估长江电力 (600900.SH — 电力能源)
    result = engine.evaluate_symbol("600900.SH")
    assert result is not None, "长江电力应在配置中"
    assert isinstance(result, WeatherFactorResult), "返回类型错误"
    results.ok(
        f"单标的评估: {result.symbol} ({result.name}) "
        f"信号={result.signal} 得分={result.composite_score:+.2f} "
        f"置信度={result.confidence:.2f}"
    )

    # 因子列表
    factor_names = [f.name for f in result.factors]
    assert len(factor_names) >= 7, f"应有 7 个因子, 实际 {len(factor_names)}"
    results.ok(f"7因子计算: {factor_names}")

    # 关键驱动因子
    assert isinstance(result.key_drivers, list), "key_drivers 应为列表"
    results.ok(f"关键驱动因子: {result.key_drivers[:3]}")

    # 推理文本
    assert result.reasoning, "reasoning 不应为空"
    results.ok(f"推理解释: {result.reasoning[:80]}...")

except Exception as e:
    results.fail("因子计算", str(e))
    traceback.print_exc()


# ============================================================
# T4: 批量标的评估
# ============================================================
print("\n[测试 4] 批量标的评估")
try:
    engine = get_engine()
    all_results = engine.evaluate_all()
    results.ok(f"全量评估: {len(all_results)} 个标的")

    # 按得分排序
    if len(all_results) >= 2:
        assert abs(all_results[0].composite_score) >= abs(all_results[-1].composite_score)
        results.ok("按绝对得分降序排列")

    # 统计各信号分布
    from collections import Counter
    signal_counts = Counter(r.signal for r in all_results)
    results.ok(f"信号分布: {dict(signal_counts)}")

    # 按类别分组
    category_scores: Dict[str, List[float]] = {}
    for r in all_results:
        cat = r.category
        category_scores.setdefault(cat, []).append(r.composite_score)
    cat_summary = {
        cat: round(sum(scores) / len(scores), 3)
        for cat, scores in category_scores.items()
    }
    results.ok(f"分类别平均得分: {cat_summary}")

except Exception as e:
    results.fail("批量评估", str(e))
    traceback.print_exc()


# ============================================================
# T5: 板块级评估
# ============================================================
print("\n[测试 5] 板块级评估")
try:
    engine = get_engine()

    for sector in ["power_energy", "mining", "smelting"]:
        sector_result = engine.evaluate_sector(sector)
        if sector_result:
            results.ok(
                f"板块 [{sector}]: 信号={sector_result.signal} "
                f"得分={sector_result.composite_score:+.2f} "
                f"覆盖={len(sector_result.symbol_results)} 个标的"
            )
        else:
            results.fail(f"板块 [{sector}]", "返回 None")

except Exception as e:
    results.fail("板块评估", str(e))
    traceback.print_exc()


# ============================================================
# T6: WeatherAgent AgentDecision 输出
# ============================================================
print("\n[测试 6] WeatherAgent 标准化决策")
try:
    from utils.finance_agents.weather_agent import WeatherAgent, create_weather_agent
    from utils.finance_agents.base_agent import AgentDecision

    agent = create_weather_agent()
    results.ok("WeatherAgent 实例化")

    # 检查可用性
    available = agent.is_available({})
    results.ok(f"Agent 可用性: {available}")

    # 分析长江电力
    decision = agent.analyze("600900.SH", {})
    assert isinstance(decision, AgentDecision), "返回类型应为 AgentDecision"
    results.ok(
        f"决策输出: {decision.agent_name} → {decision.action.upper()} "
        f"强度={decision.strength:+.3f} 置信度={decision.confidence:.3f}"
    )

    # 检查 key_metrics
    assert "composite_score" in decision.key_metrics, "缺少 composite_score"
    assert "weather_signal" in decision.key_metrics, "缺少 weather_signal"
    results.ok(f"关键指标: {list(decision.key_metrics.keys())[:6]}")

    # 检查 reasoning
    assert decision.reasoning, "推理文本不应为空"
    results.ok(f"推理: {decision.reasoning[:80]}...")

    # 测试未知标的降级
    unknown = agent.analyze("999999.UNKNOWN", {})
    assert unknown.action == "hold", "未知标的应降级为 hold"
    results.ok("未知标的降级处理")

except Exception as e:
    results.fail("WeatherAgent 决策", str(e))
    traceback.print_exc()


# ============================================================
# T7: 预计算数据注入
# ============================================================
print("\n[测试 7] 预计算数据注入")
try:
    from utils.finance_agents.weather_agent import WeatherAgent

    agent = WeatherAgent()

    # 构造预计算的天气结果
    pre_computed = {
        "composite_score": 1.5,
        "confidence": 0.8,
        "signal": "STRONG_BULL",
        "reasoning": "测试预计算注入",
        "data_source": "test",
        "category": "power_energy",
        "weather_sensitivity": 0.95,
        "key_drivers": ["temperature(+0.80)", "precipitation(+0.70)"],
        "alerts": [],
    }

    decision = agent.analyze("600900.SH", {"weather_data": pre_computed})
    assert decision.action == "buy", f"预计算 STRONG_BULL 应为 buy, 实际 {decision.action}"
    assert decision.strength > 0, "强度应为正"
    results.ok(f"预计算注入: {decision.action} 强度={decision.strength:+.3f}")

except Exception as e:
    results.fail("预计算注入", str(e))
    traceback.print_exc()


# ============================================================
# T8: SignalFusion PostMixLayer 集成
# ============================================================
print("\n[测试 8] SignalFusion 气象信号层集成")
try:
    from utils.signal_fusion import SignalFusionEngine

    engine = SignalFusionEngine()
    results.ok("SignalFusionEngine 实例化")

    # 检查 PostMixLayer 是否存在
    assert hasattr(engine, "_weather_layer"), "缺少 _weather_layer"
    assert engine._weather_layer.name == "weather_factor"
    assert engine._weather_layer.weight == 0.04
    results.ok(f"_weather_layer 存在 (name={engine._weather_layer.name}, weight={engine._weather_layer.weight})")

    # 注入天气信号 (PostMixLayer.update_signals)
    test_signals = {"600900.SH": 0.5, "601088.SH": -0.3}
    engine._weather_layer.update_signals(test_signals)
    results.ok("PostMixLayer.update_signals 调用成功")

    # 获取信号
    sig = engine._weather_layer.get_signal("600900.SH")
    assert sig == 0.5, f"信号值应为 0.5, 实际 {sig}"
    results.ok(f"_weather_layer.get_signal('600900.SH') = {sig}")

    # 应用 post-mix 叠加
    new_strength, applied = engine._weather_layer.apply(0.3, "600900.SH")
    assert applied, "信号应被应用"
    results.ok(f"_weather_layer.apply(0.3) → {new_strength:.4f} (applied={applied})")

    # 测试 fuse 主入口 (需要 alpha_signals 作为基础)
    alpha_input = {"600900.SH": {"strength": 0.2, "confidence": 0.6}}
    fused = engine.fuse(alpha_signals=alpha_input)
    assert isinstance(fused, list), "fuse 应返回列表"
    assert len(fused) >= 1, "至少应有 1 条融合结果"

    first = fused[0]
    # 检查 weather_factor_strength 在 sources 中
    sources = first.sources if hasattr(first, "sources") else {}
    assert "weather_factor_strength" in sources, "缺少 weather_factor_strength"
    results.ok(
        f"fuse() 融合: strength={first.strength:+.4f} "
        f"weather_factor_strength={sources.get('weather_factor_strength', 0):+.4f}"
    )

except Exception as e:
    results.fail("SignalFusion 集成", str(e))
    traceback.print_exc()


# ============================================================
# T9: FinanceAgentOrchestrator WeatherAgent 注册
# ============================================================
print("\n[测试 9] FinanceAgentOrchestrator 集成")
try:
    from utils.finance_agent_orchestrator import FinanceAgentOrchestrator

    orch = FinanceAgentOrchestrator()
    results.ok("FinanceAgentOrchestrator 实例化")

    # 检查 weights
    weights = orch.DEFAULT_WEIGHTS
    assert "weather" in weights, f"缺少 weather 权重, 当前: {list(weights.keys())}"
    assert abs(weights["weather"] - 0.11) < 0.01, f"weather 权重应为 0.11, 实际 {weights['weather']}"
    results.ok(f"weather 权重 = {weights['weather']} (共 {len(weights)} 个 Agent)")

    # 检查 weather agent 已注册
    weather_agent = orch.agent_map.get("weather")
    assert weather_agent is not None, "WeatherAgent 未注册到 agent_map"
    results.ok("WeatherAgent 已注册到 agent_map")

    # 执行 orchestrate (所有 Agent 协同决策)
    consensus = orch.orchestrate("600900.SH", {})
    assert consensus is not None, "协同决策不应为 None"
    # AgentConsensus 有 .final_action / .confidence 等属性
    final_action = getattr(consensus, "final_action", getattr(consensus, "action", "hold"))
    confidence = getattr(consensus, "confidence", 0.0)
    decision_count = len(getattr(consensus, "decisions", []))
    results.ok(
        f"多Agent协同决策: {final_action.upper()} "
        f"置信度={confidence:.3f} "
        f"参与决策数={decision_count}"
    )

except Exception as e:
    results.fail("Orchestrator 集成", str(e))
    traceback.print_exc()


# ============================================================
# T10: 回测模式验证 (简化版)
# ============================================================
print("\n[测试 10] 历史数据回测验证")
try:
    from utils.weather_factor_engine import get_engine
    engine = get_engine()

    # 用当前天气数据模拟"历史快照"
    test_symbols = ["600900.SH", "601088.SH", "300750.SZ", "600276.SH"]
    results_list = []

    for sym in test_symbols:
        r = engine.evaluate_symbol(sym)
        if r:
            results_list.append({
                "symbol": r.symbol,
                "name": r.name,
                "composite_score": round(r.composite_score, 4),
                "signal": r.signal,
                "confidence": round(r.confidence, 4),
                "category": r.category,
                "weather_sensitivity": r.weather_sensitivity,
                "key_drivers": r.key_drivers[:2],
            })

    assert len(results_list) == len(test_symbols), f"应有 {len(test_symbols)} 条结果"
    results.ok(f"回测快照: {len(results_list)} 个标的")

    # 输出 JSON 快照
    snapshot_path = os.path.join(
        PROJECT_ROOT, "reports", "weather_factor_snapshot.json"
    )
    os.makedirs(os.path.dirname(snapshot_path), exist_ok=True)
    snapshot = {
        "timestamp": time.time(),
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "weather_e2e_test",
        "results": results_list,
        "summary": {
            "total": len(results_list),
            "avg_score": round(
                sum(r["composite_score"] for r in results_list) / len(results_list), 4
            ),
            "by_category": {},
        },
    }
    # 分类统计
    for r in results_list:
        cat = r["category"]
        snapshot["summary"]["by_category"].setdefault(cat, []).append(r["composite_score"])
    for cat, scores in snapshot["summary"]["by_category"].items():
        snapshot["summary"]["by_category"][cat] = round(sum(scores) / len(scores), 4)

    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    results.ok(f"快照已保存: {snapshot_path}")

    # 打印摘要表
    print("\n  气象因子快照摘要:")
    print(f"  {'='*70}")
    print(f"  {'标的':<12} {'名称':<10} {'信号':<12} {'得分':>8} {'置信度':>8} {'类别':<15}")
    print(f"  {'-'*70}")
    for r in results_list:
        print(
            f"  {r['symbol']:<12} {r['name']:<10} {r['signal']:<12} "
            f"{r['composite_score']:>+8.3f} {r['confidence']:>8.3f} {r['category']:<15}"
        )
    print(f"  {'='*70}")

except Exception as e:
    results.fail("回测验证", str(e))
    traceback.print_exc()


# ============================================================
# 最终结果
# ============================================================
print("\n" + "=" * 60)
success = results.summary()
if success:
    print("  🎉 气象因子集成端到端验证全部通过!")
    print("     系统已就绪, 气象因子将作为第 9 类信号源参与决策")
else:
    print("  ⚠️  部分测试失败, 请检查上方错误详情")
print("=" * 60)

sys.exit(0 if success else 1)
