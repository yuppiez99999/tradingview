# -*- coding: utf-8 -*-
"""
v8.4 双门禁管理器
=================
为模拟盘/实盘提供两道准入门禁:

1. ReturnExpectationGate (启动前)
   - 对齐 V9 基线: 年化>=15% / 回撤<=10% / Sharpe>=1.0
   - 调用 predict_annual_return.predict_annual_return_struct() 获取结构化预测
   - 从 shadow_account_config.json 的 backtest_benchmark 读取 V9 回测基准

2. HedgeCompletenessGate (对冲后)
   - Beta+Delta 双约束: portfolio_beta<=0.30 且 |net_delta|<0.05
   - 事后校验 (基于已成交对冲订单 + SimExecutionEngine 希腊字母)

门禁生效策略:
- sim_mode: 不达标仅告警 (继续运行积累数据)
- live_mode: 不达标 fail-closed (终止工作流)
- dry_mode: 跳过门禁

配置文件: config/gate_thresholds.json
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("gate_manager")

# 路径常量 (v8.3_institutional/gate_manager.py → 项目根目录)
BASE_DIR = Path(__file__).resolve().parent.parent
GATE_CONFIG_PATH = BASE_DIR / "config" / "gate_thresholds.json"
SHADOW_CONFIG_PATH = BASE_DIR / "config" / "shadow_account_config.json"


@dataclass
class GateResult:
    """统一门禁结果数据结构"""
    gate_name: str            # "return_expectation" / "hedge_completeness"
    passed: bool
    mode: str                 # "sim" / "live" / "dry"
    action: str               # "pass" / "warn" / "block"
    metrics: Dict[str, Any] = field(default_factory=dict)
    thresholds: Dict[str, Any] = field(default_factory=dict)
    blockers: List[str] = field(default_factory=list)
    promoters: List[str] = field(default_factory=list)
    timestamp: str = ""
    recommendation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典 (用于持久化到 state)"""
        return asdict(self)


def _load_json(path: Path) -> Dict[str, Any]:
    """安全加载 JSON 配置"""
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning("加载配置失败 %s: %s", path, e)
    return {}


