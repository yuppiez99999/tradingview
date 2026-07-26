#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
代码质量优化验证脚本 (2026-07-26)
================================
验证 P0-Q1/Q3, P1-Q4/Q5/Q6, P2-Q11 修复的正确性.

测试用例:
    1. P0-Q1: 前视偏差修复 — current_dd 应基于昨日净值 (排除当日 PnL)
    2. P0-Q3: 总敞口 cap — combined_scaler>1.5 时应触发 exposure_cap_applied
    3. P1-Q4: KillSwitch fail-closed — production 模式 margin_usage=0.0 应触发
    4. P1-Q5: PostMixLayer 抽象 — 信号叠加 + NaN 防御 + LOW_QUALITY 降权
    5. P1-Q6: KillSwitchLevel IntEnum — 字符串/int/枚举解析
    6. P2-Q11: _get_total_margin — 配置化总保证金 (不硬编码 5_000_000)

运行:
    python scripts/_verify_code_quality_fixes.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 添加项目根到 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 副作用: 测试前重置环境变量
os.environ.pop("TRADING_ENV", None)

FAIL_COUNT = 0
PASS_COUNT = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    """断言辅助"""
    global FAIL_COUNT, PASS_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  [PASS] {name}")
    else:
        FAIL_COUNT += 1
        print(f"  [FAIL] {name} — {detail}")


# ============================================================
# 测试 1: P0-Q1 前视偏差修复
# ============================================================
def test_p0_q1_lookahead_bias_fix() -> None:
    """验证 current_dd 基于昨日净值 (排除当日 PnL)"""
    print("\n=== 测试 1: P0-Q1 前视偏差修复 ===")

    from utils.portfolio_optimizer import PortfolioOptimizer

    po = PortfolioOptimizer()

    # 场景: 累计净值 1.0 → 1.10 → 1.05 (回撤 4.5%) → 0.95 (回撤 13.6%)
    # daily_pnl_history 最后一个是"当日" PnL, 不应参与 current_dd 计算
    # 顺序: [p1, p2, p3, p4] = [+0.10, -0.0455, -0.0952, +0.05]
    # 修复前 (含当日): cumulative 含 +0.05, peak=1.10, current=1.10*0.9545*0.9048*1.05 ≈ 1.00
    #   current_dd = (1.10-1.00)/1.10 = 9.1% (假象乐观)
    # 修复后 (排除当日): cumulative 不含 +0.05, peak=1.10, current=1.10*0.9545*0.9048 ≈ 0.95
    #   current_dd = (1.10-0.95)/1.10 = 13.6% (真实)
    target_weights = {"AAA": 0.5, "BBB": 0.5}
    daily_pnl_history = [0.10, -0.0455, -0.0952, 0.05]

    scaled, stats = po.apply_risk_management(target_weights, daily_pnl_history)

    # 验证修复标记
    check(
        "lookahead_bias_fixed 标记存在",
        stats.get("lookahead_bias_fixed") is True,
        f"stats={stats}",
    )
    check(
        "dd_pnl_used = yesterday_only",
        stats.get("dd_pnl_used") == "yesterday_only",
        f"dd_pnl_used={stats.get('dd_pnl_used')}",
    )

    # 验证 current_dd 不含当日 PnL
    # 排除最后一天 +0.05 后: cumulative = [1.0, 1.10, 1.05, 0.95], peak=1.10, current=0.95
    # current_dd = (1.10 - 0.95) / 1.10 = 0.1364
    current_dd = stats.get("current_dd", 0)
    check(
        f"current_dd={current_dd:.4f} 应≈0.1364 (排除当日, 真实回撤)",
        abs(current_dd - 0.1364) < 0.01,
        f"current_dd={current_dd}",
    )

    # 验证修复前 (含当日) 不会出现, 即 current_dd 不应是 0.0909
    check(
        "current_dd 不等于含当日的假象值 0.0909",
        abs(current_dd - 0.0909) > 0.005,
        f"current_dd={current_dd} (若等于 0.0909 说明前视偏差未修复)",
    )


