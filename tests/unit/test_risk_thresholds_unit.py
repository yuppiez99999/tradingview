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
        # 期权成本预算基数 = 证券/ETF 腿 (被保护组合), 口径拍板 2026-09-11;
        # 09-12 修正: 预算基数经 _resolve_stock_etf_budget_base() 腿口径解析
        # (meta.total_capital 旧头按腿占比折算, 不再直接采信), 静态基准兜底同源
        assert "_resolve_stock_etf_budget_base" in src
        # 折算必须使用静态腿占比, 且折算路径带 WARNING (不静默)
        assert "static_leg / static_total" in src
        assert "已按腿占比" in src
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


class TestStopLossAutoLiquidateSection:
    """S-1 自动平仓授权段 (Issue #13, 2026-09-12) —— 三层配置链路。"""

    @pytest.mark.unit
    def test_default_is_disabled(self):
        """未显式授权时不得启用 (默认关 = 无自动下单路径)。"""
        assert risk_thresholds.get_stop_loss_auto_liquidate_config()["enabled"] is False

    @pytest.mark.unit
    def test_nested_section_survives_coercion(self):
        """嵌套 dict 段经 _coerce 后逐键保留且类型正确 (自动平仓授权全量字段)。"""
        cfg = risk_thresholds.get_stop_loss_auto_liquidate_config()
        assert cfg["scope"] == "stop_loss_only"
        assert cfg["allow_take_profit"] is False
        assert cfg["held_positions_only"] is True
        assert cfg["reduce_only"] is True
        assert cfg["max_liquidations_per_symbol_per_day"] == 1
        assert cfg["exempt_from_daily_quota"] is True
        assert cfg["exempt_from_single_trade_limit"] is True
        assert cfg["max_exec_retries"] == 2
        assert cfg["escalate_on_failure"] is True

    @pytest.mark.unit
    def test_nested_section_reachable_from_stop_loss_config(self):
        """嵌套段必须同时可从 get_stop_loss_config() 取到 (消费方口径一致)。"""
        cfg = risk_thresholds.get_stop_loss_config()
        assert isinstance(cfg["auto_liquidate"], dict)
        assert cfg["auto_liquidate"]["enabled"] is False

    @pytest.mark.unit
    def test_unknown_nested_keys_are_ignored_with_warning(self):
        """未知键丢弃并告警 —— 防配置漂移时静默引入未消费开关。"""
        nested_default = {
            "enabled": False,
            "scope": "stop_loss_only",
        }
        merged = risk_thresholds._coerce(
            nested_default, {"enabled": True, "bogus_switch": True}
        )
        assert merged["enabled"] is True
        assert "bogus_switch" not in merged

    @pytest.mark.unit
    def test_non_mapping_nested_value_falls_back_to_default(self):
        """嵌套段被写成非映射 (如字符串) → 沿用默认, 不炸链路。"""
        merged = risk_thresholds._coerce({"auto_liquidate": {"enabled": False}}, {"auto_liquidate": "oops"})
        assert merged["auto_liquidate"] == {"enabled": False}

    @pytest.mark.unit
    def test_missing_nested_section_uses_module_default(self):
        """文件缺该段时用模块内默认 (fail-open), 且默认必须是关闭。"""
        with patch.object(risk_thresholds, "get_config", return_value={}):
            cfg = risk_thresholds.get_stop_loss_auto_liquidate_config()
        assert cfg["enabled"] is False
        assert cfg["scope"] == "stop_loss_only"


class TestNestedCoerceRegression:
    """嵌套 _coerce 不得破坏既有扁平段行为 (回归护栏)。"""

    @pytest.mark.unit
    def test_flat_sections_unchanged(self):
        cfg = risk_thresholds.get_l2_config()
        assert isinstance(cfg["max_single_pct"], float)
        assert float(cfg["default_portfolio_value"]) == 2000000

    @pytest.mark.unit
    def test_stop_loss_flat_keys_still_typed(self):
        cfg = risk_thresholds.get_stop_loss_config()
        assert isinstance(cfg["stop_loss_pct"], float)
        assert cfg["block_on_trigger"] is True


