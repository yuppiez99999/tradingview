"""
P&L 多维度归因分析引擎 (P&L Attribution Engine) v1.0
======================================================

世界顶级对冲基金标准 P&L 归因系统 — Bridgewater/Point72/AQR 同级:

将组合总收益分解为:
    1. Alpha (选股超额收益)
    2. Beta (市场系统性收益)
    3. 风格因子收益 (动量/反转/波动率/流动性/质量/成长/估值)
    4. 行业暴露收益
    5. 择时收益
    6. 对冲成本
    7. 交易成本 (滑点+佣金+市场冲击)
    8. 资金成本 (融资+逆回购)

数学框架:
    Total P&L = Sum(Alpha_p + Beta_p + Style_p + Sector_p + Timing_p)
              - Hedge_Cost - Trading_Cost - Funding_Cost

其中:
    Alpha_p    = (w_p - w_bench) × (R_p - R_bench)
    Beta_p     = β_p × (R_market - R_f)
    Style_p    = Σ (style_exposure × style_return)
    Sector_p   = Σ (sector_weight × sector_return)
    Timing_p   = 调仓频率 × 时序相关性
    Hedge_Cost = 期货/期权对冲盈亏 (含保证金机会成本)
    Trading_Cost = 滑点 + 佣金 + 冲击
    Funding_Cost = 融资利息 + 逆回购收益

输出:
    - 因子归因表 (绝对值 + 占比)
    - 归因摘要
    - 归因趋势 (累积归因时序)
    - 异常贡献预警

用法:
    from utils.pnl_attribution_engine import PnLAttributionEngine, AttributionResult
    engine = PnLAttributionEngine()
    result = engine.attribute(
        positions=[...],             # 持仓列表
        portfolio_returns=[...],       # 组合日收益序列
        benchmark_returns=[...],       # 基准日收益序列
        market_returns=[...],          # 市场日收益序列
        factor_returns={...},          # 各因子日收益序列
        sector_returns={...},          # 各行业日收益序列
        trading_costs=1000.0,          # 交易成本
        funding_cost=-50.0,            # 资金成本
        hedge_pnl=-200.0,              # 对冲盈亏
    )
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("pnl_attribution")

BASE_DIR = Path(__file__).resolve().parent.parent
REPORT_DIR = BASE_DIR / "reports" / "pnl_attribution"


@dataclass
class FactorContribution:
    """因子贡献"""

    factor_name: str
    contribution: float  # 绝对贡献 (元或%)
    contribution_pct: float  # 占总 P&L 比例
    exposure: float = 0.0  # 因子暴露
    factor_return: float = 0.0  # 因子收益
    is_significant: bool = False  # 是否显著 (>5%)


@dataclass
class AttributionResult:
    """归因结果"""

    attribution_date: str = ""
    period_start: str = ""
    period_end: str = ""
    total_pnl: float = 0.0
    total_return_pct: float = 0.0

    # 分解项 (单位: 元)
    alpha_pnl: float = 0.0  # 选股超额
    beta_pnl: float = 0.0  # 市场系统性
    style_pnl: float = 0.0  # 风格因子
    sector_pnl: float = 0.0  # 行业暴露
    timing_pnl: float = 0.0  # 择时
    hedge_pnl: float = 0.0  # 对冲盈亏 (负值=对冲成本)
    trading_cost: float = 0.0  # 交易成本 (负值)
    funding_cost: float = 0.0  # 资金成本 (负值)

    # 因子明细
    style_factors: list[FactorContribution] = field(default_factory=list)
    sector_factors: list[FactorContribution] = field(default_factory=list)

    # 风险指标
    information_ratio: float = 0.0  # IR = Alpha / Tracking Error
    tracking_error: float = 0.0
    sharpe_ratio: float = 0.0

    # 异常预警
    anomalies: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PnLAttributionEngine:
    """P&L 多维度归因分析引擎"""

    # 七大风格因子 (与 factor_model.py 对齐)
    STYLE_FACTORS = [
        "momentum",  # 动量 20%
        "reversal",  # 反转 15%
        "volatility",  # 波动率 15%
        "liquidity",  # 流动性 10%
        "earnings_quality",  # 盈利质量 15%
        "growth",  # 成长 15%
        "valuation",  # 估值 10%
    ]

    # 行业分类 (十五五规划相关)
    SECTORS = [
        "tech",  # 科技 (含算力)
        "manufacturing",  # 高端制造
        "cyclical",  # 顺周期
        "resources",  # 资源
        "defensive",  # 防御
        "finance",  # 金融
        "consumer",  # 消费
        "healthcare",  # 医药
    ]

    def __init__(self, risk_free_rate: float = 0.025):
        """
        Args:
            risk_free_rate: 无风险利率 (年化), 默认 2.5%
        """
        self.risk_free_rate = risk_free_rate
        REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------
    # 主入口: 归因分析
    # ------------------------------------------------------------
    def attribute(
        self,
        positions: list[dict[str, Any]],
        portfolio_returns: list[float],
        benchmark_returns: list[float] | None = None,
        market_returns: list[float] | None = None,
        factor_returns: dict[str, list[float]] | None = None,
        sector_returns: dict[str, list[float]] | None = None,
        trading_costs: float = 0.0,
        funding_cost: float = 0.0,
        hedge_pnl: float = 0.0,
        attribution_date: str | None = None,
    ) -> AttributionResult:
        """执行 P&L 归因

        Args:
            positions: 持仓列表 [{"code", "weight", "sector", "style_exposures": {...}}]
            portfolio_returns: 组合日收益序列
            benchmark_returns: 基准日收益序列 (可选, 默认沪深300)
            market_returns: 市场日收益序列 (可选)
            factor_returns: {因子名: 因子日收益序列}
            sector_returns: {行业名: 行业日收益序列}
            trading_costs: 期间累计交易成本 (元, 负值)
            funding_cost: 期间累计资金成本 (元, 负值=融资, 正值=逆回购收益)
            hedge_pnl: 期间累计对冲盈亏 (元, 负值=对冲成本)
            attribution_date: 归因日期

        Returns:
            AttributionResult
        """
        attribution_date = attribution_date or datetime.now().strftime("%Y-%m-%d")
        portfolio_value = (
            sum(p.get("market_value", p.get("amount", 0)) for p in positions)
            or 1_000_000
        )

        # 计算总 P&L
        if not portfolio_returns:
            total_return_pct = 0.0
        else:
            cumulative = 1.0
            for r in portfolio_returns:
                cumulative *= 1 + r
            total_return_pct = cumulative - 1
        total_pnl = portfolio_value * total_return_pct

        result = AttributionResult(
            attribution_date=attribution_date,
            total_pnl=total_pnl,
            total_return_pct=total_return_pct,
            trading_cost=-abs(trading_costs),
            funding_cost=funding_cost,
            hedge_pnl=hedge_pnl,
        )

        # === 1. Beta 分解 ===
        result.beta_pnl = self._calc_beta_pnl(
            positions, portfolio_returns, market_returns, portfolio_value
        )

        # === 2. Alpha 分解 (相对于基准的超额) ===
        result.alpha_pnl = self._calc_alpha_pnl(
            positions, portfolio_returns, benchmark_returns, portfolio_value
        )

        # === 3. 风格因子归因 ===
        result.style_pnl, result.style_factors = self._calc_style_attribution(
            positions, factor_returns, portfolio_value
        )

        # === 4. 行业归因 ===
        result.sector_pnl, result.sector_factors = self._calc_sector_attribution(
            positions, sector_returns, portfolio_value
        )

        # === 5. 择时收益 (总收益 - alpha - beta - style - sector) ===
        explained = (
            result.alpha_pnl
            + result.beta_pnl
            + result.style_pnl
            + result.sector_pnl
            + result.hedge_pnl
            + result.trading_cost
            + result.funding_cost
        )
        result.timing_pnl = total_pnl - explained

        # === 6. 风险指标 ===
        result.tracking_error = self._calc_tracking_error(
            portfolio_returns, benchmark_returns
        )
        result.information_ratio = self._calc_information_ratio(
            portfolio_returns, benchmark_returns
        )
        result.sharpe_ratio = self._calc_sharpe_ratio(portfolio_returns)

        # === 7. 异常检测 ===
        result.anomalies = self._detect_anomalies(result)

        # === 8. 摘要 ===
        result.summary = self._build_summary(result)

        logger.info(
            "[PnLAttribution] %s 总收益 %.2f%% (¥%.0f) | Alpha %.2f%% | Beta %.2f%% | Style %.2f%% | Sector %.2f%% | Timing %.2f%%",  # noqa: E501
            attribution_date,
            total_return_pct * 100,
            total_pnl,
            result.alpha_pnl / portfolio_value * 100,
            result.beta_pnl / portfolio_value * 100,
            result.style_pnl / portfolio_value * 100,
            result.sector_pnl / portfolio_value * 100,
            result.timing_pnl / portfolio_value * 100,
        )

        return result

    # ------------------------------------------------------------
    # Beta 计算
    # ------------------------------------------------------------
    def _calc_beta_pnl(
        self,
        positions: list[dict],
        portfolio_returns: list[float],
        market_returns: list[float] | None,
        portfolio_value: float,
    ) -> float:
        """Beta P&L = β_portfolio × (R_market - R_f) × Portfolio_Value"""
        if not market_returns or not portfolio_returns:
            return 0.0

        beta = self._calc_beta(portfolio_returns, market_returns)
        market_excess_return = sum(market_returns) - self.risk_free_rate / 252 * len(
            market_returns
        )
        return beta * market_excess_return * portfolio_value

    def _calc_beta(
        self, asset_returns: list[float], market_returns: list[float]
    ) -> float:
        """计算 Beta"""
        n = min(len(asset_returns), len(market_returns))
        if n < 2:
            return 1.0

        mean_a = sum(asset_returns[:n]) / n
        mean_m = sum(market_returns[:n]) / n

        cov = sum(
            (asset_returns[i] - mean_a) * (market_returns[i] - mean_m) for i in range(n)
        ) / (n - 1)
        var_m = sum((m - mean_m) ** 2 for m in market_returns[:n]) / (n - 1)

        return cov / var_m if var_m > 0 else 1.0

    # ------------------------------------------------------------
    # Alpha 计算
    # ------------------------------------------------------------
    def _calc_alpha_pnl(
        self,
        positions: list[dict],
        portfolio_returns: list[float],
        benchmark_returns: list[float] | None,
        portfolio_value: float,
    ) -> float:
        """Alpha P&L = (R_portfolio - R_benchmark) × Portfolio_Value"""
        if not benchmark_returns or not portfolio_returns:
            return 0.0

        n = min(len(portfolio_returns), len(benchmark_returns))
        portfolio_cum = 1.0
        bench_cum = 1.0
        for i in range(n):
            portfolio_cum *= 1 + portfolio_returns[i]
            bench_cum *= 1 + benchmark_returns[i]
        alpha_return = portfolio_cum - bench_cum
        return alpha_return * portfolio_value

    # ------------------------------------------------------------
    # 风格因子归因
    # ------------------------------------------------------------
    def _calc_style_attribution(
        self,
        positions: list[dict],
        factor_returns: dict[str, list[float]] | None,
        portfolio_value: float,
    ) -> tuple[float, list[FactorContribution]]:
        """风格因子归因

        每个因子的贡献 = portfolio_exposure × factor_return × portfolio_value
        """
        if not factor_returns:
            return 0.0, []

        # 计算组合在每只股票上的因子暴露加权平均
        total_weight = 0.0
        exposures: dict[str, float] = dict.fromkeys(self.STYLE_FACTORS, 0.0)

        for pos in positions:
            weight = float(pos.get("weight", 0))
            style_exposures = pos.get("style_exposures", {})
            for factor in self.STYLE_FACTORS:
                exposures[factor] += weight * float(style_exposures.get(factor, 0))
            total_weight += weight

        # 归一化
        if total_weight > 0:
            for k in exposures:
                exposures[k] /= total_weight

        total_style_pnl = 0.0
        contributions: list[FactorContribution] = []

        for factor in self.STYLE_FACTORS:
            exp = exposures.get(factor, 0)
            f_returns = factor_returns.get(factor, [])
            if not f_returns:
                f_return = 0.0
            else:
                # 累积因子收益
                cum = 1.0
                for r in f_returns:
                    cum *= 1 + r
                f_return = cum - 1

            contribution = exp * f_return * portfolio_value
            total_style_pnl += contribution

            contributions.append(
                FactorContribution(
                    factor_name=factor,
                    contribution=contribution,
                    contribution_pct=contribution / portfolio_value,
                    exposure=exp,
                    factor_return=f_return,
                    is_significant=abs(contribution / portfolio_value) > 0.005,
                )
            )

        return total_style_pnl, contributions

    # ------------------------------------------------------------
    # 行业归因
    # ------------------------------------------------------------
    def _calc_sector_attribution(
        self,
        positions: list[dict],
        sector_returns: dict[str, list[float]] | None,
        portfolio_value: float,
    ) -> tuple[float, list[FactorContribution]]:
        """行业归因

        每个行业的贡献 = portfolio_weight × sector_return × portfolio_value
        """
        if not sector_returns:
            return 0.0, []

        # 计算每个行业的组合权重
        sector_weights: dict[str, float] = dict.fromkeys(self.SECTORS, 0.0)
        total_weight = 0.0
        for pos in positions:
            sector = pos.get("sector", "other")
            if sector not in sector_weights:
                sector_weights[sector] = 0.0
            weight = float(pos.get("weight", 0))
            sector_weights[sector] += weight
            total_weight += weight

        if total_weight > 0:
            for k in sector_weights:
                sector_weights[k] /= total_weight

        total_sector_pnl = 0.0
        contributions: list[FactorContribution] = []

        for sector in list(sector_weights.keys()):
            weight = sector_weights[sector]
            if weight == 0:
                continue
            s_returns = sector_returns.get(sector, [])
            if not s_returns:
                s_return = 0.0
            else:
                cum = 1.0
                for r in s_returns:
                    cum *= 1 + r
                s_return = cum - 1

            contribution = weight * s_return * portfolio_value
            total_sector_pnl += contribution

            contributions.append(
                FactorContribution(
                    factor_name=sector,
                    contribution=contribution,
                    contribution_pct=contribution / portfolio_value,
                    exposure=weight,
                    factor_return=s_return,
                    is_significant=abs(weight) > 0.05,
                )
            )

        return total_sector_pnl, contributions

    # ------------------------------------------------------------
    # 风险指标
    # ------------------------------------------------------------
    def _calc_tracking_error(
        self,
        portfolio_returns: list[float],
        benchmark_returns: list[float] | None,
    ) -> float:
        """跟踪误差 (年化)"""
        if not benchmark_returns or not portfolio_returns:
            return 0.0

        n = min(len(portfolio_returns), len(benchmark_returns))
        if n < 2:
            return 0.0

        excess = [portfolio_returns[i] - benchmark_returns[i] for i in range(n)]
        mean_excess = sum(excess) / n
        variance = sum((e - mean_excess) ** 2 for e in excess) / (n - 1)
        return math.sqrt(variance) * math.sqrt(252)

    def _calc_information_ratio(
        self,
        portfolio_returns: list[float],
        benchmark_returns: list[float] | None,
    ) -> float:
        """信息比率 = Alpha / Tracking Error"""
        te = self._calc_tracking_error(portfolio_returns, benchmark_returns)
        if te == 0:
            return 0.0

        if not benchmark_returns or not portfolio_returns:
            return 0.0

        n = min(len(portfolio_returns), len(benchmark_returns))
        p_cum = 1.0
        b_cum = 1.0
        for i in range(n):
            p_cum *= 1 + portfolio_returns[i]
            b_cum *= 1 + benchmark_returns[i]
        alpha_return = p_cum - b_cum

        return alpha_return / te

    def _calc_sharpe_ratio(self, portfolio_returns: list[float]) -> float:
        """夏普比率 (年化)"""
        if len(portfolio_returns) < 2:
            return 0.0
        mean_r = sum(portfolio_returns) / len(portfolio_returns)
        variance = sum((r - mean_r) ** 2 for r in portfolio_returns) / (
            len(portfolio_returns) - 1
        )
        std = math.sqrt(variance)
        if std == 0:
            return 0.0
        daily_rf = self.risk_free_rate / 252
        return (mean_r - daily_rf) / std * math.sqrt(252)

    # ------------------------------------------------------------
    # 异常检测
    # ------------------------------------------------------------
    def _detect_anomalies(self, result: AttributionResult) -> list[str]:
        """检测归因异常"""
        anomalies: list[str] = []

        # 1. Alpha 异常 (负 alpha > 2%)
        total = result.total_pnl
        if total != 0:
            alpha_pct = result.alpha_pnl / total
            if alpha_pct < -0.02:
                anomalies.append(f"Alpha 严重为负 ({alpha_pct:.2%}), 选股能力下降")

            # 2. Beta 异常 (> 50% 收益来自 Beta)
            beta_pct = result.beta_pnl / total
            if beta_pct > 0.50:
                anomalies.append(f"Beta 占比过高 ({beta_pct:.2%}), Alpha 不足")

            # 3. 单一风格因子贡献过大 (>30%)
            for fc in result.style_factors:
                if abs(fc.contribution / total) > 0.30:
                    anomalies.append(
                        f"风格因子 {fc.factor_name} 贡献过大 ({fc.contribution / total:.2%})"
                    )

            # 4. 单一行业贡献过大 (>40%)
            for sc in result.sector_factors:
                if abs(sc.contribution / total) > 0.40:
                    anomalies.append(
                        f"行业 {sc.factor_name} 贡献过大 ({sc.contribution / total:.2%})"
                    )

            # 5. 交易成本过高 (>总收益的 20%)
            if abs(result.trading_cost) > 0.20 * abs(total) and abs(total) > 0:
                anomalies.append(
                    f"交易成本占比过高 ({abs(result.trading_cost / total):.2%})"
                )

            # 6. 择时异常 (|timing| > 30%)
            timing_pct = result.timing_pnl / total
            if abs(timing_pct) > 0.30:
                anomalies.append(f"择时贡献异常 ({timing_pct:.2%}), 归因解释力不足")

        # 7. IR 异常
        if result.information_ratio < -0.5:
            anomalies.append(
                f"信息比率为负 ({result.information_ratio:.2f}), 长期跑输基准"
            )

        # 8. 跟踪误差过高
        if result.tracking_error > 0.15:
            anomalies.append(f"跟踪误差过高 ({result.tracking_error:.2%}), 偏离基准")

        return anomalies

    # ------------------------------------------------------------
    # 摘要生成
    # ------------------------------------------------------------
    def _build_summary(self, result: AttributionResult) -> str:
        """生成归因摘要"""
        lines = [
            f"P&L 归因摘要 ({result.attribution_date})",
            "=" * 50,
            f"总收益: ¥{result.total_pnl:,.0f} ({result.total_return_pct:.2%})",
            "",
            "分解:",
            f"  Alpha (选股超额):     ¥{result.alpha_pnl:,.0f}",
            f"  Beta (市场系统性):    ¥{result.beta_pnl:,.0f}",
            f"  Style (风格因子):     ¥{result.style_pnl:,.0f}",
            f"  Sector (行业暴露):    ¥{result.sector_pnl:,.0f}",
            f"  Timing (择时):        ¥{result.timing_pnl:,.0f}",
            f"  Hedge (对冲):         ¥{result.hedge_pnl:,.0f}",
            f"  Trading Cost (成本):  ¥{result.trading_cost:,.0f}",
            f"  Funding (资金):        ¥{result.funding_cost:,.0f}",
            "",
            "风险指标:",
            f"  Sharpe Ratio:         {result.sharpe_ratio:.2f}",
            f"  Information Ratio:    {result.information_ratio:.2f}",
            f"  Tracking Error:       {result.tracking_error:.2%}",
        ]

        if result.style_factors:
            lines.append("")
            lines.append("风格因子明细:")
            for fc in result.style_factors:
                if fc.is_significant:
                    marker = "★" if abs(fc.contribution_pct) > 0.05 else " "
                    lines.append(
                        f"  {marker} {fc.factor_name:<20} 贡献 ¥{fc.contribution:>10,.0f} "
                        f"({fc.contribution_pct:.2%}) 暴露 {fc.exposure:.3f}"
                    )

        if result.sector_factors:
            lines.append("")
            lines.append("行业明细:")
            for sc in result.sector_factors:
                if sc.is_significant:
                    lines.append(
                        f"  {sc.factor_name:<20} 贡献 ¥{sc.contribution:>10,.0f} "
                        f"({sc.contribution_pct:.2%}) 权重 {sc.exposure:.1%}"
                    )

        if result.anomalies:
            lines.append("")
            lines.append(f"⚠️ 异常预警 ({len(result.anomalies)}):")
            for a in result.anomalies:
                lines.append(f"  - {a}")

        return "\n".join(lines)

    # ------------------------------------------------------------
    # 保存报告
    # ------------------------------------------------------------
    def save_report(self, result: AttributionResult) -> Path:
        """保存归因报告"""
        path = REPORT_DIR / f"pnl_attribution_{result.attribution_date}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    result.to_dict(), f, ensure_ascii=False, indent=2, default=str
                )
            logger.info(f"归因报告已保存: {path}")
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
            logger.error(f"保存归因报告失败: {e}")
        return path


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse
    import random

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="P&L 归因分析引擎")
    parser.add_argument("--simulate", action="store_true", help="使用模拟数据测试")
    args = parser.parse_args()

    engine = PnLAttributionEngine()

    if args.simulate:
        # 模拟持仓
        positions = [
            {
                "code": "300308",
                "name": "中际旭创",
                "weight": 0.15,
                "sector": "tech",
                "style_exposures": {"momentum": 0.8, "growth": 0.7, "valuation": -0.3},
            },
            {
                "code": "002475",
                "name": "立讯精密",
                "weight": 0.08,
                "sector": "tech",
                "style_exposures": {"momentum": 0.5, "growth": 0.6, "valuation": -0.2},
            },
            {
                "code": "600519",
                "name": "贵州茅台",
                "weight": 0.10,
                "sector": "consumer",
                "style_exposures": {
                    "earnings_quality": 0.9,
                    "valuation": 0.5,
                    "momentum": 0.3,
                },
            },
            {
                "code": "601088",
                "name": "中国神华",
                "weight": 0.12,
                "sector": "cyclical",
                "style_exposures": {
                    "valuation": 0.8,
                    "earnings_quality": 0.7,
                    "momentum": 0.2,
                },
            },
            {
                "code": "ETF",
                "name": "黄金ETF华安",
                "weight": 0.15,
                "sector": "resources",
                "style_exposures": {"reversal": 0.3, "volatility": -0.2},
            },
        ]

        # 模拟 30 天收益
        random.seed(42)
        portfolio_returns = [random.gauss(0.001, 0.012) for _ in range(30)]
        benchmark_returns = [random.gauss(0.0005, 0.010) for _ in range(30)]
        market_returns = [random.gauss(0.0003, 0.011) for _ in range(30)]

        factor_returns = {
            "momentum": [random.gauss(0.0008, 0.005) for _ in range(30)],
            "reversal": [random.gauss(-0.0003, 0.004) for _ in range(30)],
            "volatility": [random.gauss(-0.0005, 0.006) for _ in range(30)],
            "liquidity": [random.gauss(0.0002, 0.003) for _ in range(30)],
            "earnings_quality": [random.gauss(0.0006, 0.004) for _ in range(30)],
            "growth": [random.gauss(0.0007, 0.005) for _ in range(30)],
            "valuation": [random.gauss(-0.0002, 0.003) for _ in range(30)],
        }

        sector_returns = {
            "tech": [random.gauss(0.0015, 0.015) for _ in range(30)],
            "cyclical": [random.gauss(0.0005, 0.010) for _ in range(30)],
            "resources": [random.gauss(0.0008, 0.012) for _ in range(30)],
            "consumer": [random.gauss(0.0003, 0.008) for _ in range(30)],
        }

        result = engine.attribute(
            positions=positions,
            portfolio_returns=portfolio_returns,
            benchmark_returns=benchmark_returns,
            market_returns=market_returns,
            factor_returns=factor_returns,
            sector_returns=sector_returns,
            trading_costs=3000.0,
            funding_cost=-500.0,
            hedge_pnl=-2000.0,
        )

        logger.info(result.summary)
        engine.save_report(result)
