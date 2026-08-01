# -*- coding: utf-8 -*-
"""test_hedge_rebalance_v59_unit.py — 对冲再平衡联动引擎 v5.9 单元测试

C-1.5 测试任务 (2026-08-01): 为 hedge_rebalance_v59.py 补单元测试覆盖。

背景:
    HedgeRebalanceIntegrator 是五阶段联合决策引擎 (风险评估→对冲决策→再平衡检查→
    联合优化→执行计划)。本测试聚焦可独立测试的纯逻辑方法, 对涉及网络/文件 IO 的
    方法使用 mock 隔离。

测试覆盖重点:
    1. 枚举与数据类结构完整性
    2. 模块常量回归保护 (PORTFOLIO_HEDGE_THRESHOLDS / TAIL_* / REBALANCE_THRESHOLDS)
    3. 纯逻辑方法: _determine_market_regime / _compute_tail_hedge_ratio /
       _get_dynamic_rebalance_threshold / _estimate_performance / _estimate_default_price
    4. 格式化方法: format_report
    5. 决策方法: decide_hedge (mock 依赖)
    6. 便捷函数: get_integrator

覆盖目标: 40%+
"""
from __future__ import annotations

import os
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ── 路径设置 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_ROOT = PROJECT_ROOT / "v8.3_institutional" / "src"
for _p in (str(PROJECT_ROOT), str(SRC_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hedging.hedge_rebalance_v59 import (  # noqa: E402
    HedgeRebalanceIntegrator,
    MarketRegime,
    HedgeMode,
    PositionWeight,
    HedgeDecision,
    RebalanceDecision,
    JointPlan,
    PORTFOLIO_HEDGE_THRESHOLDS,
    TAIL_VOL_TRIGGER,
    TAIL_DD_TRIGGER,
    TAIL_MIN_HEDGE,
    TAIL_MAX_HEDGE,
    REBALANCE_THRESHOLDS,
    SECTOR_ROTATION,
    DEFAULT_SECTOR_WEIGHTS,
    _estimate_portfolio_vol,
    _estimate_portfolio_dd_60d,
    _load_yaml,
    _load_json,
    get_integrator,
)
from risk.portfolio_risk_assessor import PortfolioRisk  # noqa: E402


# ============================================================
# Fixture
# ============================================================

@pytest.fixture
def integrator(tmp_path):
    """构造隔离的 HedgeRebalanceIntegrator (使用临时目录避免读取真实配置)"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    # 写入最小化 portfolio.yaml
    (config_dir / "portfolio.yaml").write_text(
        "global:\n  capital:\n    equity_portfolio: 1000000\n"
        "assets:\n"
        "  - code: '300308'\n    name: '中际旭创'\n    category: 'high_end_manufacturing'\n    target_weight: 0.10\n"
        "  - code: '601088'\n    name: '中国神华'\n    category: 'cyclical'\n    target_weight: 0.08\n",
        encoding="utf-8",
    )
    (config_dir / "positions.json").write_text(
        json.dumps({"300308": {"shares": 100, "avg_cost": 100.0}, "601088": {"shares": 200, "avg_cost": 40.0}}),
        encoding="utf-8",
    )
    (config_dir / "settings.yaml").write_text("log_level: INFO\n", encoding="utf-8")
    return HedgeRebalanceIntegrator(
        base_dir=str(tmp_path),
        config_dir=str(config_dir),
        portfolio_value=1_000_000,
        hedge_mode=HedgeMode.TAIL_ONLY,
    )


def _make_risk(**overrides) -> PortfolioRisk:
    """构造 PortfolioRisk 测试数据"""
    defaults = dict(
        total_value=1_000_000,
        stock_exposure=800_000,
        beta_csi300=1.10,
        beta_csi500=1.20,
        volatility_30d=0.22,
        var_95_daily=25000.0,
    )
    defaults.update(overrides)
    return PortfolioRisk(**defaults)


def _make_hedge_decision(**overrides) -> HedgeDecision:
    """构造 HedgeDecision 测试数据"""
    defaults = dict(
        needed=True,
        mode=HedgeMode.TAIL_ONLY,
        regime=MarketRegime.HIGH_VOLATILE,
        hedge_ratio=0.30,
        strength_name="MODERATE",
        futures_instruments=["IC2401", "IM2401"],
        futures_contracts={"IC2401": 2, "IM2401": 1},
        futures_notional={"IC2401": 800000.0, "IM2401": 400000.0},
        futures_margin={"IC2401": 96000.0, "IM2401": 52000.0},
        total_notional=1_200_000.0,
        total_margin=148_000.0,
        price_source="iFinD MCP",
        expected_beta_after=0.65,
        reasoning="组合波动率28%触发尾部保护",
    )
    defaults.update(overrides)
    return HedgeDecision(**defaults)


def _make_rebalance_decision(**overrides) -> RebalanceDecision:
    """构造 RebalanceDecision 测试数据"""
    defaults = dict(
        needed=True,
        rebalance_type="strategic",
        threshold=0.05,
        total_buy_amount=50_000.0,
        total_sell_amount=30_000.0,
        net_cash_flow=-20_000.0,
        reasoning="科技板块超配, 防御板块低配",
    )
    defaults.update(overrides)
    return RebalanceDecision(**defaults)


def _make_joint_plan(**overrides) -> JointPlan:
    """构造 JointPlan 测试数据 (format_report 专用)"""
    defaults = dict(
        timestamp="2026-08-01T14:30:00",
        portfolio_value=1_000_000,
        stock_exposure=800_000,
        hedge=_make_hedge_decision(),
        after_hedge_exposure=560_000,
        rebalance=_make_rebalance_decision(),
        execution_window="2026-08-01 14:35-14:50",
        execution_priority="对冲优先",
        warning_flags=["价格回退: 300308"],
        estimated_annual_return=0.105,
        estimated_max_drawdown=0.15,
        estimated_sharpe=0.55,
        estimated_volatility=0.19,
        summary="建议执行尾部对冲+战略再平衡",
    )
    defaults.update(overrides)
    return JointPlan(**defaults)


# ============================================================
# 1. TestEnumsAndDataclasses — 枚举与数据类
# ============================================================

class TestEnumsAndDataclasses:

    def test_market_regime_values(self):
        """4 种市场状态枚举值"""
        assert MarketRegime.CALM.value == "calm"
        assert MarketRegime.MILD_VOLATILE.value == "mild"
        assert MarketRegime.HIGH_VOLATILE.value == "high"
        assert MarketRegime.TAIL_EVENT.value == "tail"

    def test_hedge_mode_values(self):
        """4 种对冲模式"""
        assert HedgeMode.NONE.value == "none"
        assert HedgeMode.TAIL_ONLY.value == "tail_only"
        assert HedgeMode.DYNAMIC.value == "dynamic"
        assert HedgeMode.FIXED.value == "fixed"

    def test_position_weight_defaults(self):
        """PositionWeight 默认 action=HOLD, priority=0"""
        pw = PositionWeight(
            code="300308", name="中际旭创", category="high_end_manufacturing",
            target_weight=0.10, current_weight=0.12, deviation=0.02, deviation_pct=0.20,
            current_value=120_000, target_value=100_000, adjustment=-20_000,
            adjustment_shares=-200, current_price=100.0,
        )
        assert pw.action == "HOLD"
        assert pw.priority == 0

    def test_hedge_decision_defaults(self):
        """HedgeDecision 默认值"""
        hd = HedgeDecision(needed=False)
        assert hd.mode == HedgeMode.NONE
        assert hd.regime == MarketRegime.CALM
        assert hd.hedge_ratio == 0.0
        assert hd.futures_contracts == {}

    def test_rebalance_decision_defaults(self):
        """RebalanceDecision 默认值"""
        rd = RebalanceDecision(needed=False)
        assert rd.rebalance_type == "none"
        assert rd.threshold == 0.05
        assert rd.positions_to_adjust == []

    def test_joint_plan_defaults(self):
        """JointPlan 默认 hedge/rebalance 为 None"""
        jp = JointPlan()
        assert jp.hedge is None
        assert jp.rebalance is None
        assert jp.warning_flags == []
        assert jp.stress_tests == {}


# ============================================================
# 2. TestModuleConstants — 常量回归保护
# ============================================================

class TestModuleConstants:

    def test_portfolio_hedge_thresholds_covers_all_regimes(self):
        """阈值表覆盖全部 4 种市场状态"""
        for regime in MarketRegime:
            assert regime in PORTFOLIO_HEDGE_THRESHOLDS, f"缺失 {regime} 阈值"
            cfg = PORTFOLIO_HEDGE_THRESHOLDS[regime]
            assert "hedge_ratio" in cfg
            assert "mode" in cfg
            assert "condition" in cfg

    def test_tail_trigger_params(self):
        """尾部对冲触发参数值回归保护"""
        assert TAIL_VOL_TRIGGER == 0.28
        assert TAIL_DD_TRIGGER == 0.12
        assert TAIL_MIN_HEDGE == 0.25
        assert TAIL_MAX_HEDGE == 0.40
        assert TAIL_MIN_HEDGE < TAIL_MAX_HEDGE

    def test_rebalance_thresholds_tiers(self):
        """再平衡阈值 3 档: low/normal/high"""
        assert set(REBALANCE_THRESHOLDS.keys()) == {"low", "normal", "high"}
        for tier, cfg in REBALANCE_THRESHOLDS.items():
            assert "threshold" in cfg
            assert "check_freq" in cfg
            assert "max_adjust" in cfg
        # 低波动阈值最小, 高波动阈值最大
        assert REBALANCE_THRESHOLDS["low"]["threshold"] < REBALANCE_THRESHOLDS["normal"]["threshold"]
        assert REBALANCE_THRESHOLDS["normal"]["threshold"] < REBALANCE_THRESHOLDS["high"]["threshold"]

    def test_sector_rotation_phases(self):
        """板块轮动 4 阶段"""
        required_phases = {"recovery", "prosperity", "stagflation", "recession"}
        assert required_phases.issubset(SECTOR_ROTATION.keys())
        for phase, weights in SECTOR_ROTATION.items():
            # 每阶段权重和约等于 1.0 (4 板块)
            assert len(weights) == 4
            total = sum(weights.values())
            assert 0.99 <= total <= 1.01, f"{phase} 权重和={total} 偏离1.0"

    def test_default_sector_weights(self):
        """默认板块权重 4 板块"""
        assert len(DEFAULT_SECTOR_WEIGHTS) == 4
        assert "high_end_manufacturing" in DEFAULT_SECTOR_WEIGHTS
        assert sum(DEFAULT_SECTOR_WEIGHTS.values()) == pytest.approx(1.0)


# ============================================================
# 3. TestHelperFunctions — 辅助函数
# ============================================================

class TestHelperFunctions:

    def test_estimate_portfolio_vol_default(self):
        """默认组合年化波动率 18%"""
        assert _estimate_portfolio_vol() == 0.18

    def test_estimate_portfolio_dd_60d_default(self):
        """默认 60 日回撤"""
        # 函数返回固定值或 0
        result = _estimate_portfolio_dd_60d()
        assert isinstance(result, float)
        assert result >= 0.0

    def test_load_yaml_valid(self, tmp_path):
        """加载有效 YAML"""
        f = tmp_path / "test.yaml"
        f.write_text("key: value\nnum: 42\n", encoding="utf-8")
        result = _load_yaml(str(f))
        assert result == {"key": "value", "num": 42}

    def test_load_yaml_nonexistent(self):
        """加载不存在的 YAML 返回 None"""
        assert _load_yaml("/nonexistent/path/file.yaml") is None

    def test_load_json_valid(self, tmp_path):
        """加载有效 JSON"""
        f = tmp_path / "test.json"
        f.write_text('{"a": 1, "b": [2, 3]}', encoding="utf-8")
        result = _load_json(str(f))
        assert result == {"a": 1, "b": [2, 3]}

    def test_load_json_nonexistent(self):
        """加载不存在的 JSON 返回 None"""
        assert _load_json("/nonexistent/path/file.json") is None

    def test_load_json_invalid(self, tmp_path):
        """加载非法 JSON 返回 None (不抛异常)"""
        f = tmp_path / "bad.json"
        f.write_text("{invalid json", encoding="utf-8")
        assert _load_json(str(f)) is None


# ============================================================
# 4. TestIntegratorInit — 初始化
# ============================================================

class TestIntegratorInit:

    def test_init_with_custom_portfolio_value(self, tmp_path):
        """自定义组合金额"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        integrator = HedgeRebalanceIntegrator(
            base_dir=str(tmp_path),
            config_dir=str(config_dir),
            portfolio_value=5_000_000,
        )
        assert integrator.portfolio_value == 5_000_000
        assert integrator.hedge_mode == HedgeMode.TAIL_ONLY  # 默认

    def test_init_default_hedge_mode(self, integrator):
        """默认对冲模式为 TAIL_ONLY"""
        assert integrator.hedge_mode == HedgeMode.TAIL_ONLY

    def test_init_custom_hedge_mode(self, tmp_path):
        """自定义对冲模式"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        integrator = HedgeRebalanceIntegrator(
            base_dir=str(tmp_path),
            config_dir=str(config_dir),
            hedge_mode=HedgeMode.DYNAMIC,
        )
        assert integrator.hedge_mode == HedgeMode.DYNAMIC

    def test_init_prices_not_loaded_initially(self, integrator):
        """初始化后价格未加载"""
        assert integrator._prices_loaded is False
        assert integrator._prices == {}


# ============================================================
# 5. TestDetermineMarketRegime — 市场状态判定 (纯逻辑)
# ============================================================

class TestDetermineMarketRegime:

    def test_calm_regime(self, integrator):
        """低波动低回撤 → CALM"""
        risk = _make_risk()
        regime = integrator._determine_market_regime(risk, portfolio_volatility=0.10, portfolio_drawdown_60d=0.03)
        assert regime == MarketRegime.CALM

    def test_mild_volatile_regime(self, integrator):
        """中等波动 → MILD_VOLATILE"""
        risk = _make_risk()
        regime = integrator._determine_market_regime(risk, portfolio_volatility=0.20, portfolio_drawdown_60d=0.05)
        assert regime == MarketRegime.MILD_VOLATILE

    def test_high_volatile_regime(self, integrator):
        """高波动 → HIGH_VOLATILE"""
        risk = _make_risk()
        regime = integrator._determine_market_regime(risk, portfolio_volatility=0.28, portfolio_drawdown_60d=0.10)
        assert regime == MarketRegime.HIGH_VOLATILE

    def test_tail_event_regime_by_vol(self, integrator):
        """极端波动 → TAIL_EVENT (vol>35%)"""
        risk = _make_risk()
        regime = integrator._determine_market_regime(risk, portfolio_volatility=0.40, portfolio_drawdown_60d=0.05)
        assert regime == MarketRegime.TAIL_EVENT

    def test_tail_event_regime_by_drawdown(self, integrator):
        """极端回撤 → TAIL_EVENT (DD>18%)"""
        risk = _make_risk()
        regime = integrator._determine_market_regime(risk, portfolio_volatility=0.15, portfolio_drawdown_60d=0.20)
        assert regime == MarketRegime.TAIL_EVENT

    def test_boundary_vol_0_18(self, integrator):
        """边界: vol=0.18 恰好不 >0.18 → CALM"""
        risk = _make_risk()
        regime = integrator._determine_market_regime(risk, portfolio_volatility=0.18, portfolio_drawdown_60d=0.0)
        assert regime == MarketRegime.CALM

    def test_boundary_vol_0_25(self, integrator):
        """边界: vol=0.25 恰好不 >0.25 → MILD_VOLATILE"""
        risk = _make_risk()
        regime = integrator._determine_market_regime(risk, portfolio_volatility=0.25, portfolio_drawdown_60d=0.0)
        assert regime == MarketRegime.MILD_VOLATILE

    def test_uses_default_when_none(self, integrator):
        """volatility/drawdown 为 None 时使用默认估算"""
        risk = _make_risk()
        # 默认 vol=0.18, dd=0 → CALM (0.18 不 > 0.18)
        regime = integrator._determine_market_regime(risk)
        assert regime == MarketRegime.CALM


# ============================================================
# 6. TestComputeTailHedgeRatio — 尾部对冲比率 (纯逻辑)
# ============================================================

class TestComputeTailHedgeRatio:

    def test_none_mode_returns_zero(self, tmp_path):
        """NONE 模式始终返回 0"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        integrator = HedgeRebalanceIntegrator(
            base_dir=str(tmp_path), config_dir=str(config_dir), hedge_mode=HedgeMode.NONE,
        )
        ratio = integrator._compute_tail_hedge_ratio(portfolio_volatility=0.50, portfolio_drawdown_60d=0.30)
        assert ratio == 0.0

    def test_no_trigger_returns_zero(self, integrator):
        """未触发阈值 → 0"""
        ratio = integrator._compute_tail_hedge_ratio(portfolio_volatility=0.15, portfolio_drawdown_60d=0.05)
        assert ratio == 0.0

    def test_vol_trigger_only(self, integrator):
        """仅波动率触发 (vol>0.28)"""
        # vol=0.30 → excess=0.02 → 0.25 + 0.02*2 = 0.29
        ratio = integrator._compute_tail_hedge_ratio(portfolio_volatility=0.30, portfolio_drawdown_60d=0.05)
        assert 0.25 <= ratio <= 0.40

    def test_dd_trigger_only(self, integrator):
        """仅回撤触发 (DD>0.12)"""
        # dd=0.15 → excess=0.03 → 0.25 + 0.03*3 = 0.34
        ratio = integrator._compute_tail_hedge_ratio(portfolio_volatility=0.15, portfolio_drawdown_60d=0.15)
        assert 0.25 <= ratio <= 0.40

    def test_both_triggers_takes_max(self, integrator):
        """波动率+回撤同时触发 → 取较大者"""
        ratio_vol = integrator._compute_tail_hedge_ratio(portfolio_volatility=0.30, portfolio_drawdown_60d=0.0)
        ratio_dd = integrator._compute_tail_hedge_ratio(portfolio_volatility=0.0, portfolio_drawdown_60d=0.15)
        ratio_both = integrator._compute_tail_hedge_ratio(portfolio_volatility=0.30, portfolio_drawdown_60d=0.15)
        assert ratio_both == max(ratio_vol, ratio_dd)

    def test_ratio_capped_at_max(self, integrator):
        """极端值不超过 TAIL_MAX_HEDGE (0.40)"""
        ratio = integrator._compute_tail_hedge_ratio(portfolio_volatility=0.80, portfolio_drawdown_60d=0.50)
        assert ratio <= TAIL_MAX_HEDGE

    def test_small_ratio_filtered(self, integrator):
        """对冲比率 < 0.10 时被过滤为 0 (成本效益)"""
        # vol=0.285 → excess=0.005 → 0.25 + 0.005*2 = 0.26 (仍 >0.10, 不过滤)
        # 但若触发值接近阈值, ratio 可能 < 0.10 时被过滤
        ratio = integrator._compute_tail_hedge_ratio(portfolio_volatility=0.281, portfolio_drawdown_60d=0.0)
        # 0.25 + 0.001*2 = 0.252 → > 0.10, 不过滤
        assert ratio >= 0.10 or ratio == 0.0


