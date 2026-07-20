# -*- coding: utf-8 -*-
"""
v7.7 对冲基金视角系统 - 2030年化收益率预测报告生成器
基于v4预测数据，叠加 Theta/Gamma/KillSwitch/Liquidation 四大模块调整
"""
import json
import math
import os
from datetime import datetime
from pathlib import Path

# ============== 基础配置 ==============
BASE_DIR = Path(r"e:\各种PY程序\28-终极量化交易系统7.1")
V4_JSON = BASE_DIR / "portfolio_return_projection_2030.json"
V77_JSON = BASE_DIR / "portfolio_return_projection_2030_v77.json"
REPORTS_DIR = BASE_DIR / "reports"
V77_MD = REPORTS_DIR / "portfolio_return_projection_2030_v77.md"

REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ============== v7.7 模块参数 ==============
V77_MODULES = {
    "theta_engine": {
        "name": "Theta引擎 (Covered Call 月度)",
        "monthly_premium": 32104,
        "annual_premium": 32104 * 12,
        "collateral": 4000000,
        "collateral_overlap": True,
        "etf_positions": 6,
        "gross_yield_on_principal": 0.0771,   # 38.5万 / 500万
        "opportunity_cost": 0.02,
        "net_contribution_annual": 0.057,     # +5.7%/年
        "market_correlation": "弱相关",
        "note": "权利金收入与市场涨跌弱相关, 提供稳定增益"
    },
    "gamma_engine": {
        "name": "Gamma引擎 (尾部危机监控)",
        "triggers": ["MA60跌破", "IV分位高位"],
        "budget": 1000000,
        "protection_target": "黑天鹅情景",
        "protection_pct_cumulative": 0.172,   # 累计保护17.2% (从-62.2%到-45%)
        "note": "MA60+IV分位双重触发, 提前买入认沽对冲尾部风险"
    },
    "kill_switch": {
        "name": "KillSwitch 三级熔断",
        "levels": {
            "L1": {"margin_usage": 0.50, "action": "停止开仓"},
            "L2": {"margin_usage": 0.75, "action": "强平深虚值+变现红利ETF"},
            "L3": {"margin_usage": 0.90, "action": "全面防御"}
        },
        "bear_protection_cumulative": 0.057,  # 悲观情景累计改善5.7%
        "bear_target_cumulative": -0.08,       # 悲观情景目标累计-8%
        "note": "L1触发减少悲观回撤, L2/L3限制黑天鹅下行"
    },
    "liquidation": {
        "name": "LiquidationScheduler 2030清仓协议",
        "phases": {
            "phase_1": {"period": "2030-Q3", "action": "TWAP变现开始"},
            "phase_2": {"period": "2030-11", "action": "加速清仓"},
            "phase_3": {"period": "2030-12", "action": "完成清仓"}
        },
        "impact_months": 5,
        "impact_cumulative_range": [-0.05, -0.03],  # 清仓冲击成本累计 -3%~-5%
        "impact_cumulative_neutral": -0.04,
        "note": "TWAP分批变现, 降低冲击成本, 但仍有净损失约4%累计"
    }
}

# ============== 投资期调整 ==============
INVESTMENT_HORIZON_YEARS = 4.42   # 2026-08-01 → 2030-12-31
INITIAL_CAPITAL = 5000000

# ============== 计算辅助函数 ==============
def compound_cumulative(annualized_rate, years):
    """复利计算累计收益"""
    return (1 + annualized_rate) ** years - 1

def annualized_from_cumulative(cumulative, years):
    """从累计收益反推年化"""
    return (1 + cumulative) ** (1 / years) - 1

# ============== 各情景调整计算 ==============
# v4 基础数据
v4_scenarios = {
    "bull":       {"v4_annualized": 0.2706, "v4_cumulative": 1.845,  "v4_final": 14225400, "prob": 0.20},
    "base":       {"v4_annualized": 0.1613, "v4_cumulative": 1.018,  "v4_final": 10090000, "prob": 0.45},
    "bear":       {"v4_annualized": -0.0327,"v4_cumulative": -0.137, "v4_final": 4315000,  "prob": 0.25},
    "black_swan": {"v4_annualized": -0.2023,"v4_cumulative": -0.622, "v4_final": 1890000,  "prob": 0.10},
}

THETA_NET_ANNUAL = 0.057       # +5.7%/年
HEDGE_COST_ANNUAL = -0.020      # -2.0%/年
BULL_PARTICIPATION_CUT = 0.05  # 牛市参与度相对降低5%
LIQUIDATION_CUMULATIVE = -0.04 # 清仓冲击成本累计-4%

# 黑天鹅情景清仓冲击成本较小(已损失, 边际影响小)
LIQUIDATION_CUMULATIVE_BLACKSWAN = -0.01

v77_scenarios = {}

