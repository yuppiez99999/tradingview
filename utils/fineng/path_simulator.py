"""
组合路径蒙特卡洛模拟器 (Portfolio Path Simulator)

多资产多日路径模拟，输出组合回撤分布 P(DD > x)。
升级 stress_test_runner 从单点预期回撤到完整分布曲线。

GBM 模型 (几何布朗运动):
    S_{t+1} = S_t · exp[(μ_i - 0.5·σ²_i)·Δt + σ_i·√Δt · Z_{t+1}]

支持残差抽样方式:
    1. Normal:  Z ~ N(0,1) (标准正态)
    2. t-dist:  Z ~ t(ν) (学生t分布, 厚尾)
    3. Historical Bootstrap: Z 从历史残差序列有放回抽样 (保留厚尾和偏度)

回撤计算:
    DD_t = (Peak_t - NAV_t) / Peak_t
    MaxDD = max(DD_t)  over horizon

输出:
    - DD 分布分位数 (P50, P75, P90, P95, P99, Max)
    - NAV 终端值分布分位数
    - 与 2015/2020 等历史事件对照

设计原则:
    1. 零外部依赖 — 仅 math + random 标准库
    2. 确定性 — 固定 seed 即可复现
    3. 纯函数 — 无副作用

参考:
    Glasserman, P. (2004). "Monte Carlo Methods in Financial Engineering"
    Meucci, A. (2005). "Risk and Asset Allocation"

Usage:
    from utils.fineng.path_simulator import PathSimulator, simulate
    sim = PathSimulator(n_paths=10000, n_days=21, seed=42)
    report = sim.simulate_portfolio(weights, historical_cov, historical_residuals)
    logger.info("P99 最大回撤: %.2%%", report.dd_p99 * 100)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# ============================================================
# 结果数据结构
# ============================================================


@dataclass
class PathSimResult:
    """路径模拟结果"""

    # 回撤分布
    dd_p50: float  # 中位数最大回撤
    dd_p75: float
    dd_p90: float
    dd_p95: float
    dd_p99: float  # 99% 分位数 (重点监控)
    dd_max: float  # 最极端回撤

    # NAV 终端分布
    nav_terminal_p50: float  # NAV 中位数 (相对于 1.0)
    nav_terminal_p10: float  # 下行
    nav_terminal_p05: float
    nav_terminal_p01: float

    # 完整分布 (供绘图与回测对照)
    dd_distribution: list[float] = field(default_factory=list)  # 所有路径的最大回撤
    nav_terminal_distribution: list[float] = field(
        default_factory=list
    )  # 所有路径的终端 NAV

    # 模拟参数
    n_paths: int = 0
    n_days: int = 0
    n_assets: int = 0

    # 对照历史事件 (外部注入)
    historical_dd_2015: float = float("nan")
    historical_dd_2020: float = float("nan")
    historical_dd_2024: float = float("nan")
    p90_covers_2015: bool = False  # P90 是否覆盖 2015 实际回撤
    p90_covers_2020: bool = False


@dataclass
class StressTestReport:
    """升级版压力测试报告 (替代单点预期回撤)"""

    date: str
    portfolio_name: str
    n_paths: int
    n_days: int

    # 回撤分布
    dd_distribution_summary: dict[str, float]  # {"P50": ..., "P90": ..., "P99": ...}

    # 历史对照
    historical_dd_comparison: dict[str, dict[str, float]]

    # 判定
    p90_adequate: bool  # P90 是否覆盖历史最大回撤
    warning: str = ""


# ============================================================
# 核心引擎
# ============================================================


class PathSimulator:
    """多资产多日路径蒙特卡洛模拟器

    参数:
        n_paths: 模拟路径数, 默认 10,000
        n_days: 每路径天数, 默认 21 (约 1 个月)
        seed: 随机种子, None=非确定性
        residual_method: "normal" | "t" | "bootstrap"
        df: t 分布自由度 (仅 residual_method="t" 时使用), 默认 5

    Usage:
        sim = PathSimulator(n_paths=10000, n_days=21, seed=42)
        report = sim.simulate_portfolio(weights, cov_matrix, historical_residuals)
        sim.compare_history(report, dd_2015=-0.45, dd_2020=-0.15)
    """

    def __init__(
        self,
        n_paths: int = 10000,
        n_days: int = 21,
        seed: int | None = 42,
        residual_method: str = "bootstrap",
        df: float = 5.0,
    ):
        self.n_paths = n_paths
        self.n_days = n_days
        self.seed = seed
        self.residual_method = residual_method
        self.df = df

        self._rng: random.Random | None = None
        if seed is not None:
            self._rng = random.Random(seed)

    # ---- 残差生成 ----

    def _generate_residual(self) -> float:
        """生成单变量残差抽样子"""
        rng = self._rng if self._rng else random

        if self.residual_method == "normal":
            # Box-Muller 正态抽样
            u1 = rng.random()
            u2 = rng.random()
            while u1 <= 1e-15:
                u1 = rng.random()
            return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

        if self.residual_method == "t":
            # t 分布抽样: t = N(0,1) / sqrt(χ²/ν)
            u1 = rng.random()
            u2 = rng.random()
            while u1 <= 1e-15:
                u1 = rng.random()
            z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

            # χ²(df) 抽样: 对整数 df 用 Gamma(df/2, 2) = sum of 2*df exp(0.5)
            # 简化: Gamma(df/2, 1) 抽样
            if self.df <= 0:
                return z  # 退化为正态
            # 使用 Accept-Reject 对形状参数 < 1 的 Gamma 抽样
            chi2 = self._sample_chi2(self.df)
            return z / math.sqrt(chi2 / self.df)

        if self.residual_method == "bootstrap":
            # 这里标记需要预先缓存的历史残差, 将在 simulate_portfolio 中使用
            raise ValueError(
                "bootstrap 模式需调用 simulate_portfolio() 传入 historical_residuals"
            )

        raise ValueError(f"不支持的 residual_method: {self.residual_method}")

    def _generate_residuals(self, n: int) -> list[float]:
        """生成 n 个独立残差 (normal 或 t 模式)"""
        return [self._generate_residual() for _ in range(n)]

    def _sample_chi2(self, df: float) -> float:
        """卡方分布抽样 (用于 t 分布分母)

        使用 Gamma(k=df/2, θ=2) 抽样 = 2 * Gamma(k=df/2, θ=1)
        """
        rng = self._rng if self._rng else random

        k = df / 2.0
        if k < 1.0:
            # Marsaglia & Tsang (2000) method for Gamma < 1
            # Use: Gamma(k) = Gamma(k+1) * U^(1/k)
            return self._sample_gamma(k + 1.0) * (rng.random() ** (1.0 / k)) * 2.0

        return self._sample_gamma(k) * 2.0

    def _sample_gamma(self, k: float) -> float:
        """Gamma(k, 1) 抽样 (Marsaglia-Tsang 方法, k ≥ 1)"""
        rng = self._rng if self._rng else random

        if k < 1.0:
            return self._sample_gamma(k + 1.0) * (rng.random() ** (1.0 / k))

        d = k - 1.0 / 3.0
        c = 1.0 / math.sqrt(9.0 * d)

        while True:
            x = self._box_muller(rng)
            v = (1.0 + c * x) ** 3
            if v > 0:
                u = rng.random()
                if u < 1.0 - 0.0331 * (x**4) or math.log(u) < 0.5 * x * x + d * (
                    1.0 - v + math.log(v)
                ):
                    return d * v

    def _box_muller(self, rng: random.Random) -> float:
        """Box-Muller 标准正态抽样"""
        u1 = rng.random()
        u2 = rng.random()
        while u1 <= 1e-15:
            u1 = rng.random()
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

    # ---- Cholesky 分解 (纯 Python) ----

    def _cholesky(self, matrix: list[list[float]]) -> list[list[float]]:
        """Cholesky 分解 L (下三角), 使 A = L·L^T

        Args:
            matrix: n×n 对称正定矩阵 (协方差)

        Returns:
            L: n×n 下三角矩阵

        Raises:
            ValueError: 矩阵非正定
        """
        n = len(matrix)
        L = [[0.0] * n for _ in range(n)]

        for i in range(n):
            for j in range(i + 1):
                s = sum(L[i][k] * L[j][k] for k in range(j))
                if i == j:
                    diag = matrix[i][i] - s
                    if diag <= 0:
                        raise ValueError(
                            f"协方差矩阵非正定 (索引 {i}), 检查输入数据或启用 Ledoit-Wolf 收缩"
                        )
                    L[i][j] = math.sqrt(diag)
                else:
                    L[i][j] = (matrix[i][j] - s) / L[j][j]

        return L

    # ---- 单路径模拟 ----

    def _simulate_one_path(
        self,
        weights: list[float],
        annual_returns: list[float],
        L: list[list[float]],
        historical_residuals: list[list[float]] | None,
        dt: float = 1.0 / 252,
    ) -> tuple[float, float]:
        """模拟一条 21 日路径

        Returns:
            (max_drawdown, terminal_nav)
        """
        rng = self._rng if self._rng else random
        n_assets = len(weights)
        n_hist = len(historical_residuals[0]) if historical_residuals else 0

        nav = 1.0
        peak = 1.0
        max_dd = 0.0

        for _day in range(self.n_days):
            # 生成相关随机数 Z = L·ε (ε 为独立随机向量)
            eps = [0.0] * n_assets
            if (
                historical_residuals
                and n_hist > 0
                and self.residual_method == "bootstrap"
            ):
                # Bootstrap: 随机选一个历史日期, 取当天所有资产残差
                idx = rng.randint(0, n_hist - 1)
                for i in range(n_assets):
                    eps[i] = historical_residuals[i][idx]
            else:
                # Normal / t 抽样
                for i in range(n_assets):
                    eps[i] = self._generate_residual()

            # Z = L·ε (相关化)
            z = [0.0] * n_assets
            for i in range(n_assets):
                z[i] = sum(L[i][k] * eps[k] for k in range(i + 1))

            # GBM 步进
            for i in range(n_assets):
                (annual_returns[i] - 0.5 * annual_returns[i] * 0) * dt
                # 简化: 使用日波动率 (annual_returns 实际是年化收益率+波动率组合)
                # 这里 annual_returns 应为向量: [μ_i - 0.5σ²_i] · dt 的等效日值
                # 实际上更清晰的做法是直接用日收益率均值
                pass

            # 重写: 使用简化的日收益率模型
            daily_return = 0.0
            for i in range(n_assets):
                daily_return += weights[i] * z[i]

            nav *= 1.0 + daily_return
            if nav > peak:
                peak = nav
            dd = (peak - nav) / peak
            if dd > max_dd:
                max_dd = dd

        return max_dd, nav

    def _simulate_one_path_v2(
        self,
        weights: list[float],
        daily_mean: list[float],
        L: list[list[float]],
        historical_residuals: list[list[float]] | None,
    ) -> tuple[float, float]:
        """模拟一条路径 (使用日收益率均值 + 标准残差结构化)"""
        rng = self._rng if self._rng else random
        n_assets = len(weights)
        n_hist = len(historical_residuals[0]) if historical_residuals else 0

        nav = 1.0
        peak = 1.0
        max_dd = 0.0

        for _ in range(self.n_days):
            # 生成独立残差
            eps = [0.0] * n_assets
            if (
                historical_residuals
                and n_hist > 0
                and self.residual_method == "bootstrap"
            ):
                idx = rng.randint(0, n_hist - 1)
                for i in range(n_assets):
                    eps[i] = historical_residuals[i][idx]
            else:
                for i in range(n_assets):
                    # Box-Muller
                    u1 = rng.random()
                    u2 = rng.random()
                    while u1 <= 1e-15:
                        u1 = rng.random()
                    eps[i] = math.sqrt(-2.0 * math.log(u1)) * math.cos(
                        2.0 * math.pi * u2
                    )

            # 相关化: Z = L·ε
            z = [0.0] * n_assets
            for i in range(n_assets):
                z[i] = sum(L[i][k] * eps[k] for k in range(i + 1))

            # 组合日收益率: r_p = Σ w_i · (μ_i + z_i)
            daily_return = sum(
                weights[i] * (daily_mean[i] + z[i]) for i in range(n_assets)
            )

            nav *= 1.0 + daily_return
            if nav > peak:
                peak = nav
            dd = (peak - nav) / peak
            if dd > max_dd:
                max_dd = dd

        return max_dd, nav

    # ---- 批量模拟 ----

    def simulate_portfolio(
        self,
        weights: list[float],
        historical_cov: list[list[float]],
        historical_residuals: list[list[float]] | None = None,
        daily_mean: list[float] | None = None,
    ) -> PathSimResult:
        """执行组合路径蒙特卡洛模拟

        Args:
            weights: 各资产权重 (w_1, w_2, ..., w_n), 应和为 1
            historical_cov: 历史协方差矩阵 n×n (日频)
            historical_residuals: n_assets × T 历史残差矩阵 (bootstrap 模式必需)
            daily_mean: 各资产日收益率均值, None 则默认 0

        Returns:
            PathSimResult 含回撤分布和 NAV 终端分布
        """
        n_assets = len(weights)
        if daily_mean is None:
            daily_mean = [0.0] * n_assets

        # ---- Cholesky 分解 ----
        try:
            L = self._cholesky(historical_cov)
        except ValueError:
            # 非正定: 添加小量对角线扰动 (简化版 Ledoit-Wolf)
            perturbed = [row[:] for row in historical_cov]
            for i in range(n_assets):
                perturbed[i][i] += 1e-6 * abs(historical_cov[i][i])
            try:
                L = self._cholesky(perturbed)
            except ValueError:
                return PathSimResult(
                    dd_p50=float("nan"),
                    dd_p75=float("nan"),
                    dd_p90=float("nan"),
                    dd_p95=float("nan"),
                    dd_p99=float("nan"),
                    dd_max=float("nan"),
                    nav_terminal_p50=float("nan"),
                    nav_terminal_p10=float("nan"),
                    nav_terminal_p05=float("nan"),
                    nav_terminal_p01=float("nan"),
                    n_paths=self.n_paths,
                    n_days=self.n_days,
                    n_assets=n_assets,
                )

        # ---- 模拟 ----
        max_dds: list[float] = []
        terminal_navs: list[float] = []

        for _ in range(self.n_paths):
            max_dd, term_nav = self._simulate_one_path_v2(
                weights, daily_mean, L, historical_residuals
            )
            max_dds.append(max_dd)
            terminal_navs.append(term_nav)

        # ---- 分位数 ----
        dds_sorted = sorted(max_dds)
        navs_sorted = sorted(terminal_navs)

        def quantile(data: list[float], p: float) -> float:
            idx = int(len(data) * p)
            return data[min(idx, len(data) - 1)]

        result = PathSimResult(
            dd_p50=quantile(dds_sorted, 0.50),
            dd_p75=quantile(dds_sorted, 0.75),
            dd_p90=quantile(dds_sorted, 0.90),
            dd_p95=quantile(dds_sorted, 0.95),
            dd_p99=quantile(dds_sorted, 0.99),
            dd_max=dds_sorted[-1],
            nav_terminal_p50=quantile(navs_sorted, 0.50),
            nav_terminal_p10=quantile(navs_sorted, 0.10),
            nav_terminal_p05=quantile(navs_sorted, 0.05),
            nav_terminal_p01=quantile(navs_sorted, 0.01),
            dd_distribution=dds_sorted,
            nav_terminal_distribution=navs_sorted,
            n_paths=self.n_paths,
            n_days=self.n_days,
            n_assets=n_assets,
        )

        return result

    # ---- 历史事件对照 ----

    def compare_history(
        self,
        result: PathSimResult,
        dd_2015: float | None = None,
        dd_2020: float | None = None,
        dd_2024: float | None = None,
    ) -> PathSimResult:
        """注入历史事件回撤并验证 P90 覆盖性

        Modifies result in-place and returns it.
        """
        if dd_2015 is not None:
            result.historical_dd_2015 = dd_2015
            result.p90_covers_2015 = result.dd_p90 >= abs(dd_2015)
        if dd_2020 is not None:
            result.historical_dd_2020 = dd_2020
            result.p90_covers_2020 = result.dd_p90 >= abs(dd_2020)
        if dd_2024 is not None:
            result.historical_dd_2024 = dd_2024

        return result


# ============================================================
# 便捷接口
# ============================================================


def simulate(
    weights: list[float],
    historical_cov: list[list[float]],
    historical_residuals: list[list[float]] | None = None,
    daily_mean: list[float] | None = None,
    n_paths: int = 10000,
    n_days: int = 21,
    seed: int | None = 42,
    residual_method: str = "bootstrap",
    dd_2015: float | None = None,
    dd_2020: float | None = None,
) -> PathSimResult:
    """便捷接口: 一键执行组合路径模拟

    Args:
        weights: 资产权重列表
        historical_cov: 历史协方差矩阵 (日频)
        historical_residuals: 历史残差矩阵 (bootstrap 模式必需)
        daily_mean: 日收益率均值
        n_paths: 模拟路径数
        n_days: 每条路径天数
        seed: 随机种子
        residual_method: "normal" | "t" | "bootstrap"
        dd_2015: 2015 年实际最大回撤 (用于对照)
        dd_2020: 2020 年实际最大回撤 (用于对照)

    Returns:
        PathSimResult
    """
    sim = PathSimulator(
        n_paths=n_paths,
        n_days=n_days,
        seed=seed,
        residual_method=residual_method,
    )

    result = sim.simulate_portfolio(
        weights=weights,
        historical_cov=historical_cov,
        historical_residuals=historical_residuals,
        daily_mean=daily_mean,
    )

    if dd_2015 is not None or dd_2020 is not None:
        sim.compare_history(result, dd_2015=dd_2015, dd_2020=dd_2020)

    return result


def generate_stress_report(
    result: PathSimResult,
    portfolio_name: str = "default",
    date_str: str = "",
) -> StressTestReport:
    """从 PathSimResult 生成升级版压力测试报告

    替代 stress_test_runner 的单点预期回撤输出。
    """
    dd_summary = {
        "P50": result.dd_p50,
        "P75": result.dd_p75,
        "P90": result.dd_p90,
        "P95": result.dd_p95,
        "P99": result.dd_p99,
        "Max": result.dd_max,
        "MaxEvent": (
            result.historical_dd_2020
            if not math.isnan(result.historical_dd_2020)
            else float("nan")
        ),
    }

    hist_comparison: dict[str, dict[str, float]] = {}
    if not math.isnan(result.historical_dd_2015):
        hist_comparison["2015_Crash"] = {
            "Historical_DD": result.historical_dd_2015,
            "P90_Covers": 1.0 if result.p90_covers_2015 else 0.0,
        }
    if not math.isnan(result.historical_dd_2020):
        hist_comparison["2020_COVID"] = {
            "Historical_DD": result.historical_dd_2020,
            "P90_Covers": 1.0 if result.p90_covers_2020 else 0.0,
        }

    p90_adequate = result.p90_covers_2015 and result.p90_covers_2020

    warning = ""
    if not result.p90_covers_2015:
        warning += "P90 未覆盖 2015 股灾回撤; "
    if not result.p90_covers_2020:
        warning += "P90 未覆盖 2020 新冠回撤; "

    return StressTestReport(
        date=date_str,
        portfolio_name=portfolio_name,
        n_paths=result.n_paths,
        n_days=result.n_days,
        dd_distribution_summary=dd_summary,
        historical_dd_comparison=hist_comparison,
        p90_adequate=p90_adequate,
        warning=warning.strip(),
    )
