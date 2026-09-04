"""策略编排器 (ComboOrchestrator).

多策略协同编排: 策略路由 (按市场状态选策略) -> 多策略并行生成 ->
订单包合并 -> 滚仓调度 -> 状态持久化.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .calendar_spread import CalendarSpreadEngine
from .cash_secured_put import CashSecuredPutEngine
from .collar import CollarEngine
from .combo_base import ComboBase, ComboResult, OptionChainFetcher, StrategyType
from .combo_risk_manager import ComboRiskManager
from .combo_state import ComboStateManager
from .covered_call import CoveredCallEngine
from .vertical_spread import VerticalSpreadEngine

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "etf_option_combo.yaml"

_STRATEGY_MAP = {
    StrategyType.COVERED_CALL: "covered_call",
    StrategyType.COLLAR: "collar",
    StrategyType.CASH_SECURED_PUT: "cash_secured_put",
    StrategyType.VERTICAL_SPREAD: "vertical_spread",
    StrategyType.CALENDAR_SPREAD: "calendar_spread",
}


class ComboOrchestrator:
    """策略编排器 — 多策略协同统一入口."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        total_capital: float = 2_000_000,
        data_layer: object | None = None,
    ) -> None:
        self._config_path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        self._config = self._load_config()
        self._total_capital = self._config.get("total_capital", total_capital)

        self._validate_config()

        self._chain_fetcher = OptionChainFetcher(data_layer=data_layer)
        self._state_manager = ComboStateManager()
        self._risk_manager = ComboRiskManager(
            total_capital=self._total_capital,
            config=self._config.get("risk_control", {}),
        )

        self._engines = self._build_engines()
        logger.info("ComboOrchestrator初始化: %d 策略引擎", len(self._engines))

    def _load_config(self) -> dict:
        """加载组合策略配置 (fail-fast).

        配置缺失/不可读/语法错误/结构非法一律抛 RuntimeError, 由上层调用方
        (CLI 入口 / Streamlit UI) 显式处理并提示, 绝不静默使用默认配置运行 —
        避免与用户预期配置脱钩后带错参数建仓。
        """
        try:
            import yaml
        except ImportError as e:
            raise RuntimeError(
                f"ETF期权联动配置加载失败: 缺少 pyyaml 依赖 ({e})"
            ) from e

        try:
            with open(self._config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as e:
            raise RuntimeError(
                f"ETF期权联动配置不可读: {self._config_path} ({e})"
            ) from e
        except yaml.YAMLError as e:
            raise RuntimeError(
                f"ETF期权联动配置语法错误: {self._config_path} ({e})"
            ) from e

        combo = (cfg or {}).get("combo_strategies", {})
        if not isinstance(combo, dict):
            raise RuntimeError(
                f"ETF期权联动配置结构非法: {self._config_path} 缺少 combo_strategies 映射"
            )
        return combo

    def _validate_config(self) -> None:
        for st_key in ("covered_call", "collar", "cash_secured_put"):
            sc = self._config.get(st_key, {})
            otm = sc.get("otm_pct", 0.05)
            if not 0.0 <= otm <= 0.20:
                raise ValueError(f"{st_key}.otm_pct={otm} 越界 [0, 0.20]")
            dte_min = sc.get("dte_min", 30)
            dte_max = sc.get("dte_max", 60)
            if dte_min < 10:
                raise ValueError(f"{st_key}.dte_min={dte_min} < 10")
            if dte_max > 180:
                raise ValueError(f"{st_key}.dte_max={dte_max} > 180")

    def _build_engines(self) -> dict[StrategyType, ComboBase]:
        engines: dict[StrategyType, object] = {}
        cfg = self._config
        tc = self._total_capital

        cc_cfg = {**cfg.get("covered_call", {}), "total_capital": tc}
        engines[StrategyType.COVERED_CALL] = CoveredCallEngine(
            cc_cfg, self._chain_fetcher, self._risk_manager, None, self._state_manager,
        )

        collar_cfg = {**cfg.get("collar", {}), "total_capital": tc}
        engines[StrategyType.COLLAR] = CollarEngine(
            collar_cfg, self._chain_fetcher, self._risk_manager, None, self._state_manager,
        )

        csp_cfg = {**cfg.get("cash_secured_put", {}), "total_capital": tc}
        engines[StrategyType.CASH_SECURED_PUT] = CashSecuredPutEngine(
            csp_cfg, self._chain_fetcher, self._risk_manager, None, self._state_manager,
        )

        vs_cfg = {**cfg.get("vertical_spread", {}), "total_capital": tc}
        engines[StrategyType.VERTICAL_SPREAD] = VerticalSpreadEngine(
            vs_cfg, self._chain_fetcher, self._risk_manager, None, self._state_manager,
        )

        cal_cfg = {**cfg.get("calendar_spread", {}), "total_capital": tc}
        engines[StrategyType.CALENDAR_SPREAD] = CalendarSpreadEngine(
            cal_cfg, self._chain_fetcher, self._risk_manager, None, self._state_manager,
        )
        return engines

    def run_all(
        self,
        underlyings: list[str] | None = None,
        market_state: dict | None = None,
        spot_positions: dict | None = None,
    ) -> dict[str, list[ComboResult]]:
        """全标的全策略运行 — 返回 {标的: [ComboResult, ...]}."""
        if underlyings is None:
            underlyings = self._config.get("enabled_underlyings", [])

        strategies = self.route_strategy(market_state or {})
        results: dict[str, list[ComboResult]] = {}

        for underlying in underlyings:
            pos = (spot_positions or {}).get(underlying, {})
            results[underlying] = []
            for st in strategies:
                engine = self._engines.get(st)
                if engine is None:
                    continue
                try:
                    result = engine.generate(underlying, pos, market_state or {})
                    results[underlying].append(result)
                except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
                    logger.error("策略 %s @ %s 执行异常: %s", st.value, underlying, e)
                    results[underlying].append(ComboResult(
                        strategy_type=st, underlying=underlying, orders=(),
                        greeks=engine._empty_greeks(), net_premium=0.0,
                        budget_remaining=0.0, error_code="EXECUTION_ERROR",
                        error_msg=str(e), generated_at="",
                    ))
        return results

    def monitor(self) -> dict:
        """全组合监控扫描 — Greeks/保证金/行权风险/Theta衰减."""
        return self._risk_manager.monitor([], {})

    def roll_all(self) -> list:
        """扫描DTE≤5的策略实例执行滚仓."""
        roll_results = []
        for st, engine in self._engines.items():
            try:
                rr = engine.roll()
                if rr.needs_roll:
                    roll_results.append(rr)
            except (ValueError, TypeError, AttributeError) as e:
                logger.warning("滚仓检查异常 %s: %s", st.value, e)
        return roll_results

    def route_strategy(self, market_state: dict) -> list[StrategyType]:
        """按市场状态映射策略组合."""
        regime = market_state.get("regime", "calm")
        routing = self._config.get("strategy_routing", {})
        regime_cfg = routing.get(regime, routing.get("calm", {}))
        strategy_names = regime_cfg.get("strategies", ["covered_call"])

        name_to_type = {v: k for k, v in _STRATEGY_MAP.items()}
        return [name_to_type[name] for name in strategy_names if name in name_to_type]

    def get_portfolio_snapshot(self) -> dict:
        """各策略持仓/Greeks/盈亏/剩余预算/距到期天数."""
        state = self._state_manager.load()
        snapshot = {
            "strategy_instances": len(state.get("strategy_instances", {})),
            "budgets": state.get("budgets", {}),
            "last_updated": state.get("last_updated", ""),
        }
        return snapshot
