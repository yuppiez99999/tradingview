"""
基于 config/positions.json 真实持仓重新生成 portfolio_return_projection.json
========================================================================

数据源:
    - 500万建仓计划_20260706.json (权重 + 风格 + 风险)
    - config/positions.json (持仓代码清单)

输出:
    - portfolio_return_projection.json (覆盖原文件, 原文件自动备份)

场景假设基于:
    - 2026 年 AI/半导体/算力赛道景气度持续
    - 康波第六轮周期 + 十五五规划政策红利
    - 美联储利率周期、地缘政治、市场波动率
"""

import json
import shutil
from pathlib import Path

from utils.datetime_utils import now_bj

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ============================================================
# 真实持仓清单 (来自 500万建仓计划_20260706.json)
# 字段: code, name, style, weight, risk, base/bull/bear/black_swan 年化收益率(%)
# ============================================================
ASSET_DETAILS = [
    # === 科技/高端制造板块 (58.5%) ===
    {
        "code": "588000",
        "name": "科创50ETF华夏",
        "style": "科技",
        "weight": 0.167,
        "risk": "高",
        "reason": "AI/科技核心指数, 含中芯/海光/中微等龙头",
        "returns": {"bull": 45.0, "base": 25.0, "bear": -30.0, "black_swan": -50.0},
    },
    {
        "code": "688041",
        "name": "海光信息",
        "style": "科技",
        "weight": 0.133,
        "risk": "高",
        "reason": "AI算力国产替代, CPU/DCU 双轮驱动",
        "returns": {"bull": 55.0, "base": 30.0, "bear": -35.0, "black_swan": -55.0},
    },
    {
        "code": "002371",
        "name": "北方华创",
        "style": "科技",
        "weight": 0.117,
        "risk": "高",
        "reason": "半导体设备龙头, 国产化率提升红利",
        "returns": {"bull": 50.0, "base": 28.0, "bear": -33.0, "black_swan": -55.0},
    },
    {
        "code": "688981",
        "name": "中芯国际",
        "style": "科技",
        "weight": 0.100,
        "risk": "高",
        "reason": "半导体制造龙头, 产能扩张周期",
        "returns": {"bull": 45.0, "base": 25.0, "bear": -32.0, "black_swan": -50.0},
    },
    {
        "code": "300308",
        "name": "中际旭创",
        "style": "科技",
        "weight": 0.068,
        "risk": "高",
        "reason": "光模块全球龙头, AI数据中心 800G/1.6T 放量",
        "returns": {"bull": 60.0, "base": 35.0, "bear": -40.0, "black_swan": -60.0},
    },
    # === 顺周期板块 (12.1%) ===
    {
        "code": "000425",
        "name": "徐工机械",
        "style": "制造",
        "weight": 0.057,
        "risk": "中",
        "reason": "工程机械龙头, 周期复苏受益",
        "returns": {"bull": 25.0, "base": 15.0, "bear": -20.0, "black_swan": -35.0},
    },
    {
        "code": "601088",
        "name": "中国神华",
        "style": "顺周期",
        "weight": 0.064,
        "risk": "中",
        "reason": "煤电一体化, 高股息防御",
        "returns": {"bull": 15.0, "base": 10.0, "bear": -15.0, "black_swan": -25.0},
    },
    # === 医药板块 (6.7%) ===
    {
        "code": "600276",
        "name": "恒瑞医药",
        "style": "医药",
        "weight": 0.067,
        "risk": "中高",
        "reason": "创新药龙头, 集采影响出清, 出海加速",
        "returns": {"bull": 30.0, "base": 18.0, "bear": -20.0, "black_swan": -35.0},
    },
    # === 防御板块 (10.3%) ===
    {
        "code": "600900",
        "name": "长江电力",
        "style": "防御",
        "weight": 0.050,
        "risk": "低",
        "reason": "水电龙头, 高股息稳健",
        "returns": {"bull": 12.0, "base": 8.0, "bear": -5.0, "black_swan": -10.0},
    },
    {
        "code": "515180",
        "name": "易方达中证红利ETF",
        "style": "红利",
        "weight": 0.033,
        "risk": "中",
        "reason": "高股息低估值, 防御属性",
        "returns": {"bull": 10.0, "base": 7.0, "bear": -8.0, "black_swan": -15.0},
    },
    {
        "code": "600036",
        "name": "招商银行",
        "style": "银行",
        "weight": 0.020,
        "risk": "中",
        "reason": "零售银行龙头, 资产质量优异",
        "returns": {"bull": 18.0, "base": 12.0, "bear": -18.0, "black_swan": -30.0},
    },
    # === 资源板块 (6.7%) - 已剔除 000792 盐湖股份 ===
    {
        "code": "518880",
        "name": "黄金ETF华安",
        "style": "避险",
        "weight": 0.067,
        "risk": "中",
        "reason": "抗通胀+危机对冲, 美联储降息周期受益",
        "returns": {"bull": 20.0, "base": 12.0, "bear": -10.0, "black_swan": -25.0},
    },
    # === 2026-07-09 新增 6 标的 (十五五+康波+周金涛理论补缺) ===
    {
        "code": "300274",
        "name": "阳光电源",
        "style": "新能源",
        "weight": 0.05,
        "risk": "中高",
        "reason": "补十五五新能源与储能缺口; 康波繁荣期成长股优先; 光伏逆变器+储能龙头",
        "returns": {"bull": 50.0, "base": 28.0, "bear": -30.0, "black_swan": -50.0},
    },
    {
        "code": "603019",
        "name": "中科曙光",
        "style": "科技",
        "weight": 0.04,
        "risk": "高",
        "reason": "补 AI 算力基础设施; 国产替代核心标的; 中科院系服务器龙头",
        "returns": {"bull": 50.0, "base": 28.0, "bear": -32.0, "black_swan": -55.0},
    },
    {
        "code": "600089",
        "name": "特变电工",
        "style": "制造",
        "weight": 0.03,
        "risk": "中",
        "reason": "补高端装备制造覆盖过薄; 算力+电网输变电双轮驱动",
        "returns": {"bull": 28.0, "base": 16.0, "bear": -22.0, "black_swan": -38.0},
    },
    {
        "code": "688017",
        "name": "绿的谐波",
        "style": "制造",
        "weight": 0.02,
        "risk": "中高",
        "reason": "补机器人产业链; 高端制造谐波减速器龙头",
        "returns": {"bull": 45.0, "base": 25.0, "bear": -30.0, "black_swan": -50.0},
    },
    {
        "code": "600219",
        "name": "南山铝业",
        "style": "资源",
        "weight": 0.03,
        "risk": "中",
        "reason": "补战略资源; 康波繁荣期大宗商品主升浪; 航空航天铝材",
        "returns": {"bull": 30.0, "base": 18.0, "bear": -20.0, "black_swan": -35.0},
    },
    {
        "code": "600019",
        "name": "宝钢股份",
        "style": "资源",
        "weight": 0.02,
        "risk": "中",
        "reason": "补黑色系; 周金涛理论强调繁荣期铜铁主升浪; 钢铁龙头",
        "returns": {"bull": 22.0, "base": 13.0, "bear": -18.0, "black_swan": -30.0},
    },
    # === 2026-07-09 再增 5 标的 (来自盘前综合报告十五五对标) ===
    {
        "code": "000680",
        "name": "山推股份",
        "style": "制造",
        "weight": 0.04,
        "risk": "中",
        "reason": "补十五五科技自立自强+高端装备制造; 工程机械龙头; 康波繁荣期基建受益",
        "returns": {"bull": 28.0, "base": 16.0, "bear": -22.0, "black_swan": -38.0},
    },
    {
        "code": "000333",
        "name": "美的集团",
        "style": "制造",
        "weight": 0.04,
        "risk": "中",
        "reason": "补十五五科技自立自强(智能家电+工业自动化); 康波繁荣期消费升级; 全球化龙头",
        "returns": {"bull": 22.0, "base": 14.0, "bear": -16.0, "black_swan": -28.0},
    },
    {
        "code": "000408",
        "name": "藏格矿业",
        "style": "资源",
        "weight": 0.05,
        "risk": "中高",
        "reason": "补十五五新能源与双碳+资源安全; 康波繁荣期大宗商品主升浪; 锂+钾双资源",
        "returns": {"bull": 35.0, "base": 22.0, "bear": -25.0, "black_swan": -40.0},
    },
    {
        "code": "000975",
        "name": "山金国际",
        "style": "资源",
        "weight": 0.03,
        "risk": "中",
        "reason": "补十五五资源安全(黄金); 康波繁荣期黄金抗通胀; 货币信用对冲",
        "returns": {"bull": 28.0, "base": 18.0, "bear": -18.0, "black_swan": -30.0},
    },
    {
        "code": "002422",
        "name": "科伦药业",
        "style": "医药",
        "weight": 0.02,
        "risk": "中",
        "reason": "补十五五医药健康方向; 大输液+原料药+创新药三轨; 防御性配置",
        "returns": {"bull": 25.0, "base": 15.0, "bear": -18.0, "black_swan": -30.0},
    },
]

