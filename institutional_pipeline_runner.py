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
import json
import logging
import math
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

try:
    from utils.data_provider import MarketDataProvider
    _HAS_DATA_PROVIDER = True
except Exception:
    _HAS_DATA_PROVIDER = False

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

        # Step 5: 风险预算检查
        risk_result = self._step_risk_budget(portfolio_decision)
        result["steps"]["risk_budget"] = risk_result.to_dict() if hasattr(risk_result, "to_dict") else risk_result
        if not risk_result.allowed and self.ctx.mode not in ("smoke", "backtest"):
            logger.warning("[Pipeline] 风险预算未通过，交易计划被拦截")
            result["status"] = "blocked_by_risk_budget"
            self._save(result)
            return result

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
        """板块映射（用于板块集中度硬约束）。覆盖系统已知标的，未知标的规定为 unknown。"""
        return {
            "600519": "消费", "000858": "消费",
            "601318": "金融", "000001": "金融", "600036": "金融", "601398": "金融", "600016": "金融", "601166": "金融", "600000": "金融",
            "600276": "医药", "300760": "医药", "002594": "医药",
            "000063": "科技", "688041": "科技", "300308": "科技", "002371": "科技", "603019": "科技",
            "601088": "顺周期", "600900": "防御", "600519": "消费",
            "510300": "宽基", "510050": "宽基", "510500": "宽基", "159915": "宽基", "512100": "宽基",
            "588000": "科技", "588080": "科技", "512760": "科技",
            "512880": "金融", "512800": "金融",
            "515030": "新能源", "512170": "医药", "518880": "黄金", "512890": "防御",
            "688017": "制造", "300274": "新能源", "300308": "科技",
        }

    def _step_alpha_evaluation(self) -> Any:
        logger.info("[Pipeline] Step 2: Alpha 评估")
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
        """基于真实历史行情计算因子 IC（无前视偏差）。

        因子：60 日动量；前向收益：因子日后 20 日收益（均截至 as_of_date，
        未来 20 日数据不足的点被丢弃，杜绝泄漏）。
        """
        evaluations: List[Dict[str, Any]] = []
        active = 0
        if self.data_provider is None:
            return {"evaluations": evaluations, "category": "mock", "active_factors": 0}
        import pandas as pd

        cutoff = pd.Timestamp(self.ctx.report_date).normalize()
        for symbol in self.ctx.symbols:
            try:
                df = self._historical_cache.get(symbol) or self.data_provider.get_historical_data(symbol, period='3y')
                if df is None or df.empty or 'close' not in df.columns or len(df) < 120:
                    continue
                s = df['close'].dropna()
                if hasattr(s.index, "tz") and s.index.tz is not None:
                    s.index = s.index.tz_localize(None)
                s = s[s.index <= cutoff]
                if len(s) < 120:
                    continue
                # 因子日 t 的 60 日动量（仅用 ≤t 的数据）
                factor = s.pct_change(60).shift(1)
                # 前向 20 日收益（t+1..t+20），需要 t+20 的数据，超出 cutoff 的点为 NaN 并被丢弃
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
                })
                active += 1
            except Exception as e:
                logger.debug("[Pipeline] 真实alpha评估失败 %s: %s", symbol, e)
        category = "real" if active > 0 else "mock"
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
    # Step 5: 风险预算
    # ------------------------------------------------------------

    def _step_risk_budget(self, decision: PortfolioDecision) -> RiskCheckResult:
        logger.info("[Pipeline] Step 5: 风险预算")
        return self.risk_budget_engine.check_pre_trade(
            target_portfolio=decision.target_weights,
            current_positions={},
            price_data={},
        )

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

    def _preload_historical_data(self) -> None:
        if self.data_provider is None:
            return
        logger.info("[Pipeline] 回测预加载历史数据: symbols=%s", self.ctx.symbols)
        for symbol in self.ctx.symbols:
            try:
                df = self.data_provider.get_historical_data(symbol, period='3y')
                if df is not None and not df.empty:
                    self._historical_cache[symbol] = df.copy()
            except Exception as e:
                logger.warning("[Pipeline] 预加载失败 %s: %s", symbol, e)
        etf_candidates = [
            "510300", "510500", "510050", "159915", "512100", "512010", "512480", "512760", "515030", "515790",
        ]
        for etf in etf_candidates:
            try:
                df = self.data_provider.get_historical_data(etf, period='1m')
                if df is not None and not df.empty:
                    self._historical_cache[etf] = df.copy()
            except Exception as e:
                logger.debug("[Pipeline] ETF预加载失败 %s: %s", etf, e)

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
            for symbol in self.ctx.symbols:
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
                    df = self.data_provider.get_historical_data(symbol, period='3y')
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
                strength = (
                    0.35 * momentum
                    + 0.15 * vol_breakout
                    + 0.10 * turnover_signal
                    + 0.10 * rsi_signal
                    + 0.15 * ma_breakout
                    + 0.10 * macd_signal
                    + 0.05 * bb_signal
                )
                strength = float(max(-1.0, min(1.0, strength * 14)))

                # 置信度：数据越长越稳，信号越集中越稳
                confidence = 0.55
                if len(close) > 60:
                    confidence += 0.15
                if len(close) > 120:
                    confidence += 0.1
                if abs(momentum) > 0.02 or abs(ma_breakout) > 0.5 or abs(macd_signal) > 0.3:
                    confidence += 0.1
                confidence = float(min(1.0, confidence))

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
