"""
十五五规划年度阶段管理器单元测试
================================

覆盖:
    1. 5 年度阶段切换 (2026/2027/2028/2029/2030)
    2. 季度末判断 (3/6/9/12 月)
    3. 季度评估触发 (含压力测试降级)
    4. 2030 清仓 Q1-Q4 分步动作
    5. 提前退出触发 (5%/8%/12%/15% 四级回撤)
    6. 计划外日期处理 (pre_plan / post_plan / 未定义年份)
"""
import sys
from datetime import date
from pathlib import Path

# 将项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.phase_manager import (
    ANNUAL_PHASES,
    LIQUIDATION_QUARTERLY_ACTIONS,
    PhaseManager,
    QuarterlyReviewResult,
)


def test_annual_phase_switching():
    """测试 1: 5 年度阶段切换"""
    print("\n" + "=" * 60)
    print("测试 1: 5 年度阶段切换")
    print("=" * 60)

    pm = PhaseManager()

    # 2026 建仓期
    p2026 = pm.get_current_phase(date(2026, 7, 14))
    assert p2026.year == "2026", f"2026 年失败: {p2026.year}"
    assert p2026.phase_name == "建仓期", f"建仓期名称失败: {p2026.phase_name}"
    assert p2026.target_return == 0.08, f"2026 目标收益错误: {p2026.target_return}"
    assert p2026.max_drawdown == 0.08, f"2026 最大回撤错误: {p2026.max_drawdown}"
    assert p2026.leverage_target == 1.28, f"2026 杠杆目标错误: {p2026.leverage_target}"
    assert p2026.is_liquidation_year is False, "2026 不应为清仓年"
    print(f"  ✅ 2026 建仓期 (目标 {p2026.target_return:.0%}, 回撤 {p2026.max_drawdown:.0%}, 杠杆 {p2026.leverage_target}x)")

    # 2027 主线兑现期
    p2027 = pm.get_current_phase(date(2027, 6, 15))
    assert p2027.year == "2027", f"2027 年失败: {p2027.year}"
    assert p2027.phase_name == "主线兑现期", f"主线兑现期名称失败: {p2027.phase_name}"
    assert p2027.target_return == 0.12, f"2027 目标收益错误: {p2027.target_return}"
    assert p2027.max_drawdown == 0.10, f"2027 最大回撤错误: {p2027.max_drawdown}"
    print(f"  ✅ 2027 主线兑现期 (目标 {p2027.target_return:.0%}, 回撤 {p2027.max_drawdown:.0%})")

    # 2028 分化期
    p2028 = pm.get_current_phase(date(2028, 9, 20))
    assert p2028.year == "2028", f"2028 年失败: {p2028.year}"
    assert p2028.phase_name == "分化期", f"分化期名称失败: {p2028.phase_name}"
    assert p2028.target_return == 0.10, f"2028 目标收益错误: {p2028.target_return}"
    assert p2028.leverage_target == 1.20, f"2028 杠杆目标错误: {p2028.leverage_target}"
    print(f"  ✅ 2028 分化期 (目标 {p2028.target_return:.0%}, 杠杆 {p2028.leverage_target}x)")

    # 2029 去杠杆期
    p2029 = pm.get_current_phase(date(2029, 11, 10))
    assert p2029.year == "2029", f"2029 年失败: {p2029.year}"
    assert p2029.phase_name == "去杠杆期", f"去杠杆期名称失败: {p2029.phase_name}"
    assert p2029.target_return == 0.08, f"2029 目标收益错误: {p2029.target_return}"
    assert p2029.leverage_target == 0.96, f"2029 杠杆目标错误: {p2029.leverage_target}"
    print(f"  ✅ 2029 去杠杆期 (目标 {p2029.target_return:.0%}, 杠杆 {p2029.leverage_target}x)")

    # 2030 退出期
    p2030 = pm.get_current_phase(date(2030, 5, 15))
    assert p2030.year == "2030", f"2030 年失败: {p2030.year}"
    assert p2030.phase_name == "退出期", f"退出期名称失败: {p2030.phase_name}"
    assert p2030.is_liquidation_year is True, "2030 应为清仓年"
    assert p2030.target_return == 0.05, f"2030 目标收益错误: {p2030.target_return}"
    assert p2030.max_drawdown == 0.03, f"2030 最大回撤错误: {p2030.max_drawdown}"
    assert p2030.leverage_target == 0.40, f"2030 杠杆目标错误: {p2030.leverage_target}"
    print(f"  ✅ 2030 退出期 (目标 {p2030.target_return:.0%}, 回撤 {p2030.max_drawdown:.0%}, 清仓年=True)")

    print("\n  结果: 5 年度阶段切换全部通过 ✅")