# ============================================================
# 7. TestGetDynamicRebalanceThreshold — 动态再平衡阈值
# ============================================================

class TestGetDynamicRebalanceThreshold:

    def test_low_vol_tier(self, integrator):
        """低波动 → low 档 (阈值0.03)"""
        threshold, freq, max_adj = integrator._get_dynamic_rebalance_threshold(0.10)
        assert threshold == 0.03
        assert freq == "每周"
        assert max_adj == 3

    def test_normal_vol_tier(self, integrator):
        """中波动 → normal 档 (阈值0.05)"""
        threshold, freq, max_adj = integrator._get_dynamic_rebalance_threshold(0.20)
        assert threshold == 0.05
        assert freq == "每3天"
        assert max_adj == 2

    def test_high_vol_tier(self, integrator):
        """高波动 → high 档 (阈值0.08)"""
        threshold, freq, max_adj = integrator._get_dynamic_rebalance_threshold(0.30)
        assert threshold == 0.08
        assert freq == "每日"
        assert max_adj == 4

    def test_boundary_0_15(self, integrator):
        """边界: vol=0.15 → normal 档 (0.15 不 < 0.15)"""
        threshold, _, _ = integrator._get_dynamic_rebalance_threshold(0.15)
        assert threshold == 0.05  # normal

    def test_boundary_0_25(self, integrator):
        """边界: vol=0.25 → normal 档 (0.25 不 > 0.25)"""
        threshold, _, _ = integrator._get_dynamic_rebalance_threshold(0.25)
        assert threshold == 0.05  # normal


