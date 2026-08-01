"""
Vega 风险监控模块 (v8.5升级)
=============================
功能:
1. 实时监控组合Vega敞口(隐含波动率变化1%对应的PnL影响)
2. Vega上限管理: 建议组合Vega不超过净值的1%-2%
3. 波动率曲面监控: 检测Put Skew/Call Skew异常
4. Vega止损: 当累计Vega损失超过阈值时触发对冲调整

核心公式:
- Vega = ∂Portfolio/∂σ = Σ(w_i * Vega_i)
- Vega PnL ≈ Vega * Δσ
- Put Skew = ATM Put IV - OTM Put IV
- Call Skew = ATM Call IV - OTM Call IV
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


@dataclass
class OptionPosition:
    """期权持仓数据"""

    symbol: str
    option_type: str  # 'CALL' or 'PUT'
    strike: float
    expiry: datetime
    quantity: int
    delta: float
    gamma: float
    vega: float
    theta: float
    iv: float  # 隐含波动率
    market_value: float


@dataclass
class VegaExposure:
    """Vega暴露数据"""

    total_vega: float  # 组合总Vega
    vega_as_pct_nav: float  # Vega占净值百分比
    vega_pnl_1pct_move: float  # 波动率变动1%的PnL影响
    put_vega: float  # Put Vega总和
    call_vega: float  # Call Vega总和
    put_call_vega_ratio: float  # Put/Call Vega比率
    max_single_option_vega: float  # 单一期权最大Vega
    concentration_risk: str  # 'LOW', 'MEDIUM', 'HIGH'


@dataclass
class SkewMetrics:
    """Skew指标"""

    put_skew: float  # Put Skew
    call_skew: float  # Call Skew
    skew_imbalance: float  # Skew失衡度
    skew_zscore: float  # Skew Z-Score(历史分位数)
    is_anomalous: bool  # 是否异常


@dataclass
class VegaRiskReport:
    """Vega风险报告"""

    timestamp: datetime
    exposure: VegaExposure
    skew: SkewMetrics
    warnings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    status: str = "OK"  # "OK", "WARNING", "CRITICAL"


class VegaMonitor:
    """Vega风险监控器"""

    def __init__(
        self,
        nav: float = 5_000_000,
        max_vega_pct: float = 0.02,  # Vega上限: 净值2%
        warning_vega_pct: float = 0.015,  # 警告阈值: 净值1.5%
        max_put_skew: float = 0.15,  # Put Skew上限15%
        max_call_skew: float = 0.10,
    ):  # Call Skew上限10%
        self.nav = nav
        self.max_vega_pct = max_vega_pct
        self.warning_vega_pct = warning_vega_pct
        self.max_put_skew = max_put_skew
        self.max_call_skew = max_call_skew

        # 历史IV数据(用于计算Z-Score)
        self.history_iv: Dict[str, List[Tuple[datetime, float]]] = {}

        # 累计Vega PnL追踪
        self.cumulative_vega_pnl: float = 0.0
        self.daily_vega_pnl: float = 0.0

        logger.info(
            f"[VegaMonitor] 初始化完成 | NAV={nav:,.0f} | "
            f"Max Vega={max_vega_pct * 100:.1f}% | "
            f"Warning Vega={warning_vega_pct * 100:.1f}%"
        )

    def calculate_exposure(self, positions: List[OptionPosition]) -> VegaExposure:
        """
        计算组合Vega暴露

        Args:
            positions: 期权持仓列表

        Returns:
            VegaExposure对象
        """
        if not positions:
            return VegaExposure(
                total_vega=0,
                vega_as_pct_nav=0,
                vega_pnl_1pct_move=0,
                put_vega=0,
                call_vega=0,
                put_call_vega_ratio=0,
                max_single_option_vega=0,
                concentration_risk="LOW",
            )

        # 计算各项Vega指标
        total_vega = sum(pos.vega * pos.quantity for pos in positions)
        put_vega = sum(pos.vega * pos.quantity for pos in positions if pos.option_type == "PUT")
        call_vega = sum(pos.vega * pos.quantity for pos in positions if pos.option_type == "CALL")

        # Vega占净值比例
        vega_as_pct = abs(total_vega) / self.nav if self.nav > 0 else 0

        # Vega PnL敏感度(波动率变动1%)
        vega_pnl_1pct = total_vega * 0.01

        # 集中度评估
        max_single_vega = max(abs(pos.vega * pos.quantity) for pos in positions)
        concentration_ratio = max_single_vega / abs(total_vega) if total_vega != 0 else 0

        if concentration_ratio > 0.5:
            risk_level = "HIGH"
        elif concentration_ratio > 0.3:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        # Put/Call Vega比率
        pc_ratio = abs(put_vega) / abs(call_vega) if call_vega != 0 else float("inf")

        return VegaExposure(
            total_vega=total_vega,
            vega_as_pct_nav=vega_as_pct,
            vega_pnl_1pct_move=vega_pnl_1pct,
            put_vega=put_vega,
            call_vega=call_vega,
            put_call_vega_ratio=pc_ratio,
            max_single_option_vega=max_single_vega,
            concentration_risk=risk_level,
        )

    def calculate_skew_metrics(self, positions: List[OptionPosition], atm_iv: float) -> SkewMetrics:
        """
        计算Skew指标

        Args:
            positions: 期权持仓列表
            atm_iv: ATM隐含波动率

        Returns:
            SkewMetrics对象
        """
        # 分离Put和Call
        puts = [p for p in positions if p.option_type == "PUT"]
        calls = [p for p in positions if p.option_type == "CALL"]

        # 计算OTM Put IV(取虚值程度最大的)
        otm_puts = sorted(puts, key=lambda x: x.strike, reverse=True)[:1]
        otm_calls = sorted(calls, key=lambda x: x.strike)[:1]

        put_skew = (otm_puts[0].iv - atm_iv) / atm_iv if otm_puts and atm_iv > 0 else 0
        call_skew = (otm_calls[0].iv - atm_iv) / atm_iv if otm_calls and atm_iv > 0 else 0

        # Skew失衡度
        skew_imbalance = put_skew + call_skew

        # 异常检测
        is_anomalous = abs(put_skew) > self.max_put_skew or abs(call_skew) > self.max_call_skew

        return SkewMetrics(
            put_skew=put_skew,
            call_skew=call_skew,
            skew_imbalance=skew_imbalance,
            skew_zscore=0,  # TODO: 需要历史数据计算Z-Score
            is_anomalous=is_anomalous,
        )

    def generate_report(self, positions: List[OptionPosition]) -> VegaRiskReport:
        """
        生成Vega风险报告

        Args:
            positions: 期权持仓列表

        Returns:
            VegaRiskReport对象
        """
        atm_iv = 0.20  # 默认ATM IV 20%,实际应从市场获取
        if positions:
            atm_options = [p for p in positions if 0.95 < p.strike / 3900 < 1.05]
            if atm_options:
                atm_iv = atm_options[0].iv

        exposure = self.calculate_exposure(positions)
        skew = self.calculate_skew_metrics(positions, atm_iv)

        warnings = []
        recommendations = []
        status = "OK"

        # 检查Vega暴露
        if exposure.vega_as_pct_nav > self.max_vega_pct:
            warnings.append(
                f"[CRITICAL] Vega暴露过高: {exposure.vega_as_pct_nav * 100:.2f}% (上限{self.max_vega_pct * 100:.1f}%)"
            )
            recommendations.append("立即减少期权持仓或买入反向Vega头寸对冲")
            status = "CRITICAL"

        elif exposure.vega_as_pct_nav > self.warning_vega_pct:
            warnings.append(
                f"[WARNING] Vega暴露接近上限: {exposure.vega_as_pct_nav * 100:.2f}% "
                f"(警告线{self.warning_vega_pct * 100:.1f}%)"
            )
            recommendations.append("监控隐含波动率变化,准备应对波动率骤升")
            status = "WARNING"

        # 检查集中度
        if exposure.concentration_risk == "HIGH":
            warnings.append(
                f"[WARNING] Vega集中度风险高: 单一期权占比{exposure.max_single_option_vega / exposure.total_vega * 100:.1f}%"
                if exposure.total_vega != 0
                else "总Vega为零"
            )
            recommendations.append("分散期权持仓,避免单一合约过度集中")

        # 检查Skew异常
        if skew.is_anomalous:
            warnings.append(
                f"[WARNING] Skew异常: Put Skew={skew.put_skew * 100:.1f}%, Call Skew={skew.call_skew * 100:.1f}%"
            )
            if skew.put_skew > self.max_put_skew:
                recommendations.append("Put Skew过高,买Put对冲成本极高,考虑用Put Spread替代裸买Put")

        # Theta衰减监控
        total_theta = sum(p.theta * p.quantity for p in positions)
        if total_theta < -10000:  # 每日Theta损失超过1万
            warnings.append(f"[WARNING] Theta衰减严重: 每日损失{total_theta:,.0f}")
            recommendations.append("持有期权每日支付大量时间价值,若年化Theta超过预期alpha收益30%需重新评估对冲方案")

        return VegaRiskReport(
            timestamp=datetime.now(),
            exposure=exposure,
            skew=skew,
            warnings=warnings,
            recommendations=recommendations,
            status=status,
        )

    def update_cumulative_pnl(self, daily_pnl: float):
        """
        更新累计Vega PnL

        Args:
            daily_pnl: 当日Vega PnL
        """
        self.cumulative_vega_pnl += daily_pnl
        self.daily_vega_pnl = daily_pnl

        # 检查累计损失是否触发止损
        if abs(self.cumulative_vega_pnl) > self.nav * 0.01:  # 累计损失超过净值1%
            logger.critical(
                f"[Vega止损] 累计Vega损失{self.cumulative_vega_pnl:,.0f} "
                f"(占NAV {abs(self.cumulative_vega_pnl) / self.nav * 100:.2f}%)"
            )

    def get_status_summary(self) -> str:
        """获取状态摘要"""
        return (
            f"VegaMonitor | NAV={self.nav:,.0f} | "
            f"Max Vega={self.max_vega_pct * 100:.1f}% | "
            f"Warning Vega={self.warning_vega_pct * 100:.1f}% | "
            f"Cumulative Vega PnL={self.cumulative_vega_pnl:,.0f}"
        )


if __name__ == "__main__":
    # 测试示例
    monitor = VegaMonitor(nav=5_000_000)

    # 模拟持仓
    test_positions = [
        OptionPosition(
            symbol="10003796.SH",  # 上证50ETF购1月3900
            option_type="CALL",
            strike=3900,
            expiry=datetime(2026, 1, 17),
            quantity=100,
            delta=0.6,
            gamma=0.02,
            vega=0.3,
            theta=-50,
            iv=0.20,
            market_value=150000,
        ),
        OptionPosition(
            symbol="10003798.SH",  # 上证50ETF沽1月3850
            option_type="PUT",
            strike=3850,
            expiry=datetime(2026, 1, 17),
            quantity=50,
            delta=-0.4,
            gamma=0.015,
            vega=0.25,
            theta=-40,
            iv=0.22,
            market_value=120000,
        ),
    ]

    report = monitor.generate_report(test_positions)
    print(f"\n{'=' * 60}")
    print("Vega 风险报告")
    print(f"{'=' * 60}")
    print(f"状态: {report.status}")
    print(f"总Vega: {report.exposure.total_vega:,.2f}")
    print(f"Vega/NAV: {report.exposure.vega_as_pct_nav * 100:.2f}%")
    print(f"波动率变动1%的PnL影响: {report.exposure.vega_pnl_1pct_move:,.0f}")
    print(f"Put Vega: {report.exposure.put_vega:,.2f}")
    print(f"Call Vega: {report.exposure.call_vega:,.2f}")
    print(f"Put/Call Vega比率: {report.exposure.put_call_vega_ratio:.2f}")
    print(f"集中度风险: {report.exposure.concentration_risk}")
    print(f"\nPut Skew: {report.skew.put_skew * 100:.2f}%")
    print(f"Call Skew: {report.skew.call_skew * 100:.2f}%")
    print(f"Skew异常: {report.skew.is_anomalous}")

    if report.warnings:
        print("\n[WARNING] 警告:")
        for w in report.warnings:
            print(f"  - {w}")

    if report.recommendations:
        print("\n[RECOMMENDATION] 建议:")
        for r in report.recommendations:
            print(f"  - {r}")

    print(f"{'=' * 60}\n")
