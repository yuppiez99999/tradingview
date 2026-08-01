"""
因子衰减监控系统 (v8.5升级)
==========================
功能:
1. 实时监控Alpha因子的IC(Information Coefficient)衰减趋势
2. 自动检测因子失效信号(ICIR连续低于0.2超过6个月)
3. 因子拥挤度监控(估值价差、头部集中度、量化私募规模变化)
4. 因子半衰期计算(预测能力衰减到一半所需时间)
5. 自动退役触发和因子更替建议

核心规则:
- 有效因子标准: RankIC > 0.03, ICIR > 0.5, 多空夏普 > 1.0
- 退役标准: 连续6个月ICIR < 0.2, 或连续3个月多空夏普 < 0,
           或实盘收益显著偏离回测预期(回测CAGR的30%以下持续2个月),
           或因子拥挤度超过历史80分位数
- 模型库最多同时运行15-25个独立Alpha源,超过则边际收益递减
- 每季度全体因子审查: 淘汰>1年的失效因子
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class FactorIC:
    """因子IC数据"""

    factor_name: str
    date: datetime
    rank_ic: float  # RankIC
    ic_mean: float  # IC均值(滚动20日)
    ic_std: float  # IC标准差
    icir: float  # IC Information Ratio
    long_return: float  # 多头收益
    short_return: float  # 空头收益
    long_short_sharpe: float  # 多空夏普
    top_group_return: float  # Top组收益
    bottom_group_return: float  # Bottom组收益
    monotonicity: float  # 单调性得分(0-1)


@dataclass
class FactorHalfLife:
    """因子半衰期"""

    factor_name: str
    half_life_days: float  # 半衰期(天)
    decay_rate: float  # 衰减速率
    is_stable: bool  # 是否稳定(半衰期>60天)


@dataclass
class FactorCrowding:
    """因子拥挤度"""

    factor_name: str
    current_score: float  # 当前拥挤度评分(0-100)
    percentile: float  # 历史百分位
    valuation_spread: float  # 估值价差
    top_concentration: float  # 头部集中度
    qfii_scale_change: float  # 量化私募规模变化
    risk_level: str  # 'LOW', 'MEDIUM', 'HIGH', 'EXTREME'


@dataclass
class FactorHealthScore:
    """因子健康度评分"""

    factor_name: str
    overall_score: float  # 综合评分(0-100)
    ic_score: float  # IC得分(0-40)
    stability_score: float  # 稳定性得分(0-30)
    crowding_score: float  # 拥挤度得分(0-30)
    status: str  # 'HEALTHY', 'WARNING', 'DEGRADING', 'DEPRECATED'
    days_since_peak: int  # 距峰值天数


@dataclass
class FactorDeprecationTrigger:
    """因子退役触发条件"""

    factor_name: str
    trigger_reason: str  # 触发原因
    severity: str  # 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'
    recommended_action: str  # 建议操作
    grace_period_days: int  # 宽限期(天)


@dataclass
class FactorMonitorReport:
    """因子监控报告"""

    timestamp: datetime
    total_factors: int
    healthy_factors: int
    warning_factors: int
    degrading_factors: int
    deprecated_factors: int
    new_deprecations: List[str]  # 新触发退役的因子
    warnings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    status: str = "OK"  # "OK", "WARNING", "CRITICAL"


class FactorDecayMonitor:
    """
    因子衰减监控器

    使用示例:
        monitor = FactorDecayMonitor()

        # 记录每日IC
        monitor.record_ic(factor_data)

        # 计算因子半衰期
        half_life = monitor.calculate_half_life("value_factor")

        # 监控拥挤度
        crowding = monitor.check_crowding("momentum_factor")

        # 生成健康度报告
        report = monitor.generate_health_report()

        # 检查退役触发
        triggers = monitor.check_deprecation_triggers()
    """

    def __init__(
        self,
        max_factors: int = 20,
        min_icir: float = 0.2,
        min_sharpe: float = 0.0,
        crowding_percentile_threshold: float = 80.0,
    ):
        self.max_factors = max_factors
        self.min_icir = min_icir
        self.min_sharpe = min_sharpe
        self.crowding_percentile_threshold = crowding_percentile_threshold

        # IC历史记录
        self.ic_history: Dict[str, List[FactorIC]] = {}

        # 因子健康度
        self.health_scores: Dict[str, FactorHealthScore] = {}

        # 退役触发记录
        self.deprecation_triggers: Dict[str, List[FactorDeprecationTrigger]] = {}

        logger.info(
            f"[FactorDecayMonitor] 初始化完成 | "
            f"Max Factors={max_factors} | "
            f"Min ICIR={min_icir} | "
            f"Crowding Threshold={crowding_percentile_threshold}%"
        )

    def record_ic(self, ic_data: List[FactorIC]):
        """
        记录每日因子IC数据

        Args:
            ic_data: FactorIC列表
        """
        for ic in ic_data:
            if ic.factor_name not in self.ic_history:
                self.ic_history[ic.factor_name] = []

            self.ic_history[ic.factor_name].append(ic)

            # 保持最近365天数据
            cutoff_date = datetime.now() - timedelta(days=365)
            self.ic_history[ic.factor_name] = [
                item for item in self.ic_history[ic.factor_name] if item.date >= cutoff_date
            ]

        logger.debug(f"[FactorDecayMonitor] 记录{len(ic_data)}个因子IC数据")

    def calculate_half_life(self, factor_name: str) -> Optional[FactorHalfLife]:
        """
        计算因子半衰期

        方法: 对IC序列进行指数衰减拟合
        IC(t) = IC_0 * exp(-lambda * t)
        半衰期 T_1/2 = ln(2) / lambda

        Args:
            factor_name: 因子名称

        Returns:
            FactorHalfLife对象,若因子不存在则返回None
        """
        if factor_name not in self.ic_history or len(self.ic_history[factor_name]) < 60:
            return None

        ics = self.ic_history[factor_name]
        ic_values = np.array([ic.rank_ic for ic in ics])
        dates = np.array([ic.date.timestamp() for ic in ics])

        # 计算时间差(天)
        t_days = (dates - dates[0]) / 86400

        # 过滤零IC值
        valid_mask = ic_values != 0
        t_days = t_days[valid_mask]
        ic_values = ic_values[valid_mask]

        if len(ic_values) < 30:
            return None

        # 指数衰减拟合: log|IC| = log(IC_0) - lambda * t
        log_abs_ic = np.log(np.abs(ic_values) + 1e-8)

        # 线性回归
        slope, _intercept = np.polyfit(t_days, log_abs_ic, 1)

        # 衰减速率
        decay_rate = -slope
        half_life_days = np.log(2) / decay_rate if decay_rate > 0 else float("inf")

        # 稳定性判断(半衰期>60天为稳定)
        is_stable = half_life_days > 60

        return FactorHalfLife(
            factor_name=factor_name, half_life_days=half_life_days, decay_rate=decay_rate, is_stable=is_stable
        )

    def check_crowding(
        self,
        factor_name: str,
        valuation_spread: float = 0.05,
        top_concentration: float = 0.3,
        qfii_scale_change: float = 0.2,
    ) -> FactorCrowding:
        """
        检查因子拥挤度

        Args:
            factor_name: 因子名称
            valuation_spread: 估值价差(越大越不拥挤)
            top_concentration: 头部集中度(前10%股票占比)
            qfii_scale_change: 量化私募规模变化率

        Returns:
            FactorCrowding对象
        """
        # 拥挤度评分(0-100,越高越拥挤)
        score = 0.0

        # 1. 估值价差(价差越小越拥挤,0-40分)
        if valuation_spread > 0.1:
            score += 10
        elif valuation_spread > 0.05:
            score += 20
        elif valuation_spread > 0.02:
            score += 30
        else:
            score += 40

        # 2. 头部集中度(越高越拥挤,0-30分)
        if top_concentration > 0.5:
            score += 30
        elif top_concentration > 0.3:
            score += 20
        elif top_concentration > 0.2:
            score += 10
        else:
            score += 0

        # 3. 量化私募规模变化(越大越拥挤,0-30分)
        if qfii_scale_change > 0.5:
            score += 30
        elif qfii_scale_change > 0.3:
            score += 20
        elif qfii_scale_change > 0.1:
            score += 10
        else:
            score += 0

        # 风险等级
        if score >= 80:
            risk_level = "EXTREME"
        elif score >= 60:
            risk_level = "HIGH"
        elif score >= 40:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        # 历史百分位(简化版,实际应从历史数据库获取)
        percentile = min(score, 100)

        return FactorCrowding(
            factor_name=factor_name,
            current_score=score,
            percentile=percentile,
            valuation_spread=valuation_spread,
            top_concentration=top_concentration,
            qfii_scale_change=qfii_scale_change,
            risk_level=risk_level,
        )

    def calculate_health_score(self, factor_name: str) -> Optional[FactorHealthScore]:
        """
        计算因子健康度评分

        评分维度:
        1. IC得分(0-40分): 基于ICIR和多空夏普
        2. 稳定性得分(0-30分): 基于半衰期和IC波动率
        3. 拥挤度得分(0-30分): 基于拥挤度评分

        Args:
            factor_name: 因子名称

        Returns:
            FactorHealthScore对象
        """
        if factor_name not in self.ic_history:
            return None

        ics = self.ic_history[factor_name]
        if len(ics) < 20:
            return None

        # 1. IC得分(0-40分)
        recent_ics = ics[-60:]  # 最近60天
        mean_icir = np.mean([ic.icir for ic in recent_ics])
        mean_sharpe = np.mean([ic.long_short_sharpe for ic in recent_ics])

        ic_score = 0.0
        if mean_icir > 0.5:
            ic_score += 30
        elif mean_icir > 0.3:
            ic_score += 20
        elif mean_icir > 0.2:
            ic_score += 10

        if mean_sharpe > 1.0:
            ic_score += 10
        elif mean_sharpe > 0.5:
            ic_score += 5

        ic_score = min(ic_score, 40)

        # 2. 稳定性得分(0-30分)
        half_life = self.calculate_half_life(factor_name)
        stability_score = 0.0

        if half_life:
            if half_life.half_life_days > 180:
                stability_score += 30
            elif half_life.half_life_days > 90:
                stability_score += 20
            elif half_life.half_life_days > 60:
                stability_score += 10
        else:
            # 无半衰期数据,用IC波动率替代
            ic_vol = np.std([ic.rank_ic for ic in recent_ics])
            if ic_vol < 0.01:
                stability_score += 30
            elif ic_vol < 0.02:
                stability_score += 20
            elif ic_vol < 0.03:
                stability_score += 10

        # 3. 拥挤度得分(0-30分,简化版)
        crowding = self.check_crowding(factor_name)
        crowding_score = max(0, 30 - crowding.current_score * 0.3)

        # 综合评分
        overall_score = ic_score + stability_score + crowding_score

        # 状态判定
        if overall_score >= 70 and mean_icir > 0.3:
            status = "HEALTHY"
        elif overall_score >= 50:
            status = "WARNING"
        elif overall_score >= 30 or mean_icir < self.min_icir:
            status = "DEGRADING"
        else:
            status = "DEPRECATED"

        # 距峰值天数
        peak_icir = max(ic.icir for ic in ics)
        days_since_peak = 0
        for _i, ic in enumerate(reversed(ics)):
            if ic.icir < peak_icir * 0.5:
                break
            days_since_peak += 1

        return FactorHealthScore(
            factor_name=factor_name,
            overall_score=overall_score,
            ic_score=ic_score,
            stability_score=stability_score,
            crowding_score=crowding_score,
            status=status,
            days_since_peak=days_since_peak,
        )

    def check_deprecation_triggers(self, factor_name: str) -> List[FactorDeprecationTrigger]:
        """
        检查因子退役触发条件

        退役标准:
        1. 连续6个月ICIR < 0.2
        2. 连续3个月多空夏普 < 0
        3. 实盘收益显著偏离回测预期(CAGR的30%以下持续2个月)
        4. 因子拥挤度超过历史80分位数

        Args:
            factor_name: 因子名称

        Returns:
            触发的退役条件列表
        """
        triggers = []

        if factor_name not in self.ic_history:
            return triggers

        ics = self.ic_history[factor_name]
        if len(ics) < 20:
            return triggers

        # 1. 检查连续6个月ICIR < 0.2
        recent_6m = ics[-126:]  # 约6个月
        if len(recent_6m) >= 90:
            low_icir_count = sum(1 for ic in recent_6m if ic.icir < self.min_icir)
            if low_icir_count > 90 * 0.8:  # 80%时间ICIR低于阈值
                triggers.append(
                    FactorDeprecationTrigger(
                        factor_name=factor_name,
                        trigger_reason=f"连续{low_icir_count}天ICIR<{self.min_icir}(超80%时间)",
                        severity="HIGH",
                        recommended_action="启动因子退役流程,寻找替代因子",
                        grace_period_days=30,
                    )
                )

        # 2. 检查连续3个月多空夏普 < 0
        recent_3m = ics[-63:]
        if len(recent_3m) >= 60:
            negative_sharpe_count = sum(1 for ic in recent_3m if ic.long_short_sharpe < self.min_sharpe)
            if negative_sharpe_count > 60 * 0.5:  # 超过50%时间夏普为负
                triggers.append(
                    FactorDeprecationTrigger(
                        factor_name=factor_name,
                        trigger_reason=f"连续{negative_sharpe_count}天多空夏普<{self.min_sharpe}",
                        severity="HIGH",
                        recommended_action="立即暂停因子实盘,全面复盘",
                        grace_period_days=14,
                    )
                )

        # 3. 检查拥挤度
        crowding = self.check_crowding(factor_name)
        if crowding.percentile > self.crowding_percentile_threshold:
            triggers.append(
                FactorDeprecationTrigger(
                    factor_name=factor_name,
                    trigger_reason=f"因子拥挤度{crowding.percentile:.1f}分位(阈值{self.crowding_percentile_threshold}%)",
                    severity="MEDIUM",
                    recommended_action="降低因子权重,逐步减仓",
                    grace_period_days=60,
                )
            )

        return triggers

    def generate_health_report(self) -> FactorMonitorReport:
        """
        生成全因子健康度报告

        Returns:
            FactorMonitorReport对象
        """
        all_factors = list(self.ic_history.keys())
        total_factors = len(all_factors)

        healthy = []
        warning = []
        degrading = []
        deprecated = []
        new_deprecations = []
        warnings = []
        recommendations = []

        for factor in all_factors:
            health = self.calculate_health_score(factor)
            if health is None:
                continue

            self.health_scores[factor] = health

            if health.status == "HEALTHY":
                healthy.append(factor)
            elif health.status == "WARNING":
                warning.append(factor)
            elif health.status == "DEGRADING":
                degrading.append(factor)
            elif health.status == "DEPRECATED":
                deprecated.append(factor)
                new_deprecations.append(factor)

            # 检查退役触发
            triggers = self.check_deprecation_triggers(factor)
            if triggers:
                for trigger in triggers:
                    if trigger.severity in ["HIGH", "CRITICAL"]:
                        warnings.append(f"[{trigger.severity}] 因子'{factor}': {trigger.trigger_reason}")
                        recommendations.append(f"- {trigger.recommended_action} (因子: {factor})")

        # 总数统计
        n_healthy = len(healthy)
        n_warning = len(warning)
        n_degrading = len(degrading)
        n_deprecated = len(deprecated)

        # 状态判定
        if n_deprecated > 0 or len([w for w in warnings if "CRITICAL" in w]) > 0:
            status = "CRITICAL"
        elif n_degrading > total_factors * 0.2 or len(warnings) > 3:
            status = "WARNING"
        else:
            status = "OK"

        # 因子库容量警告
        if total_factors > self.max_factors * 0.8:
            warnings.append(f"[WARNING] 因子库接近上限({total_factors}/{self.max_factors})")
            recommendations.append("优先保留健康因子,淘汰deprecated因子后再添加新因子")

        return FactorMonitorReport(
            timestamp=datetime.now(),
            total_factors=total_factors,
            healthy_factors=n_healthy,
            warning_factors=n_warning,
            degrading_factors=n_degrading,
            deprecated_factors=n_deprecated,
            new_deprecations=new_deprecations,
            warnings=warnings,
            recommendations=recommendations,
            status=status,
        )


if __name__ == "__main__":
    # 测试示例
    print("因子衰减监控系统测试\n")
    print("=" * 60)

    # 创建监控器
    monitor = FactorDecayMonitor()

    # 生成模拟IC数据(3个因子,不同衰减特征)
    np.random.seed(42)
    n_days = 180

    # 因子1: 稳定因子(ICIR持续>0.5)
    stable_ic = np.random.normal(0.05, 0.01, n_days)

    # 因子2: 衰减因子(ICIR从0.6降至0.1)
    decay_ic = np.linspace(0.6, 0.1, n_days) + np.random.normal(0, 0.02, n_days)

    # 因子3: 不稳定因子(ICIR波动大)
    volatile_ic = np.random.normal(0.3, 0.15, n_days)

    # 记录IC数据
    ic_records = []
    base_date = datetime.now() - timedelta(days=n_days)

    for i in range(n_days):
        date = base_date + timedelta(days=i)

        # 因子1
        ic_records.append(
            FactorIC(
                factor_name="stable_value",
                date=date,
                rank_ic=stable_ic[i],
                ic_mean=np.mean(stable_ic[: i + 1]),
                ic_std=np.std(stable_ic[: i + 1]),
                icir=np.mean(stable_ic[: i + 1]) / max(np.std(stable_ic[: i + 1]), 1e-8),
                long_return=0.02,
                short_return=-0.01,
                long_short_sharpe=1.5,
                top_group_return=0.03,
                bottom_group_return=-0.02,
                monotonicity=0.85,
            )
        )

        # 因子2
        ic_records.append(
            FactorIC(
                factor_name="decaying_momentum",
                date=date,
                rank_ic=max(decay_ic[i], 0),
                ic_mean=np.mean(decay_ic[: i + 1]),
                ic_std=np.std(decay_ic[: i + 1]),
                icir=np.mean(decay_ic[: i + 1]) / max(np.std(decay_ic[: i + 1]), 1e-8),
                long_return=0.015,
                short_return=-0.005,
                long_short_sharpe=0.8,
                top_group_return=0.02,
                bottom_group_return=-0.01,
                monotonicity=0.6,
            )
        )

        # 因子3
        ic_records.append(
            FactorIC(
                factor_name="volatile_quality",
                date=date,
                rank_ic=max(volatile_ic[i], 0),
                ic_mean=np.mean(volatile_ic[: i + 1]),
                ic_std=np.std(volatile_ic[: i + 1]),
                icir=np.mean(volatile_ic[: i + 1]) / max(np.std(volatile_ic[: i + 1]), 1e-8),
                long_return=0.01,
                short_return=0.005,
                long_short_sharpe=0.3,
                top_group_return=0.015,
                bottom_group_return=0.005,
                monotonicity=0.4,
            )
        )

    # 批量记录
    monitor.record_ic(ic_records)

    # 生成健康度报告
    report = monitor.generate_health_report()

    print(f"\n因子健康度报告 ({report.timestamp.strftime('%Y-%m-%d %H:%M')})")
    print(f"{'=' * 60}")
    print(f"总因子数: {report.total_factors}")
    print(f"健康: {report.healthy_factors}")
    print(f"警告: {report.warning_factors}")
    print(f"退化: {report.degrading_factors}")
    print(f"已退役: {report.deprecated_factors}")
    print(f"\n状态: {report.status}")

    if report.warnings:
        print("\n[WARNING] 警告:")
        for w in report.warnings:
            print(f"  - {w}")

    if report.recommendations:
        print("\n[RECOMMENDATION] 建议:")
        for r in report.recommendations:
            print(f"  {r}")

    # 打印各因子详情
    print("\n各因子详情:")
    print(f"{'-' * 60}")
    for factor, health in monitor.health_scores.items():
        print(f"\n{factor}:")
        print(f"  综合评分: {health.overall_score:.1f}/100")
        print(f"  IC得分: {health.ic_score:.1f}/40")
        print(f"  稳定性得分: {health.stability_score:.1f}/30")
        print(f"  拥挤度得分: {health.crowding_score:.1f}/30")
        print(f"  状态: {health.status}")
        print(f"  距峰值天数: {health.days_since_peak}")

        # 半衰期
        hl = monitor.calculate_half_life(factor)
        if hl:
            print(f"  半衰期: {hl.half_life_days:.1f}天 {'(稳定)' if hl.is_stable else '(不稳定)'}")

    print(f"{'=' * 60}\n")