# ============================================================
# 测试 2: P0-Q3 总敞口 cap 保护
# ============================================================
def test_p0_q3_exposure_cap() -> None:
    """验证 combined_scaler>1.5 时触发 exposure_cap_applied"""
    print("\n=== 测试 2: P0-Q3 总敞口 cap 保护 ===")

    from utils.portfolio_optimizer import PortfolioOptimizer

    po = PortfolioOptimizer()

    # 场景: 极低波动率 (但不为 0) + 无回撤 → vol_scaler=2.0, combined_scaler=2.0
    # 原始敞口 1.0 → 缩放后 2.0 → 应被 cap 至 1.5
    target_weights = {"AAA": 0.5, "BBB": 0.5}  # 总敞口 1.0
    # 真实低波动数据 (有微小波动): std≈0.0005, 年化≈0.008 (远低于 target_vol=0.15)
    # 此时 vol_scaler = min(0.15/0.008, 2.0) = 2.0 (cap)
    daily_pnl_history = [0.001, -0.0005, 0.0008, -0.0003, 0.0006, -0.0002, 0.0004, 0.0001]

    scaled, stats = po.apply_risk_management(target_weights, daily_pnl_history)

    # combined_scaler 应接近 2.0 (低波动 + 无回撤)
    combined_scaler = stats.get("combined_scaler", 0)
    check(
        f"combined_scaler={combined_scaler:.4f} 应接近 2.0 (低波动场景, vol_scaler=min(target/realized, cap)=2.0)",
        combined_scaler > 1.5,
        f"combined_scaler={combined_scaler}, realized_vol={stats.get('realized_vol')}, vol_scaler={stats.get('vol_scaler')}",
    )

    # 应触发 exposure cap
    check(
        "exposure_cap_applied = True",
        stats.get("exposure_cap_applied") is True,
        f"exposure_cap_applied={stats.get('exposure_cap_applied')}, scaled_exposure={stats.get('scaled_total_exposure')}",
    )

    # 缩放后总敞口应 ≤ MAX_TOTAL_EXPOSURE (1.5)
    scaled_exposure = stats.get("scaled_total_exposure", 0)
    check(
        f"scaled_total_exposure={scaled_exposure:.4f} 应 ≤ 1.5",
        scaled_exposure <= 1.5 + 1e-6,
        f"scaled_total_exposure={scaled_exposure}",
    )

    # 验证未触发场景: 高波动 + 高回撤 → scaler 应 < 1.0, 不触发 cap
    target_weights_2 = {"AAA": 0.5, "BBB": 0.5}
    # 高波动 + 大幅回撤
    daily_pnl_2 = [0.05, -0.08, 0.04, -0.06, 0.05, -0.07, 0.04, -0.10]
    scaled_2, stats_2 = po.apply_risk_management(target_weights_2, daily_pnl_2)

    check(
        "高波动场景 combined_scaler < 1.0",
        stats_2.get("combined_scaler", 1.0) < 1.0,
        f"combined_scaler={stats_2.get('combined_scaler')}",
    )
    check(
        "高波动场景不触发 exposure_cap",
        stats_2.get("exposure_cap_applied") is False,
        f"exposure_cap_applied={stats_2.get('exposure_cap_applied')}",
    )


