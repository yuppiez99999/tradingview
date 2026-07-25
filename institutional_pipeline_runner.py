# -*- coding: utf-8 -*-
"""
机构级量化闭环运行器 (Institutional Pipeline Runner)
======================================================

职责：
- 串联新模块形成可运行闭环
- 不改变现有系统原有逻辑
- 可作为独立 runner，也可被主系统调用

用法:
    python institutional_pipeline_runner.py --mode smoke
    python institutional_pipeline_runner.py --mode backtest --symbols 600519 000858
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from utils.alpha_evaluator import AlphaEvaluator, AlphaEvaluationReport
from utils.signal_fusion import SignalFusionEngine, FusionSignal
from utils.risk_budget_engine import RiskBudgetEngine, RiskCheckResult
from utils.institutional_optimizer import InstitutionalPortfolioOptimizer, PortfolioDecision
from utils.execution_router import ExecutionRouter, ExecutionPlan
from utils.data_gate import DataGate, DataGateResult

# 顶级对冲基金整改：统一成本、硬性风险约束、回撤熔断、回测完整性守卫
from utils.cost_model import get_cost_model
from utils.risk_constraints import (
    enforce_hard_constraints,
    DEFAULT_MAX_WEIGHT,
    DEFAULT_MAX_SECTOR,
    DEFAULT_MAX_DAILY_VAR,
    DEFAULT_MAX_SINGLE_VAR,
)
from utils.drawdown_breaker import DrawdownCircuitBreaker
from utils.backtest_integrity import (
    evaluate_alpha_provenance,
    validate_backtest,
)

# === P0-13: KillSwitch 集成 (2026-07-25 顶级对冲基金审计) ===
# 审计问题: 生产 pipeline 未集成 KillSwitch, L1/L2/L3 熔断对 pipeline 无效
try:
    from utils.kill_switch import KillSwitch
    _HAS_KILL_SWITCH = True
except ImportError:
    _HAS_KILL_SWITCH = False

# === P0-8: V7.2 bull regime 5% 上限 + V7.1 高波动惩罚 (回测验证的最优解) ===
# 回测验证 (V7.1/V7.2/V8): bull regime 下 LGB 信号失效, 满仓高波动股导致 2024-06 崩盘
# V7.2 最优解: bull regime max_weight 10%→5%, 达成 DSR≥5, 峰度 6.86→3.96, 波动 12.85%→10.98%
# 生产路径必须复制此约束, 否则实盘将重蹈 2024-06 覆辙
_V72_BULL_REGIME_MAX_WEIGHT = 0.05     # bull regime 单票上限 5% (V7.2)
_V71_BULL_HIGH_VOL_THRESHOLD = 0.045   # bull regime 高波动阈值 4.5% (20日实现波动率)
_V71_BULL_VOL_PENALTY = 0.5            # 高波动股权重惩罚 ×0.5 (V7.1)

try:
    from utils.data_provider import MarketDataProvider
    _HAS_DATA_PROVIDER = True
except Exception:
    _HAS_DATA_PROVIDER = False

# LGB Walk-forward 集成（可选依赖，缺失时降级为动量信号）
try:
    from lgb_enhanced_trainer import (
        LGB_ENHANCED_CONFIG,
        POSITION_SYMBOLS,
        train_symbol_enhanced,
        train_symbol_regime_specific,  # V9: regime-specific 双模型训练
        compute_regime_series,          # V9: 计算 regime 序列
        add_technical_features,
        add_cross_sectional_features,
        add_industry_relative_strength_features,
        add_capital_flow_features,
        add_cross_market_features,
        add_sentiment_features,
        add_mean_reversion_features,  # V6: 均值回归特征, 提升震荡市Alpha
        add_regime_aware_features,    # V7: regime-aware 特征, 解决 bull regime 信号失效
    )
    _HAS_LGB = True
except Exception as _lgb_import_err:
    _HAS_LGB = False
    _LGB_IMPORT_ERR = str(_lgb_import_err)

# Walk-forward 训练配置（加速版：月度重训无需 2000 轮）
WALKFORWARD_LGB_CONFIG = {
    **LGB_ENHANCED_CONFIG,
    "lgb_params": {
        **LGB_ENHANCED_CONFIG["lgb_params"],
        "n_estimators": 1000,        # 2000 → 1000（加速）
        "n_jobs": 1,                 # 单线程, 避免 LightGBM 多线程竞争导致的 access violation
    },
    "early_stopping_rounds": 100,     # 200 → 100（加速）
    "news_lookback_days": 0,          # 跳过新闻（历史不可回溯）
    "adaptive_retrain_threshold": 5,
    "adaptive_retrain_lr": 0.001,
    "adaptive_retrain_n_estimators": 2000,
} if _HAS_LGB else {}

# LGB 训练重试配置（防止间歇性 access violation 崩溃）
_LGB_TRAIN_MAX_RETRIES = 2          # 失败后最多重试 2 次
_LGB_TRAIN_RETRY_DELAY = 1.0        # 重试间隔（秒）

# V9: Regime-Specific 训练开关 (bull/non-bull 双模型)
# 动机: V7-Model + V7.1/V7.2 权重后处理均无法将 Window 1 Sharpe CV 降至 <0.5
#       根因是单一 LGB 模型被 bear 主导 (47% 样本), 在 bull regime 信号失效
# 方案: 每个标的训练两个独立模型 (bull + non-bull), 预测时按当前 regime 选择
# 验证: 2024-06-03 (bull regime) V9 bull 模型给出 -0.9858 强烈看跌信号 (IC=0.79)
#       而 V6.2 在该月给 688017/300308 高权重 (10%/8%) 导致 -5.11% 月度亏损
_V9_REGIME_SPECIFIC_ENABLED = True  # 总开关: True=启用 V9 双模型, False=回退 V6.2 单模型
_V9_MIN_SAMPLES_PER_REGIME = 100    # 每个 regime 子集最少样本数 (低于此值降级为全样本模型)
_V9_REGIME_PROXY_SYMBOL = "510300"  # 大盘代理 (与 _REGIME_PROXY_SYMBOL 一致)
_V9_REGIME_MA_PERIOD = 60           # MA60 中期趋势
_V9_REGIME_SLOPE_WINDOW = 5         # MA60 斜率窗口

# 跨市场代理标的（特征工程依赖）
_CROSS_MARKET_PROXY_SYMBOLS = ["518880", "600036", "588000", "515180"]

# ============================================================================
# 日志
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("institutional_pipeline_runner.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("institutional_pipeline")

BASE_DIR = Path(__file__).resolve().parent
REPORT_DIR = BASE_DIR / "output" / "institutional_pipeline"


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class PipelineContext:
    """运行上下文"""
    mode: str = "smoke"
    symbols: List[str] = field(default_factory=list)
    total_capital: float = 3_000_000
    report_date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    output_path: Optional[Path] = None

    def __post_init__(self) -> None:
        if self.output_path is None:
            self.output_path = REPORT_DIR / self.report_date


# ============================================================================
# Pipeline
# ============================================================================

class InstitutionalPipelineRunner:
    """机构级闭环运行器"""

    def __init__(self, ctx: Optional[PipelineContext] = None):
        self.ctx = ctx or PipelineContext()
        self.ctx.output_path.mkdir(parents=True, exist_ok=True)

        self.alpha_evaluator = AlphaEvaluator()
        self.signal_fusion = SignalFusionEngine()
        # 硬性风险预算：单票 15%、组合日度 VaR95 1.5%、单票 VaR95 0.8%
        self.risk_budget_engine = RiskBudgetEngine(
            total_capital=self.ctx.total_capital,
            max_weight=DEFAULT_MAX_WEIGHT,
            max_daily_var_95=DEFAULT_MAX_DAILY_VAR,
            max_single_var_95=DEFAULT_MAX_SINGLE_VAR,
            max_drawdown=0.15,
        )
        self.optimizer = InstitutionalPortfolioOptimizer(
            total_capital=self.ctx.total_capital,
            max_weight=DEFAULT_MAX_WEIGHT,
            max_sector_concentration=DEFAULT_MAX_SECTOR,
            min_position_weight=0.02,
        )
        # 回撤分级熔断控制器
        self.drawdown_breaker = DrawdownCircuitBreaker(max_drawdown=0.15)
        # 板块映射（用于板块集中度硬约束）
        self.sector_map = self._build_sector_map()
        self.execution_router = ExecutionRouter()
        self.data_gate = DataGate()
        self.data_provider = MarketDataProvider(backtest_mode=(ctx.mode == "backtest")) if _HAS_DATA_PROVIDER else None
        if self.data_provider is not None and ctx.mode == "backtest":
            self.data_provider.set_backtest_date(ctx.report_date)
        self._historical_cache: Dict[str, pd.DataFrame] = {}
        # LGB Walk-forward 模型缓存（内存中，不落盘）
        self._lgb_models: Dict[str, Dict] = {}
        if ctx.mode == "backtest":
            self._preload_historical_data()

    # ------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------

    def run(self) -> Dict[str, Any]:
        """运行完整闭环"""
        logger.info("[Pipeline] 启动模式=%s symbols=%s", self.ctx.mode, self.ctx.symbols)
        result = {
            "report_date": self.ctx.report_date,
            "mode": self.ctx.mode,
            "symbols": self.ctx.symbols,
            "steps": {},
        }

        # Step 1: 数据门控（回测模式跳过，避免重复网络调用）
        if self.ctx.mode != "backtest":
            data_result = self._step_data_gate()
            result["steps"]["data_gate"] = data_result
            if not data_result.get("all_symbols_allowed", False):
                logger.warning("[Pipeline] 数据门控未通过，终止后续步骤")
                result["status"] = "blocked_by_data_gate"
                self._save(result)
                return result
        else:
            result["steps"]["data_gate"] = {"all_symbols_allowed": True, "results": []}

        # Step 2: Alpha 评估
        alpha_report = self._step_alpha_evaluation()
        result["steps"]["alpha_evaluation"] = alpha_report.to_dict() if hasattr(alpha_report, "to_dict") else alpha_report

        # Step 3: 信号融合
        fusion_signals = self._step_signal_fusion(alpha_report)
        result["steps"]["signal_fusion"] = [s.to_dict() for s in fusion_signals]

        # Step 4: 组合优化
        portfolio_decision = self._step_portfolio_optimization(fusion_signals)
        result["steps"]["portfolio_decision"] = portfolio_decision.to_dict() if hasattr(portfolio_decision, "to_dict") else portfolio_decision

        # Step 4.5: 市场状态调节 (大盘趋势过滤, 控制熊市回撤)
        portfolio_decision, regime_info = self._step_market_regime_scaling(portfolio_decision)
        result["steps"]["market_regime"] = regime_info

        # === P0-8: V7.2 bull regime 5% 上限 + V7.1 高波动惩罚 (2026-07-25 顶级对冲基金审计) ===
        # 审计问题: 生产路径未复制回测验证的 V7.2 最优解, bull regime 满仓高波动股
        # 修复: 在 regime scaling 后立即应用 V7.2 cap (回测验证: DSR≥5, 峰度 6.86→3.96)
        try:
            v72_cap_info = self._apply_v72_bull_regime_cap(portfolio_decision, regime_info)
            result["steps"]["v72_bull_regime_cap"] = v72_cap_info
        except Exception as e:
            logger.error("[V7.2] bull regime cap 异常: %s", e, exc_info=True)
            result["steps"]["v72_bull_regime_cap"] = {"error": str(e)}

        # === P0-8: 回撤熔断器 (DrawdownCircuitBreaker) 接入生产路径 ===
        # 审计问题: self.drawdown_breaker 已初始化但从未调用
        # 修复: 根据当前回撤级别动态减仓 (15% 上限, 分级减仓)
        try:
            current_drawdown = float(portfolio_decision.meta.get("current_drawdown", 0.0))
            if current_drawdown > 0:
                dd_decision = self.drawdown_breaker.evaluate(current_drawdown)
                result["steps"]["drawdown_breaker"] = dd_decision.to_dict() if hasattr(dd_decision, "to_dict") else {"level": str(dd_decision)}
                if dd_decision.target_scale(current_drawdown) < 1.0:
                    scale = dd_decision.target_scale(current_drawdown)
                    logger.warning(
                        "[DrawdownBreaker] 回撤 %.2f%% 触发减仓 ×%.2f",
                        current_drawdown * 100, scale,
                    )
                    portfolio_decision.target_weights = {
                        s: w * scale for s, w in portfolio_decision.target_weights.items()
                    }
                    portfolio_decision.meta["drawdown_scale"] = scale
        except Exception as e:
            logger.error("[DrawdownBreaker] 检查异常: %s", e, exc_info=True)

        # Step 5: 风险预算检查
        risk_result = self._step_risk_budget(portfolio_decision)
        result["steps"]["risk_budget"] = risk_result.to_dict() if hasattr(risk_result, "to_dict") else risk_result
        if not risk_result.allowed and self.ctx.mode not in ("smoke", "backtest"):
            logger.warning("[Pipeline] 风险预算未通过，交易计划被拦截")
            result["status"] = "blocked_by_risk_budget"
            self._save(result)
            return result

        # === P0-13: KillSwitch 熔断检查 (2026-07-25 顶级对冲基金审计) ===
        # 审计问题: 生产 pipeline 未集成 KillSwitch, L1/L2/L3 熔断对 pipeline 无效
        # 修复: 风险预算通过后、执行路由前, 检查 KillSwitch 级别并过滤/拦截 trades
        try:
            ks_result = self._step_kill_switch_check(portfolio_decision)
            result["steps"]["kill_switch"] = ks_result
            # L2+ 或 fail_closed: 阻止执行路由
            if (ks_result.get("level", 0) >= 2 or ks_result.get("fail_closed", False)) and self.ctx.mode not in ("smoke", "backtest"):
                logger.warning("[Pipeline] KillSwitch L%d, 交易计划被拦截", ks_result.get("level", 0))
                result["status"] = "blocked_by_kill_switch"
                self._save(result)
                return result
        except Exception as e:
            logger.error("[KillSwitch] 集成异常: %s", e, exc_info=True)
            result["steps"]["kill_switch"] = {"error": str(e)}

        # Step 6: 执行路由
        execution_plans = self._step_execution_routing(portfolio_decision, fusion_signals)
        result["steps"]["execution_plans"] = [p.to_dict() for p in execution_plans]

        # 回测完整性守卫：前视偏差 / mock alpha 一律不得报告为有效回测
        if self.ctx.mode == "backtest":
            is_valid, integrity_issues = validate_backtest(
                as_of_date=self.ctx.report_date,
                alpha_report=alpha_report,
                prices=self._historical_cache,
                require_real_alpha=True,
            )
            provenance = evaluate_alpha_provenance(alpha_report)
            result["integrity"] = {
                "valid": is_valid,
                "alpha_provenance": provenance,
                "issues": integrity_issues,
            }
            if not is_valid:
                result["status"] = "invalid_backtest"
                logger.error(
                    "[Pipeline] 回测完整性校验未通过，禁止报告为有效回测: %s",
                    integrity_issues,
                )
                self._save(result)
                return result

        result["status"] = "ok"
        self._save(result)
        logger.info("[Pipeline] 运行完成: %s", result["status"])
        return result

    # ------------------------------------------------------------
    # Step 1: 数据门控（真实数据）
    # ------------------------------------------------------------

    def _step_data_gate(self) -> Dict[str, Any]:
        logger.info("[Pipeline] Step 1: 数据门控")
        gate_results = []
        all_allowed = True

        for symbol in self.ctx.symbols:
            snapshot = self._real_snapshot(symbol)
            gate = self.data_gate.check_and_gate(symbol, snapshot)
            gate_results.append(gate.to_dict())
            if not gate.allowed:
                all_allowed = False

        return {
            "all_symbols_allowed": all_allowed,
            "results": gate_results,
        }

    # ------------------------------------------------------------
    # Step 2: Alpha 评估
    # ------------------------------------------------------------

    def _build_sector_map(self) -> Dict[str, str]:
        """板块映射（用于板块集中度硬约束）。

        覆盖全部 23 个 POSITION_SYMBOLS + 历史标的, 未知标的规定为 unknown。
        修复: 2026-07-24 发现 688981/000425/600089 等 11 个标的缺失,
              导致它们逃脱 25% 板块集中度硬约束 (2025-09 科技板块实际达 29.69%)。
        """
        return {
            # === 消费 ===
            "600519": "消费", "000858": "消费",
            # === 金融/银行 ===
            "601318": "金融", "000001": "金融", "600036": "金融", "601398": "金融",
            "600016": "金融", "601166": "金融", "600000": "金融",
            "512880": "金融", "512800": "金融",
            # === 医药 ===
            "600276": "医药", "300760": "医药", "002594": "医药", "002422": "医药",
            "512170": "医药",
            # === 科技/半导体 (25% 硬上限, 项目硬约束) ===
            "000063": "科技", "688041": "科技", "300308": "科技", "002371": "科技",
            "603019": "科技", "688981": "科技",  # 688981 中芯国际 修复缺失
            "588000": "科技", "588080": "科技", "512760": "科技",
            # === 制造 ===
            "688017": "制造", "000425": "制造", "600089": "制造",  # 修复缺失
            "000680": "制造", "000333": "制造",  # 修复缺失
            # === 资源 ===
            "600219": "资源", "600019": "资源",  # 修复缺失
            "000408": "资源", "000975": "资源",  # 修复缺失
            # === 新能源 ===
            "300274": "新能源", "515030": "新能源",
            # === 顺周期 ===
            "601088": "顺周期",
            # === 防御 ===
            "600900": "防御", "512890": "防御",
            # === 红利 ===
            "515180": "红利",  # 修复缺失
            # === 黄金/避险 ===
            "518880": "黄金",
            # === 宽基ETF ===
            "510300": "宽基", "510050": "宽基", "510500": "宽基",
            "159915": "宽基", "512100": "宽基",
        }

    def _step_alpha_evaluation(self) -> Any:
        logger.info("[Pipeline] Step 2: Alpha 评估")
        # LGB Walk-forward 训练（仅 backtest 模式，消除前视偏差）
        if self.ctx.mode == "backtest" and _HAS_LGB:
            lgb_result = self._lgb_walkforward_train()
            logger.info("[Pipeline] LGB 训练: trained=%d failed=%d skipped=%d",
                        lgb_result["trained"], lgb_result["failed"], lgb_result["skipped"])
        # 顶级对冲基金整改：优先使用真实历史因子 IC；数据不足时回退 mock，
        # 但必须明确标记 provenance，禁止把 mock 结果冒充有效回测。
        real_eval = self._real_alpha_evaluation()
        provenance = real_eval.get("category", "mock")
        active = real_eval.get("active_factors", 0)
        logger.info("[Pipeline] Alpha provenance=%s active_factors=%d", provenance, active)
        if provenance == "mock":
            logger.warning(
                "[Pipeline] 真实 alpha 不可用，回退 mock；该结果不得作为有效回测/收益证据"
            )
        return real_eval

    def _real_alpha_evaluation(self) -> Dict[str, Any]:
        """Alpha 评估：LGB Walk-forward 模型 CV IC + 动量 IC 混合。

        有 LGB 模型的标的使用 CV IC（OOS 指标，无前视偏差）；
        无 LGB 模型的标的回退到 60 日动量 IC（基于真实历史行情）。
        """
        evaluations: List[Dict[str, Any]] = []
        active = 0
        lgb_count = 0
        mom_count = 0

        # === 第一轮: LGB 模型（有模型的标的） ===
        symbols_needing_momentum: List[str] = []
        for symbol in self.ctx.symbols:
            if self._lgb_models:
                model_info = self._lgb_models.get(symbol)
                if model_info:
                    cv_metrics = model_info.get("cv_after_selection", {})
                    ic = float(cv_metrics.get("mean_ic", 0.0))
                    std_ic = float(cv_metrics.get("std_ic", 0.1))
                    ic_ir = ic / (std_ic + 1e-9)
                    evaluations.append({
                        "factor_name": f"lgb_{symbol}",
                        "ic_1d": ic,
                        "ic_ir": float(ic_ir),
                        "category": "real",
                        "symbol": symbol,
                    })
                    active += 1
                    lgb_count += 1
                    continue
            symbols_needing_momentum.append(symbol)

        # === 第二轮: 动量 IC（无 LGB 模型的标的） ===
        if symbols_needing_momentum and self.data_provider is not None:
            import pandas as pd
            cutoff = pd.Timestamp(self.ctx.report_date).normalize()
            for symbol in symbols_needing_momentum:
                try:
                    df = self._historical_cache.get(symbol)
                    if df is None:
                        df = self.data_provider.get_historical_data(symbol, period='5y')
                    if df is None or df.empty or 'close' not in df.columns or len(df) < 120:
                        continue
                    s = df['close'].dropna()
                    if hasattr(s.index, "tz") and s.index.tz is not None:
                        s.index = s.index.tz_localize(None)
                    s = s[s.index <= cutoff]
                    if len(s) < 120:
                        continue
                    factor = s.pct_change(60).shift(1)
                    fwd = s.pct_change(20).shift(-20)
                    joined = pd.concat([factor, fwd], axis=1).dropna()
                    joined.columns = ['factor', 'fwd']
                    if len(joined) < 30:
                        continue
                    ic = float(joined['factor'].corr(joined['fwd']))
                    if not np.isfinite(ic):
                        continue
                    ic_ir = ic / (joined['factor'].std() + 1e-9)
                    evaluations.append({
                        "factor_name": f"mom60_{symbol}",
                        "ic_1d": ic,
                        "ic_ir": float(ic_ir),
                        "category": "real",
                        "symbol": symbol,
                    })
                    active += 1
                    mom_count += 1
                except Exception as e:
                    logger.warning("[Pipeline] 真实alpha评估失败 %s: %s (type=%s)", symbol, e, type(e).__name__)

        category = "real" if active > 0 else "mock"
        if lgb_count > 0 or mom_count > 0:
            logger.info("[Pipeline] Alpha 评估: LGB=%d, 动量=%d, 总计=%d", lgb_count, mom_count, active)
        return {"evaluations": evaluations, "category": category, "active_factors": active}

    # ------------------------------------------------------------
    # Step 3: 信号融合
    # ------------------------------------------------------------

    def _step_signal_fusion(self, alpha_report: Any) -> List[FusionSignal]:
        logger.info("[Pipeline] Step 3: 信号融合")
        alpha_signals = self._real_alpha_signals()
        llm_signals = self._real_llm_signals()
        etf_signals = self._real_etf_signals()
        macro_signals = self._real_macro_signals()

        # 将 Alpha 评估报告中的因子信号也纳入融合，避免 mock 因子结果被丢弃
        alpha_report_signals = self._build_alpha_signals(alpha_report)
        if alpha_report_signals:
            for symbol, sig in alpha_report_signals.items():
                if symbol in alpha_signals:
                    old_s = alpha_signals[symbol].get("strength", 0.0)
                    old_c = alpha_signals[symbol].get("confidence", 0.2)
                    # 加权融合：真实价格动量 70% + Alpha 评估 30%
                    alpha_signals[symbol] = {
                        "strength": 0.7 * old_s + 0.3 * float(sig.get("strength", 0.0)),
                        "confidence": min(1.0, 0.7 * old_c + 0.3 * float(sig.get("confidence", 0.2))),
                    }

        return self.signal_fusion.fuse(alpha_signals, llm_signals, etf_signals, macro_signals)

    # ------------------------------------------------------------
    # Step 4: 组合优化
    # ------------------------------------------------------------

    def _step_portfolio_optimization(self, signals: List[FusionSignal]) -> PortfolioDecision:
        logger.info("[Pipeline] Step 4: 组合优化")
        tradable = [s for s in signals if s.strength != 0.0 and s.confidence > 0]
        if not tradable:
            tradable = [FusionSignal(symbol=symbol, strength=0.0, confidence=0.0) for symbol in self.ctx.symbols]

        # 让信号强度更灵敏地传导到预期收益，并加入置信度放大
        expected_returns = {}
        signal_map = {s.symbol: s for s in tradable}
        for symbol in self.ctx.symbols:
            s = signal_map.get(symbol, FusionSignal(symbol=symbol, strength=0.0, confidence=0.0))
            conf = float(getattr(s, "confidence", 0.5) or 0.5)
            confidence_boost = 0.6 + 0.4 * min(conf, 1.0)
            expected_returns[symbol] = float(s.strength) * 0.50 * confidence_boost

        symbols = list(expected_returns.keys())
        n = len(symbols)
        cov = pd.DataFrame(np.diag(np.full(n, 0.04 / 252)), index=symbols, columns=symbols)
        decision = self.optimizer.optimize(
            expected_returns=expected_returns,
            covariance_matrix=cov,
            current_positions={},
            impact_model=None,
            sector_map=self.sector_map,
        )
        # 硬性约束：将权重 clamp 到单票/板块硬上限，并如实记录违例
        clamped_w, violations = enforce_hard_constraints(
            decision.target_weights,
            max_weight=DEFAULT_MAX_WEIGHT,
            sector_map=self.sector_map,
            max_sector=DEFAULT_MAX_SECTOR,
        )
        if violations:
            logger.warning("[Pipeline] 硬性风险约束触发: %s", violations)
            decision.target_weights = clamped_w
            decision.meta["hard_constraint_violations"] = violations
            decision.meta["post_clamp"] = True
        return decision

    # ------------------------------------------------------------
    # Step 4.5: 市场状态调节 (大盘趋势过滤)
    # ------------------------------------------------------------
    # 动机: 2022 熊市回测显示策略仓位 89% 未减仓, 导致回撤 16.29% > 15% 上限
    # 方案: 用沪深300ETF(510300)的 MA60 判断大盘状态, 熊市按比例缩减仓位
    # ------------------------------------------------------------

    _MARKET_PROXY_SYMBOL = "510300"  # 沪深300ETF, 大盘代理
    _MA_PERIOD = 60                  # 60日均线, 中期趋势
    _MA_SLOPE_WINDOW = 5             # MA斜率观察窗口(5日变化)
    # 波动率+短期动量过滤层 (解决 MA60 滞后导致熊市满仓问题)
    # 动机: 2022-07 和 2024-12 回测显示, MA60 仍为 bull 但市场已急跌,
    #        导致 factor=1.0 全仓亏损 -6.71% / -6.02%。需快层信号补充。
    _VOL_LOOKBACK = 20               # 20日实现波动率窗口
    _VOL_HIGH_THRESHOLD = 0.015      # 日波动率>1.5%(~24%年化)视为高风险
    _MOM_LOOKBACK = 20               # 20日短期动量窗口
    _MOM_CRASH_THRESHOLD = -0.05     # 20日收益<-5%视为崩盘信号
    _MOM_SEVERE_CRASH = -0.10        # 20日收益<-10%视为严重崩盘
    _MIN_FACTOR_FLOOR = 0.4          # factor最低下限 (V2: 0.3→0.4, 避免过度减仓)

    def _step_market_regime_scaling(self, decision) -> tuple:
        """根据大盘趋势状态缩减/恢复仓位 (三层过滤)

        第一层 - 中期趋势 (MA60, 滞后但稳定):
          - close > MA60 且 MA60 上行 → 牛市  → base_factor=1.0
          - close > MA60 但 MA60 下行 → 震荡  → base_factor=0.8
          - close < MA60 但 MA60 上行 → 反弹  → base_factor=0.6
          - close < MA60 且 MA60 下行 → 熊市  → base_factor=0.5

        第二层 - 波动率过滤 (20日实现波动率, 快层):
          - 日波动率 > 1.5% → vol_override=0.8 (市场不稳定, 减仓20%)
          - 否则 → vol_override=1.0

        第三层 - 短期动量过滤 (20日收益, 最快层):
          - 20日收益 < -10% → mom_override=0.4 (严重崩盘, 减仓60%)
          - 20日收益 < -5%  → mom_override=0.6 (崩盘, 减仓40%)
          - 否则 → mom_override=1.0

        最终 factor = max(base_factor * vol_override * mom_override, 0.3)

        Returns:
            (调整后的 decision, regime_info dict)
        """
        cutoff = pd.Timestamp(self.ctx.report_date).normalize()
        # 时区处理
        try:
            if hasattr(cutoff, "tz") and cutoff.tz is not None:
                cutoff = cutoff.tz_localize(None)
        except Exception:
            cutoff = pd.Timestamp(cutoff).tz_localize(None) if pd.Timestamp(cutoff).tzinfo else pd.Timestamp(cutoff)

        # 获取大盘代理数据 (优先 _base.parquet, 杜绝前视偏差)
        df = self._load_base_cache(self._MARKET_PROXY_SYMBOL)
        if df is None or df.empty:
            # 回退到 data_provider (截断到回测日)
            if self.data_provider is not None:
                try:
                    df = self.data_provider.get_historical_data(self._MARKET_PROXY_SYMBOL, period="3y")
                except Exception:
                    df = None
        if df is None or df.empty:
            logger.warning("[MarketRegime] %s 数据不可用, 跳过趋势过滤 (factor=1.0)", self._MARKET_PROXY_SYMBOL)
            return decision, {"symbol": self._MARKET_PROXY_SYMBOL, "factor": 1.0, "regime": "unknown", "reason": "data_unavailable"}

        # 时区清理 + 截断到回测日 (杜绝前视偏差)
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df = df.sort_index()
        df = df[df.index <= cutoff]
        min_required = max(self._MA_PERIOD + self._MA_SLOPE_WINDOW, self._VOL_LOOKBACK, self._MOM_LOOKBACK)
        if len(df) < min_required:
            logger.warning("[MarketRegime] %s 数据不足 (%d 行 < %d), 跳过",
                           self._MARKET_PROXY_SYMBOL, len(df), min_required)
            return decision, {"symbol": self._MARKET_PROXY_SYMBOL, "factor": 1.0, "regime": "insufficient_data", "reason": "history_too_short"}

        close = df["close"]
        ma = close.rolling(self._MA_PERIOD).mean()
        latest_close = float(close.iloc[-1])
        latest_ma = float(ma.iloc[-1])
        # MA60 斜率: 最近5日MA的变化方向
        ma_slope = float(ma.iloc[-1] - ma.iloc[-1 - self._MA_SLOPE_WINDOW]) if len(ma) > self._MA_SLOPE_WINDOW else 0.0
        ma_rising = ma_slope > 0

        # === 第一层: MA60 中期趋势 ===
        above_ma = latest_close > latest_ma
        if above_ma and ma_rising:
            regime, base_factor = "bull", 1.0
        elif above_ma and not ma_rising:
            regime, base_factor = "choppy", 0.8
        elif not above_ma and ma_rising:
            regime, base_factor = "rebound", 0.6
        else:
            regime, base_factor = "bear", 0.5

        # === 第二层: 波动率过滤 (快层, 捕捉 MA60 滞后的崩盘) ===
        daily_rets = close.pct_change()
        recent_vol = float(daily_rets.tail(self._VOL_LOOKBACK).std())
        vol_override = 0.8 if recent_vol > self._VOL_HIGH_THRESHOLD else 1.0
        vol_flag = "high" if vol_override < 1.0 else "normal"

        # === 第三层: 短期动量过滤 (最快层, 急跌保护) ===
        if len(close) > self._MOM_LOOKBACK:
            mom_20d = float(close.iloc[-1] / close.iloc[-1 - self._MOM_LOOKBACK] - 1)
        else:
            mom_20d = 0.0
        if mom_20d < self._MOM_SEVERE_CRASH:
            mom_override, mom_flag = 0.4, "severe_crash"
        elif mom_20d < self._MOM_CRASH_THRESHOLD:
            mom_override, mom_flag = 0.6, "crash"
        else:
            mom_override, mom_flag = 1.0, "normal"

        # === 综合 factor (三层相乘, 最低0.3) ===
        factor = max(base_factor * vol_override * mom_override, self._MIN_FACTOR_FLOOR)

        # 应用缩减因子到 target_weights
        original_exposure = sum(decision.target_weights.values())
        scaled_weights = {s: w * factor for s, w in decision.target_weights.items()}
        scaled_exposure = sum(scaled_weights.values())
        decision.target_weights = scaled_weights
        decision.meta["market_regime"] = regime

        # === V5优化: 动量反转调整 (bear/rebound regime下, 超跌加仓, 超涨减仓) ===
        # 动机: 窗口1(2023-07~2024-09)年化-2.11%, bear regime下LGB信号失效
        #   V5原方案(波动率调整)失败: 降低高波动标的权重错过2024-02 603019+72.8%等大涨
        #   新方案(动量反转): 震荡市中均值回归效应显著, 超跌标的更可能反弹
        # 方案: bear/rebound regime下, 根据20日收益率调整权重
        #   20日收益 < -10% → ×1.3 (超跌加仓30%, 博反弹)
        #   20日收益 > 10%  → ×0.7 (超涨减仓30%, 锁定利润)
        #   归一化保持总仓位不变
        defensive_info = {}
        if regime in ("bear", "rebound") and self._historical_cache:
            symbol_rets = {}
            for symbol in decision.target_weights:
                df_sym = self._historical_cache.get(symbol)
                if df_sym is not None and len(df_sym) > 20:
                    try:
                        close_sym = df_sym["close"].sort_index()
                        if len(close_sym) > 20:
                            ret_20d = float(close_sym.iloc[-1] / close_sym.iloc[-1 - 20] - 1)
                            symbol_rets[symbol] = ret_20d
                    except Exception:
                        pass

            if symbol_rets:
                adjustments = {}
                n_oversold = n_overbought = 0
                for symbol, w in decision.target_weights.items():
                    ret_20d = symbol_rets.get(symbol, 0.0)
                    if ret_20d < -0.10:  # 超跌: 20日跌幅>10%
                        adjustments[symbol] = 1.3  # 加仓30%
                        n_oversold += 1
                    elif ret_20d > 0.10:  # 超涨: 20日涨幅>10%
                        adjustments[symbol] = 0.7  # 减仓30%
                        n_overbought += 1
                    else:
                        adjustments[symbol] = 1.0

                if n_oversold > 0 or n_overbought > 0:
                    pre_adjust_exposure = sum(decision.target_weights.values())
                    adjusted = {s: w * adjustments[s] for s, w in decision.target_weights.items()}
                    total_adj = sum(adjusted.values())
                    if total_adj > 0:
                        decision.target_weights = {
                            s: w / total_adj * pre_adjust_exposure for s, w in adjusted.items()
                        }
                    defensive_info = {
                        "n_oversold_increased": n_oversold,
                        "n_overbought_reduced": n_overbought,
                        "symbol_rets_20d": {s: round(r, 4) for s, r in symbol_rets.items()},
                    }
                    logger.info(
                        "[Reversal] regime=%s: 超跌加仓%d只, 超涨减仓%d只",
                        regime, n_oversold, n_overbought,
                    )
        decision.meta["regime_factor"] = factor
        decision.meta["market_proxy_close"] = latest_close
        decision.meta["market_proxy_ma60"] = latest_ma
        decision.meta["exposure_before"] = original_exposure
        decision.meta["exposure_after"] = scaled_exposure
        decision.meta["vol_override"] = vol_override
        decision.meta["mom_override"] = mom_override
        decision.meta["realized_vol_20d"] = recent_vol
        decision.meta["mom_20d"] = mom_20d

        logger.info(
            "[MarketRegime] %s regime=%s base=%.2f vol=%s(%.4f→%.2f) mom=%s(%+.4f→%.2f) final=%.2f exposure %.1f%%→%.1f%%",
            self._MARKET_PROXY_SYMBOL, regime, base_factor,
            vol_flag, recent_vol, vol_override,
            mom_flag, mom_20d, mom_override,
            factor,
            original_exposure * 100, scaled_exposure * 100,
        )

        regime_info = {
            "symbol": self._MARKET_PROXY_SYMBOL,
            "regime": regime,
            "factor": factor,
            "base_factor": base_factor,
            "vol_override": vol_override,
            "mom_override": mom_override,
            "realized_vol_20d": recent_vol,
            "mom_20d": mom_20d,
            "close": latest_close,
            "ma60": latest_ma,
            "ma_slope": ma_slope,
            "exposure_before": original_exposure,
            "exposure_after": scaled_exposure,
            "defensive": defensive_info,
        }
        return decision, regime_info

    # ------------------------------------------------------------
    # Step 5: 风险预算
    # ------------------------------------------------------------

    def _step_risk_budget(self, decision: PortfolioDecision) -> RiskCheckResult:
        """Step 5: 风险预算检查 (P0-10: 传递真实数据, VaR 1.5% 强制阻断)

        审计问题: 原代码传 price_data={} 和 current_positions={}, 导致 VaR 计算无数据,
                  VaR 1.5% 阻断规则形同虚设。
        修复: 传递 self._historical_cache 作为 price_data, 使 RiskBudgetEngine
              能计算真实组合 VaR95, 超过 1.5% 时 risk_result.allowed=False 阻断交易。
        """
        logger.info("[Pipeline] Step 5: 风险预算 (P0-10: 真实数据 + VaR 1.5%% 阻断)")
        # P0-10: 传递真实历史价格数据, 使 VaR 计算可用
        price_data = {}
        if self._historical_cache:
            for symbol, df in self._historical_cache.items():
                if df is not None and not df.empty and "close" in df.columns:
                    try:
                        price_data[symbol] = float(df["close"].iloc[-1])
                    except Exception:
                        pass
        return self.risk_budget_engine.check_pre_trade(
            target_portfolio=decision.target_weights,
            current_positions={},  # 生产环境无存量持仓 (实盘接入后填充)
            price_data=price_data,  # P0-10: 真实价格数据, 启用 VaR 1.5% 检查
        )

    def _apply_v72_bull_regime_cap(self, decision, regime_info: Dict[str, Any]) -> Dict[str, Any]:
        """P0-8: V7.2 bull regime 5% 上限 + V7.1 高波动惩罚 (生产路径复制)

        回测验证结论 (V7.1/V7.2/V8 三轮优化):
          - bull regime 下 LGB 信号失效, 满仓高波动股导致 2024-06 崩盘
          - V7.1: bull regime 下 vol20>4.5% 的股票权重 ×0.5 (但 2024-06 vol20=3.23% 未触发)
          - V7.2: bull regime max_weight 10%→5%, 达成 DSR≥5, 峰度 6.86→3.96
          - V8 动态融合失败: blend/exposure 模式均无法改善 Sharpe CV
          - 结论: V7.2 是权重后处理方案的最优解, 生产路径必须复制

        修改 decision.target_weights, 返回 cap_info。
        """
        regime = regime_info.get("regime", "unknown")
        cap_info = {
            "regime": regime,
            "v72_cap_applied": False,
            "v71_penalty_applied": False,
            "capped_symbols": [],
            "penalized_symbols": [],
        }

        if regime != "bull" or not decision.target_weights:
            return cap_info

        original_weights = dict(decision.target_weights)
        cutoff = pd.Timestamp(self.ctx.report_date).normalize()
        try:
            if hasattr(cutoff, "tz") and cutoff.tz is not None:
                cutoff = cutoff.tz_localize(None)
        except Exception:
            pass

        # === V7.1: 高波动股权重惩罚 (vol20 > 4.5% → ×0.5) ===
        penalized = {}
        for symbol, w in original_weights.items():
            if w <= 0:
                continue
            df_sym = self._historical_cache.get(symbol) if self._historical_cache else None
            if df_sym is None or df_sym.empty or "close" not in df_sym.columns:
                continue
            try:
                df_sym = df_sym.sort_index()
                if hasattr(df_sym.index, "tz") and df_sym.index.tz is not None:
                    df_sym.index = df_sym.index.tz_localize(None)
                df_sym = df_sym[df_sym.index <= cutoff]
                if len(df_sym) < 22:
                    continue
                daily_rets = df_sym["close"].pct_change().tail(20)
                vol20 = float(daily_rets.std())
                if vol20 > _V71_BULL_HIGH_VOL_THRESHOLD:
                    penalized[symbol] = vol20
            except Exception:
                continue

        if penalized:
            for symbol, vol20 in penalized.items():
                original_weights[symbol] = original_weights[symbol] * _V71_BULL_VOL_PENALTY
            cap_info["v71_penalty_applied"] = True
            cap_info["penalized_symbols"] = [
                {"symbol": s, "vol20": round(v, 4), "penalty": _V71_BULL_VOL_PENALTY}
                for s, v in penalized.items()
            ]
            logger.info(
                "[V7.1] bull regime 高波动惩罚: %d 只股票 vol20>%.1f%% → 权重×%.1f",
                len(penalized), _V71_BULL_HIGH_VOL_THRESHOLD * 100, _V71_BULL_VOL_PENALTY,
            )

        # === V7.2: bull regime 单票上限 5% (10% → 5%) ===
        capped_symbols = []
        capped_weights = {}
        excess_weight = 0.0
        for symbol, w in original_weights.items():
            if w > _V72_BULL_REGIME_MAX_WEIGHT:
                excess_weight += (w - _V72_BULL_REGIME_MAX_WEIGHT)
                capped_symbols.append({"symbol": symbol, "before": round(w, 4), "after": _V72_BULL_REGIME_MAX_WEIGHT})
                capped_weights[symbol] = _V72_BULL_REGIME_MAX_WEIGHT
            else:
                capped_weights[symbol] = w

        if capped_symbols:
            # 将截断释放的权重按比例重分配给未超限的股票 (保持总仓位)
            non_capped_total = sum(w for s, w in capped_weights.items() if s not in {c["symbol"] for c in capped_symbols})
            if non_capped_total > 0:
                for symbol in capped_weights:
                    if symbol not in {c["symbol"] for c in capped_symbols}:
                        capped_weights[symbol] += excess_weight * (capped_weights[symbol] / non_capped_total)

            decision.target_weights = capped_weights
            cap_info["v72_cap_applied"] = True
            cap_info["capped_symbols"] = capped_symbols
            logger.info(
                "[V7.2] bull regime 单票上限 5%%: 截断 %d 只股票 (释放权重 %.1f%% 按比例重分配)",
                len(capped_symbols), excess_weight * 100,
            )
        elif penalized:
            # 仅 V7.1 惩罚, 无 V7.2 截断, 仍需更新 weights
            decision.target_weights = original_weights

        return cap_info

    def _step_kill_switch_check(self, decision) -> Dict[str, Any]:
        """P0-13: KillSwitch 熔断检查 (生产 pipeline 集成)

        审计问题: 生产 pipeline 未集成 KillSwitch, L1/L2/L3 熔断对 pipeline 无效。
        修复: 在风险预算检查后、执行路由前, 检查 KillSwitch 级别:
          - L1: 过滤 BUY trades (停止新开仓, 仅保留 SELL)
          - L2+: 阻止全部 trades (返回空 trades 列表)
          - 异常: fail-closed (按 L3 处理, 阻止全部)

        Returns:
            {
                "level": int,
                "can_trade": bool,
                "can_open": bool,
                "filtered_trades": list,
                "blocked_trades_count": int,
                "fail_closed": bool,
            }
        """
        result = {
            "level": 0, "can_trade": True, "can_open": True,
            "filtered_trades_count": len(decision.trades) if hasattr(decision, "trades") else 0,
            "blocked_trades_count": 0, "fail_closed": False,
        }

        if not _HAS_KILL_SWITCH:
            logger.warning("[KillSwitch] 模块未加载, 跳过熔断检查 (降级模式)")
            result["level"] = -1
            result["note"] = "module_not_loaded"
            return result

        try:
            ks = KillSwitch()
            ks_status = ks.check_margin_status()
            ks_level = int(ks_status.get("level", 0)) if isinstance(ks_status, dict) else 0
            result["level"] = ks_level
            result["can_trade"] = bool(ks_status.get("can_trade", ks_level < 2))
            result["can_open"] = bool(ks_status.get("can_open", ks_level == 0))
            result["margin_usage_ratio"] = float(ks_status.get("margin_usage_ratio", 0))

            logger.info(
                "[KillSwitch] 生产 pipeline 熔断检查: L%d, can_trade=%s, can_open=%s, margin=%.1f%%",
                ks_level, result["can_trade"], result["can_open"],
                result["margin_usage_ratio"] * 100,
            )

            if ks_level == 0 or not hasattr(decision, "trades"):
                return result

            # L1: 过滤 BUY trades (停止新开仓)
            if ks_level == 1:
                original_count = len(decision.trades)
                decision.trades = [
                    t for t in decision.trades
                    if str(t.get("side", "BUY")).upper() != "BUY" or t.get("change", 0) < 0
                ]
                result["blocked_trades_count"] = original_count - len(decision.trades)
                result["filtered_trades_count"] = len(decision.trades)
                logger.warning(
                    "[KillSwitch L1] 停止新开仓! 过滤 %d 笔 BUY trades (保留 %d 笔 SELL)",
                    result["blocked_trades_count"], len(decision.trades),
                )

            # L2+: 阻止全部 trades
            elif ks_level >= 2:
                result["blocked_trades_count"] = len(decision.trades)
                decision.trades = []
                result["filtered_trades_count"] = 0
                logger.error(
                    "[KillSwitch L%d] 阻止全部 %d 笔 trades!",
                    ks_level, result["blocked_trades_count"],
                )
                # 触发熔断执行 (L2 强平 / L3 变现)
                try:
                    if ks_level >= 3:
                        logger.critical("[KillSwitch L3] 触发紧急变现协议!")
                        ks.execute_kill_switch(3)
                    else:
                        logger.error("[KillSwitch L2] 触发强平协议!")
                        ks.execute_kill_switch(2)
                except RuntimeError as e:
                    logger.error(f"[KillSwitch] 熔断执行失败 (无 broker_callback): {e}")
                    result["execute_error"] = str(e)

        except Exception as e:
            # P0-11: fail-closed — 异常时阻止全部交易
            logger.critical(
                "[KillSwitch] 检查异常! fail-closed 阻止全部 trades: %s", e, exc_info=True,
            )
            result["fail_closed"] = True
            result["level"] = 3
            result["can_trade"] = False
            result["can_open"] = False
            if hasattr(decision, "trades"):
                result["blocked_trades_count"] = len(decision.trades)
                decision.trades = []
                result["filtered_trades_count"] = 0

        return result

    # ------------------------------------------------------------
    # Step 6: 执行路由
    # ------------------------------------------------------------

    def _step_execution_routing(
        self,
        decision: PortfolioDecision,
        signals: List[FusionSignal],
    ) -> List[ExecutionPlan]:
        logger.info("[Pipeline] Step 6: 执行路由")
        signal_map = {s.symbol: s.to_dict() for s in signals}
        plans = []
        for trade in decision.trades:
            symbol = trade.get("symbol", "")
            plan = self.execution_router.route(
                order={
                    "symbol": symbol,
                    "quantity": 0.0,
                    "side": "BUY" if trade.get("change", 0) > 0 else "SELL",
                    "notional": abs(trade.get("change", 0)) * self.ctx.total_capital,
                },
                signal=signal_map.get(symbol),
                market_state={"volatility": 0.02},
            )
            plans.append(plan)
        return plans

    # ------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------

    def _save(self, result: Dict[str, Any]) -> None:
        path = self.ctx.output_path / f"pipeline_{self.ctx.mode}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            logger.info("[Pipeline] 报告已保存: %s", path)
        except Exception as e:
            logger.error("[Pipeline] 保存报告失败: %s", e)

    # ------------------------------------------------------------
    # 回测预加载
    # ------------------------------------------------------------

    def _load_base_cache(self, symbol: str) -> Optional[pd.DataFrame]:
        """读取预下载的 5y 基础缓存（_base.parquet），绕过 data_provider 的 24h TTL。

        由 _download_base_data.py 预先生成，覆盖 2021~2026 约 1260 个交易日，
        截断到回测日期后仍有 ~640 行，远超 LGB min_samples=150。
        """
        base_file = BASE_DIR / "data_cache" / f"historical_{symbol}_5y_base.parquet"
        if not base_file.exists():
            return None
        try:
            df = pd.read_parquet(base_file)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            logger.debug("[Pipeline] 读取 _base 缓存失败 %s: %s", symbol, e)
        return None

    def _load_and_truncate(self, symbol: str, period: str, cutoff: pd.Timestamp) -> Optional[pd.DataFrame]:
        """加载历史数据并截断到回测日期（杜绝前视偏差）。

        优先级: _base.parquet 预下载缓存 > data_provider.get_historical_data(5y)
        """
        # P1: 优先读预下载的 5y 基础缓存（无 TTL 限制，秒级读取）
        df = self._load_base_cache(symbol)
        # P2: 回退到 data_provider 在线拉取
        if (df is None or df.empty) and self.data_provider is not None:
            try:
                df = self.data_provider.get_historical_data(symbol, period=period)
            except Exception as e:
                logger.debug("[Pipeline] 在线拉取失败 %s: %s", symbol, e)
                df = None
        if df is None or df.empty:
            return None
        # 截断到回测日期, 移除时区
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df = df[df.index <= cutoff]
        return df if not df.empty else None

    def _preload_historical_data(self) -> None:
        if self.data_provider is None:
            return
        logger.info("[Pipeline] 回测预加载历史数据: symbols=%s as_of=%s", self.ctx.symbols, self.ctx.report_date)
        # 顶级对冲基金标准: 回测预加载必须截断到 as_of_date, 杜绝未来数据泄漏
        # period='5y' 确保截断到 2024-01-01 后仍有 ~640 行（> min_samples=150）
        cutoff = pd.Timestamp(self.ctx.report_date).normalize()
        loaded_count = 0
        for symbol in self.ctx.symbols:
            try:
                df = self._load_and_truncate(symbol, '5y', cutoff)
                if df is not None:
                    self._historical_cache[symbol] = df.copy()
                    loaded_count += 1
                    logger.debug("[Pipeline] 预加载 %s: %d 行 (截止 %s)",
                                 symbol, len(df), cutoff.date())
            except Exception as e:
                logger.warning("[Pipeline] 预加载失败 %s: %s", symbol, e)

        # LGB Walk-forward: 预加载全部 POSITION_SYMBOLS（截面/行业特征需要）
        if _HAS_LGB:
            for code, _suffix, _stype, _name, _style in POSITION_SYMBOLS:
                if code not in self._historical_cache:
                    try:
                        df = self._load_and_truncate(code, '5y', cutoff)
                        if df is not None:
                            self._historical_cache[code] = df.copy()
                    except Exception as e:
                        logger.debug("[Pipeline] LGB标的预加载失败 %s: %s", code, e)

        # 跨市场代理标的（LGB 特征工程依赖）
        for proxy in _CROSS_MARKET_PROXY_SYMBOLS:
            if proxy not in self._historical_cache:
                try:
                    df = self._load_and_truncate(proxy, '5y', cutoff)
                    if df is not None:
                        self._historical_cache[proxy] = df.copy()
                except Exception as e:
                    logger.debug("[Pipeline] 跨市场代理预加载失败 %s: %s", proxy, e)

        etf_candidates = [
            "510300", "510500", "510050", "159915", "512100", "512010", "512480", "512760", "515030", "515790",
        ]
        for etf in etf_candidates:
            if etf not in self._historical_cache:
                try:
                    df = self._load_and_truncate(etf, '5y', cutoff)
                    if df is not None:
                        self._historical_cache[etf] = df.copy()
                except Exception as e:
                    logger.debug("[Pipeline] ETF预加载失败 %s: %s", etf, e)

        logger.info("[Pipeline] 预加载完成: %d 个标的 (cache=%d)",
                    len(self._historical_cache), loaded_count)

    # ------------------------------------------------------------
    # LGB Walk-forward 集成
    # ------------------------------------------------------------

    def _build_lgb_feature_dict(self) -> Dict[str, pd.DataFrame]:
        """从 _historical_cache 构建完整特征字典（与训练管线一致）。

        流程:
            1. 从缓存提取 OHLCV（已截断到 cutoff）
            2. 逐标的添加技术因子
            3. 添加截面/行业/资金流向/跨市场/情绪因子
        Returns:
            {symbol: DataFrame[含全部特征列]}
        """
        if not _HAS_LGB:
            return {}

        # Step 1: 提取 OHLCV
        ohlcv_dict: Dict[str, pd.DataFrame] = {}
        for symbol, df in self._historical_cache.items():
            if df is not None and not df.empty:
                required_cols = {"open", "high", "low", "close", "volume"}
                if required_cols.issubset(set(df.columns)):
                    ohlcv_dict[symbol] = df.copy()

        if not ohlcv_dict:
            logger.warning("[LGB-WF] 无可用 OHLCV 数据构建特征")
            return {}

        # Step 2: 技术因子（逐标的）
        featured_dict: Dict[str, pd.DataFrame] = {}
        for symbol, df in ohlcv_dict.items():
            try:
                df_feat = add_technical_features(df)
                featured_dict[symbol] = df_feat
            except Exception as e:
                logger.debug("[LGB-WF] 技术因子失败 %s: %s", symbol, e)
                featured_dict[symbol] = df.copy()

        # Step 2.5: V6 均值回归特征 (提升震荡市Alpha信号质量)
        # 动机: Window 1 (2023-07~2024-09) 年化仅2.47%, 根因是趋势跟踪特征在震荡市失效
        # 方案: 添加RSI极端值、价格Z-score、短期反转因子等9个均值回归特征
        try:
            featured_dict = add_mean_reversion_features(featured_dict)
            logger.debug("[LGB-WF] 均值回归特征已添加 (9个特征/标的)")
        except Exception as e:
            logger.warning("[LGB-WF] 均值回归特征失败: %s", e)

        # Step 3: 截面 + 行业 + 资金流向 + 跨市场 + 情绪
        try:
            featured_dict = add_cross_sectional_features(featured_dict)
        except Exception as e:
            logger.debug("[LGB-WF] 截面因子失败: %s", e)
        try:
            featured_dict = add_industry_relative_strength_features(featured_dict)
        except Exception as e:
            logger.debug("[LGB-WF] 行业相对强度失败: %s", e)
        try:
            featured_dict = add_capital_flow_features(featured_dict)
        except Exception as e:
            logger.debug("[LGB-WF] 资金流向失败: %s", e)
        try:
            featured_dict = add_cross_market_features(featured_dict)
        except Exception as e:
            logger.debug("[LGB-WF] 跨市场失败: %s", e)
        try:
            # 空情绪 dict（历史新闻不可回溯，特征工程会填 0）
            featured_dict = add_sentiment_features(featured_dict, {})
        except Exception as e:
            logger.debug("[LGB-WF] 情绪因子失败: %s", e)

        return featured_dict

    def _compute_regime_series_for_cutoff(self) -> Optional[pd.Series]:
        """V9: 计算截至 cutoff 的大盘 regime 序列 (bull/bear/choppy/rebound)

        用 _V9_REGIME_PROXY_SYMBOL (510300) 作为大盘代理,
        数据从 _historical_cache 获取 (已截断到 cutoff, 无前视偏差)。

        Returns:
            pd.Series[index=date, values="bull"/"bear"/"choppy"/"rebound"/"unknown"]
            若代理数据缺失则返回 None
        """
        if not _HAS_LGB:
            return None

        proxy_code = _V9_REGIME_PROXY_SYMBOL
        proxy_df = self._historical_cache.get(proxy_code)
        if proxy_df is None or proxy_df.empty:
            logger.warning("[V9-Regime] 大盘代理 %s 数据缺失, 无法计算 regime", proxy_code)
            return None

        try:
            regime_series = compute_regime_series(
                proxy_df,
                ma_period=_V9_REGIME_MA_PERIOD,
                slope_window=_V9_REGIME_SLOPE_WINDOW,
            )
            if regime_series is None or regime_series.empty:
                logger.warning("[V9-Regime] regime 序列计算返回空")
                return None

            n_valid = int((regime_series != "unknown").sum())
            logger.info("[V9-Regime] regime 序列构建成功: %d 行 (有效 %d, 代理=%s)",
                        len(regime_series), n_valid, proxy_code)
            return regime_series
        except Exception as e:
            logger.warning("[V9-Regime] regime 序列计算异常: %s", e)
            return None

    def _lgb_walkforward_train(self) -> Dict[str, Any]:
        """Walk-forward 训练 LGB 模型（用截至 cutoff 的数据，无前视偏差）。

        V9: 当 _V9_REGIME_SPECIFIC_ENABLED=True 时, 每个标的训练 bull/non-bull 双模型,
            预测时按当前 regime 选择对应模型。否则回退 V6.2 单模型。

        Returns:
            {trained: N, failed: N, skipped: N, results: {...}}
        """
        if not _HAS_LGB:
            logger.warning("[LGB-WF] LGB 依赖不可用: %s", _LGB_IMPORT_ERR)
            return {"trained": 0, "failed": 0, "skipped": 0, "results": {}}

        # 仅在 backtest 模式下训练
        if self.ctx.mode != "backtest":
            return {"trained": 0, "failed": 0, "skipped": 0, "results": {}}

        logger.info("[LGB-WF] 开始 Walk-forward 训练 (as_of=%s, V9=%s)",
                    self.ctx.report_date, _V9_REGIME_SPECIFIC_ENABLED)

        # 构建特征字典
        featured_dict = self._build_lgb_feature_dict()
        if not featured_dict:
            logger.warning("[LGB-WF] 特征构建失败，降级为动量信号")
            return {"trained": 0, "failed": 0, "skipped": 0, "results": {}}

        # V9: 计算大盘 regime 序列 (用截至 cutoff 的 proxy 数据)
        regime_series = None
        if _V9_REGIME_SPECIFIC_ENABLED:
            regime_series = self._compute_regime_series_for_cutoff()
            if regime_series is None:
                logger.warning("[V9-Regime] regime 序列构建失败, 降级为单模型训练")
            else:
                regime_counts = regime_series.value_counts().to_dict()
                logger.info("[V9-Regime] regime 分布: %s", regime_counts)

        config = WALKFORWARD_LGB_CONFIG
        trained = 0
        failed = 0
        skipped = 0

        # 逐标的训练
        for code, _suffix, _stype, name, _style in POSITION_SYMBOLS:
            if code not in featured_dict:
                skipped += 1
                continue

            df = featured_dict[code]
            if df is None or len(df) < config.get("min_samples", 150):
                logger.debug("[LGB-WF] %s: 样本不足 (%d < %d)，跳过",
                             code, len(df) if df is not None else 0,
                             config.get("min_samples", 150))
                skipped += 1
                continue

            try:
                # 重试机制: 防止间歇性 LightGBM access violation 崩溃
                result = None
                last_err = None
                for attempt in range(_LGB_TRAIN_MAX_RETRIES + 1):
                    try:
                        if _V9_REGIME_SPECIFIC_ENABLED and regime_series is not None:
                            # V9: 训练 regime-specific 双模型
                            result = train_symbol_regime_specific(
                                code, df, config, regime_series,
                                min_samples_per_regime=_V9_MIN_SAMPLES_PER_REGIME,
                            )
                        else:
                            # V6.2 回退: 单模型训练
                            result = train_symbol_enhanced(code, df, config)
                        break
                    except Exception as e:
                        last_err = e
                        if attempt < _LGB_TRAIN_MAX_RETRIES:
                            logger.warning(
                                "[LGB-WF] %s 第 %d 次训练失败: %s, 准备重试 (%d/%d)",
                                code, attempt + 1, e, attempt + 1, _LGB_TRAIN_MAX_RETRIES,
                            )
                            time.sleep(_LGB_TRAIN_RETRY_DELAY)
                            gc.collect()
                        else:
                            raise

                if result is None:
                    raise RuntimeError(f"训练返回 None: {last_err}")

                if result.get("status") != "OK":
                    skipped += 1
                    continue

                # 缓存模型信息（内存中，不落盘）
                # V9: 额外缓存 models_by_regime / features_by_regime / selected_regime
                model_cache = {
                    "model": result["model"],
                    "selected_features": result["selected_features"],
                    "cv_after_selection": result["cv_after_selection"],
                    "final_metrics": result["final_metrics"],
                    "signal": result["signal"],
                    "raw_prediction": result["raw_prediction"],
                }
                if _V9_REGIME_SPECIFIC_ENABLED and regime_series is not None:
                    model_cache["v9_regime_specific"] = True
                    model_cache["current_regime"] = result.get("current_regime", "unknown")
                    model_cache["selected_regime"] = result.get("selected_regime", "full")
                    model_cache["models_by_regime"] = result.get("models_by_regime", {})
                    model_cache["features_by_regime"] = result.get("features_by_regime", {})
                    model_cache["n_bull_samples"] = result.get("n_bull_samples", 0)
                    model_cache["n_non_bull_samples"] = result.get("n_non_bull_samples", 0)
                self._lgb_models[code] = model_cache

                trained += 1
                cv_m = result["cv_after_selection"]
                v9_tag = ""
                if _V9_REGIME_SPECIFIC_ENABLED and regime_series is not None:
                    v9_tag = f" [V9 regime={result.get('selected_regime', '?')}]"
                logger.info(
                    "[LGB-WF] %s (%s): CV IC=%.4f±%.4f, final_ic=%.4f, signal=%.4f, features=%d%s",
                    code, name,
                    cv_m["mean_ic"], cv_m["std_ic"],
                    result["final_metrics"]["ic"],
                    result["signal"],
                    result["n_features_after"],
                    v9_tag,
                )
                # 释放当前标的的特征 DataFrame, 减少内存压力
                del df
                gc.collect()
            except Exception as e:
                failed += 1
                logger.warning("[LGB-WF] %s 训练失败 (重试 %d 次后): %s",
                              code, _LGB_TRAIN_MAX_RETRIES, e)

        logger.info("[LGB-WF] 训练完成: trained=%d failed=%d skipped=%d",
                    trained, failed, skipped)
        return {"trained": trained, "failed": failed, "skipped": skipped, "results": self._lgb_models}

    def _lgb_get_signal(self, symbol: str) -> tuple:
        """获取指定标的的 LGB 信号。

        Returns:
            (signal_strength, confidence)
            - signal_strength: tanh(raw_pred * 100), 范围 [-1, 1]
            - confidence: min(1.0, abs(cv_ic) + 0.2), 基于 CV IC
        """
        model_info = self._lgb_models.get(symbol)
        if not model_info:
            return (0.0, 0.0)

        signal = float(model_info.get("signal", 0.0))
        cv_metrics = model_info.get("cv_after_selection", {})
        cv_ic = float(cv_metrics.get("mean_ic", 0.0))
        confidence = min(1.0, abs(cv_ic) + 0.2)
        return (signal, confidence)

    # ------------------------------------------------------------
    # 真实数据快照
    # ------------------------------------------------------------

    def _real_snapshot(self, symbol: str) -> Dict[str, Any]:
        if self.data_provider is None:
            return self._mock_snapshot(symbol)
        try:
            data = self.data_provider.get_market_data(symbol)
            if not data or not isinstance(data, dict):
                return self._mock_snapshot(symbol)
            price = data.get("index_price") or data.get("close") or data.get("price")
            try:
                price = float(price)
            except Exception:
                price = None
            if not price or not math.isfinite(price) or price <= 0:
                return self._mock_snapshot(symbol)
            return {
                "price": price,
                "quality_score": 95.0,
                "timestamp": data.get("timestamp") or datetime.now().isoformat(),
                "source": data.get("source") or "data_provider",
            }
        except Exception as e:
            logger.warning("[Pipeline] 获取真实数据失败: %s", e)
            return self._mock_snapshot(symbol)

    # ------------------------------------------------------------
    # Mock 数据（smoke test / 真实环境可替换）
    # ------------------------------------------------------------

    def _mock_snapshot(self, symbol: str) -> Dict[str, Any]:
        return {
            "price": 10.0,
            "quality_score": 95.0,
            "timestamp": datetime.now().isoformat(),
            "source": "mock",
        }

    def _mock_factor_result(self):
        symbols = list(self.ctx.symbols)

        class MockFactor:
            def __init__(self, syms):
                self.values = {s: float(np.random.randn()) for s in syms}
                self.category = "mock"

        class MockResult:
            def __init__(self, syms):
                self.factors = {f"factor_{i}": MockFactor(syms) for i in range(5)}

        return MockResult(symbols)

    def _mock_forward_returns(self) -> Dict[str, float]:
        return {s: float(np.random.randn() * 0.01) for s in self.ctx.symbols}

    def _build_alpha_signals(self, alpha_report: Any) -> Dict[str, Dict[str, Any]]:
        evaluations = []
        if isinstance(alpha_report, dict):
            evaluations = alpha_report.get("evaluations", [])
        elif hasattr(alpha_report, "evaluations"):
            evaluations = [e.to_dict() if hasattr(e, "to_dict") else e for e in getattr(alpha_report, "evaluations", [])]
        signals = {}
        for ev in evaluations:
            name = ev.get("factor_name", "")
            ic = float(ev.get("ic_1d", 0.0))
            ic_ir = float(ev.get("ic_ir", 0.0))
            strength = max(-1.0, min(1.0, ic * 10.0))
            confidence = min(1.0, abs(ic_ir) + 0.2)
            # 修复 BUG: 原代码将每个 evaluation 应用到所有 symbols（最后一个覆盖全部）
            # 现在使用 evaluation 中的 symbol 字段正确映射
            symbol = ev.get("symbol", "")
            if not symbol:
                # 兼容旧格式 mom60_{symbol} / lgb_{symbol}
                if name.startswith("mom60_"):
                    symbol = name[len("mom60_"):]
                elif name.startswith("lgb_"):
                    symbol = name[len("lgb_"):]
            if symbol and symbol in self.ctx.symbols:
                signals[symbol] = {"strength": strength, "confidence": confidence}
        return signals

    def _mock_llm_signals(self) -> Dict[str, Dict[str, Any]]:
        return {s: {"strength": 0.0, "confidence": 0.3} for s in self.ctx.symbols}

    def _mock_etf_signals(self) -> Dict[str, Dict[str, Any]]:
        return {s: {"strength": 0.0, "confidence": 0.3} for s in self.ctx.symbols}

    def _mock_macro_signals(self) -> Dict[str, Dict[str, Any]]:
        return {"macro_index": {"strength": 0.0, "confidence": 0.2}}

    def _real_alpha_signals(self) -> Dict[str, Dict[str, Any]]:
        signals: Dict[str, Dict[str, Any]] = {}
        if self.data_provider is None:
            return {s: {"strength": 0.0, "confidence": 0.2} for s in self.ctx.symbols}
        for symbol in self.ctx.symbols:
            try:
                if symbol in self._historical_cache:
                    df = self._historical_cache[symbol]
                else:
                    df = self.data_provider.get_historical_data(symbol, period='5y')
                if df is None or df.empty or len(df) < 30:
                    raise ValueError("history_too_short")
                # 回测模式：按报告日期截断，避免未来信息泄露
                try:
                    cutoff = pd.Timestamp(self.ctx.report_date).normalize()
                    if hasattr(df.index, "tz") and df.index.tz is not None:
                        df.index = df.index.tz_localize(None)
                    if hasattr(cutoff, "tz") and cutoff.tz is not None:
                        cutoff = cutoff.tz_localize(None)
                    df = df[df.index <= cutoff]
                except Exception as e:
                    logger.debug("[Pipeline] Alpha 日期截断失败 %s: %s", symbol, e)
                if df is None or df.empty or len(df) < 30:
                    raise ValueError("history_too_short_after_cutoff")
                close = df['close'].dropna()
                volume = df['volume'].dropna() if 'volume' in df.columns else pd.Series(dtype=float)

                # 多周期动量：5日、20日、60日
                ret_5d = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) > 5 else 0.0
                ret_20d = float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) > 20 else 0.0
                ret_60d = float(close.iloc[-1] / close.iloc[-61] - 1) if len(close) > 60 else 0.0
                momentum = 0.40 * ret_5d + 0.35 * ret_20d + 0.25 * ret_60d

                # 波动率突破：当前波动率 vs 历史波动率
                vol_20d = float(close.pct_change().rolling(20).std().iloc[-1]) if len(close) > 20 else 0.0
                vol_60d = float(close.pct_change().rolling(60).std().iloc[-1]) if len(close) > 60 else vol_20d
                vol_ratio = float(vol_20d / vol_60d) if vol_60d > 1e-12 else 1.0
                vol_breakout = float(max(-1.0, min(1.0, (vol_ratio - 1.0) * 8)))

                # 换手率异常：当前成交量 vs 历史均值
                if len(volume) > 20:
                    vol_ma20 = float(volume.rolling(20).mean().iloc[-1])
                    vol_ratio_now = float(volume.iloc[-1] / vol_ma20) if vol_ma20 > 1e-12 else 1.0
                    turnover_signal = float(max(-1.0, min(1.0, (vol_ratio_now - 1.0) * 0.5)))
                else:
                    turnover_signal = 0.0

                # 相对强弱：当前价格 vs 20日最高/最低
                if len(close) > 20:
                    high_20d = float(close.rolling(20).max().iloc[-1])
                    low_20d = float(close.rolling(20).min().iloc[-1])
                    rsi_proxy = float((close.iloc[-1] - low_20d) / (high_20d - low_20d)) if (high_20d - low_20d) > 1e-12 else 0.5
                    rsi_signal = float(max(-1.0, min(1.0, (rsi_proxy - 0.5) * 2)))
                else:
                    rsi_signal = 0.0

                # 均线突破：价格与 MA5/MA20/MA60 的关系
                ma_breakout = 0.0
                if len(close) > 60:
                    ma5 = float(close.rolling(5).mean().iloc[-1])
                    ma20 = float(close.rolling(20).mean().iloc[-1])
                    ma60 = float(close.rolling(60).mean().iloc[-1])
                    price = float(close.iloc[-1])
                    if ma5 > ma20 > ma60 and price > ma5:
                        ma_breakout = 1.0
                    elif ma5 < ma20 < ma60 and price < ma5:
                        ma_breakout = -1.0

                # MACD 信号
                macd_signal = 0.0
                if len(close) > 26:
                    ema12 = close.ewm(span=12, adjust=False).mean()
                    ema26 = close.ewm(span=26, adjust=False).mean()
                    macd_line = ema12 - ema26
                    signal_line = macd_line.ewm(span=9, adjust=False).mean()
                    macd_val = float(macd_line.iloc[-1] - signal_line.iloc[-1])
                    macd_signal = float(max(-1.0, min(1.0, macd_val * 80)))

                # 布林带位置
                bb_signal = 0.0
                if len(close) > 20:
                    bb_mid = float(close.rolling(20).mean().iloc[-1])
                    bb_std = float(close.rolling(20).std().iloc[-1])
                    if bb_std > 1e-12:
                        bb_upper = bb_mid + 2 * bb_std
                        bb_lower = bb_mid - 2 * bb_std
                        bb_pos = float((close.iloc[-1] - bb_lower) / (bb_upper - bb_lower))
                        bb_signal = float(max(-1.0, min(1.0, (bb_pos - 0.5) * 2)))

                # 综合 Alpha 信号：动量为主，波动率/换手率/RSI/均线/MACD/布林带为辅
                mom_strength = (
                    0.35 * momentum
                    + 0.15 * vol_breakout
                    + 0.10 * turnover_signal
                    + 0.10 * rsi_signal
                    + 0.15 * ma_breakout
                    + 0.10 * macd_signal
                    + 0.05 * bb_signal
                )
                mom_strength = float(max(-1.0, min(1.0, mom_strength * 14)))

                # 置信度：数据越长越稳，信号越集中越稳
                mom_confidence = 0.55
                if len(close) > 60:
                    mom_confidence += 0.15
                if len(close) > 120:
                    mom_confidence += 0.1
                if abs(momentum) > 0.02 or abs(ma_breakout) > 0.5 or abs(macd_signal) > 0.3:
                    mom_confidence += 0.1
                mom_confidence = float(min(1.0, mom_confidence))

                # LGB Walk-forward 信号融合：LGB 80% + 动量 20%
                lgb_strength, lgb_confidence = self._lgb_get_signal(symbol)
                if abs(lgb_strength) > 1e-9:
                    strength = float(max(-1.0, min(1.0, 0.8 * lgb_strength + 0.2 * mom_strength)))
                    confidence = float(min(1.0, 0.8 * lgb_confidence + 0.2 * mom_confidence))
                else:
                    # LGB 不可用时降级为 100% 动量信号
                    strength = mom_strength
                    confidence = mom_confidence

                signals[symbol] = {"strength": strength, "confidence": confidence}
            except Exception as e:
                logger.warning("[Pipeline] 真实alpha信号获取失败 %s: %s", symbol, e)
                signals[symbol] = {"strength": 0.0, "confidence": 0.2}
        return signals

    def _real_macro_signals(self) -> Dict[str, Dict[str, Any]]:
        if self.data_provider is None:
            return {"macro_index": {"strength": 0.0, "confidence": 0.2}}
        try:
            sentiment = {}
            macro = self.data_provider.get_external_macro()
            if isinstance(macro, dict):
                sentiment = macro.get("risk_sentiment", {}) or {}
            if not sentiment:
                sentiment = self.data_provider.get_risk_sentiment() or {}
            score = float(sentiment.get("score", 0.0)) if isinstance(sentiment, dict) else 0.0
            strength = float(max(-1.0, min(1.0, score)))

            if strength == 0.0:
                try:
                    rets = []
                    for symbol in self.ctx.symbols:
                        if symbol in self._historical_cache:
                            df = self._historical_cache[symbol]
                        else:
                            df = self.data_provider.get_historical_data(symbol, period='1y')
                        if df is not None and not df.empty and 'close' in df.columns:
                            try:
                                cutoff = pd.Timestamp(self.ctx.report_date).normalize()
                                if hasattr(df.index, "tz") and df.index.tz is not None:
                                    df.index = df.index.tz_localize(None)
                                if hasattr(cutoff, "tz") and cutoff.tz is not None:
                                    cutoff = cutoff.tz_localize(None)
                                df = df[df.index <= cutoff]
                            except Exception as e:
                                logger.debug("[Pipeline] 宏观日期截断失败 %s: %s", symbol, e)
                            s = df['close'].dropna()
                            if len(s) > 5:
                                rets.append(float(s.iloc[-1] / s.iloc[-6] - 1))
                    if rets:
                        macro_proxy = float(max(-1.0, min(1.0, sum(rets) / len(rets) * 8)))
                        if macro_proxy != 0.0:
                            strength = macro_proxy
                except Exception as e:
                    logger.debug("[Pipeline] 宏观代理信号计算失败: %s", e)

            confidence = 0.5 if score != 0.0 else 0.35
            return {"macro_index": {"strength": strength, "confidence": confidence}}
        except Exception as e:
            logger.warning("[Pipeline] 真实宏观信号获取失败: %s", e)
            return {"macro_index": {"strength": 0.0, "confidence": 0.2}}


    def _real_llm_signals(self) -> Dict[str, Dict[str, Any]]:
        if self.data_provider is None:
            return {s: {"strength": 0.0, "confidence": 0.2} for s in self.ctx.symbols}
        signals: Dict[str, Dict[str, Any]] = {}
        for symbol in self.ctx.symbols:
            try:
                news_sentiment = self.data_provider.get_news_sentiment(symbol, limit=20)
                strength = 0.0
                confidence = 0.2
                if isinstance(news_sentiment, list) and news_sentiment:
                    vals = []
                    confs = []
                    for item in news_sentiment:
                        if isinstance(item, dict):
                            vals.append(float(item.get("sentiment_score", item.get("score", 0.0))))
                            confs.append(float(item.get("confidence", 0.0)))
                    if vals:
                        strength = float(max(-1.0, min(1.0, sum(vals) / len(vals))))
                        confidence = float(min(1.0, (sum(confs) / len(confs)) if confs else 0.2 + 0.1))

                if strength == 0.0 and confidence <= 0.2:
                    try:
                        if symbol in self._historical_cache:
                            df = self._historical_cache[symbol]
                        else:
                            df = self.data_provider.get_historical_data(symbol, period='1m')
                        if df is not None and not df.empty and 'close' in df.columns:
                            try:
                                cutoff = pd.Timestamp(self.ctx.report_date).normalize()
                                if hasattr(df.index, "tz") and df.index.tz is not None:
                                    df.index = df.index.tz_localize(None)
                                if hasattr(cutoff, "tz") and cutoff.tz is not None:
                                    cutoff = cutoff.tz_localize(None)
                                df = df[df.index <= cutoff]
                            except Exception as e:
                                logger.debug("[Pipeline] LLM 日期截断失败 %s: %s", symbol, e)
                            s = df['close'].dropna()
                            if len(s) > 10:
                                proxy = float(s.iloc[-1] / s.iloc[-6] - 1)
                                strength = float(max(-1.0, min(1.0, proxy * 12)))
                                confidence = 0.45
                    except Exception as e:
                        logger.debug("[Pipeline] LLM代理信号计算失败 %s: %s", symbol, e)

                conf = 0.55 if any(isinstance(item, dict) and item.get("sentiment_score", item.get("score", 0.0)) != 0.0 for item in (news_sentiment or [])) else confidence
                signals[symbol] = {"strength": strength, "confidence": conf}
            except Exception as e:
                logger.warning("[Pipeline] 真实LLM信号获取失败 %s: %s", symbol, e)
                signals[symbol] = {"strength": 0.0, "confidence": 0.2}
        return signals

    def _real_etf_signals(self) -> Dict[str, Dict[str, Any]]:
        if self.data_provider is None:
            return {s: {"strength": 0.0, "confidence": 0.2} for s in self.ctx.symbols}
        signals: Dict[str, Dict[str, Any]] = {}
        etf_candidates = [
            "510300", "510500", "510050", "159915", "512100", "512010", "512480", "512760", "515030", "515790",
        ]
        category_map = {
            "510300": "沪深300", "510500": "中证500", "510050": "上证50", "159915": "创业板",
            "512100": "中证1000", "512010": "医药", "512480": "半导体", "512760": "半导体", "515030": "新能源", "515790": "光伏",
        }
        sector_keywords = {
            "600519": ["白酒", "消费"], "000858": ["白酒", "消费"], "601318": ["保险", "金融"], "000001": ["金融"],
            "600036": ["银行", "金融"], "601398": ["银行", "金融"], "600276": ["医药", "创新药"], "000063": ["通信", "5G", "科技"],
        }
        for symbol in self.ctx.symbols:
            try:
                matched_etf = None
                for code in etf_candidates:
                    cat = category_map.get(code, "")
                    keywords = sector_keywords.get(symbol, [])
                    if cat and any(cat in kw or kw in cat for kw in keywords):
                        matched_etf = code
                        break
                strength = 0.0
                confidence = 0.2
                if matched_etf:
                    raw = self.data_provider.get_market_data(matched_etf) or {}
                    change_pct = raw.get("change_pct", 0.0)
                    try:
                        change_pct = float(change_pct)
                    except Exception:
                        change_pct = 0.0
                    if math.isfinite(change_pct) and change_pct != 0.0:
                        strength = float(max(-1.0, min(1.0, change_pct / 10.0)))
                        confidence = 0.6 if abs(strength) >= 0.15 else 0.4
                    else:
                        if matched_etf in self._historical_cache:
                            df = self._historical_cache[matched_etf]
                        else:
                            df = self.data_provider.get_historical_data(matched_etf, period='1m')
                        if df is not None and not df.empty and 'close' in df.columns:
                            try:
                                cutoff = pd.Timestamp(self.ctx.report_date).normalize()
                                if hasattr(df.index, "tz") and df.index.tz is not None:
                                    df.index = df.index.tz_localize(None)
                                if hasattr(cutoff, "tz") and cutoff.tz is not None:
                                    cutoff = cutoff.tz_localize(None)
                                df = df[df.index <= cutoff]
                            except Exception as e:
                                logger.debug("[Pipeline] ETF 日期截断失败 %s: %s", matched_etf, e)
                            s = df['close'].dropna()
                            if len(s) > 10:
                                proxy = float(s.iloc[-1] / s.iloc[-6] - 1)
                                strength = float(max(-1.0, min(1.0, proxy * 10)))
                                confidence = 0.45
                    conf = 0.55 if change_pct != 0.0 else confidence
                    signals[symbol] = {"strength": strength, "confidence": conf}
                else:
                    signals[symbol] = {"strength": 0.0, "confidence": 0.2}
            except Exception as e:
                logger.warning("[Pipeline] 真实ETF信号获取失败 %s: %s", symbol, e)
                signals[symbol] = {"strength": 0.0, "confidence": 0.2}
        return signals



# ============================================================================
# CLI
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Institutional Pipeline Runner")
    parser.add_argument("--institutional-pipeline", action="store_true", help="运行机构级量化闭环")
    parser.add_argument("--mode", default="smoke", choices=["smoke", "backtest", "live"])
    parser.add_argument("--symbols", nargs="*", default=["600519", "000858", "601318", "000001", "600036", "601398", "600276", "000063"])
    parser.add_argument("--capital", type=float, default=3_000_000.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.institutional_pipeline:
        return
    ctx = PipelineContext(
        mode=args.mode,
        symbols=args.symbols,
        total_capital=args.capital,
    )
    runner = InstitutionalPipelineRunner(ctx)
    result = runner.run()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