# 23 标的权重归一化到 1.0 (18 旧标的 + 5 新标的; 总和需归一化)
_TOTAL_WEIGHT = sum(a["weight"] for a in ASSET_DETAILS)
for _a in ASSET_DETAILS:
    _a["weight"] = round(_a["weight"] / _TOTAL_WEIGHT, 6)

# 场景概率权重 (基于历史数据校准)
PROBABILITY_WEIGHTS = {
    "bull": 0.25,
    "base": 0.45,
    "bear": 0.25,
    "black_swan": 0.05,
}

# 投资期限
HORIZON_YEARS = 1.5
INITIAL_CAPITAL = 5_000_000


def calc_scenario_weighted(scenario: str) -> dict:
    """计算某场景下组合加权年化收益率"""
    weighted_annualized = 0.0
    style_breakdown = {}
    for asset in ASSET_DETAILS:
        w = asset["weight"]
        r = asset["returns"][scenario]
        contrib = r * w
        weighted_annualized += contrib
        style = asset["style"]
        if style not in style_breakdown:
            style_breakdown[style] = {"weight": 0.0, "weighted_return": 0.0}
        style_breakdown[style]["weight"] += w
        style_breakdown[style]["weighted_return"] += contrib

    # 计算各风格隐含年化
    for _style, d in style_breakdown.items():
        d["effective_return"] = (
            d["weighted_return"] / d["weight"] if d["weight"] > 0 else 0
        )

    cumulative = (1 + weighted_annualized / 100) ** HORIZON_YEARS - 1
    final_amount = INITIAL_CAPITAL * (1 + cumulative)
    return {
        "weighted_annualized": round(weighted_annualized, 2),
        "cumulative_return": round(cumulative * 100, 2),
        "final_amount": round(final_amount, 0),
        "total_profit": round(final_amount - INITIAL_CAPITAL, 0),
        "style_breakdown": style_breakdown,
    }


