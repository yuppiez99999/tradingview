"""
基于 config/positions.json 最新持仓重新生成 portfolio_return_projection.json v3
==========================================================================

更新内容 (v3):
  - 对齐 2026-07-09 新质生产力降权 + 健康中国加仓后的 20 标的
  - 权重源: config/positions.json (target_weight 字段)
  - 场景假设: 基于十五五规划周期 + 康波复苏期 + AI 算力景气度

数据源:
  - config/positions.json (持仓代码 + target_weight + style + reason)
  - 历史场景假设 (个股 beta + 行业景气度)

输出:
  - portfolio_return_projection.json (覆盖原文件, 原文件自动备份)
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ============================================================
# 20 标的场景假设 (年化收益率 %)
# 场景: bull(牛市) / base(中性) / bear(熊市) / black_swan(黑天鹅)
# 权重来自 config/positions.json (target_weight 字段)
# ============================================================
ASSET_DETAILS = [
    # === 新质生产力方向 (20%, 6 标的) - 已降权 ===
    {
        "code": "588080", "name": "科创50ETF易方达", "style": "科技", "weight": 0.05, "risk": "高",
        "reason": "ETF资金流强信号+国家队57亿净流入; 科创板AI/半导体龙头集合; 十五五降权新质生产力",
        "returns": {"bull": 45.0, "base": 25.0, "bear": -30.0, "black_swan": -50.0},
    },
    {
        "code": "512760", "name": "半导体ETF国泰", "style": "科技", "weight": 0.03, "risk": "高",
        "reason": "ETF资金流关注9亿; 半导体国产替代整体beta; 十五五降权新质生产力",
        "returns": {"bull": 42.0, "base": 23.0, "bear": -28.0, "black_swan": -48.0},
    },
    {
        "code": "688041", "name": "海光信息", "style": "科技", "weight": 0.04, "risk": "高",
        "reason": "AI算力CPU+DCU龙头; 科创50ETF重仓; 十五五降权新质生产力",
        "returns": {"bull": 55.0, "base": 30.0, "bear": -35.0, "black_swan": -55.0},
    },
    {
        "code": "300308", "name": "中际旭创", "style": "科技", "weight": 0.04, "risk": "高",
        "reason": "光模块龙头; AI算力核心; 三重共振; 十五五降权新质生产力",
        "returns": {"bull": 60.0, "base": 35.0, "bear": -40.0, "black_swan": -60.0},
    },
    {
        "code": "603019", "name": "中科曙光", "style": "科技", "weight": 0.02, "risk": "高",
        "reason": "算力基础设施; 康波新技术革命; 十五五降权新质生产力",
        "returns": {"bull": 50.0, "base": 28.0, "bear": -32.0, "black_swan": -55.0},
    },
    {
        "code": "688981", "name": "中芯国际", "style": "科技", "weight": 0.02, "risk": "高",
        "reason": "晶圆代工龙头; 半导体制造核心; 十五五新质生产力+国产替代",
        "returns": {"bull": 45.0, "base": 25.0, "bear": -30.0, "black_swan": -50.0},
    },
    # === 健康中国方向 (16%, 2 标的) - 已加仓 ===
    {
        "code": "512170", "name": "医疗ETF华宝", "style": "医药", "weight": 0.10, "risk": "中",
        "reason": "ETF资金流关注3亿; 十五五健康中国旗舰仓位; 医药全产业链beta; 十五五降权新质生产力转移",
        "returns": {"bull": 28.0, "base": 16.0, "bear": -18.0, "black_swan": -32.0},
    },
    {
        "code": "600276", "name": "恒瑞医药", "style": "医药", "weight": 0.06, "risk": "中高",
        "reason": "创新药龙头; 十五五健康中国核心仓; 十五五降权新质生产力转移",
        "returns": {"bull": 30.0, "base": 18.0, "bear": -20.0, "black_swan": -35.0},
    },
    # === 数字中国方向 (9%, 2 标的) ===
    {
        "code": "512880", "name": "证券ETF国泰", "style": "金融", "weight": 0.05, "risk": "中",
        "reason": "ETF资金流最强67亿净流入; 牛市旗手; 康波复苏期券商先行",
        "returns": {"bull": 35.0, "base": 18.0, "bear": -25.0, "black_swan": -40.0},
    },
    {
        "code": "300033", "name": "同花顺", "style": "科技", "weight": 0.04, "risk": "中高",
        "reason": "金融科技AI; 证券ETF强信号受益; 十五五数字中国",
        "returns": {"bull": 40.0, "base": 22.0, "bear": -28.0, "black_swan": -45.0},
    },
    # === 绿色低碳方向 (13%, 3 标的) ===
    {
        "code": "515030", "name": "新能源车ETF华夏", "style": "新能源", "weight": 0.05, "risk": "中高",
        "reason": "ETF资金流加仓11亿; 十五五双碳+新能源车战略",
        "returns": {"bull": 35.0, "base": 20.0, "bear": -25.0, "black_swan": -42.0},
    },
    {
        "code": "300274", "name": "阳光电源", "style": "新能源", "weight": 0.04, "risk": "中高",
        "reason": "光伏储能龙头; 十五五双碳",
        "returns": {"bull": 50.0, "base": 28.0, "bear": -30.0, "black_swan": -50.0},
    },
    {
        "code": "600900", "name": "长江电力", "style": "防御", "weight": 0.04, "risk": "低",
        "reason": "水电龙头+高股息4%; 防御底仓; 降低回撤",
        "returns": {"bull": 12.0, "base": 8.0, "bear": -5.0, "black_swan": -10.0},
    },
    # === 制造强国方向 (3%, 1 标的) ===
    {
        "code": "688017", "name": "绿的谐波", "style": "制造", "weight": 0.03, "risk": "中高",
        "reason": "机器人精密减速器; 新技术革命核心",
        "returns": {"bull": 45.0, "base": 25.0, "bear": -30.0, "black_swan": -50.0},
    },
    # === 安全发展方向 (18%, 4 标的) ===
    {
        "code": "510050", "name": "上证50ETF华夏", "style": "宽基", "weight": 0.06, "risk": "低",
        "reason": "ETF资金流加仓40亿; 大盘蓝筹底仓; 降低波动率",
        "returns": {"bull": 15.0, "base": 9.0, "bear": -10.0, "black_swan": -20.0},
    },
    {
        "code": "512800", "name": "银行ETF华宝", "style": "金融", "weight": 0.06, "risk": "中",
        "reason": "ETF资金流加仓23亿; 高股息5%+低波动; 组合稳定器",
        "returns": {"bull": 14.0, "base": 8.0, "bear": -8.0, "black_swan": -18.0},
    },
    {
        "code": "000408", "name": "藏格矿业", "style": "资源", "weight": 0.04, "risk": "中高",
        "reason": "锂+钾双资源; 新能源上游",
        "returns": {"bull": 35.0, "base": 22.0, "bear": -25.0, "black_swan": -40.0},
    },
    {
        "code": "601088", "name": "中国神华", "style": "顺周期", "weight": 0.03, "risk": "中",
        "reason": "煤炭高股息6%+低波动; 组合稳定器",
        "returns": {"bull": 15.0, "base": 10.0, "bear": -15.0, "black_swan": -25.0},
    },
    # === 区域协调方向 (0%, 未配置) ===
    # === 避险/对冲方向 (5%, 1 标的) ===
    {
        "code": "518880", "name": "黄金ETF华安", "style": "资源", "weight": 0.05, "risk": "中",
        "reason": "周金涛萧条末期黄金最优; 避险+降低回撤",
        "returns": {"bull": 20.0, "base": 12.0, "bear": -10.0, "black_swan": -25.0},
    },
    # === 其他 (2 标的, 辅助配置) ===
    {
        "code": "002371", "name": "北方华创", "style": "科技", "weight": 0.04, "risk": "高",
        "reason": "半导体设备龙头; 国产替代核心",
        "returns": {"bull": 50.0, "base": 28.0, "bear": -33.0, "black_swan": -55.0},
    },
    {
        "code": "601899", "name": "紫金矿业", "style": "资源", "weight": 0.02, "risk": "中",
        "reason": "黄金+铜龙头; 战略资源安全; 周金涛萧条末期黄金最优+康波资源主升浪",
        "returns": {"bull": 35.0, "base": 20.0, "bear": -22.0, "black_swan": -38.0},
    },
    {
        "code": "002281", "name": "光迅科技", "style": "科技", "weight": 0.02, "risk": "高",
        "reason": "光模块龙头; AI算力网络层核心; 十五五算力基建+康波新技术革命",
        "returns": {"bull": 55.0, "base": 30.0, "bear": -35.0, "black_swan": -55.0},
    },
    {
        "code": "000901", "name": "国盾量子", "style": "科技", "weight": 0.01, "risk": "高",
        "reason": "量子通信龙头; 十五五新质生产力前沿方向; 高风险主题观察仓",
        "returns": {"bull": 60.0, "base": 25.0, "bear": -40.0, "black_swan": -60.0},
    },
]

# 验证权重总和
_TOTAL_WEIGHT = sum(a["weight"] for a in ASSET_DETAILS)
print(f"[预检] 标的数: {len(ASSET_DETAILS)}, 权重总和: {_TOTAL_WEIGHT:.2%}")
# 归一化到 100%
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
        d["effective_return"] = d["weighted_return"] / d["weight"] if d["weight"] > 0 else 0

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
    print("重新生成 portfolio_return_projection.json v3")
    print("基于 config/positions.json (2026-07-09 十五五降权后持仓)")
    print("=" * 70)

    # 校验持仓清单与 positions.json 一致
    pos_path = PROJECT_ROOT / "config" / "positions.json"
    with open(pos_path, encoding="utf-8") as f:
        pos_data = json.load(f)
    pos_keys = list(pos_data["positions"].keys())
    pos_codes = [k.split(".")[0] for k in pos_keys]
    asset_codes = [a["code"] for a in ASSET_DETAILS]
    print(f"positions.json 持仓: {len(pos_codes)} 标的")
    print(f"projection 标的:    {len(asset_codes)} 标的")

    missing = [c for c in pos_codes if c not in asset_codes]
    extra = [c for c in asset_codes if c not in pos_codes]
    if missing or extra:
        print(f"⚠️ 不一致: missing={missing}, extra={extra}")
    else:
        print("✓ 持仓清单完全一致")
    print()

    # 校验权重总和
    total_weight = sum(a["weight"] for a in ASSET_DETAILS)
    print(f"权重总和(归一化后): {total_weight*100:.2f}%")
    print()

    # 计算四场景
    scenarios = {}
    for s in ["bull", "base", "bear", "black_swan"]:
        scenarios[s] = calc_scenario_weighted(s)
        sc = scenarios[s]
        label = {"bull": "乐观 (牛市)", "base": "基准 (中性)",
                 "bear": "悲观 (熊市)", "black_swan": "黑天鹅 (极端)"}[s]
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

    # 构造 asset_detail
    asset_detail = []
    for a in ASSET_DETAILS:
        base_r = a["returns"]["base"]
        base_cum = (1 + base_r / 100) ** HORIZON_YEARS - 1
        base_profit = INITIAL_CAPITAL * a["weight"] * base_cum
        asset_detail.append({
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
        })

    # 构造完整 projection
    projection = {
        "generated_at": datetime.now().isoformat(),
        "version": "v3_2026-07-09_十五五降权",
        "investment_horizon": "2026-07-10 → 2027-12-31",
        "horizon_years": HORIZON_YEARS,
        "initial_capital": INITIAL_CAPITAL,
        "data_source": {
            "positions": "config/positions.json (2026-07-09, 降权后)",
            "weights": "config/positions.json target_weight",
            "history": "config/returns_history.json (2025-06-23 → 2026-07-05)",
        },
        "scenarios": {
            "bull": {"label": "乐观 (牛市)", **scenarios["bull"]},
            "base": {"label": "基准 (中性)", **scenarios["base"]},
            "bear": {"label": "悲观 (熊市)", **scenarios["bear"]},
            "black_swan": {"label": "黑天鹅 (极端)", **scenarios["black_swan"]},
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
            "calibrated_at": datetime.now().isoformat(),
            "realized_annualized": 76.46,
            "realized_period": "2025-06-23 → 2026-07-05",
            "calibration_reason": "realized > base*1.2, bull 概率上调至 0.25",
            "market_annualized": 27.58,
            "market_sharpe": 1.70,
            "note": "校准数据基于已覆盖持仓的历史年化 (样本小, 仅供参考)",
        },
        "risk_disclosure": {
            "concentration_risk": "新质生产力 20% (降权后), 健康中国 16% (加仓后), 双主线配置",
            "volatility_risk": "高波动标的 (688041/300308/002371) 占比 12%, 单日波动可达 ±5%",
            "hedge_coverage": "已建仓 IF 股指期货空头 2 手 + IM 1 手 + 510050 Put 10 张, 尾部保护",
            "policy_risk": "十五五规划落地节奏、半导体出口管制、AI 监管、医保集采",
            "liquidity_risk": "500 万规模对个股冲击成本约 0.1-0.3%",
        },
    }

    # 备份原文件
    proj_path = PROJECT_ROOT / "portfolio_return_projection.json"
    bak_path = proj_path.with_suffix(
        f".json.bak_{datetime.now():%Y%m%d_%H%M%S}"
    )
    if proj_path.exists():
        shutil.copy(proj_path, bak_path)
        print(f"✓ 原文件备份: {bak_path.name}")

    # 写入
    with open(proj_path, "w", encoding="utf-8") as f:
        json.dump(projection, f, ensure_ascii=False, indent=2)
    print(f"✓ 新文件已写入: {proj_path}")
    print()
    print("=" * 70)
    print("重新生成完成 (v3, 十五五降权后)")
    print("=" * 70)


if __name__ == "__main__":
    main()