for key, v4 in v4_scenarios.items():
    scenario = {"v4": v4.copy()}
    adjustments = []

    # 1. Theta 净贡献 (所有情景)
    theta_annual = THETA_NET_ANNUAL
    adjustments.append({"item": "Theta净贡献", "annual": theta_annual, "note": "权利金收入扣除担保物机会成本"})

    # 2. 对冲成本 (所有情景)
    hedge_annual = HEDGE_COST_ANNUAL
    adjustments.append({"item": "对冲成本(IF/IM空头+认沽)", "annual": hedge_annual, "note": "侵蚀所有情景"})

    # 3. 情景特定调整
    if key in ("bull", "base"):
        # 牛市参与度降低 5% (相对)
        participation_adj_annual = v4["v4_annualized"] * (-BULL_PARTICIPATION_CUT)
        adjustments.append({
            "item": "Theta担保物牛市参与度降低",
            "annual": participation_adj_annual,
            "note": f"v4年化 {v4['v4_annualized']*100:.2f}% × -5% = {participation_adj_annual*100:.3f}%"
        })
        killswitch_annual = 0.0
        gamma_annual = 0.0
        liquidation_cumulative = LIQUIDATION_CUMULATIVE
    elif key == "bear":
        # KillSwitch L1 改善 (累计5.7%, 从-13.7%到-8%)
        killswitch_cumulative_improvement = 0.057
        killswitch_annual = annualized_from_cumulative(killswitch_cumulative_improvement, INVESTMENT_HORIZON_YEARS)
        adjustments.append({
            "item": "KillSwitch L1触发减少回撤",
            "annual": killswitch_annual,
            "cumulative_improvement": killswitch_cumulative_improvement,
            "note": f"累计改善 {killswitch_cumulative_improvement*100:.1f}% (从-13.7%到-8%)"
        })
        gamma_annual = 0.0
        liquidation_cumulative = LIQUIDATION_CUMULATIVE
    elif key == "black_swan":
        # Gamma + KillSwitch 改善 (累计17.2%, 从-62.2%到-45%)
        gamma_cumulative_improvement = 0.172
        gamma_annual = annualized_from_cumulative(gamma_cumulative_improvement, INVESTMENT_HORIZON_YEARS)
        adjustments.append({
            "item": "Gamma+KillSwitch尾部保护",
            "annual": gamma_annual,
            "cumulative_improvement": gamma_cumulative_improvement,
            "note": f"累计改善 {gamma_cumulative_improvement*100:.1f}% (从-62.2%到-45%)"
        })
        killswitch_annual = 0.0
        liquidation_cumulative = LIQUIDATION_CUMULATIVE_BLACKSWAN

    # 4. 计算调整后年化收益率
    v4_annualized = v4["v4_annualized"]
    bull_participation_adj = (v4_annualized * (-BULL_PARTICIPATION_CUT)) if key in ("bull", "base") else 0.0

    adjusted_annualized = (
        v4_annualized
        + bull_participation_adj
        + THETA_NET_ANNUAL
        + HEDGE_COST_ANNUAL
        + killswitch_annual
        + gamma_annual
    )

    # 5. 清仓冲击成本 (累计)
    adjustments.append({
        "item": "Liquidation TWAP清仓冲击",
        "cumulative": liquidation_cumulative,
        "note": "2030-Q3~12 分批变现, 累计净冲击成本"
    })

    # 6. 计算调整后累计收益
    # 先计算复利累计(基于调整后年化)
    cumulative_from_annual = compound_cumulative(adjusted_annualized, INVESTMENT_HORIZON_YEARS)
    # 再叠加清仓冲击成本
    adjusted_cumulative = cumulative_from_annual + liquidation_cumulative

    # 7. 计算最终金额
    final_amount = INITIAL_CAPITAL * (1 + adjusted_cumulative)
    total_profit = final_amount - INITIAL_CAPITAL

    # 8. 反推等效年化(考虑清仓冲击)
    equivalent_annualized = annualized_from_cumulative(adjusted_cumulative, INVESTMENT_HORIZON_YEARS)

    scenario.update({
        "v4_annualized_pct": round(v4_annualized * 100, 2),
        "v4_cumulative_pct": round(v4["v4_cumulative"] * 100, 2),
        "v4_final_amount": v4["v4_final"],
        "adjustments": adjustments,
        "bull_participation_adj_annual": round(bull_participation_adj * 100, 4),
        "theta_net_annual": round(THETA_NET_ANNUAL * 100, 2),
        "hedge_cost_annual": round(HEDGE_COST_ANNUAL * 100, 2),
        "killswitch_annual": round(killswitch_annual * 100, 4),
        "gamma_annual": round(gamma_annual * 100, 4),
        "adjusted_annualized_pct": round(adjusted_annualized * 100, 2),
        "cumulative_from_annual_pct": round(cumulative_from_annual * 100, 2),
        "liquidation_cumulative_pct": round(liquidation_cumulative * 100, 2),
        "adjusted_cumulative_pct": round(adjusted_cumulative * 100, 2),
        "equivalent_annualized_pct": round(equivalent_annualized * 100, 2),
        "final_amount": round(final_amount, 0),
        "total_profit": round(total_profit, 0),
        "probability": v4["prob"],
    })
    v77_scenarios[key] = scenario