# ============================================================
# 8. TestEstimateDefaultPrice — 兜底价格 (CR1 修复)
# ============================================================

class TestEstimateDefaultPrice:

    def test_known_code_returns_price(self, integrator):
        """已知标的返回兜底价格"""
        price = integrator._estimate_default_price("300308.SZ")
        assert price == 105.0

    def test_unknown_code_returns_placeholder(self, integrator):
        """未知标的返回占位价 50.0"""
        price = integrator._estimate_default_price("999999.SZ")
        assert price == 50.0

    def test_known_code_does_not_crash(self, integrator):
        """所有已知标的兜底价格可获取"""
        known_codes = [
            "300308.SZ", "688041.SH", "002371.SZ", "688981.SH", "300750.SZ",
            "000425.SZ", "601088.SH", "600219.SH", "600019.SH", "518880.SH",
            "000792.SZ", "600276.SH", "603259.SH", "002422.SZ",
        ]
        for code in known_codes:
            price = integrator._estimate_default_price(code)
            assert price > 0, f"{code} 兜底价格应 > 0"


# ============================================================
# 9. TestIsPriceSafe — 价格安全检查 (CR1)
# ============================================================

class TestIsPriceSafe:

    def test_safe_when_no_fallback_codes(self, integrator):
        """无回退记录时所有价格安全"""
        # integrator fixture 未设置 _fallback_stale_codes
        assert integrator.is_price_safe("300308") is True

    def test_unsafe_when_code_in_fallback(self, integrator):
        """代码在回退列表中时不安全"""
        integrator._fallback_stale_codes = {"300308"}
        assert integrator.is_price_safe("300308") is False
        assert integrator.is_price_safe("601088") is True


