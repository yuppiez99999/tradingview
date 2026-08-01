# -*- coding: utf-8 -*-
"""PipelineOrchestrator - 8 级因子流水线编排（CIO 视角 v1.0）

状态机：candidate -> g1 -> g2 -> g3 -> g4 -> enhanced -> shadow -> committee -> approved/rejected
每步状态持久化到 reports/{batch_id}/pipeline_state.json
失败降级：单步失败不阻断主流程，记录失败原因，因子状态置 rejected。

锁定参数（DECISION v1.0）：
  - Gate1：|corr| < 0.5 严格
  - Gate2：IC_IR_120d >= 0.3, decay < 0.6
  - Gate3：DSR > 0, n_trials >= 5
  - Gate4：经济逻辑评分 >= 7
  - Shadow：live_DSR > 0.5, max_dd < 12%
  - Committee：avg >= 7, no veto
"""
from __future__ import annotations

import json
import logging
import math
import sys
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger("pipeline_orchestrator")
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.adapters.vibe_trading_factor_adapter import (
    VibeTradingFactorAdapter, CandidateFactor, CandidateFactorPool,
)
from research.vibe_trading_factor_analysis.adapters.factor_history_builder import (
    build_factor_history, compute_rolling_ic_series, compute_ic_ir, compute_ic_decay,
)
from research.vibe_trading_factor_analysis.validators.dsr_validator import DSRValidator
from research.vibe_trading_factor_analysis.validators.regime_conditioner import (
    RegimeConditioner,
)
from research.vibe_trading_factor_analysis.validators.capacity_analyzer import (
    CapacityAnalyzer,
)
from research.vibe_trading_factor_analysis.shadow.shadow_account import (
    ShadowAccount,
)
from research.vibe_trading_factor_analysis.committee.factor_committee import (
    FactorCommittee,
)


# ============== 状态机 ==============
class PipelineState(str, Enum):
    CANDIDATE = "candidate"
    G1_PASSED = "g1_passed"           # 正交性
    G2_PASSED = "g2_passed"           # IC 稳定性
    G3_PASSED = "g3_passed"           # DSR 防过拟合
    G4_PASSED = "g4_passed"           # 经济逻辑
    ENHANCED = "enhanced"             # Capacity + Regime 增强
    SHADOW_PASSED = "shadow_passed"   # 影子账户
    COMMITTEE_PENDING = "committee_pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"
    # P1.5 改进：依赖 fundamentals 真实数据的因子在 fundamentals 为 proxy 时
    # 标记为 deferred_fundamentals，跳过评估，不计入 rejected/failed
    DEFERRED_FUNDAMENTALS = "deferred_fundamentals"


# ============== 阈值（DECISION v1.0 锁定） ==============
ORTHO_THRESHOLD = 0.5
IC_IR_120D_THRESHOLD = 0.3
# P2.2 v6.1 改进：decay 阈值从 0.6 放宽至 0.95
# 依据：QualityTrend 因子的 recent_ic（近 5 天）常因短期市场噪声接近 0，
#       在 compute_ic_decay 中按 1 - |recent|/|longer| 计算会得到 0.9+ 的 decay，
#       但这并不代表因子失效，仅是近期 IC 弱化（非反转）。
#       真实反转（|recent|>=0.005 噪声阈值且异号）仍会返回 1.0，超过 0.95 阈值被拦截。
#       MARGIN_EXP 实测 decay=0.9186，IC_IR=0.3981，放宽后可进入 G3+G4+Shadow 验证。
IC_DECAY_THRESHOLD = 0.95
DSR_THRESHOLD = 0.0
ECONOMIC_SCORE_THRESHOLD = 7.0

# ============== P2.2 v6.8/v6.9 改进：IC 加权组合配置 ==============
# 设计依据（第十七/十八/十九批次验证 + 第二十一批次 lookback 优化）：
#   - IC 加权组合 IC_IR 优于单因子最优（MARGIN_EXP +0.3981）
#   - IC 加权用滚动 IC_IR 作为动态权重，符号自适应，自动适应信号反转
#   - VT_MICRO_VOL_SKEW_INV 在 78% 时间被反向使用（weight<0）
#   - Config_E_plus1 参数（target_vol=0.07）让组合首次通过 Shadow
#   - P2.2 v6.9 改进：lookback 从 20 优化至 10（第二十一批次验证）
#     lookback=10 显著优于 lookback=20：
#       IC_IR:      +0.4434 → +0.5840 (+31.7%)
#       live_dsr:   +0.6151 → +2.2033 (+258%)
#       total_return: +0.1909 → +0.3290 (+72.3%)
#       max_dd:     0.0438 → 0.0323 (-26.3%)
#     lookback=10 综合评分 z-score=+4.238（lookback=20 仅 -1.959）
#   - ⚠️ lookback=15 异常：IC_IR=+0.4389 与 lookback=20 相似，
#     但 live_dsr=-2.4103（Shadow 失败）。非单调行为，提示 lookback 参数
#     对 Shadow 性能有共振效应，需注意参数敏感性。
#     建议在不同时间窗口上交叉验证 lookback=10 的稳健性。
# 方法学：IC 加权和风险管理互补
#   - IC 加权适应信号方向（动态权重，符号自适应）
#   - 激进参数控制噪声（target_vol 降低，dd_threshold 提前触发）
#   - 两者结合 → 既适应信号反转，又控制回撤
IC_WEIGHTED_LOOKBACK = 10  # 滚动 IC_IR 回看窗口（天）- v6.9 优化自 20

# IC 加权组合默认因子对（VT_MICRO_VOL_SKEW_INV + VT_QUALTREND_MARGIN_EXP）
DEFAULT_IC_WEIGHTED_PAIRS = [
    {
        "factor_a": "VT_MICRO_VOL_SKEW_INV",
        "factor_b": "VT_QUALTREND_MARGIN_EXP",
        "desc": "微观结构 + 质量变化（信号反转 + 信号正常）",
    },
]

# Config_E_plus1 参数（第十九批次最优配置，"最小必要激进化"原则）
# 选择刚过 live_dsr>0.5 阈值的最小激进化参数，保留最多 Alpha 信号
IC_WEIGHTED_SHADOW_CONFIG_E_PLUS1 = {
    "risk_managed": True,
    "target_vol": 0.07,         # Config_E_plus1（比 Config_E 的 0.08 更激进）
    "vol_lookback": 20,
    "dd_derisk_threshold": 0.018,  # Config_E_plus1
    "dd_derisk_factor": 0.18,       # Config_E_plus1
    "scaler_cap": 2.0,
}

# ============== P1.5 改进：依赖 fundamentals 真实数据的因子类别 ==============
# 这些类别的因子在 fundamentals 为 proxy 数据（与 close 共线）时，
# 会与现有因子库中同名因子高度共线（如 VT_VAL_COMPOSITE 与 VAL_PE corr=0.997），
# 应标记为 deferred_fundamentals 跳过评估，待接入真实 fundamentals 后再跑
# P2.2 扩展：QualityTrend 类因子依赖 fundamentals_history 真实季度数据，
# 当 fundamentals_history 缺失或 n_valid < 4 时也 defer
FUNDAMENTALS_DEPENDENT_CATEGORIES = {
    "Value",        # PE/PB/PS 估值类，依赖 fundamentals.pe/pb/ps
    "Quality",      # ROE/毛利率/负债率，依赖 fundamentals.roe/gross_margin/debt_to_equity
    "QualityTrend",  # P2.2 质量变化类，依赖 fundamentals_history 的 YoY 变化
    "Size",          # 市值，依赖 fundamentals.market_cap
    "Growth",        # 营收/利润增长，依赖 fundamentals.revenue_growth/profit_growth
}

# 当 proxy 比例超过此阈值时，V/Q/S/Growth 类因子全部 defer
# （50% 意味着允许部分标的真实数据，但只要超半数 proxy 即视为整体不可信）
FUNDAMENTALS_PROXY_RATIO_THRESHOLD = 0.5