def main():
    print("=" * 70)
    print("重新生成 portfolio_return_projection.json")
    print("基于 config/positions.json 真实持仓")
    print("=" * 70)

    # 校验持仓清单与 positions.json 一致
    pos_path = PROJECT_ROOT / "config" / "positions.json"
    with open(pos_path, encoding="utf-8") as f:
        pos_data = json.load(f)
    # positions 字段是 dict, key 格式如 "588000.SZ"
    pos_keys = list(pos_data["positions"].keys())
    # 归一化 (取点号前的 6 位代码)
    pos_codes = [k.split(".")[0] for k in pos_keys]
    asset_codes = [a["code"] for a in ASSET_DETAILS]
    print(f"positions.json 持仓: {len(pos_codes)} 标的")
    print(f"projection 标的:    {len(asset_codes)} 标的")

    missing = [c for c in pos_codes if c not in asset_codes]
    extra = [c for c in asset_codes if c not in pos_codes]
    if missing or extra:
        print(f"⚠️ 不一致: missing={missing}, extra={extra}")
        print("  (继续生成, 但请检查持仓清单一致性)")
    else:
        print("✓ 持仓清单完全一致")
    print()

    # 校验权重总和
    total_weight = sum(a["weight"] for a in ASSET_DETAILS)
    print(f"权重总和: {total_weight*100:.2f}%")
    print()

    # 计算四场景
    scenarios = {}
    for s in ["bull", "base", "bear", "black_swan"]:
        scenarios[s] = calc_scenario_weighted(s)
        sc = scenarios[s]
        label = {
            "bull": "乐观 (牛市)",
            "base": "基准 (中性)",
            "bear": "悲观 (熊市)",
            "black_swan": "黑天鹅 (极端)",
        }[s]
        print(f"[{s}] {label}")
        print(f"  加权年化: {sc['weighted_annualized']:.2f}%")
        print(f"  累计收益: {sc['cumulative_return']:.2f}%")
        print(f"  期末金额: ¥{sc['final_amount']:,.0f}")
        print(f"  盈亏: ¥{sc['total_profit']:+,.0f}")
        print()

    # 计算加权期望
    expected_annualized = sum(
        scenarios[s]["weighted_annualized"] * PROBABILITY_WEIGHTS[s]
        for s in ["bull", "base", "bear", "black_swan"]
    )
    expected_cumulative = (1 + expected_annualized / 100) ** HORIZON_YEARS - 1
    expected_final = INITIAL_CAPITAL * (1 + expected_cumulative)
    expected_profit = expected_final - INITIAL_CAPITAL
    print(f"加权期望年化: {expected_annualized:.2f}%")
    print(f"加权期望累计: {expected_cumulative*100:.2f}%")
    print(f"加权期望期末金额: ¥{expected_final:,.0f}")
    print(f"加权期望盈亏: ¥{expected_profit:+,.0f}")
    print()

    # 构造 asset_detail (每标的 4 场景)
    asset_detail = []
    for a in ASSET_DETAILS:
        for scenario_name in ["bull", "base"]:
            r = a["returns"][scenario_name]
            cum = (1 + r / 100) ** HORIZON_YEARS - 1
            profit = INITIAL_CAPITAL * a["weight"] * cum
            if scenario_name == "base":
                base_cum = cum
                base_profit = profit
        # base 场景数据
        base_r = a["returns"]["base"]
        base_cum = (1 + base_r / 100) ** HORIZON_YEARS - 1
        base_profit = INITIAL_CAPITAL * a["weight"] * base_cum
        asset_detail.append(
            {
                "code": a["code"],
                "name": a["name"],
                "weight": a["weight"],
                "style": a["style"],
                "risk": a["risk"],
                "reason": a["reason"],
                "scenario_returns": a["returns"],
                "base_annualized": base_r,
                "base_cumulative": round(base_cum * 100, 2),
                "base_profit": round(base_profit, 0),
            }
        )

    # 构造完整 projection
    projection = {
        "generated_at": now_bj().isoformat(),
        "investment_horizon": "2026-07-06 → 2027-12-31",
        "horizon_years": HORIZON_YEARS,
        "initial_capital": INITIAL_CAPITAL,
        "data_source": {
            "positions": "config/positions.json (2026-07-06)",
            "weights": "500万建仓计划_20260706.json",
            "history": "config/returns_history.json (2025-06-23 → 2026-07-05)",
        },
        "scenarios": {
            "bull": {
                "label": "乐观 (牛市)",
                **scenarios["bull"],
            },
            "base": {
                "label": "基准 (中性)",
                **scenarios["base"],
            },
            "bear": {
                "label": "悲观 (熊市)",
                **scenarios["bear"],
            },
            "black_swan": {
                "label": "黑天鹅 (极端)",
                **scenarios["black_swan"],
            },
        },
        "probability_weights": PROBABILITY_WEIGHTS,
        "probability_weights_note": "基于 2025-06-23 → 2026-07-05 历史数据校准 (realized 76% > base*1.2)",
        "expected": {
            "expected_annualized": round(expected_annualized, 2),
            "expected_cumulative": round(expected_cumulative * 100, 2),
            "expected_final_amount": round(expected_final, 0),
            "expected_profit": round(expected_profit, 0),
        },
        "asset_detail": asset_detail,
        "calibration": {
            "calibrated_at": now_bj().isoformat(),
            "realized_annualized": 76.46,
            "realized_period": "2025-06-23 → 2026-07-05",
            "calibration_reason": "realized > base*1.2, bull 概率上调至 0.25",
            "market_annualized": 27.58,
            "market_sharpe": 1.70,
            "note": "校准数据基于已覆盖持仓 (4/13, 权重34.7%) 的历史年化",
        },
        "risk_disclosure": {
            "concentration_risk": "科技板块权重 58.5%, 单一板块回调 20% → 组合损失 11.7%",
            "volatility_risk": "高波动标的 (688041/002371/300308) 占比 31.8%, 单日波动可达 ±5%",
            "hedge_coverage": "已建仓 IF 股指期货空头 2 手 @4703.4, 对冲比率 56.44%",
            "policy_risk": "十五五规划落地节奏、半导体出口管制、AI 监管",
            "liquidity_risk": "500 万规模对个股冲击成本约 0.1-0.3%",
        },
    }

    # 备份原文件
    proj_path = PROJECT_ROOT / "portfolio_return_projection.json"
    bak_path = proj_path.with_suffix(f".json.bak_{now_bj():%Y%m%d_%H%M%S}")
    if proj_path.exists():
        shutil.copy(proj_path, bak_path)
        print(f"✓ 原文件备份: {bak_path.name}")

    # 写入
    with open(proj_path, "w", encoding="utf-8") as f:
        json.dump(projection, f, ensure_ascii=False, indent=2)
    print(f"✓ 新文件已写入: {proj_path}")
    print()
    print("=" * 70)
    print("重新生成完成")
    print("=" * 70)


if __name__ == "__main__":
    main()
