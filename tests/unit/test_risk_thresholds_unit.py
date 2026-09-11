"""test_risk_thresholds_unit.py — 风控阈值单一事实源 (Issue #13: S-1/S-2/P1-3)

覆盖要点:
    - 各段阈值从 config/risk_thresholds.yaml 正常解析 (含 capital_base, P1-2)
    - 文件缺失 → 安全默认 + from_file=False (fail-open 不抛异常)
    - 段级缺键 → 逐键补默认并记录 missing_keys
    - 类型强制 (int/float/bool)
    - 未知段 → KeyError (编程错误显式失败)
    - 与消费方默认值同值 (防漂移)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from utils import risk_thresholds
from utils.risk_thresholds import (
    DEFAULT_CAPITAL_BASE,
    DEFAULT_L2_EXECUTION,
    DEFAULT_STOP_LOSS,
    describe_sources,
    get_default_portfolio_value,
    get_factor_validation_config,
    get_max_single_pct,
    get_portfolio_protection_config,
    get_stop_loss_config,
    get_stop_loss_pct,
    get_take_profit_pct,
    resolve_config,
)


class TestResolveConfig:
    @pytest.mark.unit
    def test_all_sections_from_file(self):
        """四段阈值均应来自版本库内的唯一事实源。"""
        sources = describe_sources()
        assert set(sources) == {
            "stop_loss",
            "portfolio_protection",
            "l2_execution",
            "factor_validation",
            "capital_base",
        }
        for section, desc in sources.items():
            assert "config/risk_thresholds.yaml" in desc, (
                f"{section} 未从单一事实源读取: {desc}"
            )

    @pytest.mark.unit
    def test_missing_file_falls_back_to_defaults(self):
        """配置不可用 → 安全默认 + from_file=False, 不抛异常 (fail-open)。"""
        with patch.object(risk_thresholds, "get_config", return_value={}):
            cfg, source = resolve_config("stop_loss")
        assert cfg == DEFAULT_STOP_LOSS
        assert source.from_file is False
        assert set(source.missing_keys) == set(DEFAULT_STOP_LOSS)

    @pytest.mark.unit
    def test_partial_section_fills_missing_keys(self):
        """段内缺键 → 逐键补默认并记录 missing_keys。"""
        partial = {"stop_loss": {"stop_loss_pct": 0.10}}
        with patch.object(risk_thresholds, "get_config", return_value=partial):
            cfg, source = resolve_config("stop_loss")
        assert cfg["stop_loss_pct"] == 0.10
        assert cfg["take_profit_pct"] == DEFAULT_STOP_LOSS["take_profit_pct"]
        assert "take_profit_pct" in source.missing_keys
        assert "stop_loss_pct" not in source.missing_keys

    @pytest.mark.unit
    def test_type_coercion(self):
        """YAML 常见 str 数值应被强制为目标类型。"""
        raw = {"l2_execution": {"max_single_pct": "0.07", "default_portfolio_value": 3e6}}
        with patch.object(risk_thresholds, "get_config", return_value=raw):
            cfg, _ = resolve_config("l2_execution")
        assert cfg["max_single_pct"] == pytest.approx(0.07)
        assert isinstance(cfg["max_single_pct"], float)
        assert cfg["default_portfolio_value"] == 3_000_000

    @pytest.mark.unit
    def test_invalid_value_keeps_default(self):
        """不可解析的值 → 保留默认并告警, 不抛异常。"""
        raw = {"l2_execution": {"max_single_pct": "not-a-number"}}
        with patch.object(risk_thresholds, "get_config", return_value=raw):
            cfg, _ = resolve_config("l2_execution")
        assert cfg["max_single_pct"] == DEFAULT_L2_EXECUTION["max_single_pct"]

    @pytest.mark.unit
    def test_unknown_section_raises(self):
        """未知段属编程错误, 应显式失败。"""
        with pytest.raises(KeyError):
            resolve_config("no_such_section")

    @pytest.mark.unit
    def test_bool_coercion(self):
        raw = {"stop_loss": {"block_on_trigger": 0}}
        with patch.object(risk_thresholds, "get_config", return_value=raw):
            cfg, _ = resolve_config("stop_loss")
        assert cfg["block_on_trigger"] is False


class TestTypedAccessors:
    @pytest.mark.unit
    def test_stop_loss_accessors(self):
        assert get_stop_loss_pct() == get_stop_loss_config()["stop_loss_pct"]
        assert get_take_profit_pct() == get_stop_loss_config()["take_profit_pct"]
        # S-1 口径: 止损 8% / 止盈 15% (与熔断线 3%/5% 同源可审计)
        assert get_stop_loss_pct() == pytest.approx(0.08)
        assert get_take_profit_pct() == pytest.approx(0.15)

    @pytest.mark.unit
    def test_l2_accessors(self):
        # S-2 口径: 真实组合 200 万, 单笔 5%
        assert get_max_single_pct() == pytest.approx(0.05)
        assert get_default_portfolio_value() == pytest.approx(2_000_000.0)

    @pytest.mark.unit
    def test_portfolio_protection_defaults(self):
        cfg = get_portfolio_protection_config()
        assert cfg["initial_drawdown_stop_pct"] == pytest.approx(0.05)
        assert cfg["initial_min_samples"] >= 1

    @pytest.mark.unit
    def test_factor_validation_defaults(self):
        cfg = get_factor_validation_config()
        assert cfg["min_samples"] == 60
        assert cfg["ic_effective_threshold"] == pytest.approx(0.03)
        assert cfg["ir_effective_threshold"] == pytest.approx(0.5)
        assert cfg["score_mode"] == "signed"


class TestConsumerAlignment:
    """消费方默认值与配置同值 (防两处漂移)。"""

    @pytest.mark.unit
    def test_factor_validator_aligned_with_config(self):
        from utils.factor_research.factor_discovery import FactorValidator

        cfg = get_factor_validation_config()
        assert FactorValidator.MIN_SAMPLES == cfg["min_samples"]
        assert (
            FactorValidator.IC_EFFECTIVE_THRESHOLD == cfg["ic_effective_threshold"]
        )
        assert FactorValidator.IR_EFFECTIVE_THRESHOLD == cfg["ir_effective_threshold"]
        assert FactorValidator.SCORE_MODE == cfg["score_mode"]

    @pytest.mark.unit
    def test_defaults_cover_all_sections(self):
        for section, defaults in risk_thresholds._DEFAULTS_BY_SECTION.items():
            cfg, _ = resolve_config(section)
            assert set(cfg) == set(defaults), f"{section} 默认键集不一致"


class TestCapitalBaseSection:
    """P1-2 (2026-09-11): 资金口径单一事实源 — capital_base 段。"""

    @pytest.mark.unit
    def test_capital_base_from_file(self):
        from utils.risk_thresholds import (
            get_capital_base_config,
            get_hedge_capital,
            get_stock_etf_capital,
            get_total_capital,
        )

        cfg = get_capital_base_config()
        # 口径拍板 2026-09-11: 权威总口径 = 300 万 (证券 200w + 对冲 100w)
        assert cfg["total_capital"] == get_total_capital() == 3_000_000.0
        assert cfg["stock_etf_capital"] == get_stock_etf_capital() == 2_000_000.0
        assert cfg["hedge_capital"] == get_hedge_capital() == 1_000_000.0
        # 腿口径恒等式 (防再次腿口径混用): total = stock_etf + hedge
        assert cfg["total_capital"] == cfg["stock_etf_capital"] + cfg["hedge_capital"]
        # 与项目既有已拍板源一致 (kill_switch total_margin / system_config)
        import json

        from utils.path_config import get_project_root

        syscfg = json.loads(
            (get_project_root() / "system_config.json").read_text(encoding="utf-8")
        )
        assert syscfg["total_capital"] == cfg["total_capital"]
        assert syscfg["stock_etf_capital"] == cfg["stock_etf_capital"]
        assert syscfg["hedge_capital"] == cfg["hedge_capital"]

    @pytest.mark.unit
    def test_capital_base_missing_file_falls_back(self):
        """配置不可用 → 安全默认 (fail-open), 不抛异常。"""
        with patch.object(risk_thresholds, "get_config", return_value={}):
            cfg, source = resolve_config("capital_base")
        assert cfg == DEFAULT_CAPITAL_BASE
        assert source.from_file is False

    @pytest.mark.unit
    def test_capital_base_partial_override(self):
        """只改 total_capital 时其余键回默认 (拍板后只动一处即生效的机制保证)。"""
        raw = {"capital_base": {"total_capital": 2_741_928}}
        with patch.object(risk_thresholds, "get_config", return_value=raw):
            cfg, source = resolve_config("capital_base")
        assert cfg["total_capital"] == 2_741_928.0
        assert cfg["stock_etf_capital"] == DEFAULT_CAPITAL_BASE["stock_etf_capital"]
        assert cfg["hedge_capital"] == DEFAULT_CAPITAL_BASE["hedge_capital"]
        assert source.from_file is True

    @pytest.mark.unit
    def test_capital_base_negative_rejected_by_consumers_convention(self):
        """capital_base 为资金基数 (非风控阈值), 负值属配置错误 —
        本模块只做类型强制; 数值合法性由人工审阅 (yaml 注释已声明勿自动改)。"""
        raw = {"capital_base": {"total_capital": "not-a-number"}}
        with patch.object(risk_thresholds, "get_config", return_value=raw):
            cfg, _ = resolve_config("capital_base")
        assert cfg["total_capital"] == DEFAULT_CAPITAL_BASE["total_capital"]


class TestCapitalBaseConsumers:
    """活跃消费点全部经单一事实源 (P1-2 收敛验证)。"""

    @pytest.mark.unit
    def test_rebalance_target_total_uses_stock_etf_leg(self):
        """P1-2 口径拍板: 再平衡目标是**证券/ETF 腿**基数, 非 total。

        审查报告 §P1-2 根因 = 此前用含期货腿/计划口径的 5M → 目标高估 ~82%。
        """
        from utils.execution.rebalance_execution_orders import TARGET_TOTAL
        from utils.risk_thresholds import get_stock_etf_capital, get_total_capital

        assert TARGET_TOTAL == get_stock_etf_capital() == 2_000_000.0
        # 显式负向断言: 不得回退到 total 口径 (腿混淆防复发)
        assert TARGET_TOTAL != get_total_capital()
        assert TARGET_TOTAL != 5_000_000.0

    @pytest.mark.unit
    def test_protective_put_engine_uses_stock_etf_leg(self):
        """认沽保护的被保护对象是证券/ETF 腿 (PROTECTION_TARGETS 全为 ETF)。"""
        from utils.protective_put_engine import ProtectivePutEngine
        from utils.risk_thresholds import get_stock_etf_capital

        assert ProtectivePutEngine.TOTAL_CAPITAL == get_stock_etf_capital()

    @pytest.mark.unit
    def test_automated_execution_system_default_single_source(self):
        """默认参数为 None → 运行时取 capital_base (不再 100 万独立口径)。"""
        import inspect

        from utils.execution.automated_execution_system import AutomatedExecutionSystem

        sig = inspect.signature(AutomatedExecutionSystem.__init__)
        assert sig.parameters["total_capital"].default is None

    @pytest.mark.unit
    def test_workflow_config_single_source(self):
        # v8.3_institutional 目录用 sys.path 直导 (同 test_daily_workflow_unit 先例)
        import sys

        from utils.risk_thresholds import get_stock_etf_capital, get_total_capital

        _v83 = str(
            __import__("pathlib").Path(__file__).resolve().parents[2]
            / "v8.3_institutional"
        )
        if _v83 not in sys.path:
            sys.path.insert(0, _v83)
        import daily_workflow as dw_module  # noqa: F401  (触发模块加载)

        WorkflowConfig = dw_module.WorkflowConfig

        assert WorkflowConfig.TOTAL_CAPITAL == get_total_capital()
        assert WorkflowConfig.STOCK_CAPITAL == get_stock_etf_capital()
        # HEDGE_CAPITAL 106 万是 2026 计划排布 (21.2%), 语义不同, 不参与收敛
        assert WorkflowConfig.HEDGE_CAPITAL == 1_060_000

    @pytest.mark.unit
    def test_hedge_execution_engine_fallback_single_source(self):
        """hedge_execution_engine 兜底口径取 capital_base (meta 显式值优先不变)。"""
        import inspect

        import utils.hedge_execution_engine as hee

        src = inspect.getsource(hee)
        assert '"total_capital", get_total_capital()' in src
        assert '"hedge_capital", get_hedge_capital()' in src
        # 期权成本预算基数 = 证券/ETF 腿 (被保护组合), 口径拍板 2026-09-11
        assert '"total_capital", get_stock_etf_capital()' in src
        # 旧 5M/2M 硬编码兜底不得残留
        assert '"total_capital", 5_000_000' not in src
        assert '"hedge_capital", 2_000_000' not in src

class TestEffectiveCapitalResolution:
    """口径拍板 2026-09-11: 运行时优先序 + 腿语义 (P1-2 完整闭环)。"""

    @pytest.mark.unit
    def test_runtime_value_wins_over_static(self):
        """positions meta / 实时权益优先于静态基准, 且来源可审计。"""
        from utils.risk_thresholds import resolve_effective_capital

        value, src = resolve_effective_capital("stock_etf", runtime_value=2_741_928)
        assert value == 2_741_928.0
        assert "runtime" in src

    @pytest.mark.unit
    def test_static_fallback_when_runtime_missing(self):
        """运行时值缺失 → 回退静态基准, 来源标明配置文件。"""
        from utils.risk_thresholds import (
            get_stock_etf_capital,
            resolve_effective_capital,
        )

        value, src = resolve_effective_capital("stock_etf", runtime_value=None)
        assert value == get_stock_etf_capital()
        assert "risk_thresholds.yaml" in src

    @pytest.mark.unit
    @pytest.mark.parametrize("bad", [0, -1, 0.0, None])
    def test_non_positive_runtime_rejected(self, bad):
        """非正/None 运行时值不得被当成真实权益 (防"0 权益"静默通过)。"""
        from utils.risk_thresholds import (
            get_stock_etf_capital,
            resolve_effective_capital,
        )

        value, src = resolve_effective_capital("stock_etf", runtime_value=bad)
        assert value == get_stock_etf_capital()
        assert "runtime" not in src

    @pytest.mark.unit
    def test_unknown_leg_raises(self):
        from utils.risk_thresholds import resolve_effective_capital

        with pytest.raises(KeyError, match="未知资金腿"):
            resolve_effective_capital("not_a_leg")

    @pytest.mark.unit
    def test_leg_semantics_are_distinct(self):
        """三条腿语义不同且各有唯一基数 (防再次腿混淆 = §P1-2 根因)。"""
        from utils.risk_thresholds import resolve_effective_capital

        total, _ = resolve_effective_capital("total")
        stock, _ = resolve_effective_capital("stock_etf")
        hedge, _ = resolve_effective_capital("hedge")
        assert total == stock + hedge

    @pytest.mark.unit
    def test_rebalance_resolve_target_total_uses_runtime(self):
        """再平衡基数解析器: 运行时账本 (2.74M) 优先于静态 2M。"""
        from utils.execution.rebalance_execution_orders import resolve_target_total

        value, src = resolve_target_total(2_741_928)
        assert value == 2_741_928.0
        assert "runtime" in src

    @pytest.mark.unit
    def test_rebalance_generate_orders_rejects_non_positive_base(self):
        """非正基数 → 拒绝生成订单 (不产出不可执行/放大订单)。"""
        from utils.execution.rebalance_execution_orders import generate_rebalance_orders

        orders = generate_rebalance_orders(
            style_allocation={"科技": {"amount": 0.0, "weight": 0.0, "codes": []}},
            target_allocation={"科技": 0.15},
            positions={},
            prices={},
            target_total=0.0,
        )
        assert orders == []