# ============================================================
# 测试 3: P1-Q4 KillSwitch fail-closed 完整性
# ============================================================
def test_p1_q4_killswitch_fail_closed() -> None:
    """验证 production 模式下 margin_usage 异常低值触发 fail-closed"""
    print("\n=== 测试 3: P1-Q4 KillSwitch fail-closed 完整性 ===")

    from utils.kill_switch import KillSwitch

    # 场景 A: dev 模式, margin_usage=0.0 → 不应 fail-closed (开发环境容忍)
    os.environ["TRADING_ENV"] = "dev"
    ks_dev = KillSwitch()
    result_dev = ks_dev.check_margin_status(margin_usage=0.0)
    check(
        "dev 模式 margin_usage=0.0 不触发 fail-closed",
        result_dev.get("level", 0) < 3,
        f"level={result_dev.get('level')}",
    )

    # 场景 B: production 模式, margin_usage=0.0 → 应 fail-closed
    os.environ["TRADING_ENV"] = "production"
    ks_prod = KillSwitch()
    result_prod = ks_prod.check_margin_status(margin_usage=0.0)
    check(
        "production 模式 margin_usage=0.0 触发 fail-closed",
        result_prod.get("level") == 3
        and result_prod.get("can_trade") is False,
        f"level={result_prod.get('level')}, can_trade={result_prod.get('can_trade')}",
    )
    check(
        "fail_closed_reason 字段存在",
        "fail_closed_reason" in result_prod,
        f"keys={list(result_prod.keys())}",
    )
    check(
        "reason = SUSPICIOUS_LOW_MARGIN_USAGE",
        result_prod.get("fail_closed_reason") == "SUSPICIOUS_LOW_MARGIN_USAGE",
        f"reason={result_prod.get('fail_closed_reason')}",
    )

    # 场景 C: production 模式, margin_usage="invalid" 类型 → 应 fail-closed
    result_invalid = ks_prod.check_margin_status(margin_usage="invalid")
    check(
        "production 模式 margin_usage='invalid' 类型触发 fail-closed",
        result_invalid.get("level") == 3,
        f"level={result_invalid.get('level')}",
    )

    # 场景 D: production 模式, margin_usage=None → 应 fail-closed (原始路径)
    result_none = ks_prod.check_margin_status(margin_usage=None)
    check(
        "production 模式 margin_usage=None 触发 fail-closed",
        result_none.get("level") == 3,
        f"level={result_none.get('level')}",
    )

    # 场景 E: production 模式, 正常 margin_usage=0.5 → 不应 fail-closed
    result_normal = ks_prod.check_margin_status(margin_usage=0.5)
    check(
        "production 模式正常 margin_usage=0.5 不触发 fail-closed",
        result_normal.get("level", 0) < 3,
        f"level={result_normal.get('level')}",
    )

    os.environ["TRADING_ENV"] = "dev"


