# -*- coding: utf-8 -*-
"""
对冲引擎 v5.9 — 多指数Beta加权 + 组合自触发 + 成本意识优化

v5.9 核心改进（基于2021-2026回测发现）:
1. 多指数Beta加权对冲分配 — IC/IM/IF按Beta比例分配，替代纯IF
2. 组合自身波动率触发 — 不再依赖CSI300市场状态判断
3. 成本效益阈值 — 仅在对冲收益预期 > 成本*1.5时激活
4. 极端行情尾部保护模式 — 默认模式，仅在vol>28%或DD>12%时触发

数据源: iFinD MCP → Wind MCP → Sina/AKShare (免费回退)
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("hedge_engine")

# ============================================================
# B3.3: 期货价格获取层已抽取到 src/data/futures_prices.py
# 双路径导入: 支持 src.hedging (正常包) 和 hedging (测试 sys.path) 两种加载方式
# ============================================================
try:
    from ..data.futures_prices import (  # noqa: E402
        DEFAULT_FUTURES_PRICES,  # noqa: F401
        get_live_futures_prices,  # noqa: F401
    )
except (ImportError, ValueError):  # pragma: no cover — 测试 sys.path 兼容路径
    pass

# ============================================================
# B-4.6: 指数规格常量已抽取到 hedging/index_specs.py
# 从 config/index_specs.yaml 加载 (失败回退到硬编码默认值)
# 双路径导入: 支持 src.hedging (正常包) 和 hedging (测试 sys.path) 两种加载方式
# ============================================================
try:
    from .index_specs import (  # noqa: E402
        ETF_OPTIONS_SPECS,  # noqa: F401
        INDEX_FUTURES_SPECS,
        INDEX_WEIGHTS_CSI300,  # noqa: F401
        INDEX_WEIGHTS_CSI500,  # noqa: F401
    )
except (ImportError, ValueError):  # pragma: no cover — 测试 sys.path 兼容路径
    from index_specs import (  # noqa: E402
        INDEX_FUTURES_SPECS,
    )


# ============================================================
# B3.3: HedgeType / HedgeSignalStrength / HedgeRecommendation
# 已抽取到 hedge_types.py (共享类型, 避免循环导入与重复定义)
# ============================================================
from .hedge_types import (  # noqa: E402
    HedgeRecommendation,
    HedgeSignalStrength,
)

# ============================================================
# B3.3: 风险评估层已抽取到 src/risk/portfolio_risk_assessor.py
# 双路径导入: 支持 src.hedging (正常包) 和 hedging (测试 sys.path) 两种加载方式
# ============================================================
try:
    from ..risk.portfolio_risk_assessor import (  # noqa: E402
        CORRELATION_WARN,  # noqa: F401
        DEFAULT_BETAS,  # noqa: F401
        FALLBACK_PRICE_MAP,  # noqa: F401
        FIXED_INCOME_TYPES,  # noqa: F401
        HISTORICAL_STRESS_SCENARIOS,  # noqa: F401
        MRC_LIMIT,  # noqa: F401
        SECTOR_LIMIT,  # noqa: F401
        SECTOR_MAP,  # noqa: F401
        PortfolioRisk,
    )
    from ..risk.portfolio_risk_assessor import (
        assess_portfolio_risk as _assessor_assess_portfolio_risk,
    )
    from ..risk.portfolio_risk_assessor import (
        check_sector_concentration as _assessor_check_sector_concentration,
    )
    from ..risk.portfolio_risk_assessor import (
        compute_correlation_matrix as _assessor_compute_correlation_matrix,
    )
    from ..risk.portfolio_risk_assessor import (
        compute_expected_shortfall as _assessor_compute_expected_shortfall,
    )
    from ..risk.portfolio_risk_assessor import (
        compute_mrc as _assessor_compute_mrc,
    )
    from ..risk.portfolio_risk_assessor import (
        compute_portfolio_vol_cov as _assessor_compute_portfolio_vol_cov,
    )
    from ..risk.portfolio_risk_assessor import (
        compute_weighted_beta as _assessor_compute_weighted_beta,
    )
    from ..risk.portfolio_risk_assessor import (
        estimate_default_price as _assessor_estimate_default_price,
    )
    from ..risk.portfolio_risk_assessor import (
        monitor_daily_correlation as _assessor_monitor_daily_correlation,
    )
    from ..risk.portfolio_risk_assessor import (
        run_historical_stress_tests as _assessor_run_historical_stress_tests,
    )
except (ImportError, ValueError):  # pragma: no cover — 测试 sys.path 兼容路径
    from risk.portfolio_risk_assessor import (  # noqa: E402
        PortfolioRisk,
    )
    from risk.portfolio_risk_assessor import (
        assess_portfolio_risk as _assessor_assess_portfolio_risk,
    )
    from risk.portfolio_risk_assessor import (
        check_sector_concentration as _assessor_check_sector_concentration,
    )
    from risk.portfolio_risk_assessor import (
        compute_correlation_matrix as _assessor_compute_correlation_matrix,
    )
    from risk.portfolio_risk_assessor import (
        compute_expected_shortfall as _assessor_compute_expected_shortfall,
    )
    from risk.portfolio_risk_assessor import (
        compute_mrc as _assessor_compute_mrc,
    )
    from risk.portfolio_risk_assessor import (
        compute_portfolio_vol_cov as _assessor_compute_portfolio_vol_cov,
    )
    from risk.portfolio_risk_assessor import (
        compute_weighted_beta as _assessor_compute_weighted_beta,
    )
    from risk.portfolio_risk_assessor import (
        estimate_default_price as _assessor_estimate_default_price,
    )
    from risk.portfolio_risk_assessor import (
        monitor_daily_correlation as _assessor_monitor_daily_correlation,
    )
    from risk.portfolio_risk_assessor import (
        run_historical_stress_tests as _assessor_run_historical_stress_tests,
    )

# ============================================================
# B3.3: 对冲策略执行层已抽取到 src/hedging/hedge_strategy_executor.py
# ============================================================
from .hedge_strategy_executor import (
    compute_optimal_hedge_ratio as _executor_compute_optimal_hedge_ratio,
)
from .hedge_strategy_executor import (  # noqa: E402
    determine_hedge_signal_strength as _executor_determine_hedge_signal_strength,
)
from .hedge_strategy_executor import (
    generate_futures_hedge as _executor_generate_futures_hedge,
)
from .hedge_strategy_executor import (
    generate_hedge_plan as _executor_generate_hedge_plan,
)
from .hedge_strategy_executor import (
    generate_hedge_reason as _executor_generate_hedge_reason,
)
from .hedge_strategy_executor import (
    generate_options_hedge as _executor_generate_options_hedge,
)

# 注: HedgeRecommendation 已从 hedge_types.py 导入 (见上方), 此处不再重复定义


# B3.3: DEFAULT_FUTURES_PRICES / FALLBACK_PRICES_UPDATED / DEFAULT_INDEX_PRICES
# 已迁移到 src/data/futures_prices.py

VOLATILITY_TARGET_ANNUAL = 0.18

# ── 对冲成本参数 ──
HEDGE_ROLL_COST_ANNUAL = 0.025  # 年化展期成本(基差+交易费)
HEDGE_MARGIN_OPP_COST = 0.020  # 保证金机会成本(按无风险利率)


# ── v5.9 新增：多指数Beta分配权重 ──
# 指数优先级的判定基于组合在各指数的暴露度
INDEX_ALLOCATION_ORDER = ["IC", "IM", "IF"]  # 中证500优先(匹配中小盘成长), 中证1000次之

# ── v5.9 新增：组合自触发阈值 ──
PORTFOLIO_TAIL_HEDGE_TRIGGERS = {
    "vol_trigger": 0.28,  # 年化波动率>28%触发
    "dd_trigger": 0.12,  # 60日最大回撤>12%触发
    "min_hedge_ratio": 0.25,  # 触发后最小对冲比率
    "max_hedge_ratio": 0.50,  # 触发后最大对冲比率
}

# ── v5.9 新增：成本效益阈值 ──
COST_BENEFIT_THRESHOLD = 1.5  # 预期对冲收益必须 > 对冲成本 * 1.5 才激活


# B3.3: 期货价格获取函数 (_exec_ifind, fetch_futures_prices_from_*, get_live_futures_prices)
# 已迁移到 src/data/futures_prices.py, 通过文件顶部 import 语句导入


# ============================================================
# HedgeEngine v5.9
# ============================================================


class HedgeEngine:
    """对冲引擎核心类 v5.9

    核心改进:
    1. 多指数Beta加权对冲分配 — IC/IM优先, IF辅助
    2. 组合自触发尾部对冲 — 不依赖外部市场状态
    3. 成本效益过滤 — 对冲期望收益必须 > 1.5倍成本
    """

    def __init__(self, portfolio_value: float = 1_000_000):
        self.portfolio_value = portfolio_value
        self._price_cache: Dict[str, float] = {}
        self._beta_cache: Dict[str, float] = {}

    # ── 风险评估 ──

    def assess_portfolio_risk(
        self,
        positions: Dict[str, Dict[str, Any]],
        prices: Dict[str, float],
        historical_returns: Optional[Dict[str, List[float]]] = None,
        cash: float = 0.0,
    ) -> PortfolioRisk:
        """[B3.3 委托] 评估组合风险 — 委托到 portfolio_risk_assessor 模块

        v5.10 改进:
        - 组合VaR = sqrt(w^T * Σ * w) * z * total_value
        - 当historical_returns可用时, 从历史数据推算协方差矩阵
        - 无历史数据时回退到独立假设并标注风险低估警告
        """
        return _assessor_assess_portfolio_risk(
            positions=positions,
            prices=prices,
            historical_returns=historical_returns,
            cash=cash,
        )

    # v5.10: 类级别Beta常量 — 供压力测试和组合Beta计算复用
    DEFAULT_BETAS = {
        "300308": (1.35, 1.50, 1.60, 1.20),
        "688041": (1.40, 1.55, 1.70, 1.25),
        "002371": (1.25, 1.40, 1.50, 1.15),
        "688981": (1.30, 1.45, 1.55, 1.20),
        "300750": (1.20, 1.35, 1.45, 1.10),
        "000425": (1.05, 1.15, 1.25, 0.95),
        "601088": (0.85, 0.80, 0.75, 0.90),
        "600219": (0.90, 0.95, 1.05, 0.85),
        "600019": (0.95, 1.00, 1.10, 0.90),
        "518880": (0.40, 0.35, 0.30, 0.45),
        "000792": (0.80, 0.90, 1.00, 0.75),
        "600276": (0.75, 0.70, 0.65, 0.80),
        "603259": (0.90, 0.95, 1.00, 0.85),
        "002422": (0.70, 0.65, 0.60, 0.75),
    }

    # v5.10 P0-6: 板块映射 — 集中度监控
    SECTOR_MAP = {
        "300308": "高端制造",
        "688041": "高端制造",
        "002371": "高端制造",
        "688981": "高端制造",
        "300750": "高端制造",
        "000425": "高端制造",
        "601088": "顺周期",
        "600219": "顺周期",
        "600019": "顺周期",
        "518880": "黄金ETF",
        "000792": "资源",
        "600276": "防御",
        "603259": "防御",
        "002422": "防御",
        "600900": "防御",  # 长江电力
        "511010": "国债ETF",  # 固收敞口
    }

    # 固收/国债类不计入股票板块集中度
    FIXED_INCOME_TYPES = {"国债ETF"}

    SECTOR_LIMIT = 0.35  # 单一板块上限35% (P0-6)
    MRC_LIMIT = 0.25  # 单标的边际风险贡献上限25%
    CORRELATION_WARN = 0.70  # 平均相关性 > 0.7 触发预警 (P0-7)

    def _compute_weighted_beta(self, weights: Dict[str, float], index: str) -> float:
        """[B3.3 委托] 计算加权Beta — 委托到 portfolio_risk_assessor"""
        return _assessor_compute_weighted_beta(weights, index)

    # ── v5.10 P0-8: 历史极端压力测试 ──

    HISTORICAL_STRESS_SCENARIOS = {
        "2015股灾 (沪深300 -45%)": {
            "csi300": -0.45,
            "csi500": -0.50,
            "csi1000": -0.50,
            "sse50": -0.40,
            "gold": 0.02,
            "sector": "全面崩盘, 流动性枯竭, 千股停牌",
        },
        "2016熔断 (沪深300 -25%)": {
            "csi300": -0.25,
            "csi500": -0.30,
            "csi1000": -0.28,
            "sse50": -0.22,
            "gold": 0.01,
            "sector": "指数熔断, 恐慌抛售, 两日触发2次熔断",
        },
        "2018贸易战 (沪深300 -32%)": {
            "csi300": -0.32,
            "csi500": -0.35,
            "csi1000": -0.38,
            "sse50": -0.28,
            "gold": 0.04,
            "sector": "中美贸易摩擦升级, 科技股重挫, 人民币贬值",
        },
        "2020疫情闪崩 (沪深300 -16%)": {
            "csi300": -0.16,
            "csi500": -0.15,
            "csi1000": -0.14,
            "sse50": -0.14,
            "gold": 0.06,
            "sector": "新冠疫情全球爆发, 节后首日3000股跌停",
        },
        "2024国庆后暴跌 (沪深300 -20%)": {
            "csi300": -0.20,
            "csi500": -0.22,
            "csi1000": -0.25,
            "sse50": -0.18,
            "gold": 0.01,
            "sector": "政策宽松预期逆转, 前期过热回调",
        },
        "极端尾部事件 (1% VaR, -40%)": {
            "csi300": -0.40,
            "csi500": -0.45,
            "csi1000": -0.50,
            "sse50": -0.35,
            "gold": 0.08,
            "sector": "复合危机: 流动性枯竭+信用违约+汇率贬值叠加",
        },
    }

    def run_historical_stress_tests(
        self,
        positions: Dict[str, Dict[str, Any]],
        prices: Dict[str, float],
    ) -> Dict[str, Dict[str, Any]]:
        """[B3.3 委托] 历史极端情景压力测试 — 委托到 portfolio_risk_assessor"""
        return _assessor_run_historical_stress_tests(positions, prices)

    def _compute_portfolio_vol(self, weights, returns) -> float:
        """旧版单资产波动率估算 — 保留向后兼容"""
        return 0.015

    def _estimate_default_price(self, code: str) -> float:
        """[B3.3 委托] 兜底价格 — 委托到 portfolio_risk_assessor"""
        return _assessor_estimate_default_price(code)

    def _compute_portfolio_vol_cov(
        self,
        weights: Dict[str, float],
        historical_returns: Dict[str, List[float]],
        codes: List[str],
    ) -> float:
        """[B3.3 委托] 协方差矩阵组合波动率 — 委托到 portfolio_risk_assessor"""
        return _assessor_compute_portfolio_vol_cov(weights, historical_returns, codes)

    def _compute_expected_shortfall(
        self,
        weights: Dict[str, float],
        historical_returns: Dict[str, List[float]],
        codes: List[str],
        total_value: float,
        confidence: float = 0.95,
    ) -> float:
        """[B3.3 委托] Expected Shortfall — 委托到 portfolio_risk_assessor"""
        return _assessor_compute_expected_shortfall(
            weights, historical_returns, codes, total_value, confidence
        )

    def _compute_mrc(
        self,
        weights: Dict[str, float],
        historical_returns: Dict[str, List[float]],
        codes: List[str],
        portfolio_vol: float,
    ) -> Dict[str, float]:
        """[B3.3 委托] 边际风险贡献 — 委托到 portfolio_risk_assessor"""
        return _assessor_compute_mrc(weights, historical_returns, codes, portfolio_vol)

    # ── v5.9 信号强度（组合自触发优先） ──

    def determine_hedge_signal_strength(
        self,
        risk: PortfolioRisk,
        market_signals: Optional[Dict[str, Any]] = None,
        portfolio_volatility: Optional[float] = None,
        portfolio_drawdown_60d: Optional[float] = None,
    ) -> Tuple[HedgeSignalStrength, float]:
        """[B3.3 委托] 五因子信号强度评估 — 委托到 hedge_strategy_executor"""
        return _executor_determine_hedge_signal_strength(
            risk, market_signals, portfolio_volatility, portfolio_drawdown_60d
        )

    def compute_optimal_hedge_ratio(
        self,
        risk: PortfolioRisk,
        hedge_strength: HedgeSignalStrength,
        method: str = "min_variance",
        portfolio_volatility: Optional[float] = None,
        portfolio_drawdown_60d: Optional[float] = None,
    ) -> float:
        """[B3.3 委托] 最优对冲比率 — 委托到 hedge_strategy_executor"""
        return _executor_compute_optimal_hedge_ratio(
            risk, hedge_strength, method, portfolio_volatility, portfolio_drawdown_60d
        )

    # ── v5.9 多指数Beta加权期货对冲 ──

    def generate_futures_hedge(
        self,
        risk: PortfolioRisk,
        hedge_ratio: float,
        futures_prices: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """[B3.3 委托] 多指数Beta加权对冲方案 — 委托到 hedge_strategy_executor"""
        return _executor_generate_futures_hedge(risk, hedge_ratio, futures_prices)

    # ── 期权对冲（保留原实现） ──

    def generate_options_hedge(
        self,
        risk: PortfolioRisk,
        hedge_ratio: float,
        options_data: Optional[Dict[str, Any]] = None,
        strategy: str = "protective_put",
    ) -> Dict[str, Any]:
        """[B3.3 委托] 期权对冲方案 — 委托到 hedge_strategy_executor"""
        return _executor_generate_options_hedge(risk, hedge_ratio, options_data, strategy)

    def generate_hedge_plan(
        self,
        risk: PortfolioRisk,
        market_signals: Optional[Dict[str, Any]] = None,
        futures_prices: Optional[Dict[str, float]] = None,
        prefer_options: bool = False,
        portfolio_volatility: Optional[float] = None,
        portfolio_drawdown_60d: Optional[float] = None,
        positions: Optional[Dict[str, Dict[str, Any]]] = None,
        prices: Optional[Dict[str, float]] = None,
    ) -> HedgeRecommendation:
        """[B3.3 委托] 完整对冲方案 — 委托到 hedge_strategy_executor

        v5.10 完整对冲方案 — 组合自触发增强 + P0-8压力测试
        """
        return _executor_generate_hedge_plan(
            risk=risk,
            market_signals=market_signals,
            futures_prices=futures_prices,
            prefer_options=prefer_options,
            portfolio_volatility=portfolio_volatility,
            portfolio_drawdown_60d=portfolio_drawdown_60d,
            positions=positions,
            prices=prices,
        )

    def _generate_hedge_reason(self, risk, hedge_ratio, contracts) -> str:
        """[B3.3 委托] 生成对冲理由 — 委托到 hedge_strategy_executor"""
        return _executor_generate_hedge_reason(risk, hedge_ratio, contracts)

    def format_report(self, recommendation: HedgeRecommendation) -> str:
        lines = []
        lines.append("=" * 70)
        lines.append("  Hedge Engine v5.9 — 对冲策略报告")
        lines.append("=" * 70)
        lines.append(f"  生成时间: {recommendation.timestamp[:19]}")
        lines.append(f"  对冲类型: {recommendation.hedge_type.value}")
        lines.append(f"  信号强度: {recommendation.strength.name} (紧急度={recommendation.urgency_score:.2f})")
        lines.append(f"  对冲比率: {recommendation.hedge_ratio * 100:.0f}%")
        lines.append(f"  预期对冲后Beta: {recommendation.expected_beta_after:.2f}")
        lines.append(f"  预期回撤减少: {recommendation.expected_drawdown_reduce * 100:.0f}%")
        lines.append("-" * 70)

        if recommendation.futures_contracts:
            lines.append("\n  期货对冲方案 (多指数Beta加权)")
            lines.append("  " + "-" * 50)
            total_margin = 0
            for code, n in recommendation.futures_contracts.items():
                notional = recommendation.futures_notional.get(code, 0)
                margin = recommendation.futures_margin.get(code, 0)
                total_margin += margin
                spec = INDEX_FUTURES_SPECS.get(code, {})
                lines.append(f"    {code} {spec.get('name', '')}: 做空 {n} 手")
                lines.append(f"      名义价值: {notional:,.0f} | 保证金: {margin:,.0f}")
            lines.append(f"\n    总保证金需求: {total_margin:,.0f}")

        if recommendation.options_contracts:
            lines.append("\n  期权对冲方案")
            lines.append("  " + "-" * 50)
            for opt in recommendation.options_contracts:
                lines.append(f"    标的: {opt.get('underlying', '')}")
                lines.append(f"    策略: {opt.get('strategy', '')}")
                lines.append(f"    合约数: {opt.get('contracts', 0)} 张")
                lines.append(f"    预估成本: {opt.get('cost', 0):,.0f}")

        lines.append("\n  对冲逻辑")
        lines.append(f"    {recommendation.reasoning}")

        # v5.10 P0-6: 集中度与板块预警
        if recommendation.sector_warnings or recommendation.mrc_warnings:
            lines.append("\n" + "-" * 70)
            lines.append("  集中度风险监控 (P0-6)")
            lines.append("  " + "-" * 50)
            if recommendation.sector_warnings:
                for w in recommendation.sector_warnings:
                    lines.append(f"  ⚠️  {w}")
            if recommendation.mrc_warnings:
                for w in recommendation.mrc_warnings:
                    lines.append(f"  ⚠️  {w}")
            if not recommendation.sector_warnings and not recommendation.mrc_warnings:
                lines.append("  ✅ 板块和单标的集中度均在安全范围")

        # v5.10 P0-7: 相关性预警
        if recommendation.correlation_warning:
            lines.append("\n" + "-" * 70)
            lines.append("  相关性风险监控 (P0-7)")
            lines.append("  " + "-" * 50)
            lines.append(f"  ⚠️  {recommendation.correlation_warning}")

        # v5.10 P0-8: 历史极端压力测试
        if recommendation.stress_tests:
            lines.append("\n" + "-" * 70)
            lines.append("  历史极端压力测试 (P0-8 修复)")
            lines.append("  " + "-" * 50)
            breach_count = 0
            for scenario, result in recommendation.stress_tests.items():
                breach = "❌ 突破15%回撤上限" if result["breaches_limit"] else "✅ 未触发"
                if result["breaches_limit"]:
                    breach_count += 1
                dd = result["drawdown_pct"]
                loss = result["estimated_loss"]
                lines.append(f"  {scenario}")
                lines.append(f"    预估回撤: {dd:.1f}% | 损失: {loss:,.0f} | {breach}")
                lines.append(f"    情景: {result['sector_impact']}")
            if breach_count > 0:
                lines.append(f"\n  ⚠️  {breach_count}/6 个历史情景突破15%回撤上限，强烈建议启用尾部保护")
            else:
                lines.append("\n  全部历史情景均未突破15%回撤上限，尾部保护可选")

        lines.append("\n" + "=" * 70)
        lines.append("  以上分析仅供参考，不构成投资建议。")
        lines.append("=" * 70)

        return "\n".join(lines)

    def get_hedge_signal_for_fusion(self, portfolio_code: str = "portfolio") -> Dict[str, Any]:
        return {
            "code": portfolio_code,
            "source": "hedge_engine_v59",
            "action": "HOLD",
            "score": 0.5,
            "confidence": 0.3,
            "reason": "对冲引擎v5.9已初始化，等待风险评估",
            "timestamp": datetime.now().isoformat(),
        }

    def compute_correlation_matrix(
        self,
        historical_returns: Dict[str, List[float]],
        codes: List[str],
        lookback_days: int = 60,
    ) -> Dict[str, Dict[str, float]]:
        """[B3.3 委托] 计算组合内资产相关性矩阵 (P0-7修复) — 委托到 portfolio_risk_assessor

        返回N×N的相关性矩阵，用于实盘风控链路监控。
        当滚动60日平均相关系数>0.7时触发集中度预警。
        """
        return _assessor_compute_correlation_matrix(historical_returns, codes, lookback_days)

    def monitor_daily_correlation(
        self,
        positions: Dict[str, Dict[str, Any]],
        historical_returns: Dict[str, List[float]],
        alert_threshold: float = 0.7,
        lookback_days: int = 60,
    ) -> Dict[str, Any]:
        """[B3.3 委托] 每日相关性监控 (P0-7修复核心) — 委托到 portfolio_risk_assessor

        监控组合内资产间的相关性变化，当滚动60日平均相关系数>0.7时触发预警。
        同时监控"相关性变化率"，识别危机中所有资产趋向1的情况。
        """
        return _assessor_monitor_daily_correlation(
            positions, historical_returns, alert_threshold, lookback_days
        )

    def check_sector_concentration(
        self,
        positions: Dict[str, Dict[str, Any]],
        historical_returns: Dict[str, List[float]],
        lookback_days: int = 60,
    ) -> Dict[str, Any]:
        """[B3.3 委托] 板块集中度风险检查 (P0-6/P0-7联动) — 委托到 portfolio_risk_assessor

        检查同一板块内标的的相关性是否过高，识别"伪分散化"风险。
        """
        return _assessor_check_sector_concentration(positions, historical_returns, lookback_days)


# ── 便捷函数 ──


def get_hedge_engine(portfolio_value: Optional[float] = None) -> HedgeEngine:
    return HedgeEngine(portfolio_value=portfolio_value or 1_000_000)


def calculate_portfolio_beta(
    positions: Dict[str, Dict[str, Any]],
    prices: Dict[str, float],
) -> Dict[str, float]:
    engine = HedgeEngine()
    risk = engine.assess_portfolio_risk(positions, prices)
    return {
        "beta_csi300": risk.beta_csi300,
        "beta_csi500": risk.beta_csi500,
        "beta_csi1000": risk.beta_csi1000,
        "beta_sse50": risk.beta_sse50,
        "concentration_hhi": risk.concentration_risk,
        "var_95_daily": risk.var_95_daily,
    }
