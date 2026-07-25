# -*- coding: utf-8 -*-
"""
v7.6 期货优先对冲组合年化收益率预测模型
==========================================
基于 portfolio.yaml + trade_plan_20260721.json 的多情景预测
"""

import json
import math
import sys
import io

# 强制 UTF-8 输出
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


def predict_annual_return():
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
    # 2. 权益组合收益预测 (20只标的)
    # ============================================================
    assets = [
        # code, name, weight, category, est_bull_return, est_base_return, est_bear_return
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

    def weighted_return(scenario):
        total = 0
        for _, _, w, _, bull, base, bear in assets:
            if scenario == "bull":
                total += w * bull
            elif scenario == "base":
                total += w * base
            else:
                total += w * bear
        return total

    equity_return_bull = weighted_return("bull")
    equity_return_base = weighted_return("base")
    equity_return_bear = weighted_return("bear")

    # 考虑建仓期：当前在第12/30天，已建仓约40%
    # 建仓期 1 个月 (7/10-8/10)，全年约 11/12 时间满仓
    build_ratio = 0.40  # 截至 7/21 建仓进度
    post_build_return_factor = 0.92  # 全年约 11 个月满仓运行

    # ============================================================
    # 3. 期权对冲收益/成本
    # ============================================================

    # A. Collar 领口 (中际旭创 + 海光信息)
    collar_monthly_cost = -21_000  # -12,000 - 9,000
    # 在bull/base/bear下领口效果不同
    # Bull: 卖Call被行权 → 上行被 cap，但获得 Put 保护
    # Base: 正常到期，净成本 -21,000/month
    # Bear: Put 深度实值，净收益为正

    # B. Put Spread 看跌价差 (510050 + 588080)
    put_spread_monthly_cost = -14_000  # -8,000 - 6,000
    # Bear case: 价差赔付

    # C. Put Ladder 看跌阶梯 (510300)
    put_ladder_monthly_cost = -10_000  # -20,000 per 2 months

    # D. Covered Call (Theta引擎)
    theta_monthly_income = 32_095   # 6 ETF CC from Theta Engine
    portfolio_cc_monthly = 28_000   # 510300 CC only (510050 overlaps)
    covered_call_monthly = theta_monthly_income + portfolio_cc_monthly

    # E. Risk Reversal (510880)
    rr_monthly_cost = -2_000

    # F. VIX Tail — 四层阶梯渐进式预部署 (v8.4修订)
    # Tier2 Put Spread 月化成本 (VIX 18-22, 买-15%OTM + 卖-20%OTM, 15张, 净权利金~3,000/月)
    vix_tail_monthly_cost = -3_000  # 全年持续部署 (不再等VIX>25才trigger)
    vix_tail_annual_cost = -36_000

    total_monthly_options_cost = (
        collar_monthly_cost + put_spread_monthly_cost +
        put_ladder_monthly_cost + rr_monthly_cost + vix_tail_monthly_cost
    )
    total_monthly_options_income = covered_call_monthly

    options_net_monthly = total_monthly_options_income + total_monthly_options_cost
    options_net_annual = options_net_monthly * 12

    # Bear case 下期权赔付估算
    def options_bear_payout(equity_loss_pct):
        """估算大跌幅下的期权赔付"""
        # Collar: Put在-5%以下每1%赔付=名义金的1%*合约数
        collar_payout = (max(0, -equity_loss_pct - 0.05) * 660_000)  # 中际旭创+海光名义
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

    # ============================================================
    # 4. 期货备用
    # ============================================================
    futures_drag_base = -6_000     # 平时小幅拖累
    futures_drag_bull = -12_000     # 牛市拖累更大

    # ============================================================
    # 5. 现金收益
    # ============================================================
    cash_yield = CASH_BUFFER * 0.0155  # 逆回购 1.55%

    # ============================================================
    # 6. 交易成本
    # ============================================================
    # 每日建仓 15万 × 20天/月 × 12个月
    # 佣金: 万2.5 × 买卖双向
    # 期权: 5元/张
    trading_cost_annual = -25_000  # 综合估算

    # ============================================================
    # 7. 三情景分析
    # ============================================================
    print("=" * 70)
    print("v7.6 期权优先对冲组合 — 年化收益率预测")
    print("=" * 70)

    print(f"\n【组合概况】")
    print(f"  总资金: ¥{TOTAL_CAPITAL:,.0f}")
    print(f"  权益仓位: ¥{EQUITY_CAPITAL:,} (60%)")
    print(f"  对冲账户: ¥{HEDGE_CAPITAL:,} (40%)")
    print(f"    ├ 期权预算: ¥{OPTIONS_BUDGET:,} (60%)")
    print(f"    ├ 期货备用: ¥{FUTURES_BUDGET:,} (15%)")
    print(f"    └ 现金缓冲: ¥{CASH_BUFFER:,} (25%)")
    print(f"  权益加权预期收益 (基准): {equity_return_base*100:.1f}%")

    # --- 牛市情景 ---
    bull_equity = EQUITY_CAPITAL * equity_return_bull * post_build_return_factor
    bull_options = options_net_annual
    # 牛市中卖Call被行权 → 上行被Cap的机会成本
    # Collar(OTM+15%): 30万名义金 × (牛市25% - 15%) ≈ 30K机会成本
    # Covered Call(OTM+8%): 4M名义金 × 部分仓位 × (牛市15% - 8%) ≈ 微小
    bull_collar_cost = -30_000  # 上行Cap机会成本 (实际账面仍为正)
    bull_futures = futures_drag_bull
    bull_cash = cash_yield
    bull_trading = trading_cost_annual
    bull_total = bull_equity + bull_options + bull_collar_cost + bull_futures + bull_cash + bull_trading
    bull_return = bull_total / TOTAL_CAPITAL

    # --- 基准情景 ---
    base_equity = EQUITY_CAPITAL * equity_return_base * post_build_return_factor
    base_options = options_net_annual
    base_collar_cost = 0  # Collar 未触发
    base_futures = futures_drag_base
    base_cash = cash_yield
    base_trading = trading_cost_annual
    base_total = base_equity + base_options + base_collar_cost + base_futures + base_cash + base_trading
    base_return = base_total / TOTAL_CAPITAL

    # --- 熊市情景 (市场跌-18%) ---
    bear_market_return = -0.18
    bear_equity = EQUITY_CAPITAL * equity_return_bear * post_build_return_factor
    bear_options_pre = options_net_annual  # 权利金收入已锁定
    bear_options_payout = options_bear_payout(-bear_market_return)
    bear_futures = 0  # 已在payout中包含
    bear_cash = cash_yield
    bear_trading = trading_cost_annual
    bear_total = bear_equity + bear_options_pre + bear_options_payout + bear_futures + bear_cash + bear_trading
    bear_return = bear_total / TOTAL_CAPITAL

    print(f"\n{'='*70}")
    print(f"【情景分析】")
    print(f"{'='*70}")

    scenarios = [
        ("🐂 牛市 (概率 35%)", bull_equity, bull_options, bull_collar_cost, bull_futures, bull_cash, bull_trading, bull_total, bull_return),
        ("📊 基准 (概率 50%)", base_equity, base_options, base_collar_cost, base_futures, base_cash, base_trading, base_total, base_return),
        ("🐻 熊市 (概率 15%)", bear_equity, bear_options_pre, bear_options_payout, bear_futures, bear_cash, bear_trading, bear_total, bear_return),
    ]

    for name, eq, opt, coli, fut, cash, trade, total, ret in scenarios:
        print(f"\n{name}")
        print(f"  权益回报:     ¥{eq:>12,.0f}  ({eq/EQUITY_CAPITAL*100:+.1f}%)")
        print(f"  期权净收益:   ¥{opt:>12,.0f}")
        if coli != 0:
            print(f"  牛/熊额外:    ¥{coli:>12,.0f}")
        print(f"  期货贡献:     ¥{fut:>12,.0f}")
        print(f"  现金收益:     ¥{cash:>11,.0f}")
        print(f"  交易成本:     ¥{trade:>11,.0f}")
        print(f"  {'─'*40}")
        print(f"  合计回报:     ¥{total:>12,.0f}")
        print(f"  年化收益率:   {ret*100:>10.2f}%")

    # 概率加权
    expected_return = 0.35 * bull_return + 0.50 * base_return + 0.15 * bear_return
    expected_vol = math.sqrt(
        0.35 * (bull_return - expected_return)**2 +
        0.50 * (base_return - expected_return)**2 +
        0.15 * (bear_return - expected_return)**2
    )

    # 考虑建仓未完成 → 实际生效约 70%
    phase_factor = 0.70
    adjusted_return = expected_return * phase_factor

    print(f"\n{'='*70}")
    print(f"【概率加权预测】")
    print(f"{'='*70}")
    print(f"  加权年化收益率:   {expected_return*100:.2f}%")
    print(f"  年化波动率:       {expected_vol*100:.2f}%")
    print(f"  夏普比率 (估算):  {(expected_return - 0.025)/expected_vol:.2f}  (无风险利率 2.5%)")
    print(f"  建仓阶段调整后:   {adjusted_return*100:.2f}%  (建仓进度 {build_ratio*100:.0f}%)")

    # ============================================================
    # 8. 收益分解瀑布图数据
    # ============================================================
    print(f"\n{'='*70}")
    print(f"【收益来源分解 (基准情景)】")
    print(f"{'='*70}")

    contributions = [
        ("权益投资 (20标的)",  base_equity,                     base_equity / TOTAL_CAPITAL),
        ("Theta CC 收入",     theta_monthly_income * 12,       theta_monthly_income * 12 / TOTAL_CAPITAL),
        ("Portfolio CC 收入", portfolio_cc_monthly * 12,       portfolio_cc_monthly * 12 / TOTAL_CAPITAL),
        ("Collar 成本",       collar_monthly_cost * 12,        collar_monthly_cost * 12 / TOTAL_CAPITAL),
        ("Put Spread 成本",   put_spread_monthly_cost * 12,    put_spread_monthly_cost * 12 / TOTAL_CAPITAL),
        ("Put Ladder 成本",   put_ladder_monthly_cost * 12,    put_ladder_monthly_cost * 12 / TOTAL_CAPITAL),
        ("Risk Reversal",     rr_monthly_cost * 12,             rr_monthly_cost * 12 / TOTAL_CAPITAL),
        ("VIX Tail 成本",     vix_tail_annual_cost,             vix_tail_annual_cost / TOTAL_CAPITAL),
        ("期货备用拖累",      futures_drag_base,                futures_drag_base / TOTAL_CAPITAL),
        ("现金逆回购收益",    cash_yield,                       cash_yield / TOTAL_CAPITAL),
        ("交易成本",          trading_cost_annual,              trading_cost_annual / TOTAL_CAPITAL),
    ]

    for name, amount, pct in contributions:
        bar_len = int(abs(pct) * 200)
        bar = "█" * min(bar_len, 40)
        sign = "+" if amount >= 0 else ""
        print(f"  {name:<20} {sign}{amount:>10,.0f}  ({sign}{pct*100:.2f}%) {bar}")

    print(f"\n  总计:                  ¥{base_total:>10,.0f}  ({base_return*100:.2f}%)")

    # ============================================================
    # 9. 敏感度分析
    # ============================================================
    print(f"\n{'='*70}")
    print(f"【敏感度分析 — 权益平均收益每变动 ±2% 的影响】")
    print(f"{'='*70}")
    for delta in [-4, -2, 0, 2, 4]:
        adj_equity_return = equity_return_base + delta / 100
        adj_equity = EQUITY_CAPITAL * adj_equity_return * post_build_return_factor
        adj_total = adj_equity + base_options + base_futures + base_cash + base_trading
        adj_return = adj_total / TOTAL_CAPITAL
        marker = "← 接近熊市" if delta == -4 else ("← 基准" if delta == 0 else "← 强牛" if delta == 4 else "")
        print(f"  权益 +{delta:+.0f}% → 组合年化 {adj_return*100:.2f}%  {marker}")

    print(f"\n{'='*70}")
    print(f"【结论】")
    print(f"{'='*70}")
    print(f"  期权优先对冲组合在基准情景下预计年化收益率约 {base_return*100:.1f}%")
    print(f"  概率加权年化收益率约 {expected_return*100:.1f}%  (考虑牛市概率较高)")
    print(f"  建仓期调整后 2026 当年回报约 {adjusted_return*100:.1f}%")
    print()
    print(f"  主要超额收益来源:")
    print(f"    1. AI/半导体等高Beta科技股 (中际旭创/海光/北方华创) 在康波第六轮上升期")
    print(f"    2. Theta Covered Call 月化收入约 ¥{covered_call_monthly:,.0f} (年化 ¥{covered_call_monthly*12:,.0f})")
    print(f"    3. 期权优先模式下期货资金释放的收益机会成本避免")
    print()
    print(f"  主要风险:")
    print(f"    1. 科技股高Beta集中度风险 (占权益35%)")
    print(f"    2. 建仓期中市场大幅下跌 → 权益回撤 + 期权保护尚未完全部署")
    print(f"    3. 波动率骤升时卖出的Covered Call可能被严重套牢")


if __name__ == "__main__":
    predict_annual_return()