# ============== 概率加权期望 ==============
expected_annualized = sum(s["adjusted_annualized_pct"] * s["probability"] for s in v77_scenarios.values()) / 100
# 用等效年化(考虑清仓)计算期望
expected_equivalent_annualized = sum(s["equivalent_annualized_pct"] * s["probability"] for s in v77_scenarios.values()) / 100
expected_cumulative = sum(s["adjusted_cumulative_pct"] * s["probability"] for s in v77_scenarios.values()) / 100
expected_final = sum(s["final_amount"] * s["probability"] for s in v77_scenarios.values())
expected_profit = expected_final - INITIAL_CAPITAL

# v4 期望
v4_expected_annualized = 0.1184
v4_expected_final = 8430000

# ============== 构造完整 JSON ==============
v77_json = {
    "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    "version": "v7.7_对冲基金视角_Theta_Gamma_KillSwitch_Liquidation",
    "base_version": "v4_2030_康波繁荣期外推",
    "investment_horizon": "2026-08-01 → 2030-12-31",
    "horizon_years": INVESTMENT_HORIZON_YEARS,
    "initial_capital": INITIAL_CAPITAL,
    "build_complete_date": "2026-07-31",
    "v4_reference": {
        "v4_horizon_years": 4.48,
        "v4_expected_annualized_pct": 11.84,
        "v4_expected_final_amount": 8430000,
        "v4_json": "portfolio_return_projection_2030.json"
    },
    "v77_modules": V77_MODULES,
    "v77_adjustments_summary": {
        "theta_net_annual_pct": 5.70,
        "hedge_cost_annual_pct": -2.00,
        "bull_participation_cut_pct": -5.00,
        "bear_killswitch_cumulative_improvement_pct": 5.70,
        "blackswan_gamma_killswitch_cumulative_improvement_pct": 17.20,
        "liquidation_cumulative_impact_pct": -4.00,
        "liquidation_cumulative_blackswan_pct": -1.00
    },
    "scenarios": {
        "bull": {
            "label": "乐观 (牛市+康波繁荣超预期)",
            "probability": v77_scenarios["bull"]["probability"],
            "v4_annualized_pct": v77_scenarios["bull"]["v4_annualized_pct"],
            "v4_cumulative_pct": v77_scenarios["bull"]["v4_cumulative_pct"],
            "v4_final_amount": v77_scenarios["bull"]["v4_final_amount"],
            "adjustments": v77_scenarios["bull"]["adjustments"],
            "adjusted_annualized_pct": v77_scenarios["bull"]["adjusted_annualized_pct"],
            "cumulative_from_annual_pct": v77_scenarios["bull"]["cumulative_from_annual_pct"],
            "liquidation_cumulative_pct": v77_scenarios["bull"]["liquidation_cumulative_pct"],
            "adjusted_cumulative_pct": v77_scenarios["bull"]["adjusted_cumulative_pct"],
            "equivalent_annualized_pct": v77_scenarios["bull"]["equivalent_annualized_pct"],
            "final_amount": v77_scenarios["bull"]["final_amount"],
            "total_profit": v77_scenarios["bull"]["total_profit"],
            "key_drivers": [
                "康波第六轮繁荣期2030年全面开启",
                "AI算力渗透率突破30%, 中际旭创/海光信息业绩持续超预期",
                "Theta引擎月度权利金稳定提供9.63%年化收入(毛)",
                "对冲头寸限制部分上行(牛市参与度降低5%)",
                "Liquidation TWAP清仓分摊冲击成本"
            ]
        },
        "base": {
            "label": "基准 (中性, 康波按时切换)",
            "probability": v77_scenarios["base"]["probability"],
            "v4_annualized_pct": v77_scenarios["base"]["v4_annualized_pct"],
            "v4_cumulative_pct": v77_scenarios["base"]["v4_cumulative_pct"],
            "v4_final_amount": v77_scenarios["base"]["v4_final_amount"],
            "adjustments": v77_scenarios["base"]["adjustments"],
            "adjusted_annualized_pct": v77_scenarios["base"]["adjusted_annualized_pct"],
            "cumulative_from_annual_pct": v77_scenarios["base"]["cumulative_from_annual_pct"],
            "liquidation_cumulative_pct": v77_scenarios["base"]["liquidation_cumulative_pct"],
            "adjusted_cumulative_pct": v77_scenarios["base"]["adjusted_cumulative_pct"],
            "equivalent_annualized_pct": v77_scenarios["base"]["equivalent_annualized_pct"],
            "final_amount": v77_scenarios["base"]["final_amount"],
            "total_profit": v77_scenarios["base"]["total_profit"],
            "key_drivers": [
                "康波复苏→繁荣按时切换(2028-2030)",
                "Theta引擎权利金稳定贡献+5.7%/年(净)",
                "对冲成本-2.0%/年侵蚀收益",
                "KillSwitch L1未触发, 正常持有",
                "Liquidation清仓2030-Q3启动"
            ]
        },
        "bear": {
            "label": "悲观 (熊市+康波延迟)",
            "probability": v77_scenarios["bear"]["probability"],
            "v4_annualized_pct": v77_scenarios["bear"]["v4_annualized_pct"],
            "v4_cumulative_pct": v77_scenarios["bear"]["v4_cumulative_pct"],
            "v4_final_amount": v77_scenarios["bear"]["v4_final_amount"],
            "adjustments": v77_scenarios["bear"]["adjustments"],
            "adjusted_annualized_pct": v77_scenarios["bear"]["adjusted_annualized_pct"],
            "cumulative_from_annual_pct": v77_scenarios["bear"]["cumulative_from_annual_pct"],
            "liquidation_cumulative_pct": v77_scenarios["bear"]["liquidation_cumulative_pct"],
            "adjusted_cumulative_pct": v77_scenarios["bear"]["adjusted_cumulative_pct"],
            "equivalent_annualized_pct": v77_scenarios["bear"]["equivalent_annualized_pct"],
            "final_amount": v77_scenarios["bear"]["final_amount"],
            "total_profit": v77_scenarios["bear"]["total_profit"],
            "key_drivers": [
                "康波繁荣期延迟到2032+ (周金涛理论分歧)",
                "KillSwitch L1触发(保证金占用50%), 停止开仓减少回撤",
                "Theta权利金仍可收取(弱相关), 缓冲下跌",
                "对冲头寸部分对冲下跌",
                "Liquidation清仓可能提前触发"
            ]
        },
        "black_swan": {
            "label": "黑天鹅 (极端风险)",
            "probability": v77_scenarios["black_swan"]["probability"],
            "v4_annualized_pct": v77_scenarios["black_swan"]["v4_annualized_pct"],
            "v4_cumulative_pct": v77_scenarios["black_swan"]["v4_cumulative_pct"],
            "v4_final_amount": v77_scenarios["black_swan"]["v4_final_amount"],
            "adjustments": v77_scenarios["black_swan"]["adjustments"],
            "adjusted_annualized_pct": v77_scenarios["black_swan"]["adjusted_annualized_pct"],
            "cumulative_from_annual_pct": v77_scenarios["black_swan"]["cumulative_from_annual_pct"],
            "liquidation_cumulative_pct": v77_scenarios["black_swan"]["liquidation_cumulative_pct"],
            "adjusted_cumulative_pct": v77_scenarios["black_swan"]["adjusted_cumulative_pct"],
            "equivalent_annualized_pct": v77_scenarios["black_swan"]["equivalent_annualized_pct"],
            "final_amount": v77_scenarios["black_swan"]["final_amount"],
            "total_profit": v77_scenarios["black_swan"]["total_profit"],
            "key_drivers": [
                "台海冲突升级或全球性金融危机",
                "Gamma引擎MA60+IV分位双重触发, 提前买入认沽",
                "KillSwitch L2/L3触发, 强平深虚值+变现红利ETF",
                "尾部保护将累计回撤从-62.2%改善到约-45%",
                "Liquidation提前启动, 冲击成本边际影响小"
            ]
        }
    },
    "expected": {
        "expected_annualized_pct": round(expected_annualized * 100, 2),
        "expected_equivalent_annualized_pct": round(expected_equivalent_annualized * 100, 2),
        "expected_cumulative_pct": round(expected_cumulative * 100, 2),
        "expected_final_amount": round(expected_final, 0),
        "expected_profit": round(expected_profit, 0),
        "calculation_note": (
            f"概率加权(等效年化, 含清仓冲击): "
            f"0.20×{v77_scenarios['bull']['equivalent_annualized_pct']:.2f}% + "
            f"0.45×{v77_scenarios['base']['equivalent_annualized_pct']:.2f}% + "
            f"0.25×{v77_scenarios['bear']['equivalent_annualized_pct']:.2f}% + "
            f"0.10×{v77_scenarios['black_swan']['equivalent_annualized_pct']:.2f}% = "
            f"{expected_equivalent_annualized*100:.2f}%"
        )
    },
    "style_rotation_2030": {
        "高端制造_含算力": {
            "weight_current": 0.40,
            "weight_2030": 0.40,
            "holdings": "中际旭创/海光信息/北方华创/中芯国际/宁德时代/徐工机械",
            "trend": "AI算力主线维持高景气, 2028+估值切换",
            "annualized_2026_2030": 18.0,
            "theta_applicable": "ETF部分可做Covered Call",
            "note": "v7.7 Theta引擎覆盖6个ETF头寸"
        },
        "顺周期": {
            "weight_current": 0.20,
            "weight_2030": 0.20,
            "holdings": "中国神华/南山铝业/宝钢股份",
            "trend": "康波繁荣期主升浪受益",
            "annualized_2026_2030": 12.0,
            "note": "2030+资源股主升浪启动"
        },
        "资源": {
            "weight_current": 0.20,
            "weight_2030": 0.20,
            "holdings": "华安黄金ETF/盐湖股份",
            "trend": "★康波繁荣期核心受益",
            "annualized_2026_2030": 22.0,
            "note": "黄金ETF+盐湖股份, 2030年主升浪"
        },
        "防御": {
            "weight_current": 0.20,
            "weight_2030": 0.20,
            "holdings": "长江电力/恒瑞医药/药明康德/科伦药业",
            "trend": "稳健配置, KillSwitch变现池",
            "annualized_2026_2030": 8.0,
            "note": "L2触发时红利ETF作为变现池"
        }
    },
    "key_risks": {
        "concentration_risk": "高端制造(含算力)占比40%, 2027-2028估值回调风险",
        "theta_risk": "Covered Call在牛市损失上行, 月度权利金不稳定",
        "gamma_risk": "Gamma引擎预算100万, 黑天鹅时对冲容量可能不足",
        "killswitch_risk": "L2/L3强平可能踩踏, 变现红利ETF可能损失股息",
        "liquidation_risk": "TWAP清仓遇到2030年市场下行会放大损失",
        "hedging_cost": "对冲成本年化-2.0%, 4.42年累计侵蚀约-8.5%",
        "kondratiev_timing_risk": "★康波繁荣期开启时点存在1-3年分歧, 是最大不确定性"
    },
    "comparison_v4_vs_v77": {
        "v4": {
            "expected_annualized_pct": 11.84,
            "expected_final_amount": 8430000,
            "horizon_years": 4.48,
            "bull_annualized_pct": 27.06,
            "base_annualized_pct": 16.13,
            "bear_annualized_pct": -3.27,
            "blackswan_cumulative_pct": -62.2
        },
        "v77": {
            "expected_annualized_pct": round(expected_equivalent_annualized * 100, 2),
            "expected_final_amount": round(expected_final, 0),
            "horizon_years": INVESTMENT_HORIZON_YEARS,
            "bull_annualized_pct": v77_scenarios["bull"]["equivalent_annualized_pct"],
            "base_annualized_pct": v77_scenarios["base"]["equivalent_annualized_pct"],
            "bear_annualized_pct": v77_scenarios["bear"]["equivalent_annualized_pct"],
            "blackswan_cumulative_pct": v77_scenarios["black_swan"]["adjusted_cumulative_pct"]
        },
        "delta_expected_annualized_pct": round(expected_equivalent_annualized * 100 - 11.84, 2),
        "delta_expected_final_amount": round(expected_final - 8430000, 0),
        "improvement_reason": "Theta引擎净贡献+5.7%/年, 部分被对冲成本-2.0%/年和清仓冲击成本抵消, 黑天鹅/悲观情景尾部保护显著改善"
    },
    "conclusion": {
        "summary": (
            f"v7.7对冲基金视角系统(Theta+Gamma+KillSwitch+Liquidation)叠加v4康波繁荣期外推, "
            f"预测2026-08至2030-12期间(4.42年)组合期望等效年化收益率约{expected_equivalent_annualized*100:.2f}%, "
            f"累计收益约{expected_cumulative*100:.2f}%, 最终金额约{expected_final/10000:.0f}万(初始500万)。"
        ),
        "key_insight": (
            "★Theta引擎为所有情景提供稳定的+5.7%/年净增益, 是v7.7最大的alpha来源; "
            "Gamma+KillSwitch将黑天鹅情景从-62.2%改善到约-45%, 显著降低尾部风险; "
            "对冲成本-2.0%/年和清仓冲击成本-4%累计是主要侵蚀项。"
        ),
        "rebalance_suggestion": (
            "建议2028年开始将资源股权重从20%观察提升, 高端制造保持40%; "
            "Theta引擎月度 Covered Call 严格执行, 权利金再投资; "
            "KillSwitch L1触发立即停止开仓, L2/L3按协议执行强平。"
        )
    }
}

