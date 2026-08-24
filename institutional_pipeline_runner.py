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
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.alpha_evaluator import AlphaEvaluator
from utils.backtest_integrity import (
    evaluate_alpha_provenance,
    validate_backtest,
)
from utils.data_gate import DataGate
from utils.drawdown_breaker import DrawdownCircuitBreaker
from utils.execution_router import ExecutionPlan, ExecutionRouter
from utils.institutional_optimizer import InstitutionalPortfolioOptimizer, PortfolioDecision
from utils.killswitch_guard import apply_killswitch_l1_filter  # GLM-5.2 C2(#22) 修复
from utils.path_config import get_institutional_pipeline_report_dir

# === Mixin: 从本文件抽取的数据加载/LGB训练/信号计算方法 ===
from utils.pipeline_data_mixin import DataMixin
from utils.pipeline_lgb_mixin import LGBMixin
from utils.pipeline_signal_mixin import SignalMixin
from utils.risk_budget_engine import RiskBudgetEngine, RiskCheckResult

# 顶级对冲基金整改：统一成本、硬性风险约束、回撤熔断、回测完整性守卫
from utils.risk_constraints import (
    DEFAULT_MAX_DAILY_VAR,
    DEFAULT_MAX_SECTOR,
    DEFAULT_MAX_SINGLE_VAR,
    DEFAULT_MAX_WEIGHT,
    enforce_hard_constraints,
)
from utils.signal_fusion import FusionSignal, SignalFusionEngine

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
_V72_BULL_REGIME_MAX_WEIGHT = 0.05  # bull regime 单票上限 5% (V7.2)
_V71_BULL_HIGH_VOL_THRESHOLD = 0.045  # bull regime 高波动阈值 4.5% (20日实现波动率)
_V71_BULL_VOL_PENALTY = 0.5  # 高波动股权重惩罚 ×0.5 (V7.1)

try:
    from utils.data_provider import MarketDataProvider

    _HAS_DATA_PROVIDER = True
except Exception as e:
    _HAS_DATA_PROVIDER = False
    # BUG-08 修复 (2026-07-31): logger 在第 151 行才定义, 此处引用会抛 NameError
    # 改用 logging.getLogger 直接获取, 避免模块加载顺序依赖
    logging.getLogger("institutional_pipeline").debug(
        f"MarketDataProvider 加载失败, 数据源降级: {e}"
    )

# LGB Walk-forward 集成（可选依赖，缺失时降级为动量信号）
# B2 修复: 将 logger 定义提前到 try 之前, 避免 LGB 导入失败进入 except 时
# 引用尚未绑定的 logger 变量导致二次 NameError, 掩盖原始导入错误。
logger = logging.getLogger("institutional_pipeline")

# === 高价值资产集成 (2026-08-10): Ledoit-Wolf 收缩协方差 + Black-Litterman 影子 ===
# LW 收敛为唯一收缩协方差真相源（替代硬编码对角协方差），BL 以 Feature Flag 影子模式接入
try:
    from utils.ledoit_wolf_covariance import LedoitWolfCovariance

    _HAS_LW = True
except Exception as e:  # noqa: BLE001
    _HAS_LW = False
    logger.debug("LedoitWolfCovariance 加载失败, 协方差降级为对角矩阵: %s", e)

try:
    from utils.black_litterman_optimizer import BlackLittermanOptimizer

    _HAS_BL = True
except Exception as e:  # noqa: BLE001
    _HAS_BL = False
    logger.debug("BlackLittermanOptimizer 加载失败, BL 影子模式不可用: %s", e)

try:
    from lgb_enhanced_trainer import LGB_ENHANCED_CONFIG

    _HAS_LGB = True
except Exception as _lgb_import_err:
    _HAS_LGB = False
    _LGB_IMPORT_ERR = str(_lgb_import_err)
    logger.warning(f"LightGBM 导入失败, 已降级禁用 LGB 模型: {_lgb_import_err}")

# Walk-forward 训练配置（加速版：月度重训无需 2000 轮）
WALKFORWARD_LGB_CONFIG = (
    {
        **LGB_ENHANCED_CONFIG,
        "lgb_params": {
            **LGB_ENHANCED_CONFIG["lgb_params"],
            "n_estimators": 1000,  # 2000 → 1000（加速）
            "n_jobs": 1,  # 单线程, 避免 LightGBM 多线程竞争导致的 access violation
        },
        "early_stopping_rounds": 100,  # 200 → 100（加速）
        "news_lookback_days": 0,  # 跳过新闻（历史不可回溯）
        "adaptive_retrain_threshold": 5,
        "adaptive_retrain_lr": 0.001,
        "adaptive_retrain_n_estimators": 2000,
    }
    if _HAS_LGB
    else {}
)

# LGB 训练重试配置（防止间歇性 access violation 崩溃）
_LGB_TRAIN_MAX_RETRIES = 2  # 失败后最多重试 2 次
_LGB_TRAIN_RETRY_DELAY = 1.0  # 重试间隔（秒）

# V9: Regime-Specific 训练开关 (bull/non-bull 双模型)
# 动机: V7-Model + V7.1/V7.2 权重后处理均无法将 Window 1 Sharpe CV 降至 <0.5
#       根因是单一 LGB 模型被 bear 主导 (47% 样本), 在 bull regime 信号失效
# 方案: 每个标的训练两个独立模型 (bull + non-bull), 预测时按当前 regime 选择
# 验证: 2024-06-03 (bull regime) V9 bull 模型给出 -0.9858 强烈看跌信号 (IC=0.79)
#       而 V6.2 在该月给 688017/300308 高权重 (10%/8%) 导致 -5.11% 月度亏损
_V9_REGIME_SPECIFIC_ENABLED = True  # 总开关: True=启用 V9 双模型, False=回退 V6.2 单模型
_V9_MIN_SAMPLES_PER_REGIME = 100  # 每个 regime 子集最少样本数 (低于此值降级为全样本模型)
_V9_REGIME_PROXY_SYMBOL = "510300"  # 大盘代理 (与 _REGIME_PROXY_SYMBOL 一致)
_V9_REGIME_MA_PERIOD = 60  # MA60 中期趋势
_V9_REGIME_SLOPE_WINDOW = 5  # MA60 斜率窗口

# 跨市场代理标的（特征工程依赖）
_CROSS_MARKET_PROXY_SYMBOLS = ["518880", "600036", "588000", "515180"]