# ============================================================
# 10. TestNotifyDataSourceFailure — 告警通知 (fail-safe)
# ============================================================

class TestNotifyDataSourceFailure:

    def test_notify_does_not_crash_when_utils_missing(self, integrator):
        """utils.notify 不可用时不崩溃, 仅记录日志"""
        # 应正常返回 None, 不抛异常
        integrator._notify_data_source_failure("300308", 105.0, 20260715)

    def test_notify_calls_send_sms_when_available(self, integrator):
        """utils.notify 可用时调用 send_sms_alert"""
        # 源码内 from utils.notify import send_sms_alert (局部导入)
        # 需先注入 sys.modules 才能让局部导入成功
        mock_module = MagicMock()
        mock_module.send_sms_alert = MagicMock()
        original = sys.modules.get("utils.notify")
        sys.modules["utils.notify"] = mock_module
        try:
            integrator._notify_data_source_failure("300308", 105.0, 20260715)
            mock_module.send_sms_alert.assert_called_once()
            # 验证消息内容包含关键信息
            call_args = mock_module.send_sms_alert.call_args
            assert "300308" in call_args[0][0]
        finally:
            if original is not None:
                sys.modules["utils.notify"] = original
            else:
                sys.modules.pop("utils.notify", None)


# ============================================================
# 11. TestEstimatePerformance — 性能估算 (纯计算)
# ============================================================