# ============== 保存 JSON ==============
with open(V77_JSON, "w", encoding="utf-8") as f:
    json.dump(v77_json, f, ensure_ascii=False, indent=2)
print(f"[OK] JSON 已保存: {V77_JSON}")

# ============== 生成 Markdown 报告 ==============
bull = v77_scenarios["bull"]
base = v77_scenarios["base"]
bear = v77_scenarios["bear"]
bs = v77_scenarios["black_swan"]

md_content = f"""# v7.7 对冲基金视角系统 - 2030年化收益率预测报告

> **版本**: v7.7 (Theta + Gamma + KillSwitch + Liquidation 四模块叠加)
> **基础版本**: v4 康波繁荣期外推
> **生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
> **工作目录**: `e:\\各种PY程序\\28-终极量化交易系统7.1`

---

## 一、执行摘要

| 指标 | v4 基准 | v7.7 调整后 | 变动 |
|------|---------|-------------|------|
| **期望年化收益率** | 11.84% | **{expected_equivalent_annualized*100:.2f}%** | **{expected_equivalent_annualized*100 - 11.84:+.2f}%** |
| **期望累计收益** | 68.60% | **{expected_cumulative*100:.2f}%** | **{expected_cumulative*100 - 68.60:+.2f}%** |
| **期望最终金额** | 843.0万 | **{expected_final/10000:.2f}万** | **{(expected_final-8430000)/10000:+.2f}万** |
| **期望总利润** | 343.0万 | **{expected_profit/10000:.2f}万** | **{(expected_profit-3430000)/10000:+.2f}万** |
| 投资期 | 4.48年 | 4.42年 | -0.06年 |
| 初始本金 | 500万 | 500万 | — |
| 建仓完成日 | 2026-07-10 | 2026-07-31 | +21天 |

**核心结论**: v7.7 通过 Theta 引擎月度 Covered Call 权利金收入(+5.7%/年净增益)叠加 Gamma+KillSwitch 尾部保护, 在轻微牺牲牛市参与度(-5%)和承担对冲成本(-2%/年)、清仓冲击成本(-4%累计)的前提下, 将期望年化收益率从 **11.84%** 提升至 **{expected_equivalent_annualized*100:.2f}%**, 同时将黑天鹅情景累计回撤从 -62.2% 改善到约 -45%, 显著优化风险调整后收益。

---

## 二、v7.7 四大模块参数

### 2.1 Theta 引擎 (Covered Call 月度)

| 参数 | 数值 |
|------|------|
| 月度权利金 | 32,104 元 |
| 年化权利金 | 385,248 元 |
| 担保物 | 400 万元 (重叠现货持仓) |
| ETF 头寸数 | 6 个 |
| 毛年化收益(按500万本金) | 7.71% |
| 担保物机会成本 | -2.00%/年 |
| **净年化贡献** | **+5.70%/年** |
| 与市场涨跌相关性 | 弱相关 |

### 2.2 Gamma 引擎 (尾部危机监控)

| 参数 | 数值 |
|------|------|
| 触发条件 | MA60 跌破 + IV 分位高位 |
| 预算 | 100 万元 |
| 保护目标 | 黑天鹅情景 |
| 累计保护幅度 | +17.2% (从 -62.2% 到 -45%) |

### 2.3 KillSwitch 三级熔断

| 级别 | 保证金占用 | 触发动作 |
|------|-----------|----------|
| **L1** | 50% | 停止开仓 |
| **L2** | 75% | 强平深虚值 + 变现红利 ETF |
| **L3** | 90% | 全面防御 |

- **悲观情景保护**: L1 触发减少回撤, 累计改善 +5.7% (从 -13.7% 到 -8%)
- **黑天鹅情景保护**: L2/L3 触发限制下行

### 2.4 LiquidationScheduler 2030 清仓协议

| 阶段 | 时点 | 动作 |
|------|------|------|
| Phase 1 | 2030-Q3 | TWAP 变现开始 |
| Phase 2 | 2030-11 | 加速清仓 |
| Phase 3 | 2030-12 | 完成清仓 |

- **影响时长**: 2030 年最后 5 个月
- **累计冲击成本**: -3% ~ -5% (中性取 -4%)
- **黑天鹅情景**: -1% (已损失, 边际影响小)

---

## 三、v4 vs v7.7 情景对比表

### 3.1 乐观情景 (概率 20%)

| 指标 | v4 | v7.7 | 变动 |
|------|-----|------|------|
| 年化收益率 | 27.06% | **{bull['equivalent_annualized_pct']:.2f}%** | {bull['equivalent_annualized_pct']-27.06:+.2f}% |
| 累计收益 | 184.50% | **{bull['adjusted_cumulative_pct']:.2f}%** | {bull['adjusted_cumulative_pct']-184.50:+.2f}% |
| 最终金额 | 1,422.54万 | **{bull['final_amount']/10000:.2f}万** | {(bull['final_amount']-14225400)/10000:+.2f}万 |

**调整明细**:
- Theta 净贡献: +5.70%/年
- 对冲成本: -2.00%/年
- 牛市参与度降低: {bull['bull_participation_adj_annual']:.3f}%/年 (v4 27.06% × -5%)
- Liquidation 清仓冲击: -4.00% 累计

### 3.2 基准情景 (概率 45%) ★最高权重

| 指标 | v4 | v7.7 | 变动 |
|------|-----|------|------|
| 年化收益率 | 16.13% | **{base['equivalent_annualized_pct']:.2f}%** | {base['equivalent_annualized_pct']-16.13:+.2f}% |
| 累计收益 | 101.80% | **{base['adjusted_cumulative_pct']:.2f}%** | {base['adjusted_cumulative_pct']-101.80:+.2f}% |
| 最终金额 | 1,009.00万 | **{base['final_amount']/10000:.2f}万** | {(base['final_amount']-10090000)/10000:+.2f}万 |

**调整明细**:
- Theta 净贡献: +5.70%/年
- 对冲成本: -2.00%/年
- 牛市参与度降低: {base['bull_participation_adj_annual']:.3f}%/年 (v4 16.13% × -5%)
- Liquidation 清仓冲击: -4.00% 累计

### 3.3 悲观情景 (概率 25%)

| 指标 | v4 | v7.7 | 变动 |
|------|-----|------|------|
| 年化收益率 | -3.27% | **{bear['equivalent_annualized_pct']:.2f}%** | {bear['equivalent_annualized_pct']-(-3.27):+.2f}% |
| 累计收益 | -13.70% | **{bear['adjusted_cumulative_pct']:.2f}%** | {bear['adjusted_cumulative_pct']-(-13.70):+.2f}% |
| 最终金额 | 431.50万 | **{bear['final_amount']/10000:.2f}万** | {(bear['final_amount']-4315000)/10000:+.2f}万 |

**调整明细**:
- Theta 净贡献: +5.70%/年
- 对冲成本: -2.00%/年
- KillSwitch L1 触发改善: 累计 +5.7% (年化 {bear['killswitch_annual']:.3f}%)
- Liquidation 清仓冲击: -4.00% 累计

### 3.4 黑天鹅情景 (概率 10%)

| 指标 | v4 | v7.7 | 变动 |
|------|-----|------|------|
| 年化收益率 | -20.23% | **{bs['equivalent_annualized_pct']:.2f}%** | {bs['equivalent_annualized_pct']-(-20.23):+.2f}% |
| 累计收益 | -62.20% | **{bs['adjusted_cumulative_pct']:.2f}%** | {bs['adjusted_cumulative_pct']-(-62.20):+.2f}% |
| 最终金额 | 189.00万 | **{bs['final_amount']/10000:.2f}万** | {(bs['final_amount']-1890000)/10000:+.2f}万 |

**调整明细**:
- Theta 净贡献: +5.70%/年
- 对冲成本: -2.00%/年
- Gamma+KillSwitch 尾部保护: 累计 +17.2% (年化 {bs['gamma_annual']:.3f}%)
- Liquidation 清仓冲击: -1.00% 累计 (黑天鹅情景边际影响小)

---

## 四、概率加权期望分析

### 4.1 加权计算

| 情景 | 概率 | v7.7 等效年化 | 加权贡献 |
|------|------|--------------|----------|
| 乐观 | 20% | {bull['equivalent_annualized_pct']:.2f}% | {0.20*bull['equivalent_annualized_pct']:.4f}% |
| 基准 | 45% | {base['equivalent_annualized_pct']:.2f}% | {0.45*base['equivalent_annualized_pct']:.4f}% |
| 悲观 | 25% | {bear['equivalent_annualized_pct']:.2f}% | {0.25*bear['equivalent_annualized_pct']:.4f}% |
| 黑天鹅 | 10% | {bs['equivalent_annualized_pct']:.2f}% | {0.10*bs['equivalent_annualized_pct']:.4f}% |
| **期望等效年化** | — | — | **{expected_equivalent_annualized*100:.2f}%** |

### 4.2 v4 vs v7.7 期望对比

| 指标 | v4 | v7.7 | 变动 |
|------|-----|------|------|
| 期望年化 | 11.84% | **{expected_equivalent_annualized*100:.2f}%** | **{expected_equivalent_annualized*100-11.84:+.2f}%** |
| 期望累计 | 68.60% | **{expected_cumulative*100:.2f}%** | **{expected_cumulative*100-68.60:+.2f}%** |
| 期望最终金额 | 843.00万 | **{expected_final/10000:.2f}万** | **{(expected_final-8430000)/10000:+.2f}万** |
| 黑天鹅累计回撤 | -62.20% | **{bs['adjusted_cumulative_pct']:.2f}%** | **{bs['adjusted_cumulative_pct']-(-62.20):+.2f}%** (改善) |

---

## 五、风格轮动 2030 展望 (v7.7 配置)

| 风格 | 当前权重 | 2030 权重 | 代表标的 | 2026-2030 年化 | Theta 适用 |
|------|---------|----------|----------|---------------|-----------|
| **高端制造(含算力)** | 40% | 40% | 中际旭创/海光信息/北方华创/中芯国际/宁德时代/徐工机械 | 18.0% | ETF 部分可做 |
| **顺周期** | 20% | 20% | 中国神华/南山铝业/宝钢股份 | 12.0% | — |
| **资源** | 20% | 20% | 华安黄金 ETF/盐湖股份 | 22.0% | 黄金 ETF 可做 |
| **防御** | 20% | 20% | 长江电力/恒瑞医药/药明康德/科伦药业 | 8.0% | 红利 ETF 可做 |

**配置要点**:
- 高端制造(含算力)维持 40% 上限, AI 算力主线 2-3 年内维持高景气
- 资源股 20% 权重迎接康波繁荣期主升浪, 2030+ 预期 22% 年化
- 防御板块 20% 同时承担 KillSwitch L2 触发时的变现池功能
- Theta 引擎覆盖 6 个 ETF 头寸, 担保物与现货持仓重叠, 不额外占用资金

---

## 六、关键风险

| 风险类型 | 描述 | 影响程度 |
|----------|------|----------|
| **集中度风险** | 高端制造(含算力)占比 40%, 2027-2028 估值回调风险 | 中 |
| **Theta 风险** | Covered Call 在牛市损失上行, 月度权利金不稳定 | 低-中 |
| **Gamma 风险** | 预算 100 万, 黑天鹅时对冲容量可能不足 | 中 |
| **KillSwitch 风险** | L2/L3 强平可能踩踏, 变现红利 ETF 损失股息 | 中 |
| **Liquidation 风险** | TWAP 清仓遇 2030 市场下行会放大损失 | 中 |
| **对冲成本** | 年化 -2.0%, 4.42 年累计侵蚀约 -8.5% | 中 |
| **★康波时点风险** | 繁荣期开启时点存在 1-3 年分歧, 最大不确定性 | 高 |

---

## 七、结论与建议

### 7.1 核心结论

基于 v4 康波繁荣期外推 + v7.7 四大对冲基金视角模块(Theta/Gamma/KillSwitch/Liquidation), 预测 2026-08 至 2030-12 期间(4.42 年)组合:

- **期望等效年化收益率**: **{expected_equivalent_annualized*100:.2f}%** (v4: 11.84%, 提升 {expected_equivalent_annualized*100-11.84:+.2f}%)
- **期望累计收益**: **{expected_cumulative*100:.2f}%** (v4: 68.60%)
- **期望最终金额**: **{expected_final/10000:.2f}万** (初始 500 万, v4: 843 万)
- **黑天鹅情景改善**: 累计回撤从 -62.2% 改善到 **{bs['adjusted_cumulative_pct']:.2f}%** (改善 {bs['adjusted_cumulative_pct']-(-62.20):+.2f}%)

### 7.2 关键洞察

1. **Theta 引擎是 v7.7 最大的 alpha 来源**: 月度权利金 32,104 元, 年化净贡献 +5.7%/年, 与市场涨跌弱相关, 为所有情景提供稳定增益
2. **Gamma+KillSwitch 显著优化尾部风险**: 黑天鹅情景从 -62.2% 改善到约 -45%, 悲观情景从 -13.7% 改善到约 -8%
3. **对冲成本是主要侵蚀项**: 年化 -2.0%, 4.42 年累计侵蚀约 -8.5%, 但换来尾部保护值得
4. **Liquidation 清仓协议降低冲击成本**: TWAP 分批变现将原本可能的一次性冲击成本分摊, 净损失约 4% 累计

### 7.3 操作建议

1. **严格执行 Theta 引擎月度 Covered Call**: 权利金再投资, 6 个 ETF 头寸持续轮动
2. **KillSwitch 监控**: 实时监控保证金占用, L1(50%)立即停止开仓, L2(75%)强平深虚值
3. **2028 年开始风格再平衡**: 观察资源股权重提升信号, 高端制造保持 40% 上限
4. **2030-Q3 启动 Liquidation**: 严格执行三阶段 TWAP 清仓协议
5. **康波繁荣期时点监控**: 若 2030 年如期开启, 资源股主升浪将贡献超额收益

---

## 八、文件输出

- **JSON**: `portfolio_return_projection_2030_v77.json`
- **Markdown**: `reports/portfolio_return_projection_2030_v77.md`
- **基础版本**: `portfolio_return_projection_2030.json` (v4)

---

*报告生成: v7.7 对冲基金视角系统 | 工作目录: `28-终极量化交易系统7.1`*
"""