# ============================================================
# 测试 4: P1-Q5 PostMixLayer 抽象
# ============================================================
def test_p1_q5_postmix_layer() -> None:
    """验证 PostMixLayer 信号叠加 + NaN 防御 + LOW_QUALITY 降权"""
    print("\n=== 测试 4: P1-Q5 PostMixLayer 抽象 ===")

    from utils.signal_fusion import PostMixLayer

    # 场景 A: 基本叠加 (weight=0.1, signal=1.0, strength=0.5)
    layer = PostMixLayer(name="test", weight=0.1)
    layer.update_signals({"AAA": 1.0})
    new_strength, applied = layer.apply(strength=0.5, symbol="AAA")
    # 期望: 0.5 * 0.9 + 1.0 * 0.1 = 0.55
    check(
        f"基本叠加: 0.5 → {new_strength:.4f} (期望 0.55)",
        abs(new_strength - 0.55) < 1e-6 and applied is True,
        f"new_strength={new_strength}, applied={applied}",
    )

    # 场景 B: NaN 信号 → 应归零, applied=False
    layer_b = PostMixLayer(name="test_b", weight=0.1)
    # 直接注入 NaN (应被 update_signals 过滤掉)
    layer_b.update_signals({"AAA": float("nan")})
    new_b, applied_b = layer_b.apply(strength=0.5, symbol="AAA")
    check(
        "NaN 信号被过滤 (不应用叠加)",
        applied_b is False and abs(new_b - 0.5) < 1e-6,
        f"applied={applied_b}, new_strength={new_b}",
    )

    # 场景 C: LOW_QUALITY 降权 (weight=0.4, decay=0.5, signal=1.0, strength=0.5)
    layer_c = PostMixLayer(name="test_c", weight=0.4, quality_decay=0.5)
    layer_c.update_signals(
        {"AAA": 1.0},
        quality_flags={"AAA": "LOW_QUALITY"},
    )
    new_c, applied_c = layer_c.apply(strength=0.5, symbol="AAA")
    # effective_weight = 0.4 * 0.5 = 0.2
    # 期望: 0.5 * 0.8 + 1.0 * 0.2 = 0.6
    check(
        f"LOW_QUALITY 降权: 0.5 → {new_c:.4f} (期望 0.6)",
        abs(new_c - 0.6) < 1e-6 and applied_c is True,
        f"new_strength={new_c}, applied={applied_c}",
    )

    # 场景 D: weight=0 (禁用层) → 不叠加
    layer_d = PostMixLayer(name="test_d", weight=0.0)
    layer_d.update_signals({"AAA": 1.0})
    new_d, applied_d = layer_d.apply(strength=0.5, symbol="AAA")
    check(
        "weight=0 禁用层不叠加",
        applied_d is False and abs(new_d - 0.5) < 1e-6,
        f"applied={applied_d}, new_strength={new_d}",
    )

    # 场景 E: enabled=False (production 隔离)
    layer_e = PostMixLayer(name="test_e", weight=0.1, enabled=False)
    layer_e.update_signals({"AAA": 1.0})
    new_e, applied_e = layer_e.apply(strength=0.5, symbol="AAA")
    check(
        "enabled=False 不叠加",
        applied_e is False and abs(new_e - 0.5) < 1e-6,
        f"applied={applied_e}, new_strength={new_e}",
    )

    # 场景 F: 结构化格式 {"signal": float, "quality_flag": str}
    layer_f = PostMixLayer(name="test_f", weight=0.4, quality_decay=0.5)
    layer_f.update_signals({
        "AAA": {"signal": 1.0, "quality_flag": "LOW_QUALITY"},
        "BBB": {"signal": 0.8, "quality_flag": "OK"},
    })
    aaa_flag = layer_f.quality_flags.get("AAA")
    bbb_flag = layer_f.quality_flags.get("BBB")
    check(
        "结构化格式解析 quality_flag",
        aaa_flag == "LOW_QUALITY" and bbb_flag == "OK",
        f"AAA={aaa_flag}, BBB={bbb_flag}",
    )

    # 场景 G: SignalFusionEngine 端到端 (向后兼容)
    from utils.signal_fusion import SignalFusionEngine

    engine = SignalFusionEngine()
    engine.inject_pipeline_factor_signals({"AAA": 0.5})
    engine.inject_research_distilled_signals({"AAA": 0.3})
    engine.inject_lgb_enhanced_signals({"AAA": {"signal": 0.4, "quality_flag": "OK"}})

    check(
        "PostMixLayer 注入向后兼容 (_pipeline_factor_signals)",
        engine._pipeline_factor_signals.get("AAA", 0) == 0.5,
        f"value={engine._pipeline_factor_signals.get('AAA')}",
    )
    check(
        "PostMixLayer 注入向后兼容 (_lgb_quality_flags)",
        engine._lgb_quality_flags.get("AAA") == "OK",
        f"value={engine._lgb_quality_flags.get('AAA')}",
    )