def test_pre_post_plan():
    """测试 2: 计划外日期处理"""
    print("\n" + "=" * 60)
    print("测试 2: 计划外日期处理")
    print("=" * 60)

    pm = PhaseManager()

    # 计划前
    pre = pm.get_current_phase(date(2026, 6, 30))
    assert pre.year == "pre_plan", f"pre_plan 年失败: {pre.year}"
    assert pre.phase_name == "计划未启动", f"pre_plan 名称失败: {pre.phase_name}"
    print(f"  ✅ 计划前 (2026-06-30): {pre.phase_name}")

    # 计划后
    post = pm.get_current_phase(date(2031, 1, 15))
    assert post.year == "post_plan", f"post_plan 年失败: {post.year}"
    assert post.phase_name == "计划已完成", f"post_plan 名称失败: {post.phase_name}"
    print(f"  ✅ 计划后 (2031-01-15): {post.phase_name}")

    # 计划外年份 (2025)
    pre2 = pm.get_current_phase(date(2025, 1, 1))
    assert pre2.year == "pre_plan", f"2025 应为 pre_plan: {pre2.year}"
    print(f"  ✅ 2025 年: {pre2.phase_name}")

    print("\n  结果: 计划外日期处理全部通过 ✅")


def test_quarter_end_detection():
    """测试 3: 季度末判断"""
    print("\n" + "=" * 60)
    print("测试 3: 季度末判断 (3/6/9/12 月最后3天)")
    print("=" * 60)

    pm = PhaseManager()

    # 季度末日期 (2026-03-29 为 3 月倒数第3天)
    cases = [
        (date(2026, 3, 29), True, "3月倒数第3天"),
        (date(2026, 3, 31), True, "3月最后一天"),
        (date(2026, 6, 29), True, "6月倒数第2天"),
        (date(2026, 9, 28), True, "9月最后3天"),
        (date(2026, 12, 29), True, "12月倒数第3天"),
        (date(2026, 7, 14), False, "7月非季度末月"),
        (date(2026, 3, 15), False, "3月非末3天"),
        (date(2026, 6, 10), False, "6月非末3天"),
    ]

    for d, expected, label in cases:
        actual = pm.is_quarter_end(d)
        status = "✅" if actual == expected else "❌"
        assert actual == expected, f"{label} ({d}): 期望 {expected}, 实际 {actual}"
        print(f"  {status} {label} ({d}): is_quarter_end={actual}")

    # 季度标识
    assert pm.get_current_quarter(date(2026, 1, 15)) == "Q1", "Q1 判断失败"
    assert pm.get_current_quarter(date(2026, 4, 15)) == "Q2", "Q2 判断失败"
    assert pm.get_current_quarter(date(2026, 7, 15)) == "Q3", "Q3 判断失败"
    assert pm.get_current_quarter(date(2026, 10, 15)) == "Q4", "Q4 判断失败"
    print("\n  ✅ 季度标识: Q1/Q2/Q3/Q4 判断正确")

    print("\n  结果: 季度末判断全部通过 ✅")