# ============================================================================
# 日志
# ============================================================================
# S2修复: 将 logging 配置从模块级别移至函数, 避免 import 时污染测试/子进程环境。
# 调用方需显式调用 _setup_logging() 或在 main() 中配置。
# B2 修复: logger 已前置定义 (见文件顶部 try 之前), 此处不再重复定义。
def _setup_logging() -> None:
    """初始化日志配置 (仅在主进程入口调用, 避免 import 副作用)。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler("institutional_pipeline_runner.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


# logger 已前置定义 (B2 修复), 此处不再重复。
BASE_DIR = Path(__file__).resolve().parent
# 数据存储路径通过集中配置管理 (支持 QUANT_DATA_ROOT 迁移到 D 盘)
REPORT_DIR = get_institutional_pipeline_report_dir()


# ============================================================================
# 数据结构
# ============================================================================


@dataclass
class PipelineContext:
    """运行上下文"""

    mode: str = "smoke"
    symbols: list[str] = field(default_factory=list)
    total_capital: float = 3_000_000
    report_date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    output_path: Path | None = None

    def __post_init__(self) -> None:
        if self.output_path is None:
            self.output_path = REPORT_DIR / self.report_date


# ============================================================================
# Pipeline
# ============================================================================


class InstitutionalPipelineRunner(DataMixin, LGBMixin, SignalMixin):
    """机构级闭环运行器

    继承 Mixin:
    - DataMixin: 历史数据加载 + 快照 + Alpha信号构建
    - LGBMixin: LGB Walk-forward 训练 + 模型缓存
    - SignalMixin: 技术指标计算 + 多源真实信号
    """

    def __init__(self, ctx: PipelineContext | None = None):
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
        self._historical_cache: dict[str, pd.DataFrame] = {}
        # B2.2: cache 回填并发安全锁 (run_io_batch 多线程同时拉取历史数据时)
        # _cache_lock 仅保护 _historical_cache 字典读写;
        # 真正阻止并发重复拉取同一 symbol 的是 _sym_locks[symbol] (per-symbol 锁)
        self._cache_lock = threading.Lock()
        self._sym_locks: dict[str, threading.Lock] = {}
        self._sym_locks_lock = threading.Lock()  # 保护 _sym_locks 字典本身
        # LGB Walk-forward 模型缓存（内存中，不落盘）
        self._lgb_models: dict[str, dict] = {}
        if ctx.mode == "backtest":
            self._preload_historical_data()

    # ------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------

    def run(self) -> dict[str, Any]:
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
        result["steps"]["alpha_evaluation"] = (
            alpha_report.to_dict() if hasattr(alpha_report, "to_dict") else alpha_report
        )

        # Step 3: 信号融合
        fusion_signals = self._step_signal_fusion(alpha_report)
        result["steps"]["signal_fusion"] = [
            s.to_dict() if hasattr(s, "to_dict") else asdict(s) for s in fusion_signals
        ]

        # Step 4: 组合优化
        portfolio_decision = self._step_portfolio_optimization(fusion_signals)
        result["steps"]["portfolio_decision"] = (
            portfolio_decision.to_dict() if hasattr(portfolio_decision, "to_dict") else portfolio_decision
        )

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
        # BUG-01 修复 (2026-07-31): 原代码 `if current_drawdown > 0` 与 DrawdownCircuitBreaker
        #   的符号约定 (负数=回撤) 冲突, 导致熔断器永远不触发:
        #   - 若 current_drawdown=-0.10 (符合约定), `> 0` 不进入, 熔断器失效
        #   - 若 current_drawdown=+0.10 (符号错误), `> 0` 进入但 evaluate(+0.10) 不触发任何级别
        #   修复: 使用 abs() 判断并规范化符号为负值
        try:
            current_drawdown_raw = float(portfolio_decision.meta.get("current_drawdown", 0.0))
            if abs(current_drawdown_raw) > 1e-9:
                # 统一为负值约定 (DrawdownCircuitBreaker 期望负数表示回撤)
                current_drawdown = -abs(current_drawdown_raw)
                dd_decision = self.drawdown_breaker.evaluate(current_drawdown)
                result["steps"]["drawdown_breaker"] = (
                    dd_decision.to_dict() if hasattr(dd_decision, "to_dict") else {"level": str(dd_decision)}
                )
                scale = dd_decision.target_scale(current_drawdown)
                if scale < 1.0:
                    logger.warning(
                        "[DrawdownBreaker] 回撤 %.2f%% 触发减仓 ×%.2f (level=%s)",
                        abs(current_drawdown) * 100,
                        scale,
                        dd_decision.level.value if hasattr(dd_decision.level, "value") else dd_decision.level,
                    )
                    portfolio_decision.target_weights = {
                        s: w * scale for s, w in portfolio_decision.target_weights.items()
                    }
                    portfolio_decision.meta["drawdown_scale"] = scale
        except Exception as e:
            logger.error("[DrawdownBreaker] 检查异常: %s", e, exc_info=True)

        # Step 4.6: 自我进化编排 (phase_evolution) — G3 ER-2.1, feature flag 控制
        self._run_evolution_phase(result)

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
            # L1+ 或 fail_closed: 阻止执行路由
            if (ks_result.get("level", 0) >= 1 or ks_result.get("fail_closed", False)) and self.ctx.mode not in (
                "smoke",
                "backtest",
            ):
                logger.warning("[Pipeline] KillSwitch L%d, 交易计划被拦截", ks_result.get("level", 0))
                result["status"] = "blocked_by_kill_switch"
                self._save(result)
                return result
        except Exception as e:
            logger.error("[KillSwitch] 集成异常: %s", e, exc_info=True)
            result["steps"]["kill_switch"] = {"error": str(e)}

        # === BUG-05 修复 (2026-07-31): trades 与 target_weights 同步 ===
        # 问题: portfolio_decision.trades 在 _step_portfolio_optimization 后基于原始权重生成,
        #   后续 _step_market_regime_scaling / _apply_v72_bull_regime_cap / drawdown_breaker /
        #   enforce_hard_constraints 均修改 target_weights 但未同步 trades,
        #   导致 _step_execution_routing 执行的是未约束的原始权重 (风险约束被绕过).
        # 修复: 在执行路由前, 根据最新 target_weights 重建 trades.
        try:
            sync_info = self._regenerate_trades_from_weights(portfolio_decision)
            result["steps"]["trades_sync"] = sync_info
            if sync_info.get("regenerated"):
                logger.warning(
                    "[Pipeline] trades 已重建: %d → %d (原因: target_weights 被风险约束修改)",
                    sync_info.get("old_count", 0),
                    sync_info.get("new_count", 0),
                )
        except Exception as e:
            logger.error("[Pipeline] trades 重建异常: %s", e, exc_info=True)
            result["steps"]["trades_sync"] = {"error": str(e)}

        # GLM-5.2 C2(#22) 修复: 重建 trades 后须重新应用 KillSwitch L1 约束
        # 提取至 utils.killswitch_guard.apply_killswitch_l1_filter (独立模块, 可单测)
        result = apply_killswitch_l1_filter(portfolio_decision, ks_result, result)

        # Step 6: 执行路由
        execution_plans = self._step_execution_routing(portfolio_decision, fusion_signals)
        result["steps"]["execution_plans"] = [p.to_dict() for p in execution_plans]

        # Step 6.6: ETF期权对冲再平衡 (phase_rebalance) — G3 ER-2.2, feature flag 控制
        self._run_rebalance_phase(result, portfolio_decision)

        # Step 6.5: AI EOD 复盘 (phase_review) — v86 集成 W35, feature flag 控制
        self._run_eod_review_phase(result, portfolio_decision)

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

        # Step 7: 盘后报告生成 (phase_report) — v86 集成 W35
        self._run_report_phase(result)

        self._save(result)
        logger.info("[Pipeline] 运行完成: %s", result["status"])
        return result

    # ------------------------------------------------------------
    # Step 1: 数据门控（真实数据）
    # ------------------------------------------------------------

    def _step_data_gate(self) -> dict[str, Any]:
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

    def _build_sector_map(self) -> dict[str, str]:
        """板块映射（用于板块集中度硬约束）。

        覆盖全部 23 个 POSITION_SYMBOLS + 历史标的, 未知标的规定为 unknown。
        修复: 2026-07-24 发现 688981/000425/600089 等 11 个标的缺失,
              导致它们逃脱 25% 板块集中度硬约束 (2025-09 科技板块实际达 29.69%)。
        """
        return {
            # === 消费 ===
            "600519": "消费",
            "000858": "消费",
            # === 金融/银行 ===
            "601318": "金融",
            "000001": "金融",
            "600036": "金融",
            "601398": "金融",
            "600016": "金融",
            "601166": "金融",
            "600000": "金融",
            "512880": "金融",
            "512800": "金融",
            # === 医药 ===
            "600276": "医药",
            "300760": "医药",
            "002594": "医药",
            "002422": "医药",
            "512170": "医药",
            # === 科技/半导体 (25% 硬上限, 项目硬约束) ===
            "000063": "科技",
            "688041": "科技",
            "300308": "科技",
            "002371": "科技",
            "603019": "科技",
            "688981": "科技",  # 688981 中芯国际 修复缺失
            "588000": "科技",
            "588080": "科技",
            "512760": "科技",
            # === 制造 ===
            "688017": "制造",
            "000425": "制造",
            "600089": "制造",  # 修复缺失
            "000680": "制造",
            "000333": "制造",  # 修复缺失
            # === 资源 ===
            "600219": "资源",
            "600019": "资源",  # 修复缺失
            "000408": "资源",
            "000975": "资源",  # 修复缺失
            # === 新能源 ===
            "300274": "新能源",
            "515030": "新能源",
            # === 顺周期 ===
            "601088": "顺周期",
            # === 防御 ===
            "600900": "防御",
            "512890": "防御",
            # === 红利 ===
            "515180": "红利",  # 修复缺失
            # === 黄金/避险 ===
            "518880": "黄金",
            # === 宽基ETF ===
            "510300": "宽基",
            "510050": "宽基",
            "510500": "宽基",
            "159915": "宽基",
            "512100": "宽基",
        }

    def _step_alpha_evaluation(self) -> Any:
        logger.info("[Pipeline] Step 2: Alpha 评估")
        # LGB Walk-forward 训练（仅 backtest 模式，消除前视偏差）
        if self.ctx.mode == "backtest" and _HAS_LGB:
            lgb_result = self._lgb_walkforward_train()
            logger.info(
                "[Pipeline] LGB 训练: trained=%d failed=%d skipped=%d",
                lgb_result["trained"],
                lgb_result["failed"],
                lgb_result["skipped"],
            )
        # 顶级对冲基金整改：优先使用真实历史因子 IC；数据不足时回退 mock，
        # 但必须明确标记 provenance，禁止把 mock 结果冒充有效回测。
        real_eval = self._real_alpha_evaluation()
        provenance = real_eval.get("category", "mock")
        active = real_eval.get("active_factors", 0)
        logger.info("[Pipeline] Alpha provenance=%s active_factors=%d", provenance, active)
        if provenance == "mock":
            logger.warning("[Pipeline] 真实 alpha 不可用，回退 mock；该结果不得作为有效回测/收益证据")
        return real_eval

    def _real_alpha_evaluation(self) -> dict[str, Any]:
        """Alpha 评估：LGB Walk-forward 模型 CV IC + 动量 IC 混合。

        有 LGB 模型的标的使用 CV IC（OOS 指标，无前视偏差）；
        无 LGB 模型的标的回退到 60 日动量 IC（基于真实历史行情）。
        """
        evaluations: list[dict[str, Any]] = []
        active = 0
        lgb_count = 0
        mom_count = 0

        # === 第一轮: LGB 模型（有模型的标的） ===
        symbols_needing_momentum: list[str] = []
        for symbol in self.ctx.symbols:
            if self._lgb_models:
                model_info = self._lgb_models.get(symbol)
                if model_info:
                    cv_metrics = model_info.get("cv_after_selection", {})
                    ic = float(cv_metrics.get("mean_ic", 0.0))
                    std_ic = float(cv_metrics.get("std_ic", 0.1))
                    ic_ir = ic / (std_ic + 1e-9)
                    evaluations.append(
                        {
                            "factor_name": f"lgb_{symbol}",
                            "ic_1d": ic,
                            "ic_ir": float(ic_ir),
                            "category": "real",
                            "symbol": symbol,
                        }
                    )
                    active += 1
                    lgb_count += 1
                    continue
            symbols_needing_momentum.append(symbol)

        # === 第二轮: 动量 IC（无 LGB 模型的标的） ===
        if symbols_needing_momentum and self.data_provider is not None:
            import pandas as pd

            cutoff = pd.Timestamp(self.ctx.report_date).normalize()
            # BUG 修复 (2026-08-01): cutoff 必须强制 tz-naive, 否则与
            # tz-naive 的 s.index 比较会抛 "Cannot compare tz-naive and tz-aware
            # timestamps", 导致动量 IC 全部失败, alpha=mock.
            if hasattr(cutoff, "tz") and cutoff.tz is not None:
                cutoff = cutoff.tz_localize(None)
            for symbol in symbols_needing_momentum:
                try:
                    df = self._historical_cache.get(symbol)
                    if df is None:
                        df = self.data_provider.get_historical_data(symbol, period="5y")
                    if df is None or df.empty or "close" not in df.columns or len(df) < 120:
                        continue
                    s = df["close"].dropna()
                    if hasattr(s.index, "tz") and s.index.tz is not None:
                        s.index = s.index.tz_localize(None)
                    s = s[s.index <= cutoff]
                    if len(s) < 120:
                        continue
                    factor = s.pct_change(60).shift(1)  # 60日动量因子 (t-1时刻)
                    fwd = s.pct_change(20).shift(-20)  # 20日未来收益标签: (close[t+20]-close[t])/close[t]
                    joined = pd.concat([factor, fwd], axis=1).dropna()
                    joined.columns = ["factor", "fwd"]
                    if len(joined) < 30:
                        continue
                    ic = float(joined["factor"].corr(joined["fwd"]))
                    if not np.isfinite(ic):
                        continue
                    ic_ir = ic / (joined["factor"].std() + 1e-9)
                    evaluations.append(
                        {
                            "factor_name": f"mom60_{symbol}",
                            "ic_1d": ic,
                            "ic_ir": float(ic_ir),
                            "category": "real",
                            "symbol": symbol,
                        }
                    )
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

    def _step_signal_fusion(self, alpha_report: Any) -> list[FusionSignal]:
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

        # 保存 alpha 信号报告到 reports/pipeline/ (供 DriftShadowIntegrator 读取)
        self._save_alpha_signals_report(alpha_signals)

        fused = self.signal_fusion.fuse(
            alpha_signals,
            llm_signals=llm_signals,
            etf_signals=etf_signals,
            macro_signals=macro_signals,
        )
        # FusedSignalV2 → FusionSignal 兼容转换
        # (下游 _step_portfolio_optimization / _step_execution_routing 期望 FusionSignal 接口)
        return [
            FusionSignal(
                symbol=s.symbol,
                strength=s.strength,
                confidence=float(s.meta.get("confidence", 0.5)) if hasattr(s, "meta") else 0.5,
                source="post_mix_v2",
            )
            for s in fused
        ]

    # ------------------------------------------------------------
    # Step 4: 组合优化
    # ------------------------------------------------------------

    def _step_portfolio_optimization(self, signals: list[FusionSignal]) -> PortfolioDecision:
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
        len(symbols)
        # 高价值资产集成 (2026-08-10): Ledoit-Wolf 收缩协方差替代硬编码对角协方差
        # USE_LW_COV 默认开启（确定性改进），失败时 fail-open 回退对角矩阵（与现状一致）
        cov = self._build_covariance(symbols)
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

        # 高价值资产集成 (2026-08-10): Black-Litterman 影子模式（Feature Flag, 默认关闭）
        # 仅观测对比 BL 权重 vs 现有权重, 不下达任何生产决策; 异常时 fail-open 静默跳过
        if os.environ.get("USE_BL_SHADOW", "0") == "1" and _HAS_BL and _HAS_LW:
            self._run_bl_shadow(symbols, expected_returns, cov, decision.target_weights)

        return decision

    # ------------------------------------------------------------
    # 高价值资产集成辅助方法 (2026-08-10)
    # ------------------------------------------------------------

    def _build_covariance(self, symbols: list[str]) -> "pd.DataFrame":
        """构建组合协方差矩阵。

        优先使用 Ledoit-Wolf 收缩估计（基于 price_history 收益率），
        ``USE_LW_COV`` 关闭或不可用时 fail-open 回退到硬编码对角矩阵（与历史行为一致）。
        """
        n = len(symbols)
        diag_cov = pd.DataFrame(np.diag(np.full(n, 0.04 / 252)), index=symbols, columns=symbols)
        if not _HAS_LW or os.environ.get("USE_LW_COV", "1") == "0":
            return diag_cov
        try:
            returns = self._load_returns_matrix(symbols)
            if returns is None or returns.shape[1] < 2:
                logger.debug("[LW] 收益率数据不足, 回退对角协方差")
                return diag_cov
            est = LedoitWolfCovariance()
            cov = est.fit(returns).cov_shrunk
            # 对齐到 symbols 顺序（LW 内部已按列对齐，此处防御性校验）
            if cov.shape == (n, n):
                # 数值正定性校验: 对角线必须全 > 0, 否则优化器不稳定, fail-open 回退
                if np.all(np.diag(cov) > 0):
                    return pd.DataFrame(cov, index=symbols, columns=symbols)
                logger.debug("[LW] 协方差对角含非正元素, 回退对角协方差")
            else:
                logger.debug("[LW] 协方差维度不匹配 (%s vs %s), 回退对角协方差", cov.shape, (n, n))
            return diag_cov
        except Exception as e:  # noqa: BLE001
            logger.warning("[LW] 收缩协方差估计失败, fail-open 回退对角矩阵: %s", e)
            return diag_cov

    def _load_returns_matrix(self, symbols: list[str]) -> "np.ndarray | None":
        """从 config/price_history.jsonl 构造收益率矩阵（按 symbols 顺序对齐）。

        与 signal_fusion 共用同一历史缓存文件。数据缺失的标的用 0 收益率填充，
        确保矩阵维度与 symbols 一致（LW 要求完整 N×T 矩阵）。
        """
        # 优先项目内 config (与 signal_fusion 共用历史缓存), 回退 output_path 派生路径
        candidates = [
            BASE_DIR / "config" / "price_history.jsonl",
            Path(self.ctx.output_path).parent.parent / "config" / "price_history.jsonl",
        ]
        hist_path = next((p for p in candidates if p.exists()), None)
        if hist_path is None:
            return None
        try:
            import json as _json

            price_map: dict[str, list[float]] = {}
            with open(hist_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = _json.loads(line)
                    code = rec.get("code") or rec.get("symbol")
                    prices = rec.get("prices") or rec.get("close")
                    if code and prices:
                        price_map[str(code)] = list(prices)
            if not price_map:
                return None
            series: list[np.ndarray] = []
            for s in symbols:
                prices = price_map.get(s, price_map.get(s[2:] if len(s) > 2 else s))
                if not prices or len(prices) < 3:
                    # 缺失标的: 填极小噪声收益率 (避免方差=0 导致 LW 对角线非正)
                    # 噪声标准差 1e-4 << 正常标的 ~1e-2, 保证缺失标的权重被自然压低
                    series.append(np.random.default_rng(hash(s) & 0xFFFFFFFF).normal(0.0, 1e-4, 30))
                else:
                    arr = np.asarray(prices, dtype=float)
                    rets = arr[1:] / arr[:-1] - 1.0
                    series.append(rets)
            # 统一长度到最短序列（截断尾部，避免前视）
            min_len = min(len(s) for s in series)
            if min_len < 2:
                return None
            aligned = np.column_stack([s[-min_len:] for s in series])
            return aligned
        except Exception as e:  # noqa: BLE001
            logger.debug("[LW] 读取 price_history 失败: %s", e)
            return None

    def _run_bl_shadow(
        self,
        symbols: list[str],
        expected_returns: dict[str, float],
        cov: "pd.DataFrame",
        base_weights: dict[str, float],
    ) -> None:
        """运行 BL 影子对比（观测路径 fail-open）。

        计算 BL 后验权重并与现有组合优化器权重做偏差对比，落盘影子报告。
        不修改任何生产决策；任何异常均静默跳过，不阻断主链路。
        """
        try:
            bl = BlackLittermanOptimizer(risk_aversion=2.5, tau=0.05)
            cov_np = cov.to_numpy() if hasattr(cov, "to_numpy") else np.asarray(cov)
            result = bl.run_shadow(
                assets=symbols,
                expected_returns=expected_returns,
                cov_matrix=cov_np,
                max_weight=DEFAULT_MAX_WEIGHT,
                min_weight=0.0,
            )
            bl_w = dict(zip(symbols, (float(x) for x in result.optimal_weights)))
            self._write_shadow_report(symbols, base_weights, bl_w, result)
            logger.info(
                "[BL-Shadow] 影子对比完成, 平均权重偏差=%.4f",
                float(np.mean([abs(bl_w.get(s, 0.0) - base_weights.get(s, 0.0)) for s in symbols])),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[BL-Shadow] 影子模式计算失败, fail-open 跳过: %s", e)

    def _write_shadow_report(
        self,
        symbols: list[str],
        base_weights: dict[str, float],
        bl_weights: dict[str, float],
        bl_result: "object",
    ) -> None:
        """落盘 BL 影子对比报告（不污染主报告）。

        报告路径: 每日报告归档/<date>/shadow_bl_report.md
        注意: 日志/内容避免 ¥ 符号, 用 CNY/RMB 替代 (Windows GBK 编码坑)。
        """
        out_dir = self.ctx.output_path
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path = out_dir / "shadow_bl_report.md"
        lines = ["# Black-Litterman 影子对比报告", "", f"- 日期: {self.ctx.report_date}", f"- 标的数: {len(symbols)}", ""]
        lines.append("| 标的 | 现有权重 | BL权重 | 偏差 |")
        lines.append("|------|---------|--------|------|")
        for s in symbols:
            bw = base_weights.get(s, 0.0)
            blw = bl_weights.get(s, 0.0)
            lines.append(f"| {s} | {bw:.4f} | {blw:.4f} | {blw - bw:+.4f} |")
        lines.append("")
        try:
            lines.append(f"- BL 预期组合收益: {bl_result.expected_portfolio_return:.4f}")
            lines.append(f"- BL 预期组合波动: {bl_result.expected_portfolio_vol:.4f}")
            lines.append(f"- BL 夏普比率: {bl_result.sharpe_ratio:.4f}")
            lines.append(f"- BL 有效持仓数: {bl_result.effective_n:.2f}")
        except Exception:  # noqa: BLE001
            pass
        lines.append("")
        lines.append("> 本报告仅为 BL 影子观测, 不下达任何生产决策。切换需经观察期 (建议 20 交易日) 达标后由 USE_BL_SHADOW 常开。")
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            logger.info("[BL-Shadow] 影子报告已落盘: %s", report_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("[BL-Shadow] 影子报告落盘失败: %s", e)

    # ------------------------------------------------------------
    # Step 4.5: 市场状态调节 (大盘趋势过滤)
    # ------------------------------------------------------------
    # 动机: 2022 熊市回测显示策略仓位 89% 未减仓, 导致回撤 16.29% > 15% 上限
    # 方案: 用沪深300ETF(510300)的 MA60 判断大盘状态, 熊市按比例缩减仓位
    # ------------------------------------------------------------

    _MARKET_PROXY_SYMBOL = "510300"  # 沪深300ETF, 大盘代理
    _MA_PERIOD = 60  # 60日均线, 中期趋势
    _MA_SLOPE_WINDOW = 5  # MA斜率观察窗口(5日变化)
    # 波动率+短期动量过滤层 (解决 MA60 滞后导致熊市满仓问题)
    # 动机: 2022-07 和 2024-12 回测显示, MA60 仍为 bull 但市场已急跌,
    #        导致 factor=1.0 全仓亏损 -6.71% / -6.02%。需快层信号补充。
    _VOL_LOOKBACK = 20  # 20日实现波动率窗口
    _VOL_HIGH_THRESHOLD = 0.015  # 日波动率>1.5%(~24%年化)视为高风险
    _MOM_LOOKBACK = 20  # 20日短期动量窗口
    _MOM_CRASH_THRESHOLD = -0.05  # 20日收益<-5%视为崩盘信号
    _MOM_SEVERE_CRASH = -0.10  # 20日收益<-10%视为严重崩盘
    _MIN_FACTOR_FLOOR = 0.4  # factor最低下限 (V2: 0.3→0.4, 避免过度减仓)

    def _load_market_proxy_data(self, cutoff: pd.Timestamp) -> tuple:
        """加载大盘代理数据并截断到回测日 (杜绝前视偏差)。

        Args:
            cutoff: 截断日期

        Returns:
            (df, min_required) 元组, df 为 None 表示数据不可用
        """
        df = self._load_base_cache(self._MARKET_PROXY_SYMBOL)
        if df is None or df.empty:
            # 回退到 data_provider (截断到回测日)
            if self.data_provider is not None:
                try:
                    df = self.data_provider.get_historical_data(self._MARKET_PROXY_SYMBOL, period="3y")
                except Exception as e:  # noqa: BLE001
                    logger.exception(f"获取市场代理历史数据失败, 已降级 df=None: {e}")
                    df = None
        if df is None or df.empty:
            return None, 0

        # 时区清理 + 截断到回测日 (杜绝前视偏差)
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df = df.sort_index()
        df = df[df.index <= cutoff]
        min_required = max(self._MA_PERIOD + self._MA_SLOPE_WINDOW, self._VOL_LOOKBACK, self._MOM_LOOKBACK)
        return df, min_required

    @staticmethod
    def _classify_trend_regime(latest_close: float, latest_ma: float, ma_rising: bool) -> tuple:
        """第一层: MA60 中期趋势分类。

        Args:
            latest_close: 最新收盘价
            latest_ma: 最新 MA60
            ma_rising: MA60 是否上行

        Returns:
            (regime, base_factor) 元组
        """
        above_ma = latest_close > latest_ma
        if above_ma and ma_rising:
            return "bull", 1.0
        if above_ma and not ma_rising:
            return "choppy", 0.8
        if not above_ma and ma_rising:
            return "rebound", 0.6
        return "bear", 0.5

    def _compute_vol_override(self, close: pd.Series) -> tuple:
        """第二层: 波动率过滤。

        Args:
            close: 收盘价序列

        Returns:
            (vol_override, recent_vol, vol_flag) 元组
        """
        daily_rets = close.pct_change()
        recent_vol = float(daily_rets.tail(self._VOL_LOOKBACK).std())
        vol_override = 0.8 if recent_vol > self._VOL_HIGH_THRESHOLD else 1.0
        vol_flag = "high" if vol_override < 1.0 else "normal"
        return vol_override, recent_vol, vol_flag

    def _compute_mom_override(self, close: pd.Series) -> tuple:
        """第三层: 短期动量过滤 (最快层, 急跌保护)。

        Args:
            close: 收盘价序列

        Returns:
            (mom_override, mom_20d, mom_flag) 元组
        """
        if len(close) > self._MOM_LOOKBACK:
            mom_20d = float(close.iloc[-1] / close.iloc[-1 - self._MOM_LOOKBACK] - 1)
        else:
            mom_20d = 0.0
        if mom_20d < self._MOM_SEVERE_CRASH:
            return 0.4, mom_20d, "severe_crash"
        if mom_20d < self._MOM_CRASH_THRESHOLD:
            return 0.6, mom_20d, "crash"
        return 1.0, mom_20d, "normal"

    def _apply_momentum_reversal(self, decision: dict, regime: str) -> dict:
        """V5优化: 动量反转调整 (bear/rebound regime下, 超跌加仓, 超涨减仓)。

        方案: bear/rebound regime下, 根据20日收益率调整权重
          - 20日收益 < -10% → ×1.3 (超跌加仓30%, 博反弹)
          - 20日收益 > 10%  → ×0.7 (超涨减仓30%, 锁定利润)
          - 归一化保持总仓位不变

        Args:
            decision: PortfolioDecision 对象
            regime: 市场状态 ("bull"/"choppy"/"rebound"/"bear")

        Returns:
            defensive_info dict (无调整时为空字典)
        """
        if regime not in ("bear", "rebound") or not self._historical_cache:
            return {}

        # 计算各标的 20 日收益率
        symbol_rets: dict = {}
        for symbol in decision.target_weights:
            df_sym = self._historical_cache.get(symbol)
            if df_sym is not None and len(df_sym) > 20:
                try:
                    close_sym = df_sym["close"].sort_index()
                    if len(close_sym) > 20:
                        ret_20d = float(close_sym.iloc[-1] / close_sym.iloc[-1 - 20] - 1)
                        symbol_rets[symbol] = ret_20d
                except Exception:
                    logger.exception("[Pipeline] 计算 20日收益率失败 symbol=%s", symbol)

        if not symbol_rets:
            return {}

        # 根据收益率分类调整
        adjustments: dict = {}
        n_oversold = n_overbought = 0
        for symbol, _w in decision.target_weights.items():
            ret_20d = symbol_rets.get(symbol, 0.0)
            if ret_20d < -0.10:  # 超跌: 20日跌幅>10%
                adjustments[symbol] = 1.3  # 加仓30%
                n_oversold += 1
            elif ret_20d > 0.10:  # 超涨: 20日涨幅>10%
                adjustments[symbol] = 0.7  # 减仓30%
                n_overbought += 1
            else:
                adjustments[symbol] = 1.0

        if n_oversold == 0 and n_overbought == 0:
            return {}

        # 归一化保持总仓位不变
        pre_adjust_exposure = sum(decision.target_weights.values())
        adjusted = {s: w * adjustments[s] for s, w in decision.target_weights.items()}
        total_adj = sum(adjusted.values())
        if total_adj > 0:
            decision.target_weights = {
                s: w / total_adj * pre_adjust_exposure for s, w in adjusted.items()
            }
        logger.info(
            "[Reversal] regime=%s: 超跌加仓%d只, 超涨减仓%d只",
            regime, n_oversold, n_overbought,
        )
        return {
            "n_oversold_increased": n_oversold,
            "n_overbought_reduced": n_overbought,
            "symbol_rets_20d": {s: round(r, 4) for s, r in symbol_rets.items()},
        }

    def _step_market_regime_scaling(self, decision: dict) -> tuple:
        """根据大盘趋势状态缩减/恢复仓位 (三层过滤)。

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
        # 1. 计算截断日期 (时区清理)
        cutoff = pd.Timestamp(self.ctx.report_date).normalize()
        try:
            if hasattr(cutoff, "tz") and cutoff.tz is not None:
                cutoff = cutoff.tz_localize(None)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"cutoff 时区清理失败, 已降级处理: {e}")
            cutoff = pd.Timestamp(cutoff).tz_localize(None) if pd.Timestamp(cutoff).tzinfo else pd.Timestamp(cutoff)

        # 2. 加载大盘代理数据
        df, min_required = self._load_market_proxy_data(cutoff)
        if df is None or df.empty:
            logger.warning("[MarketRegime] %s 数据不可用, 跳过趋势过滤 (factor=1.0)", self._MARKET_PROXY_SYMBOL)
            return decision, {
                "symbol": self._MARKET_PROXY_SYMBOL,
                "factor": 1.0,
                "regime": "unknown",
                "reason": "data_unavailable",
            }
        if len(df) < min_required:
            logger.warning(
                "[MarketRegime] %s 数据不足 (%d 行 < %d), 跳过",
                self._MARKET_PROXY_SYMBOL, len(df), min_required,
            )
            return decision, {
                "symbol": self._MARKET_PROXY_SYMBOL,
                "factor": 1.0,
                "regime": "insufficient_data",
                "reason": "history_too_short",
            }

        # 3. 计算 MA60 和斜率
        close = df["close"]
        ma = close.rolling(self._MA_PERIOD).mean()
        latest_close = float(close.iloc[-1])
        latest_ma_raw = ma.iloc[-1]
        if not np.isfinite(latest_ma_raw):
            logger.warning("[MarketRegime] MA值 = NaN/Inf, 数据不足, 回归 insufficient_data")
            return decision, {
                "symbol": self._MARKET_PROXY_SYMBOL,
                "factor": 1.0,
                "regime": "insufficient_data",
                "reason": "ma_nan",
            }
        latest_ma = float(latest_ma_raw)
        ma_slope = float(ma.iloc[-1] - ma.iloc[-1 - self._MA_SLOPE_WINDOW]) if len(ma) > self._MA_SLOPE_WINDOW else 0.0
        ma_rising = ma_slope > 0

        # 4. 三层过滤
        regime, base_factor = self._classify_trend_regime(latest_close, latest_ma, ma_rising)
        vol_override, recent_vol, vol_flag = self._compute_vol_override(close)
        mom_override, mom_20d, mom_flag = self._compute_mom_override(close)

        # 5. 综合 factor (三层相乘, 最低0.3)
        factor = max(base_factor * vol_override * mom_override, self._MIN_FACTOR_FLOOR)

        # 6. 应用缩减因子到 target_weights
        original_exposure = sum(decision.target_weights.values())
        scaled_weights = {s: w * factor for s, w in decision.target_weights.items()}
        scaled_exposure = sum(scaled_weights.values())
        decision.target_weights = scaled_weights
        decision.meta["market_regime"] = regime

        # 7. V5优化: 动量反转调整
        defensive_info = self._apply_momentum_reversal(decision, regime)

        # 8. 记录元数据
        decision.meta.update({
            "regime_factor": factor,
            "market_proxy_close": latest_close,
            "market_proxy_ma60": latest_ma,
            "exposure_before": original_exposure,
            "exposure_after": scaled_exposure,
            "vol_override": vol_override,
            "mom_override": mom_override,
            "realized_vol_20d": recent_vol,
            "mom_20d": mom_20d,
        })

        logger.info(
            "[MarketRegime] %s regime=%s base=%.2f vol=%s(%.4f→%.2f) mom=%s(%+.4f→%.2f) final=%.2f exposure %.1f%%→%.1f%%",
            self._MARKET_PROXY_SYMBOL, regime, base_factor,
            vol_flag, recent_vol, vol_override,
            mom_flag, mom_20d, mom_override,
            factor, original_exposure * 100, scaled_exposure * 100,
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
                        price_data[symbol] = df["close"].copy()
                    except Exception:
                        logger.exception("[Pipeline] 提取 close 序列失败 symbol=%s", symbol)
        return self.risk_budget_engine.check_pre_trade(
            target_portfolio=decision.target_weights,
            current_positions={},  # 生产环境无存量持仓 (实盘接入后填充)
            price_data=price_data,  # P0-10: 真实价格数据, 启用 VaR 1.5% 检查
        )

    def _apply_v72_bull_regime_cap(self, decision: dict, regime_info: dict[str, Any]) -> dict[str, Any]:
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
        cutoff = self._compute_regime_cutoff()

        # === V7.1: 高波动股权重惩罚 (vol20 > 4.5% → ×0.5) ===
        penalized = self._detect_high_vol_symbols(original_weights, cutoff)
        if penalized:
            for symbol in penalized:
                original_weights[symbol] = original_weights[symbol] * _V71_BULL_VOL_PENALTY
            cap_info["v71_penalty_applied"] = True
            cap_info["penalized_symbols"] = [
                {"symbol": s, "vol20": round(v, 4), "penalty": _V71_BULL_VOL_PENALTY} for s, v in penalized.items()
            ]
            logger.info(
                "[V7.1] bull regime 高波动惩罚: %d 只股票 vol20>%.1f%% → 权重×%.1f",
                len(penalized),
                _V71_BULL_HIGH_VOL_THRESHOLD * 100,
                _V71_BULL_VOL_PENALTY,
            )

        # === V7.2: bull regime 单票上限 5% (10% → 5%) ===
        capped_symbols, capped_weights, excess_weight = self._apply_max_weight_cap(original_weights)
        if capped_symbols:
            self._redistribute_excess_weight(capped_weights, capped_symbols, excess_weight)
            decision.target_weights = capped_weights
            cap_info["v72_cap_applied"] = True
            cap_info["capped_symbols"] = capped_symbols
            logger.info(
                "[V7.2] bull regime 单票上限 5%%: 截断 %d 只股票 (释放权重 %.1f%% 按比例重分配)",
                len(capped_symbols),
                excess_weight * 100,
            )
        elif penalized:
            # 仅 V7.1 惩罚, 无 V7.2 截断, 仍需更新 weights
            decision.target_weights = original_weights

        return cap_info

    def _compute_regime_cutoff(self) -> pd.Timestamp:
        """计算 regime 分析用的 cutoff 时间戳 (去除时区)。"""
        cutoff = pd.Timestamp(self.ctx.report_date).normalize()
        try:
            if hasattr(cutoff, "tz") and cutoff.tz is not None:
                cutoff = cutoff.tz_localize(None)
        except Exception:
            logger.exception("[Pipeline] cutoff 去时区失败 raw=%r", cutoff)
        return cutoff

    def _detect_high_vol_symbols(
        self, weights: dict[str, float], cutoff: pd.Timestamp
    ) -> dict[str, float]:
        """V7.1: 识别 vol20 > 4.5% 的高波动股票。

        Returns:
            {symbol: vol20} 字典, 空字典表示无高波动股票
        """
        penalized: dict[str, float] = {}
        for symbol, w in weights.items():
            if w <= 0:
                continue
            df_sym = self._historical_cache.get(symbol) if self._historical_cache else None
            if df_sym is None or df_sym.empty or "close" not in df_sym.columns:
                continue
            vol20 = self._compute_vol20(df_sym, cutoff)
            if vol20 is not None and vol20 > _V71_BULL_HIGH_VOL_THRESHOLD:
                penalized[symbol] = vol20
        return penalized

    def _compute_vol20(self, df_sym: pd.DataFrame, cutoff: pd.Timestamp) -> float | None:
        """计算 20 日波动率 (截止 cutoff 时间), 不足返回 None。"""
        try:
            df_sym = df_sym.sort_index()
            if hasattr(df_sym.index, "tz") and df_sym.index.tz is not None:
                df_sym.index = df_sym.index.tz_localize(None)
            # BUG 修复 (2026-08-01): cutoff 强制 tz-naive, 与 df_sym.index 比较才安全.
            cutoff_naive = pd.Timestamp(cutoff)
            if hasattr(cutoff_naive, "tz") and cutoff_naive.tz is not None:
                cutoff_naive = cutoff_naive.tz_localize(None)
            df_sym = df_sym[df_sym.index <= cutoff_naive]
            if len(df_sym) < 22:
                return None
            daily_rets = df_sym["close"].pct_change().tail(20)
            return float(daily_rets.std())
        except Exception as e:  # noqa: BLE001
            logger.exception(f"计算日收益率标准差失败, 已降级返回 None: {e}")
            return None

    def _apply_max_weight_cap(
        self, weights: dict[str, float]
    ) -> tuple[list[dict[str, Any]], dict[str, float], float]:
        """V7.2: 应用单票上限 5% 截断。

        Returns:
            (capped_symbols, capped_weights, excess_weight) 元组
        """
        capped_symbols: list[dict[str, Any]] = []
        capped_weights: dict[str, float] = {}
        excess_weight = 0.0
        for symbol, w in weights.items():
            if w > _V72_BULL_REGIME_MAX_WEIGHT:
                excess_weight += w - _V72_BULL_REGIME_MAX_WEIGHT
                capped_symbols.append({"symbol": symbol, "before": round(w, 4), "after": _V72_BULL_REGIME_MAX_WEIGHT})
                capped_weights[symbol] = _V72_BULL_REGIME_MAX_WEIGHT
            else:
                capped_weights[symbol] = w
        return capped_symbols, capped_weights, excess_weight

    def _redistribute_excess_weight(
        self,
        capped_weights: dict[str, float],
        capped_symbols: list[dict[str, Any]],
        excess_weight: float,
    ) -> None:
        """将截断释放的权重按比例重分配给未超限的股票 (原地修改 capped_weights)。"""
        capped_symbol_set = {c["symbol"] for c in capped_symbols}
        non_capped_total = sum(w for s, w in capped_weights.items() if s not in capped_symbol_set)
        if non_capped_total <= 0:
            return
        for symbol in capped_weights:
            if symbol not in capped_symbol_set:
                capped_weights[symbol] += excess_weight * (capped_weights[symbol] / non_capped_total)

    def _step_kill_switch_check(self, decision: dict) -> dict[str, Any]:
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
            "level": 0,
            "can_trade": True,
            "can_open": True,
            "filtered_trades_count": len(decision.trades) if hasattr(decision, "trades") else 0,
            "blocked_trades_count": 0,
            "fail_closed": False,
        }

        if not _HAS_KILL_SWITCH:
            # BUG-06 修复 (2026-07-31): 模块加载失败时 fail-closed, 而非 fail-open
            # 原代码: level=-1, can_trade=True, can_open=True → 调用方 `level >= 2` 不触发,
            #         风控核心模块缺失却允许所有交易通过 (fail-open, 极端市场灾难性风险).
            # 修复: 视为 L3 (最高风险), fail_closed=True, 阻止全部交易.
            #       smoke/backtest 模式保留 trades (保持可测试性, 由调用方判定不阻塞).
            is_test_mode = self.ctx.mode in ("smoke", "backtest")
            if is_test_mode:
                logger.warning(
                    "[KillSwitch] 模块未加载 (测试模式, 保留 trades 不阻塞). "
                    "生产模式将 fail-closed. 请检查 utils/kill_switch.py 依赖."
                )
                result["level"] = 0
                result["note"] = "module_not_loaded_test_mode"
                return result
            logger.critical(
                "[KillSwitch] 模块未加载! fail-closed 视为 L3 (阻止全部交易). "
                "请检查 utils/kill_switch.py 依赖."
            )
            result["level"] = 3
            result["can_trade"] = False
            result["can_open"] = False
            result["fail_closed"] = True
            result["note"] = "module_not_loaded_fail_closed"
            if hasattr(decision, "trades"):
                result["blocked_trades_count"] = len(decision.trades)
                decision.trades = []
                result["filtered_trades_count"] = 0
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
                ks_level,
                result["can_trade"],
                result["can_open"],
                result["margin_usage_ratio"] * 100,
            )

            if ks_level == 0 or not hasattr(decision, "trades"):
                return result

            # L1: 过滤 BUY trades (停止新开仓)
            if ks_level == 1:
                original_count = len(decision.trades)
                decision.trades = [
                    t for t in decision.trades if str(t.get("side", "BUY")).upper() != "BUY" or t.get("change", 0) < 0
                ]
                result["blocked_trades_count"] = original_count - len(decision.trades)
                result["filtered_trades_count"] = len(decision.trades)
                logger.warning(
                    "[KillSwitch L1] 停止新开仓! 过滤 %d 笔 BUY trades (保留 %d 笔 SELL)",
                    result["blocked_trades_count"],
                    len(decision.trades),
                )

            # L2+: 阻止全部 trades
            elif ks_level >= 2:
                result["blocked_trades_count"] = len(decision.trades)
                decision.trades = []
                result["filtered_trades_count"] = 0
                logger.error(
                    "[KillSwitch L%d] 阻止全部 %d 笔 trades!",
                    ks_level,
                    result["blocked_trades_count"],
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
                "[KillSwitch] 检查异常! fail-closed 阻止全部 trades: %s",
                e,
                exc_info=True,
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

    def _regenerate_trades_from_weights(self, decision: PortfolioDecision) -> dict[str, Any]:
        """BUG-05 修复: 根据最新 target_weights 重建 trades 列表.

        背景: portfolio_decision.trades 在 optimizer.optimize() 内基于原始权重生成,
              后续 regime scaling / V7.2 cap / drawdown_breaker / enforce_hard_constraints
              修改 target_weights 后, trades 未同步, 导致执行路由使用过期权重.

        策略: 当前生产路径 current_positions={}, 故 current_weights 恒为 0,
              trades = 所有 target_weight > 0 的标的 (均为 BUY).
              未来接入实盘持仓后, 需传入 current_positions 计算 change.

        Args:
            decision: PortfolioDecision (in-place 修改 decision.trades)

        Returns:
            sync_info: {regenerated, old_count, new_count, max_diff}
        """
        if not decision.target_weights:
            return {"regenerated": False, "reason": "empty_target_weights"}

        old_trades = list(decision.trades)
        old_count = len(old_trades)

        # 提取原始 trades 中的 estimated_cost (保留冲击成本估算)
        old_cost_map = {t.get("symbol", ""): t.get("estimated_cost", 0.0) for t in old_trades if isinstance(t, dict)}

        # 重建 trades: 当前生产路径 current_weights = 0 (无存量持仓)
        # 未来接入实盘后, 应从 decision.meta 或外部持仓源获取 current_weights
        current_weights: dict[str, float] = decision.meta.get("current_weights", {}) or {}

        new_trades: list[dict[str, Any]] = []
        max_diff = 0.0
        for symbol, target_w in decision.target_weights.items():
            target_w = float(target_w)
            current_w = float(current_weights.get(symbol, 0.0))
            change = round(target_w - current_w, 6)
            if abs(change) > 1e-6:
                new_trades.append(
                    {
                        "symbol": symbol,
                        "current_weight": round(current_w, 4),
                        "target_weight": round(target_w, 4),
                        "change": change,
                        "estimated_cost": old_cost_map.get(symbol, 0.0),
                        "side": "BUY" if change > 0 else "SELL",
                    }
                )
                max_diff = max(max_diff, abs(change))

        # 检测是否实际发生变化
        old_signature = {(t.get("symbol"), t.get("change")) for t in old_trades if isinstance(t, dict)}
        new_signature = {(t.get("symbol"), t.get("change")) for t in new_trades}
        regenerated = old_signature != new_signature

        decision.trades = new_trades
        decision.meta["trades_regenerated"] = True
        decision.meta["trades_regenerated_at"] = datetime.now().isoformat()

        return {
            "regenerated": regenerated,
            "old_count": old_count,
            "new_count": len(new_trades),
            "max_diff": round(max_diff, 6),
        }

    def _step_execution_routing(
        self,
        decision: PortfolioDecision,
        signals: list[FusionSignal],
    ) -> list[ExecutionPlan]:
        logger.info("[Pipeline] Step 6: 执行路由")
        signal_map = {
            s.symbol: (s.to_dict() if hasattr(s, "to_dict") else asdict(s)) for s in signals
        }
        plans = []
        for trade in decision.trades:
            symbol = trade.get("symbol", "")
            # BUG-05: 优先使用重建后的 side 字段, 回退兼容旧格式
            side = trade.get("side") or ("BUY" if trade.get("change", 0) > 0 else "SELL")
            plan = self.execution_router.route(
                order={
                    "symbol": symbol,
                    "quantity": 0.0,
                    "side": side,
                    "notional": abs(trade.get("change", 0)) * self.ctx.total_capital,
                },
                signal=signal_map.get(symbol),
                market_state={"volatility": 0.02},
            )
            plans.append(plan)
        return plans

    # ------------------------------------------------------------
    # Step 4.6: 自我进化编排 (phase_evolution) — G3 ER-2.1
    # ------------------------------------------------------------

    def _run_evolution_phase(self, result: dict[str, Any]) -> None:
        """Step 4.6 入口: 自我进化编排, feature flag 控制, 失败不阻塞."""
        evolution_result = self._step_evolution()
        if evolution_result is not None:
            result["steps"]["evolution"] = evolution_result

    def _step_evolution(self) -> dict[str, Any] | None:
        """自我进化编排 (phase_evolution) — G3 ER-2.1.

        调用 EvolutionOrchestratorV2.run_cycle() 或 V1.run_observation_cycle()
        feature flag USE_EVOLUTION_ORCHESTRATOR 控制, 默认关闭。
        失败优雅降级, 不阻塞管道。
        """
        if self.ctx.mode == "smoke":
            return None
        try:
            from utils.infra.feature_flags import is_enabled

            if not is_enabled("USE_EVOLUTION_ORCHESTRATOR"):
                return {"status": "disabled", "reason": "USE_EVOLUTION_ORCHESTRATOR=false"}

            # 优先 V2, 降级 V1
            try:
                from utils.evolution.orchestrator import EvolutionOrchestratorV2

                orchestrator_v2 = EvolutionOrchestratorV2()
                if orchestrator_v2.enabled:
                    cycle_result = orchestrator_v2.run_cycle()
                    logger.info("[Pipeline] Step 4.6: 自我进化 (V2) 完成")
                    return cycle_result.to_dict() if hasattr(cycle_result, "to_dict") else {"status": "ok", "version": "v2"}
                logger.info("[Pipeline] Step 4.6: EvolutionOrchestratorV2 flag 关闭, 降级 V1")
            except (ImportError, ValueError, TypeError, OSError, AttributeError) as e:
                logger.warning("[Pipeline] EvolutionOrchestratorV2 不可用, 降级 V1: %s", e)

            from utils.alpha.evolution_orchestrator import EvolutionOrchestrator

            orchestrator_v1 = EvolutionOrchestrator()
            cycle_result = orchestrator_v1.run_observation_cycle()
            logger.info("[Pipeline] Step 4.6: 自我进化 (V1) 完成")
            return cycle_result
        except Exception as e:
            logger.warning("[Pipeline] Step 4.6: 自我进化失败，降级跳过: %s", e)
            return {"status": "degraded", "error": str(e)}

    # ------------------------------------------------------------
    # Step 6.6: ETF期权对冲再平衡 (phase_rebalance) — G3 ER-2.2
    # ------------------------------------------------------------

    def _run_rebalance_phase(
        self, result: dict[str, Any], portfolio_decision: PortfolioDecision
    ) -> None:
        """Step 6.6 入口: ETF期权对冲再平衡, feature flag 控制, 失败不阻塞."""
        rebalance_result = self._step_rebalance(portfolio_decision)
        if rebalance_result is not None:
            result["steps"]["eod_rebalance"] = rebalance_result

    def _step_rebalance(self, portfolio_decision: PortfolioDecision) -> dict[str, Any] | None:
        """ETF期权对冲再平衡 (phase_rebalance) — G3 ER-2.2.

        调用 ETFOptionHedgeRebalancer.run_daily_rebalance()
        feature flag USE_EOD_REBALANCE 控制, 默认关闭。
        失败优雅降级, 不阻塞管道。
        """
        if self.ctx.mode == "smoke":
            return None
        try:
            from utils.infra.feature_flags import is_enabled

            if not is_enabled("USE_EOD_REBALANCE"):
                return {"status": "disabled", "reason": "USE_EOD_REBALANCE=false"}

            from etf_option_hedge_rebalancer import ETFOptionHedgeRebalancer

            rebalancer = ETFOptionHedgeRebalancer()
            positions = self._extract_positions_for_rebalance()
            prices = self._extract_prices_for_rebalance(portfolio_decision)
            current_drawdown = float(portfolio_decision.meta.get("current_drawdown", 0.0))

            plan = rebalancer.run_daily_rebalance(
                positions=positions,
                prices=prices,
                trade_date=self.ctx.report_date,
                current_drawdown=current_drawdown,
            )
            logger.info("[Pipeline] Step 6.6: ETF期权对冲再平衡完成 (date=%s)", self.ctx.report_date)
            return plan.to_dict() if hasattr(plan, "to_dict") else {"status": "ok"}
        except Exception as e:
            logger.warning("[Pipeline] Step 6.6: 再平衡失败，降级跳过: %s", e)
            return {"status": "degraded", "error": str(e)}

    def _extract_positions_for_rebalance(self) -> dict[str, dict]:
        """从 config/positions.json 加载持仓 (fail-safe, 缺失返回空)."""
        try:
            positions_path = Path("config/positions.json")
            if positions_path.exists():
                with open(positions_path, encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError) as e:
            logger.warning("[Pipeline] 加载 positions.json 失败, 用空持仓: %s", e)
        return {}

    def _extract_prices_for_rebalance(self, portfolio_decision: PortfolioDecision) -> dict[str, float]:
        """从 portfolio_decision.meta 提取价格 (fail-safe, 缺失返回空)."""
        prices = portfolio_decision.meta.get("prices", {})
        if isinstance(prices, dict):
            return {k: float(v) for k, v in prices.items() if isinstance(v, (int, float))}
        return {}

    # ------------------------------------------------------------
    # Step 6.5 + 7: EOD 收尾阶段入口 (v86 集成 W35)
    # ------------------------------------------------------------

    def _run_eod_review_phase(
        self, result: dict[str, Any], portfolio_decision: PortfolioDecision
    ) -> None:
        """Step 6.5 入口: AI EOD 复盘, feature flag 控制, 失败不阻塞."""
        review_result = self._step_ai_eod_review(result, portfolio_decision)
        if review_result is not None:
            result["steps"]["ai_eod_review"] = review_result

    def _run_report_phase(self, result: dict[str, Any]) -> None:
        """Step 7 入口: 盘后报告生成, 失败不阻塞."""
        try:
            report_path = self._step_report_generation(result)
            result["report_path"] = str(report_path)
        except Exception as e:
            logger.error("[Pipeline] 报告生成异常: %s", e, exc_info=True)
            result["report_path"] = None

    # ------------------------------------------------------------
    # Step 6.5: AI EOD 复盘 (phase_review) — v86 集成 W35
    # ------------------------------------------------------------

    def _step_ai_eod_review(
        self, result: dict[str, Any], portfolio_decision: PortfolioDecision
    ) -> dict[str, Any] | None:
        """AI EOD 复盘 (phase_review)。

        调用 ai_decision/eod_review.py 生成 5 维度复盘报告
        (决策分布/辩论效能/风险拦截/执行质量/异常检测 + 告警)。
        feature flag AI_DECISION_INTEGRATED=1 开启, 默认关闭。
        失败优雅降级, 不阻塞管道。
        """
        if os.environ.get("AI_DECISION_INTEGRATED") != "1":
            return None
        if self.ctx.mode == "smoke":
            return None
        try:
            from ai_decision.eod_review import EODReviewGenerator

            review_gen = EODReviewGenerator()
            review_report = review_gen.generate_eod_review(self.ctx.report_date)
            logger.info("[Pipeline] AI EOD 复盘完成 (date=%s)", self.ctx.report_date)
            return review_report
        except Exception as e:
            logger.warning("[Pipeline] AI EOD 复盘失败，降级到规则复盘: %s", e)
            return {"error": str(e), "status": "degraded"}

    # ------------------------------------------------------------
    # Step 7: 盘后报告生成 (phase_report) — v86 集成 W35
    # ------------------------------------------------------------

    def _step_report_generation(self, result: dict[str, Any]) -> Path:
        """盘后报告生成 (phase_report)。

        生成 Markdown 格式的 pipeline 运行报告, 含各步骤状态/权重/风险/执行计划。
        v86 集成: AI_DECISION_INTEGRATED=1 时追加 AI 复盘 + dashboard 章节。
        """
        date = self.ctx.report_date
        mode = self.ctx.mode
        report_path = self.ctx.output_path / f"pipeline_report_{mode}_{date}.md"

        lines: list[str] = []
        lines.append(f"# 机构级量化闭环报告 — {date}")
        lines.append("")
        lines.append(
            f"> 模式: `{mode}` | 标的: {', '.join(self.ctx.symbols)} "
            f"| 资金: {self.ctx.total_capital:,.0f}"
        )
        lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        steps = result.get("steps", {})

        lines.append("## 步骤状态摘要")
        lines.append("")
        lines.append("| 步骤 | 状态 |")
        lines.append("|------|------|")
        step_names = [
            ("data_gate", "1. 数据门控"),
            ("alpha_evaluation", "2. Alpha 评估"),
            ("signal_fusion", "3. 信号融合"),
            ("portfolio_decision", "4. 组合优化"),
            ("market_regime", "4.5 市场状态"),
            ("v72_bull_regime_cap", "V7.2 Bull Cap"),
            ("drawdown_breaker", "回撤熔断"),
            ("risk_budget", "5. 风险预算"),
            ("kill_switch", "KillSwitch"),
            ("trades_sync", "trades 同步"),
            ("execution_plans", "6. 执行路由"),
            ("ai_eod_review", "6.5 AI 复盘"),
        ]
        for key, name in step_names:
            if key in steps:
                step_data = steps[key]
                if isinstance(step_data, dict):
                    status = step_data.get("status", "OK")
                elif isinstance(step_data, list):
                    status = f"{len(step_data)} 项"
                else:
                    status = "OK"
                lines.append(f"| {name} | {status} |")
        lines.append("")

        portfolio = steps.get("portfolio_decision", {})
        if isinstance(portfolio, dict) and portfolio.get("target_weights"):
            lines.append("## 目标权重")
            lines.append("")
            lines.append("| 标的 | 权重 |")
            lines.append("|------|------|")
            for sym, w in portfolio["target_weights"].items():
                lines.append(f"| {sym} | {w:.2%} |")
            lines.append("")

        risk = steps.get("risk_budget", {})
        if isinstance(risk, dict):
            lines.append("## 风险预算")
            lines.append("")
            lines.append(f"- 允许: {risk.get('allowed', 'N/A')}")
            if risk.get("portfolio_var95") is not None:
                lines.append(f"- 组合 VaR95: {risk['portfolio_var95']:.4f}")
            if risk.get("max_weight_used") is not None:
                lines.append(f"- 最大权重: {risk['max_weight_used']:.2%}")
            lines.append("")

        exec_plans = steps.get("execution_plans", [])
        if exec_plans:
            lines.append("## 执行计划")
            lines.append("")
            lines.append(f"共 {len(exec_plans)} 笔执行计划")
            lines.append("")

        self._report_ai_review_section(lines, steps.get("ai_eod_review", {}), date)
        self._report_dashboard_section(result, lines, date)

        lines.append("---")
        lines.append("*由 institutional_pipeline_runner.py 自动生成 | v8.6 EOD 闭环*")

        report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("[Pipeline] 盘后报告已生成: %s", report_path)
        return report_path

    def _report_ai_review_section(
        self, lines: list[str], review: Any, date: str
    ) -> None:
        """报告 AI 复盘章节 (降级/正常两分支)."""
        if isinstance(review, dict) and review.get("error"):
            lines.append("## AI EOD 复盘 (降级)")
            lines.append("")
            lines.append(f"降级原因: {review.get('error', 'N/A')}")
            lines.append("")
        elif isinstance(review, dict) and review:
            lines.append("## AI EOD 复盘")
            lines.append("")
            lines.append(f"- 日期: {review.get('date', date)}")
            alerts = review.get("alerts")
            if alerts is not None:
                if isinstance(alerts, list):
                    lines.append(f"- 告警数: {len(alerts)}")
                elif isinstance(alerts, dict):
                    lines.append(f"- 告警: {alerts}")
            lines.append("")

    def _report_dashboard_section(
        self, result: dict[str, Any], lines: list[str], date: str
    ) -> None:
        """报告 dashboard 章节 (v86 集成, feature flag 控制, 失败降级)."""
        if os.environ.get("AI_DECISION_INTEGRATED") != "1" or self.ctx.mode == "smoke":
            return
        try:
            from ai_decision.dashboard import DashboardGenerator

            dash_gen = DashboardGenerator()
            dash_report = dash_gen.generate_daily_dashboard(date)
            lines.append("## AI 决策看板")
            lines.append("")
            lines.append(f"- 看板已生成 (date={date})")
            dash_alerts = dash_report.get("alerts")
            if dash_alerts is not None:
                lines.append(f"- 看板告警: {dash_alerts}")
            lines.append("")
            result["steps"]["ai_dashboard"] = dash_report
        except Exception as e:
            logger.warning("[Pipeline] AI 看板生成失败，降级: %s", e)
            lines.append("## AI 决策看板 (降级)")
            lines.append("")
            lines.append(f"降级原因: {e}")
            lines.append("")

    # ------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------

    def _save(self, result: dict[str, Any]) -> None:
        """原子写 pipeline 报告（临时文件+rename，防崩溃导致文件损坏）。"""
        path = self.ctx.output_path / f"pipeline_{self.ctx.mode}.json"
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            tmp_path.replace(path)  # atomic on same fs
            logger.info("[Pipeline] 报告已保存: %s", path)
        except Exception as e:
            logger.error("[Pipeline] 保存报告失败: %s", e)


# ============================================================================
# CLI
# ============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Institutional Pipeline Runner")
    parser.add_argument("--institutional-pipeline", action="store_true", help="运行机构级量化闭环")
    parser.add_argument("--pipeline", action="store_true", help="运行金融工程闭环流水线")
    parser.add_argument("--mode", default="smoke", choices=["smoke", "backtest", "live", "dry_run"])
    parser.add_argument(
        "--symbols", nargs="*", default=["600519", "000858", "601318", "000001", "600036", "601398", "600276", "000063"]
    )
    parser.add_argument("--capital", type=float, default=3_000_000.0)
    return parser.parse_args()


def main() -> None:
    _setup_logging()  # S2修复: 仅在主入口初始化日志, 避免导入时污染
    args = parse_args()
    if args.pipeline:
        from utils.pipeline import PipelineOrchestrator
        orchestrator = PipelineOrchestrator()
        result = orchestrator.run_full_cycle(
            mode=args.mode,
            symbols=args.symbols,
        )
        logger.info(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return
    if not args.institutional_pipeline:
        return
    ctx = PipelineContext(
        mode=args.mode,
        symbols=args.symbols,
        total_capital=args.capital,
    )
    runner = InstitutionalPipelineRunner(ctx)
    result = runner.run()
    logger.info(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