# ============================================================
# 测试 5: P1-Q6 KillSwitchLevel IntEnum
# ============================================================
def test_p1_q6_killswitch_level_enum() -> None:
    """验证 KillSwitchLevel IntEnum 解析"""
    print("\n=== 测试 5: P1-Q6 KillSwitchLevel IntEnum ===")

    from utils.risk_guard_integrator import KillSwitchLevel, parse_kill_switch_level

    # 场景 A: 整数解析
    check("int(0) → OK", parse_kill_switch_level(0) == KillSwitchLevel.OK)
    check("int(1) → L1", parse_kill_switch_level(1) == KillSwitchLevel.L1)
    check("int(2) → L2", parse_kill_switch_level(2) == KillSwitchLevel.L2)
    check("int(3) → L3", parse_kill_switch_level(3) == KillSwitchLevel.L3)

    # 场景 B: 字符串解析
    check("str('L0') → OK", parse_kill_switch_level("L0") == KillSwitchLevel.OK)
    check("str('L1') → L1", parse_kill_switch_level("L1") == KillSwitchLevel.L1)
    check("str('L2') → L2", parse_kill_switch_level("L2") == KillSwitchLevel.L2)
    check("str('L3') → L3", parse_kill_switch_level("L3") == KillSwitchLevel.L3)

    # 场景 C: "OK" + 高保证金 → 隐式升级 L3
    check(
        "'OK' + margin_usage=0.80 → L3 (隐式升级)",
        parse_kill_switch_level("OK", margin_usage=0.80) == KillSwitchLevel.L3,
    )
    check(
        "'OK' + margin_usage=0.50 → OK (未升级)",
        parse_kill_switch_level("OK", margin_usage=0.50) == KillSwitchLevel.OK,
    )

    # 场景 D: 无效值保守返回 OK
    check("无效整数 99 → OK", parse_kill_switch_level(99) == KillSwitchLevel.OK)
    check("无效字符串 'XX' → OK", parse_kill_switch_level("XX") == KillSwitchLevel.OK)
    check("None → OK", parse_kill_switch_level(None) == KillSwitchLevel.OK)

    # 场景 E: 已是枚举直接返回
    check(
        "KillSwitchLevel.L2 直接传入 → L2",
        parse_kill_switch_level(KillSwitchLevel.L2) == KillSwitchLevel.L2,
    )

    # 场景 F: IntEnum 可比较 (兼容旧代码 int(level))
    check(
        "int(KillSwitchLevel.L3) == 3 (向后兼容)",
        int(KillSwitchLevel.L3) == 3,
    )
    check(
        "KillSwitchLevel.L3 >= KillSwitchLevel.L2 (枚举比较)",
        KillSwitchLevel.L3 >= KillSwitchLevel.L2,
    )


# ============================================================
# 测试 6: P2-Q11 _get_total_margin 配置化
# ============================================================
def test_p2_q11_total_margin_config() -> None:
    """验证 _get_total_margin 不硬编码 5_000_000"""
    print("\n=== 测试 6: P2-Q11 _get_total_margin 配置化 ===")

    from utils.kill_switch import KillSwitch

    ks = KillSwitch()

    # 场景 A: 默认值 5_000_000 (兼容历史行为)
    os.environ.pop("KILL_SWITCH_TOTAL_MARGIN", None)
    margin = ks._get_total_margin()
    check(
        f"默认总保证金 = {margin} (应为 5_000_000 兼容)",
        margin == 5_000_000,
        f"margin={margin}",
    )

    # 场景 B: 环境变量覆盖 (运维场景: 快速扩容到 1000 万)
    os.environ["KILL_SWITCH_TOTAL_MARGIN"] = "10000000"
    margin_b = ks._get_total_margin()
    check(
        f"环境变量 KILL_SWITCH_TOTAL_MARGIN=10000000 → {margin_b}",
        margin_b == 10_000_000,
        f"margin={margin_b}",
    )

    # 场景 C: 无效环境变量回退到默认值
    os.environ["KILL_SWITCH_TOTAL_MARGIN"] = "invalid_value"
    margin_c = ks._get_total_margin()
    check(
        f"无效 KILL_SWITCH_TOTAL_MARGIN='invalid_value' → 回退默认 {margin_c}",
        margin_c == 5_000_000,
        f"margin={margin_c}",
    )

    # 场景 D: 负值环境变量回退到默认值
    os.environ["KILL_SWITCH_TOTAL_MARGIN"] = "-100"
    margin_d = ks._get_total_margin()
    check(
        f"负值 KILL_SWITCH_TOTAL_MARGIN='-100' → 回退默认 {margin_d}",
        margin_d == 5_000_000,
        f"margin={margin_d}",
    )

    # 清理环境变量
    os.environ.pop("KILL_SWITCH_TOTAL_MARGIN", None)