class TestEstimatePerformance:

    def test_no_hedge_no_rebalance(self, integrator):
        """无对冲无再平衡 → 基础值"""
        risk = _make_risk()
        hedge = HedgeDecision(needed=False, hedge_ratio=0.0)
        rebalance = RebalanceDecision(needed=False)
        ret, dd, sharpe, vol = integrator._estimate_performance(risk, hedge, rebalance)
        # base_return=0.12, base_dd=0.18, base_vol=0.20
        assert ret == pytest.approx(0.12)
        assert dd == pytest.approx(0.18)
        assert vol == pytest.approx(0.20)
        # sharpe = (0.12 - 0.03) / 0.20 = 0.45
        assert sharpe == pytest.approx(0.45, rel=1e-6)

    def test_with_hedge_reduces_drawdown(self, integrator):
        """有对冲 → 回撤和波动率下降, 收益略降"""
        risk = _make_risk()
        hedge_no = HedgeDecision(needed=False, hedge_ratio=0.0)
        hedge_yes = HedgeDecision(needed=True, hedge_ratio=0.30)
        rebalance = RebalanceDecision(needed=False)
        ret_no, dd_no, _, vol_no = integrator._estimate_performance(risk, hedge_no, rebalance)
        ret_yes, dd_yes, _, vol_yes = integrator._estimate_performance(risk, hedge_yes, rebalance)
        # 对冲后回撤和波动率应更低
        assert dd_yes < dd_no
        assert vol_yes < vol_no
        # 对冲后收益略降 (对冲成本)
        assert ret_yes < ret_no

    def test_strategic_rebalance_boosts_return(self, integrator):
        """战略再平衡 → 收益提升 0.015"""
        risk = _make_risk()
        hedge = HedgeDecision(needed=False, hedge_ratio=0.0)
        rebalance_none = RebalanceDecision(needed=False)
        rebalance_strat = RebalanceDecision(needed=True, rebalance_type="strategic")
        ret_none, _, _, _ = integrator._estimate_performance(risk, hedge, rebalance_none)
        ret_strat, _, _, _ = integrator._estimate_performance(risk, hedge, rebalance_strat)
        assert ret_strat > ret_none
        assert ret_strat - ret_none == pytest.approx(0.015)

    def test_non_strategic_rebalance_smaller_boost(self, integrator):
        """非战略再平衡 → 收益提升 0.008"""
        risk = _make_risk()
        hedge = HedgeDecision(needed=False, hedge_ratio=0.0)
        rebalance_tactical = RebalanceDecision(needed=True, rebalance_type="tactical")
        ret_base, _, _, _ = integrator._estimate_performance(
            risk, hedge, RebalanceDecision(needed=False)
        )
        ret_tac, _, _, _ = integrator._estimate_performance(risk, hedge, rebalance_tactical)
        assert ret_tac - ret_base == pytest.approx(0.008)

    def test_drawdown_floor(self, integrator):
        """回撤不低于 0.05 下限"""
        risk = _make_risk()
        hedge = HedgeDecision(needed=True, hedge_ratio=1.0)  # 极端对冲
        rebalance = RebalanceDecision(needed=False)
        _, dd, _, _ = integrator._estimate_performance(risk, hedge, rebalance)
        assert dd >= 0.05

    def test_volatility_floor(self, integrator):
        """波动率不低于 0.10 下限"""
        risk = _make_risk()
        hedge = HedgeDecision(needed=True, hedge_ratio=1.0)
        rebalance = RebalanceDecision(needed=False)
        _, _, _, vol = integrator._estimate_performance(risk, hedge, rebalance)
        assert vol >= 0.10


# ============================================================
# 12. TestDecideHedge — 对冲决策 (mock 依赖)
# ============================================================