def test_quarterly_review():
    """测试 4: 季度评估触发"""
    print("\n" + "=" * 60)
    print("测试 4: 季度评估触发")
    print("=" * 60)

    pm = PhaseManager()

    # 季度末触发评估
    quarter_end = date(2026, 9, 29)
    result = pm.trigger_quarterly_review(
        positions=[],
        portfolio_value=5_000_000,
        today=quarter_end,
    )

    assert isinstance(result, QuarterlyReviewResult), "返回类型错误"
    assert result.quarter == "Q3", f"季度错误: {result.quarter}"
    assert result.is_quarter_end is True, "应识别为季度末"
    assert result.stress_test_triggered is True, "应触发压力测试"
    assert len(result.actions) > 0, "动作列表不应为空"
    print(f"  ✅ 季度末 (2026-09-29): Q3, 压测触发={result.stress_test_triggered}, 动作数={len(result.actions)}")

    # 非季度末
    non_quarter_end = date(2026, 7, 14)
    result2 = pm.trigger_quarterly_review(today=non_quarter_end)
    assert result2.is_quarter_end is False, "不应识别为季度末"
    assert result2.stress_test_triggered is False, "不应触发压力测试"
    print(f"  ✅ 非季度末 (2026-07-14): Q3, 压测触发={result2.stress_test_triggered}")

    # 带持仓数据的集中度检查
    positions = [
        {"code": "300308", "name": "中际旭创", "sector": "科技", "weight": 0.18},
        {"code": "002475", "name": "立讯精密", "sector": "科技", "weight": 0.05},
        {"code": "600519", "name": "贵州茅台", "sector": "消费", "weight": 0.08},
    ]
    result3 = pm.trigger_quarterly_review(
        positions=positions,
        portfolio_value=5_000_000,
        today=date(2026, 9, 29),
    )
    assert result3.rebalance_needed is True, "应检测到行业超限"
    print(f"  ✅ 集中度超限检测: rebalance_needed={result3.rebalance_needed}")

    print("\n  结果: 季度评估触发全部通过 ✅")


def test_liquidation_actions():
    """测试 5: 2030 清仓 Q1-Q4 分步动作"""
    print("\n" + "=" * 60)
    print("测试 5: 2030 清仓 Q1-Q4 分步动作")
    print("=" * 60)

    pm = PhaseManager()

    # 非清仓年
    assert pm.is_liquidation_phase(date(2026, 7, 14)) is False, "2026 不应清仓"
    assert pm.is_liquidation_phase(date(2029, 12, 31)) is False, "2029 不应清仓"
    assert pm.get_liquidation_actions(date(2026, 7, 14)) is None, "2026 应返回 None"
    print("  ✅ 非清仓年: is_liquidation_phase=False, get_liquidation_actions=None")

    # Q1-Q4 清仓动作
    q1 = pm.get_liquidation_actions(date(2030, 2, 15))
    assert q1 is not None, "Q1 不应为 None"
    assert q1["name"] == "保留核心+方向性清零", f"Q1 名称错误: {q1['name']}"
    assert q1["stock_target_pct"] == 0.50, f"Q1 股票保留 50% 错误: {q1['stock_target_pct']}"
    assert q1["futures_directional"] == "clear", "Q1 方向性应清零"
    print(f"  ✅ Q1: {q1['name']} (保留股票 {q1['stock_target_pct']:.0%})")

    q2 = pm.get_liquidation_actions(date(2030, 5, 15))
    assert q2 is not None, "Q2 不应为 None"
    assert q2["name"] == "分4周系统性清仓", f"Q2 名称错误: {q2['name']}"
    assert q2["weekly_sell_pct"] == 0.25, f"Q2 每周卖出 25% 错误: {q2['weekly_sell_pct']}"
    assert q2["stock_target_pct"] == 0.0, "Q2 股票目标应为 0"
    print(f"  ✅ Q2: {q2['name']} (每周卖出 {q2['weekly_sell_pct']:.0%})")

    q3 = pm.get_liquidation_actions(date(2030, 8, 15))
    assert q3 is not None, "Q3 不应为 None"
    assert q3["name"] == "全部清零+转入安全资产", f"Q3 名称错误: {q3['name']}"
    assert q3["cash_allocation"]["reverse_repo"] == 0.50, "Q3 逆回购 50% 错误"
    assert q3["cash_allocation"]["money_market_fund"] == 0.30, "Q3 货基 30% 错误"
    print(f"  ✅ Q3: {q3['name']} (逆回购 {q3['cash_allocation']['reverse_repo']:.0%}+货基 {q3['cash_allocation']['money_market_fund']:.0%})")

    q4 = pm.get_liquidation_actions(date(2030, 11, 15))
    assert q4 is not None, "Q4 不应为 None"
    assert q4["name"] == "纯现金+清算完成", f"Q4 名称错误: {q4['name']}"
    assert q4["final_liquidation_date"] == "2030-12-31", "Q4 最终清仓日期错误"
    print(f"  ✅ Q4: {q4['name']} (清算日 {q4['final_liquidation_date']})")

    # 清仓顺序
    order = pm.get_liquidation_order()
    assert len(order) == 6, f"清仓顺序应为 6 步, 实际 {len(order)}"
    assert order[0] == "1_illiquid_small_cap", "第1步应为小盘股"
    assert order[-1] == "6_futures_hedge_close", "最后一步应为对冲平仓"
    print("  ✅ 清仓顺序: 6 步 (小盘股 → 量化中性 → 方向性期货 → 大盘股 → 期权 → 对冲平仓)")

    print("\n  结果: 2030 清仓 Q1-Q4 分步动作全部通过 ✅")