# ============================================================
# 测试 7: P1-Q7 _estimate_margin_from_positions 拆分
# ============================================================
def test_p1_q7_estimate_margin_refactor() -> None:
    """验证 _estimate_margin_from_positions 拆分后行为一致"""
    print("\n=== 测试 7: P1-Q7 _estimate_margin_from_positions 拆分 ===")

    from utils.kill_switch import KillSwitch

    ks = KillSwitch()

    # 场景 A: _compute_position_margin 纯函数 — 纯股票持仓 (无保证金占用)
    positions_a = {
        "600000": {"amount": 100000, "type": "STOCK"},
        "510300": {"amount": 50000, "type": "ETF"},
    }
    total_val, margin_usage = KillSwitch._compute_position_margin(positions_a)
    check(
        f"纯股票/ETF 持仓: 总市值={total_val}, 保证金占用={margin_usage} (应 0)",
        total_val == 150000 and margin_usage == 0,
        f"total_val={total_val}, margin_usage={margin_usage}",
    )

    # 场景 B: 期货持仓 (12% 保证金)
    positions_b = {
        "IF2406": {"amount": 1000000, "type": "FUTURE"},
    }
    total_val_b, margin_usage_b = KillSwitch._compute_position_margin(positions_b)
    check(
        f"期货持仓: 总市值={total_val_b}, 保证金占用={margin_usage_b} (应 12% × 1000000 = 120000)",
        total_val_b == 1000000 and abs(margin_usage_b - 120000) < 1e-6,
        f"total_val={total_val_b}, margin_usage={margin_usage_b}",
    )

    # 场景 C: 期权持仓 (100% 权利金)
    positions_c = {
        "50ETF2410A2500": {"amount": 50000, "type": "OPTION"},
    }
    total_val_c, margin_usage_c = KillSwitch._compute_position_margin(positions_c)
    check(
        f"期权持仓: 总市值={total_val_c}, 保证金占用={margin_usage_c} (应 100% × 50000 = 50000)",
        total_val_c == 50000 and abs(margin_usage_c - 50000) < 1e-6,
        f"total_val={total_val_c}, margin_usage={margin_usage_c}",
    )

    # 场景 D: 混合持仓 (STOCK + FUTURE + OPTION)
    positions_d = {
        "600000": {"amount": 200000, "type": "STOCK"},
        "IF2406": {"amount": 800000, "type": "FUTURE"},
        "50ETF2410A2500": {"amount": 30000, "type": "OPTION"},
    }
    total_val_d, margin_usage_d = KillSwitch._compute_position_margin(positions_d)
    # 期望: 总市值=200000+800000+30000=1030000, 保证金占用=0+96000+30000=126000
    check(
        f"混合持仓: 总市值={total_val_d} (期望 1030000), 保证金占用={margin_usage_d} (期望 126000)",
        total_val_d == 1030000 and abs(margin_usage_d - 126000) < 1e-6,
        f"total_val={total_val_d}, margin_usage={margin_usage_d}",
    )

    # 场景 E: 异常输入防御 — pos 不是 dict / amount 缺失 / 类型非法
    positions_e = {
        "AAA": "invalid_string",  # 非 dict
        "BBB": {"type": "FUTURE"},  # 缺失 amount
        "CCC": {"amount": "invalid", "type": "FUTURE"},  # amount 类型非法
        "DDD": {"amount": 100000, "type": "FUTURE"},  # 正常
    }
    total_val_e, margin_usage_e = KillSwitch._compute_position_margin(positions_e)
    check(
        f"异常输入防御: 仅 DDD 被统计 (总市值={total_val_e}, 保证金占用={margin_usage_e})",
        total_val_e == 100000 and abs(margin_usage_e - 12000) < 1e-6,
        f"total_val={total_val_e}, margin_usage={margin_usage_e}",
    )

    # 场景 F: 端到端 — _estimate_from_budget_summary OPTIONS_ONLY 应返回 None
    data_options_only = {
        "meta": {"hedge_mode": "OPTIONS_ONLY", "hedge_capital": 2_000_000},
        "hedge_positions": {
            "budget_summary": {"usage_pct": 80.0, "total_put_premium": 100000},
        },
    }
    budget_ratio = ks._estimate_from_budget_summary(data_options_only)
    check(
        "OPTIONS_ONLY 模式 _estimate_from_budget_summary 返回 None (落到真实持仓)",
        budget_ratio is None,
        f"budget_ratio={budget_ratio}",
    )

    # 场景 G: 非纯期权模式 (含期货) + usage_pct 应返回估算值
    data_with_futures = {
        "meta": {"hedge_mode": "FUTURES_AND_OPTIONS", "hedge_capital": 2_000_000},
        "hedge_positions": {
            "budget_summary": {"usage_pct": 65.0},
        },
    }
    budget_ratio_g = ks._estimate_from_budget_summary(data_with_futures)
    check(
        f"含期货模式 usage_pct=65% → 估算 ratio={budget_ratio_g}",
        budget_ratio_g is not None and abs(budget_ratio_g - 0.65) < 1e-6,
        f"budget_ratio={budget_ratio_g}",
    )

    # 场景 H: _estimate_from_real_positions 端到端
    data_real = {
        "meta": {"total_capital": 5_000_000, "hedge_capital": 2_000_000},
        "positions": {
            "IF2406": {"amount": 500000, "type": "FUTURE"},
        },
    }
    real_ratio = ks._estimate_from_real_positions(data_real)
    # 期货保证金 = 500000 * 0.12 = 60000, ratio = 60000 / 2000000 = 0.03
    check(
        f"_estimate_from_real_positions 期货 50万 → ratio={real_ratio:.4f} (期望 0.03)",
        abs(real_ratio - 0.03) < 1e-6,
        f"real_ratio={real_ratio}",
    )


