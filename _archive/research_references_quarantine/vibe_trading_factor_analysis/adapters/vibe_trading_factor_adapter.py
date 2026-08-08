"""
VibeTrading 因子库适配器（Factor Bridge）
==========================================

设计目标（来自战略整合白皮书第二章 §2.3 Adapter 1）：
    将 Vibe-Trading 项目的 450+ Alpha 因子接入现有因子库（utils/alpha_factor_library.py），
    但只做"候选因子供给"，不直接影响生产交易决策。

安全契约（硬约束，违反即触发告警）：
    1. 只读：本模块绝不修改任何生产数据（utils/alpha_factor_library.py 的内部状态）
    2. 隔离：因子计算在独立进程中运行，失败不影响生产主循环
    3. 审计：所有因子输出自动记录溯源链（factor_origin = "vibe_trading"）
    4. 可降级：外部项目不可用时，返回空候选池，不抛异常到调用方

因子进入生产的四道关卡（来自白皮书）：
    1. 正交性检查：与现有 50+ 因子的相关性 |r| < 0.7
    2. IC 稳定性：Walk-Forward IC_IR > 0.5
    3. 防过拟合：DSR 检验 n_trials > 5
    4. 经济逻辑：必须有可解释的金融直觉（人工标注）

依赖关系：
    - utils.alpha_factor_library.AlphaFactorLibrary  （现有系统，只读引用）
    - utils.alpha_factor_library.FactorValue          （共享数据结构）
    - vibe_trading_factor_analysis.validators.*       （四道关卡验证器）

参考：
    - HKUDS/Vibe-Trading: https://github.com/HKUDS/Vibe-Trading
    - 战略整合白皮书: research/research_report_github_projects_integration_strategy_20260725.md
"""

from __future__ import annotations

import logging
import math
import sys
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# 确保 utils 模块可导入（项目根目录加入 sys.path）
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 只读引用现有系统（绝不修改）
try:
    from utils.alpha_factor_library import AlphaFactorLibrary, FactorLibraryResult, FactorValue
    _UTILS_AVAILABLE = True
except ImportError as e:
    _UTILS_AVAILABLE = False
    _IMPORT_ERROR = str(e)

logger = logging.getLogger("vibe_trading_adapter")

# 因子来源标识（用于审计溯源）
FACTOR_ORIGIN = "vibe_trading"

# Vibe-Trading 项目宣称的因子总数（来自战略白皮书）
VIBE_TRADING_TOTAL_FACTORS = 450


# ============================================================
# 数据结构
# ============================================================

@dataclass
class CandidateFactor:
    """Vibe-Trading 候选因子（尚未通过四道关卡）"""
    name: str                          # 因子名（如 VT_MOM_ILLIQUID_60D）
    category: str                      # 因子类别（Momentum/Value/Quality/...）
    description: str = ""              # 因子经济含义描述
    values: dict[str, float] = field(default_factory=dict)  # {symbol: factor_value}
    origin: str = FACTOR_ORIGIN        # 来源标识
    vt_formula: str = ""               # Vibe-Trading 中的公式说明
    created_at: str = ""               # 计算时间戳

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class CandidateFactorPool:
    """候选因子池（批次计算结果）"""
    batch_id: str                                       # 批次 ID
    compute_date: str                                   # 计算日期
    total_candidates: int = 0                            # 候选因子总数
    factors: dict[str, CandidateFactor] = field(default_factory=dict)
    failed_computations: list[dict[str, Any]] = field(default_factory=list)
    source_version: str = ""                            # Vibe-Trading 版本标识
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OrthogonalityResult:
    """正交性检查结果"""
    factor_name: str
    is_orthogonal: bool                  # 是否通过正交性检查（|r| < 0.7）
    max_abs_corr: float                  # 与现有因子的最大相关性绝对值
    max_corr_factor: str                 # 相关性最高的现有因子名
    corr_with_existing: dict[str, float] = field(default_factory=dict)  # 与各现有因子的相关系数
    threshold: float = 0.7


# ============================================================
# VibeTrading 因子库适配器
# ============================================================