def test_early_exit_trigger():
    """测试 6: 提前退出触发 (4 级回撤)"""
    print("\n" + "=" * 60)
    print("测试 6: 提前退出触发 (5%/8%/12%/15% 四级回撤)")
    print("=" * 60)

    pm = PhaseManager()

    # 无触发 (回撤 < 5%)
    result = pm.check_early_exit_trigger(0.03)
    assert result is None, f"回撤 3% 不应触发: {result}"
    print("  ✅ 回撤 3%: 无触发")

    # 5% 预警
    result = pm.check_early_exit_trigger(0.05)
    assert result is not None, "回撤 5% 应触发"
    assert result["trigger"] == "drawdown_5pct", f"5% 触发器错误: {result['trigger']}"
    assert result["action"] == "alert_review", f"5% 动作错误: {result['action']}"
    print(f"  ✅ 回撤 5%: trigger={result['trigger']}, action={result['action']}")

    # 8% 一级防御
    result = pm.check_early_exit_trigger(0.08)
    assert result is not None, "回撤 8% 应触发"
    assert result["trigger"] == "drawdown_8pct", f"8% 触发器错误: {result['trigger']}"
    assert "reduce_20pct" in result["action"], f"8% 动作错误: {result['action']}"
    print(f"  ✅ 回撤 8%: trigger={result['trigger']}, action={result['action']}")

    # 12% 二级防御
    result = pm.check_early_exit_trigger(0.12)
    assert result is not None, "回撤 12% 应触发"
    assert result["trigger"] == "drawdown_12pct", f"12% 触发器错误: {result['trigger']}"
    assert "reduce_60pct" in result["action"], f"12% 动作错误: {result['action']}"
    print(f"  ✅ 回撤 12%: trigger={result['trigger']}, action={result['action']}")

    # 15% 极限防御
    result = pm.check_early_exit_trigger(0.15)
    assert result is not None, "回撤 15% 应触发"
    assert result["trigger"] == "drawdown_15pct", f"15% 触发器错误: {result['trigger']}"
    assert result["action"] == "defensive_mode", f"15% 动作错误: {result['action']}"
    assert result["target_allocation"]["cash"] == 0.60, "15% 现金目标错误"
    print(f"  ✅ 回撤 15%: trigger={result['trigger']}, action={result['action']}, cash=60%")

    # 超过 15% (如 18%)
    result = pm.check_early_exit_trigger(0.18)
    assert result is not None, "回撤 18% 应触发"
    assert result["trigger"] == "drawdown_15pct", "18% 仍应触发 15% 红线"
    print("  ✅ 回撤 18%: 触发 15% 红线 (defensive_mode)")

    print("\n  结果: 提前退出 4 级回撤触发全部通过 ✅")


