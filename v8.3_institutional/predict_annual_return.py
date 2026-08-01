# -*- coding: utf-8 -*-
"""
v7.6 期货优先对冲组合年化收益率预测模型
==========================================
基于 portfolio.yaml + trade_plan_20260721.json 的多情景预测

v8.4 重构: 拆出 predict_annual_return_struct() 返回结构化数据, 供 ReturnExpectationGate 调用;
          保留 predict_annual_return() 作为 CLI print wrapper (向后兼容)。

v8.4 Phase 1 增强: predict_annual_return_struct() 动态化双路径
  - Flag OFF (默认): _static_predict() 保留 v7.6 静态 20 标的硬编码行为
  - Flag ON: _dynamic_predict() 注入 V9 Shadow 基线 + 真实持仓建仓比例
    · expected_return = V9 backtest annual_return (19.62%) 替代静态 6.85%
    · phase_factor 动态计算: max(build_ratio, 0.92) 替代硬编码 0.70
    · sharpe = V9 backtest sharpe_annual (1.315) 替代静态 0.72
"""

import json
import math
import sys
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

# v8.4: stdout 替换移到 main 块, 避免 import 时副作用导致
#       "I/O operation on closed file" (被 gate_manager 延迟 import 时触发)

logger = logging.getLogger("predict_annual_return")

# 项目根目录 (动态解析, 避免硬编码路径)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_POSITIONS_FILE = _PROJECT_ROOT / "config" / "positions.json"
_SHADOW_CONFIG_FILE = _PROJECT_ROOT / "config" / "shadow_account_config.json"

# Feature Flag 名称
FLAG_NAME = "USE_DYNAMIC_RETURN_PREDICTION"


def _build_assets() -> List[Tuple[str, str, float, str, float, float, float]]:
    """20 只标的清单 (code, name, weight, category, bull, base, bear)"""
    return [
        ("588080", "科创50ETF易方达",   0.05, "科技",     0.16,  0.13, -0.18),
        ("512880", "证券ETF国泰",       0.05, "金融",     0.14,  0.10, -0.16),
        ("510050", "上证50ETF华夏",     0.06, "宽基",     0.09,  0.07, -0.12),
        ("512800", "银行ETF华宝",       0.06, "金融",     0.08,  0.06, -0.10),
        ("515030", "新能源车ETF华夏",   0.05, "新能源",   0.18,  0.12, -0.22),
        ("512760", "半导体ETF国泰",     0.03, "科技",     0.20,  0.14, -0.25),
        ("512170", "医疗ETF华宝",       0.09, "医药",     0.13,  0.09, -0.15),
        ("518880", "黄金ETF华安",       0.05, "资源",     0.08,  0.05,  0.03),
        ("688041", "海光信息",          0.05, "AI芯片",   0.35,  0.22, -0.30),
        ("300308", "中际旭创",          0.05, "AI光模块", 0.35,  0.22, -0.30),
        ("002371", "北方华创",          0.05, "半导体设备", 0.25,  0.18, -0.25),
        ("603019", "中科曙光",          0.03, "超算",     0.22,  0.16, -0.25),
        ("300033", "同花顺",            0.04, "金融科技", 0.18,  0.13, -0.20),
        ("300782", "卓胜微",            0.04, "射频芯片", 0.20,  0.14, -0.22),
        ("688017", "绿的谐波",          0.04, "机器人",   0.22,  0.15, -0.22),
        ("300274", "阳光电源",          0.05, "光伏储能", 0.20,  0.14, -0.22),
        ("000408", "藏格矿业",          0.05, "锂钾资源", 0.15,  0.10, -0.14),
        ("601088", "中国神华",          0.05, "煤炭",     0.10,  0.07, -0.08),
        ("600276", "恒瑞医药",          0.06, "创新药",   0.16,  0.11, -0.15),
        ("600900", "长江电力",          0.05, "水电防御", 0.08,  0.05, -0.03),
    ]