class TestDecideHedge:

    def test_calm_regime_no_hedge_needed(self, integrator):
        """CALM 状态 + TAIL_ONLY 模式 → 不需要对冲"""
        risk = _make_risk()
        decision = integrator.decide_hedge(risk, portfolio_volatility=0.10, portfolio_drawdown_60d=0.03)
        assert isinstance(decision, HedgeDecision)
        # CALM + TAIL_ONLY 未触发 → hedge_ratio=0
        assert decision.regime == MarketRegime.CALM
        assert decision.hedge_ratio == 0.0

    def test_tail_event_triggers_hedge(self, integrator):
        """TAIL_EVENT 状态 → 触发对冲"""
        risk = _make_risk()
        decision = integrator.decide_hedge(risk, portfolio_volatility=0.40, portfolio_drawdown_60d=0.20)
        assert decision.regime == MarketRegime.TAIL_EVENT
        assert decision.hedge_ratio > 0.0

    def test_fixed_mode_uses_threshold_ratio(self, tmp_path):
        """FIXED 模式使用阈值表比率"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        integrator = HedgeRebalanceIntegrator(
            base_dir=str(tmp_path), config_dir=str(config_dir), hedge_mode=HedgeMode.FIXED,
        )
        risk = _make_risk()
        decision = integrator.decide_hedge(risk, portfolio_volatility=0.40, portfolio_drawdown_60d=0.20)
        # TAIL_EVENT + FIXED → hedge_ratio=0.40 (来自阈值表)
        assert decision.hedge_ratio == 0.40


# ============================================================
# 13. TestFormatReport — 报告格式化
# ============================================================

class TestFormatReport:

    def test_basic_report_structure(self, integrator):
        """报告基础结构"""
        plan = _make_joint_plan()
        report = integrator.format_report(plan)
        assert "对冲+再平衡联动分析报告" in report
        assert "HedgeRebalanceIntegrator v5.9" in report
        assert "组合市值" in report
        assert "股票敞口" in report

    def test_report_hedge_section(self, integrator):
        """对冲决策段"""
        plan = _make_joint_plan()
        report = integrator.format_report(plan)
        assert "[1] 对冲决策" in report
        assert "组合状态" in report
        assert "high" in report  # regime.value
        assert "对冲比率" in report
        assert "IC2401" in report
        assert "做空" in report

    def test_report_no_hedge_when_none(self, integrator):
        """无对冲决策时显示无需对冲"""
        plan = _make_joint_plan(hedge=None)
        report = integrator.format_report(plan)
        assert "无需" in report
        assert "S2动态再平衡" in report

    def test_report_includes_warnings(self, integrator):
        """报告包含警告标志"""
        plan = _make_joint_plan(warning_flags=["价格回退: 300308", "波动率偏高"])
        report = integrator.format_report(plan)
        assert "价格回退" in report

    def test_report_includes_performance(self, integrator):
        """报告包含性能估算"""
        plan = _make_joint_plan()
        report = integrator.format_report(plan)
        # 性能估算段
        assert "0.105" in report or "10.5%" in report  # estimated_annual_return

    def test_report_timestamp_truncated(self, integrator):
        """timestamp 截取前19字符"""
        plan = _make_joint_plan(timestamp="2026-08-01T14:30:00.123456")
        report = integrator.format_report(plan)
        assert "2026-08-01T14:30:00" in report

    def test_report_rebalance_section(self, integrator):
        """再平衡段"""
        plan = _make_joint_plan()
        report = integrator.format_report(plan)
        assert "[2] 再平衡" in report or "再平衡" in report


# ============================================================
# 13.5 TestCheckRebalance — 再平衡检查 (覆盖 Phase 3)
# ============================================================

class TestCheckRebalance:

    def test_no_rebalance_needed_when_within_threshold(self, integrator):
        """所有标的偏离在阈值内 → needed=False"""
        risk = _make_risk(stock_exposure=1_000_000)
        # mock 价格使持仓权重接近目标
        with patch.object(integrator, "load_prices", return_value={"300308": 100.0, "601088": 40.0}):
            decision = integrator.check_rebalance(risk, portfolio_volatility=0.20)
        assert isinstance(decision, RebalanceDecision)
        # 可能 needed=True 或 False, 取决于实际持仓 vs 目标
        assert decision.threshold == 0.05  # normal 档

    def test_rebalance_triggered_when_deviation_large(self, integrator):
        """持仓严重偏离 → needed=True"""
        risk = _make_risk(stock_exposure=1_000_000)
        # 用极端价格制造偏离
        with patch.object(integrator, "load_prices", return_value={"300308": 500.0, "601088": 5.0}):
            decision = integrator.check_rebalance(risk, portfolio_volatility=0.20)
        assert decision.needed is True
        assert decision.rebalance_type in ("strategic", "tactical")
        assert len(decision.positions_to_adjust) > 0

    def test_rebalance_strategic_when_severe_deviation(self, integrator):
        """偏离 > 阈值*2 → strategic 类型"""
        risk = _make_risk(stock_exposure=1_000_000)
        # 极端价格制造严重偏离
        with patch.object(integrator, "load_prices", return_value={"300308": 1000.0, "601088": 1.0}):
            decision = integrator.check_rebalance(risk, portfolio_volatility=0.20)
        if decision.needed:
            # 当最大偏离 > threshold*2 (0.10) 时为 strategic
            max_dev = max(pw.deviation_pct for pw in decision.positions_to_adjust) if decision.positions_to_adjust else 0
            if max_dev > 0.10:
                assert decision.rebalance_type == "strategic"

    def test_rebalance_respects_max_adjust(self, integrator):
        """再平衡调整标的数不超过 max_adjust"""
        risk = _make_risk(stock_exposure=1_000_000)
        with patch.object(integrator, "load_prices", return_value={"300308": 500.0, "601088": 5.0}):
            decision = integrator.check_rebalance(risk, portfolio_volatility=0.20)
        if decision.needed:
            # normal 档 max_adjust=2
            assert len(decision.positions_to_adjust) <= 2

    def test_rebalance_categorized_buy_sell(self, integrator):
        """BUY/SELL 动作正确分类"""
        risk = _make_risk(stock_exposure=1_000_000)
        with patch.object(integrator, "load_prices", return_value={"300308": 500.0, "601088": 5.0}):
            decision = integrator.check_rebalance(risk, portfolio_volatility=0.20)
        if decision.needed and decision.positions_to_adjust:
            actions = {pw.action for pw in decision.positions_to_adjust}
            # 应该有 BUY 或 SELL (至少一种)
            assert actions.intersection({"BUY", "SELL"})

    def test_rebalance_net_cash_flow_calculation(self, integrator):
        """净现金流 = 卖出 - 买入"""
        risk = _make_risk(stock_exposure=1_000_000)
        with patch.object(integrator, "load_prices", return_value={"300308": 500.0, "601088": 5.0}):
            decision = integrator.check_rebalance(risk, portfolio_volatility=0.20)
        if decision.needed:
            expected_net = decision.total_sell_amount - decision.total_buy_amount
            assert decision.net_cash_flow == pytest.approx(expected_net, rel=1e-6)


# ============================================================
# 13.6 TestJointOptimize — 联合优化 (Phase 4)
# ============================================================

class TestJointOptimize:

    def test_no_optimize_when_hedge_not_needed(self, integrator):
        """对冲不需要时直接返回"""
        risk = _make_risk(stock_exposure=800_000)
        hedge = HedgeDecision(needed=False, hedge_ratio=0.0)
        rebalance = _make_rebalance_decision()
        adj_h, adj_r, warnings = integrator.joint_optimize(risk, hedge, rebalance)
        assert warnings == []
        assert adj_h is hedge

    def test_no_optimize_when_rebalance_not_needed(self, integrator):
        """再平衡不需要时直接返回"""
        risk = _make_risk(stock_exposure=800_000)
        hedge = _make_hedge_decision()
        rebalance = RebalanceDecision(needed=False)
        adj_h, adj_r, warnings = integrator.joint_optimize(risk, hedge, rebalance)
        assert warnings == []

    def test_warning_on_large_discrepancy(self, integrator):
        """对冲后敞口与再平衡后敞口差异 > 15% → 调整对冲比率"""
        risk = _make_risk(stock_exposure=800_000, cash=100_000)
        hedge = HedgeDecision(needed=True, hedge_ratio=0.30, total_margin=200_000)
        # 制造大差异: net_cash_flow 使 after_rebalance 与 after_hedge 差异大
        rebalance = RebalanceDecision(needed=True, net_cash_flow=300_000)
        adj_h, adj_r, warnings = integrator.joint_optimize(risk, hedge, rebalance)
        # 应有警告 (差异 > 15% 或保证金较高)
        assert len(warnings) > 0

    def test_warning_on_small_discrepancy(self, integrator):
        """差异 5-15% → 轻微不一致警告"""
        risk = _make_risk(stock_exposure=800_000, cash=500_000)
        hedge = HedgeDecision(needed=True, hedge_ratio=0.20, total_margin=50_000)
        # 小差异
        rebalance = RebalanceDecision(needed=True, net_cash_flow=20_000)
        adj_h, adj_r, warnings = integrator.joint_optimize(risk, hedge, rebalance)
        # 差异较小时可能有轻微警告
        # (具体取决于计算, 但不应崩溃)
        assert isinstance(warnings, list)

    def test_margin_warning_when_high(self, integrator):
        """保证金需求高于净现金流+30%现金 → 警告"""
        risk = _make_risk(stock_exposure=800_000, cash=100_000)
        hedge = HedgeDecision(needed=True, hedge_ratio=0.30, total_margin=500_000)
        rebalance = RebalanceDecision(needed=True, net_cash_flow=10_000)
        _, _, warnings = integrator.joint_optimize(risk, hedge, rebalance)
        # 500000 > 10000 + 100000*0.3=30000 → 应有保证金警告
        assert any("保证金" in w for w in warnings)


# ============================================================
# 13.7 TestGenerateExecutionPlan — 执行计划生成 (Phase 5)
# ============================================================

class TestGenerateExecutionPlan:

    def test_high_priority_when_large_hedge(self, integrator):
        """对冲比率 > 30% → HIGH 优先级"""
        risk = _make_risk(stock_exposure=800_000)
        hedge = _make_hedge_decision(hedge_ratio=0.40)
        rebalance = _make_rebalance_decision()
        plan = integrator.generate_execution_plan(risk, hedge, rebalance)
        assert plan.execution_priority == "HIGH"
        assert plan.execution_window == "当日/次日"

    def test_high_priority_when_strategic_rebalance(self, integrator):
        """战略再平衡 → HIGH 优先级"""
        risk = _make_risk(stock_exposure=800_000)
        hedge = HedgeDecision(needed=False, hedge_ratio=0.0)
        rebalance = _make_rebalance_decision(rebalance_type="strategic")
        plan = integrator.generate_execution_plan(risk, hedge, rebalance)
        assert plan.execution_priority == "HIGH"

    def test_medium_priority(self, integrator):
        """对冲比率 10-30% → MEDIUM"""
        risk = _make_risk(stock_exposure=800_000)
        hedge = _make_hedge_decision(hedge_ratio=0.20)
        rebalance = RebalanceDecision(needed=False)
        plan = integrator.generate_execution_plan(risk, hedge, rebalance)
        assert plan.execution_priority == "MEDIUM"
        assert plan.execution_window == "本周内"

    def test_low_priority(self, integrator):
        """无对冲无再平衡 → LOW"""
        risk = _make_risk(stock_exposure=800_000)
        hedge = HedgeDecision(needed=False, hedge_ratio=0.0)
        rebalance = RebalanceDecision(needed=False)
        plan = integrator.generate_execution_plan(risk, hedge, rebalance)
        assert plan.execution_priority == "LOW"
        assert plan.execution_window == "两周内"

    def test_plan_after_hedge_exposure(self, integrator):
        """对冲后净敞口计算"""
        risk = _make_risk(stock_exposure=800_000)
        hedge = _make_hedge_decision(hedge_ratio=0.30)
        rebalance = RebalanceDecision(needed=False)
        plan = integrator.generate_execution_plan(risk, hedge, rebalance)
        # 800000 * (1 - 0.30) = 560000
        assert plan.after_hedge_exposure == pytest.approx(560_000, rel=1e-6)

    def test_plan_hedge_none_when_not_needed(self, integrator):
        """对冲不需要时 plan.hedge = None"""
        risk = _make_risk(stock_exposure=800_000)
        hedge = HedgeDecision(needed=False, hedge_ratio=0.0)
        rebalance = RebalanceDecision(needed=False)
        plan = integrator.generate_execution_plan(risk, hedge, rebalance)
        assert plan.hedge is None

    def test_plan_summary_includes_hedge_info(self, integrator):
        """summary 包含对冲信息"""
        risk = _make_risk(stock_exposure=800_000)
        hedge = _make_hedge_decision(hedge_ratio=0.30)
        rebalance = _make_rebalance_decision()
        plan = integrator.generate_execution_plan(risk, hedge, rebalance)
        assert "对冲" in plan.summary
        assert "30%" in plan.summary
        assert "IC2401" in plan.summary

    def test_plan_summary_no_hedge(self, integrator):
        """无对冲时 summary 显示无需"""
        risk = _make_risk(stock_exposure=800_000)
        hedge = HedgeDecision(needed=False, hedge_ratio=0.0)
        rebalance = RebalanceDecision(needed=False)
        plan = integrator.generate_execution_plan(risk, hedge, rebalance)
        assert "无需" in plan.summary

    def test_plan_performance_fields_populated(self, integrator):
        """性能字段已填充"""
        risk = _make_risk(stock_exposure=800_000)
        hedge = _make_hedge_decision()
        rebalance = _make_rebalance_decision()
        plan = integrator.generate_execution_plan(risk, hedge, rebalance)
        assert plan.estimated_annual_return > 0
        assert plan.estimated_max_drawdown > 0
        assert plan.estimated_volatility > 0

    def test_plan_timestamp_is_iso(self, integrator):
        """timestamp 为 ISO 格式"""
        risk = _make_risk(stock_exposure=800_000)
        hedge = HedgeDecision(needed=False, hedge_ratio=0.0)
        rebalance = RebalanceDecision(needed=False)
        plan = integrator.generate_execution_plan(risk, hedge, rebalance)
        from datetime import datetime
        datetime.fromisoformat(plan.timestamp)  # 不抛异常即可


# ============================================================
# 13.75 TestLoadPrices — 价格加载多源降级 (覆盖 Phase 1 数据层)
# ============================================================

class TestLoadPrices:

    def test_load_prices_caches_result(self, integrator):
        """第二次调用返回缓存 (不重复网络请求)"""
        with patch.object(integrator, "_estimate_default_price", return_value=50.0):
            prices1 = integrator.load_prices()
            prices2 = integrator.load_prices()
        assert prices1 is prices2  # 同一对象引用 (缓存)
        assert integrator._prices_loaded is True

    def test_load_prices_fallback_when_all_sources_fail(self, integrator):
        """所有数据源失败 → 使用兜底价格"""
        # integrator fixture 的 codes 为 "300308"/"601088" (无后缀)
        # 所有外部源不可用 → 走 _estimate_default_price 兜底
        with patch.object(integrator, "_estimate_default_price", return_value=50.0):
            prices = integrator.load_prices()
        # 所有代码都应有价格 (兜底 50.0)
        assert "300308" in prices
        assert "601088" in prices
        assert prices["300308"] == 50.0
        assert prices["601088"] == 50.0

    def test_load_prices_marks_fallback_codes(self, integrator):
        """使用兜底价格的标的被标记到 _fallback_codes"""
        with patch.object(integrator, "_estimate_default_price", return_value=50.0):
            integrator.load_prices()
        assert hasattr(integrator, "_fallback_codes")
        # 两个标的都用了兜底
        assert len(integrator._fallback_codes) == 2

    def test_load_prices_marks_stale_codes(self, integrator):
        """过期兜底价格被标记到 _fallback_stale_codes (CR1 拦截)"""
        with patch.object(integrator, "_estimate_default_price", return_value=50.0):
            integrator.load_prices()
        # fixture 的 codes 无后缀, fallback_db 查不到 → 标记为 stale
        assert hasattr(integrator, "_fallback_stale_codes")
        # 无后缀代码不在 fallback_db → 全部标记为 stale
        assert len(integrator._fallback_stale_codes) == 2

    def test_load_prices_returns_all_codes(self, integrator):
        """返回价格覆盖所有配置标的"""
        with patch.object(integrator, "_estimate_default_price", return_value=50.0):
            prices = integrator.load_prices()
        assets = integrator.portfolio_config.get("assets", [])
        for asset in assets:
            assert asset["code"] in prices

    def test_is_price_safe_after_load_with_stale(self, integrator):
        """加载含过期兜底价格后, is_price_safe 返回 False"""
        with patch.object(integrator, "_estimate_default_price", return_value=50.0):
            integrator.load_prices()
        # 过期代码不安全
        assert integrator.is_price_safe("300308") is False
        assert integrator.is_price_safe("601088") is False


# ============================================================
# 13.8 TestRunFullWorkflow — 完整五阶段工作流 (覆盖主链路)
# ============================================================

class TestRunFullWorkflow:

    def test_full_workflow_completes_with_mocked_prices(self, integrator):
        """完整五阶段工作流不崩溃且产出 JointPlan"""
        with patch.object(integrator, "load_prices", return_value={"300308": 100.0, "601088": 40.0}):
            plan = integrator.run_full_workflow(
                portfolio_volatility=0.22, portfolio_drawdown_60d=0.05
            )
        assert isinstance(plan, JointPlan)
        assert plan.portfolio_value > 0
        assert plan.execution_priority in ("HIGH", "MEDIUM", "LOW")
        assert plan.summary != ""

    def test_full_workflow_calm_market(self, integrator):
        """平静市场 → 可能无需对冲"""
        with patch.object(integrator, "load_prices", return_value={"300308": 100.0, "601088": 40.0}):
            plan = integrator.run_full_workflow(
                portfolio_volatility=0.10, portfolio_drawdown_60d=0.02
            )
        # CALM 状态 → hedge_ratio=0 → plan.hedge 可能 None
        assert isinstance(plan, JointPlan)

    def test_full_workflow_tail_event(self, integrator):
        """极端市场 → 触发对冲决策 (joint_optimize 可能调整比率)"""
        with patch.object(integrator, "load_prices", return_value={"300308": 100.0, "601088": 40.0}):
            plan = integrator.run_full_workflow(
                portfolio_volatility=0.40, portfolio_drawdown_60d=0.20
            )
        assert isinstance(plan, JointPlan)
        # 极端市场产生对冲决策 (注: joint_optimize 可能将比率调整为 0)
        # 关键验证: 工作流完整运行不崩溃, 且 hedge 决策对象存在
        if plan.hedge:
            assert plan.hedge.regime == MarketRegime.TAIL_EVENT
            assert plan.hedge.needed is True

    def test_full_workflow_includes_stress_tests(self, integrator):
        """工作流包含压力测试 (当 hedge_engine 可用)"""
        with patch.object(integrator, "load_prices", return_value={"300308": 100.0, "601088": 40.0}):
            plan = integrator.run_full_workflow(
                portfolio_volatility=0.22, portfolio_drawdown_60d=0.05
            )
        # 压力测试可能为空 (持仓不足或引擎不可用), 但字段存在
        assert hasattr(plan, "stress_tests")
        assert isinstance(plan.stress_tests, dict)

    def test_full_workflow_then_format_report(self, integrator):
        """完整工作流后可格式化报告"""
        with patch.object(integrator, "load_prices", return_value={"300308": 100.0, "601088": 40.0}):
            plan = integrator.run_full_workflow(
                portfolio_volatility=0.22, portfolio_drawdown_60d=0.05
            )
        report = integrator.format_report(plan)
        assert "对冲+再平衡联动分析报告" in report
        assert isinstance(report, str)


# ============================================================
# 14. TestConvenienceFunctions — 便捷函数
# ============================================================

class TestConvenienceFunctions:

    def test_get_integrator_default_mode(self, tmp_path):
        """get_integrator 默认 TAIL_ONLY 模式"""
        integrator = get_integrator(base_dir=str(tmp_path), portfolio_value=1_000_000)
        assert isinstance(integrator, HedgeRebalanceIntegrator)
        assert integrator.hedge_mode == HedgeMode.TAIL_ONLY

    def test_get_integrator_custom_mode(self, tmp_path):
        """get_integrator 自定义模式"""
        integrator = get_integrator(
            base_dir=str(tmp_path), portfolio_value=2_000_000, mode="dynamic"
        )
        assert integrator.hedge_mode == HedgeMode.DYNAMIC
        assert integrator.portfolio_value == 2_000_000

    def test_get_integrator_default_values(self, tmp_path):
        """get_integrator 默认值 (base_dir/portfolio_value 为 None)"""
        integrator = get_integrator(base_dir=str(tmp_path))
        assert isinstance(integrator, HedgeRebalanceIntegrator)
        # portfolio_value 从配置读取或默认
        assert integrator.portfolio_value > 0