class ReturnExpectationGate:
    """启动前预期收益门禁 — 对齐 V9 基线

    校验项:
        - 年化收益 >= 15% (用 phase_adjusted_return 更严格)
        - 最大回撤 <= 10% (取 shadow backtest_benchmark.max_drawdown)
        - Sharpe >= 1.0
    """

    DEFAULT_THRESHOLDS: Dict[str, Any] = {
        "min_annual_return": 0.15,
        "max_drawdown": 0.10,
        "min_sharpe": 1.0,
        "phase_factor": 0.70,
        "use_phase_adjusted": True,
    }

    def __init__(
        self,
        thresholds: Optional[Dict[str, Any]] = None,
        config_path: Optional[str] = None,
        inherit_shadow: bool = True,
    ) -> None:
        """初始化门禁

        优先级: 显式 thresholds > config_path > DEFAULT_THRESHOLDS
        inherit_shadow=True 时从 shadow_account_config.json 读取 backtest_benchmark
        """
        cfg_path = Path(config_path) if config_path else GATE_CONFIG_PATH
        cfg = _load_json(cfg_path)

        # 全局 enabled 开关
        self.enabled: bool = cfg.get("enabled", True)
        self.inherit_shadow: bool = cfg.get("inherit_shadow_thresholds", inherit_shadow)

        # 阈值优先级: 显式 > config > DEFAULT
        cfg_thresholds = cfg.get("return_expectation_gate", {})
        self.thresholds: Dict[str, Any] = thresholds or cfg_thresholds or self.DEFAULT_THRESHOLDS

        # 从 shadow_account_config 继承 benchmark (单向, 只读)
        self._shadow_benchmark: Dict[str, Any] = {}
        if self.inherit_shadow:
            shadow_cfg = _load_json(SHADOW_CONFIG_PATH)
            self._shadow_benchmark = shadow_cfg.get("backtest_benchmark", {})

    def _load_prediction(self) -> Dict[str, Any]:
        """加载年化收益预测 (延迟导入避免循环依赖)"""
        try:
            from predict_annual_return import predict_annual_return_struct
            return predict_annual_return_struct()
        except Exception as e:
            logger.error("加载 predict_annual_return_struct 失败: %s", e)
            return {}

    def evaluate(self, mode: str = "sim") -> GateResult:
        """评估启动前预期收益门禁

        Args:
            mode: "sim" / "live" / "dry"

        Returns:
            GateResult, metrics 包含:
                expected_return, expected_vol, sharpe_estimate,
                phase_adjusted_return, backtest_benchmark
        """
        timestamp = datetime.now().isoformat()

        # dry 模式跳过
        if mode == "dry" or not self.enabled:
            return GateResult(
                gate_name="return_expectation",
                passed=True,
                mode=mode,
                action="skip",
                metrics={"reason": "dry 模式或门禁已禁用"},
                thresholds=self.thresholds,
                timestamp=timestamp,
                recommendation="跳过",
            )

        prediction = self._load_prediction()
        if not prediction:
            return GateResult(
                gate_name="return_expectation",
                passed=False,
                mode=mode,
                action="block" if mode == "live" else "warn",
                metrics={"error": "无法加载预测数据"},
                thresholds=self.thresholds,
                blockers=["预测数据加载失败"],
                timestamp=timestamp,
                recommendation="检查 predict_annual_return 模块",
            )

        # 决定用哪个 return 校验: phase_adjusted (建仓期更严格) 或 expected
        use_phase = self.thresholds.get("use_phase_adjusted", True)
        ar = prediction.get("phase_adjusted_return" if use_phase else "expected_return", 0.0)
        sharpe = prediction.get("sharpe_estimate", 0.0)
        expected_vol = prediction.get("expected_vol", 0.0)

        # 回撤: 优先用 shadow benchmark 的 max_drawdown (回测实测值)
        backtest_dd = float(self._shadow_benchmark.get("max_drawdown", 0.0))
        # 若 benchmark 缺失, 用 expected_vol * sqrt(12) 估算年化波动率上限作为回撤代理
        drawdown = backtest_dd if backtest_dd > 0 else expected_vol * math_sqrt(12) * 0.8

        metrics: Dict[str, Any] = {
            "expected_return": round(ar, 4),
            "expected_vol": round(expected_vol, 4),
            "sharpe_estimate": round(sharpe, 4),
            "backtest_max_drawdown": round(backtest_dd, 4),
            "backtest_annual_return": float(self._shadow_benchmark.get("annual_return", 0.0)),
            "backtest_sharpe": float(self._shadow_benchmark.get("sharpe_annual", 0.0)),
            "use_phase_adjusted": use_phase,
            "phase_adjusted_return": round(prediction.get("phase_adjusted_return", 0.0), 4),
            "raw_expected_return": round(prediction.get("expected_return", 0.0), 4),
        }

        blockers: List[str] = []
        promoters: List[str] = []

        # 校验 1: 年化收益
        min_ar = float(self.thresholds.get("min_annual_return", 0.15))
        if ar >= min_ar:
            promoters.append(f"annual_return={ar:.2%}>={min_ar:.2%}")
        else:
            blockers.append(f"annual_return={ar:.2%}<{min_ar:.2%}")

        # 校验 2: 最大回撤
        max_dd = float(self.thresholds.get("max_drawdown", 0.10))
        if drawdown <= max_dd:
            promoters.append(f"max_drawdown={drawdown:.2%}<={max_dd:.2%}")
        else:
            blockers.append(f"max_drawdown={drawdown:.2%}>{max_dd:.2%}")

        # 校验 3: Sharpe
        min_sharpe = float(self.thresholds.get("min_sharpe", 1.0))
        if sharpe >= min_sharpe:
            promoters.append(f"sharpe={sharpe:.2f}>={min_sharpe:.2f}")
        else:
            blockers.append(f"sharpe={sharpe:.2f}<{min_sharpe:.2f}")

        passed = len(blockers) == 0
        action = "pass" if passed else ("block" if mode == "live" else "warn")

        if passed:
            recommendation = "通过, 可继续运行"
        elif mode == "live":
            recommendation = "禁止接入实盘, 模型预测未达 V9 基线"
        else:
            recommendation = "sim_mode 继续运行积累数据, 暂不接入实盘"

        return GateResult(
            gate_name="return_expectation",
            passed=passed,
            mode=mode,
            action=action,
            metrics=metrics,
            thresholds=self.thresholds,
            blockers=blockers,
            promoters=promoters,
            timestamp=timestamp,
            recommendation=recommendation,
        )

    def enforce(self, result: GateResult, live_mode: bool) -> bool:
        """根据模式决定是否阻断

        Returns:
            True = 继续, False = 阻断
        """
        if result.passed:
            return True
        if live_mode:
            logger.critical("[Gate-REVENUE] fail-closed: %s", result.blockers)
            return False
        logger.warning("[Gate-REVENUE] sim_mode 告警不阻断, 继续积累数据: %s", result.blockers)
        return True