def _weighted_return(assets: List[Tuple], scenario: str) -> float:
    """按权重计算情景加权收益"""
    total = 0.0
    for _, _, w, _, bull, base, bear in assets:
        if scenario == "bull":
            total += w * bull
        elif scenario == "base":
            total += w * base
        else:
            total += w * bear
    return total


def _options_bear_payout(equity_loss_pct: float) -> float:
    """估算大跌幅下的期权赔付"""
    # Collar: Put 在 -5% 以下每 1% 赔付 = 名义金的 1% * 合约数
    collar_payout = max(0, -equity_loss_pct - 0.05) * 660_000
    # Put Spread: max payout = spread_width * notional
    ps_payout_50 = min(max(0, -equity_loss_pct - 0.03), 0.09) * 58_000 * 20
    ps_payout_588 = min(max(0, -equity_loss_pct - 0.05), 0.10) * 42_000 * 15
    put_spread_payout = ps_payout_50 + ps_payout_588
    # Put Ladder
    ladder_payout = (
        min(max(0, -equity_loss_pct - 0.03), 0.05) * 82_000 * 0.5 +
        min(max(0, -equity_loss_pct - 0.08), 0.07) * 82_000 * 0.3 +
        max(0, -equity_loss_pct - 0.15) * 82_000 * 0.2
    )
    # Futures backup
    futures_payout = (-equity_loss_pct - 0.10) * 300_000 if -equity_loss_pct > 0.10 else 0
    return collar_payout + put_spread_payout + ladder_payout + futures_payout