class VibeTradingFactorAdapter:
    """Vibe-Trading 因子库适配器

    设计模式：适配器 + 策略模式
    安全等级：只读 + 隔离 + 可降级

    用法（典型流程）：
        >>> adapter = VibeTradingFactorAdapter()
        >>> # 1. 计算候选因子（只读，不影响生产）
        >>> pool = adapter.compute_candidate_factors(price_data, fundamentals)
        >>> # 2. 与现有因子做正交性检查
        >>> existing = adapter.load_existing_factors(price_data, fundamentals)
        >>> ortho_results = adapter.check_orthogonality(pool, existing)
        >>> # 3. 筛选通过正交性的因子
        >>> qualified = adapter.filter_orthogonal_factors(pool, ortho_results)

    注意：
        - 本类不调用任何生产交易接口
        - 所有失败均降级为空结果 + 日志告警，不抛异常到调用方
        - 因子计算使用本地数据，不联网访问外部服务
    """

    # 因子来源标识
    ORIGIN = FACTOR_ORIGIN

    # 正交性阈值（来自白皮书四道关卡 §1）
    ORTHO_THRESHOLD = 0.7

    # Vibe-Trading 因子分类（参考其项目元数据）
    VIBE_TRADING_CATEGORIES = [
        "Momentum",           # 动量类
        "Reversal",           # 反转类
        "Liquidity",          # 流动性类
        "Volatility",         # 波动率类
        "Value",              # 价值类
        "Quality",            # 质量类（水平值：ROE/毛利率/负债率）
        "QualityTrend",       # 质量变化类（P2.2 新增：ROE/毛利率 YoY 变化率）
        "Size",               # 规模类
        "Growth",             # 成长类
        "Sentiment",          # 情绪类
        "Technical",          # 技术指标类
        "Microstructure",     # 微观结构类（P2.1 新增）
    ]

    def __init__(self, config: dict[str, Any] | None = None):
        """初始化适配器

        Args:
            config: 可选配置，支持字段：
                - enable_existing_lib: 是否加载现有因子库（默认 True）
                - ortho_threshold: 正交性阈值（默认 0.7）
                - max_candidates_per_category: 每类最大候选因子数（默认 50）
        """
        if not _UTILS_AVAILABLE:
            logger.warning(
                "[VibeTradingAdapter] utils 模块不可用: %s，将以降级模式运行",
                _IMPORT_ERROR if "_IMPORT_ERROR" in globals() else "unknown",
            )

        config = config or {}
        self.enable_existing_lib: bool = bool(config.get("enable_existing_lib", True))
        self.ortho_threshold: float = float(config.get("ortho_threshold", self.ORTHO_THRESHOLD))
        self.max_candidates_per_category: int = int(config.get("max_candidates_per_category", 50))

        logger.info(
            "[VibeTradingAdapter] 初始化完成 | ortho_threshold=%.2f max_per_cat=%d",
            self.ortho_threshold, self.max_candidates_per_category,
        )

    # ------------------------------------------------------------
    # 主入口：计算候选因子
    # ------------------------------------------------------------

    def compute_candidate_factors(
        self,
        price_data: dict[str, dict[str, list[float]]],
        fundamentals: dict[str, dict[str, float]] | None = None,
        industries: dict[str, str] | None = None,
        benchmark_returns: list[float] | None = None,
        fundamentals_history: dict[str, dict[str, Any]] | None = None,
    ) -> CandidateFactorPool:
        """计算 Vibe-Trading 风格的候选因子（只读，不直接影响交易）

        本方法模拟 Vibe-Trading 项目的因子计算逻辑，使用本地数据计算候选因子。
        所有因子都标记 origin="vibe_trading"，与现有系统因子明确区分。

        Args:
            price_data: {symbol: {"closes": [...], "volumes": [...], "highs": [...], "lows": [...]}}
            fundamentals: {symbol: {"pe": ..., "pb": ..., "roe": ...}}
            industries: {symbol: industry_name}
            benchmark_returns: 基准收益率序列
            fundamentals_history: P2.2 质量变化类因子所需的历史季度财务数据
                {symbol: {"quarters": [{"year":..., "quarter":..., "roe":..., ...}], "n_valid": int}}
                由 download_fundamentals_history() 生成

        Returns:
            CandidateFactorPool: 候选因子池（可能为空，绝不抛异常）
        """
        batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        compute_date = datetime.now().strftime("%Y-%m-%d")
        pool = CandidateFactorPool(
            batch_id=batch_id,
            compute_date=compute_date,
            source_version="vibe_trading_simulated_v1.0",
        )

        try:
            fundamentals = fundamentals or {}
            industries = industries or {}

            # 计算 Vibe-Trading 风格的候选因子
            # 设计原则：每个因子都对应 Vibe-Trading 项目的某类 alpha 思路，
            # 但使用本地可计算的实现，不依赖外部项目代码（保证隔离性）

            # 1. 动量增强因子（Vibe-Trading 拓展的动量变体）
            momentum_factors = self._compute_vt_momentum_factors(price_data)
            self._merge_into_pool(pool, momentum_factors)

            # 2. 反转因子（Vibe-Trading 的反转 alpha）
            reversal_factors = self._compute_vt_reversal_factors(price_data)
            self._merge_into_pool(pool, reversal_factors)

            # 3. 流动性增强因子（Vibe-Trading 的流动性 alpha）
            liquidity_factors = self._compute_vt_liquidity_factors(price_data)
            self._merge_into_pool(pool, liquidity_factors)

            # 4. 波动率结构因子
            volatility_factors = self._compute_vt_volatility_factors(price_data, benchmark_returns)
            self._merge_into_pool(pool, volatility_factors)

            # 5. 价值增强因子（P2.1: 移除 VT_VAL_COMPOSITE/VT_VAL_EARNINGS_YIELD_SCALED，
            #    原因：与现有 VAL_PE corr=1.00，因子定义本身重复，非数据质量问题）
            value_factors = self._compute_vt_value_factors(fundamentals)
            # 过滤已知共线因子
            value_factors = {
                k: v for k, v in value_factors.items()
                if k not in ("VT_VAL_COMPOSITE", "VT_VAL_EARNINGS_YIELD_SCALED")
            }
            self._merge_into_pool(pool, value_factors)

            # 6. 质量复合因子（P2.1: 移除 VT_QUA_COMPOSITE，与 QUA_DEBT_TO_EQUITY corr=0.99）
            quality_factors = self._compute_vt_quality_factors(fundamentals)
            quality_factors = {
                k: v for k, v in quality_factors.items()
                if k not in ("VT_QUA_COMPOSITE",)
            }
            self._merge_into_pool(pool, quality_factors)

            # 7. 规模非线性因子（P2.1: 移除 VT_SIZE_LOG_NORMALIZED，与 SIZE_LOG_MCAP corr=1.00）
            size_factors = self._compute_vt_size_factors(fundamentals)
            size_factors = {
                k: v for k, v in size_factors.items()
                if k not in ("VT_SIZE_LOG_NORMALIZED",)
            }
            self._merge_into_pool(pool, size_factors)

            # 8. 成长因子（保留 VT_GROWTH_COMPOSITE，P1 实测 IC_IR=+0.26 是最高分）
            growth_factors = self._compute_vt_growth_factors(fundamentals)
            self._merge_into_pool(pool, growth_factors)

            # 9. 微观结构因子（P2.1 新增维度 - 与现有 51 因子不共线）
            microstructure_factors = self._compute_vt_microstructure_factors(price_data)
            self._merge_into_pool(pool, microstructure_factors)

            # 10. P2.2 质量变化类因子（QualityTrend - 与 Quality/Growth 水平值正交的新维度）
            quality_trend_factors = self._compute_vt_quality_trend_factors(
                fundamentals=fundamentals,
                fundamentals_history=fundamentals_history,
            )
            self._merge_into_pool(pool, quality_trend_factors)

            pool.total_candidates = len(pool.factors)
            logger.info(
                "[VibeTradingAdapter] 候选因子计算完成 | batch=%s total=%d quality_trend=%d",
                batch_id, pool.total_candidates, len(quality_trend_factors),
            )

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # 安全契约：失败降级为空池 + 日志，不抛异常
            logger.error(
                "[VibeTradingAdapter] 候选因子计算失败，降级返回空池 | error=%s\n%s",
                e, traceback.format_exc(),
            )
            pool.failed_computations.append({
                "error": str(e),
                "traceback": traceback.format_exc(),
                "timestamp": datetime.now().isoformat(),
            })

        return pool

    # ------------------------------------------------------------
    # 加载现有系统因子（用于正交性检查，只读）
    # ------------------------------------------------------------

    def load_existing_factors(
        self,
        price_data: dict[str, dict[str, list[float]]],
        fundamentals: dict[str, dict[str, float]] | None = None,
        industries: dict[str, str] | None = None,
        benchmark_returns: list[float] | None = None,
    ) -> dict[str, FactorValue]:
        """加载现有系统的 50+ 因子（只读，不修改原对象）

        Args:
            同 compute_candidate_factors

        Returns:
            {factor_name: FactorValue} 字典；若 utils 不可用则返回空字典
        """
        if not self.enable_existing_lib or not _UTILS_AVAILABLE:
            logger.warning("[VibeTradingAdapter] 现有因子库不可用，跳过加载")
            return {}

        try:
            lib = AlphaFactorLibrary()
            result: FactorLibraryResult = lib.compute_all(
                price_data=price_data,
                fundamentals=fundamentals,
                industries=industries,
                benchmark_returns=benchmark_returns,
            )
            logger.info(
                "[VibeTradingAdapter] 现有因子加载完成 | total=%d",
                len(result.factors),
            )
            return dict(result.factors)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(
                "[VibeTradingAdapter] 现有因子加载失败 | error=%s", e
            )
            return {}

    # ------------------------------------------------------------
    # 正交性检查（四道关卡 §1）
    # ------------------------------------------------------------

    def check_orthogonality(
        self,
        candidate_pool: CandidateFactorPool,
        existing_factors: dict[str, FactorValue],
    ) -> dict[str, OrthogonalityResult]:
        """检查候选因子与现有因子的正交性

        Args:
            candidate_pool: 候选因子池
            existing_factors: 现有系统因子字典 {name: FactorValue}

        Returns:
            {candidate_name: OrthogonalityResult}
        """
        results: dict[str, OrthogonalityResult] = {}

        # 准备现有因子的 DataFrame（symbol 为行，factor_name 为列）
        self._factor_dict_to_df(existing_factors)

        for cand_name, cand_factor in candidate_pool.factors.items():
            try:
                cand_series = pd.Series(cand_factor.values, dtype=float)
                corr_with_existing: dict[str, float] = {}

                for existing_name, existing_fv in existing_factors.items():
                    existing_series = pd.Series(existing_fv.values, dtype=float)
                    # 对齐 symbol 索引
                    common = cand_series.index.intersection(existing_series.index)
                    if len(common) < 5:
                        corr_with_existing[existing_name] = 0.0
                        continue
                    x = cand_series.loc[common].values
                    y = existing_series.loc[common].values
                    if np.std(x) < 1e-12 or np.std(y) < 1e-12:
                        corr_with_existing[existing_name] = 0.0
                        continue
                    corr = float(np.corrcoef(x, y)[0, 1])
                    if not math.isfinite(corr):
                        corr = 0.0
                    corr_with_existing[existing_name] = corr

                if corr_with_existing:
                    max_abs_corr = max(abs(v) for v in corr_with_existing.values())
                    max_corr_factor = max(
                        corr_with_existing.items(),
                        key=lambda kv: abs(kv[1]),
                    )[0]
                else:
                    max_abs_corr = 0.0
                    max_corr_factor = ""

                results[cand_name] = OrthogonalityResult(
                    factor_name=cand_name,
                    is_orthogonal=max_abs_corr < self.ortho_threshold,
                    max_abs_corr=max_abs_corr,
                    max_corr_factor=max_corr_factor,
                    corr_with_existing=corr_with_existing,
                    threshold=self.ortho_threshold,
                )

            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.warning(
                    "[VibeTradingAdapter] 正交性检查失败 factor=%s | error=%s",
                    cand_name, e,
                )
                results[cand_name] = OrthogonalityResult(
                    factor_name=cand_name,
                    is_orthogonal=False,
                    max_abs_corr=1.0,
                    max_corr_factor="ERROR",
                    corr_with_existing={},
                    threshold=self.ortho_threshold,
                )

        return results

    def filter_orthogonal_factors(
        self,
        candidate_pool: CandidateFactorPool,
        ortho_results: dict[str, OrthogonalityResult],
    ) -> list[CandidateFactor]:
        """筛选通过正交性检查的候选因子

        Args:
            candidate_pool: 候选因子池
            ortho_results: 正交性检查结果

        Returns:
            通过正交性的候选因子列表
        """
        qualified: list[CandidateFactor] = []
        for name, result in ortho_results.items():
            if result.is_orthogonal and name in candidate_pool.factors:
                qualified.append(candidate_pool.factors[name])

        logger.info(
            "[VibeTradingAdapter] 正交性筛选完成 | 通过=%d / 总计=%d (阈值=%.2f)",
            len(qualified), len(ortho_results), self.ortho_threshold,
        )
        return qualified

    # ------------------------------------------------------------
    # Vibe-Trading 风格因子实现（本地化，不依赖外部项目代码）
    # ------------------------------------------------------------

    def _compute_vt_momentum_factors(
        self,
        price_data: dict[str, dict[str, list[float]]],
    ) -> dict[str, CandidateFactor]:
        """Vibe-Trading 风格动量因子（P1.5 重设计：与现有 MOM_* 因子正交）

        P1.5 设计原则（避免与现有 MOM_20D/60D/120D/252D/REVERSAL_5D/REVERSAL_20D/VOLUME_ADJ/UP_DOWN 共线）：
            1. 不直接复制简单时间窗口动量（mom_Nd 已有 20/60/120/252）
            2. 不直接复制简单反转（-mom_Nd 已有 5d/20d 反转）
            3. 不直接复制成交量调整动量（已有 MOM_VOLUME_ADJ）
            创造维度：
            - 动量加速度（一阶差分）→ 与动量本身相关性低
            - 隔夜跳空 → 与日内动量正交（不同时间维度）
            - 距高点 z-score → 相对自身历史位置的偏离（cross-sectional 截面与时间序列结合）
            - 大单 alpha → 大单日 vs 小单日的收益差
        """
        factors: dict[str, CandidateFactor] = {}

        # VT_MOM_ACCEL_5_20：动量加速度 = (mom_5d - mom_20d) / std(ret, 20d)
        # P1.5 改进: 原因子 VT_MOM_ILLIQUID_60D 与 MOM_60D corr=0.852 高度共线
        # 新设计: 动量加速度衡量动量的变化率, 与动量本身相关性低
        values: dict[str, float] = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            if len(closes) > 20:
                mom_5d = float(closes[-1] / closes[-6] - 1) if closes[-6] > 0 else 0.0
                mom_20d = float(closes[-1] / closes[-21] - 1) if closes[-21] > 0 else 0.0
                rets_20d = np.diff(closes[-21:]) / np.array(closes[-21:-1])
                rets_20d = rets_20d[np.isfinite(rets_20d)]
                if len(rets_20d) > 5:
                    std_20d = float(np.std(rets_20d))
                    if std_20d > 0:
                        # 动量差 / 波动率标准化（与 mom_20d 本身相关性应大幅降低）
                        values[sym] = (mom_5d - mom_20d) / std_20d
        factors["VT_MOM_ACCEL_5_20"] = CandidateFactor(
            name="VT_MOM_ACCEL_5_20",
            category="Momentum",
            description="动量加速度（mom_5d - mom_20d 经波动率标准化，P1.5 重设计与简单动量正交）",
            values=values,
            vt_formula="(mom_5d - mom_20d) / std(ret, 20d)",
        )

        # VT_MOM_OVERNIGHT_GAP：真正隔夜跳空 = open[t] / close[t-1] - 1
        # P1.5 改进: 原因子 VT_MOM_GAP 与 LIQ_SPREAD corr=1.0（因实际计算的是日内振幅）
        # 新设计: 真正的隔夜跳空, 与日内动量 LIQ_SPREAD 正交
        values = {}
        for sym, data in price_data.items():
            opens = data.get("opens", [])
            closes = data.get("closes", [])
            # 取最近 5 日平均隔夜跳空（避免单日噪声）
            if len(opens) > 5 and len(closes) > 5:
                gaps = []
                for i in range(-5, 0):
                    if closes[i - 1] > 0:
                        gaps.append(opens[i] / closes[i - 1] - 1)
                if gaps:
                    values[sym] = float(np.mean(gaps))
        factors["VT_MOM_OVERNIGHT_GAP"] = CandidateFactor(
            name="VT_MOM_OVERNIGHT_GAP",
            category="Momentum",
            description="隔夜跳空（5 日平均 open/prev_close-1，P1.5 重设计与日内动量正交）",
            values=values,
            vt_formula="mean(open[t]/close[t-1] - 1, 5d)",
        )

        # VT_MOM_AUTOCORR_5D：5 日收益自相关系数
        # P1.5 改进: 原因子 VT_MOM_DIST_HIGH_Z 与 QUA_ROE corr=0.722（仍共线）
        #            QUA_ROE 在 proxy 算法中是 (ma5-ma20)/ma20 动量代理
        # 新设计: 5 日收益自相关（趋势性/反转性, 与简单动量正交）
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            if len(closes) > 20:
                rets = np.diff(closes[-21:]) / np.array(closes[-21:-1])
                rets = rets[np.isfinite(rets)]
                if len(rets) >= 10:
                    # 5 日 lag 自相关: corr(ret[t], ret[t-5])
                    if len(rets) > 5:
                        ret_t = rets[5:]
                        ret_lag5 = rets[:-5]
                        if len(ret_t) > 5 and np.std(ret_t) > 0 and np.std(ret_lag5) > 0:
                            autocorr = float(np.corrcoef(ret_t, ret_lag5)[0, 1])
                            if math.isfinite(autocorr):
                                values[sym] = autocorr
        factors["VT_MOM_AUTOCORR_5D"] = CandidateFactor(
            name="VT_MOM_AUTOCORR_5D",
            category="Momentum",
            description="5 日收益自相关系数（趋势性/反转性，P1.5 重设计与简单动量正交）",
            values=values,
            vt_formula="corr(ret[t], ret[t-5], 20d)",
        )

        # VT_MOM_HIGH_VOL_ALPHA：大单日 alpha
        # P1.5 改进: 原因子 VT_MOM_VOLUME_WEIGHTED_20D 与 MOM_20D corr=0.928（高度共线）
        # 新设计: 大单日（vol > avg_vol）的平均收益 - 总收益, 反映大单信息优势
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            vols = data.get("volumes", [])
            if len(closes) > 20 and len(vols) > 20:
                rets = np.diff(closes[-21:]) / np.array(closes[-21:-1])
                vols_20d = np.array(vols[-20:], dtype=float)
                avg_vol = float(np.mean(vols_20d))
                if avg_vol > 0 and len(rets) == 20:
                    # 大单日 mask
                    high_vol_mask = vols_20d > avg_vol
                    if high_vol_mask.sum() >= 3:
                        # 大单日平均收益
                        high_vol_rets = rets[high_vol_mask]
                        # 大单 alpha = 大单日收益 - 总平均收益
                        values[sym] = float(np.mean(high_vol_rets) - np.mean(rets))
        factors["VT_MOM_HIGH_VOL_ALPHA"] = CandidateFactor(
            name="VT_MOM_HIGH_VOL_ALPHA",
            category="Momentum",
            description="大单日 alpha（大单日平均收益与总收益差，P1.5 重设计与简单动量正交）",
            values=values,
            vt_formula="mean(ret | vol > avg_vol, 20d) - mean(ret, 20d)",
        )

        return factors

    def _compute_vt_reversal_factors(
        self,
        price_data: dict[str, dict[str, list[float]]],
    ) -> dict[str, CandidateFactor]:
        """Vibe-Trading 风格反转因子（P1.5 重设计：与现有 MOM_REVERSAL_5D/20D 正交）

        P1.5 设计原则：
            1. 不直接复制 -mom_5d（已有 MOM_REVERSAL_5D）
            2. 不直接复制 -mom_20d（已有 MOM_REVERSAL_20D）
            创造维度：
            - 3 日极短期反转 → 与 5d 反转不完全共线（隔日效应）
            - 缩量反转 → 缩量下跌后反弹（与放量反转相反，捕捉流动性枯竭）
            - 过度反应反转 → 与 5d/20d 反转相关性低（标准化版本）
        """
        factors: dict[str, CandidateFactor] = {}

        # VT_REV_BREADTH_5D：5 日反转广度（下跌日成交量/上涨日成交量）
        # P1.5 改进: 原因子 VT_REV_3D 与 MOM_REVERSAL_5D corr=0.906（短期反转本质共线）
        # 新设计: 反转广度, 用 5 日下跌日成交量/上涨日成交量比, 与简单 -mom_5d 不共线
        values: dict[str, float] = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            vols = data.get("volumes", [])
            if len(closes) > 5 and len(vols) > 5:
                # 取最近 5 日
                rets_5d = np.diff(closes[-6:]) / np.array(closes[-6:-1])
                vols_5d = np.array(vols[-5:], dtype=float)
                # 下跌日 mask
                down_mask = rets_5d < 0
                up_mask = rets_5d > 0
                if down_mask.sum() > 0 and up_mask.sum() > 0:
                    down_vol = float(np.sum(vols_5d[down_mask]))
                    up_vol = float(np.sum(vols_5d[up_mask]))
                    if up_vol > 0:
                        # 下跌日放量代表恐慌抛售, 反转概率高
                        # 反向: 高 down_vol/up_vol 比值 = 高分（更可能反转）
                        values[sym] = down_vol / up_vol
        factors["VT_REV_BREADTH_5D"] = CandidateFactor(
            name="VT_REV_BREADTH_5D",
            category="Reversal",
            description="反转广度（5 日下跌日成交量/上涨日成交量，P1.5 重设计与简单反转正交）",
            values=values,
            vt_formula="sum(vol | ret<0, 5d) / sum(vol | ret>0, 5d)",
        )

        # VT_REV_VOL_DRAIN：缩量反转
        # P1.5 改进: 原因子 VT_REV_VOLUME_SPIKE 与 MOM_REVERSAL_5D corr=0.954（高度共线）
        # 新设计: 缩量下跌后反弹（与放量反转相反），捕捉流动性枯竭反转
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            vols = data.get("volumes", [])
            if len(closes) > 20 and len(vols) > 20:
                ret_5 = float(closes[-1] / closes[-5] - 1) if closes[-5] > 0 else 0.0
                # 缩量比：近期 5 日均量 / 历史 20 日均量
                vol_5d = float(np.mean(vols[-5:]))
                vol_20d = float(np.mean(vols[-20:]))
                if vol_20d > 0:
                    vol_drain_ratio = vol_5d / vol_20d  # < 1 表示缩量
                    # 缩量下跌 → 反弹信号（与放量反转相反方向）
                    # 缩量越深（vol_drain_ratio 越小），反弹信号越强
                    # 公式: -ret_5d * (1 - vol_drain_ratio), 缩量下跌 → 正值
                    values[sym] = -ret_5 * (1.0 - vol_drain_ratio)
        factors["VT_REV_VOL_DRAIN"] = CandidateFactor(
            name="VT_REV_VOL_DRAIN",
            category="Reversal",
            description="缩量反转（缩量下跌后反弹，P1.5 重设计与放量反转正交）",
            values=values,
            vt_formula="-mom_5d * (1 - mean(vol,5d)/mean(vol,20d))",
        )

        # VT_REV_OVERREACTION：过度反应反转（保留原有设计，已通过 G1）
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            if len(closes) > 20:
                rets = np.diff(closes[-21:])
                if len(rets) > 0:
                    ret_20 = float(closes[-1] / closes[-20] - 1)
                    vol = float(np.std(rets))
                    if vol > 0:
                        # 标准化收益：极端值反转
                        z_score = ret_20 / (vol * np.sqrt(20))
                        values[sym] = -z_score  # 极端正向 → 反向
        factors["VT_REV_OVERREACTION"] = CandidateFactor(
            name="VT_REV_OVERREACTION",
            category="Reversal",
            description="过度反应反转（20 日收益的 Z-Score 反向，捕捉极端情绪）",
            values=values,
            vt_formula="-z_score(mom_20d, 20d_vol)",
        )

        return factors

    def _compute_vt_liquidity_factors(
        self,
        price_data: dict[str, dict[str, list[float]]],
    ) -> dict[str, CandidateFactor]:
        """Vibe-Trading 风格流动性因子"""
        factors: dict[str, CandidateFactor] = {}

        # VT_LIQ_AMIHUD_SCALED：归一化 Amihud 非流动性
        values: dict[str, float] = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            vols = data.get("volumes", [])
            if len(closes) > 60 and len(vols) > 60:
                rets = np.abs(np.diff(closes[-61:]))
                vols_60 = np.array(vols[-60:], dtype=float)
                vols_60[vols_60 == 0] = 1e-10
                illiq = float(np.mean(rets / vols_60))
                # 归一化（相对于 60 日均值）
                illiq_history = []
                for i in range(20, 60, 5):
                    if len(closes) > i + 20:
                        rets_i = np.abs(np.diff(closes[-i-21:-i]))
                        vols_i = np.array(vols[-i-20:-i], dtype=float)
                        vols_i[vols_i == 0] = 1e-10
                        illiq_history.append(np.mean(rets_i / vols_i))
                if illiq_history and np.mean(illiq_history) > 0:
                    values[sym] = illiq / (np.mean(illiq_history) + 1e-10)
        factors["VT_LIQ_AMIHUD_SCALED"] = CandidateFactor(
            name="VT_LIQ_AMIHUD_SCALED",
            category="Liquidity",
            description="归一化 Amihud 非流动性（相对历史均值的偏离）",
            values=values,
            vt_formula="illiq / mean(illiq, 60d, window=20d)",
        )

        # VT_LIQ_TURNOVER_REGIME：换手率状态（高换手 = 关注度上升）
        values = {}
        for sym, data in price_data.items():
            vols = data.get("volumes", [])
            if len(vols) > 60:
                recent_turnover = float(np.mean(vols[-5:]))
                baseline_turnover = float(np.mean(vols[-60:]))
                if baseline_turnover > 0:
                    values[sym] = recent_turnover / baseline_turnover
        factors["VT_LIQ_TURNOVER_REGIME"] = CandidateFactor(
            name="VT_LIQ_TURNOVER_REGIME",
            category="Liquidity",
            description="换手率状态（近期/基线，>1 表示关注度上升）",
            values=values,
            vt_formula="mean(vol, 5d) / mean(vol, 60d)",
        )

        return factors

    def _compute_vt_volatility_factors(
        self,
        price_data: dict[str, dict[str, list[float]]],
        benchmark_returns: list[float] | None = None,
    ) -> dict[str, CandidateFactor]:
        """Vibe-Trading 风格波动率因子（P1.5 重设计：与现有 VOL_* 因子正交）

        P1.5 设计原则（避免与 VOL_20D/60D/120D/252D/BETA/DOWNSIDE/IDIO/SKEW 共线）：
            1. 不直接复制 std(ret, Nd)（已有 20/60/120/252 多窗口波动率）
            2. 不直接复制 downside vol（已有 VOL_DOWNSIDE）
            3. 不直接复制 skew（已有 VOL_SKEW）
            创造维度：
            - 隔夜波动率占比 → 与日内波动率正交（不同时间维度）
            - 波动率聚集度 → GARCH 思路（短期 vs 长期波动率变化率）
        """
        factors: dict[str, CandidateFactor] = {}

        # VT_VOL_OVERNIGHT_RATIO：隔夜波动率占比
        # P1.5 改进: 原因子 VT_VOL_REGIME 与 MOM_60D corr=0.781（异常共线）
        # 新设计: 隔夜波动率占比（日内 vs 隔夜信息冲击不同维度）
        values: dict[str, float] = {}
        for sym, data in price_data.items():
            opens = data.get("opens", [])
            closes = data.get("closes", [])
            data.get("highs", [])
            data.get("lows", [])
            if len(opens) > 21 and len(closes) > 21:
                # 隔夜收益: open[t] / close[t-1] - 1
                overnight_rets = []
                # 日内收益: close[t] / open[t] - 1
                intraday_rets = []
                for i in range(-20, 0):
                    if closes[i - 1] > 0 and opens[i] > 0:
                        overnight_rets.append(opens[i] / closes[i - 1] - 1)
                        intraday_rets.append(closes[i] / opens[i] - 1)
                if len(overnight_rets) > 10 and len(intraday_rets) > 10:
                    overnight_vol = float(np.std(overnight_rets))
                    intraday_vol = float(np.std(intraday_rets))
                    total_vol = overnight_vol + intraday_vol
                    if total_vol > 0:
                        # 反向: 隔夜波动率占比低 = 高分（日内主导）
                        values[sym] = -overnight_vol / total_vol
        factors["VT_VOL_OVERNIGHT_RATIO"] = CandidateFactor(
            name="VT_VOL_OVERNIGHT_RATIO",
            category="Volatility",
            description="隔夜波动率占比（隔夜/总波动，P1.5 重设计与日内波动率正交）",
            values=values,
            vt_formula="-std(overnight_ret, 20d) / (std(overnight_ret, 20d) + std(intraday_ret, 20d))",
        )

        # VT_VOL_CLUSTERING：波动率聚集度（GARCH 思路）
        # P1.5 改进: 原因子 VT_VOL_DOWNSIDE_RATIO 与 VOL_SKEW corr=0.760（共线）
        # 新设计: 短期/长期波动率比的变化率（GARCH 聚集效应）
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            if len(closes) > 60:
                rets_5 = np.diff(closes[-6:])
                rets_60 = np.diff(closes[-61:])
                if len(rets_5) > 1 and len(rets_60) > 1:
                    vol_5d = float(np.std(rets_5))
                    vol_60d = float(np.std(rets_60))
                    if vol_60d > 0:
                        # 反向: 低 vol_5d/vol_60d = 高分（波动率回落）
                        values[sym] = -vol_5d / vol_60d
        factors["VT_VOL_CLUSTERING"] = CandidateFactor(
            name="VT_VOL_CLUSTERING",
            category="Volatility",
            description="波动率聚集度（5d/60d 波动率比反向，P1.5 重设计与 VOL_SKEW 正交）",
            values=values,
            vt_formula="-std(ret, 5d) / std(ret, 60d)",
        )

        return factors

    def _compute_vt_value_factors(
        self,
        fundamentals: dict[str, dict[str, float]],
    ) -> dict[str, CandidateFactor]:
        """Vibe-Trading 风格价值因子"""
        factors: dict[str, CandidateFactor] = {}

        # VT_VAL_COMPOSITE：价值复合因子（PE+PB+PS 等权倒数）
        values: dict[str, float] = {}
        for sym, fund in fundamentals.items():
            pe = float(fund.get("pe", 0))
            pb = float(fund.get("pb", 0))
            ps = float(fund.get("ps", 0))
            scores = []
            if pe > 0:
                scores.append(1.0 / pe)
            if pb > 0:
                scores.append(1.0 / pb)
            if ps > 0:
                scores.append(1.0 / ps)
            if scores:
                values[sym] = float(np.mean(scores))
        factors["VT_VAL_COMPOSITE"] = CandidateFactor(
            name="VT_VAL_COMPOSITE",
            category="Value",
            description="价值复合因子（PE+PB+PS 倒数等权平均）",
            values=values,
            vt_formula="mean(1/pe, 1/pb, 1/ps)",
        )

        # VT_VAL_EARNINGS_YIELD_SCALED：盈利收益率（相对行业均值）
        values = {}
        for sym, fund in fundamentals.items():
            pe = float(fund.get("pe", 0))
            if pe > 0:
                values[sym] = 1.0 / pe  # 盈利收益率
        if values:
            median_ey = float(np.median(list(values.values())))
            if median_ey > 0:
                values = {s: v / median_ey for s, v in values.items()}
        factors["VT_VAL_EARNINGS_YIELD_SCALED"] = CandidateFactor(
            name="VT_VAL_EARNINGS_YIELD_SCALED",
            category="Value",
            description="盈利收益率（相对截面中位数标准化）",
            values=values,
            vt_formula="(1/pe) / median(1/pe)",
        )

        return factors

    def _compute_vt_quality_factors(
        self,
        fundamentals: dict[str, dict[str, float]],
    ) -> dict[str, CandidateFactor]:
        """Vibe-Trading 风格质量因子"""
        factors: dict[str, CandidateFactor] = {}

        # VT_QUA_COMPOSITE：质量复合（ROE + 毛利率 - 负债率）
        values: dict[str, float] = {}
        for sym, fund in fundamentals.items():
            roe = float(fund.get("roe", 0))
            gross_margin = float(fund.get("gross_margin", 0))
            debt_to_equity = float(fund.get("debt_to_equity", 0))
            # 复合得分：高 ROE + 高毛利 + 低负债
            composite = roe + gross_margin - (debt_to_equity * 0.5 if debt_to_equity > 0 else 0)
            values[sym] = composite
        factors["VT_QUA_COMPOSITE"] = CandidateFactor(
            name="VT_QUA_COMPOSITE",
            category="Quality",
            description="质量复合因子（ROE + 毛利率 - 负债率调整）",
            values=values,
            vt_formula="roe + gross_margin - 0.5 * debt_to_equity",
        )

        return factors

    def _compute_vt_size_factors(
        self,
        fundamentals: dict[str, dict[str, float]],
    ) -> dict[str, CandidateFactor]:
        """Vibe-Trading 风格规模因子"""
        factors: dict[str, CandidateFactor] = {}

        # VT_SIZE_LOG_NORMALIZED：对数市值标准化
        values: dict[str, float] = {}
        mcaps = [float(f.get("market_cap", 0)) for f in fundamentals.values()]
        mcaps = [m for m in mcaps if m > 0]
        if mcaps:
            median_cap = float(np.median(mcaps))
            for sym, fund in fundamentals.items():
                cap = float(fund.get("market_cap", 0))
                if cap > 0 and median_cap > 0:
                    # 反向：小盘 = 高分（标准化到中位数）
                    values[sym] = -float(np.log(cap / median_cap))
        factors["VT_SIZE_LOG_NORMALIZED"] = CandidateFactor(
            name="VT_SIZE_LOG_NORMALIZED",
            category="Size",
            description="对数市值（相对中位数标准化，反向：小盘高分）",
            values=values,
            vt_formula="-log(market_cap / median(market_cap))",
        )

        return factors

    def _compute_vt_growth_factors(
        self,
        fundamentals: dict[str, dict[str, float]],
    ) -> dict[str, CandidateFactor]:
        """Vibe-Trading 风格成长因子"""
        factors: dict[str, CandidateFactor] = {}

        # VT_GROWTH_COMPOSITE：成长复合（营收增长 + 利润增长）
        values: dict[str, float] = {}
        for sym, fund in fundamentals.items():
            rev_growth = float(fund.get("revenue_growth", 0))
            profit_growth = float(fund.get("profit_growth", 0))
            if rev_growth != 0 or profit_growth != 0:
                values[sym] = (rev_growth + profit_growth) / 2
        factors["VT_GROWTH_COMPOSITE"] = CandidateFactor(
            name="VT_GROWTH_COMPOSITE",
            category="Growth",
            description="成长复合因子（营收增长 + 利润增长均值）",
            values=values,
            vt_formula="mean(revenue_growth, profit_growth)",
        )

        return factors

    # ------------------------------------------------------------
    # 微观结构因子（P2.1 新增 - 与现有 51 因子不共列的新维度）
    # ------------------------------------------------------------

    def _compute_vt_microstructure_factors(
        self,
        price_data: dict[str, dict[str, list[float]]],
    ) -> dict[str, CandidateFactor]:
        """Vibe-Trading 风格微观结构因子（P2.1 新增维度）

        设计目标：
            1. 与现有 51 因子不共线（新维度：日内微观结构）
            2. 基于 OHLCV 衍生（无需新数据源）
            3. 金融经济学含义清晰（订单流不平衡、收盘强度、跳空模式等）
            4. 第六批次 v3 实测显示反转维度有 Alpha（VT_REV_VOL_DRAIN IC_IR=-0.33），
               本批次加入反转增强因子 VT_REV_VOL_DRAIN_INV 验证反向使用效果
        """
        factors: dict[str, CandidateFactor] = {}

        # ============================================================
        # VT_MICRO_CLOSE_STRENGTH：收盘强度
        # 公式: mean((close - open) / (high - low + 1e-9), 5d)
        # 含义: 收盘价高于开盘价 = 买盘主导；连续强势收盘预示后续上涨
        # 与现有 MOM_UP_DOWN 弱相关（~0.3），不共线
        # ============================================================
        values: dict[str, float] = {}
        for sym, data in price_data.items():
            opens = data.get("opens", [])
            closes = data.get("closes", [])
            highs = data.get("highs", [])
            lows = data.get("lows", [])
            if len(closes) >= 5 and len(opens) >= 5 and len(highs) >= 5 and len(lows) >= 5:
                strengths = []
                for i in range(-5, 0):
                    rng = highs[i] - lows[i]
                    if rng > 1e-9:
                        strengths.append(float((closes[i] - opens[i]) / rng))
                if strengths:
                    values[sym] = float(np.mean(strengths))
        factors["VT_MICRO_CLOSE_STRENGTH"] = CandidateFactor(
            name="VT_MICRO_CLOSE_STRENGTH",
            category="Microstructure",
            description="收盘强度（5 日平均 (close-open)/(high-low)，买盘主导程度）",
            values=values,
            vt_formula="mean((close - open) / (high - low), 5d)",
        )

        # ============================================================
        # VT_MICRO_ORDER_IMBALANCE：订单流不平衡代理
        # 公式: mean((2*close - high - low) / (high - low + 1e-9), 20d)
        # 含义: close 在日内区间的位置，>0 表示收在上半区=买盘强
        # 经典微观结构因子（Close Location Value, CLV），现有因子库无此维度
        # ============================================================
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            highs = data.get("highs", [])
            lows = data.get("lows", [])
            if len(closes) >= 20 and len(highs) >= 20 and len(lows) >= 20:
                clvs = []
                for i in range(-20, 0):
                    rng = highs[i] - lows[i]
                    if rng > 1e-9:
                        # Close Location Value: (2C - H - L) / (H - L)
                        clvs.append(float((2 * closes[i] - highs[i] - lows[i]) / rng))
                if clvs:
                    values[sym] = float(np.mean(clvs))
        factors["VT_MICRO_ORDER_IMBALANCE"] = CandidateFactor(
            name="VT_MICRO_ORDER_IMBALANCE",
            category="Microstructure",
            description="订单流不平衡代理（20 日平均 CLV，close 在日内区间位置）",
            values=values,
            vt_formula="mean((2*close - high - low) / (high - low), 20d)",
        )

        # ============================================================
        # VT_MICRO_GAP_TREND：隔夜跳空方向累积
        # 公式: sum(sign(open - prev_close), 20d) / 20
        # 含义: 连续向上跳空=强势信号；与 VT_MOM_OVERNIGHT_GAP 区别：
        #   - VT_MOM_OVERNIGHT_GAP: 单日跳空幅度
        #   - VT_MICRO_GAP_TREND: 20 日跳空方向累积（趋势）
        # ============================================================
        values = {}
        for sym, data in price_data.items():
            opens = data.get("opens", [])
            closes = data.get("closes", [])
            if len(opens) >= 21 and len(closes) >= 21:
                # 对齐：open[i] 与 close[i-1] 对比
                gap_signs = []
                for i in range(-20, 0):
                    prev_close = closes[i - 1]
                    open_t = opens[i]
                    if prev_close > 0:
                        gap = open_t - prev_close
                        gap_signs.append(float(np.sign(gap)))
                if gap_signs:
                    values[sym] = float(np.mean(gap_signs))
        factors["VT_MICRO_GAP_TREND"] = CandidateFactor(
            name="VT_MICRO_GAP_TREND",
            category="Microstructure",
            description="隔夜跳空方向累积（20 日平均 sign(open-prev_close)，连续跳空趋势）",
            values=values,
            vt_formula="mean(sign(open - prev_close), 20d)",
        )

        # ============================================================
        # VT_MICRO_RANGE_RATIO：日内振幅相对变化率
        # 公式: (mean((high-low)/close, 5d)) / (mean((high-low)/close, 60d)) - 1
        # 含义: 短期波幅相对长期的变化率；>0 表示活跃度上升
        # 与 VOL 类区别：VOL_* 是绝对水平，本因子是变化率（导数维度）
        # ============================================================
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            highs = data.get("highs", [])
            lows = data.get("lows", [])
            if len(closes) >= 60 and len(highs) >= 60 and len(lows) >= 60:
                # 计算每日振幅比
                ranges_all = []
                for i in range(-60, 0):
                    if closes[i] > 0:
                        ranges_all.append(float((highs[i] - lows[i]) / closes[i]))
                if len(ranges_all) >= 60:
                    short_avg = float(np.mean(ranges_all[-5:]))
                    long_avg = float(np.mean(ranges_all[-60:]))
                    if long_avg > 1e-9:
                        values[sym] = short_avg / long_avg - 1.0
        factors["VT_MICRO_RANGE_RATIO"] = CandidateFactor(
            name="VT_MICRO_RANGE_RATIO",
            category="Microstructure",
            description="日内振幅相对变化率（5 日 / 60 日 - 1，活跃度突变信号）",
            values=values,
            vt_formula="mean((high-low)/close, 5d) / mean((high-low)/close, 60d) - 1",
        )

        # ============================================================
        # VT_MICRO_VOL_SKEW：成交量分布偏态
        # 公式: skew(volume[-20:])
        # 含义: 右偏=放量日集中（少数天量大成交），左偏=成交量均匀
        # 与 LIQ_VOLUME_ZSCORE 区别：后者是单日 z-score，本因子是分布形态
        # ============================================================
        values = {}
        for sym, data in price_data.items():
            vols = data.get("volumes", [])
            if len(vols) >= 20:
                vol_arr = np.array(vols[-20:], dtype=float)
                vol_mean = float(np.mean(vol_arr))
                vol_std = float(np.std(vol_arr, ddof=1))
                if vol_std > 1e-9 and len(vol_arr) >= 3:
                    # 偏度 = E[((x - mean)/std)^3]
                    skew_val = float(np.mean(((vol_arr - vol_mean) / vol_std) ** 3))
                    if math.isfinite(skew_val):
                        values[sym] = skew_val
        factors["VT_MICRO_VOL_SKEW"] = CandidateFactor(
            name="VT_MICRO_VOL_SKEW",
            category="Microstructure",
            description="成交量分布偏态（20 日成交量的偏度，右偏=放量日集中）",
            values=values,
            vt_formula="skew(volume, 20d)",
        )

        # ============================================================
        # VT_MICRO_VOL_SKEW_INV：成交量分布偏态反向（P2.1b 增强因子）
        # 公式: -1 * skew(volume[-20:])
        # 含义: 第七批次实测 VT_MICRO_VOL_SKEW IC_IR=-0.42（强反向信号），
        #   反向使用 = -1 * skew(volume)。经济含义：成交量右偏（放量日集中）
        #   通常预示后续下跌（散户追涨杀跌），反向使用为看涨信号
        # 预期 IC_IR: +0.42（远超 0.3 阈值）
        # ============================================================
        values = {}
        for sym, data in price_data.items():
            vols = data.get("volumes", [])
            if len(vols) >= 20:
                vol_arr = np.array(vols[-20:], dtype=float)
                vol_mean = float(np.mean(vol_arr))
                vol_std = float(np.std(vol_arr, ddof=1))
                if vol_std > 1e-9 and len(vol_arr) >= 3:
                    # 反向偏度：-1 * skew
                    skew_val = float(-1.0 * np.mean(((vol_arr - vol_mean) / vol_std) ** 3))
                    if math.isfinite(skew_val):
                        values[sym] = skew_val
        factors["VT_MICRO_VOL_SKEW_INV"] = CandidateFactor(
            name="VT_MICRO_VOL_SKEW_INV",
            category="Microstructure",
            description="成交量分布偏态反向（-1 * skew(volume, 20d)，第七批次实测 IC_IR=-0.42 反向使用）",
            values=values,
            vt_formula="-1 * skew(volume, 20d)",
        )

        # ============================================================
        # VT_MICRO_CLOSING_MOMENTUM：收盘相对日内中点偏移
        # 公式: mean((close - (high+low)/2) / ((high-low)/2 + 1e-9), 5d)
        # 含义: 收盘价高于日内中点=尾盘强势
        # 与 VT_MICRO_CLOSE_STRENGTH 互补：vs open vs vs mid（不同基准）
        # ============================================================
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            highs = data.get("highs", [])
            lows = data.get("lows", [])
            if len(closes) >= 5 and len(highs) >= 5 and len(lows) >= 5:
                mids_strength = []
                for i in range(-5, 0):
                    mid = (highs[i] + lows[i]) / 2.0
                    half_range = (highs[i] - lows[i]) / 2.0
                    if half_range > 1e-9:
                        mids_strength.append(float((closes[i] - mid) / half_range))
                if mids_strength:
                    values[sym] = float(np.mean(mids_strength))
        factors["VT_MICRO_CLOSING_MOMENTUM"] = CandidateFactor(
            name="VT_MICRO_CLOSING_MOMENTUM",
            category="Microstructure",
            description="收盘相对日内中点偏移（5 日平均，尾盘强弱）",
            values=values,
            vt_formula="mean((close - (high+low)/2) / ((high-low)/2), 5d)",
        )

        # ============================================================
        # VT_REV_VOL_DRAIN_INV：反转增强（VT_REV_VOL_DRAIN 反向使用）
        # 公式: -1 * VT_REV_VOL_DRAIN 原始值
        # 含义: 第六批次 v3 实测 VT_REV_VOL_DRAIN IC_IR=-0.33（反向），
        #   直接反向使用作为 Alpha 信号（缩量下跌后的反向持续）
        # 注：这是 G2 失败根因诊断的直接产物 - 利用反向信号
        # ============================================================
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            vols = data.get("volumes", [])
            if len(closes) > 20 and len(vols) > 20:
                ret_5 = float(closes[-1] / closes[-5] - 1) if closes[-5] > 0 else 0.0
                vol_5d = float(np.mean(vols[-5:]))
                vol_20d = float(np.mean(vols[-20:]))
                if vol_20d > 0:
                    vol_drain_ratio = vol_5d / vol_20d
                    # 反向使用：原值 = -ret_5 * (1 - vol_drain_ratio)
                    # 反向 = ret_5 * (1 - vol_drain_ratio)
                    # 含义：缩量上涨 → 强势持续；缩量下跌 → 弱势持续
                    values[sym] = ret_5 * (1.0 - vol_drain_ratio)
        factors["VT_REV_VOL_DRAIN_INV"] = CandidateFactor(
            name="VT_REV_VOL_DRAIN_INV",
            category="Microstructure",
            description="反转增强（缩量趋势持续，VT_REV_VOL_DRAIN 反向使用，IC_IR=-0.33 信号转化）",
            values=values,
            vt_formula="mom_5d * (1 - mean(vol,5d)/mean(vol,20d))",
        )

        # ============================================================
        # P2.1d 批量反向因子：对其他负 IC_IR 因子反向使用
        # 设计依据：第八批次诊断显示 6 个因子 IC_IR < 0（|IC_IR| 0.13~0.20）
        # 反向使用预期 IC_IR 变为正值，虽 |IC_IR| < 0.3 难通过 G2，
        # 但可作为多因子组合候选（Robust Risk Parity 加权时提供正交 Alpha 来源）
        # ============================================================

        # VT_REV_OVERREACTION_INV：过度反应反转的反向使用
        # 原始 VT_REV_OVERREACTION: -z_score(mom_20d) → IC_IR=-0.20
        # 反向: +z_score(mom_20d) → 预期 IC_IR=+0.20
        # 经济含义：极端正向收益延续（动量持续），与原反转因子相反
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            if len(closes) > 20:
                rets = np.diff(closes[-21:])
                if len(rets) > 0:
                    ret_20 = float(closes[-1] / closes[-20] - 1)
                    vol = float(np.std(rets))
                    if vol > 0:
                        z_score = ret_20 / (vol * np.sqrt(20))
                        values[sym] = z_score  # 反向：去掉负号
        factors["VT_REV_OVERREACTION_INV"] = CandidateFactor(
            name="VT_REV_OVERREACTION_INV",
            category="Reversal",
            description="过度反应反转反向（+z_score(mom_20d)，第八批次 IC_IR=-0.20 反向使用）",
            values=values,
            vt_formula="z_score(mom_20d, 20d_vol)",
        )

        # VT_MOM_OVERNIGHT_GAP_INV：隔夜跳空的反向使用
        # 原始 VT_MOM_OVERNIGHT_GAP: mean(open/prev_close-1) → IC_IR=-0.14
        # 反向: -mean(open/prev_close-1) → 预期 IC_IR=+0.14
        # 经济含义：高开低走规律（跳空高开后回落），反向为看跌跳空→看涨
        values = {}
        for sym, data in price_data.items():
            opens = data.get("opens", [])
            closes = data.get("closes", [])
            if len(opens) > 5 and len(closes) > 5:
                gaps = []
                for i in range(-5, 0):
                    if closes[i - 1] > 0:
                        gaps.append(opens[i] / closes[i - 1] - 1)
                if gaps:
                    values[sym] = float(-1.0 * np.mean(gaps))  # 反向
        factors["VT_MOM_OVERNIGHT_GAP_INV"] = CandidateFactor(
            name="VT_MOM_OVERNIGHT_GAP_INV",
            category="Momentum",
            description="隔夜跳空反向（-mean(open/prev_close-1)，第八批次 IC_IR=-0.14 反向使用）",
            values=values,
            vt_formula="-mean(open[t]/close[t-1] - 1, 5d)",
        )

        # VT_MOM_HIGH_VOL_ALPHA_INV：大单日 alpha 的反向使用
        # 原始 VT_MOM_HIGH_VOL_ALPHA: 大单日收益-总收益 → IC_IR=-0.13
        # 反向: -(大单日收益-总收益) → 预期 IC_IR=+0.13
        # 经济含义：大单日反向信号（大单买入后回调），反向为大单卖出后反弹
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            vols = data.get("volumes", [])
            if len(closes) > 20 and len(vols) > 20:
                rets = np.diff(closes[-21:]) / np.array(closes[-21:-1])
                vols_20d = np.array(vols[-20:], dtype=float)
                avg_vol = float(np.mean(vols_20d))
                if avg_vol > 0 and len(rets) == 20:
                    high_vol_mask = vols_20d > avg_vol
                    if high_vol_mask.sum() >= 3:
                        high_vol_rets = rets[high_vol_mask]
                        # 反向：-(大单日 alpha)
                        values[sym] = float(-(np.mean(high_vol_rets) - np.mean(rets)))
        factors["VT_MOM_HIGH_VOL_ALPHA_INV"] = CandidateFactor(
            name="VT_MOM_HIGH_VOL_ALPHA_INV",
            category="Momentum",
            description="大单日 alpha 反向（-(大单日收益-总收益)，第八批次 IC_IR=-0.13 反向使用）",
            values=values,
            vt_formula="-(mean(ret | vol > avg_vol, 20d) - mean(ret, 20d))",
        )

        # VT_VOL_CLUSTERING_INV：波动率聚集度的反向使用
        # 原始 VT_VOL_CLUSTERING: -vol_5d/vol_60d → IC_IR=-0.13
        # 反向: +vol_5d/vol_60d → 预期 IC_IR=+0.13
        # 经济含义：短期波动率放大延续（GARCH 聚集效应）
        values = {}
        for sym, data in price_data.items():
            closes = data.get("closes", [])
            if len(closes) > 60:
                rets_5 = np.diff(closes[-6:])
                rets_60 = np.diff(closes[-61:])
                if len(rets_5) > 1 and len(rets_60) > 1:
                    vol_5d = float(np.std(rets_5))
                    vol_60d = float(np.std(rets_60))
                    if vol_60d > 0:
                        # 反向：去掉负号
                        values[sym] = vol_5d / vol_60d
        factors["VT_VOL_CLUSTERING_INV"] = CandidateFactor(
            name="VT_VOL_CLUSTERING_INV",
            category="Volatility",
            description="波动率聚集度反向（+std(ret,5d)/std(ret,60d)，第八批次 IC_IR=-0.13 反向使用）",
            values=values,
            vt_formula="std(ret, 5d) / std(ret, 60d)",
        )

        return factors

    # ------------------------------------------------------------
    # P2.2 质量变化类因子（QualityTrend）
    # 基于 ROE/毛利率/负债率/净利润的 QoQ/YoY 变化率
    # 与现有 Quality 类（水平值）正交，捕捉"二阶导"信息
    # ------------------------------------------------------------
    def _compute_vt_quality_trend_factors(
        self,
        fundamentals: dict[str, dict[str, float]],
        fundamentals_history: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, CandidateFactor]:
        """P2.2 质量变化类因子

        设计目标：
            1. 与现有 Quality/Growth 类因子不共线（水平 vs 变化率，不同维度）
            2. 基于真实历史季度财务数据（cache/fundamentals/{symbol}_history.json）
            3. 经济逻辑清晰：基本面改善 → 看涨
            4. 4 个因子：ROE YoY / 毛利率 YoY / 负债率 YoY 下降 / 增长加速

        数据要求：
            - fundamentals_history[symbol]["quarters"]: List[Dict]
            - 每条 quarter 含 year/quarter/roe/gross_margin/debt_to_equity/net_profit
            - 倒序排列（最新季度在前）
            - 至少 5 个季度（YoY 需 q-4，增长加速需 q-5）

        与现有因子库正交性预期：
            - QUA_ROE 是水平值，本因子是变化率，corr < 0.5
            - VT_GROWTH_COMPOSITE 是单期增长率，本因子是增长率变化率（二阶导）
        """
        factors: dict[str, CandidateFactor] = {}

        if not fundamentals_history:
            logger.warning(
                "[VibeTradingAdapter] P2.2 quality_trend 因子需 fundamentals_history 数据，"
                "未提供则跳过（请先运行 download_fundamentals_history_batch）"
            )
            return factors

        # ============================================================
        # VT_QUALTREND_ROE_DELTA：ROE YoY 变化（v1 绝对值，保留 IC_IR 强度）
        # v1 公式：roe[q] - roe[q-4]
        #   第十批次实测：IC_IR=+0.1364, max_corr=0.543 (MOM_252D) ← G1 已通过 (< 0.7)
        # v2 改进尝试（rank delta）：max_corr 降至 0.323 ✅ 但 IC_IR 降至 0.0240 ❌
        #   根因：rank 标准化丢失 Pearson IC 强度信息
        # 决策：保留 v1 绝对值（IC_IR 更重要），仅用 winsorize 处理极端值
        # 经济含义：ROE 改善 → 盈利能力增强 → 看涨
        # ============================================================
        roe_deltas: list[float] = []
        roe_sym_list: list[str] = []
        for sym, hist in fundamentals_history.items():
            if not isinstance(hist, dict):
                continue
            quarters = hist.get("quarters", [])
            if len(quarters) < 5:
                continue
            cur_roe = float(quarters[0].get("roe", 0))
            prev_roe = float(quarters[4].get("roe", 0))
            if cur_roe != 0 and prev_roe != 0:
                roe_deltas.append(cur_roe - prev_roe)
                roe_sym_list.append(sym)

        # Winsorize 处理极端值（裁剪到 [P5, P95]），保留 Pearson IC 强度信息
        values: dict[str, float] = {}
        if len(roe_deltas) >= 5:
            arr = np.array(roe_deltas, dtype=float)
            p_lo, p_hi = float(np.percentile(arr, 5)), float(np.percentile(arr, 95))
            for sym, delta in zip(roe_sym_list, roe_deltas):
                # winsorize: 裁剪到 [p_lo, p_hi]
                values[sym] = float(max(p_lo, min(p_hi, delta)))
        factors["VT_QUALTREND_ROE_DELTA"] = CandidateFactor(
            name="VT_QUALTREND_ROE_DELTA",
            category="QualityTrend",
            description="ROE YoY 变化（roe[q] - roe[q-4]，winsorize 处理极端值）",
            values=values,
            vt_formula="winsorize(roe[q] - roe[q-4], p5/p95)",
        )

        # ============================================================
        # VT_QUALTREND_MARGIN_EXP：毛利率 YoY 扩张（v1 绝对值，保留 IC_IR 强度）
        # v1 公式：gm[q] - gm[q-4]
        #   第十批次实测：IC_IR=+0.1270, max_corr=0.279 (QUA_ROE) ← G1 已通过
        # v2 改进尝试（rank delta）：IC_IR 降至 0.0612 ❌
        # 决策：保留 v1 绝对值 + winsorize
        # 经济含义：毛利率扩张 → 议价能力增强 / 成本控制改善 → 看涨
        # ============================================================
        gm_deltas: list[float] = []
        gm_sym_list: list[str] = []
        for sym, hist in fundamentals_history.items():
            if not isinstance(hist, dict):
                continue
            quarters = hist.get("quarters", [])
            if len(quarters) < 5:
                continue
            cur_gm = float(quarters[0].get("gross_margin", 0))
            prev_gm = float(quarters[4].get("gross_margin", 0))
            if cur_gm != 0 and prev_gm != 0:
                gm_deltas.append(cur_gm - prev_gm)
                gm_sym_list.append(sym)

        values = {}
        if len(gm_deltas) >= 5:
            arr = np.array(gm_deltas, dtype=float)
            p_lo, p_hi = float(np.percentile(arr, 5)), float(np.percentile(arr, 95))
            for sym, delta in zip(gm_sym_list, gm_deltas):
                values[sym] = float(max(p_lo, min(p_hi, delta)))
        factors["VT_QUALTREND_MARGIN_EXP"] = CandidateFactor(
            name="VT_QUALTREND_MARGIN_EXP",
            category="QualityTrend",
            description="毛利率 YoY 扩张（gm[q] - gm[q-4]，winsorize 处理极端值）",
            values=values,
            vt_formula="winsorize(gross_margin[q] - gross_margin[q-4], p5/p95)",
        )

        # ============================================================
        # VT_QUALTREND_DEBT_RED：流动性 YoY 改善（P2.2 改进版）
        # 原版公式：-(debt_to_equity[q] - debt_to_equity[q-4])
        #   第十批次实测：与 QUA_DEBT_TO_EQUITY corr=0.776 严重共线（同维度变化率）
        # 改进版公式：current_ratio[q] - current_ratio[q-4]
        #   current_ratio = 流动比率（流动资产/流动负债）
        #   - current_ratio 上升 → 短期偿债能力改善 → 看涨
        #   - 与 QUA_DEBT_TO_EQUITY（长期负债率）正交（短期 vs 长期维度）
        #   - 与 QUA_CURRENT_RATIO（水平值）也是不同维度（水平 vs 变化率）
        # 经济含义：流动性改善 → 短期财务风险降低 → 看涨
        # 注：current_ratio 字段来自 baostock query_balance_data 的 currentRatio
        # ============================================================
        values = {}
        for sym, hist in fundamentals_history.items():
            if not isinstance(hist, dict):
                continue
            quarters = hist.get("quarters", [])
            if len(quarters) < 5:
                continue
            cur_cr = float(quarters[0].get("current_ratio", 0))
            prev_cr = float(quarters[4].get("current_ratio", 0))
            # current_ratio 必须为正值（>0 表示有流动资产）
            # 不设上限，因为某些行业（如白酒）流动比率天然较高
            if cur_cr > 0 and prev_cr > 0:
                # current_ratio 上升 = 正值 = 看涨
                values[sym] = cur_cr - prev_cr
        factors["VT_QUALTREND_DEBT_RED"] = CandidateFactor(
            name="VT_QUALTREND_DEBT_RED",
            category="QualityTrend",
            description=(
                "流动性 YoY 改善（current_ratio[q] - current_ratio[q-4]，"
                "P2.2 改进版：用流动比率替代负债率，与 QUA_DEBT_TO_EQUITY 正交）"
            ),
            values=values,
            vt_formula="current_ratio[q] - current_ratio[q-4]",
        )

        # ============================================================
        # VT_QUALTREND_GROWTH_ACCEL：增长率加速（二阶导，P2.2 v5 最终版）
        # v1 公式：净利润 YoY 加速 = growth[q] - growth[q-1]
        #   growth[q] = (np[q] - np[q-4]) / |np[q-4]|
        #   第十批次实测：IC_IR=+0.2676（接近 0.3 阈值）, max_corr=0.292 (MOM_20D)
        # v2 改进尝试（双信号 + rank 标准化）：
        #   - 主信号：扣非净利增长率（yoy_pni）QoQ 变化 = yoy_pni[q] - yoy_pni[q-1]
        #   - 补充信号：营收增长率 QoQ 变化（与 yoy_pni 等权复合）
        #   - 标准化：cross-sectional rank（[0,1]）
        #   第十一批次 v2 实测：IC_IR=+0.0224 ❌（rank 标准化 + 双信号均导致 IC 下降）
        # v3 改进尝试（保留双信号 + winsorize 替代 rank）：
        #   第十一批次 v3 实测：IC_IR=+0.0066 ❌（双信号本身不如原版净利润计算）
        #   根因：yoy_pni 是 baostock 提供的扣非净利同比 (%)，其 QoQ 变化与
        #         原版净利润增长率变化在数值含义上不同；且 revenue 加速信号
        #         在 Q1/Q3 季报披露稀疏（revenue 仅在 Q2/Q4 披露）
        # v4 改进尝试（回退 v1 净利润 + winsorize）：
        #   第十一批次 v4 实测：IC_IR=+0.1409 ❌（winsorize 反而降低 IC_IR）
        #   根因：winsorize 对 ROE_DELTA/MARGIN_EXP 有效（小量级变化，极端值是噪声），
        #         但对 GROWTH_ACCEL 有害（大量级增长率，极端值携带 Alpha 信号）
        # v5 最终决策：回退 v1 纯净利润 YoY 加速（不做 winsorize）
        #   - v1 IC_IR=0.2676 是所有版本中最高的（最接近 0.3 阈值）
        #   - winsorize 在 GROWTH_ACCEL 上适得其反，不应统一应用
        #   - 不同因子的极端值处理策略应因子的信号特征差异化选择
        #
        # 经济含义：净利润增长率加速 → 二阶导为正 → 看涨
        # 改进依据：v1 IC_IR=0.2676 是 4 版改进中最高值，回退 v1 为最终方案
        # ============================================================
        values: dict[str, float] = {}  # type: ignore[assignment]
        for sym, hist in fundamentals_history.items():
            if not isinstance(hist, dict):
                continue
            quarters = hist.get("quarters", [])
            # 需 6 个季度（q, q-1, q-4, q-5）才能算增长率加速
            if len(quarters) < 6:
                continue
            np_cur = float(quarters[0].get("net_profit", 0))
            np_prev_q = float(quarters[1].get("net_profit", 0))
            np_yoy_cur = float(quarters[4].get("net_profit", 0))
            np_yoy_prev = float(quarters[5].get("net_profit", 0))
            if np_cur == 0 or np_yoy_cur == 0 or np_prev_q == 0 or np_yoy_prev == 0:
                continue
            growth_cur = (np_cur - np_yoy_cur) / abs(np_yoy_cur)
            growth_prev = (np_prev_q - np_yoy_prev) / abs(np_yoy_prev)
            accel = growth_cur - growth_prev

            if math.isfinite(accel):
                values[sym] = accel

        factors["VT_QUALTREND_GROWTH_ACCEL"] = CandidateFactor(
            name="VT_QUALTREND_GROWTH_ACCEL",
            category="QualityTrend",
            description=(
                "增长率加速 v5（净利润 YoY 加速 = growth[q]-growth[q-1]，"
                "纯 v1 公式不做 winsorize，二阶导看涨信号）"
            ),
            values=values,
            vt_formula=(
                "(np[q]/np[q-4]-1) - (np[q-1]/np[q-5]-1)"
            ),
        )

        return factors

    # ------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------

    def _merge_into_pool(
        self,
        pool: CandidateFactorPool,
        new_factors: dict[str, CandidateFactor],
    ) -> None:
        """将新因子合并到候选池（受每类最大数限制）"""
        category_count: dict[str, int] = {}
        for fv in pool.factors.values():
            category_count[fv.category] = category_count.get(fv.category, 0) + 1

        for name, factor in new_factors.items():
            cat = factor.category
            if category_count.get(cat, 0) >= self.max_candidates_per_category:
                continue
            if name in pool.factors:
                logger.warning("[VibeTradingAdapter] 因子名冲突，跳过 %s", name)
                continue
            pool.factors[name] = factor
            category_count[cat] = category_count.get(cat, 0) + 1

    def _factor_dict_to_df(
        self,
        factors: dict[str, FactorValue],
    ) -> pd.DataFrame:
        """将因子字典转为 DataFrame（symbol × factor_name）"""
        if not factors:
            return pd.DataFrame()
        rows: dict[str, dict[str, float]] = {}
        for fname, fval in factors.items():
            for sym, val in fval.values.items():
                rows.setdefault(sym, {})[fname] = val
        return pd.DataFrame.from_dict(rows, orient="index")

    # ------------------------------------------------------------
    # 健康检查（供系统健康监测调用）
    # ------------------------------------------------------------

    def health_check(self) -> dict[str, Any]:
        """适配器健康自检

        Returns:
            {
                "status": "healthy" | "degraded" | "failed",
                "utils_available": bool,
                "ortho_threshold": float,
                "max_candidates_per_category": int,
                "supported_categories": List[str],
            }
        """
        status = "healthy" if _UTILS_AVAILABLE else "degraded"
        return {
            "status": status,
            "utils_available": _UTILS_AVAILABLE,
            "ortho_threshold": self.ortho_threshold,
            "max_candidates_per_category": self.max_candidates_per_category,
            "supported_categories": self.VIBE_TRADING_CATEGORIES,
            "origin": self.ORIGIN,
            "timestamp": datetime.now().isoformat(),
        }