class HedgeCompletenessGate:
    """对冲完整性校验 — 事后 Beta+Delta 双约束

    校验项:
        - portfolio_beta_after <= 0.30 (对齐 HedgeExecutionEngine.TARGET_BETA)
        - |net_delta| < 0.05 (接近 Delta 中性)
    """

    DEFAULT_THRESHOLDS: Dict[str, Any] = {
        "max_portfolio_beta": 0.30,
        "max_abs_net_delta": 0.05,
    }

    # 期权 delta 估算常量 (live 模式无 sim_engine 时使用)
    _PUT_BUDGET_PER_CONTRACT = 10_000.0   # 1 万预算 ≈ 1 张 Put
    _PUT_DELTA_PER_CONTRACT = -0.4        # Put 平均 delta

    def __init__(
        self,
        thresholds: Optional[Dict[str, Any]] = None,
        config_path: Optional[str] = None,
    ) -> None:
        cfg_path = Path(config_path) if config_path else GATE_CONFIG_PATH
        cfg = _load_json(cfg_path)
        self.enabled: bool = cfg.get("enabled", True)
        cfg_thresholds = cfg.get("hedge_completeness_gate", {})
        self.thresholds: Dict[str, Any] = thresholds or cfg_thresholds or self.DEFAULT_THRESHOLDS

    def evaluate(
        self,
        portfolio_beta_before: float,
        portfolio_value: float,
        hedge_orders_executed: List[Dict[str, Any]],
        sim_engine: Optional[Any] = None,
        if_multiplier: int = 300,
        if_beta: float = 1.0,
    ) -> GateResult:
        """事后校验组合 Beta 和净 Delta

        Args:
            portfolio_beta_before: 对冲前组合 Beta (来自 coordinated["portfolio_beta"])
            portfolio_value: 组合市值 (用于归一化)
            hedge_orders_executed: 已成交对冲订单列表
            sim_engine: SimExecutionEngine 实例 (sim_mode 下提供, 用于 get_greek_exposure)
            if_multiplier: IF 合约乘数 (默认 300)
            if_beta: IF 期货 Beta (默认 1.0)

        Returns:
            GateResult, metrics 包含 portfolio_beta_after, net_delta 等
        """
        timestamp = datetime.now().isoformat()

        if not self.enabled:
            return GateResult(
                gate_name="hedge_completeness",
                passed=True,
                mode="sim" if sim_engine is not None else "live",
                action="skip",
                metrics={"reason": "门禁已禁用"},
                thresholds=self.thresholds,
                timestamp=timestamp,
                recommendation="跳过",
            )

        # === 1. 计算对冲后组合 Beta ===
        if_hedge_notional = 0.0
        if_contracts = 0
        for order in hedge_orders_executed:
            action = order.get("action", "")
            if action == "SHORT_FUTURES":
                contracts = int(order.get("contracts", 0) or 0)
                price = float(order.get("price", order.get("futures_price", order.get("est_price", 0))) or 0)
                if_hedge_notional += contracts * price * if_multiplier
                if_contracts += contracts

        if portfolio_value > 0:
            portfolio_beta_after = portfolio_beta_before - \
                                   (if_hedge_notional * if_beta) / portfolio_value
        else:
            portfolio_beta_after = portfolio_beta_before

        # === 2. 计算净 Delta ===
        # 注意: portfolio_beta_after 已包含期货对冲效应
        #   (portfolio_beta_after = before - if_hedge_notional * if_beta / portfolio_value)
        # 所以 net_delta = (权益+期货综合 delta) + 期权 delta, 不再单独减 futures_delta
        #
        # 2a. 期权 Delta: sim_mode 从 sim_engine 取, live 从 hedge_orders 估算
        options_delta = 0.0
        options_delta_source = "unknown"
        if sim_engine is not None and hasattr(sim_engine, "get_greek_exposure"):
            try:
                greek = sim_engine.get_greek_exposure() or {}
                options_delta = float(greek.get("delta", 0.0) or 0.0)
                options_delta_source = "sim_engine"
            except Exception as e:
                logger.warning("get_greek_exposure 失败, 降级到估算: %s", e)
                options_delta = self._estimate_options_delta_from_orders(hedge_orders_executed)
                options_delta_source = "estimate_after_sim_fail"
        else:
            options_delta = self._estimate_options_delta_from_orders(hedge_orders_executed)
            options_delta_source = "estimate_from_orders"

        # 2b. 期货 Delta (元单位, 仅作信息展示, 不参与 net_delta 计算)
        futures_delta_notional = -if_hedge_notional * if_beta

        # 2c. 权益+期货综合 delta (元, 已含期货对冲)
        equity_futures_delta = portfolio_beta_after * portfolio_value

        # 2d. 净 Delta 归一化到 portfolio_value
        #    net_delta = (权益+期货 delta + 期权 delta) / portfolio_value
        #             = portfolio_beta_after + options_delta / portfolio_value
        options_delta_normalized = options_delta / max(portfolio_value, 1.0)
        net_delta = portfolio_beta_after + options_delta_normalized

        metrics: Dict[str, Any] = {
            "portfolio_beta_before": round(portfolio_beta_before, 4),
            "portfolio_beta_after": round(portfolio_beta_after, 4),
            "if_hedge_notional": round(if_hedge_notional, 0),
            "if_contracts": if_contracts,
            "options_delta": round(options_delta, 2),
            "options_delta_source": options_delta_source,
            "futures_delta_notional": round(futures_delta_notional, 2),
            "equity_futures_delta": round(equity_futures_delta, 2),
            "options_delta_normalized": round(options_delta_normalized, 4),
            "net_delta": round(net_delta, 4),
            "portfolio_value": portfolio_value,
        }

        # === 3. 双约束校验 ===
        blockers: List[str] = []
        promoters: List[str] = []

        max_beta = float(self.thresholds.get("max_portfolio_beta", 0.30))
        if portfolio_beta_after <= max_beta:
            promoters.append(f"portfolio_beta={portfolio_beta_after:.3f}<={max_beta}")
        else:
            blockers.append(f"portfolio_beta={portfolio_beta_after:.3f}>{max_beta}")

        max_delta = float(self.thresholds.get("max_abs_net_delta", 0.05))
        if abs(net_delta) < max_delta:
            promoters.append(f"|net_delta|={abs(net_delta):.4f}<{max_delta}")
        else:
            blockers.append(f"|net_delta|={abs(net_delta):.4f}>={max_delta}")

        passed = len(blockers) == 0
        mode = "sim" if sim_engine is not None else "live"
        action = "pass" if passed else ("block" if mode == "live" else "warn")

        if passed:
            recommendation = "对冲完整, 风险敞口已充分覆盖"
        elif mode == "live":
            recommendation = f"对冲缺口, 禁止后续实盘操作: {blockers}"
        else:
            recommendation = f"sim_mode 对冲缺口告警, 继续积累数据: {blockers}"

        return GateResult(
            gate_name="hedge_completeness",
            passed=passed,
            mode=mode,
            action=action,
            metrics=metrics,
            thresholds=self.thresholds,
            blockers=blockers,
            promoters=promoters,
            timestamp=timestamp,
            recommendation=recommendation,
        )

    def _estimate_options_delta_from_orders(self, hedge_orders: List[Dict[str, Any]]) -> float:
        """从 BUY_PUT 类订单估算期权 Delta (live 模式或 sim_engine 失败时降级使用)

        估算: budget / 1万 × Put delta (-0.4)
        """
        total_delta = 0.0
        for order in hedge_orders:
            action = order.get("action", "")
            if action in ("BUY_PUT_SPREAD", "BUY_BARE_PUT", "BUY_EMERGENCY_PUT", "BUY_PUT"):
                budget = float(order.get("budget", 0) or 0)
                contracts = budget / self._PUT_BUDGET_PER_CONTRACT
                total_delta += contracts * self._PUT_DELTA_PER_CONTRACT * self._PUT_BUDGET_PER_CONTRACT
        return total_delta

    def enforce(self, result: GateResult, live_mode: bool) -> bool:
        """根据模式决定是否阻断

        Returns:
            True = 继续, False = 阻断
        """
        if result.passed:
            return True
        if live_mode:
            logger.critical("[Gate-HEDGE] fail-closed: %s", result.blockers)
            return False
        logger.warning("[Gate-HEDGE] sim_mode 告警不阻断: %s", result.blockers)
        return True