def test_summary_output():
    """测试 7: 摘要输出"""
    print("\n" + "=" * 60)
    print("测试 7: 摘要输出")
    print("=" * 60)

    pm = PhaseManager()

    # 普通日
    summary = pm.summary(date(2026, 7, 14))
    assert "建仓期" in summary, "摘要应包含 '建仓期'"
    assert "2026" in summary, "摘要应包含 '2026'"
    assert "8.0%" in summary, "摘要应包含目标收益"
    print(f"  ✅ 2026-07-14 普通日摘要 (长度 {len(summary)} 字符)")

    # 季度末
    summary_q = pm.summary(date(2026, 9, 29))
    assert "季度末" in summary_q, "季度末摘要应包含 '季度末'"
    print("  ✅ 2026-09-29 季度末摘要 (含触发标记)")

    # 清仓年
    summary_l = pm.summary(date(2030, 5, 15))
    assert "2030 清仓年" in summary_l, "清仓年摘要应包含 '2030 清仓年'"
    assert "Q2" in summary_l, "清仓年摘要应包含季度"
    print("  ✅ 2030-05-15 清仓年 Q2 摘要 (含清仓动作)")

    print("\n  结果: 摘要输出全部通过 ✅")


def test_config_completeness():
    """测试 8: 配置完整性"""
    print("\n" + "=" * 60)
    print("测试 8: 配置完整性")
    print("=" * 60)

    # 5 年度阶段全部定义
    expected_years = {"2026", "2027", "2028", "2029", "2030"}
    actual_years = set(ANNUAL_PHASES.keys())
    assert actual_years == expected_years, f"年度阶段缺失: 期望 {expected_years}, 实际 {actual_years}"
    print(f"  ✅ 5 年度阶段全部定义: {sorted(actual_years)}")

    # 每个年度必填字段
    required_fields = ["name", "target_return", "max_drawdown", "leverage_target", "actions", "risk_focus"]
    for year, cfg in ANNUAL_PHASES.items():
        for field_name in required_fields:
            assert field_name in cfg, f"{year} 缺少字段: {field_name}"
    print(f"  ✅ 每个年度阶段必填字段完整 ({len(required_fields)} 个字段)")

    # 2030 必须包含清仓顺序
    assert "liquidation_order" in ANNUAL_PHASES["2030"], "2030 应包含 liquidation_order"
    assert len(ANNUAL_PHASES["2030"]["liquidation_order"]) == 6, "2030 清仓顺序应为 6 步"
    print("  ✅ 2030 清仓顺序: 6 步")

    # Q1-Q4 清仓动作全部定义
    expected_qs = {"Q1", "Q2", "Q3", "Q4"}
    actual_qs = set(LIQUIDATION_QUARTERLY_ACTIONS.keys())
    assert actual_qs == expected_qs, f"Q1-Q4 缺失: 期望 {expected_qs}, 实际 {actual_qs}"
    print(f"  ✅ Q1-Q4 清仓动作全部定义: {sorted(actual_qs)}")

    # 目标收益递减验证 (2027 最高 → 2030 最低)
    returns = [ANNUAL_PHASES[y]["target_return"] for y in ["2026", "2027", "2028", "2029", "2030"]]
    assert returns[1] == max(returns), "2027 应为最高目标收益"
    assert returns[4] == min(returns), "2030 应为最低目标收益"
    print(f"  ✅ 目标收益趋势: 2027 最高 {returns[1]:.0%} → 2030 最低 {returns[4]:.0%}")

    # 杠杆目标递减验证
    leverages = [ANNUAL_PHASES[y]["leverage_target"] for y in ["2026", "2027", "2028", "2029", "2030"]]
    assert leverages[0] == leverages[1] == 1.28, "2026/2027 杠杆应一致 (1.28x)"
    assert leverages[4] == 0.40, "2030 杠杆应为 0.40x"
    print(f"  ✅ 杠杆目标趋势: 2026/2027={leverages[0]}x → 2030={leverages[4]}x")

    print("\n  结果: 配置完整性全部通过 ✅")


def main():
    """运行所有测试"""
    print("\n" + "#" * 60)
    print("# 十五五规划年度阶段管理器 - 单元测试")
    print("#" * 60)

    tests = [
        test_annual_phase_switching,
        test_pre_post_plan,
        test_quarter_end_detection,
        test_quarterly_review,
        test_liquidation_actions,
        test_early_exit_trigger,
        test_summary_output,
        test_config_completeness,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"\n  ❌ {test.__name__} 失败: {e}")
        except Exception as e:
            failed += 1
            print(f"\n  ❌ {test.__name__} 异常: {type(e).__name__}: {e}")

    print("\n" + "#" * 60)
    print(f"# 测试总结: {passed}/{len(tests)} 通过, {failed} 失败")
    print("#" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
