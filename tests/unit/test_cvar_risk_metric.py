"""W7.3.3 G11 CVaR 风险计量模块单元测试.

被测模块: utils/risk/cvar.py
覆盖目标: >=85% (目标 >=85%)

12 类覆盖:
    1. historical 计算: 正态/肥尾/边界样本/NaN/inf/CVaR>=VaR/耗时
    2. parametric 计算: normal/student_t/dof/置信水平/dof<2
    3. evt 计算: 收敛/不收敛/xi>0.5/置信映射/异常
    4. 降级链: evt->historical/monte_carlo->parametric/耗尽/校验失败不降级
    5. 超限检查与事件发布: 95%/99%/payload/Flag/总线异常
    6. 审计日志: 调用/module/sub_module/Flag=False 仍写
    7. 配置管理: 合法/缺失/非法/from_system_config/子段缺失
    8. 输入校验: NaN/inf/None/样本量/置信/空序列
    9. 组合级计算: 聚合/长度不一致/权重偏离/缺失标的
    10. VaR 对照: enable/disable/CVaR>=VaR/var_pct=0
    11. VaRMonitorAdapter 扩展: cvar_95/cvar_99 (在 test_risk_module_adapters.py)
    12. calculate_scalar: return_amount/_skip_breach
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.fineng.tail_risk_evt import EVTResult  # noqa: E402
from utils.risk.cvar import (  # noqa: E402
    CVaRCalculator,
    CVaRConfig,
    CVaRResult,
)
from utils.risk.risk_event import RiskEventType  # noqa: E402

_NAN = float("nan")


# ============================================================
# Mock 工厂
# ============================================================
def make_evt_result(
    converged: bool = True,
    xi: float = 0.2,
    es_975: float = -0.025,
    es_99: float = -0.035,
    var_975: float = -0.022,
    var_99: float = -0.030,
    n_excess: int = 50,
    threshold_u: float = -0.02,
    warning: str = "",
    error_message: str = "",
) -> EVTResult:
    """构造完整 EVTResult (避免 dataclass 必填字段遗漏)."""
    return EVTResult(
        threshold_u=threshold_u,
        threshold_percentile=0.95,
        n_total=200,
        n_excess=n_excess,
        sigma=0.01,
        xi=xi,
        log_likelihood=100.0,
        var_975=var_975,
        var_99=var_99,
        es_975=es_975,
        es_99=es_99,
        xi_lower=0.1,
        xi_upper=0.3,
        sigma_lower=0.005,
        sigma_upper=0.015,
        empirical_var_975=-0.021,
        empirical_var_99=-0.029,
        empirical_es_975=-0.024,
        empirical_es_99=-0.034,
        evt_vs_empirical_ratio=1.02,
        converged=converged,
        warning=warning,
        error_message=error_message,
    )


def make_calculator(
    config: CVaRConfig | None = None,
    flag_enabled: bool = False,
):
    """构造 calculator + mock bus/audit, 并 patch is_enabled.

    返回 (calc, bus, audit, patcher). 需在 finally 中 stop patcher.
    """
    bus = MagicMock(name="bus")
    audit = MagicMock(name="audit")
    cfg = config or CVaRConfig()
    calc = CVaRCalculator(cfg, bus, audit)
    patcher = patch("utils.risk.cvar.is_enabled", return_value=flag_enabled)
    patcher.start()
    return calc, bus, audit, patcher


def normal_returns(n: int = 200, seed: int = 42) -> list[float]:
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, 0.01, n).tolist()


def fat_tail_returns(n: int = 200, seed: int = 7) -> list[float]:
    rng = np.random.default_rng(seed)
    return (rng.standard_t(3, n) * 0.01).tolist()


# ============================================================
# 7. 配置管理测试
# ============================================================
class CVaRConfigTest:
    """CVaRConfig.from_dict / from_system_config 测试."""

    def test_from_dict_empty_returns_default(self):
        cfg = CVaRConfig.from_dict(None)
        assert cfg.method == "historical"
        assert cfg.distribution == "normal"
        assert cfg.dof == 5
        assert cfg.confidence_level == 0.95
        assert cfg.fallback_chain == ("evt", "historical")

    def test_from_dict_valid_fields(self):
        cfg = CVaRConfig.from_dict(
            {
                "method": "parametric",
                "distribution": "student_t",
                "dof": 10,
                "confidence_level": 0.99,
                "threshold_percentile": 0.975,
                "min_history": 50,
                "var_95_limit_pct": -0.03,
                "var_99_limit_pct": -0.05,
                "enable_var_comparison": False,
                "fallback_chain": ["historical", "parametric"],
                "feature_flag_name": "MY_FLAG",
            }
        )
        assert cfg.method == "parametric"
        assert cfg.distribution == "student_t"
        assert cfg.dof == 10
        assert cfg.confidence_level == 0.99
        assert cfg.threshold_percentile == 0.975
        assert cfg.min_history == 50
        assert cfg.var_95_limit_pct == -0.03
        assert cfg.var_99_limit_pct == -0.05
        assert cfg.enable_var_comparison is False
        assert cfg.fallback_chain == ("historical", "parametric")
        assert cfg.feature_flag_name == "MY_FLAG"

    def test_from_dict_invalid_method_falls_back(self):
        cfg = CVaRConfig.from_dict({"method": "invalid_method"})
        assert cfg.method == "historical"

    def test_from_dict_invalid_distribution_falls_back(self):
        cfg = CVaRConfig.from_dict({"distribution": "laplace"})
        assert cfg.distribution == "normal"

    def test_from_dict_invalid_dof_falls_back(self):
        cfg = CVaRConfig.from_dict({"dof": 1})
        assert cfg.dof == 5
        cfg2 = CVaRConfig.from_dict({"dof": "abc"})
        assert cfg2.dof == 5

    def test_from_dict_invalid_confidence_falls_back(self):
        cfg = CVaRConfig.from_dict({"confidence_level": 0.5})
        assert cfg.confidence_level == 0.95
        cfg2 = CVaRConfig.from_dict({"confidence_level": "bad"})
        assert cfg2.confidence_level == 0.95

    def test_from_dict_invalid_threshold_falls_back(self):
        cfg = CVaRConfig.from_dict({"threshold_percentile": 0.5})
        assert cfg.threshold_percentile == 0.95

    def test_from_dict_invalid_min_history_falls_back(self):
        cfg = CVaRConfig.from_dict({"min_history": 5})
        assert cfg.min_history == 30

    def test_from_dict_invalid_var_limits_falls_back(self):
        cfg = CVaRConfig.from_dict({"var_95_limit_pct": 0.04, "var_99_limit_pct": 0.06})
        assert cfg.var_95_limit_pct == -0.04
        assert cfg.var_99_limit_pct == -0.06

    def test_from_dict_empty_fallback_chain_falls_back(self):
        cfg = CVaRConfig.from_dict({"fallback_chain": []})
        assert cfg.fallback_chain == ("evt", "historical")

    def test_from_dict_all_invalid_fallback_chain_falls_back(self):
        cfg = CVaRConfig.from_dict({"fallback_chain": ["unknown1", "unknown2"]})
        assert cfg.fallback_chain == ("evt", "historical")

    def test_from_dict_partial_fields(self):
        cfg = CVaRConfig.from_dict({"method": "evt"})
        assert cfg.method == "evt"
        assert cfg.distribution == "normal"
        assert cfg.dof == 5

    def test_from_system_config_real_file(self):
        cfg = CVaRConfig.from_system_config()
        assert isinstance(cfg, CVaRConfig)
        # config/system_config.json 中 risk_metric.method = "historical"
        assert cfg.method == "historical"

    def test_from_system_config_file_not_exist(self):
        with patch("pathlib.Path.exists", return_value=False):
            cfg = CVaRConfig.from_system_config()
        assert cfg.method == "historical"
        assert cfg.confidence_level == 0.95

    def test_from_system_config_subsection_missing(self):
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch(
                "pathlib.Path.read_text",
                return_value='{"risk_management": {"cvar": {}}}',
            ),
        ):
            cfg = CVaRConfig.from_system_config()
        assert cfg.method == "historical"

    def test_from_system_config_json_error(self):
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text", return_value="not-json"),
        ):
            cfg = CVaRConfig.from_system_config()
        assert cfg.method == "historical"


# ============================================================
# CVaRResult.to_dict 测试
# ============================================================
class CVaRResultTest:
    """CVaRResult.to_dict 测试."""

    def test_to_dict_roundtrip(self):
        r = CVaRResult(
            cvar_pct=-0.05,
            cvar_amount=-500.0,
            method="historical",
            method_used="historical",
            confidence=0.95,
            breach=True,
            breach_level="cvar_95",
            threshold=-0.04,
            warning="",
            evt_converged=False,
            var_comparison={"var_pct": -0.04},
            timestamp="2026-01-01T00:00:00Z",
            elapsed_ms=1.5,
        )
        d = r.to_dict()
        assert d["cvar_pct"] == -0.05
        assert d["cvar_amount"] == -500.0
        assert d["breach"] is True
        assert d["breach_level"] == "cvar_95"
        assert d["var_comparison"]["var_pct"] == -0.04
        assert d["elapsed_ms"] == 1.5

    def test_to_dict_none_fields(self):
        r = CVaRResult(
            cvar_pct=_NAN,
            cvar_amount=None,
            method="evt",
            method_used="evt_fail_closed",
            confidence=0.99,
            breach=False,
            breach_level=None,
            threshold=None,
            warning="fail",
            evt_converged=False,
            var_comparison=None,
            timestamp="t",
            elapsed_ms=0.0,
        )
        d = r.to_dict()
        assert d["cvar_amount"] is None
        assert d["var_comparison"] is None
        assert math.isnan(d["cvar_pct"])


# ============================================================
# CVaRCalculator 测试 (覆盖 1,2,3,4,5,6,8,9,10,12 类)
# ============================================================
class CVaRCalculatorTest:
    """CVaRCalculator 全方法单元测试."""

    # --------------------------------------------------------
    # 8. 输入校验 (_validate_returns)
    # --------------------------------------------------------
    def test_validate_empty_returns_false(self):
        calc = CVaRCalculator(CVaRConfig())
        ok, msg = calc._validate_returns([], 30)
        assert ok is False
        assert "空" in msg

    def test_validate_insufficient_samples_returns_false(self):
        calc = CVaRCalculator(CVaRConfig())
        ok, msg = calc._validate_returns([-0.01] * 10, 30)
        assert ok is False
        assert "样本量不足" in msg

    def test_validate_none_value_returns_false(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = [-0.01] * 30
        rets[5] = None  # type: ignore[index]
        ok, msg = calc._validate_returns(rets, 30)
        assert ok is False
        assert "None" in msg

    def test_validate_nan_returns_false(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = [-0.01] * 30
        rets[3] = float("nan")
        ok, msg = calc._validate_returns(rets, 30)
        assert ok is False
        assert "NaN" in msg

    def test_validate_inf_returns_false(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = [-0.01] * 30
        rets[3] = float("inf")
        ok, msg = calc._validate_returns(rets, 30)
        assert ok is False
        assert "inf" in msg

    def test_validate_non_numeric_returns_false(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = [-0.01] * 30
        rets[3] = "not_a_number"  # type: ignore[index]
        ok, msg = calc._validate_returns(rets, 30)
        assert ok is False
        assert "非数值" in msg

    def test_validate_valid_returns_true(self):
        calc = CVaRCalculator(CVaRConfig())
        ok, msg = calc._validate_returns([-0.01] * 30, 30)
        assert ok is True
        assert msg == ""

    # --------------------------------------------------------
    # 1. historical 计算
    # --------------------------------------------------------
    def test_historical_normal_sequence(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = normal_returns(200, seed=42)
        cvar, warn, converged = calc._calculate_historical(rets, 0.95)
        assert not math.isnan(cvar)
        assert cvar < 0  # 95% CVaR 应为负
        assert warn == ""
        assert converged is False

    def test_historical_fat_tail_sequence(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = fat_tail_returns(200, seed=7)
        cvar, _, _ = calc._calculate_historical(rets, 0.95)
        assert not math.isnan(cvar)
        assert cvar < 0

    def test_historical_boundary_sample(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = normal_returns(30, seed=1)
        cvar, _, _ = calc._calculate_historical(rets, 0.95)
        assert not math.isnan(cvar)

    def test_historical_with_none_returns_nan(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = [-0.01] * 30
        rets[0] = None  # type: ignore[index]
        cvar, warn, _ = calc._calculate_historical(rets, 0.95)
        assert math.isnan(cvar)
        assert "historical 计算异常" in warn

    def test_historical_with_string_returns_nan(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = [-0.01] * 30
        rets[0] = "x"  # type: ignore[index]
        cvar, warn, _ = calc._calculate_historical(rets, 0.95)
        assert math.isnan(cvar)
        assert "historical 计算异常" in warn

    def test_historical_with_inf_returns_value(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = [-0.02, -0.01] + [0.0] * 28 + [float("inf")]
        cvar, warn, _ = calc._calculate_historical(rets, 0.95)
        # k=1, sorted[0] = -0.02, 不受 inf 影响
        assert cvar == -0.02
        assert warn == ""

    def test_historical_cvar_ge_var_invariant(self):
        """CVaR (尾部均值) 应 <= VaR (分位数), 即更极端."""
        calc = CVaRCalculator(CVaRConfig())
        rets = normal_returns(200, seed=42)
        cvar, _, _ = calc._calculate_historical(rets, 0.95)
        # VaR = sorted_returns[idx], idx = int(n*0.95)
        sorted_r = sorted(rets)
        var_pct = sorted_r[int(200 * 0.95)]
        assert cvar <= var_pct + 1e-10

    def test_historical_latency_under_100ms(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = normal_returns(200, seed=42)
        start = time.perf_counter()
        for _ in range(100):
            calc._calculate_historical(rets, 0.95)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 100.0, f"100 次耗时 {elapsed_ms:.1f}ms > 100ms"

    # --------------------------------------------------------
    # 2. parametric 计算
    # --------------------------------------------------------
    def test_parametric_normal(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = normal_returns(200, seed=42)
        cvar, warn, _ = calc._calculate_parametric(rets, 0.95, "normal", 5)
        assert not math.isnan(cvar)
        assert cvar < 0
        assert warn == ""

    def test_parametric_student_t(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = fat_tail_returns(200, seed=7)
        cvar, warn, _ = calc._calculate_parametric(rets, 0.95, "student_t", 5)
        assert not math.isnan(cvar)
        assert cvar < 0
        assert warn == ""

    def test_parametric_student_t_dof_10(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = normal_returns(200, seed=3)
        cvar, _, _ = calc._calculate_parametric(rets, 0.99, "student_t", 10)
        assert not math.isnan(cvar)
        assert cvar < 0

    def test_parametric_dof_below_2_returns_nan(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = normal_returns(200, seed=42)
        cvar, warn, _ = calc._calculate_parametric(rets, 0.95, "student_t", 1)
        assert math.isnan(cvar)
        assert "自由度不足" in warn

    def test_parametric_unknown_distribution_returns_nan(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = normal_returns(200, seed=42)
        cvar, warn, _ = calc._calculate_parametric(rets, 0.95, "laplace", 5)
        assert math.isnan(cvar)
        assert "未知分布" in warn

    def test_parametric_with_none_returns_nan(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = [-0.01] * 30
        rets[0] = None  # type: ignore[index]
        cvar, warn, _ = calc._calculate_parametric(rets, 0.95, "normal", 5)
        assert math.isnan(cvar)
        assert "parametric 计算异常" in warn

    # --------------------------------------------------------
    # 3. evt 计算
    # --------------------------------------------------------
    def test_evt_converged_fat_tail(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = fat_tail_returns(200, seed=7)
        with patch(
            "utils.risk.cvar.fit_evt", return_value=make_evt_result(converged=True)
        ):
            cvar, warn, converged, info = calc._calculate_evt(rets, 0.975, 0.95)
        assert converged is True
        assert not math.isnan(cvar)
        assert cvar == -0.025  # es_975
        assert "xi" in info

    def test_evt_short_sequence_not_converge(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = normal_returns(50, seed=1)
        with patch(
            "utils.risk.cvar.fit_evt",
            return_value=make_evt_result(converged=False, error_message="样本不足"),
        ):
            cvar, warn, converged, _ = calc._calculate_evt(rets, 0.95, 0.95)
        assert converged is False
        assert math.isnan(cvar)
        assert "EVT 不收敛" in warn

    def test_evt_xi_above_half_warning(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = fat_tail_returns(200, seed=7)
        with patch(
            "utils.risk.cvar.fit_evt",
            return_value=make_evt_result(converged=True, xi=0.6),
        ):
            cvar, warn, converged, _ = calc._calculate_evt(rets, 0.99, 0.95)
        assert converged is True
        assert "ξ>0.5" in warn

    def test_evt_confidence_975_mapping(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = fat_tail_returns(200, seed=7)
        with patch(
            "utils.risk.cvar.fit_evt",
            return_value=make_evt_result(converged=True, es_975=-0.025),
        ):
            cvar, _, converged, _ = calc._calculate_evt(rets, 0.975, 0.95)
        assert converged is True
        assert cvar == -0.025

    def test_evt_confidence_99_mapping(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = fat_tail_returns(200, seed=7)
        with patch(
            "utils.risk.cvar.fit_evt",
            return_value=make_evt_result(converged=True, es_99=-0.035),
        ):
            cvar, _, converged, _ = calc._calculate_evt(rets, 0.99, 0.95)
        assert converged is True
        assert cvar == -0.035

    def test_evt_confidence_other_calls_evt_var_es(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = fat_tail_returns(200, seed=7)
        with (
            patch(
                "utils.risk.cvar.fit_evt", return_value=make_evt_result(converged=True)
            ),
            patch(
                "utils.risk.cvar.evt_var_es", return_value={"es": -0.04, "var": -0.03}
            ),
        ):
            cvar, _, converged, _ = calc._calculate_evt(rets, 0.95, 0.95)
        assert converged is True
        assert cvar == -0.04

    def test_evt_exception_caught(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = fat_tail_returns(200, seed=7)
        with patch("utils.risk.cvar.fit_evt", side_effect=RuntimeError("evt crash")):
            cvar, warn, converged, info = calc._calculate_evt(rets, 0.95, 0.95)
        assert converged is False
        assert math.isnan(cvar)
        assert "EVT 模块调用异常" in warn

    def test_evt_warning_from_result_propagated(self):
        calc = CVaRCalculator(CVaRConfig())
        rets = fat_tail_returns(200, seed=7)
        with patch(
            "utils.risk.cvar.fit_evt",
            return_value=make_evt_result(converged=True, warning="custom_warn"),
        ):
            _, warn, _, _ = calc._calculate_evt(rets, 0.99, 0.95)
        assert "custom_warn" in warn

    # --------------------------------------------------------
    # 4. 降级链
    # --------------------------------------------------------
    def test_fallback_evt_to_historical(self):
        cfg = CVaRConfig(method="evt", fallback_chain=("evt", "historical"))
        calc = CVaRCalculator(cfg)
        rets = normal_returns(200, seed=42)
        with patch(
            "utils.risk.cvar.fit_evt",
            return_value=make_evt_result(converged=False, error_message="fail"),
        ):
            cvar, warn, method_used, converged, info = calc._apply_fallback_chain(
                rets,
                0.95,
                "evt",
                "EVT 不收敛: fail",
            )
        assert not math.isnan(cvar)
        assert method_used == "historical_fallback"
        assert "降级至 historical" in warn

    def test_fallback_monte_carlo_to_parametric(self):
        cfg = CVaRConfig(fallback_chain=("evt", "historical"))
        calc = CVaRCalculator(cfg)
        rets = normal_returns(200, seed=42)
        # monte_carlo failed -> candidates = ["parametric", "evt", "historical"]
        # parametric 成功
        cvar, warn, method_used, _, _ = calc._apply_fallback_chain(
            rets,
            0.95,
            "monte_carlo",
            "mc fail",
        )
        assert not math.isnan(cvar)
        assert method_used == "parametric_fallback"
        assert "降级至 parametric" in warn

    def test_fallback_chain_exhausted(self):
        cfg = CVaRConfig(fallback_chain=())  # 空降级链
        calc = CVaRCalculator(cfg)
        rets = normal_returns(200, seed=42)
        cvar, warn, method_used, converged, info = calc._apply_fallback_chain(
            rets,
            0.95,
            "evt",
            "fail",
        )
        assert math.isnan(cvar)
        assert "降级链耗尽" in warn
        assert method_used == "evt_fail_closed"

    def test_fallback_evt_fail_then_evt_in_chain_skipped(self):
        """evt 失败, chain 只含 evt -> 耗尽."""
        cfg = CVaRConfig(fallback_chain=("evt",))
        calc = CVaRCalculator(cfg)
        rets = normal_returns(200, seed=42)
        with patch(
            "utils.risk.cvar.fit_evt",
            return_value=make_evt_result(converged=False, error_message="fail"),
        ):
            cvar, warn, method_used, _, _ = calc._apply_fallback_chain(
                rets,
                0.95,
                "evt",
                "fail",
            )
        assert math.isnan(cvar)
        assert "降级链耗尽" in warn

    def test_calculate_validate_failure_no_fallback(self):
        """输入校验失败直接 fail_closed, 不走降级."""
        calc = CVaRCalculator(CVaRConfig())
        result = calc.calculate([], portfolio_value=10000)
        assert math.isnan(result.cvar_pct)
        assert "空" in result.warning
        assert result.method_used == "historical"

    # --------------------------------------------------------
    # calculate 主入口分支
    # --------------------------------------------------------
    def test_calculate_historical_success(self):
        calc, _, _, p = make_calculator(flag_enabled=False)
        try:
            rets = normal_returns(200, seed=42)
            result = calc.calculate(rets, portfolio_value=10000, method="historical")
        finally:
            p.stop()
        assert not math.isnan(result.cvar_pct)
        assert result.method_used == "historical"
        assert result.cvar_amount is not None

    def test_calculate_parametric_success(self):
        calc, _, _, p = make_calculator(flag_enabled=False)
        try:
            rets = normal_returns(200, seed=42)
            result = calc.calculate(rets, method="parametric", distribution="normal")
        finally:
            p.stop()
        assert not math.isnan(result.cvar_pct)
        assert result.method_used == "parametric"

    def test_calculate_evt_success(self):
        calc, _, _, p = make_calculator(flag_enabled=False)
        try:
            rets = fat_tail_returns(200, seed=7)
            with patch(
                "utils.risk.cvar.fit_evt", return_value=make_evt_result(converged=True)
            ):
                result = calc.calculate(rets, method="evt", confidence=0.99)
        finally:
            p.stop()
        assert not math.isnan(result.cvar_pct)
        assert result.method_used == "evt"
        assert result.evt_converged is True

    def test_calculate_evt_fail_fallback_to_historical(self):
        cfg = CVaRConfig(method="evt", fallback_chain=("evt", "historical"))
        calc, _, _, p = make_calculator(config=cfg, flag_enabled=False)
        try:
            rets = normal_returns(200, seed=42)
            with patch(
                "utils.risk.cvar.fit_evt",
                return_value=make_evt_result(converged=False, error_message="fail"),
            ):
                result = calc.calculate(rets, method="evt")
        finally:
            p.stop()
        assert not math.isnan(result.cvar_pct)
        assert result.method_used == "historical_fallback"

    def test_calculate_monte_carlo_success(self):
        calc, _, _, p = make_calculator(flag_enabled=False)
        try:
            rets = normal_returns(200, seed=42)
            result = calc.calculate(rets, method="monte_carlo", distribution="normal")
        finally:
            p.stop()
        assert not math.isnan(result.cvar_pct)
        assert result.method_used == "parametric_fallback"

    def test_calculate_monte_carlo_fail_fallback(self):
        cfg = CVaRConfig(
            method="monte_carlo",
            distribution="student_t",
            dof=1,
            fallback_chain=("evt", "historical"),
        )
        calc, _, _, p = make_calculator(config=cfg, flag_enabled=False)
        try:
            rets = normal_returns(200, seed=42)
            with patch(
                "utils.risk.cvar.fit_evt",
                return_value=make_evt_result(converged=False, error_message="fail"),
            ):
                result = calc.calculate(
                    rets, method="monte_carlo", distribution="student_t", dof=1
                )
        finally:
            p.stop()
        # monte_carlo -> parametric (dof=1 NaN) -> evt (fail) -> historical
        assert not math.isnan(result.cvar_pct)
        assert result.method_used == "historical_fallback"

    def test_calculate_unknown_method_fail_closed(self):
        calc, _, _, p = make_calculator(flag_enabled=False)
        try:
            rets = normal_returns(200, seed=42)
            result = calc.calculate(rets, method="unknown")
        finally:
            p.stop()
        assert math.isnan(result.cvar_pct)
        assert "未知计算方式" in result.warning

    def test_calculate_invalid_confidence_fail_closed(self):
        calc, _, _, p = make_calculator(flag_enabled=False)
        try:
            rets = normal_returns(200, seed=42)
            result = calc.calculate(rets, confidence=0.5)
        finally:
            p.stop()
        assert math.isnan(result.cvar_pct)
        assert "置信水平非法" in result.warning

    def test_calculate_portfolio_value_zero_warning(self):
        calc, _, _, p = make_calculator(flag_enabled=False)
        try:
            rets = normal_returns(200, seed=42)
            result = calc.calculate(rets, portfolio_value=0)
        finally:
            p.stop()
        assert result.cvar_amount == 0.0
        assert "portfolio_value <= 0" in result.warning

    def test_calculate_skip_breach_no_event(self):
        calc, bus, audit, p = make_calculator(flag_enabled=True)
        try:
            rets = [-0.05] * 30  # cvar=-0.05 < -0.04 threshold
            result = calc.calculate(rets, portfolio_value=10000, _skip_breach=True)
        finally:
            p.stop()
        assert result.breach is False
        bus.publish.assert_not_called()
        audit.log.assert_not_called()

    # --------------------------------------------------------
    # 5. 超限检查与事件发布
    # --------------------------------------------------------
    def test_check_breach_95_triggered(self):
        calc = CVaRCalculator(CVaRConfig())
        breach, level, threshold = calc._check_breach(-0.05, 0.95)
        assert breach is True
        assert level == "cvar_95"
        assert threshold == -0.04

    def test_check_breach_95_not_triggered(self):
        calc = CVaRCalculator(CVaRConfig())
        breach, level, threshold = calc._check_breach(-0.03, 0.95)
        assert breach is False
        assert level == "cvar_95"
        assert threshold == -0.04

    def test_check_breach_99_triggered(self):
        calc = CVaRCalculator(CVaRConfig())
        breach, level, threshold = calc._check_breach(-0.07, 0.99)
        assert breach is True
        assert level == "cvar_99"
        assert threshold == -0.06

    def test_check_breach_other_confidence_no_breach(self):
        calc = CVaRCalculator(CVaRConfig())
        breach, level, threshold = calc._check_breach(-0.10, 0.90)
        assert breach is False
        assert level is None
        assert threshold is None

    def test_check_breach_nan_no_breach(self):
        calc = CVaRCalculator(CVaRConfig())
        breach, level, threshold = calc._check_breach(_NAN, 0.95)
        assert breach is False
        assert level is None
        assert threshold is None

    def test_breach_95_publishes_event(self):
        calc, bus, audit, p = make_calculator(flag_enabled=True)
        try:
            rets = [-0.05] * 30
            calc.calculate(rets, portfolio_value=10000, confidence=0.95)
        finally:
            p.stop()
        bus.publish.assert_called_once()
        event = bus.publish.call_args[0][0]
        assert event.event_type == RiskEventType.VAR_BREACH

    def test_breach_99_publishes_event(self):
        calc, bus, audit, p = make_calculator(flag_enabled=True)
        try:
            rets = [-0.07] * 30
            calc.calculate(rets, portfolio_value=10000, confidence=0.99)
        finally:
            p.stop()
        bus.publish.assert_called_once()
        event = bus.publish.call_args[0][0]
        assert event.event_type == RiskEventType.VAR_BREACH

    def test_breach_payload_complete(self):
        calc, bus, audit, p = make_calculator(flag_enabled=True)
        try:
            rets = [-0.05] * 30
            calc.calculate(
                rets, portfolio_value=10000, confidence=0.95, method="historical"
            )
        finally:
            p.stop()
        event = bus.publish.call_args[0][0]
        payload = event.payload
        assert payload["var_type"] == "cvar_95"
        assert payload["breach_pct"] == -0.05
        assert payload["cvar_value"] == -0.05
        assert payload["threshold"] == -0.04
        assert payload["confidence"] == 0.95
        assert payload["method"] == "historical"

    def test_flag_disabled_no_publish(self):
        calc, bus, audit, p = make_calculator(flag_enabled=False)
        try:
            rets = [-0.05] * 30
            calc.calculate(rets, portfolio_value=10000, confidence=0.95)
        finally:
            p.stop()
        bus.publish.assert_not_called()

    def test_is_enabled_exception_no_publish(self):
        calc, bus, audit = CVaRCalculator(CVaRConfig()), MagicMock(), MagicMock()
        calc._bus = bus
        calc._audit_logger = audit
        with patch("utils.risk.cvar.is_enabled", side_effect=ValueError("flag error")):
            calc._publish_breach_event(-0.05, -0.04, 0.95, "historical", "cvar_95")
        bus.publish.assert_not_called()

    def test_bus_exception_fail_open(self):
        calc, bus, audit = CVaRCalculator(CVaRConfig()), MagicMock(), MagicMock()
        calc._bus = bus
        calc._audit_logger = audit
        bus.publish.side_effect = RuntimeError("bus down")
        with patch("utils.risk.cvar.is_enabled", return_value=True):
            # 不应抛异常
            calc._publish_breach_event(-0.05, -0.04, 0.95, "historical", "cvar_95")

    # --------------------------------------------------------
    # 6. 审计日志
    # --------------------------------------------------------
    def test_audit_log_called_on_breach(self):
        calc, bus, audit, p = make_calculator(flag_enabled=True)
        try:
            rets = [-0.05] * 30
            calc.calculate(rets, portfolio_value=10000, confidence=0.95)
        finally:
            p.stop()
        audit.log.assert_called_once()

    def test_audit_log_module_and_sub_module(self):
        calc, bus, audit, p = make_calculator(flag_enabled=True)
        try:
            rets = [-0.07] * 30
            calc.calculate(rets, portfolio_value=10000, confidence=0.99)
        finally:
            p.stop()
        call_kwargs = audit.log.call_args.kwargs
        assert call_kwargs["module"] == "T14_AUDIT"
        assert call_kwargs["context"]["sub_module"] == "cvar"
        assert call_kwargs["context"]["cvar_value"] == -0.07
        assert call_kwargs["context"]["confidence"] == 0.99
        assert call_kwargs["reason"] == "cvar_breach_cvar_99"

    def test_audit_log_written_when_flag_disabled(self):
        """Feature Flag=False 时事件不发布, 但审计仍写."""
        calc, bus, audit, p = make_calculator(flag_enabled=False)
        try:
            rets = [-0.05] * 30
            calc.calculate(rets, portfolio_value=10000, confidence=0.95)
        finally:
            p.stop()
        bus.publish.assert_not_called()
        audit.log.assert_called_once()

    def test_audit_log_exception_not_propagated(self):
        calc, bus, audit = CVaRCalculator(CVaRConfig()), MagicMock(), MagicMock()
        calc._bus = bus
        calc._audit_logger = audit
        audit.log.side_effect = ValueError("audit fail")
        # 不应抛异常
        calc._write_audit_log(-0.05, -0.04, 0.95, "historical", "cvar_95")

    # --------------------------------------------------------
    # 10. VaR 对照
    # --------------------------------------------------------
    def test_var_comparison_enabled(self):
        cfg = CVaRConfig(enable_var_comparison=True)
        calc = CVaRCalculator(cfg)
        rets = normal_returns(200, seed=42)
        cvar, _, _ = calc._calculate_historical(rets, 0.95)
        result = calc._calculate_var_comparison(rets, 0.95, cvar)
        assert result is not None
        assert "var_pct" in result
        assert "cvar_to_var_ratio" in result

    def test_var_comparison_disabled(self):
        cfg = CVaRConfig(enable_var_comparison=False)
        calc = CVaRCalculator(cfg)
        rets = normal_returns(200, seed=42)
        result = calc._calculate_var_comparison(rets, 0.95, -0.02)
        assert result is None

    def test_var_comparison_cvar_ge_var_invariant(self):
        """CVaR <= VaR (更极端), ratio = cvar/var 应 >= 1 (同号负值)."""
        cfg = CVaRConfig(enable_var_comparison=True)
        calc = CVaRCalculator(cfg)
        rets = normal_returns(200, seed=42)
        cvar, _, _ = calc._calculate_historical(rets, 0.95)
        result = calc._calculate_var_comparison(rets, 0.95, cvar)
        assert result is not None
        var_pct = result["var_pct"]
        # CVaR 应 <= VaR
        assert cvar <= var_pct + 1e-10

    def test_var_comparison_var_zero_ratio_nan(self):
        """var_pct=0 时 ratio=NaN."""
        cfg = CVaRConfig(enable_var_comparison=True)
        calc = CVaRCalculator(cfg)
        # 构造 var_pct=0: sorted_returns[idx]=0
        rets = [-0.01] * 28 + [0.0, 0.0]
        # n=30, confidence=0.95, idx=int(30*0.95)=28, sorted[28]=0.0
        result = calc._calculate_var_comparison(rets, 0.95, -0.02)
        assert result is not None
        assert result["var_pct"] == 0.0
        assert math.isnan(result["cvar_to_var_ratio"])

    def test_var_comparison_cvar_nan_ratio_nan(self):
        cfg = CVaRConfig(enable_var_comparison=True)
        calc = CVaRCalculator(cfg)
        rets = normal_returns(200, seed=42)
        result = calc._calculate_var_comparison(rets, 0.95, _NAN)
        assert result is not None
        assert math.isnan(result["cvar_to_var_ratio"])

    # --------------------------------------------------------
    # 12. calculate_scalar
    # --------------------------------------------------------
    def test_scalar_return_pct(self):
        calc, _, _, p = make_calculator(flag_enabled=False)
        try:
            rets = normal_returns(200, seed=42)
            value = calc.calculate_scalar(rets, method="historical")
        finally:
            p.stop()
        assert not math.isnan(value)
        assert value < 0

    def test_scalar_return_amount(self):
        calc, _, _, p = make_calculator(flag_enabled=False)
        try:
            rets = normal_returns(200, seed=42)
            value = calc.calculate_scalar(
                rets, portfolio_value=10000, method="historical", return_amount=True
            )
        finally:
            p.stop()
        assert not math.isnan(value)
        assert value < 0  # cvar_pct * 10000

    def test_scalar_return_amount_no_portfolio_returns_nan(self):
        calc, _, _, p = make_calculator(flag_enabled=False)
        try:
            rets = normal_returns(200, seed=42)
            value = calc.calculate_scalar(rets, method="historical", return_amount=True)
        finally:
            p.stop()
        assert math.isnan(value)

    def test_scalar_skip_breach_no_risk_trigger(self):
        """calculate_scalar 内部 _skip_breach=True, 不触发风控."""
        calc, bus, audit, p = make_calculator(flag_enabled=True)
        try:
            rets = [-0.05] * 30
            value = calc.calculate_scalar(rets, portfolio_value=10000)
        finally:
            p.stop()
        assert not math.isnan(value)
        bus.publish.assert_not_called()
        audit.log.assert_not_called()

    # --------------------------------------------------------
    # 9. 组合级计算
    # --------------------------------------------------------
    def test_portfolio_normal_aggregation(self):
        calc, _, _, p = make_calculator(flag_enabled=False)
        try:
            rets_a = normal_returns(60, seed=1)
            rets_b = normal_returns(60, seed=2)
            positions = [{"code": "A", "weight": 0.6}, {"code": "B", "weight": 0.4}]
            matrix = {"A": rets_a, "B": rets_b}
            result = calc.calculate_portfolio(
                positions, matrix, 100000, method="historical"
            )
        finally:
            p.stop()
        assert not math.isnan(result.cvar_pct)
        assert result.cvar_amount is not None

    def test_portfolio_length_mismatch_warning(self):
        calc, _, _, p = make_calculator(flag_enabled=False)
        try:
            rets_a = normal_returns(60, seed=1)
            rets_b = normal_returns(50, seed=2)
            positions = [{"code": "A", "weight": 0.5}, {"code": "B", "weight": 0.5}]
            matrix = {"A": rets_a, "B": rets_b}
            result = calc.calculate_portfolio(
                positions, matrix, 100000, method="historical"
            )
        finally:
            p.stop()
        assert "序列长度不一致" in result.warning

    def test_portfolio_weight_deviation_warning(self):
        calc, _, _, p = make_calculator(flag_enabled=False)
        try:
            rets_a = normal_returns(60, seed=1)
            rets_b = normal_returns(60, seed=2)
            positions = [{"code": "A", "weight": 0.3}, {"code": "B", "weight": 0.3}]
            matrix = {"A": rets_a, "B": rets_b}
            result = calc.calculate_portfolio(
                positions, matrix, 100000, method="historical"
            )
        finally:
            p.stop()
        assert "权重和偏离" in result.warning

    def test_portfolio_missing_symbol_returns_nan(self):
        calc, _, _, p = make_calculator(flag_enabled=False)
        try:
            positions = [{"code": "A", "weight": 0.5}, {"code": "C", "weight": 0.5}]
            matrix = {"A": normal_returns(60, seed=1)}
            result = calc.calculate_portfolio(
                positions, matrix, 100000, method="historical"
            )
        finally:
            p.stop()
        assert math.isnan(result.cvar_pct)
        assert "缺少收益率序列" in result.warning

    def test_portfolio_combined_warnings(self):
        """长度不一致 + 权重偏离, warning 合并."""
        calc, _, _, p = make_calculator(flag_enabled=False)
        try:
            rets_a = normal_returns(60, seed=1)
            rets_b = normal_returns(50, seed=2)
            positions = [{"code": "A", "weight": 0.3}, {"code": "B", "weight": 0.3}]
            matrix = {"A": rets_a, "B": rets_b}
            result = calc.calculate_portfolio(
                positions, matrix, 100000, method="historical"
            )
        finally:
            p.stop()
        assert "序列长度不一致" in result.warning
        assert "权重和偏离" in result.warning

    # --------------------------------------------------------
    # 补充分支覆盖
    # --------------------------------------------------------
    def test_config_property(self):
        """config property 返回内部配置."""
        cfg = CVaRConfig(method="parametric")
        calc = CVaRCalculator(cfg)
        assert calc.config is cfg
        assert calc.config.method == "parametric"

    def test_fallback_unknown_method_in_chain_skipped(self):
        """降级链中含未知 method 时 continue 跳过 (覆盖 326 continue 分支)."""
        # 未知 method 在前, historical 在后; 未知 -> continue, historical -> 成功
        cfg = CVaRConfig(fallback_chain=("unknown_method", "historical"))
        calc = CVaRCalculator(cfg)
        rets = normal_returns(200, seed=42)
        cvar, warn, method_used, _, _ = calc._apply_fallback_chain(
            rets,
            0.95,
            "evt",
            "fail",
        )
        assert not math.isnan(cvar)
        assert method_used == "historical_fallback"

    def test_portfolio_warnings_and_result_warning_combined(self):
        """portfolio 有 warning 且 calculate 结果也有 warning → 合并 (579-581 分支)."""
        calc, _, _, p = make_calculator(flag_enabled=False)
        try:
            rets_a = normal_returns(60, seed=1)
            rets_b = normal_returns(50, seed=2)  # 长度不一致 -> portfolio warning
            positions = [{"code": "A", "weight": 0.5}, {"code": "B", "weight": 0.5}]
            matrix = {"A": rets_a, "B": rets_b}
            # portfolio_value=0 -> result.warning 含 "portfolio_value <= 0"
            result = calc.calculate_portfolio(positions, matrix, 0, method="historical")
        finally:
            p.stop()
        assert "序列长度不一致" in result.warning
        assert "portfolio_value <= 0" in result.warning

    def test_var_comparison_exception_returns_none(self):
        """_calculate_var_comparison 内部异常 → 返回 None."""
        cfg = CVaRConfig(enable_var_comparison=True)
        calc = CVaRCalculator(cfg)
        # 含 None 的 returns 使 sorted 抛 TypeError
        rets = [None, -0.01, 0.01]  # type: ignore[list-item]
        result = calc._calculate_var_comparison(rets, 0.95, -0.02)
        assert result is None