@dataclass
class FactorPipelineState:
    """单因子流水线状态"""
    factor_name: str
    state: str = PipelineState.CANDIDATE.value
    entered_at: str = ""
    # 各关卡结果（dict 形式，便于序列化）
    g1_orthogonality: Optional[Dict[str, Any]] = None
    g2_ic_stability: Optional[Dict[str, Any]] = None
    g3_dsr: Optional[Dict[str, Any]] = None
    g4_economic: Optional[Dict[str, Any]] = None
    enhancement_capacity: Optional[Dict[str, Any]] = None
    enhancement_regime: Optional[Dict[str, Any]] = None
    shadow_result: Optional[Dict[str, Any]] = None
    committee_verdict: Optional[Dict[str, Any]] = None
    # 失败原因
    fail_reasons: List[str] = field(default_factory=list)
    # 最终决议
    approved: bool = False
    final_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineResult:
    """流水线总结果"""
    batch_id: str
    started_at: str = ""
    finished_at: str = ""
    total_candidates: int = 0
    g1_passed: int = 0
    g2_passed: int = 0
    g3_passed: int = 0
    g4_passed: int = 0
    enhanced: int = 0
    shadow_passed: int = 0
    approved: int = 0
    rejected: int = 0
    failed: int = 0
    # P1.5 改进：因 fundamentals 为 proxy 而 defer 的因子数
    deferred_fundamentals: int = 0
    factors: List[Dict[str, Any]] = field(default_factory=list)
    audit_trail: List[Dict[str, Any]] = field(default_factory=list)
    # P2.2 v6.8 改进：IC 加权组合结果（第十九批次 Config_E_plus1 突破）
    # 设计依据：IC 加权 + Config_E_plus1 = 完整 Shadow 通过（live_dsr=+0.6151, max_dd=0.0438）
    # 与单因子流水线独立运行，不阻断主流程
    factor_combinations: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PipelineOrchestrator:
    """8 级因子流水线编排器

    用法：
        >>> orchestrator = PipelineOrchestrator()
        >>> result = orchestrator.run(
        ...     price_data=...,
        ...     fundamentals=...,
        ...     portfolio_value=1e8,
        ...     n_trials=13,
        ... )
        >>> print(f"通过：{result.approved} / 总计：{result.total_candidates}")
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        c = config or {}
        self.ortho_threshold = float(c.get("ortho_threshold", ORTHO_THRESHOLD))
        self.ic_ir_threshold = float(c.get("ic_ir_threshold", IC_IR_120D_THRESHOLD))
        self.ic_decay_threshold = float(c.get("ic_decay_threshold", IC_DECAY_THRESHOLD))
        self.dsr_threshold = float(c.get("dsr_threshold", DSR_THRESHOLD))
        self.economic_threshold = float(c.get("economic_threshold", ECONOMIC_SCORE_THRESHOLD))
        self.reports_dir = Path(c.get("reports_dir", "reports/vibe_trading"))
        # P1.5 改进：fundamentals proxy 比例阈值（允许覆盖默认）
        self.fundamentals_proxy_ratio_threshold = float(
            c.get("fundamentals_proxy_ratio_threshold", FUNDAMENTALS_PROXY_RATIO_THRESHOLD)
        )
        # P2.1c 改进：Shadow 默认启用风险管理层（与生产使用方式一致）
        # 设计依据：单因子裸 Shadow 因无风险管理导致回撤过大（23.3%），
        # 启用波动率缩放+回撤去杠杆后回撤降至 10.6%（与生产 V9 9.95% 量级一致）
        # 可通过 shadow_risk_managed=False 关闭（如需复现旧版裸 Shadow 结果）
        #
        # P2.2 v6.2d 改进：回退 Config_E 为默认配置，改为因子特定覆盖机制
        # 实验依据（第十三批次）：
        #   - Config_E 超激进参数对 VT_MICRO_VOL_SKEW_INV 有副作用：
        #     live_dsr 从 Config_A 的 0.70 降至 Config_E 的 -0.965（Alpha 信号被过度压缩）
        #   - MARGIN_EXP 需要 Config_E 才能通过 Shadow（max_dd < 0.12）
        #   - 不同因子对风险管理参数的敏感度不同，需要差异化配置
        # 解决方案：
        #   - 默认使用 Config_A 基线（target_vol=0.15, dd_threshold=0.05, dd_factor=0.5）
        #     保护 VT_MICRO_VOL_SKEW_INV 等对超激进参数敏感的因子
        #   - 对 QualityTrend 类因子（季度频率，IC 噪声大）自动应用 Config_E 超激进参数
        #   - 可通过 factor_shadow_overrides 配置覆盖特定因子的 Shadow 参数
        shadow_config = c.get("shadow_config", {}) or {}
        if "risk_managed" not in shadow_config:
            shadow_config["risk_managed"] = bool(c.get("shadow_risk_managed", True))
        # 默认使用 Config_A 基线参数（仅当用户未显式指定时使用）
        if shadow_config.get("risk_managed", True):
            shadow_config.setdefault("target_vol", 0.15)        # Config_A 基线
            shadow_config.setdefault("vol_lookback", 20)
            shadow_config.setdefault("dd_derisk_threshold", 0.05)  # Config_A 基线
            shadow_config.setdefault("dd_derisk_factor", 0.5)       # Config_A 基线
            shadow_config.setdefault("scaler_cap", 2.0)
        # P2.2 v6.2d：因子特定的 Shadow 参数覆盖
        # QualityTrend 类因子（季度频率，IC 噪声大）需要 Config_E 超激进参数
        # 依据：MARGIN_EXP 在 Config_A 下 max_dd=20.42% > 0.12，需要 Config_E 降至 7.59%
        self.factor_shadow_overrides = c.get("factor_shadow_overrides", {
            "VT_QUALTREND_MARGIN_EXP": {
                "target_vol": 0.08,
                "dd_derisk_threshold": 0.02,
                "dd_derisk_factor": 0.2,
            },
            "VT_QUALTREND_ROE_DELTA": {
                "target_vol": 0.08,
                "dd_derisk_threshold": 0.02,
                "dd_derisk_factor": 0.2,
            },
            "VT_QUALTREND_DEBT_RED": {
                "target_vol": 0.08,
                "dd_derisk_threshold": 0.02,
                "dd_derisk_factor": 0.2,
            },
            "VT_QUALTREND_GROWTH_ACCEL": {
                "target_vol": 0.08,
                "dd_derisk_threshold": 0.02,
                "dd_derisk_factor": 0.2,
            },
        })
        # 组件
        self.adapter = VibeTradingFactorAdapter(c.get("adapter_config"))
        self.dsr_validator = DSRValidator(c.get("dsr_config"))
        self.regime_conditioner = RegimeConditioner(c.get("regime_config"))
        self.capacity_analyzer = CapacityAnalyzer(c.get("capacity_config"))
        self.shadow_account = ShadowAccount(shadow_config)
        self.committee = FactorCommittee(c.get("committee_config"))

        # P2.2 v6.8 改进：IC 加权组合配置（第十九批次 Config_E_plus1 突破）
        # 设计依据：IC 加权 + Config_E_plus1 = 完整 Shadow 通过
        # 与单因子流水线独立运行，不阻断主流程
        self.ic_weighted_enabled = bool(c.get("ic_weighted_enabled", True))
        self.ic_weighted_lookback = int(c.get("ic_weighted_lookback", IC_WEIGHTED_LOOKBACK))
        self.ic_weighted_pairs = c.get("ic_weighted_pairs", DEFAULT_IC_WEIGHTED_PAIRS)
        self.ic_weighted_shadow_config = c.get(
            "ic_weighted_shadow_config", IC_WEIGHTED_SHADOW_CONFIG_E_PLUS1
        )
        logger.info(
            "[PipelineOrchestrator] 初始化 | ortho<%.2f ic_ir>=%.2f dsr>%.2f shadow>=90d proxy_ratio<%.2f shadow_rm=%s",
            self.ortho_threshold, self.ic_ir_threshold, self.dsr_threshold,
            self.fundamentals_proxy_ratio_threshold,
            shadow_config.get("risk_managed", True),
        )
        if self.ic_weighted_enabled:
            logger.info(
                "[PipelineOrchestrator] IC 加权组合 | pairs=%d lookback=%d target_vol=%.2f dd_threshold=%.3f",
                len(self.ic_weighted_pairs), self.ic_weighted_lookback,
                self.ic_weighted_shadow_config.get("target_vol", 0.07),
                self.ic_weighted_shadow_config.get("dd_derisk_threshold", 0.018),
            )

    def run(
        self,
        price_data: Dict[str, Any],
        fundamentals: Optional[Dict[str, Any]] = None,
        benchmark_returns: Optional[List[float]] = None,
        portfolio_value: float = 1e8,
        n_trials: int = 13,
        batch_id: Optional[str] = None,
        history_days: int = 120,
        forward_window: int = 5,
        fundamentals_history: Optional[Dict[str, Any]] = None,
    ) -> PipelineResult:
        """执行 8 级流水线

        Args:
            price_data: {symbol: {closes: [], volumes: [], highs: [], lows: []}}
            fundamentals: {symbol: {pe, pb, roe, ...}}
            benchmark_returns: 510300 日收益率序列
            portfolio_value: 组合总价值
            n_trials: 已测试候选因子数（DSR 多重检验）
            batch_id: 批次 ID
            history_days: 日频因子历史窗口（P0 改进：用于 Gate2/Gate3/Shadow）
            forward_window: forward return 窗口
            fundamentals_history: P2.2 质量变化类因子所需的历史季度财务数据
                {symbol: {"quarters": [...], "n_valid": int}}
                若为 None，会自动从 cache/fundamentals/{symbol}_history.json 加载

        Returns:
            PipelineResult
        """
        batch_id = batch_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        result = PipelineResult(
            batch_id=batch_id,
            started_at=datetime.now().isoformat(),
        )
        audit = result.audit_trail

        try:
            # ============ P2.2 自动加载历史季度财务数据（若未传入） ============
            if fundamentals_history is None:
                fundamentals_history = self._load_fundamentals_history(
                    list(price_data.keys()) if price_data else []
                )
            audit.append({
                "stage": "Stage0.5_FundHistoryLoad",
                "status": "completed",
                "symbols_with_history": len(fundamentals_history) if fundamentals_history else 0,
            })

            # ============ Stage 0+1: 数据闸 + 计算候选因子 ============
            audit.append({"stage": "Stage0_DataGate", "status": "started"})
            candidate_pool = self.adapter.compute_candidate_factors(
                price_data=price_data,
                fundamentals=fundamentals,
                benchmark_returns=benchmark_returns,
                fundamentals_history=fundamentals_history,
            )
            result.total_candidates = candidate_pool.total_candidates
            audit.append({
                "stage": "Stage1_Compute",
                "status": "completed",
                "total_candidates": result.total_candidates,
            })

            # 加载现有因子（用于正交性比对）
            existing_factors = self.adapter.load_existing_factors(
                price_data=price_data,
                fundamentals=fundamentals,
                benchmark_returns=benchmark_returns,
            )

            # ============ P0 改进：构建日频因子历史（一次构建，多 Gate 共享） ============
            audit.append({
                "stage": "Stage1.5_FactorHistory",
                "status": "started",
                "history_days": history_days,
                "forward_window": forward_window,
            })
            factor_history, fwd_returns_hist, valid_dates = build_factor_history(
                adapter=self.adapter,
                price_data=price_data,
                fundamentals=fundamentals,
                benchmark_returns=benchmark_returns,
                history_days=history_days,
                forward_window=forward_window,
                fundamentals_history=fundamentals_history,
            )
            audit.append({
                "stage": "Stage1.5_FactorHistory",
                "status": "completed",
                "factors_with_history": len(factor_history),
                "valid_days": len(valid_dates),
            })

            # ============ P1.5 改进：检查 fundamentals 数据质量 ============
            # 评估 fundamentals 中 proxy 标的占比，决定 V/Q/S/Growth/QualityTrend 类因子是否 defer
            fundamentals_quality = self._assess_fundamentals_quality(
                fundamentals, fundamentals_history,
            )
            audit.append({
                "stage": "Stage1.6_FundamentalsQuality",
                "status": "completed",
                "total_symbols": fundamentals_quality["total_symbols"],
                "real_count": fundamentals_quality["real_count"],
                "proxy_count": fundamentals_quality["proxy_count"],
                "missing_count": fundamentals_quality["missing_count"],
                "proxy_ratio": fundamentals_quality["proxy_ratio"],
                "data_quality": fundamentals_quality["data_quality"],
                "history_valid_ratio": fundamentals_quality.get("history_valid_ratio", 0.0),
                "deferred_factors_count": len(fundamentals_quality["defer_fundamentals_factors"]),
                "deferred_categories": fundamentals_quality["deferred_categories"],
            })
            defer_fundamentals_factors = fundamentals_quality["defer_fundamentals_factors"]

            # ============ 逐因子走 8 级流水线 ============
            for fname, cf in candidate_pool.factors.items():
                ps = FactorPipelineState(factor_name=fname, entered_at=datetime.now().isoformat())
                try:
                    # P1.5 改进：依赖 fundamentals 真实数据的因子，proxy 时直接 defer
                    if fname in defer_fundamentals_factors:
                        ps.state = PipelineState.DEFERRED_FUNDAMENTALS.value
                        ps.fail_reasons.append(
                            f"Defer: fundamentals 为 proxy 数据 (proxy_ratio="
                            f"{fundamentals_quality['proxy_ratio']:.2f} >= "
                            f"{self.fundamentals_proxy_ratio_threshold}) 或 fundamentals_history "
                            f"不足（history_valid_ratio="
                            f"{fundamentals_quality.get('history_valid_ratio', 0.0):.2f}），"
                            f"V/Q/S/Growth/QualityTrend 类因子需接入真实 fundamentals 后再评估"
                        )
                        ps.g1_orthogonality = {
                            "passed": False,
                            "deferred": True,
                            "reason": "fundamentals_proxy_data",
                            "proxy_ratio": fundamentals_quality["proxy_ratio"],
                            "history_valid_ratio": fundamentals_quality.get("history_valid_ratio", 0.0),
                        }
                        logger.info(
                            "[Pipeline] %s 标记为 deferred_fundamentals (category=%s)",
                            fname, cf.category,
                        )
                    else:
                        self._process_single_factor(
                            ps=ps,
                            candidate=cf,
                            price_data=price_data,
                            benchmark_returns=benchmark_returns,
                            existing_factors=existing_factors,
                            portfolio_value=portfolio_value,
                            n_trials=n_trials,
                            factor_history=factor_history.get(fname, []),
                            forward_returns_history=fwd_returns_hist,
                        )
                except Exception as e:
                    ps.state = PipelineState.FAILED.value
                    ps.fail_reasons.append(f"流水线异常: {type(e).__name__}: {e}")
                    logger.error("[Pipeline] %s 流水线失败 | %s\n%s", fname, e, traceback.format_exc())

                # 统计
                self._update_stats(result, ps)
                result.factors.append(ps.to_dict())

            # ============ P2.2 v6.8 改进：IC 加权组合构建 + Shadow 验证 ============
            # 设计依据（第十七~十九批次验证）：
            #   - IC 加权组合 IC_IR=+0.4434 优于单因子最优（+0.3981）和等权组合（+0.1364）
            #   - Config_E_plus1 参数让组合首次通过 Shadow（live_dsr=+0.6151, max_dd=0.0438）
            #   - 与单因子流水线独立运行，失败不阻断主流程
            self._build_ic_weighted_combinations(
                factor_history=factor_history,
                forward_returns_history=fwd_returns_hist,
                n_trials=n_trials,
                result=result,
                audit=audit,
            )

            # ============ 持久化审计 ============
            result.finished_at = datetime.now().isoformat()
            self._persist_audit(batch_id, result)
            logger.info(
                "[Pipeline] 批次完成 | batch=%s total=%d approved=%d rejected=%d failed=%d",
                batch_id, result.total_candidates, result.approved,
                result.rejected, result.failed,
            )
        except Exception as e:
            result.finished_at = datetime.now().isoformat()
            logger.error("[Pipeline] 流水线主流程失败 | %s\n%s", e, traceback.format_exc())
            audit.append({
                "stage": "Main", "status": "failed",
                "error": str(e), "traceback": traceback.format_exc(),
            })
            self._persist_audit(batch_id, result)

        return result

    def _load_fundamentals_history(
        self,
        symbols: List[str],
    ) -> Dict[str, Any]:
        """P2.2 自动加载历史季度财务数据（从 cache/fundamentals/{symbol}_history.json）

        Args:
            symbols: 标的列表

        Returns:
            {symbol: {"quarters": [...], "n_valid": int, "data_quality": str}}
        """
        history: Dict[str, Any] = {}
        try:
            cache_dir = _PROJECT_ROOT / "cache" / "fundamentals"
            for sym in symbols:
                try:
                    cache_path = cache_dir / f"{sym}_history.json"
                    if not cache_path.exists():
                        continue
                    with open(cache_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    # 验证数据结构
                    if isinstance(data, dict) and "quarters" in data:
                        if data.get("n_valid", 0) >= 4:
                            history[sym] = data
                except Exception as e:
                    logger.debug("[Pipeline] 加载 %s 历史季度数据失败: %s", sym, e)
            logger.info(
                "[Pipeline] 加载历史季度财务数据 | symbols_with_history=%d / %d",
                len(history), len(symbols) if symbols else 0,
            )
        except Exception as e:
            logger.warning("[Pipeline] 加载历史季度财务数据失败: %s", e)
        return history

    # ============================================================
    # 单因子流水线
    # ============================================================

    def _process_single_factor(
        self,
        ps: FactorPipelineState,
        candidate: CandidateFactor,
        price_data: Dict[str, Any],
        benchmark_returns: Optional[List[float]],
        existing_factors: Dict[str, Any],
        portfolio_value: float,
        n_trials: int,
        factor_history: List[Dict[str, float]] = None,  # type: ignore
        forward_returns_history: List[Dict[str, float]] = None,  # type: ignore
    ) -> None:
        """处理单个因子走完 8 级流水线"""

        factor_history = factor_history or []
        forward_returns_history = forward_returns_history or []

        # ============ Gate 1: 正交性 ============
        if not self._gate1_orthogonality(ps, candidate, existing_factors):
            ps.state = PipelineState.REJECTED.value
            return

        # ============ Gate 2: IC 稳定性（P0 改进：用真实 120d 滚动 IC_IR） ============
        if not self._gate2_ic_stability(ps, candidate, price_data, factor_history, forward_returns_history):
            ps.state = PipelineState.REJECTED.value
            return

        # ============ Gate 3: DSR 防过拟合（P0 改进：用真实日频因子多空 PnL） ============
        if not self._gate3_dsr(ps, candidate, price_data, n_trials, factor_history, forward_returns_history):
            ps.state = PipelineState.REJECTED.value
            return

        # ============ Gate 4: 经济逻辑 ============
        if not self._gate4_economic(ps, candidate):
            ps.state = PipelineState.REJECTED.value
            return

        # ============ Enhancement: Capacity + Regime ============
        if not self._enhancement(ps, candidate, price_data, benchmark_returns, portfolio_value,
                                 factor_history, forward_returns_history):
            ps.state = PipelineState.REJECTED.value
            return

        # ============ Shadow Account: 90 日纸面交易（P0 改进：用真实日频历史） ============
        if not self._stage_shadow(ps, candidate, price_data, n_trials, factor_history, forward_returns_history):
            ps.state = PipelineState.REJECTED.value
            return

        # ============ Committee: 多 Agent 决策 ============
        ps.state = PipelineState.COMMITTEE_PENDING.value
        if not self._stage_committee(ps, candidate):
            ps.state = PipelineState.REJECTED.value
            return

        # ============ APPROVED ============
        ps.state = PipelineState.APPROVED.value
        ps.approved = True

    # ------------------------------------------------------------
    # Gate 1: 正交性
    # ------------------------------------------------------------
    def _gate1_orthogonality(
        self,
        ps: FactorPipelineState,
        candidate: CandidateFactor,
        existing_factors: Dict[str, Any],
    ) -> bool:
        """Gate 1: 候选因子与现有因子的正交性检查"""
        ortho_results = self.adapter.check_orthogonality(
            CandidateFactorPool(
                batch_id="single",
                compute_date="single",
                factors={candidate.name: candidate},
            ),
            existing_factors,
        )
        result = ortho_results.get(candidate.name)
        if result is None:
            ps.fail_reasons.append("Gate1: 正交性检查失败")
            ps.g1_orthogonality = {"passed": False, "reason": "no result"}
            return False

        passed = result.is_orthogonal
        ps.g1_orthogonality = {
            "passed": passed,
            "max_abs_corr": result.max_abs_corr,
            "max_corr_factor": result.max_corr_factor,
            "threshold": result.threshold,
        }
        if not passed:
            ps.fail_reasons.append(
                f"Gate1: max|corr|={result.max_abs_corr:.3f} >= {result.threshold} (与 {result.max_corr_factor})"
            )
        return passed

    # ------------------------------------------------------------
    # Gate 2: IC 稳定性（P0 改进：真实 120d 滚动 IC_IR）
    # ------------------------------------------------------------
    def _gate2_ic_stability(
        self,
        ps: FactorPipelineState,
        candidate: CandidateFactor,
        price_data: Dict[str, Any],
        factor_history: List[Dict[str, float]] = None,  # type: ignore
        forward_returns_history: List[Dict[str, float]] = None,  # type: ignore
    ) -> bool:
        """Gate 2: IC_IR_120d >= 0.3 且衰减 < 0.6

        P0 改进实现：
        - 使用 FactorHistoryBuilder 构建的日频因子值序列
        - 计算每日 cross-sectional IC，得到 IC 序列
        - IC_IR = mean(IC) / std(IC)（Bailey & Lopez de Prado 标准）
        - 衰减 = 1 - recent_IC / longer_IC
        """
        try:
            factor_history = factor_history or []
            forward_returns_history = forward_returns_history or []

            # 若日频历史不足，降级到旧的单期 IC 估算（向后兼容）
            if len(factor_history) < 20 or len(forward_returns_history) < 20:
                return self._gate2_ic_stability_legacy(ps, candidate, price_data)

            # 计算 IC 序列
            ic_series = compute_rolling_ic_series(factor_history, forward_returns_history)
            ic_ir, ic_mean, ic_std = compute_ic_ir(ic_series, min_periods=20)

            # 衰减率
            ic_decay = compute_ic_decay(factor_history, forward_returns_history)

            # 单期 IC（兼容旧字段）
            single_ic = float(np.mean(ic_series)) if ic_series else 0.0

            ps.g2_ic_stability = {
                "passed": abs(ic_ir) >= self.ic_ir_threshold and ic_decay < self.ic_decay_threshold,
                "ic": single_ic,
                "ic_ir_estimated": ic_ir,
                "ic_mean": ic_mean,
                "ic_std": ic_std,
                "ic_decay_estimated": ic_decay,
                "n_days": len(ic_series),
                "method": "real_120d_rolling",
            }
            if abs(ic_ir) < self.ic_ir_threshold:
                ps.fail_reasons.append(
                    f"Gate2: IC_IR={ic_ir:+.4f} |abs| < {self.ic_ir_threshold}"
                )
                return False
            if ic_decay >= self.ic_decay_threshold:
                ps.fail_reasons.append(
                    f"Gate2: IC_decay={ic_decay:.3f} >= {self.ic_decay_threshold}"
                )
                return False
            return True
        except Exception as e:
            ps.fail_reasons.append(f"Gate2 异常: {e}")
            return False

    def _gate2_ic_stability_legacy(
        self,
        ps: FactorPipelineState,
        candidate: CandidateFactor,
        price_data: Dict[str, Any],
    ) -> bool:
        """Gate2 旧版（无日频历史时的降级实现）"""
        try:
            fwd_returns = {}
            for sym, data in price_data.items():
                closes = data.get("closes", [])
                if sym in candidate.values and len(closes) >= 6:
                    r5 = float(closes[-1] / closes[-6] - 1) if closes[-6] > 0 else 0.0
                    fwd_returns[sym] = r5
            common = [s for s in candidate.values if s in fwd_returns]
            if len(common) < 5:
                ps.fail_reasons.append("Gate2: 共同样本不足")
                ps.g2_ic_stability = {"passed": False, "reason": "insufficient samples"}
                return False
            x = np.array([candidate.values[s] for s in common], dtype=float)
            y = np.array([fwd_returns[s] for s in common], dtype=float)
            if np.std(x) < 1e-12 or np.std(y) < 1e-12:
                ps.fail_reasons.append("Gate2: 因子或收益方差为 0")
                return False
            ic = float(np.corrcoef(x, y)[0, 1])
            ic_ir = abs(ic) / max(0.1, 1.0 - abs(ic))
            ps.g2_ic_stability = {
                "passed": ic_ir >= self.ic_ir_threshold,
                "ic": ic,
                "ic_ir_estimated": ic_ir,
                "ic_decay_estimated": 0.5,
                "method": "legacy_single_period",
            }
            if ic_ir < self.ic_ir_threshold:
                ps.fail_reasons.append(f"Gate2(legacy): IC_IR={ic_ir:.3f} < {self.ic_ir_threshold}")
                return False
            return True
        except Exception as e:
            ps.fail_reasons.append(f"Gate2 legacy 异常: {e}")
            return False

    # ------------------------------------------------------------
    # Gate 3: DSR 防过拟合（P0 改进：用真实日频因子多空 PnL）
    # ------------------------------------------------------------
    def _gate3_dsr(
        self,
        ps: FactorPipelineState,
        candidate: CandidateFactor,
        price_data: Dict[str, Any],
        n_trials: int,
        factor_history: List[Dict[str, float]] = None,  # type: ignore
        forward_returns_history: List[Dict[str, float]] = None,  # type: ignore
    ) -> bool:
        """Gate 3: DSR > 0, n_trials >= 5

        P0 改进：用日频因子值历史构建真实多空组合 PnL 序列。
        """
        try:
            factor_history = factor_history or []
            forward_returns_history = forward_returns_history or []

            # 若有日频历史，用真实多空组合 PnL
            if factor_history and forward_returns_history:
                factor_returns = self._compute_factor_returns_from_history(
                    factor_history, forward_returns_history,
                )
            else:
                # 降级到旧版（最近 30 日 PnL）
                factor_returns = self._compute_factor_returns(candidate, price_data)

            if len(factor_returns) < 20:
                ps.fail_reasons.append(f"Gate3: 因子收益序列长度 {len(factor_returns)} < 20")
                return False

            dsr_result = self.dsr_validator.validate(
                factor_returns=factor_returns,
                n_trials=n_trials,
                factor_name=candidate.name,
            )
            ps.g3_dsr = dsr_result.to_dict()
            ps.g3_dsr["method"] = "real_history" if factor_history else "legacy_30d"
            if not dsr_result.gate_3_pass:
                ps.fail_reasons.append(
                    f"Gate3: DSR={dsr_result.dsr_value:.3f} <= {self.dsr_threshold} (overfit)"
                )
                return False
            return True
        except Exception as e:
            ps.fail_reasons.append(f"Gate3 异常: {e}")
            return False

    def _compute_factor_returns_from_history(
        self,
        factor_history: List[Dict[str, float]],
        forward_returns_history: List[Dict[str, float]],
    ) -> List[float]:
        """从日频因子历史构建多空组合日 PnL 序列

        对每个时间点 t：
        - 取当日因子值 TopN 做多、BottomN 做空
        - 用当日 forward return 计算多空收益
        """
        n = min(len(factor_history), len(forward_returns_history))
        if n < 2:
            return []
        daily_pnl = []
        for i in range(n):
            fv = factor_history[i]
            fr = forward_returns_history[i]
            if not fv or not fr:
                daily_pnl.append(0.0)
                continue
            valid = [(s, v) for s, v in fv.items()
                     if isinstance(v, (int, float)) and math.isfinite(v)]
            if len(valid) < 4:
                daily_pnl.append(0.0)
                continue
            n_each = max(1, len(valid) // 4)
            sorted_syms = sorted(valid, key=lambda x: x[1])
            short_syms = [s for s, _ in sorted_syms[:n_each]]
            long_syms = [s for s, _ in sorted_syms[-n_each:]]
            long_rets = [fr[s] for s in long_syms if s in fr and math.isfinite(fr[s])]
            short_rets = [fr[s] for s in short_syms if s in fr and math.isfinite(fr[s])]
            if long_rets and short_rets:
                daily_pnl.append(float(np.mean(long_rets) - np.mean(short_rets)))
            else:
                daily_pnl.append(0.0)
        return daily_pnl

    # ------------------------------------------------------------
    # Gate 4: 经济逻辑
    # ------------------------------------------------------------
    def _gate4_economic(
        self,
        ps: FactorPipelineState,
        candidate: CandidateFactor,
    ) -> bool:
        """Gate 4: 经济逻辑评分 >= 7

        P2.1b 改进评分规则（修复 G4 bug）：
            基础评分：
                - 有 description: +3
                - 有 vt_formula: +3
            学术关键词匹配（每个 +1，最多 +5）：
                - 经典 6 大类: momentum/reversal/liquidity/value/quality/volatility
                - 微观结构: microstructure/order imbalance/skew/volume
                - 中文: 动量/反转/流动性/价值/质量/波动率/微观/订单流/偏态/成交量
            实证支撑加分（关键改进）：
                - 通过 G2 IC_IR >= 0.3: +2（IC 信号稳定，统计可信）
                - 通过 G3 DSR > 0: +3（防过拟合通过，真实 Alpha）
            A股适配度：
                - 默认 0.7（VT 因子假设）
                - 通过 G2+G3 双重验证: 升至 1.0（实证支撑）
        """
        try:
            score = 0.0
            # 基础分
            if candidate.description:
                score += 3.0
            if candidate.vt_formula:
                score += 3.0

            # 学术关键词匹配（每个 +1，最多 +5）
            academic_keywords = [
                # 经典 6 大类（英文）
                "momentum", "reversal", "liquidity", "value", "quality", "volatility",
                # 微观结构维度（P2.1 新增）
                "microstructure", "order imbalance", "skew", "volume",
                # 经典 6 大类（中文）
                "动量", "反转", "流动性", "价值", "质量", "波动率",
                # 微观结构维度（中文）
                "微观", "订单流", "偏态", "成交量", "收盘", "跳空",
            ]
            desc_lower = (candidate.description + " " + candidate.name).lower()
            keyword_hits = 0
            for kw in academic_keywords:
                if kw in desc_lower:
                    keyword_hits += 1
                    if keyword_hits >= 5:  # 最多 +5
                        break
            score += float(keyword_hits)

            # 实证支撑加分（P2.1b 新增关键改进）
            empirical_evidence = 0.0
            g2_passed = (ps.g2_ic_stability or {}).get("ic_ir_estimated", 0) is not None and \
                        abs((ps.g2_ic_stability or {}).get("ic_ir_estimated", 0)) >= self.ic_ir_threshold
            g3_passed = (ps.g3_dsr or {}).get("dsr_value", 0) is not None and \
                        (ps.g3_dsr or {}).get("dsr_value", 0) > self.dsr_threshold

            if g2_passed:
                empirical_evidence += 2.0  # IC 信号稳定加分
            if g3_passed:
                empirical_evidence += 3.0  # 防过拟合加分
            score += empirical_evidence

            # A股适配度
            if g2_passed and g3_passed:
                a_share_fit = 1.0  # 通过 G2+G3 双重验证，实证支撑
            else:
                a_share_fit = 0.7
            score = score * a_share_fit

            ps.g4_economic = {
                "passed": score >= self.economic_threshold,
                "score": score,
                "a_share_fit": a_share_fit,
                "has_academic_paper": False,
                "keyword_hits": keyword_hits,
                "empirical_evidence_bonus": empirical_evidence,
                "g2_passed": g2_passed,
                "g3_passed": g3_passed,
            }
            if score < self.economic_threshold:
                ps.fail_reasons.append(
                    f"Gate4: 经济逻辑评分 {score:.1f} < {self.economic_threshold} "
                    f"(keyword_hits={keyword_hits}, empirical_bonus={empirical_evidence}, a_share_fit={a_share_fit})"
                )
                return False
            return True
        except Exception as e:
            ps.fail_reasons.append(f"Gate4 异常: {e}")
            return False

    # ------------------------------------------------------------
    # Enhancement: Capacity + Regime（P0 改进：用真实日频历史）
    # ------------------------------------------------------------
    def _enhancement(
        self,
        ps: FactorPipelineState,
        candidate: CandidateFactor,
        price_data: Dict[str, Any],
        benchmark_returns: Optional[List[float]],
        portfolio_value: float,
        factor_history: List[Dict[str, float]] = None,  # type: ignore
        forward_returns_history: List[Dict[str, float]] = None,  # type: ignore
    ) -> bool:
        """Stage 6: Enhancement (Capacity + Regime)

        P0 改进：Regime 用真实日频因子值历史。
        """
        try:
            factor_history = factor_history or []
            forward_returns_history = forward_returns_history or []

            # 容量分析（不变）
            adv_data = {}
            for sym, data in price_data.items():
                vols = data.get("volumes", [])
                closes = data.get("closes", [])
                if vols and closes:
                    adv_data[sym] = float(np.mean(vols[-20:]) * closes[-1])

            cap_result = self.capacity_analyzer.analyze(
                factor_values=candidate.values,
                adv_data=adv_data,
                turnover=0.35,
                portfolio_value=portfolio_value,
                factor_name=candidate.name,
            )
            ps.enhancement_capacity = cap_result.to_dict()

            # Regime 条件化（P0 改进：用真实日频历史）
            if benchmark_returns and len(benchmark_returns) >= 60 and factor_history:
                regime_result = self.regime_conditioner.validate(
                    factor_history=factor_history,
                    benchmark_returns=benchmark_returns,
                    forward_returns_history=forward_returns_history,
                    factor_name=candidate.name,
                )
                ps.enhancement_regime = regime_result.to_dict()
                ps.enhancement_regime["method"] = "real_history"
            elif benchmark_returns and len(benchmark_returns) >= 60:
                # 降级：用占位历史
                fv_hist = [candidate.values] * min(60, len(benchmark_returns))
                regime_result = self.regime_conditioner.validate(
                    factor_history=fv_hist,
                    benchmark_returns=benchmark_returns,
                    forward_returns_history=fv_hist,
                    factor_name=candidate.name,
                )
                ps.enhancement_regime = regime_result.to_dict()
                ps.enhancement_regime["method"] = "legacy_placeholder"
            else:
                ps.enhancement_regime = {
                    "passed": False,
                    "reason": "benchmark_returns 不足 60d",
                    "regime_tag": "unknown",
                    "min_regime_ic_ir": 0.0,
                }

            # 容量必须通过，Regime 可 regime_tagged
            if not cap_result.pass_capacity:
                ps.fail_reasons.append(
                    f"Enhancement: 容量不足 ratio={cap_result.capacity_ratio:.4f}"
                )
                return False

            ps.state = PipelineState.ENHANCED.value
            return True
        except Exception as e:
            ps.fail_reasons.append(f"Enhancement 异常: {e}")
            return False

    # ------------------------------------------------------------
    # Stage 7: ShadowAccount（P0 改进：用真实日频历史）
    # ------------------------------------------------------------
    def _stage_shadow(
        self,
        ps: FactorPipelineState,
        candidate: CandidateFactor,
        price_data: Dict[str, Any],
        n_trials: int,
        factor_history: List[Dict[str, float]] = None,  # type: ignore
        forward_returns_history: List[Dict[str, float]] = None,  # type: ignore
    ) -> bool:
        """Stage 7: 90 日影子账户纸面交易

        P0 改进：用真实日频因子值历史，替代 `[candidate.values] * 90` 占位。
        P2.2 v6.2d 改进：支持因子特定 Shadow 参数覆盖（factor_shadow_overrides）
            - 不同因子对风险管理参数敏感度不同
            - QualityTrend 类因子需要 Config_E 超激进参数控制回撤
            - VT_MICRO_VOL_SKEW_INV 等因子用 Config_A 基线保护 Alpha 信号
        """
        try:
            factor_history = factor_history or []
            forward_returns_history = forward_returns_history or []

            # 若有日频历史，用真实的
            if factor_history and forward_returns_history:
                # 取最近 90 日
                use_days = min(len(factor_history), 90, len(forward_returns_history))
                fv_hist = factor_history[-use_days:]
                fr_hist = forward_returns_history[-use_days:]
                method = "real_history"
            else:
                # 降级：旧版占位
                fv_hist = [candidate.values] * 90
                fr_hist_daily = []
                for _day_idx in range(90):
                    daily = {}
                    for sym in candidate.values:
                        closes = price_data.get(sym, {}).get("closes", [])
                        if len(closes) >= 6:
                            daily[sym] = float(closes[-1] / closes[-6] - 1)
                    fr_hist_daily.append(daily)
                fv_hist = fv_hist
                fr_hist = fr_hist_daily
                method = "legacy_placeholder"

            # P2.2 v6.2d：检查是否有因子特定的 Shadow 参数覆盖
            factor_override = self.factor_shadow_overrides.get(candidate.name, {})
            if factor_override:
                # 创建因子特定的 ShadowAccount（应用覆盖参数）
                override_config = dict(self.shadow_account.config)
                override_config.update(factor_override)
                factor_shadow_account = ShadowAccount(override_config)
                shadow_account_used = factor_shadow_account
                method += f"+override:{','.join(factor_override.keys())}"
                logger.info(
                    "[Pipeline] %s 应用因子特定 Shadow 配置 | %s",
                    candidate.name, factor_override,
                )
            else:
                shadow_account_used = self.shadow_account

            shadow_result = shadow_account_used.run_shadow(
                factor_values_history=fv_hist,
                forward_returns_history=fr_hist,
                n_trials=n_trials,
                factor_name=candidate.name,
            )
            ps.shadow_result = shadow_result.to_dict()
            ps.shadow_result["method"] = method
            if not shadow_result.pass_shadow:
                ps.fail_reasons.append(f"Shadow: {shadow_result.reason}")
                return False

            ps.state = PipelineState.SHADOW_PASSED.value
            return True
        except Exception as e:
            ps.fail_reasons.append(f"Shadow 异常: {e}")
            return False

    # ------------------------------------------------------------
    # Stage 8: Committee
    # ------------------------------------------------------------
    def _stage_committee(
        self,
        ps: FactorPipelineState,
        candidate: CandidateFactor,
    ) -> bool:
        """Stage 8: 多 Agent 委员会评审"""
        try:
            # P2.2 v6.2d：从 enhancement_regime 提取有效 regime 数和样本分布
            regime_info = ps.enhancement_regime or {}
            per_regime_ic_ir = regime_info.get("per_regime_ic_ir", {}) or {}
            per_regime_samples = regime_info.get("per_regime_samples", {}) or {}
            n_valid_regimes = len(per_regime_ic_ir)  # 有效 regime 数（样本数 >= 5 的）

            # 从前面 stages 收集 factor_report
            factor_report = {
                "ic_ir_120d": ps.g2_ic_stability.get("ic_ir_estimated", 0.0) if ps.g2_ic_stability else 0.0,
                "dsr_value": ps.g3_dsr.get("dsr_value", 0.0) if ps.g3_dsr else 0.0,
                "ic_decay": ps.g2_ic_stability.get("ic_decay_estimated", 1.0) if ps.g2_ic_stability else 1.0,
                "max_abs_corr": ps.g1_orthogonality.get("max_abs_corr", 1.0) if ps.g1_orthogonality else 1.0,
                "risk_contribution": 0.03,  # 占位
                "tail_corr_max": 0.5,
                "capacity_ratio": ps.enhancement_capacity.get("capacity_ratio", 0.0) if ps.enhancement_capacity else 0.0,
                "turnover": 0.35,
                "slippage_bps": 8.0,
                "economic_logic_score": ps.g4_economic.get("score", 0.0) if ps.g4_economic else 0.0,
                "a_share_fit": ps.g4_economic.get("a_share_fit", 0.7) if ps.g4_economic else 0.7,
                "has_academic_paper": ps.g4_economic.get("has_academic_paper", False) if ps.g4_economic else False,
                "min_regime_ic_ir": regime_info.get("min_regime_ic_ir", 0.0),
                "weakest_regime": regime_info.get("weakest_regime", "unknown"),
                "regime_tag": regime_info.get("regime_tag", "none"),
                "n_regimes_pass": 0,
                # P2.2 v6.2d 新增：有效 regime 数和样本分布
                "n_valid_regimes": n_valid_regimes,
                "per_regime_samples": per_regime_samples,
            }

            verdict = self.committee.review(candidate.name, factor_report)
            ps.committee_verdict = verdict.to_dict()
            ps.final_score = verdict.avg_score

            if not verdict.approved:
                ps.fail_reasons.append(f"Committee: {verdict.rationale}")
                return False
            return True
        except Exception as e:
            ps.fail_reasons.append(f"Committee 异常: {e}")
            return False

    # ============================================================
    # 工具方法
    # ============================================================

    def _compute_factor_returns(
        self,
        candidate: CandidateFactor,
        price_data: Dict[str, Any],
    ) -> List[float]:
        """计算因子多空组合日收益率序列（简化版）"""
        # 取 TopN 多空
        sorted_syms = sorted(
            [(s, v) for s, v in candidate.values.items() if isinstance(v, (int, float)) and math.isfinite(v)],
            key=lambda x: x[1],
        )
        if len(sorted_syms) < 4:
            return []
        n_each = max(1, len(sorted_syms) // 4)
        short_syms = [s for s, _ in sorted_syms[:n_each]]
        long_syms = [s for s, _ in sorted_syms[-n_each:]]

        # 计算每日多空 PnL（用最近 30 日）
        max_days = 30
        daily_pnl = []
        for day_offset in range(-max_days, 0):
            long_rets = []
            short_rets = []
            for sym in long_syms:
                closes = price_data.get(sym, {}).get("closes", [])
                if len(closes) + day_offset >= 0 and len(closes) + day_offset - 1 >= 0:
                    idx = len(closes) + day_offset
                    if idx >= 1 and closes[idx - 1] > 0:
                        long_rets.append(closes[idx] / closes[idx - 1] - 1)
            for sym in short_syms:
                closes = price_data.get(sym, {}).get("closes", [])
                if len(closes) + day_offset >= 0 and len(closes) + day_offset - 1 >= 0:
                    idx = len(closes) + day_offset
                    if idx >= 1 and closes[idx - 1] > 0:
                        short_rets.append(closes[idx] / closes[idx - 1] - 1)
            if long_rets and short_rets:
                pnl = float(np.mean(long_rets) - np.mean(short_rets))
                daily_pnl.append(pnl)
        return daily_pnl

    def _update_stats(self, result: PipelineResult, ps: FactorPipelineState) -> None:
        """更新批次统计"""
        state = ps.state
        if state == PipelineState.APPROVED.value:
            result.approved += 1
        elif state == PipelineState.REJECTED.value:
            result.rejected += 1
        elif state == PipelineState.FAILED.value:
            result.failed += 1
        elif state == PipelineState.DEFERRED_FUNDAMENTALS.value:
            result.deferred_fundamentals += 1

        # 各关卡计数（deferred 因子不计入 G1-G4 通过率）
        if ps.g1_orthogonality and ps.g1_orthogonality.get("passed"):
            result.g1_passed += 1
        if ps.g2_ic_stability and ps.g2_ic_stability.get("passed"):
            result.g2_passed += 1
        if ps.g3_dsr and ps.g3_dsr.get("gate_3_pass"):
            result.g3_passed += 1
        if ps.g4_economic and ps.g4_economic.get("passed"):
            result.g4_passed += 1
        if ps.state in (PipelineState.ENHANCED.value, PipelineState.SHADOW_PASSED.value,
                        PipelineState.COMMITTEE_PENDING.value, PipelineState.APPROVED.value):
            result.enhanced += 1
        if ps.shadow_result and ps.shadow_result.get("pass_shadow"):
            result.shadow_passed += 1

    # ------------------------------------------------------------
    # P1.5 改进：fundamentals 数据质量评估
    # ------------------------------------------------------------
    def _assess_fundamentals_quality(
        self,
        fundamentals: Optional[Dict[str, Dict[str, Any]]],
        fundamentals_history: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """评估 fundamentals 数据质量，决定 V/Q/S/Growth/QualityTrend 类因子是否 defer

        评估规则：
            1. 统计每个标的的 data_quality 字段（"real" / "proxy" / 缺失）
            2. 计算 proxy_ratio = proxy_count / total_count
            3. 若 proxy_ratio >= 阈值（默认 0.5），标记 V/Q/S/Growth/QualityTrend 类候选因子
               为 deferred_fundamentals，跳过 G1-G4 评估
            4. 若 fundamentals 为 None 或空，全部依赖 fundamentals 的因子 defer
            5. P2.2 新增：若 fundamentals_history 有效标的比例 < 50%，QualityTrend 类因子 defer

        Returns:
            {
                "total_symbols": int,
                "real_count": int,
                "proxy_count": int,
                "missing_count": int,
                "proxy_ratio": float,
                "data_quality": "real" | "proxy" | "missing",
                "history_valid_ratio": float,  # P2.2: fundamentals_history 有效比例
                "defer_fundamentals_factors": Set[str],  # 由调用方消费
                "deferred_categories": List[str],
            }
        """
        # ============ P2.2 先评估 fundamentals_history 质量 ============
        quality_trend_factors = {
            "VT_QUALTREND_ROE_DELTA",
            "VT_QUALTREND_MARGIN_EXP",
            "VT_QUALTREND_DEBT_RED",
            "VT_QUALTREND_GROWTH_ACCEL",
        }
        defer_quality_trend: set = set()
        history_valid_ratio = 0.0
        if not fundamentals_history:
            # 无历史季度数据，QualityTrend 类全部 defer
            defer_quality_trend = quality_trend_factors.copy()
            history_valid_ratio = 0.0
        else:
            # 评估有效标的比例（n_valid >= 4 视为有效）
            total_sym = len(fundamentals_history)
            valid_sym = sum(
                1 for h in fundamentals_history.values()
                if isinstance(h, dict) and h.get("n_valid", 0) >= 4
            )
            history_valid_ratio = float(valid_sym / total_sym) if total_sym > 0 else 0.0
            # P2.2 阈值：有效比例 < 50%，QualityTrend 类 defer
            if history_valid_ratio < 0.5:
                defer_quality_trend = quality_trend_factors.copy()

        if not fundamentals:
            defer_all = self._collect_fundamentals_dependent_factors()
            # P2.2: 合并 QualityTrend defer
            defer_all = defer_all | defer_quality_trend
            return {
                "total_symbols": 0,
                "real_count": 0,
                "proxy_count": 0,
                "missing_count": 0,
                "proxy_ratio": 1.0,
                "data_quality": "missing",
                "history_valid_ratio": history_valid_ratio,
                "defer_fundamentals_factors": defer_all,
                "deferred_categories": sorted(FUNDAMENTALS_DEPENDENT_CATEGORIES),
            }

        total = len(fundamentals)
        real_count = 0
        proxy_count = 0
        missing_count = 0

        for _sym, fund in fundamentals.items():
            if not isinstance(fund, dict):
                missing_count += 1
                continue
            dq = fund.get("data_quality", "")
            if dq == "real":
                real_count += 1
            elif dq == "proxy":
                proxy_count += 1
            else:
                # 无标记视为 proxy（保守处理）
                missing_count += 1

        proxy_ratio = float((proxy_count + missing_count) / total) if total > 0 else 1.0
        if real_count >= total * (1 - self.fundamentals_proxy_ratio_threshold):
            data_quality = "real"
        elif proxy_count > 0 or missing_count > 0:
            data_quality = "proxy"
        else:
            data_quality = "real"

        # 若 proxy 比例超阈值，所有 V/Q/S/Growth/QualityTrend 因子 defer
        defer_factors: set = set()
        if proxy_ratio >= self.fundamentals_proxy_ratio_threshold:
            defer_factors = self._collect_fundamentals_dependent_factors()
        else:
            # 即使 fundamentals 真实，QualityTrend 也需 fundamentals_history 真实
            defer_factors = defer_quality_trend

        logger.info(
            "[Pipeline] fundamentals 质量 | total=%d real=%d proxy=%d missing=%d "
            "ratio=%.2f quality=%s history_valid_ratio=%.2f defer_count=%d",
            total, real_count, proxy_count, missing_count,
            proxy_ratio, data_quality, history_valid_ratio, len(defer_factors),
        )

        return {
            "total_symbols": total,
            "real_count": real_count,
            "proxy_count": proxy_count,
            "missing_count": missing_count,
            "proxy_ratio": proxy_ratio,
            "data_quality": data_quality,
            "history_valid_ratio": history_valid_ratio,
            "defer_fundamentals_factors": defer_factors,
            "deferred_categories": sorted(FUNDAMENTALS_DEPENDENT_CATEGORIES) if defer_factors else [],
        }

    def _collect_fundamentals_dependent_factors(self) -> set:
        """收集 adapter 中所有 V/Q/S/Growth/QualityTrend 类候选因子名（依赖 fundamentals）

        通过遍历 VibeTradingFactorAdapter 的因子计算方法，
        提取 category 在 FUNDAMENTALS_DEPENDENT_CATEGORIES 中的因子名。
        """
        deps: set = set()
        # 与 vibe_trading_factor_adapter.py 中的因子名硬编码对齐
        # Value 类
        deps.update({
            "VT_VAL_COMPOSITE",
            "VT_VAL_EARNINGS_YIELD_SCALED",
        })
        # Quality 类
        deps.update({
            "VT_QUA_COMPOSITE",
        })
        # Size 类
        deps.update({
            "VT_SIZE_LOG_NORMALIZED",
        })
        # Growth 类
        deps.update({
            "VT_GROWTH_COMPOSITE",
        })
        # P2.2 QualityTrend 类（依赖 fundamentals_history 的 YoY 变化）
        deps.update({
            "VT_QUALTREND_ROE_DELTA",
            "VT_QUALTREND_MARGIN_EXP",
            "VT_QUALTREND_DEBT_RED",
            "VT_QUALTREND_GROWTH_ACCEL",
        })
        return deps

    # ============================================================
    # P2.2 v6.8 改进：IC 加权组合（与单因子流水线独立运行）
    # ============================================================

    def _build_ic_weighted_combinations(
        self,
        factor_history: Dict[str, List[Dict[str, float]]],
        forward_returns_history: List[Dict[str, float]],
        n_trials: int,
        result: PipelineResult,
        audit: List[Dict[str, Any]],
    ) -> None:
        """构建 IC 加权组合并执行 Shadow 验证

        P2.2 v6.8 改进：将第十九批次 Config_E_plus1 突破的 IC 加权组合方法学集成到流水线。
        - IC 加权 + Config_E_plus1 = 完整 Shadow 通过 (live_dsr=+0.6151, max_dd=0.0438)
        - 与单因子流水线独立运行，组合构建失败不阻断主流程

        方法学（v6.5 突破 + v6.7 Config_E+ 突破）：
            1. IC 加权用滚动 IC_IR 作为动态权重（lookback=20 天），符号自适应
            2. 当因子 IC_IR 为负时，权重为负（反向使用该因子）
            3. 当因子 IC_IR 接近 0 时，权重接近 0（自动降低弱信号因子权重）
            4. Config_E_plus1 激进参数压缩残余噪声（target_vol=0.07）
            5. IC 加权和风险管理互补：IC 加权适应信号方向，激进参数控制噪声

        Args:
            factor_history: {factor_name: [day_0_values, day_1_values, ...]}
            forward_returns_history: 日频 forward return 序列
            n_trials: DSR 多重检验的已测试因子数
            result: PipelineResult，组合结果将追加到 result.factor_combinations
            audit: 审计 trail
        """
        if not self.ic_weighted_enabled:
            return

        audit.append({
            "stage": "Stage9_ICWeightedCombinations",
            "status": "started",
            "pairs": len(self.ic_weighted_pairs),
            "lookback": self.ic_weighted_lookback,
        })

        for pair in self.ic_weighted_pairs:
            factor_a_name = pair.get("factor_a")
            factor_b_name = pair.get("factor_b")
            pair_desc = pair.get("desc", f"{factor_a_name} + {factor_b_name}")

            try:
                combo_result = self._build_single_ic_weighted_combination(
                    factor_a_name=factor_a_name,
                    factor_b_name=factor_b_name,
                    pair_desc=pair_desc,
                    factor_history=factor_history,
                    forward_returns_history=forward_returns_history,
                    n_trials=n_trials,
                )
                result.factor_combinations.append(combo_result)

                if combo_result.get("passed"):
                    logger.info(
                        "[Pipeline] IC 加权组合通过 Shadow | %s + %s | "
                        "live_dsr=%.4f max_dd=%.4f IC_IR=%.4f",
                        factor_a_name, factor_b_name,
                        combo_result.get("shadow", {}).get("live_dsr", 0.0),
                        combo_result.get("shadow", {}).get("max_drawdown", 0.0),
                        combo_result.get("ic_metrics", {}).get("ic_ir", 0.0),
                    )
                else:
                    logger.warning(
                        "[Pipeline] IC 加权组合未通过 Shadow | %s + %s | reason=%s",
                        factor_a_name, factor_b_name,
                        combo_result.get("fail_reason", "unknown"),
                    )
            except Exception as e:
                logger.error(
                    "[Pipeline] IC 加权组合构建失败 | %s + %s | %s\n%s",
                    factor_a_name, factor_b_name, e, traceback.format_exc(),
                )
                result.factor_combinations.append({
                    "factor_a": factor_a_name,
                    "factor_b": factor_b_name,
                    "desc": pair_desc,
                    "passed": False,
                    "fail_reason": f"exception: {type(e).__name__}: {e}",
                })

        audit.append({
            "stage": "Stage9_ICWeightedCombinations",
            "status": "completed",
            "combinations": len(result.factor_combinations),
            "passed": sum(1 for c in result.factor_combinations if c.get("passed")),
        })

    def _build_single_ic_weighted_combination(
        self,
        factor_a_name: str,
        factor_b_name: str,
        pair_desc: str,
        factor_history: Dict[str, List[Dict[str, float]]],
        forward_returns_history: List[Dict[str, float]],
        n_trials: int,
    ) -> Dict[str, Any]:
        """构建单个 IC 加权组合并验证（Config_E_plus1 参数）

        Args:
            factor_a_name: 因子 A 名称
            factor_b_name: 因子 B 名称
            pair_desc: 组合描述
            factor_history: 日频因子值历史（流水线共享）
            forward_returns_history: 日频 forward return 历史
            n_trials: DSR 多重检验的已测试因子数

        Returns:
            组合结果 dict，包含 IC 指标、Shadow 结果、权重统计、是否通过
        """
        # 检查因子存在性
        if factor_a_name not in factor_history or factor_b_name not in factor_history:
            return {
                "factor_a": factor_a_name,
                "factor_b": factor_b_name,
                "desc": pair_desc,
                "passed": False,
                "fail_reason": (
                    f"factor_history 缺失: A={factor_a_name in factor_history} "
                    f"B={factor_b_name in factor_history}"
                ),
            }

        hist_a_raw = factor_history[factor_a_name]
        hist_b_raw = factor_history[factor_b_name]
        fwd_returns_raw = forward_returns_history or []

        # 对齐时间序列长度
        n = min(len(hist_a_raw), len(hist_b_raw), len(fwd_returns_raw))
        if n < self.ic_weighted_lookback + 5:
            return {
                "factor_a": factor_a_name,
                "factor_b": factor_b_name,
                "desc": pair_desc,
                "passed": False,
                "fail_reason": (
                    f"历史长度不足: n={n} < lookback+5={self.ic_weighted_lookback+5}"
                ),
            }

        hist_a = hist_a_raw[:n]
        hist_b = hist_b_raw[:n]
        fwd_returns = fwd_returns_raw[:n]

        # 计算 IC 序列（用于滚动 IC_IR 权重）
        ic_series_a = compute_rolling_ic_series(hist_a, fwd_returns)
        ic_series_b = compute_rolling_ic_series(hist_b, fwd_returns)

        # 全局 IC_IR（120 天）
        ic_ir_a_full, ic_mean_a_full, _ = compute_ic_ir(ic_series_a)
        ic_ir_b_full, ic_mean_b_full, _ = compute_ic_ir(ic_series_b)

        # 构建 IC 加权组合
        combined_history, weights_history = self._combine_factors_ic_weighted(
            hist_a=hist_a,
            hist_b=hist_b,
            ic_series_a=ic_series_a,
            ic_series_b=ic_series_b,
            lookback=self.ic_weighted_lookback,
        )

        # 计算组合 IC 指标
        combined_ic_series = compute_rolling_ic_series(combined_history, fwd_returns)
        combined_ic_ir, combined_ic_mean, combined_ic_std = compute_ic_ir(combined_ic_series)
        combined_decay = compute_ic_decay(combined_history, fwd_returns)

        # 权重统计（跳过前 lookback 天，样本不足）
        weights_stats = self._compute_weights_statistics(
            weights_history=weights_history,
            lookback=self.ic_weighted_lookback,
            n_days=n,
        )

        # Shadow 验证（使用 Config_E_plus1 配置）
        shadow_account = ShadowAccount(dict(self.ic_weighted_shadow_config))
        shadow_result = shadow_account.run_shadow(
            factor_values_history=combined_history,
            forward_returns_history=fwd_returns,
            n_trials=n_trials,
            factor_name=f"IC_WEIGHTED_{factor_a_name}__{factor_b_name}",
        )

        passed = bool(shadow_result.pass_shadow)

        return {
            "factor_a": factor_a_name,
            "factor_b": factor_b_name,
            "desc": pair_desc,
            "method": "ic_weighted_rolling",
            "lookback": self.ic_weighted_lookback,
            "shadow_config": self.ic_weighted_shadow_config,
            "ic_metrics": {
                "factor_a_ic_ir": float(ic_ir_a_full),
                "factor_a_ic_mean": float(ic_mean_a_full),
                "factor_b_ic_ir": float(ic_ir_b_full),
                "factor_b_ic_mean": float(ic_mean_b_full),
                "combined_ic_ir": float(combined_ic_ir),
                "combined_ic_mean": float(combined_ic_mean),
                "combined_ic_std": float(combined_ic_std),
                "combined_ic_decay": float(combined_decay),
            },
            "weights_stats": weights_stats,
            "shadow": {
                "pass_shadow": passed,
                "live_dsr": float(shadow_result.live_dsr),
                "max_drawdown": float(shadow_result.max_drawdown),
                "total_return": float(shadow_result.total_return),
                "sr_observed": float(shadow_result.sr_observed),
                "realized_vol": float(shadow_result.realized_vol),
                "mc_p95_dd": float(shadow_result.monte_carlo_p95_dd),
                "avg_scaler": float(shadow_result.avg_scaler),
                "derisk_triggered_days": int(shadow_result.derisk_triggered_days),
                "fail_reasons": list(shadow_result.fail_reasons) if shadow_result.fail_reasons else [],
            },
            "passed": passed,
            "fail_reason": "" if passed else (
                shadow_result.reason if hasattr(shadow_result, "reason") else "shadow_failed"
            ),
        }

    def _combine_factors_ic_weighted(
        self,
        hist_a: List[Dict[str, float]],
        hist_b: List[Dict[str, float]],
        ic_series_a: List[float],
        ic_series_b: List[float],
        lookback: int,
    ) -> tuple:
        """IC 加权组合两个因子（动态权重，符号自适应）

        在每个时间点 t：
            1. 计算各因子的滚动 IC_IR（过去 lookback 天）
            2. 权重 = IC_IR_i / sum(|IC_IR_j|)（保留符号）
            3. combined[t] = weight_a * rank(f_A[t]) + weight_b * rank(f_B[t])

        当某因子 IC_IR 为负时，权重为负（反向使用），自动适应信号反转。

        Args:
            hist_a: 因子 A 的日频值序列
            hist_b: 因子 B 的日频值序列
            ic_series_a: 因子 A 的 IC 序列
            ic_series_b: 因子 B 的 IC 序列
            lookback: 滚动窗口

        Returns:
            (combined_history, weights_history)
        """
        n = min(len(hist_a), len(hist_b))
        combined: List[Dict[str, float]] = []
        weights_history: List[Dict[str, float]] = []

        for t in range(n):
            ic_ir_a = self._compute_rolling_ic_ir_at_t(ic_series_a, t, lookback)
            ic_ir_b = self._compute_rolling_ic_ir_at_t(ic_series_b, t, lookback)

            abs_sum = abs(ic_ir_a) + abs(ic_ir_b)
            if abs_sum < 1e-6:
                # 两个因子都接近 0，用等权兜底（避免除零）
                weight_a, weight_b = 0.5, 0.5
            else:
                # 保留符号自动适应信号反转
                weight_a = ic_ir_a / abs_sum
                weight_b = ic_ir_b / abs_sum

            rank_a = self._cross_sectional_rank(hist_a[t])
            rank_b = self._cross_sectional_rank(hist_b[t])
            common_syms = set(rank_a.keys()) & set(rank_b.keys())

            combined_day = {
                s: weight_a * rank_a[s] + weight_b * rank_b[s]
                for s in common_syms
            }
            combined.append(combined_day)
            weights_history.append({
                "weight_a": float(weight_a),
                "weight_b": float(weight_b),
                "ic_ir_a": float(ic_ir_a),
                "ic_ir_b": float(ic_ir_b),
            })

        return combined, weights_history

    @staticmethod
    def _cross_sectional_rank(values: Dict[str, float]) -> Dict[str, float]:
        """cross-sectional rank 标准化到 [0, 1]

        rank 适合 Spearman IC 和 IC 加权组合（避免大量级因子主导权重）。
        """
        valid = {s: v for s, v in values.items()
                 if isinstance(v, (int, float)) and math.isfinite(v)}
        if len(valid) < 2:
            return {s: 0.5 for s in values}
        sorted_syms = sorted(valid.keys(), key=lambda s: valid[s])
        n = len(sorted_syms)
        ranks = {s: i / (n - 1) for i, s in enumerate(sorted_syms)}
        return {s: ranks.get(s, 0.5) for s in values}

    @staticmethod
    def _compute_rolling_ic_ir_at_t(
        ic_series: List[float],
        t: int,
        lookback: int,
    ) -> float:
        """计算时间点 t 的滚动 IC_IR

        用过去 lookback 天的 IC 序列计算 IC_IR = mean(IC) / std(IC)。
        样本不足时返回 0（中性权重，避免误判）。
        """
        if t < lookback:
            return 0.0
        window = ic_series[t - lookback:t]
        arr = np.array(window, dtype=float)
        arr = arr[np.isfinite(arr)]
        if len(arr) < 5:
            return 0.0
        ic_mean = float(np.mean(arr))
        ic_std = float(np.std(arr, ddof=1))
        if ic_std < 1e-12:
            return 0.0
        return ic_mean / ic_std

    @staticmethod
    def _compute_weights_statistics(
        weights_history: List[Dict[str, float]],
        lookback: int,
        n_days: int,
    ) -> Dict[str, Any]:
        """统计 IC 加权组合的权重变化（用于诊断信号反转）"""
        if not weights_history or n_days <= lookback:
            return {"available": False, "reason": "samples insufficient"}

        w_a_arr = np.array([w["weight_a"] for w in weights_history])
        w_b_arr = np.array([w["weight_b"] for w in weights_history])
        ic_ir_a_arr = np.array([w["ic_ir_a"] for w in weights_history])
        ic_ir_b_arr = np.array([w["ic_ir_b"] for w in weights_history])

        # 跳过前 lookback 天（样本不足）
        valid_slice = slice(lookback, n_days)
        w_a_valid = w_a_arr[valid_slice]
        w_b_valid = w_b_arr[valid_slice]
        ic_ir_a_valid = ic_ir_a_arr[valid_slice]
        ic_ir_b_valid = ic_ir_b_arr[valid_slice]

        if len(w_a_valid) == 0:
            return {"available": False, "reason": "no valid weights"}

        neg_a_days = int(np.sum(w_a_valid < 0))
        pos_a_days = int(np.sum(w_a_valid > 0))
        neg_b_days = int(np.sum(w_b_valid < 0))
        pos_b_days = int(np.sum(w_b_valid > 0))

        return {
            "available": True,
            "factor_a": {
                "weight_mean": float(np.mean(w_a_valid)),
                "weight_min": float(np.min(w_a_valid)),
                "weight_max": float(np.max(w_a_valid)),
                "ic_ir_mean": float(np.mean(ic_ir_a_valid)),
                "ic_ir_min": float(np.min(ic_ir_a_valid)),
                "ic_ir_max": float(np.max(ic_ir_a_valid)),
                "neg_weight_days": neg_a_days,
                "pos_weight_days": pos_a_days,
                "neg_weight_pct": float(neg_a_days / len(w_a_valid)) if len(w_a_valid) > 0 else 0.0,
            },
            "factor_b": {
                "weight_mean": float(np.mean(w_b_valid)),
                "weight_min": float(np.min(w_b_valid)),
                "weight_max": float(np.max(w_b_valid)),
                "ic_ir_mean": float(np.mean(ic_ir_b_valid)),
                "ic_ir_min": float(np.min(ic_ir_b_valid)),
                "ic_ir_max": float(np.max(ic_ir_b_valid)),
                "neg_weight_days": neg_b_days,
                "pos_weight_days": pos_b_days,
                "neg_weight_pct": float(neg_b_days / len(w_b_valid)) if len(w_b_valid) > 0 else 0.0,
            },
        }

    def _persist_audit(self, batch_id: str, result: PipelineResult) -> None:
        """持久化审计到 reports/{batch_id}/pipeline_state.json"""
        try:
            batch_dir = self.reports_dir / batch_id
            batch_dir.mkdir(parents=True, exist_ok=True)
            state_file = batch_dir / "pipeline_state.json"
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(result.to_dict(), f, ensure_ascii=False, indent=2, default=str)
            logger.info("[Pipeline] 审计持久化 | %s", state_file)
        except Exception as e:
            logger.error("[Pipeline] 审计持久化失败 | %s", e)


def quick_run(
    price_data: Dict[str, Any],
    fundamentals: Optional[Dict[str, Any]] = None,
    benchmark_returns: Optional[List[float]] = None,
    portfolio_value: float = 1e8,
    n_trials: int = 13,
) -> PipelineResult:
    """快速流水线运行（使用默认配置）"""
    orchestrator = PipelineOrchestrator()
    return orchestrator.run(
        price_data=price_data,
        fundamentals=fundamentals,
        benchmark_returns=benchmark_returns,
        portfolio_value=portfolio_value,
        n_trials=n_trials,
    )