# ============================================================
# 主入口
# ============================================================
def main() -> int:
    print("=" * 70)
    print("代码质量优化验证脚本 (2026-07-26)")
    print("覆盖: P0-Q1, P0-Q3, P1-Q4, P1-Q5, P1-Q6, P2-Q11")
    print("=" * 70)

    try:
        test_p0_q1_lookahead_bias_fix()
    except Exception as e:
        print(f"  [ERROR] P0-Q1 测试异常: {e}")
        import traceback
        traceback.print_exc()

    try:
        test_p0_q3_exposure_cap()
    except Exception as e:
        print(f"  [ERROR] P0-Q3 测试异常: {e}")
        import traceback
        traceback.print_exc()

    try:
        test_p1_q4_killswitch_fail_closed()
    except Exception as e:
        print(f"  [ERROR] P1-Q4 测试异常: {e}")
        import traceback
        traceback.print_exc()

    try:
        test_p1_q5_postmix_layer()
    except Exception as e:
        print(f"  [ERROR] P1-Q5 测试异常: {e}")
        import traceback
        traceback.print_exc()

    try:
        test_p1_q6_killswitch_level_enum()
    except Exception as e:
        print(f"  [ERROR] P1-Q6 测试异常: {e}")
        import traceback
        traceback.print_exc()

    try:
        test_p2_q11_total_margin_config()
    except Exception as e:
        print(f"  [ERROR] P2-Q11 测试异常: {e}")
        import traceback
        traceback.print_exc()

    try:
        test_p1_q7_estimate_margin_refactor()
    except Exception as e:
        print(f"  [ERROR] P1-Q7 测试异常: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 70)
    print(f"测试结果: PASS={PASS_COUNT}, FAIL={FAIL_COUNT}")
    print("=" * 70)

    return 0 if FAIL_COUNT == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