with open(V77_MD, "w", encoding="utf-8") as f:
    f.write(md_content)
print(f"[OK] Markdown 已保存: {V77_MD}")

# ============== 控制台摘要 ==============
print("\n" + "=" * 70)
print("v7.7 对冲基金视角系统 - 2030年化收益率预测报告生成完成")
print("=" * 70)
print(f"投资期: 2026-08-01 → 2030-12-31 ({INVESTMENT_HORIZON_YEARS}年)")
print(f"初始本金: {INITIAL_CAPITAL/10000:.0f}万")
print()
print("【四情景对比】")
print(f"  乐观 (20%): 年化 {bull['equivalent_annualized_pct']:.2f}% | 累计 {bull['adjusted_cumulative_pct']:.2f}% | 最终 {bull['final_amount']/10000:.2f}万")
print(f"  基准 (45%): 年化 {base['equivalent_annualized_pct']:.2f}% | 累计 {base['adjusted_cumulative_pct']:.2f}% | 最终 {base['final_amount']/10000:.2f}万")
print(f"  悲观 (25%): 年化 {bear['equivalent_annualized_pct']:.2f}% | 累计 {bear['adjusted_cumulative_pct']:.2f}% | 最终 {bear['final_amount']/10000:.2f}万")
print(f"  黑天鹅(10%): 年化 {bs['equivalent_annualized_pct']:.2f}% | 累计 {bs['adjusted_cumulative_pct']:.2f}% | 最终 {bs['final_amount']/10000:.2f}万")
print()
print("【期望值】")
print(f"  期望等效年化: {expected_equivalent_annualized*100:.2f}% (v4: 11.84%, Δ {expected_equivalent_annualized*100-11.84:+.2f}%)")
print(f"  期望累计收益: {expected_cumulative*100:.2f}% (v4: 68.60%)")
print(f"  期望最终金额: {expected_final/10000:.2f}万 (v4: 843.00万)")
print()
print("【输出文件】")
print(f"  JSON: {V77_JSON}")
print(f"  MD:   {V77_MD}")
print("=" * 70)
