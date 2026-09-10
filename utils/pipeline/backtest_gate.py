"""
回测验证网关 (Backtest Gate)

复用系统现有 Walk-Forward / DSR / StressTest，新增过拟合检测。
验收标准: 样本外 IC > 0.03, DSR > 1.0, 四大压力场景回撤 < 15%

设计原则:
- 不过闸门不上线 — 保护实盘资金
- 复用现有基础设施，不重复造轮子
- 输出详细的拒绝原因，便于调试
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.datetime_utils import now_bj
from utils.pipeline.config import get_pipeline_config
from utils.pipeline.types import (
    AlphaSignalResult,
    BacktestGateResult,
    PipelineConfig,
    PipelineResult,
    PipelineStage,
)

logger = logging.getLogger("pipeline.backtest_gate")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# walk-forward 取数周期 (审计 item 14): 默认规格 252(train)+5(purge)+63(test)=320
# 交易日, "6m" 不够 → 用 "2y"; 数据源不支持时按可用长度自适应降规格。
WALK_FORWARD_PRICE_PERIOD = "2y"
WALK_FORWARD_MIN_SAMPLES = 120


class BacktestGate:
    """回测验证网关

    在 Alpha 信号上线前执行四道验证:
    1. Walk-Forward 回测
    2. Deflated Sharpe Ratio 检验
    3. 四大压力场景测试
    4. CRO Gate (首席风控官检查)
    """

    def __init__(self, config: PipelineConfig | None = None):
        self.config = config or get_pipeline_config()
        self._report_dir = Path(self.config.report_dir)
        self._report_dir.mkdir(parents=True, exist_ok=True)

        # 尝试导入现有模块 (类对象延迟导入, Any: 运行时可缺失, None=不可用)
        self._WalkForwardConfig: type[Any] | None = None
        self._walk_forward_evaluate: Any | None = None
        self._StressTestRunner: type[Any] | None = None
        self._StrategyEvaluator: type[Any] | None = None
        self._data_provider: Any | None = None
        # 最近一次 walk-forward 所用的组合收益序列 (供 DSR 复用, 避免重复取数)
        self._last_port_returns: pd.Series | None = None
        self._setup_imports()

    def _setup_imports(self) -> None:
        """尝试导入现有的回测/验证模块"""
        self._has_walk_forward = False
        self._has_stress_test = False
        self._has_dsr = False

        # 2026-09-10 (审计 item 14) 修复假 PASS:
        # 原实现 `from utils.alpha.purged_kfold import PurgedKFold` —— 该模块
        # **在仓库中从未存在**(只有 utils/purged_kfold.py 的纯函数版), ImportError
        # 使 `_has_purged_kfold` 恒为 False → run() 第一步 Walk-Forward 永远被跳过
        # 并按"通过"处理。现改指向真实存在的样本外验证内核。
        try:
            from utils.alpha.walk_forward import (
                WalkForwardConfig,
                walk_forward_evaluate,
            )

            self._WalkForwardConfig = WalkForwardConfig
            self._walk_forward_evaluate = walk_forward_evaluate
            self._has_walk_forward = True
        except ImportError:
            self._WalkForwardConfig = None
            self._walk_forward_evaluate = None

        try:
            from utils.stress_test_runner import StressTestRunner

            self._StressTestRunner = StressTestRunner
            self._has_stress_test = True
        except ImportError:
            self._StressTestRunner = None

        try:
            # DSR 计算 — 系统已有 Deflated Sharpe Ratio 机制
            from utils.alpha.strategy_evaluator import StrategyEvaluator

            self._StrategyEvaluator = StrategyEvaluator
            self._has_dsr = True
        except ImportError:
            self._StrategyEvaluator = None

    def run(
        self,
        signal_result: AlphaSignalResult | None,
        save_report: bool = True,
    ) -> tuple[BacktestGateResult, PipelineResult]:
        """执行回测验证

        Args:
            signal_result: Alpha 信号结果
            save_report: 是否保存报告

        Returns:
            (gate_result, result) — 验证结果 + 流水线执行结果
        """
        started_at = now_bj()
        logger.info("[回测网关] 开始验证")

        try:
            gate_result = BacktestGateResult()

            # 无信号输入 — 直接拒绝
            if signal_result is None or not signal_result.signals:
                gate_result.passed = False
                gate_result.rejection_reason = "无信号输入"
                return gate_result, self._make_result(started_at, gate_result)

            # 步骤 1: Walk-Forward 验证
            # 2026-09-10 (item 14): 闸门启用时**必须真验证**; 内核不可用即
            # fail-closed (原实现是"模块缺失 → 跳过并视为通过", 假 PASS 源头)。
            if self.config.backtest_gate_enabled:
                gate_result = self._run_walk_forward(signal_result, gate_result)
            else:
                gate_result.walk_forward_passed = True
                gate_result.details["walk_forward_status"] = "SKIPPED_BY_CONFIG"

            # 步骤 2: DSR 检验
            if self.config.backtest_gate_enabled:
                gate_result = self._run_dsr_check(signal_result, gate_result)
            else:
                gate_result.details["dsr_status"] = "SKIPPED_BY_CONFIG"

            # 步骤 3: 压力测试
            if self.config.backtest_gate_enabled:
                gate_result = self._run_stress_test(signal_result, gate_result)
            else:
                gate_result.stress_test_passed = True
                gate_result.details["stress_test_status"] = "SKIPPED_BY_CONFIG"

            # 步骤 4: 综合判定
            gate_result = self._final_judgment(gate_result)

            # 保存报告
            report_path = None
            if save_report:
                report_path = self._save_report(gate_result, signal_result)

            duration_ms = (now_bj() - started_at).total_seconds() * 1000

            result = PipelineResult(
                stage=PipelineStage.BACKTEST_GATE,
                success=gate_result.passed,
                started_at=started_at,
                completed_at=now_bj(),
                duration_ms=duration_ms,
                metrics={
                    "passed": gate_result.passed,
                    "ic": round(gate_result.ic, 4),
                    "dsr": round(gate_result.dsr, 4),
                    "sharpe": round(gate_result.sharpe, 4),
                    "max_drawdown": round(gate_result.max_drawdown, 4),
                    "walk_forward_passed": gate_result.walk_forward_passed,
                    "stress_test_passed": gate_result.stress_test_passed,
                },
                reports=[report_path] if report_path else [],
            )

            status = (
                "通过"
                if gate_result.passed
                else f"拒绝: {gate_result.rejection_reason}"
            )
            logger.info(
                f"[回测网关] {status} (IC={gate_result.ic:.4f}, DSR={gate_result.dsr:.4f})"
            )
            return gate_result, result

        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"[回测网关] 执行失败: {e}", exc_info=True)
            gate_result = BacktestGateResult(
                passed=False,
                rejection_reason=f"执行异常: {e}",
            )
            return gate_result, self._make_result(started_at, gate_result)

    # ============================================================
    # 四道验证
    # ============================================================

    def _adaptive_walk_forward_config(
        self, n_samples: int, horizon_days: int
    ) -> Any | None:
        """按可用历史长度自适应 walk-forward 规格; 不足则返回 None (fail-closed).

        默认规格 252/5/63 = 320 交易日; 历史更短时按比例降规格, 但仍要求
        purge 间隔 >= 标签 horizon (泄漏约束不因数据少而放宽)。
        """
        cfg_cls = self._WalkForwardConfig
        if cfg_cls is None or n_samples < WALK_FORWARD_MIN_SAMPLES:
            return None
        purge = max(int(horizon_days), 1)
        train = min(252, max(60, int(n_samples * 0.5)))
        test = max(20, int(n_samples * 0.2))
        while train + purge + test > n_samples and test > 10:
            test = max(10, test // 2)
        if train + purge + test > n_samples:
            return None
        return cfg_cls(
            train_days=train,
            test_days=test,
            step_days=test,
            purge_days=purge,
        )

    def _run_walk_forward(
        self, signal: AlphaSignalResult, gate: BacktestGateResult
    ) -> BacktestGateResult:
        """真实 Walk-Forward 样本外验证.

        2026-09-10 (审计 item 14) 修复: 原实现名为 Walk-Forward, 实际只对
        signal 算单窗口静态 IC/Sharpe/回撤, 并把 ``ic >= min_ic`` 当作
        "walk_forward_passed" —— 没有任何滚动切分, 也没有样本外。现改为:
          - 取更长历史, 构造信号加权组合日收益序列;
          - 按 train/purge/test 滚动切窗, 逐窗口算 Sharpe 并拼接 OOS 序列;
          - 稳定性判据见 utils.alpha.walk_forward (窗口间 Sharpe CV + 正收益窗口占比);
          - 数据不足/内核缺失/异常 → **fail-closed** (决策路径不得降级通过)。
        """
        logger.info("[回测网关] 执行 Walk-Forward 验证")
        if not self._has_walk_forward or self._walk_forward_evaluate is None:
            gate.walk_forward_passed = False
            gate.details["walk_forward_status"] = "UNVERIFIED_NO_KERNEL"
            logger.error("[回测网关] 样本外验证内核不可用 → fail-closed 拒绝通过")
            return gate
        try:
            # IC 仍为"信号 × horizon 远期收益"的秩相关 (与 WF 收益序列不同层, 保持原口径)
            gate.ic = self._estimate_ic(signal)
            prices = self._load_close_prices(
                list(signal.signals.keys()), period=WALK_FORWARD_PRICE_PERIOD
            )
            port = self._portfolio_returns(signal, prices, window_days=None)
            self._last_port_returns = port
            n_samples = len(port)
            cfg = self._adaptive_walk_forward_config(
                n_samples, int(getattr(self.config, "horizon", 5))
            )
            if cfg is None:
                gate.walk_forward_passed = False
                gate.details["walk_forward_status"] = "UNVERIFIED_INSUFFICIENT_HISTORY"
                gate.details["walk_forward_n_samples"] = n_samples
                logger.error(
                    "[回测网关] 组合收益样本 %d 不足 (下限 %d) → fail-closed 拒绝通过",
                    n_samples,
                    WALK_FORWARD_MIN_SAMPLES,
                )
                return gate

            wf = self._walk_forward_evaluate(port.to_numpy(dtype=float), config=cfg)
            gate.sharpe = round(float(wf.oos_sharpe), 4)
            gate.walk_forward_passed = bool(wf.executed and wf.is_stable)
            gate.details["walk_forward_status"] = (
                "EXECUTED" if wf.executed else "UNVERIFIED_NO_WINDOW"
            )
            wf_summary = wf.as_dict()
            wf_summary.pop("windows", None)
            gate.details["walk_forward"] = wf_summary
            gate.details["walk_forward_config"] = {
                "train_days": cfg.train_days,
                "test_days": cfg.test_days,
                "step_days": cfg.step_days,
                "purge_days": cfg.purge_days,
                "price_period": WALK_FORWARD_PRICE_PERIOD,
            }
            logger.info(
                "[回测网关] Walk-Forward: %d 窗口, OOS Sharpe=%.3f, CV=%.3f, 稳定性=%s",
                wf.n_windows,
                wf.oos_sharpe,
                wf.oos_sharpe_cv,
                wf.is_stable,
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 决策路径: fail-closed (原实现 logger.warning + 降级通过 → 假 PASS)
            gate.walk_forward_passed = False
            gate.details["walk_forward_status"] = f"ERROR: {e}"
            logger.error(
                "[回测网关] Walk-Forward 执行失败 (fail-closed): %s", e, exc_info=True
            )
        return gate

    def _run_dsr_check(
        self, signal: AlphaSignalResult, gate: BacktestGateResult
    ) -> BacktestGateResult:
        """Deflated Sharpe Ratio 检验 (真实公式, 2026-09-10 修复假 PASS).

        原实现有三处问题: ① import 的 ``StrategyEvaluator`` 只被实例化、从未用于
        计算; ② 自造公式 ``SR*sqrt(1-0.5) - sqrt(2*ln(n))/sqrt(252)`` 不属任何
        已发表口径; ③ 失败时默认 1.5, 而配置 ``min_dsr`` 默认 1.0 → 恒"通过"。
        现改用 ``utils.backtest.deflated_sharpe.deflated_sharpe_ratio``
        (Bailey & López de Prado 2014), ``gate.dsr`` 为 **raw DSR 概率 ∈ [0,1]**。
        """
        logger.info("[回测网关] 执行 DSR 检验")
        try:
            from utils.backtest.deflated_sharpe import deflated_sharpe_ratio
        except ImportError as e:
            gate.dsr = 0.0
            gate.details["dsr_status"] = "UNVERIFIED_NO_MODULE"
            logger.error("[回测网关] DSR 模块不可用 → fail-closed: %s", e)
            return gate

        try:
            port = self._last_port_returns
            if port is None or len(port) < 20:
                prices = self._load_close_prices(
                    list(signal.signals.keys()), period=WALK_FORWARD_PRICE_PERIOD
                )
                port = self._portfolio_returns(signal, prices, window_days=None)
            if port is None or len(port) < 20:
                gate.dsr = 0.0
                gate.details["dsr_status"] = "UNVERIFIED_INSUFFICIENT_DATA"
                logger.error(
                    "[回测网关] DSR 样本不足 (%d < 20) → fail-closed",
                    0 if port is None else len(port),
                )
                return gate

            n_trials = max(len(signal.signals), 1)
            res = deflated_sharpe_ratio(
                daily_returns=port.to_numpy(dtype=float).tolist(),
                n_trials=n_trials,
                risk_free_rate=0.02,
            )
            gate.dsr = round(float(res.deflated_sharpe_ratio), 4)
            gate.details["dsr_status"] = "EXECUTED"
            gate.details["dsr_verdict"] = res.verdict
            gate.details["dsr_n_trials"] = n_trials
            gate.details["dsr_n_observations"] = int(res.n_observations)
            logger.info(
                "[回测网关] DSR(raw)=%.4f (Sharpe=%.3f, n_trials=%d) → %s",
                gate.dsr,
                res.sharpe_ratio,
                n_trials,
                "PASS" if res.is_pass else "FAIL",
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 决策路径: fail-closed (原实现降级 1.5 通过 → 假 PASS)
            gate.dsr = 0.0
            gate.details["dsr_status"] = f"ERROR: {e}"
            logger.error("[回测网关] DSR 计算失败 (fail-closed): %s", e, exc_info=True)
        return gate

    def _load_stress_positions(self) -> tuple[list[dict[str, Any]], float]:
        """读取真实持仓用于压力测试 (审计 item 14 附带修复).

        原实现 ``runner.run_all_scenarios([])`` 传空持仓 → 所有场景 pnl=0 →
        回撤恒 0 → 闸门恒通过; 且读取的 ``result["max_drawdown"]`` 键**不存在**
        (真实键为 ``worst_dd``) → 又恒为 0。两处叠加构成确定性假 PASS。
        """
        try:
            from utils.stress_test_runner import build_positions_from_positions_json
        except ImportError as e:
            logger.warning("[回测网关] 无法导入压力测试持仓映射: %s", e)
            return [], 0.0
        positions_path = _PROJECT_ROOT / "config" / "positions.json"
        try:
            with open(positions_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("[回测网关] 读取 %s 失败: %s", positions_path, e)
            return [], 0.0
        positions, portfolio_value = build_positions_from_positions_json(data)
        return positions, portfolio_value

    def _run_stress_test(
        self, signal: AlphaSignalResult, gate: BacktestGateResult
    ) -> BacktestGateResult:
        """压力场景测试 (真实持仓 + 正确字段, 2026-09-10 修复假 PASS)"""
        logger.info("[回测网关] 执行压力测试")
        runner_cls = self._StressTestRunner
        if runner_cls is None:
            gate.stress_test_passed = False
            gate.details["stress_test_status"] = "UNVERIFIED_NO_KERNEL"
            logger.error("[回测网关] 压力测试内核不可用 → fail-closed 拒绝通过")
            return gate
        try:
            positions, portfolio_value = self._load_stress_positions()
            if not positions:
                gate.stress_test_passed = False
                gate.details["stress_test_status"] = "UNVERIFIED_NO_POSITIONS"
                logger.error("[回测网关] 无真实持仓可压测 → fail-closed 拒绝通过")
                return gate
            runner = runner_cls()
            result = runner.run_all_scenarios(positions, portfolio_value=portfolio_value)
            n_scenarios = len(result.get("scenarios", {}))
            if n_scenarios == 0:
                gate.stress_test_passed = False
                gate.details["stress_test_status"] = "UNVERIFIED_NO_SCENARIOS"
                logger.error("[回测网关] 压力测试 0 场景 → fail-closed 拒绝通过")
                return gate
            # 正确字段: run_all_scenarios 返回 worst_dd (原用不存在的 max_drawdown)
            worst_dd = round(abs(float(result.get("worst_dd", 0.0))), 4)
            gate.max_drawdown = worst_dd
            gate.stress_test_passed = worst_dd < self.config.max_drawdown
            gate.details["stress_test_status"] = "EXECUTED"
            gate.details["stress_test_worst_dd"] = worst_dd
            gate.details["stress_test_worst_scenario"] = result.get("worst_scenario", "")
            gate.details["stress_test_n_scenarios"] = n_scenarios
            gate.details["stress_test_unclassified_amount"] = result.get(
                "unclassified_amount", 0.0
            )
            logger.info(
                "[回测网关] 压力测试: %d 场景, 最差回撤 %.2f%% (限额 %.2f%%)",
                n_scenarios,
                worst_dd * 100,
                self.config.max_drawdown * 100,
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 决策路径: fail-closed (原实现降级通过 → 假 PASS)
            gate.stress_test_passed = False
            gate.details["stress_test_status"] = f"ERROR: {e}"
            logger.error("[回测网关] 压力测试执行失败 (fail-closed): %s", e, exc_info=True)
        return gate

    def _final_judgment(self, gate: BacktestGateResult) -> BacktestGateResult:
        """综合判定 (决策路径 — 任一未通过即拒绝)"""
        failures = []

        if gate.ic < self.config.min_ic:
            failures.append(f"IC={gate.ic:.4f} < {self.config.min_ic}")

        # 口径守卫 (2026-09-10): gate.dsr 是 raw DSR 概率 ∈ [0,1], 而历史默认
        # min_dsr=1.0 是"Score 口径"残留 (>1 对概率不可达)。若配置仍 >1, 说明口径
        # 未迁移 → fail-closed 并明确报错, 绝不放宽成"自动 /10 换算"式的静默松弛。
        min_dsr = self.config.min_dsr
        if min_dsr >= 1.0:
            failures.append(
                f"配置口径错误: min_dsr={min_dsr} 对 DSR 概率值域 [0,1] 不可达 "
                f"(请改为 0.5 或其它 <1 的阈值)"
            )
            gate.details["dsr_threshold_scale_error"] = min_dsr
        elif gate.dsr < min_dsr:
            failures.append(f"DSR(raw)={gate.dsr:.4f} < {min_dsr}")

        if not gate.walk_forward_passed:
            failures.append(
                "Walk-Forward 未通过"
                f" ({gate.details.get('walk_forward_status', 'UNKNOWN')})"
            )
        if not gate.stress_test_passed:
            failures.append(
                "压力测试未通过"
                f" ({gate.details.get('stress_test_status', 'UNKNOWN')})"
            )

        if failures:
            gate.passed = False
            gate.rejection_reason = "; ".join(failures)
        else:
            gate.passed = True

        return gate

    # ============================================================
    # 指标计算（真实历史数据驱动）
    # ============================================================

    def _get_data_provider(self) -> Any:
        """惰性获取多源数据提供器（Wind/通达信/AKShare/新浪 四级降级）"""
        if self._data_provider is None:
            try:
                from utils.data_provider import MarketDataProvider

                self._data_provider = MarketDataProvider()
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ) as e:
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.warning(f"[回测网关] 数据提供器初始化失败: {e}")
                self._data_provider = None
        return self._data_provider

    @staticmethod
    def _extract_close_series(df: Any, symbol: str) -> Any:
        """从不同数据源的 DataFrame 中提取以日期为索引的收盘价序列"""
        if df is None or df.empty:
            return None
        try:
            close_col = next(
                (c for c in ("close", "收盘", "Close", "CLOSE") if c in df.columns),
                None,
            )
            if close_col is None:
                return None
            date_col = next(
                (
                    c
                    for c in ("date", "日期", "trade_date", "datetime", "time")
                    if c in df.columns
                ),
                None,
            )
            if date_col is not None:
                idx = pd.to_datetime(df[date_col], errors="coerce")
            else:
                idx = pd.to_datetime(df.index, errors="coerce")
            series = pd.Series(df[close_col].to_numpy(dtype=float), index=idx)
            series = series[series.index.notna()].sort_index()
            return series if len(series) >= 2 else None
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.debug(f"[回测网关] 提取 {symbol} 收盘价失败: {e}")
            return None

    def _load_close_prices(
        self, symbols: list[str], period: str = "6m"
    ) -> dict[str, pd.Series]:
        """批量加载信号股票的收盘价序列.

        Args:
            symbols: 股票代码列表
            period: 取数周期。默认 "6m" (静态指标口径不变); walk-forward 需要
                252(train)+purge+test 长度, 故传更长期限 (见 WALK_FORWARD_PRICE_PERIOD)。
        """
        provider = self._get_data_provider()
        if provider is None:
            return {}
        prices: dict[str, pd.Series] = {}
        for symbol in symbols:
            try:
                df = provider.get_historical_data(symbol, period=period)
                series = self._extract_close_series(df, symbol)
                if series is not None:
                    prices[symbol] = series
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ) as e:
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.debug(f"[回测网关] 获取 {symbol} 历史行情失败: {e}")
        return prices

    @staticmethod
    def _signal_date(
        signal: AlphaSignalResult, prices: dict[str, pd.Series]
    ) -> pd.Timestamp | None:
        """确定信号基准日：优先 training_date，否则取价格序列的最后共同交易日"""
        raw = getattr(signal, "training_date", "") or ""
        if raw:
            for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
                try:
                    return pd.Timestamp(datetime.strptime(str(raw)[:10], fmt).date())
                except ValueError:
                    continue
        if prices:
            return max(s.index.max() for s in prices.values())
        return None

    def _forward_returns(
        self,
        signal: AlphaSignalResult,
        prices: dict[str, pd.Series],
        horizon: int,
    ) -> pd.Series:
        """计算各股票自信号日起 horizon 个交易日的远期收益"""
        sig_date = self._signal_date(signal, prices)
        if sig_date is None:
            return pd.Series(dtype=float)
        rets: dict[str, float] = {}
        for symbol, series in prices.items():
            hist = series[series.index <= sig_date]
            if hist.empty:
                continue
            base_date = hist.index[-1]
            future = series[series.index > base_date]
            if len(future) < horizon:
                continue  # 远期数据不足，该票不参与 IC
            rets[symbol] = float(future.iloc[horizon - 1] / hist.iloc[-1] - 1.0)
        return pd.Series(rets)

    def _portfolio_returns(
        self,
        signal: AlphaSignalResult,
        prices: dict[str, pd.Series],
        window_days: int | None = 126,
    ) -> pd.Series:
        """信号加权（多头）组合的日收益序列.

        Args:
            window_days: 只取信号日前最近 N 日 (默认 126, 静态指标口径);
                传 ``None`` 取全量可用历史 (walk-forward 需要更长序列)。
        """
        values = pd.Series(signal.signals, dtype=float)
        weights = values.clip(lower=0.0)
        if weights.sum() <= 0:
            weights = pd.Series(1.0, index=values.index)
        weights = weights / weights.sum()

        sig_date = self._signal_date(signal, prices)
        aligned: dict[str, pd.Series] = {}
        for symbol, w in weights.items():
            if w <= 0 or symbol not in prices:
                continue
            aligned[symbol] = prices[symbol].pct_change() * w
        if not aligned:
            return pd.Series(dtype=float)

        port = pd.DataFrame(aligned).sum(axis=1, min_count=max(1, len(aligned) // 2))
        port = port.dropna()
        if sig_date is not None:
            port = port[port.index <= sig_date]
        if window_days is None:
            return port
        return port.tail(window_days)

    def _estimate_ic(self, signal: AlphaSignalResult) -> float:
        """真实 IC：信号强度与 horizon 日远期收益的 Spearman 秩相关

        数据不足（<5 只有远期收益）时返回 0.0 并在 details 中标注，
        由闸门按未达标处理（fail-closed），不再使用变异系数代理。
        """
        if not signal.signals:
            return 0.0
        horizon = max(int(getattr(self.config, "horizon", 5)), 1)
        prices = self._load_close_prices(list(signal.signals.keys()))
        if not prices:
            logger.warning("[回测网关] 无可用行情数据，IC 按 0 处理")
            return 0.0
        fwd = self._forward_returns(signal, prices, horizon)
        common = [s for s in fwd.index if s in signal.signals]
        if len(common) < 5:
            logger.warning(
                f"[回测网关] 远期收益样本不足 ({len(common)} < 5)，"
                "信号日可能过于接近当前，IC 按 0 处理"
            )
            return 0.0
        sig_vals = pd.Series({s: signal.signals[s] for s in common}, dtype=float)
        ic = sig_vals.corr(fwd[common], method="spearman")
        return round(float(ic), 4) if pd.notna(ic) else 0.0

    def _estimate_sharpe(self, signal: AlphaSignalResult) -> float:
        """真实 Sharpe：信号加权组合日收益的年化夏普（近 126 交易日窗口）"""
        if not signal.signals:
            return 0.0
        prices = self._load_close_prices(list(signal.signals.keys()))
        port = self._portfolio_returns(signal, prices)
        if len(port) < 20:
            return 0.0
        sharpe = float(port.mean() / max(port.std(), 1e-10) * np.sqrt(252))
        return max(-5.0, min(5.0, round(sharpe, 4)))

    def _estimate_max_drawdown(self, signal: AlphaSignalResult) -> float:
        """真实最大回撤：信号加权组合净值曲线的最大回撤（近 126 交易日窗口）"""
        if not signal.signals:
            return 0.0
        prices = self._load_close_prices(list(signal.signals.keys()))
        port = self._portfolio_returns(signal, prices)
        if len(port) < 5:
            return 0.0
        nav = (1.0 + port).cumprod()
        drawdown = nav / nav.cummax() - 1.0
        return round(abs(float(drawdown.min())), 4)

    # ============================================================
    # 报告
    # ============================================================

    def _make_result(
        self, started_at: datetime, gate: BacktestGateResult
    ) -> PipelineResult:
        return PipelineResult(
            stage=PipelineStage.BACKTEST_GATE,
            success=gate.passed,
            started_at=started_at,
            completed_at=now_bj(),
            metrics={
                "passed": gate.passed,
                "ic": gate.ic,
                "dsr": gate.dsr,
                "rejection": gate.rejection_reason or "",
            },
        )

    def _save_report(
        self, gate: BacktestGateResult, signal: AlphaSignalResult
    ) -> str | None:
        """保存验证报告"""
        timestamp = now_bj().strftime("%Y%m%d_%H%M%S")
        path = self._report_dir / f"backtest_gate_{timestamp}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "timestamp": timestamp,
                        "passed": gate.passed,
                        "ic": gate.ic,
                        "dsr": gate.dsr,
                        "sharpe": gate.sharpe,
                        "max_drawdown": gate.max_drawdown,
                        "walk_forward_passed": gate.walk_forward_passed,
                        "stress_test_passed": gate.stress_test_passed,
                        "rejection_reason": gate.rejection_reason,
                        "n_stocks": signal.n_stocks if signal else 0,
                        "model": signal.model_name if signal else "",
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            return str(path)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            return None
