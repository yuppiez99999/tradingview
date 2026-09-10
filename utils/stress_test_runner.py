"""
压力测试自动化模块 (Stress Test Runner)
========================================

基于 v10.0 投资计划手册第四章 4.4 节, 实现四种压力测试场景:
    1. 2015 式股灾: 指数 -40%/3个月/V 型反弹 → 预期组合回撤 -8.5%
    2. 2018 式慢熊: 指数 -25%/12个月/阴跌 → 预期组合回撤 -13.9% (无干预) / -8.0% (有干预)
    3. 2020 式冲击: 指数 -15%/1个月/V 型反弹 → 预期组合回撤 -0.4%
    4. 流动性危机: 相关性 → 1, 对冲效率降至 30% → 预期组合回撤 -18.6%

季度执行, 输出报告到 reports/stress_test_{date}.json

用法:
    from utils.stress_test_runner import StressTestRunner
    runner = StressTestRunner()
    result = runner.run_all_scenarios(positions, portfolio_value=5_000_000)
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

# CLI 直跑 (python utils/stress_test_runner.py) 时 sys.path[0] 是 utils/,
# 顶层 `utils` 包不可见 → 显式补项目根 (项目约定: CLI 入口须显式 sys.path.insert)
_BASE_DIR = Path(__file__).resolve().parent.parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from utils.datetime_utils import now_bj  # noqa: E402

logger = logging.getLogger("stress_test")

BASE_DIR = _BASE_DIR
REPORT_DIR = BASE_DIR / "reports"

# 模拟持仓 (--simulate 或显式要求时使用)。集中定义避免散落重复、便于维护。
# 注意: 真实持仓路径在任何情况下都不得静默回退到此数据, 否则会产出"假真实"报告。
_SIMULATED_POSITIONS = [
    {
        "code": "stock",
        "name": "股票多头",
        "amount": 1_800_000,
        "strategy": "stock_long",
    },
    {"code": "etf", "name": "ETF组合", "amount": 500_000, "strategy": "etf"},
    {
        "code": "quant",
        "name": "量化中性",
        "amount": 700_000,
        "strategy": "quant_neutral",
    },
    {
        "code": "option",
        "name": "期权尾部",
        "amount": 200_000,
        "strategy": "options_tail",
    },
    {
        "code": "future",
        "name": "期货对冲",
        "amount": 500_000,
        "strategy": "futures_hedge",
    },
    {"code": "cash", "name": "现金管理", "amount": 1_300_000, "strategy": "cash"},
]


# 四大压力测试场景定义 (v10.0 手册)
STRESS_SCENARIOS = {
    "crash_2015": {
        "name": "2015式股灾",
        "description": "指数 -40%/3个月/V型反弹",
        "index_drop_pct": -0.40,
        "duration_months": 3,
        "shape": "V",
        "asset_impacts": {
            "stock": -0.40,  # 股票 -40%
            "etf": -0.35,  # ETF -35%
            "quant_neutral": -0.15,  # 量化中性 -15%
            "options_tail": 0.75,  # 尾部期权 +75% (put payoff)
            "futures_hedge": 0.30,  # 期货对冲回收 30%
            "cash": 0.0,  # 现金无影响
        },
        "expected_portfolio_dd": -0.085,
        "limit": -0.15,
    },
    "slow_bear_2018": {
        "name": "2018式慢熊",
        "description": "指数 -25%/12个月/阴跌",
        "index_drop_pct": -0.25,
        "duration_months": 12,
        "shape": "downside",
        "asset_impacts": {
            "stock": -0.25,
            "etf": -0.20,
            "quant_neutral": -0.08,
            "options_tail": -1.0,  # 期权全部到期归零
            "futures_hedge": 0.30,  # 对冲回收 30% (有干预时)
            "cash": 0.0,
        },
        "expected_portfolio_dd": -0.139,
        "expected_with_intervention": -0.08,
        "limit": -0.15,
    },
    "v_shock_2020": {
        "name": "2020式冲击",
        "description": "指数 -15%/1个月/V型反弹",
        "index_drop_pct": -0.15,
        "duration_months": 1,
        "shape": "V",
        "asset_impacts": {
            "stock": -0.15,
            "etf": -0.12,
            "quant_neutral": -0.03,
            "options_tail": 0.30,
            "futures_hedge": 0.15,
            "cash": 0.0,
        },
        "expected_portfolio_dd": -0.004,
        "limit": -0.15,
    },
    "liquidity_crisis": {
        "name": "流动性危机",
        "description": "相关性→1, 对冲效率降至30%",
        "index_drop_pct": -0.20,
        "duration_months": 1,
        "shape": "sharp_drop",
        "asset_impacts": {
            "stock": -0.35,
            "etf": -0.30,
            "quant_neutral": -0.20,  # 中性策略失效
            "options_tail": 0.40,  # 对冲效率降至 30%
            "futures_hedge": 0.10,  # 对冲效率降至 30%
            "cash": 0.0,
        },
        "expected_portfolio_dd": -0.186,
        "limit": -0.15,
        "pre_action": "VIX>40 或基差>2% 时提前 1 天降至 40% 仓位, 可将回撤压至 -14%",
        "pre_action_dd_with_intervention": -0.14,
    },
}


# ============================================================
# 资产类别映射 (2026-09-10 D1 修复)
# ============================================================
# 缺陷背景: 原 _run_scenario 只按 strategy/style 里的英文关键词
# ("stock"/"etf"/...) 分支判断, 而 config/positions.json 的真实字段是
# type="ETF"/"STOCK" + style="科技"/"宽基"/"金融"... → 26 个真实持仓
# 全部落到 other、impact_pct=0 → 四个场景 actual_pnl 恒为 0, 报告
# asset_class_pnl={"other": 0.0}, D1 门禁 FAIL。
# 现改为确定性映射, 并把"未分类"显式暴露 (绝不静默记 0 损益)。
ASSET_CLASSES: tuple[str, ...] = (
    "stock",
    "etf",
    "quant_neutral",
    "options_tail",
    "futures_hedge",
    "cash",
)

# strategy 字段关键词 → 资产类别 (顺序即优先级, 具体优先于宽泛)
_STRATEGY_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("option", "期权", "put", "call"), "options_tail"),
    (("future", "期货"), "futures_hedge"),
    (("cash", "现金", "货币"), "cash"),
    (("neutral", "中性", "对冲", "hedge", "arbitrage"), "quant_neutral"),
    (("etf", "lof"), "etf"),
    (("stock", "股票", "equity"), "stock"),
)

# type 字段 → 资产类别 (positions.json 的真实资产类型入口)
_TYPE_MAP: dict[str, str] = {
    "ETF": "etf",
    "LOF": "etf",
    "FUND": "etf",
    "基金": "etf",
    "STOCK": "stock",
    "股票": "stock",
    "OPTION": "options_tail",
    "期权": "options_tail",
    "FUTURE": "futures_hedge",
    "FUTURES": "futures_hedge",
    "期货": "futures_hedge",
    "CASH": "cash",
    "现金": "cash",
}

# style/sector 行业名 → 资产类别 (无 type 时的兜底; 保持既有"科技→stock"语义)
_SECTOR_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("期权",), "options_tail"),
    (("期货",), "futures_hedge"),
    (("现金", "货币"), "cash"),
    (("中性", "对冲"), "quant_neutral"),
    (
        (
            "科技", "制造", "医药", "新能源", "军工", "消费", "金融", "顺周期",
            "成长", "防御", "资源", "宽基", "周期", "地产", "公用", "信息",
        ),
        "stock",
    ),
)


def build_positions_from_positions_json(
    pos_data: dict[str, Any],
) -> tuple[list[dict[str, Any]], float]:
    """把 config/positions.json 的内容映射为压力测试持仓列表 + 组合净值。

    D1 修复 (2026-09-10): 必须透传 ``type``/``style``/``sector`` —— 原 CLI 实现只传
    ``strategy``(=style 行业名如"科技/宽基"), 分类器匹配不到英文资产类别关键词,
    26 个真实持仓全部落 ``other``、``impact_pct=0`` → 四个场景 ``actual_pnl`` 恒为 0。

    Returns:
        ``(positions, portfolio_value)``; positions 仅含 amount>0 的条目。
    """
    raw = pos_data.get("positions") or {}
    meta = pos_data.get("meta") or {}
    try:
        portfolio_value = float(meta.get("total_capital", 5_000_000) or 0)
    except (TypeError, ValueError):
        portfolio_value = 0.0
    if portfolio_value <= 0:
        portfolio_value = 5_000_000.0

    positions: list[dict[str, Any]] = []
    for code, p in raw.items():
        if not isinstance(p, dict):
            continue
        try:
            amount = float(p.get("amount", 0) or 0)
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        style = p.get("style") or p.get("sector") or ""
        positions.append(
            {
                "code": code,
                "name": p.get("name", code),
                "amount": amount,
                "strategy": style,
                "type": p.get("type"),
                "style": style,
                "sector": p.get("sector"),
            }
        )
    return positions, portfolio_value


def classify_asset_class(pos: dict[str, Any]) -> str:
    """把单个持仓映射到压力测试资产类别 (键与 asset_impacts 一一对应)。

    优先级: 显式 asset_class > strategy 关键词 > type > style/sector 行业名 >
    名称含 "ETF"。全部未命中返回 "other" —— 调用方必须告警, 不得静默记 0 损益。
    """
    explicit = str(pos.get("asset_class") or "").strip()
    if explicit in ASSET_CLASSES:
        return explicit

    strategy = str(pos.get("strategy") or "").strip().lower()
    if strategy:
        for keys, cls in _STRATEGY_KEYWORDS:
            if any(k in strategy for k in keys):
                return cls

    ptype = str(pos.get("type") or "").strip().upper()
    if ptype in _TYPE_MAP:
        return _TYPE_MAP[ptype]

    sector = str(pos.get("style") or pos.get("sector") or "").strip()
    if sector:
        for keys, cls in _SECTOR_KEYWORDS:
            if any(k in sector for k in keys):
                return cls

    if "etf" in str(pos.get("name") or "").lower():
        return "etf"

    return "other"


class StressTestRunner:
    """压力测试自动化执行器"""

    def __init__(self):
        REPORT_DIR.mkdir(parents=True, exist_ok=True)

    def run_all_scenarios(
        self,
        positions: list[dict[str, Any]],
        portfolio_value: float = 5_000_000,
        with_intervention: bool = True,
        is_simulated: bool = False,
    ) -> dict[str, Any]:
        """运行所有压力测试场景

        Args:
            positions: 持仓列表 [{"code", "name", "amount", "style", "strategy"}, ...]
            portfolio_value: 组合净值
            with_intervention: 是否包含干预措施 (对冲+减仓)
            is_simulated: 是否模拟持仓。True 时报告写独立文件名并在报告中标记,
                供 assert_data_validity D1 区分真实/模拟数据。

        Returns:
            {
                "timestamp": "...",
                "portfolio_value": 5000000,
                "scenarios": {...},
                "all_pass": True/False,
                "worst_scenario": "...",
                "report_path": "..."
            }
        """
        positions_amount = sum(
            float(p.get("amount", p.get("market_value", 0)) or 0) for p in positions
        )
        results: dict[str, Any] = {
            "timestamp": now_bj().isoformat(),
            "portfolio_value": portfolio_value,
            "with_intervention": with_intervention,
            "is_simulated": is_simulated,
            "positions_count": len(positions),
            "positions_amount": positions_amount,
            "scope_note": (
                "覆盖 positions.json 的 positions (股票/ETF 持仓)。hedge_positions 的"
                "期权/期货对冲腿未计入: 场景 asset_impacts 的 options_tail/futures_hedge"
                "是「多头保护腿」口径, 直接套用空头备兑 Call 会得出错误符号, 故不做静默近似。"
                "这意味着实际回撤反映的是「未含对冲」的证券端, 与含对冲假设的"
                "expected_portfolio_dd 口径不同, 比较时须留意。"
            ),
            "scenarios": {},
        }

        all_pass = True
        worst_dd = 0.0
        worst_scenario = ""

        for scenario_id, scenario_def in STRESS_SCENARIOS.items():
            scenario_result = self._run_scenario(
                scenario_id, scenario_def, positions, portfolio_value, with_intervention
            )
            results["scenarios"][scenario_id] = scenario_result

            actual_dd = scenario_result["actual_portfolio_dd"]
            limit = scenario_def["limit"]

            scenario_result["pass"] = actual_dd >= limit
            if actual_dd < limit:
                all_pass = False

            if actual_dd < worst_dd:
                worst_dd = actual_dd
                worst_scenario = scenario_id

        results["all_pass"] = all_pass
        results["worst_scenario"] = worst_scenario
        results["worst_dd"] = worst_dd
        results["unclassified_amount"] = max(
            (s.get("unclassified_amount", 0.0) for s in results["scenarios"].values()),
            default=0.0,
        )

        # 保存报告
        report_path = self._save_report(results)
        results["report_path"] = str(report_path)

        return results

    def _run_scenario(
        self,
        scenario_id: str,
        scenario_def: dict,
        positions: list[dict],
        portfolio_value: float,
        with_intervention: bool,
    ) -> dict[str, Any]:
        """运行单个压力测试场景"""
        impacts = scenario_def["asset_impacts"]

        # 计算各资产类别的影响
        asset_class_pnl: dict[str, float] = {}
        unclassified_amount = 0.0
        total_pnl = 0.0

        for pos in positions:
            asset_class = classify_asset_class(pos)
            amount = float(pos.get("amount", pos.get("market_value", 0)) or 0)
            impact_pct = impacts.get(asset_class, 0.0)

            pos_pnl = amount * impact_pct
            total_pnl += pos_pnl

            # D1 修复 (2026-09-10): 未分类持仓显式暴露, 绝不静默记 0 损益
            if asset_class == "other":
                unclassified_amount += amount

            if asset_class not in asset_class_pnl:
                asset_class_pnl[asset_class] = 0.0
            asset_class_pnl[asset_class] += pos_pnl

        if unclassified_amount > 0:
            logger.warning(
                "[StressTest] %s: %.0f 元持仓未能映射到资产类别 (type/style 未识别), "
                "该部分按 0 损益计入 — 请补 classify_asset_class 映射表",
                scenario_id,
                unclassified_amount,
            )

        # 干预措施影响 (有干预时, 慢熊和流动性危机改善)
        intervention_benefit = 0.0
        if with_intervention:
            if scenario_id == "slow_bear_2018":
                # 有干预: 对冲回收 30%, 实际减仓 20%
                intervention_benefit = abs(total_pnl) * 0.42  # -13.9% → -8.0%
            elif scenario_id == "liquidity_crisis":
                # 有干预: 提前降至 40% 仓位
                intervention_benefit = abs(total_pnl) * 0.25  # -18.6% → -14%

        actual_pnl = total_pnl + intervention_benefit
        actual_dd = actual_pnl / portfolio_value if portfolio_value > 0 else 0

        expected_dd = scenario_def.get("expected_portfolio_dd", 0)
        if with_intervention:
            if scenario_id == "slow_bear_2018":
                expected_dd = scenario_def.get(
                    "expected_with_intervention", expected_dd
                )
            elif scenario_id == "liquidity_crisis":
                expected_dd = scenario_def.get(
                    "pre_action_dd_with_intervention", expected_dd
                )

        return {
            "scenario_id": scenario_id,
            "scenario_name": scenario_def["name"],
            "description": scenario_def["description"],
            "index_drop_pct": scenario_def["index_drop_pct"],
            "duration_months": scenario_def["duration_months"],
            "shape": scenario_def["shape"],
            "total_pnl_no_intervention": total_pnl,
            "intervention_benefit": intervention_benefit,
            "actual_pnl": actual_pnl,
            "actual_portfolio_dd": actual_dd,
            "expected_portfolio_dd": expected_dd,
            "limit": scenario_def["limit"],
            "pass": actual_dd >= scenario_def["limit"],
            "asset_class_pnl": asset_class_pnl,
            "unclassified_amount": unclassified_amount,
            "with_intervention": with_intervention,
            "pre_action": scenario_def.get("pre_action"),
        }

    def _save_report(self, results: dict) -> Path:
        """保存压力测试报告

        模拟持仓报告使用独立文件名 (stress_test_SIMULATED_*.json), 避免覆盖真实报告,
        也便于 assert_data_validity D1 通过 is_simulated 字段区分真实/模拟数据。
        """
        date_str = now_bj().strftime("%Y%m%d")
        prefix = (
            "stress_test_SIMULATED"
            if results.get("is_simulated", False)
            else "stress_test"
        )
        report_path = REPORT_DIR / f"{prefix}_{date_str}.json"
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"压力测试报告已保存: {report_path}")
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.error(f"保存压力测试报告失败: {e}")
        return report_path


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    # notify 导入容错: 直接运行脚本时 utils 可能不在 sys.path, 降级为 logger 告警 (观测路径 fail-open)
    try:
        from utils.notify import send_alert
    except ImportError:
        try:
            from notify import send_alert
        except ImportError:

            def send_alert(title: str, content: str, level: str = "warning") -> None:  # type: ignore
                logger.error(f"[ALERT-{level}] {title}: {content}")

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="压力测试自动化")
    parser.add_argument(
        "--portfolio",
        type=float,
        default=None,
        help="组合净值 (默认从 positions.json 读取)",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="使用模拟持仓测试 (默认使用 positions.json 真实持仓)",
    )
    args = parser.parse_args()

    runner = StressTestRunner()

    if args.simulate:
        # 模拟持仓: 显式标记 is_simulated, 供 assert_data_validity D1 区分真实/模拟
        positions = _SIMULATED_POSITIONS
        portfolio_value = args.portfolio or 5_000_000
        result = runner.run_all_scenarios(
            positions, portfolio_value, with_intervention=True, is_simulated=True
        )
        logger.warning("[SIMULATE] 使用模拟持仓, 报告不反映真实组合风险")
    else:
        # 真实持仓: 从 config/positions.json 加载并映射为压力测试格式
        # 失败友好原则: 决策/数据完整性路径必须 fail-close, 绝不在非 --simulate 模式
        # 静默回退到模拟持仓 (否则会产出"假真实"报告骗过 D1 门禁)。
        positions_path = BASE_DIR / "config" / "positions.json"
        try:
            with open(positions_path, encoding="utf-8") as f:
                pos_data = json.load(f)
            positions, default_value = build_positions_from_positions_json(pos_data)
            portfolio_value = args.portfolio or default_value
            if not positions:
                # 空仓/初始化态: fail-close 阻断, 不产出假真实报告
                msg = (
                    "positions.json 无有效持仓 (amount<=0), 拒绝以模拟持仓冒充真实报告"
                )
                logger.error(msg)
                send_alert(title="压力测试数据缺失", content=msg, level="critical")
                sys.exit(1)
            logger.info(
                f"已从 positions.json 加载 {len(positions)} 个真实持仓, 组合净值 {portfolio_value:,.0f}"
            )
            result = runner.run_all_scenarios(
                positions, portfolio_value, with_intervention=True, is_simulated=False
            )
            # D1 修复: 未分类持仓不得静默 (观测路径 fail-open, 但必须告警留痕)
            if result.get("unclassified_amount", 0) > 0:
                msg = (
                    f"{result['unclassified_amount']:,.0f} 元持仓未能映射到资产类别, "
                    "已在报告中按 0 损益计入 — 请补 classify_asset_class 映射表"
                )
                logger.error(msg)
                send_alert(title="压力测试持仓分类不全", content=msg, level="warning")
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 加载失败: fail-close 阻断, 不静默回退
            msg = f"加载 positions.json 失败 ({e}), 拒绝以模拟持仓冒充真实报告"
            logger.error(msg)
            send_alert(title="压力测试数据加载失败", content=msg, level="critical")
            sys.exit(1)

        logger.info("\n" + "=" * 60)
        logger.info("压力测试结果")
        logger.info("=" * 60)
        for sid, sres in result["scenarios"].items():
            status = "✅ 通过" if sres["pass"] else "❌ 超限"
            logger.info(f"\n{sres['scenario_name']} ({sid})")
            logger.info(f"  指数跌幅: {sres['index_drop_pct']:.0%}")
            logger.info(f"  预期回撤: {sres['expected_portfolio_dd']:.1%}")
            logger.info(f"  实际回撤: {sres['actual_portfolio_dd']:.1%}")
            logger.info(f"  限额:     {sres['limit']:.1%}")
            logger.info(f"  状态:     {status}")
            if sres.get("pre_action"):
                logger.info(f"  预警:     {sres['pre_action']}")

        logger.info(f"\n{'=' * 60}")
        logger.info(f"总体: {'✅ 全部通过' if result['all_pass'] else '❌ 有场景超限'}")
        logger.info(
            f"最差场景: {result['worst_scenario']} (回撤 {result['worst_dd']:.1%})"
        )
        logger.info(f"报告: {result['report_path']}")