# 辅助: 避免 import math 在模块顶部 (仅在 _load_prediction 失败路径用)
def math_sqrt(x: float) -> float:
    """延迟 import math 的 sqrt 包装"""
    import math
    return math.sqrt(x)


def get_gate_status_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    """从 DailyWorkflow.state 提取门禁状态摘要 (供报告使用)

    Args:
        state: DailyWorkflow.state

    Returns:
        {"pre_launch": {...}, "hedge_completeness": {...}, "all_passed": bool}
    """
    pre = state.get("pre_launch_gate", {})
    hedge_gate = state.get("phases", {}).get("hedge", {}).get("hedge_completeness_gate", {})

    pre_passed = pre.get("passed", True) if pre else True
    hedge_passed = hedge_gate.get("passed", True) if hedge_gate else True
    fail_closed = state.get("fail_closed", False)

    return {
        "pre_launch": {
            "passed": pre_passed,
            "action": pre.get("action", "skip"),
            "blockers": pre.get("blockers", []),
            "metrics": pre.get("metrics", {}),
        },
        "hedge_completeness": {
            "passed": hedge_passed,
            "action": hedge_gate.get("action", "skip"),
            "blockers": hedge_gate.get("blockers", []),
            "metrics": hedge_gate.get("metrics", {}),
        },
        "all_passed": pre_passed and hedge_passed and not fail_closed,
        "fail_closed": fail_closed,
        "fail_closed_reason": state.get("fail_closed_reason", ""),
    }