def _load_root_hedge_execution_orders():
    """显式按路径加载**根目录** hedge_execution_orders.py (5 参 build_orders)。

    必要性: ms_strategy/scripts/ 下存在同名模块 (3 参旧签名副本),
    二者同名同 sys.modules 键 —— 测试执行顺序不同会解析到不同副本
    (pre-existing 测试隔离缺陷)。本测试必须锁定根目录生产版, 故显式
    importlib 按文件路径加载, 不依赖 sys.path 顺序。
    """
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "hedge_execution_orders.py"
    spec = importlib.util.spec_from_file_location("_root_heo_sc19", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCapitalBaseResidualHardcodes:
    """SC-19~24 (2026-09-12): capital_base 消费点复验 — 清除 P1-2 统一时漏改的残留。

    背景: 审查报告 §05 缺口 1「capital_base 消费点逐点复验」把
    rebalance_order_executor / alpha_hedge_engine / institutional_pipeline_runner
    列为未逐行过的三项。本轮复验这三项**本身合规**, 但顺着同一张消费面
    扫出 6 处**漏网硬编码** (SC-19~24) —— 均为 P1-2 口径统一时改了主路径、
    漏了兜底路径/邻近文件。

    本测试组把「静态数字」变成「断言」: 任何回退到 5M/3M 旧口径的写法即红。
    """

    FORBIDDEN_OLD_CALIBER = ("5_000_000", "5000000")

    @pytest.mark.unit
    def test_sc19_hedge_orders_empty_portfolio_fail_closed(self):
        """SC-19: hedge_execution_orders 空组合市值 → 拒绝生成对冲单 (原回退 5M)。

        原缺陷实测: 空持仓 + beta=0.8 + hedge_pct=0.4 → 仍产出 IF 空头 1 张
        (名义 ~114 万), 即对**不存在的敞口**做空 = 裸空敞口。现应 fail-closed。
        """
        heo = _load_root_hedge_execution_orders()

        plan = {"action": "HEDGE", "portfolio_beta": 0.8, "total_hedge_pct": 0.4}
        result = heo.build_orders(plan, {}, {}, None, {})

        assert result["orders"] == [], "空组合市值不得生成任何对冲单"
        assert result["action"] == "NO_HEDGE"
        assert result["degraded"] is True
        assert result["degraded_reason"] == "portfolio_value_unavailable"

    @pytest.mark.unit
    def test_sc19_hedge_orders_real_positions_still_hedge(self):
        """SC-19 正向: 真实持仓 (有价) 仍正常生成对冲单 —— 修复不得误伤主路径。"""
        heo = _load_root_hedge_execution_orders()

        plan = {"action": "HEDGE", "portfolio_beta": 0.8, "total_hedge_pct": 0.4}
        result = heo.build_orders(plan, {"510300": 1_000_000}, {"510300": 2.0}, None, {})

        assert result.get("degraded") is not True
        assert result["orders"], "有真实市值时必须产出对冲单"
        # 目标基数 = 真实市值 200 万, 而非 5M 旧口径
        # (5M 口径下 IF 名义会达 114 万 × 0.5/0.4 量级; 这里只断言非退化)
        assert result["portfolio_beta"] == 0.8

    @pytest.mark.unit
    def test_sc20_daily_hedge_update_delegates_portfolio_value(self):
        """SC-20: daily_hedge_update 不再硬编码 portfolio_value=5M, 传 None 自算。"""
        from pathlib import Path

        # 用源码级断言而非 import: 该模块依赖 requests (沙箱可能缺失),
        # 且本断言的对象就是源码文本本身, 源码级更直接且不引入运行时依赖。
        src = (
            Path(__file__).resolve().parents[2] / "daily_hedge_update.py"
        ).read_text(encoding="utf-8")
        # 只断言调用点, 避开注释中引用旧值的历史说明
        assert "portfolio_value=5_000_000.0," not in src
        assert "portfolio_value=None," in src

    @pytest.mark.unit
    def test_sc21_launch_shadow_account_fallback_single_source(self):
        """SC-21: launch_shadow_account 缺 capital_config 时取 capital_base (非 5M)。"""
        import inspect

        import launch_shadow_account as lsa

        src = inspect.getsource(lsa)
        assert 'capital_config.get("total_capital", 5_000_000)' not in src
        assert "_get_total_capital()" in src

    @pytest.mark.unit
    def test_sc22_next_day_planner_fallback_single_source(self):
        """SC-22: next_day_planner / generate_daily_report 的资金兜底同源。"""
        import inspect

        from reporting import next_day_planner

        src = inspect.getsource(next_day_planner)
        assert '"total_capital", 5000000' not in src
        assert '"stock_etf_capital", 3000000' not in src
        assert "get_total_capital()" in src
        assert "get_stock_etf_capital()" in src

    @pytest.mark.unit
    def test_sc23_run_daily_eod_nav_fallback_single_source(self):
        """SC-23: run_daily_eod NAV 兜底同源 + 兜底时显式告警。

        原缺陷: peak/current 同时回退同一个 5M ⇒ 比值恒 1 ⇒ 回撤判定恒 0 级。
        """
        import inspect

        import run_daily_eod

        src = inspect.getsource(run_daily_eod)
        assert '_nav_fallback = _get_stock_etf_capital()' in src
        assert '"initial_capital", 5_000_000' not in src
        assert '"daily_nav", 5_000_000' not in src
        # 必须显式告警, 不得静默用静态基准冒充真实 NAV
        assert "回撤判定不可信" in src

    @pytest.mark.unit
    def test_sc24_calc_2030_fallback_single_source(self):
        """SC-24: scripts/calc_2030_annual_return 兜底同源 (非 3M/5M)。"""
        import inspect
        import sys
        from pathlib import Path

        _scripts = str(Path(__file__).resolve().parents[2] / "scripts")
        if _scripts not in sys.path:
            sys.path.insert(0, _scripts)
        import calc_2030_annual_return as c30

        src = inspect.getsource(c30)
        assert 'meta.get("total_capital", 5000000)' not in src
        assert 'meta.get("stock_etf_capital", 3000000)' not in src
        assert "get_total_capital()" in src
        assert "get_stock_etf_capital()" in src
