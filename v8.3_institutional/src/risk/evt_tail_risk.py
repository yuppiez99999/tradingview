"""
极值理论(EVT)肥尾建模模块 (v8.5升级)
====================================
功能:
1. Generalized Pareto Distribution (GPD)拟合尾部风险
2. Value at Risk (VaR)和Conditional VaR (CVaR/Expected Shortfall)计算
3. 历史极端情景压力测试(2008金融海啸、2015股灾、2016熔断、2020疫情崩盘等)
4. 蒙特卡洛模拟2000条路径的99% CVaR
5. 肥尾预警和极端事件检测

核心理论:
- 传统正态分布假设低估极端事件概率
- EVT专注于尾部分布,使用Pareto分布拟合极值
- Peak Over Threshold (POT)方法: 选取超过阈值的所有观测值拟合GPD
- Block Maxima方法: 取固定时间窗口的最大值拟合Generalized Extreme Value (GEV)分布

参考:
- McNeil, A.J., Frey, R., and Embrechts, P. (2015). "Quantitative Risk Management"
- Embrechts, P., Klüppelberg, C., and Mikosch, T. (1997). "Modelling Extremal Events"
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

import numpy as np
from scipy import stats
from scipy.optimize import minimize

logger = logging.getLogger(__name__)


@dataclass
class GPDParameters:
    """GPD分布参数"""

    xi: float  # 形状参数(tail index)
    sigma: float  # 尺度参数
    threshold: float  # 阈值
    log_likelihood: float  # 对数似然值
    kolmogorov_smirnov_stat: float  # KS检验统计量
    ks_pvalue: float  # KS检验p值


@dataclass
class RiskMetrics:
    """风险指标"""

    var_95: float  # 95% VaR
    var_975: float  # 97.5% VaR
    var_99: float  # 99% VaR
    var_995: float  # 99.5% VaR
    cvar_99: float  # 99% CVaR (Expected Shortfall)
    cvar_995: float  # 99.5% CVaR
    tail_index: float  # 尾部指数(xi)
    is_heavy_tailed: bool  # 是否肥尾(xi > 0)
    is_fat_enough: bool  # 是否足够肥(xi > 0.5)


@dataclass
class StressTestResult:
    """压力测试结果"""

    scenario_name: str
    portfolio_value: float
    loss: float
    loss_pct: float
    max_drawdown: float
    time_to_recover_days: int
    is_severe: bool  # 是否严重(损失>20%)


@dataclass
class EVTRiskReport:
    """EVT风险报告"""

    timestamp: datetime
    gpd_params: GPDParameters
    risk_metrics: RiskMetrics
    stress_tests: List[StressTestResult]
    monte_carlo_cvar: float
    warnings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    status: str = "OK"  # "OK", "WARNING", "CRITICAL"


class ExtremeValueAnalyzer:
    """
    极值理论分析器

    使用示例:
        analyzer = ExtremeValueAnalyzer(confidence_level=0.99)

        # 拟合GPD
        gpd_params = analyzer.fit_gpd(returns, threshold=0.95)

        # 计算风险指标
        risk_metrics = analyzer.calculate_risk_metrics(gpd_params, portfolio_value=5_000_000)

        # 压力测试
        stress_results = analyzer.run_stress_tests(portfolio_returns)

        # 蒙特卡洛模拟
        mc_cvar = analyzer.monte_carlo_cvar(n_simulations=2000)
    """

    def __init__(
        self,
        confidence_level: float = 0.99,
        n_blocks: int = 252,  # 每年交易日数
        seed: int = 42,
    ):
        self.confidence_level = confidence_level
        self.n_blocks = n_blocks
        self.rng = np.random.RandomState(seed)

        logger.info(f"[ExtremeValueAnalyzer] 初始化完成 | Confidence={confidence_level} | N Blocks={n_blocks}")

    def fit_gpd(self, returns: np.ndarray, threshold: Optional[float] = None) -> GPDParameters:
        """
        使用POT方法拟合GPD分布

        Args:
            returns: 收益率序列
            threshold: 阈值(默认取95%分位数)

        Returns:
            GPDParameters对象
        """
        if threshold is None:
            threshold = np.percentile(returns, 95)

        # 选取超过阈值的极值
        excesses = returns[returns > threshold] - threshold

        if len(excesses) < 30:
            logger.warning(f"极值数量不足({len(excesses)}<30),GPD拟合可能不可靠")

        # 最大似然估计拟合GPD
        def neg_log_likelihood(params):
            xi, sigma = params
            if sigma <= 1e-6 or (xi == 0 and sigma <= abs(threshold)):
                return 1e10
            try:
                # GPD负对数似然函数
                term1 = np.log(sigma)
                term2 = (1 + 1 / xi) * np.log(np.maximum(1 + xi * (excesses - sigma) / sigma, 1e-10))
                ll = -np.sum(term1 + term2)
                return ll
            except Exception:
                return 1e10

        # 初始参数
        x0 = [0.1, max(np.std(excesses), 1e-4)]

        # 优化
        result = minimize(
            neg_log_likelihood, x0, method="Nelder-Mead", options={"maxiter": 10000, "xatol": 1e-8, "fatol": 1e-6}
        )

        xi, sigma = result.x
        sigma = max(sigma, 1e-6)  # 防止零或负值

        # 计算对数似然值
        log_likelihood = -neg_log_likelihood([xi, sigma])

        # KS检验
        ks_stat, ks_pvalue = self._kolmogorov_smirnov_test(returns, xi, sigma, threshold)

        return GPDParameters(
            xi=xi,
            sigma=sigma,
            threshold=threshold,
            log_likelihood=log_likelihood,
            kolmogorov_smirnov_stat=ks_stat,
            ks_pvalue=ks_pvalue,
        )

    def _kolmogorov_smirnov_test(
        self, returns: np.ndarray, xi: float, sigma: float, threshold: float
    ) -> Tuple[float, float]:
        """
        Kolmogorov-Smirnov检验GPD拟合优度

        Returns:
            (KS统计量, p值)
        """
        excesses = returns[returns > threshold] - threshold

        # 极值数量不足时返回默认值
        if len(excesses) < 10:
            logger.warning(f"极值数量不足({len(excesses)}<10),KS检验跳过")
            return 1.0, 0.0

        # GPD累积分布函数
        if abs(xi) > 1e-6:

            def cdf(x):
                return 1 - (1 + xi * x / sigma) ** (-1 / xi)
        else:

            def cdf(x):
                return 1 - np.exp(-x / sigma)

        # 计算经验CDF和理论CDF
        emp_cdf = np.arange(1, len(excesses) + 1) / len(excesses)
        theo_cdf = np.array([cdf(x) for x in sorted(excesses)])

        # KS统计量
        ks_stat = np.max(np.abs(emp_cdf - theo_cdf))

        # 近似p值(使用Kolmogorov分布)
        n = len(excesses)
        lambda_val = (np.sqrt(n) + 0.12 + 0.11 / np.sqrt(n)) * ks_stat
        p_value = 1 - stats.kolmogorov.cdf(lambda_val)

        return ks_stat, p_value

    def calculate_risk_metrics(
        self, gpd_params: GPDParameters, portfolio_value: float = 5_000_000, daily_volatility: float = 0.015
    ) -> RiskMetrics:
        """
        基于GPD参数计算风险指标

        Args:
            gpd_params: GPD参数
            portfolio_value: 组合市值
            daily_volatility: 日波动率

        Returns:
            RiskMetrics对象
        """
        xi = gpd_params.xi
        sigma = gpd_params.sigma

        # VaR计算(使用GPD外推)
        p_levels = [0.95, 0.975, 0.99, 0.995]
        vars = []

        for p in p_levels:
            if abs(xi) > 1e-6:
                # GPD分位数函数
                var = gpd_params.threshold + sigma * ((1 / (1 - p)) ** xi - 1) / xi
            else:
                var = gpd_params.threshold - sigma * np.log(1 / (1 - p))

            vars.append(var)

        # CVaR计算(Expected Shortfall)
        # ES_p = VaR_p + sigma*xi/(1-xi) * ((1/(1-p))**xi - 1)
        def calculate_cvar(var_p, p):
            if abs(xi) < 1e-6:
                return var_p + sigma
            else:
                return var_p + sigma * xi / (1 - xi) * ((1 / (1 - p)) ** xi - 1)

        cvar_99 = calculate_cvar(vars[2], 0.99)
        cvar_995 = calculate_cvar(vars[3], 0.995)

        # 肥尾判断
        is_heavy_tailed = xi > 0  # xi>0表示肥尾
        is_fat_enough = xi > 0.5  # xi>0.5表示非常肥尾

        return RiskMetrics(
            var_95=vars[0],
            var_975=vars[1],
            var_99=vars[2],
            var_995=vars[3],
            cvar_99=cvar_99,
            cvar_995=cvar_995,
            tail_index=xi,
            is_heavy_tailed=is_heavy_tailed,
            is_fat_enough=is_fat_enough,
        )

    def run_stress_tests(
        self, portfolio_returns: np.ndarray, portfolio_value: float = 5_000_000
    ) -> List[StressTestResult]:
        """
        历史极端情景压力测试

        测试场景:
        1. 2008金融海啸(-40%)
        2. 2015股灾(-35%)
        3. 2016熔断(-10%)
        4. 2020疫情崩盘(-30%)
        5. 2024年初量化踩踏(-25%)

        Args:
            portfolio_returns: 历史收益率序列
            portfolio_value: 组合市值

        Returns:
            StressTestResult列表
        """
        scenarios = {
            "2008_Financial_Crisis": -0.40,
            "2015_China_Market_Crash": -0.35,
            "2016_Circuit_Breaker": -0.10,
            "2020_Pandemic_Crash": -0.30,
            "2024_Quant_Liquidation": -0.25,
        }

        results = []

        for scenario_name, shock in scenarios.items():
            # 计算损失
            loss = portfolio_value * abs(shock)
            loss_pct = shock

            # 估算最大回撤(假设冲击期间回撤为损失的1.2倍)
            max_dd = shock * 1.2

            # 估算恢复时间(基于历史数据标准差)
            daily_vol = np.std(portfolio_returns)
            if daily_vol > 0:
                # 简化模型: 恢复时间 ∝ (回撤幅度 / 日均收益)^2
                avg_return = np.mean(portfolio_returns)
                if avg_return > 0:
                    time_to_recover = int(abs(max_dd) / avg_return)
                else:
                    time_to_recover = 252  # 默认1年
            else:
                time_to_recover = 252

            # 严重程度判断(损失>20%为严重)
            is_severe = abs(loss_pct) > 0.20

            results.append(
                StressTestResult(
                    scenario_name=scenario_name,
                    portfolio_value=portfolio_value,
                    loss=loss,
                    loss_pct=loss_pct,
                    max_drawdown=max_dd,
                    time_to_recover_days=time_to_recover,
                    is_severe=is_severe,
                )
            )

        return results

    def monte_carlo_cvar(
        self,
        n_simulations: int = 2000,
        horizon_days: int = 1,
        portfolio_value: float = 5_000_000,
        annual_return: float = 0.08,
        annual_vol: float = 0.20,
    ) -> float:
        """
        蒙特卡洛模拟计算CVaR

        Args:
            n_simulations: 模拟路径数
            horizon_days: 预测 horizon(天)
            portfolio_value: 组合市值
            annual_return: 年化预期收益
            annual_vol: 年化波动率

        Returns:
            99% CVaR金额
        """
        # 日度和日收益
        daily_vol = annual_vol / np.sqrt(252)
        daily_return = annual_return / 252

        # 模拟收益率分布
        simulated_returns = self.rng.normal(daily_return, daily_vol, n_simulations)

        # 计算PnL分布
        pnl = simulated_returns * portfolio_value

        # 计算99% CVaR(尾部期望)
        var_99 = np.percentile(pnl, 1)  # 1%分位数
        tail_pnl = pnl[pnl <= var_99]
        cvar_99 = np.mean(tail_pnl)

        return abs(cvar_99)

    def generate_report(
        self,
        gpd_params: GPDParameters,
        risk_metrics: RiskMetrics,
        stress_tests: List[StressTestResult],
        mc_cvar: float,
        portfolio_value: float = 5_000_000,
    ) -> EVTRiskReport:
        """
        生成EVT风险报告

        Args:
            gpd_params: GPD参数
            risk_metrics: 风险指标
            stress_tests: 压力测试结果
            mc_cvar: 蒙特卡洛CVaR
            portfolio_value: 组合市值

        Returns:
            EVTRiskReport对象
        """
        warnings = []
        recommendations = []
        status = "OK"

        # 1. 肥尾检测
        if risk_metrics.is_fat_enough:
            warnings.append(f"[CRITICAL] 检测到严重肥尾效应(Tail Index={risk_metrics.tail_index:.3f}>0.5)")
            recommendations.append("尾部风险极高,必须使用期权对冲或降低仓位")
            status = "WARNING"

        elif risk_metrics.is_heavy_tailed:
            warnings.append(f"[WARNING] 检测到肥尾效应(Tail Index={risk_metrics.tail_index:.3f}>0)")
            recommendations.append("考虑使用Vega对冲模块管理波动率风险")

        # 2. VaR检查
        var_99_pct = risk_metrics.var_99
        if var_99_pct > 0.05:  # 日VaR超过5%
            warnings.append(f"[CRITICAL] 99% VaR过高({var_99_pct * 100:.2f}%>5%)")
            recommendations.append("立即减少风险敞口,日频调仓检查Delta中性")
            status = "CRITICAL"

        # 3. 压力测试检查
        severe_scenarios = [st for st in stress_tests if st.is_severe]
        if severe_scenarios:
            worst_loss = max(st.loss for st in severe_scenarios)
            warnings.append(
                f"[CRITICAL] 极端情景最大损失{worst_loss:,.0f}元 ({worst_loss / portfolio_value * 100:.1f}%)"
            )
            recommendations.append("必须为黑天鹅事件保留缓冲,期权对冲或现金储备至少覆盖99% VaR敞口")
            status = "CRITICAL"

        # 4. CVaR检查
        if mc_cvar > portfolio_value * 0.01:  # CVaR超过净值1%
            warnings.append(
                f"[WARNING] 蒙特卡洛99% CVaR={mc_cvar:,.0f}元 (占NAV {mc_cvar / portfolio_value * 100:.2f}%)"
            )
            recommendations.append("CVaR超过预期alpha收益的30%,需重新评估对冲方案")

        # 5. KS检验
        if gpd_params.ks_pvalue < 0.05:
            warnings.append(f"[WARNING] GPD拟合不佳(KS p-value={gpd_params.ks_pvalue:.4f}<0.05)")
            recommendations.append("考虑使用更高阈值或更换分布拟合方法")

        return EVTRiskReport(
            timestamp=datetime.now(),
            gpd_params=gpd_params,
            risk_metrics=risk_metrics,
            stress_tests=stress_tests,
            monte_carlo_cvar=mc_cvar,
            warnings=warnings,
            recommendations=recommendations,
            status=status,
        )


if __name__ == "__main__":
    # 测试示例
    print("极值理论(EVT)肥尾建模模块测试\n")
    print("=" * 60)

    # 生成模拟收益率数据(包含肥尾特征)
    np.random.seed(42)
    n_days = 500

    # 使用t分布生成肥尾收益率(自由度=3)
    returns = np.random.standard_t(df=3, size=n_days) * 0.015

    # 注入极端事件
    extreme_events = [-0.08, -0.12, 0.10, -0.15, 0.07]  # 2008, 2015, 2020等
    for i, event in enumerate(extreme_events):
        idx = int(i * n_days / len(extreme_events))
        returns[idx] = event

    # 创建分析器
    analyzer = ExtremeValueAnalyzer(confidence_level=0.99)

    # 1. 拟合GPD
    print("\n1. 拟合GPD分布...")
    gpd_params = analyzer.fit_gpd(returns, threshold=0.95)
    print(f"   形状参数(xi): {gpd_params.xi:.4f}")
    print(f"   尺度参数(sigma): {gpd_params.sigma:.4f}")
    print(f"   阈值: {gpd_params.threshold:.4f}")
    print(f"   对数似然: {gpd_params.log_likelihood:.2f}")
    print(f"   KS统计量: {gpd_params.kolmogorov_smirnov_stat:.4f}")
    print(f"   KS p-value: {gpd_params.ks_pvalue:.4f}")

    # 2. 计算风险指标
    print("\n2. 计算风险指标...")
    risk_metrics = analyzer.calculate_risk_metrics(gpd_params, portfolio_value=5_000_000, daily_volatility=0.015)
    print(f"   95% VaR: {risk_metrics.var_95 * 100:.2f}%")
    print(f"   99% VaR: {risk_metrics.var_99 * 100:.2f}%")
    print(f"   99.5% VaR: {risk_metrics.var_995 * 100:.2f}%")
    print(f"   99% CVaR: {risk_metrics.cvar_99 * 100:.2f}%")
    print(f"   99.5% CVaR: {risk_metrics.cvar_995 * 100:.2f}%")
    print(f"   肥尾检测: {'是' if risk_metrics.is_heavy_tailed else '否'}")
    print(f"   严重肥尾: {'是' if risk_metrics.is_fat_enough else '否'}")

    # 3. 压力测试
    print("\n3. 历史极端情景压力测试...")
    stress_tests = analyzer.run_stress_tests(returns, portfolio_value=5_000_000)
    for st in stress_tests:
        severity = "[SEVERE]" if st.is_severe else "[OK]"
        print(f"   {severity} {st.scenario_name}: 损失{st.loss:,.0f}元 ({st.loss_pct * 100:.1f}%)")

    # 4. 蒙特卡洛模拟
    print("\n4. 蒙特卡洛模拟(2000路径)...")
    mc_cvar = analyzer.monte_carlo_cvar(n_simulations=2000, portfolio_value=5_000_000)
    print(f"   99% CVaR: {mc_cvar:,.0f}元")

    # 5. 生成报告
    print("\n5. 生成EVT风险报告...")
    report = analyzer.generate_report(
        gpd_params=gpd_params,
        risk_metrics=risk_metrics,
        stress_tests=stress_tests,
        mc_cvar=mc_cvar,
        portfolio_value=5_000_000,
    )

    print(f"\n状态: {report.status}")
    if report.warnings:
        print("\n[WARNING] 警告:")
        for w in report.warnings:
            print(f"  - {w}")

    if report.recommendations:
        print("\n[RECOMMENDATION] 建议:")
        for r in report.recommendations:
            print(f"  - {r}")

    print("=" * 60)