def _load_shadow_benchmark() -> Dict[str, Any]:
    """加载 Shadow 账户回测基准 (V9 Regime-Specific LGB).

    Returns:
        backtest_benchmark 字典, 包含:
        - annual_return: 年化收益率 (0.1962)
        - max_drawdown: 最大回撤 (0.0995)
        - sharpe_annual: 年化夏普 (1.315)
        - dsr_max_pass: DSR 通过次数 (18)
    """
    try:
        if not _SHADOW_CONFIG_FILE.exists():
            logger.warning("Shadow 配置文件不存在: %s", _SHADOW_CONFIG_FILE)
            return {}
        with open(_SHADOW_CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("backtest_benchmark", {}) or {}
    except (json.JSONDecodeError, OSError) as e:
        logger.error("加载 Shadow 基准失败: %s", e)
        return {}


def _compute_real_build_ratio() -> Tuple[float, Dict[str, Any]]:
    """从 positions.json 计算真实建仓比例.

    建仓比例 = sum(shares × est_price) / stock_etf_capital

    Returns:
        (build_ratio, positions_meta) 元组:
        - build_ratio: 0.0~1.0+ 的建仓比例
        - positions_meta: 包含标的数/总市值/目标资金的字典
    """
    try:
        if not _POSITIONS_FILE.exists():
            logger.warning("持仓文件不存在: %s", _POSITIONS_FILE)
            return 0.0, {}
        with open(_POSITIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        positions = data.get("positions", {})
        stock_capital = float(data.get("meta", {}).get("stock_etf_capital", 3_000_000))
        total_market_value = sum(
            float(p.get("shares", 0)) * float(p.get("est_price", 0))
            for p in positions.values()
        )
        build_ratio = total_market_value / stock_capital if stock_capital > 0 else 0.0
        meta = {
            "n_positions": len(positions),
            "total_market_value": total_market_value,
            "stock_etf_capital": stock_capital,
            "build_ratio": build_ratio,
        }
        return build_ratio, meta
    except (json.JSONDecodeError, OSError) as e:
        logger.error("加载持仓文件失败: %s", e)
        return 0.0, {}


def _dynamic_predict() -> Dict[str, Any]:
    """动态年化收益预测 — 基于 V9 Shadow 基线 + 真实持仓.

    核心改进 (对比 _static_predict):
        1. expected_return = V9 backtest annual_return (19.62%) 替代静态 6.85%
        2. phase_factor = max(build_ratio, 0.92) 替代硬编码 0.70
        3. sharpe_estimate = V9 backtest sharpe (1.315) 替代静态 0.72
        4. max_drawdown 来自 V9 实测 (9.95%) 替代估算

    Returns:
        与 _static_predict() 结构一致的字典
    """
    # 1. 加载 V9 基准
    benchmark = _load_shadow_benchmark()
    v9_annual_return = float(benchmark.get("annual_return", 0.0))
    v9_max_drawdown = float(benchmark.get("max_drawdown", 0.0))
    v9_sharpe = float(benchmark.get("sharpe_annual", 0.0))

    if v9_annual_return <= 0:
        logger.warning("V9 基准缺失或异常, 回退到静态预测")
        return _static_predict()

    # 2. 真实建仓比例
    build_ratio, pos_meta = _compute_real_build_ratio()

    # 3. 动态 phase_factor
    #    - build_ratio >= 0.95: phase_factor = 1.0 (已满仓)
    #    - build_ratio < 0.95: phase_factor = max(build_ratio, 0.92)
    #      0.92 = 11/12 个月满仓运行 (即使当前建仓未完成, 年内会完成)
    if build_ratio >= 0.95:
        phase_factor = 1.0
    else:
        phase_factor = max(build_ratio, 0.92)

    # 4. 核心预测值 (来自 V9 回测)
    expected_return = v9_annual_return
    phase_adjusted_return = expected_return * phase_factor

    # 5. 波动率与夏普 (从 V9 基准反推或直接使用)
    risk_free_rate = 0.025
    if v9_sharpe > 0:
        sharpe_estimate = v9_sharpe
        expected_vol = (expected_return - risk_free_rate) / sharpe_estimate
    else:
        # 回退: 用 V9 回撤估算波动率
        expected_vol = max(v9_max_drawdown * 1.5, 0.10)
        sharpe_estimate = (expected_return - risk_free_rate) / expected_vol if expected_vol > 0 else 0.0

    # 6. 三情景分析 (基于 V9 基准 ± 波动率)
    bull_return = expected_return + expected_vol * 0.5
    base_return = expected_return
    bear_return = expected_return - expected_vol * 1.2

    # 7. 资金结构 (复用静态假设, 这些是组合配置不变)
    TOTAL_CAPITAL = 5_000_000
    EQUITY_CAPITAL = 3_000_000
    HEDGE_CAPITAL = 2_000_000
    OPTIONS_BUDGET = 1_200_000
    FUTURES_BUDGET = 300_000
    CASH_BUFFER = 500_000

    # 8. 收益分解 (基准情景)
    base_equity = EQUITY_CAPITAL * expected_return
    contributions = [
        {"name": "权益投资 (V9基线)", "amount": base_equity,
         "pct": base_equity / TOTAL_CAPITAL},
        {"name": "对冲账户净收益", "amount": 0.0, "pct": 0.0},
        {"name": "现金缓冲收益", "amount": 7_750, "pct": 7_750 / TOTAL_CAPITAL},
    ]

    return {
        "scenarios": {
            "bull": {
                "prob": 0.35,
                "return": bull_return,
                "equity": EQUITY_CAPITAL * bull_return,
                "options": 0.0,
                "total": TOTAL_CAPITAL * bull_return,
            },
            "base": {
                "prob": 0.50,
                "return": base_return,
                "equity": base_equity,
                "options": 0.0,
                "total": TOTAL_CAPITAL * base_return,
            },
            "bear": {
                "prob": 0.15,
                "return": bear_return,
                "equity": EQUITY_CAPITAL * bear_return,
                "options_pre": 0.0,
                "options_payout": 0.0,
                "total": TOTAL_CAPITAL * bear_return,
                "market_return": -0.18,
            },
        },
        "expected_return": expected_return,
        "expected_vol": expected_vol,
        "sharpe_estimate": sharpe_estimate,
        "phase_adjusted_return": phase_adjusted_return,
        "base_return": base_return,
        "assumptions": {
            "total_capital": TOTAL_CAPITAL,
            "equity_capital": EQUITY_CAPITAL,
            "hedge_capital": HEDGE_CAPITAL,
            "options_budget": OPTIONS_BUDGET,
            "futures_budget": FUTURES_BUDGET,
            "cash_buffer": CASH_BUFFER,
            "build_ratio": build_ratio,
            "phase_factor": phase_factor,
            "risk_free_rate": risk_free_rate,
            "equity_return_bull": bull_return,
            "equity_return_base": base_return,
            "equity_return_bear": bear_return,
            "v9_annual_return": v9_annual_return,
            "v9_max_drawdown": v9_max_drawdown,
            "v9_sharpe": v9_sharpe,
            "positions_meta": pos_meta,
            "data_source": "shadow_benchmark",
        },
        "contributions": contributions,
        "generated_at": datetime.now().isoformat(),
    }


def predict_annual_return_struct() -> Dict[str, Any]:
    """结构化年化收益预测 (供 ReturnExpectationGate 调用).

    v8.4 Phase 1: 根据 USE_DYNAMIC_RETURN_PREDICTION Flag 分发到静态/动态路径.

    Flag OFF (默认): _static_predict() — v7.6 静态 20 标的硬编码 (expected_return≈6.85%)
    Flag ON: _dynamic_predict() — V9 Shadow 基线 + 真实持仓 (expected_return≈19.62%)

    Returns:
        {
            "scenarios": {...},
            "expected_return": float,
            "expected_vol": float,
            "sharpe_estimate": float,
            "phase_adjusted_return": float,
            "base_return": float,
            "assumptions": {...},
            "contributions": [...],
            "generated_at": str,
        }
    """
    # Feature Flag 检查 (延迟导入避免循环依赖)
    try:
        from utils.infra.feature_flags import is_enabled
        if is_enabled(FLAG_NAME):
            logger.info("USE_DYNAMIC_RETURN_PREDICTION=True, 使用动态预测路径")
            return _dynamic_predict()
    except Exception as e:
        logger.warning("Feature Flag 检查失败, 回退到静态路径: %s", e)

    # 默认: 静态路径 (v7.6 行为, 向后兼容)
    return _static_predict()


def _static_predict() -> Dict[str, Any]:
    """静态年化收益预测 — v7.6 硬编码 20 标的 (向后兼容).

    保留原始行为: expected_return≈6.85%, phase_adjusted_return≈4.79%, sharpe≈0.72
    """
    # ============================================================
    # 1. 组合基本参数
    # ============================================================
    TOTAL_CAPITAL = 5_000_000
    EQUITY_CAPITAL = 3_000_000
    HEDGE_CAPITAL = 2_000_000
    OPTIONS_BUDGET = 1_200_000
    FUTURES_BUDGET = 300_000
    CASH_BUFFER = 500_000

    # ============================================================
    # 2. 权益组合收益预测 (20 只标的)
    # ============================================================
    assets = _build_assets()
    equity_return_bull = _weighted_return(assets, "bull")
    equity_return_base = _weighted_return(assets, "base")
    equity_return_bear = _weighted_return(assets, "bear")

    # 建仓期: 当前在第 12/30 天, 已建仓约 40%
    build_ratio = 0.40
    post_build_return_factor = 0.92  # 全年约 11 个月满仓运行

    # ============================================================
    # 3. 期权对冲收益/成本
    # ============================================================
    collar_monthly_cost = -21_000
    put_spread_monthly_cost = -14_000
    put_ladder_monthly_cost = -10_000
    theta_monthly_income = 32_095
    portfolio_cc_monthly = 28_000
    covered_call_monthly = theta_monthly_income + portfolio_cc_monthly
    rr_monthly_cost = -2_000
    vix_tail_monthly_cost = -3_000
    vix_tail_annual_cost = -36_000

    total_monthly_options_cost = (
        collar_monthly_cost + put_spread_monthly_cost +
        put_ladder_monthly_cost + rr_monthly_cost + vix_tail_monthly_cost
    )
    total_monthly_options_income = covered_call_monthly
    options_net_monthly = total_monthly_options_income + total_monthly_options_cost
    options_net_annual = options_net_monthly * 12

    # ============================================================
    # 4. 期货备用
    # ============================================================
    futures_drag_base = -6_000
    futures_drag_bull = -12_000

    # ============================================================
    # 5. 现金收益
    # ============================================================
    cash_yield = CASH_BUFFER * 0.0155  # 逆回购 1.55%

    # ============================================================
    # 6. 交易成本
    # ============================================================
    trading_cost_annual = -25_000

    # ============================================================
    # 7. 三情景分析
    # ============================================================
    # 牛市
    bull_equity = EQUITY_CAPITAL * equity_return_bull * post_build_return_factor
    bull_options = options_net_annual
    bull_collar_cost = -30_000
    bull_futures = futures_drag_bull
    bull_cash = cash_yield
    bull_trading = trading_cost_annual
    bull_total = bull_equity + bull_options + bull_collar_cost + bull_futures + bull_cash + bull_trading
    bull_return = bull_total / TOTAL_CAPITAL

    # 基准
    base_equity = EQUITY_CAPITAL * equity_return_base * post_build_return_factor
    base_options = options_net_annual
    base_collar_cost = 0
    base_futures = futures_drag_base
    base_cash = cash_yield
    base_trading = trading_cost_annual
    base_total = base_equity + base_options + base_collar_cost + base_futures + base_cash + base_trading
    base_return = base_total / TOTAL_CAPITAL

    # 熊市 (市场跌 -18%)
    bear_market_return = -0.18
    bear_equity = EQUITY_CAPITAL * equity_return_bear * post_build_return_factor
    bear_options_pre = options_net_annual
    bear_options_payout = _options_bear_payout(-bear_market_return)
    bear_futures = 0
    bear_cash = cash_yield
    bear_trading = trading_cost_annual
    bear_total = bear_equity + bear_options_pre + bear_options_payout + bear_futures + bear_cash + bear_trading
    bear_return = bear_total / TOTAL_CAPITAL

    # 概率加权
    expected_return = 0.35 * bull_return + 0.50 * base_return + 0.15 * bear_return
    expected_vol = math.sqrt(
        0.35 * (bull_return - expected_return) ** 2 +
        0.50 * (base_return - expected_return) ** 2 +
        0.15 * (bear_return - expected_return) ** 2
    )
    sharpe_estimate = (expected_return - 0.025) / expected_vol if expected_vol > 0 else 0.0

    # 建仓未完成 → 实际生效约 70%
    phase_factor = 0.70
    adjusted_return = expected_return * phase_factor

    # ============================================================
    # 8. 收益分解 (基准情景)
    # ============================================================
    contributions = [
        {"name": "权益投资 (20标的)",   "amount": base_equity,                     "pct": base_equity / TOTAL_CAPITAL},
        {"name": "Theta CC 收入",       "amount": theta_monthly_income * 12,       "pct": theta_monthly_income * 12 / TOTAL_CAPITAL},
        {"name": "Portfolio CC 收入",   "amount": portfolio_cc_monthly * 12,       "pct": portfolio_cc_monthly * 12 / TOTAL_CAPITAL},
        {"name": "Collar 成本",         "amount": collar_monthly_cost * 12,        "pct": collar_monthly_cost * 12 / TOTAL_CAPITAL},
        {"name": "Put Spread 成本",     "amount": put_spread_monthly_cost * 12,    "pct": put_spread_monthly_cost * 12 / TOTAL_CAPITAL},
        {"name": "Put Ladder 成本",     "amount": put_ladder_monthly_cost * 12,    "pct": put_ladder_monthly_cost * 12 / TOTAL_CAPITAL},
        {"name": "Risk Reversal",       "amount": rr_monthly_cost * 12,            "pct": rr_monthly_cost * 12 / TOTAL_CAPITAL},
        {"name": "VIX Tail 成本",       "amount": vix_tail_annual_cost,            "pct": vix_tail_annual_cost / TOTAL_CAPITAL},
        {"name": "期货备用拖累",        "amount": futures_drag_base,               "pct": futures_drag_base / TOTAL_CAPITAL},
        {"name": "现金逆回购收益",      "amount": cash_yield,                      "pct": cash_yield / TOTAL_CAPITAL},
        {"name": "交易成本",            "amount": trading_cost_annual,             "pct": trading_cost_annual / TOTAL_CAPITAL},
    ]

    return {
        "scenarios": {
            "bull": {
                "prob": 0.35,
                "return": bull_return,
                "equity": bull_equity,
                "options": bull_options,
                "collar_cost": bull_collar_cost,
                "futures": bull_futures,
                "cash": bull_cash,
                "trading": bull_trading,
                "total": bull_total,
            },
            "base": {
                "prob": 0.50,
                "return": base_return,
                "equity": base_equity,
                "options": base_options,
                "collar_cost": base_collar_cost,
                "futures": base_futures,
                "cash": base_cash,
                "trading": base_trading,
                "total": base_total,
            },
            "bear": {
                "prob": 0.15,
                "return": bear_return,
                "equity": bear_equity,
                "options_pre": bear_options_pre,
                "options_payout": bear_options_payout,
                "futures": bear_futures,
                "cash": bear_cash,
                "trading": bear_trading,
                "total": bear_total,
                "market_return": bear_market_return,
            },
        },
        "expected_return": expected_return,
        "expected_vol": expected_vol,
        "sharpe_estimate": sharpe_estimate,
        "phase_adjusted_return": adjusted_return,
        "base_return": base_return,
        "assumptions": {
            "total_capital": TOTAL_CAPITAL,
            "equity_capital": EQUITY_CAPITAL,
            "hedge_capital": HEDGE_CAPITAL,
            "options_budget": OPTIONS_BUDGET,
            "futures_budget": FUTURES_BUDGET,
            "cash_buffer": CASH_BUFFER,
            "build_ratio": build_ratio,
            "post_build_return_factor": post_build_return_factor,
            "phase_factor": phase_factor,
            "risk_free_rate": 0.025,
            "equity_return_bull": equity_return_bull,
            "equity_return_base": equity_return_base,
            "equity_return_bear": equity_return_bear,
        },
        "contributions": contributions,
        "generated_at": datetime.now().isoformat(),
    }


def _print_prediction(result: Dict[str, Any]) -> None:
    """打印结构化预测结果 (向后兼容原 CLI 输出)"""
    TOTAL_CAPITAL = result["assumptions"]["total_capital"]
    EQUITY_CAPITAL = result["assumptions"]["equity_capital"]
    OPTIONS_BUDGET = result["assumptions"]["options_budget"]
    FUTURES_BUDGET = result["assumptions"]["futures_budget"]
    CASH_BUFFER = result["assumptions"]["cash_buffer"]
    build_ratio = result["assumptions"]["build_ratio"]

    print("=" * 70)
    print("v7.6 期权优先对冲组合 — 年化收益率预测")
    print("=" * 70)

    print("\n【组合概况】")
    print(f"  总资金: ¥{TOTAL_CAPITAL:,.0f}")
    print(f"  权益仓位: ¥{EQUITY_CAPITAL:,} (60%)")
    print(f"  对冲账户: ¥{result['assumptions']['hedge_capital']:,} (40%)")
    print(f"    ├ 期权预算: ¥{OPTIONS_BUDGET:,} (60%)")
    print(f"    ├ 期货备用: ¥{FUTURES_BUDGET:,} (15%)")
    print(f"    └ 现金缓冲: ¥{CASH_BUFFER:,} (25%)")
    print(f"  权益加权预期收益 (基准): {result['assumptions']['equity_return_base']*100:.1f}%")

    print(f"\n{'='*70}")
    print("【情景分析】")
    print(f"{'='*70}")

    scenarios = [
        ("🐂 牛市 (概率 35%)", result["scenarios"]["bull"], "collar_cost"),
        ("📊 基准 (概率 50%)", result["scenarios"]["base"], "collar_cost"),
        ("🐻 熊市 (概率 15%)", result["scenarios"]["bear"], "options_payout"),
    ]

    for name, sc, extra_key in scenarios:
        print(f"\n{name}")
        print(f"  权益回报:     ¥{sc['equity']:>12,.0f}  ({sc['equity']/EQUITY_CAPITAL*100:+.1f}%)")
        opt_field = "options" if "options" in sc else "options_pre"
        print(f"  期权净收益:   ¥{sc[opt_field]:>12,.0f}")
        extra = sc.get(extra_key, 0)
        if extra != 0:
            print(f"  牛/熊额外:    ¥{extra:>12,.0f}")
        print(f"  期货贡献:     ¥{sc['futures']:>12,.0f}")
        print(f"  现金收益:     ¥{sc['cash']:>11,.0f}")
        print(f"  交易成本:     ¥{sc['trading']:>11,.0f}")
        print(f"  {'─'*40}")
        print(f"  合计回报:     ¥{sc['total']:>12,.0f}")
        print(f"  年化收益率:   {sc['return']*100:>10.2f}%")

    print(f"\n{'='*70}")
    print("【概率加权预测】")
    print(f"{'='*70}")
    print(f"  加权年化收益率:   {result['expected_return']*100:.2f}%")
    print(f"  年化波动率:       {result['expected_vol']*100:.2f}%")
    print(f"  夏普比率 (估算):  {result['sharpe_estimate']:.2f}  (无风险利率 2.5%)")
    print(f"  建仓阶段调整后:   {result['phase_adjusted_return']*100:.2f}%  (建仓进度 {build_ratio*100:.0f}%)")

    print(f"\n{'='*70}")
    print("【收益来源分解 (基准情景)】")
    print(f"{'='*70}")
    for c in result["contributions"]:
        bar_len = int(abs(c["pct"]) * 200)
        bar = "█" * min(bar_len, 40)
        sign = "+" if c["amount"] >= 0 else ""
        print(f"  {c['name']:<20} {sign}{c['amount']:>10,.0f}  ({sign}{c['pct']*100:.2f}%) {bar}")

    base_total = result["scenarios"]["base"]["total"]
    base_return = result["base_return"]
    print(f"\n  总计:                  ¥{base_total:>10,.0f}  ({base_return*100:.2f}%)")

    print(f"\n{'='*70}")
    print("【结论】")
    print(f"{'='*70}")
    print(f"  期权优先对冲组合在基准情景下预计年化收益率约 {base_return*100:.1f}%")
    print(f"  概率加权年化收益率约 {result['expected_return']*100:.1f}%  (考虑牛市概率较高)")
    print(f"  建仓期调整后 2026 当年回报约 {result['phase_adjusted_return']*100:.1f}%")


def predict_annual_return():
    """CLI 入口 (向后兼容: 调用 struct 后打印)"""
    # v8.4: stdout 替换仅在 CLI 运行时执行, 避免 import 副作用
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    result = predict_annual_return_struct()
    _print_prediction(result)


if __name__ == "__main__":
    predict_annual_return()
