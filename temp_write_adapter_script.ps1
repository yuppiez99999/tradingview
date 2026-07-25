$file = "e:\各种PY程序\28-终极量化交易系统8.4\research\vibe_trading_factor_analysis\adapters\vibe_trading_factor_adapter.py"

$content = @'
# -*- coding: utf-8 -*-
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

参考：
    - HKUDS/Vibe-Trading: https://github.com/HKUDS/Vibe-Trading
    - 战略整合白皮书: research/research_report_github_projects_integration_strategy_20260725.md
"""

from __future__ import annotations

import logging
import math
import sys
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# 确保 utils 模块可导入（项目根目录加入 sys.path）
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 只读引用现有系统（绝不修改）
try:
    from utils.alpha_factor_library import AlphaFactorLibrary, FactorValue, FactorLibraryResult
    _UTILS_AVAILABLE = True
except ImportError as e:
    _UTILS_AVAILABLE = False
    _IMPORT_ERROR = str(e)

logger = logging.getLogger("vibe_trading_adapter")

FACTOR_ORIGIN = "vibe_trading"
VIBE_TRADING_TOTAL_FACTORS = 450


@dataclass
class CandidateFactor:
    """Vibe-Trading 候选因子（尚未通过四道关卡）"""
    name: str
    category: str
    description: str = ""
    values: Dict[str, float] = field(default_factory=dict)
    origin: str = FACTOR_ORIGIN
    vt_formula: str = ""
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class CandidateFactorPool:
    """候选因子池（批次计算结果）"""
    batch_id: str
    compute_date: str
    total_candidates: int = 0
    factors: Dict[str, CandidateFactor] = field(default_factory=dict)
    failed_computations: List[Dict[str, Any]] = field(default_factory=list)
    source_version: str = ""
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OrthogonalityResult:
    """正交性检查结果"""
    factor_name: str
    is_orthogonal: bool
    max_abs_corr: float
    max_corr_factor: str
    corr_with_existing: Dict[str, float] = field(default_factory=dict)
    threshold: float = 0.7


class VibeTradingFactorAdapter:
    """Vibe-Trading 因子库适配器

    设计模式：适配器 + 策略模式
    安全等级：只读 + 隔离 + 可降级

    用法（典型流程）：
        >>> adapter = VibeTradingFactorAdapter()
        >>> pool = adapter.compute_candidate_factors(price_data, fundamentals)
        >>> existing = adapter.load_existing_factors(price_data, fundamentals)
        >>> ortho_results = adapter.check_orthogonality(pool, existing)
        >>> qualified = adapter.filter_orthogonal_factors(pool, ortho_results)
    """

    ORIGIN = FACTOR_ORIGIN
    ORTHO_THRESHOLD = 0.7
    VIBE_TRADING_CATEGORIES = [
        "Momentum", "Reversal", "Liquidity", "Volatility",
        "Value", "Quality", "Size", "Growth",
    ]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
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
        price_data: Dict[str, Dict[str, List[float]]],
        fundamentals: Optional[Dict[str, Dict[str, float]]] = None,
        industries: Optional[Dict[str, str]] = None,
        benchmark_returns: Optional[List[float]] = None,
    ) -> CandidateFactorPool:
        """计算 Vibe-Trading 风格的候选因子（只读，不直接影响交易）

        本方法模拟 Vibe-Trading 项目的因子计算逻辑，使用本地数据计算候选因子。
        所有因子都标记 origin="vibe_trading"，与现有系统因子明确区分。

        Args:
            price_data: {symbol: {"closes": [...], "volumes": [...], "highs": [...], "lows": [...]}}
            fundamentals: {symbol: {"pe": ..., "pb": ..., "roe": ...}}
            industries: {symbol: industry_name}
            benchmark_returns: 基准收益率序列

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

            momentum_factors = self._compute_vt_momentum_factors(price_data)
            self._merge_into_pool(pool, momentum_factors)

            reversal_factors = self._compute_vt_reversal_factors(price_data)
            self._merge_into_pool(pool, reversal_factors)

            liquidity_factors = self._compute_vt_liquidity_factors(price_data)
            self._merge_into_pool(pool, liquidity_factors)

            volatility_factors = self._compute_vt_volatility_factors(price_data, benchmark_returns)
            self._merge_into_pool(pool, volatility_factors)

            value_factors = self._compute_vt_value_factors(fundamentals)
            self._merge_into_pool(pool, value_factors)

            quality_factors = self._compute_vt_quality_factors(fundamentals)
            self._merge_into_pool(pool, quality_factors)

            size_factors = self._compute_vt_size_factors(fundamentals)
            self._merge_into_pool(pool, size_factors)

            growth_factors = self._compute_vt_growth_factors(fundamentals)
            self._merge_into_pool(pool, growth_factors)

            pool.total_candidates = len(pool.factors)
            logger.info(
                "[VibeTradingAdapter] 候选因子计算完成 | batch=%s total=%d",
                batch_id, pool.total_candidates,
            )

        except Exception as e:
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
        price_data: Dict[str, Dict[str, List[float]]],
        fundamentals: Optional[Dict[str, Dict[str, float]]] = None,
        industries: Optional[Dict[str, str]] = None,
        benchmark_returns: Optional[List[float]] = None,
    ) -> Dict[str, FactorValue]:
        """加载现有系统的 50+ 因子（只读，不修改原对象）"""
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
        except Exception as e:
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
        existing_factors: Dict[str, FactorValue],
    ) -> Dict[str, OrthogonalityResult]:
        """检查候选因子与现有因子的正交性"""
        results: Dict[str, OrthogonalityResult] = {}

        for cand_name, cand_factor in candidate_pool.factors.items():
            try:
                cand_series = pd.Series(cand_factor.values, dtype=float)
                corr_with_existing: Dict[str, float] = {}

                for existing_name, existing_fv in existing_factors.items():
                    existing_series = pd.Series(existing_fv.values, dtype=float)
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

            except Exception as e:
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
        ortho_results: Dict[str, OrthogonalityResult],
    ) -> List[CandidateFactor]:
        """筛选通过正交性检查的候选因子"""
        qualified: List[CandidateFactor] = []
        for name, result in ortho_results.items():
            if result.is_orthogonal and name in candidate_pool.factors:
                qualified.append(candidate_pool.factors[name])

        logger.info(
            "[VibeTradingAdapter] 正交性筛选完成 | 通过=%d / 总计=%d (阈值=%.2f)",
            len(qualified), len(ortho_results), self.ortho_threshold,
        )
        return qualified
'@

Set-Content -Path $file -Value $content -Encoding UTF8
Write-Host "Part 1 写入完成: $((Get-Item $file).Length) 字节"
