"""test_daily_workflow_unit.py — DailyWorkflow 关键 phase 单元测试 (R3 C-1.1)

目标: 为 v8.3_institutional/daily_workflow.py (9147 行/97 方法) 补单元测试,
      覆盖率 0% → ≥40% (针对关键 phase), 为未来 B-3 God Class 拆分铺路.

覆盖 4 个最关键且可测的 phase:
    - phase_check  (L1213): 系统自检 — NTP/风控/C9 兜底价格 fail-closed 分支
    - phase_calibrate (L1453): 收益预测动态校准 — SKIP/FAIL/PASS/DEGRADED/异常
    - phase_market (L1536): 市场状态评估 — 熔断级别 + 数据质量
    - phase_risk   (L1667): 风险预算计算 — 基本流 + 压力测试分支

测试模式 (参考 tests/unit/test_phase_hedge_sim_branch.py):
    1. DailyWorkflow.__new__(DailyWorkflow) 跳过 267 行 __init__ (重依赖)
    2. 手动注入最小属性集 + MagicMock 隔离外部依赖 (NTPSync/RiskManager/CircuitBreaker)
    3. monkeypatch 模块级常量 (V75_READY / V85_READY / CALIBRATE_READY 等)

推迟覆盖的 phase (理由):
    - phase_hedge (800 行) / phase_signal (2000 行) / phase_execute (700 行 God Function):
      需 B-3 God Class 拆分后再补测试, 避免测试随重构反复改写 (YAGNI)
    - phase_hedge 的 _execute_sim_hedge_orders 已由 test_phase_hedge_sim_branch.py 覆盖

R1 联动验证:
    phase_check 的 C9 兜底价格新鲜度检查 (2026-08-01 R1 新增) 在本文件中通过
    mock SystemChecker 验证 ERROR/WARN/PASS 三分支与 fail-closed 阻断逻辑.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_V83_DIR = _PROJECT_ROOT / "v8.3_institutional"
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_V83_DIR))
sys.path.insert(0, str(_V83_DIR / "src"))

# 导入被测模块 (失败则 skip 整个文件, 避免依赖缺失导致收集错误)
try:
    import daily_workflow as dw_module
    from daily_workflow import DailyWorkflow
except Exception as e:  # pragma: no cover
    pytest.skip(f"DailyWorkflow 导入失败 (依赖缺失): {e}", allow_module_level=True)

# C9 检查结果所需符号 (R1 联动)
try:
    from utils.system_check import CheckLevel, CheckStatus
    _HAS_SYSTEM_CHECK = True
except Exception:  # pragma: no cover
    _HAS_SYSTEM_CHECK = False


# ============================================================
# 辅助工厂
# ============================================================


class _FakeCheckResult:
    """轻量 CheckResult 替身 — 使用真实属性值而非 MagicMock

    关键: phase_check 通过 `r.level == CheckLevel.ERROR` 判断阻断,
    MagicMock 的 __eq__ 按身份比较会返回 False, 导致 C9 分支误判.
    使用普通类确保 enum 比较正确.
    """

    def __init__(self, code, level, status, detail="", remediation=""):
        self.code = code
        self.level = level
        self.status = status
        self.detail = detail
        self.remediation = remediation
        self.elapsed_ms = 0.0
        self.is_blocking = (level == CheckLevel.ERROR and status == CheckStatus.FAIL)


def _make_check_result(code, level, status, detail="", remediation=""):
    """构造 C9 检查结果 (轻量 mock, 确保枚举比较正确)"""
    return _FakeCheckResult(code, level, status, detail, remediation)


def _make_workflow(trade_date="2026-08-01", capital=5_000_000):
    """创建简化 DailyWorkflow 实例 (跳过 267 行 __init__ 的重依赖)

    注: 仅注入 4 个目标 phase 所需的最小属性集, 其他 phase 不可用
    """
    wf = DailyWorkflow.__new__(DailyWorkflow)
    # 基本属性
    wf.trade_date = trade_date
    wf.capital = capital
    wf.state = {"phases": {}, "risk_status": {}}
    wf.current_phase_info = None  # 跳过十五五阶段提示
    wf.phase_manager = None
    wf.ntp = None
    wf.rm = None
    wf.cb = None
    # phase_market/phase_risk 可选依赖 (默认 None 跳过相关分支)
    wf.data_quality_monitor = None
    wf.stress_test_engine = None
    wf.risk_budget_opt = None
    wf.barra_decomposer = None
    # _scan_anysearch_news 在 phase_market 末尾被调用, 提供空实现避免 ImportError 链
    wf._scan_anysearch_news = lambda: None
    wf._get_portfolio_positions_for_stress_test = lambda: []
    # WorkflowConfig mock (phase_risk 读取多个配置属性)
    wf.config = MagicMock(name="WorkflowConfig")
    wf.config.STOCK_CAPITAL = 3_000_000
    wf.config.HEDGE_CAPITAL = 2_000_000
    wf.config.STOP_LOSS_RULES = {"科技股": -0.12}
    wf.config.YELLOW_WARNING = 0.06
    wf.config.ORANGE_WARNING = 0.08
    wf.config.RED_WARNING = 0.12
    wf.config.FULL_STOP = 0.15
    wf.config.MOCK_PRICES = {}
    # trade_plan (phase_risk 读取)
    wf.trade_plan = {
        "phase": {"day_capital": 150_000},
        "execution_plan": {
            "morning_total": 80_000,
            "afternoon_total": 70_000,
            "grand_total": 150_000,
        },
    }
    return wf


def _patch_module_constants(monkeypatch, **overrides):
    """批量 patch daily_workflow 模块级常量

    默认值模拟"全模块就绪"环境, 可通过 overrides 覆盖单个常量
    """
    defaults = {
        "V75_READY": True,
        "SHENHUA_READY": True,
        "PHASE_MANAGER_READY": True,
        "HEDGE_FUND_CORE_READY": True,
        "HEDGE_FUND_MODULES_READY": True,
        "INSTITUTIONAL_MODULES_READY": True,
        "RISK_MGT_MODULES_READY": True,
        "ALPHA_MODULES_READY": True,
        "EXECUTION_MODULES_READY": True,
        "ALT_DATA_MODULES_READY": True,
        "V85_READY": False,  # False 以跳过 TimeSync/DataPipeline 真实初始化
        "_V85_FAILURES": [],
        "CALIBRATE_READY": True,
    }
    defaults.update(overrides)
    for key, val in defaults.items():
        monkeypatch.setattr(dw_module, key, val, raising=False)


def _patch_external_classes(monkeypatch, ntp_offset=0.0, rm_mode="NORMAL",
                            rm_factor=1.0, cb_level_name="NORMAL", cb_level_value=0):
    """patch phase_check 直接实例化的外部类 (NTPSync/RiskManager/CircuitBreaker)

    Args:
        ntp_offset: NTPSync().get_offset() 返回值 (<0.05 视为同步)
        rm_mode: RiskManager().mode
        rm_factor: RiskManager().position_size_factor
        cb_level_name: CircuitBreaker().check() 返回对象的 .name
        cb_level_value: CircuitBreaker().check() 返回对象的 .value
    """
    # NTPSync: phase_check L1257 无条件 self.ntp = NTPSync(); L1280 _ntp_instance.get_offset()
    ntp_inst = MagicMock(name="NTPSync_instance")
    ntp_inst.get_offset.return_value = ntp_offset
    ntp_inst.sync_failed_count = 0
    monkeypatch.setattr(dw_module, "NTPSync", lambda *a, **kw: ntp_inst)

    # RiskManager: phase_check L1320 self.rm = RiskManager(total_capital=...)
    rm_inst = MagicMock(name="RiskManager_instance")
    rm_inst.mode = rm_mode
    rm_inst.position_size_factor = rm_factor
    rm_inst.update_drawdown.return_value = {"drawdown": 0.0, "breach": False, "reduce_pct": 0.0}
    monkeypatch.setattr(dw_module, "RiskManager", lambda *a, **kw: rm_inst)

    # CircuitBreaker: phase_check L1322 self.cb = CircuitBreaker(name=...)
    cb_inst = MagicMock(name="CircuitBreaker_instance")
    level_mock = MagicMock(name=f"CircuitLevel<{cb_level_name}>")
    level_mock.name = cb_level_name
    level_mock.value = cb_level_value
    cb_inst.check.return_value = level_mock
    cb_inst.allowed_actions.return_value = {"open_new": True, "force_reduce_pct": 0.0}
    monkeypatch.setattr(dw_module, "CircuitBreaker", lambda *a, **kw: cb_inst)
    return ntp_inst, rm_inst, cb_inst


def _patch_c9(monkeypatch, results):
    """patch utils.system_check.SystemChecker 使 C9 返回指定结果

    Args:
        results: CheckResult 列表 (空列表 = C9 通过无告警)
    """
    if not _HAS_SYSTEM_CHECK:
        pytest.skip("utils.system_check 不可用, 跳过 C9 集成测试")

    class _FakeSystemChecker:
        def __init__(self, *a, **kw):
            self._results = []  # phase_check 构造后会重置为 []

        def check_fallback_price_freshness(self):
            # phase_check 在调用前会执行 _c9_checker._results = [] 清空,
            # 因此必须在此处填充预期结果 (而非 __init__), 否则会被覆盖
            self._results = list(results)

    # phase_check 内部通过 `from utils.system_check import SystemChecker` 导入
    import utils.system_check as sc_module
    monkeypatch.setattr(sc_module, "SystemChecker", _FakeSystemChecker)


# ============================================================
# Phase 1: phase_check 系统自检 (6 测试, 含 C9 R1 联动)
# ============================================================


class TestPhaseCheck:
    """phase_check 系统自检 — 验证 fail-closed 阻断逻辑

    覆盖分支:
        1. V75 模块未就绪 → 降级 PASS
        2. NTP 同步失败 → fail-closed (v8.6.8 P0-03)
        3. 风控初始化失败 → fail-closed
        4. C9 兜底价格 ERROR → fail-closed (R1 联动)
        5. C9 兜底价格 WARN → 告警不阻断 (R1 联动)
        6. 全部通过 → PASS
    """

    def test_v75_not_ready_degraded_pass(self, monkeypatch):
        """V75_READY=False → 降级模式 PASS, 立即返回"""
        _patch_module_constants(monkeypatch, V75_READY=False)
        _patch_external_classes(monkeypatch)
        _patch_c9(monkeypatch, results=[])
        wf = _make_workflow()

        result = wf.phase_check()

        assert result is True
        check_state = wf.state["phases"]["check"]
        assert check_state["status"] == "PASS"
        assert check_state["degraded"] is True
        # V75 未就绪时应立即返回, 不进入 NTP/风控/C9 检查
        assert check_state["checks"]["v75_modules"] is False

    def test_ntp_sync_failure_fail_closed(self, monkeypatch):
        """NTP offset 超 0.05s 阈值 → fail-closed 禁止开仓 (P0-03 回归)

        注: drift 路径 (offset 超阈值但无异常) 在 L1354 触发 fail-closed,
            不设置 ntp_fail_closed 键 (该键仅在 NTP 抛异常时于 L1291 设置)
        """
        _patch_module_constants(monkeypatch)
        _patch_external_classes(monkeypatch, ntp_offset=0.5)  # 0.5s >> 0.05s
        _patch_c9(monkeypatch, results=[])
        wf = _make_workflow()

        result = wf.phase_check()

        assert result is True  # phase_check 始终返回 True (不阻断后续 phase 调度)
        check_state = wf.state["phases"]["check"]
        assert check_state["status"] == "FAIL"
        assert check_state["fail_closed"] is True
        assert "NTP" in check_state["reason"]
        assert check_state["checks"]["ntp_sync"] is False  # offset 超阈值

    def test_ntp_sync_exception_fail_closed(self, monkeypatch):
        """NTP 同步抛异常 → fail-closed + 设置 ntp_fail_closed 标记 (P0-03 异常路径)

        区别于 drift 路径: 异常路径额外设置 checks["ntp_fail_closed"]=True
        和 checks["ntp_failure_reason"]
        """
        _patch_module_constants(monkeypatch)
        # NTPSync.get_offset 抛异常
        ntp_inst = MagicMock(name="NTPSync_instance")
        ntp_inst.get_offset.side_effect = RuntimeError("ntp server unreachable")
        ntp_inst.sync_failed_count = 0
        monkeypatch.setattr(dw_module, "NTPSync", lambda *a, **kw: ntp_inst)
        monkeypatch.setattr(dw_module, "RiskManager",
                            lambda *a, **kw: MagicMock(mode="NORMAL", position_size_factor=1.0,
                                                       update_drawdown=lambda e: {"drawdown": 0.0}))
        monkeypatch.setattr(dw_module, "CircuitBreaker", lambda *a, **kw: MagicMock())
        _patch_c9(monkeypatch, results=[])
        wf = _make_workflow()

        result = wf.phase_check()

        assert result is True
        check_state = wf.state["phases"]["check"]
        assert check_state["status"] == "FAIL"
        assert check_state["fail_closed"] is True
        # 异常路径特有标记
        assert check_state["checks"]["ntp_fail_closed"] is True
        assert "ntp_failure_reason" in check_state["checks"]

    def test_risk_init_failure_fail_closed(self, monkeypatch):
        """RiskManager 初始化抛异常 → fail-closed 禁止开仓"""
        _patch_module_constants(monkeypatch)
        # RiskManager 构造抛异常
        monkeypatch.setattr(dw_module, "NTPSync", lambda *a, **kw: MagicMock(get_offset=lambda: 0.0))
        monkeypatch.setattr(dw_module, "RiskManager",
                            MagicMock(side_effect=RuntimeError("risk init broken")))
        monkeypatch.setattr(dw_module, "CircuitBreaker", lambda *a, **kw: MagicMock())
        _patch_c9(monkeypatch, results=[])
        wf = _make_workflow()

        result = wf.phase_check()

        assert result is True
        check_state = wf.state["phases"]["check"]
        assert check_state["status"] == "FAIL"
        assert check_state["fail_closed"] is True
        assert "风控初始化失败" in check_state["reason"]
        assert check_state["checks"]["risk_manager"] is False

    @pytest.mark.regression
    def test_c9_fallback_price_error_fail_closed(self, monkeypatch):
        """C9 兜底价格 ERROR FAIL → fail-closed (R1 联动回归)

        场景: DEFAULT_FUTURES_PRICES 过期超 30 天, C9 返回 ERROR FAIL
        预期: phase_check 标记 fail_closed=True, reason 含 C9 + 修复建议
        """
        _patch_module_constants(monkeypatch)
        _patch_external_classes(monkeypatch, ntp_offset=0.0)
        c9_err = _make_check_result(
            "C9.2", CheckLevel.ERROR, CheckStatus.FAIL,
            detail="最后更新 2026-06-29, 已过期 33 天 (超 30 天阈值)",
            remediation="立即更新 DEFAULT_FUTURES_PRICES",
        )
        _patch_c9(monkeypatch, results=[c9_err])
        wf = _make_workflow()

        result = wf.phase_check()

        assert result is True
        check_state = wf.state["phases"]["check"]
        assert check_state["status"] == "FAIL"
        assert check_state["fail_closed"] is True
        assert "C9" in check_state["reason"]
        assert "兜底价格新鲜度检查失败" in check_state["reason"]
        # C9 结果应写入 checks
        assert "fallback_price_freshness" in check_state["checks"]

    @pytest.mark.regression
    def test_c9_fallback_price_warn_non_blocking(self, monkeypatch):
        """C9 兜底价格 WARN FAIL → 告警不阻断 (R1 联动回归)

        场景: 兜底价格过期 7-30 天, C9 返回 WARN FAIL
        预期: phase_check 继续, 最终状态 PASS (WARN 仅记录不阻断)
        """
        _patch_module_constants(monkeypatch)
        _patch_external_classes(monkeypatch, ntp_offset=0.0)
        c9_warn = _make_check_result(
            "C9.2", CheckLevel.WARN, CheckStatus.FAIL,
            detail="最后更新 2026-07-20, 已 12 天 (超 7 天告警阈值)",
            remediation="建议尽快更新 DEFAULT_FUTURES_PRICES",
        )
        _patch_c9(monkeypatch, results=[c9_warn])
        wf = _make_workflow()

        result = wf.phase_check()

        assert result is True
        check_state = wf.state["phases"]["check"]
        # WARN 不阻断 → 最终 PASS
        assert check_state["status"] == "PASS"
        assert "fail_closed" not in check_state  # 未触发 fail-closed
        assert "fallback_price_freshness" in check_state["checks"]

    def test_all_pass(self, monkeypatch):
        """全部检查通过 → PASS, 无 fail_closed, 无 degraded"""
        _patch_module_constants(monkeypatch)
        _patch_external_classes(monkeypatch, ntp_offset=0.0, rm_mode="NORMAL", rm_factor=1.0)
        _patch_c9(monkeypatch, results=[])  # C9 无告警
        wf = _make_workflow()

        result = wf.phase_check()

        assert result is True
        check_state = wf.state["phases"]["check"]
        assert check_state["status"] == "PASS"
        assert "fail_closed" not in check_state
        assert "degraded" not in check_state
        assert check_state["checks"]["ntp_sync"] is True
        assert check_state["checks"]["risk_manager"] is True
        assert check_state["checks"]["circuit_breaker"] is True
        assert "fallback_price_freshness" in check_state["checks"]


# ============================================================
# Phase 1.5: phase_calibrate 收益预测校准 (5 测试)
# ============================================================


class TestPhaseCalibrate:
    """phase_calibrate 收益预测动态校准 — 验证 4 个状态分支

    覆盖: SKIP (模块未就绪) / FAIL (校准失败) / PASS (OK) / DEGRADED (降级) / 异常
    """

    def test_skip_when_module_not_ready(self, monkeypatch):
        """CALIBRATE_READY=False → SKIP, 不阻断"""
        _patch_module_constants(monkeypatch, CALIBRATE_READY=False)
        wf = _make_workflow()

        result = wf.phase_calibrate()

        assert result is True
        cal_state = wf.state["phases"]["calibrate"]
        assert cal_state["status"] == "SKIP"
        assert "未导入" in cal_state["reason"]

    def test_fail_when_calibration_status_fail(self, monkeypatch):
        """_run_calibration 返回 status=FAIL → FAIL 状态, 不阻断"""
        _patch_module_constants(monkeypatch, CALIBRATE_READY=True)
        monkeypatch.setattr(dw_module, "_run_calibration",
                            lambda: {"status": "FAIL", "error": "wind_fetch_error"})
        wf = _make_workflow()

        result = wf.phase_calibrate()

        assert result is True  # 校准失败不阻断后续阶段
        cal_state = wf.state["phases"]["calibrate"]
        assert cal_state["status"] == "FAIL"
        assert "FAIL" in cal_state["error"]

    def test_pass_when_calibration_ok(self, monkeypatch):
        """_run_calibration 返回 status=OK → PASS, 含三步明细"""
        _patch_module_constants(monkeypatch, CALIBRATE_READY=True)
        monkeypatch.setattr(dw_module, "_run_calibration", lambda: {
            "status": "OK",
            "step1_update": {"success": 23, "fail": 0, "total_days": 30,
                             "total_symbols": 23, "degraded_reason": None},
            "step2_realized": {"start_date": "2026-01-01", "end_date": "2026-07-31",
                               "years": 0.58, "portfolio_weighted_annualized": 0.092,
                               "market_annualized": 0.045, "market_sharpe": 1.2,
                               "portfolio_weight_total": 1.0},
            "step3_calibration": {"original_weights": {"bull": 0.4, "base": 0.4, "bear": 0.2},
                                  "calibrated_weights": {"bull": 0.5, "base": 0.35, "bear": 0.15},
                                  "calibration_reason": "realized > expected",
                                  "calibrated_expected_annualized": 0.085,
                                  "calibrated_expected_final": 5_850_000},
        })
        wf = _make_workflow()

        result = wf.phase_calibrate()

        assert result is True
        cal_state = wf.state["phases"]["calibrate"]
        assert cal_state["status"] == "PASS"
        assert cal_state["wind_fetch"]["success"] == 23
        assert cal_state["realized"]["portfolio_weighted_annualized"] == 0.092
        assert cal_state["calibration"]["calibrated_expected_annualized"] == 0.085

    def test_degraded_when_calibration_degraded(self, monkeypatch):
        """_run_calibration 返回 status=DEGRADED → DEGRADED (Wind 拉取失败, 用历史数据)"""
        _patch_module_constants(monkeypatch, CALIBRATE_READY=True)
        monkeypatch.setattr(dw_module, "_run_calibration", lambda: {
            "status": "DEGRADED",
            "step1_update": {"success": 0, "fail": 23, "total_days": 0,
                             "total_symbols": 23, "degraded_reason": "wind_mcp_timeout"},
            "step2_realized": {"portfolio_weighted_annualized": 0.088},
            "step3_calibration": {"calibrated_expected_annualized": 0.082},
        })
        wf = _make_workflow()

        result = wf.phase_calibrate()

        assert result is True
        cal_state = wf.state["phases"]["calibrate"]
        assert cal_state["status"] == "DEGRADED"
        assert cal_state["wind_fetch"]["degraded_reason"] == "wind_mcp_timeout"

    def test_exception_non_blocking(self, monkeypatch):
        """_run_calibration 抛异常 → FAIL 但不阻断 (return True)"""
        _patch_module_constants(monkeypatch, CALIBRATE_READY=True)
        monkeypatch.setattr(dw_module, "_run_calibration",
                            MagicMock(side_effect=RuntimeError("unexpected crash")))
        wf = _make_workflow()

        result = wf.phase_calibrate()

        assert result is True
        cal_state = wf.state["phases"]["calibrate"]
        assert cal_state["status"] == "FAIL"
        assert "unexpected crash" in cal_state["error"]


# ============================================================
# Phase 2: phase_market 市场状态评估 (4 测试)
# ============================================================


class TestPhaseMarket:
    """phase_market 市场状态评估 — 验证熔断级别与 build_allowed 联动

    覆盖: 正常级别 / LEVEL_3 暂停建仓 / 数据质量监控分支 / CircuitBreaker 异常降级
    """

    def test_normal_level_allows_build(self, monkeypatch):
        """熔断级别 NORMAL (value=0) → build_allowed=True"""
        _patch_module_constants(monkeypatch)
        wf = _make_workflow()
        # 预置 cb (跳过懒初始化)
        cb = MagicMock(name="CircuitBreaker")
        level = MagicMock(name="CircuitLevel<NORMAL>")
        level.name = "NORMAL"
        level.value = 0
        cb.check.return_value = level
        cb.allowed_actions.return_value = {"open_new": True, "force_reduce_pct": 0.0}
        wf.cb = cb

        returned_level = wf.phase_market()

        assert returned_level is level
        mkt_state = wf.state["phases"]["market"]
        assert mkt_state["status"] == "PASS"
        assert mkt_state["circuit_level"] == "NORMAL"
        assert mkt_state["build_allowed"] is True
        cb.check.assert_called_once()

    def test_level3_blocks_build(self, monkeypatch):
        """熔断级别 LEVEL_3 (value=3) → build_allowed=False, 暂停建仓"""
        _patch_module_constants(monkeypatch)
        wf = _make_workflow()
        cb = MagicMock(name="CircuitBreaker")
        level = MagicMock(name="CircuitLevel<LEVEL_3>")
        level.name = "LEVEL_3"
        level.value = 3
        cb.check.return_value = level
        cb.allowed_actions.return_value = {"open_new": False, "force_reduce_pct": 0.5}
        wf.cb = cb

        wf.phase_market()

        mkt_state = wf.state["phases"]["market"]
        assert mkt_state["build_allowed"] is False
        assert mkt_state["circuit_level"] == "LEVEL_3"
        assert mkt_state["actions"]["force_reduce_pct"] == 0.5

    def test_data_quality_monitor_branch_runs(self, monkeypatch):
        """data_quality_monitor 非 None → 执行数据质量检查并写入 state"""
        _patch_module_constants(monkeypatch)
        wf = _make_workflow()
        cb = MagicMock(name="CircuitBreaker")
        level = MagicMock(name="CircuitLevel<NORMAL>")
        level.name = "NORMAL"
        level.value = 0
        cb.check.return_value = level
        cb.allowed_actions.return_value = {"open_new": True, "force_reduce_pct": 0.0}
        wf.cb = cb

        # 数据质量监控 mock
        dq_report = MagicMock(name="DataQualityReport")
        dq_report.overall_score = 95.0
        dq_report.critical_count = 0
        dq_report.error_count = 0
        dq_report.warning_count = 2
        dq_report.passed = True
        dq_monitor = MagicMock(name="DataQualityMonitor")
        dq_monitor.check_market_data.return_value = dq_report
        wf.data_quality_monitor = dq_monitor
        # 提供持仓数据 (否则 data_for_check 为空, 跳过检查)
        wf._get_portfolio_positions_for_stress_test = lambda: [
            {"code": "510300.SH", "price": 4.0, "amount": 400_000, "volume": 1000},
        ]

        wf.phase_market()

        mkt_state = wf.state["phases"]["market"]
        assert "data_quality" in mkt_state
        assert mkt_state["data_quality"]["score"] == 95.0
        assert mkt_state["data_quality"]["passed"] is True
        dq_monitor.check_market_data.assert_called_once()

    def test_circuit_breaker_exception_falls_back_to_safe_level(self, monkeypatch):
        """CircuitBreaker.check 抛异常 → 降级到 _SafeLevel.NORMAL, 不崩溃"""
        _patch_module_constants(monkeypatch)
        wf = _make_workflow()
        cb = MagicMock(name="CircuitBreaker")
        cb.check.side_effect = TypeError("cb broken")
        wf.cb = cb

        returned_level = wf.phase_market()

        # 应返回降级 level (非崩溃)
        assert returned_level is not None
        mkt_state = wf.state["phases"]["market"]
        assert mkt_state["status"] == "PASS"
        # 降级 level 视为非 LEVEL_3 → 允许建仓
        assert mkt_state["build_allowed"] is True


# ============================================================
# Phase 3: phase_risk 风险预算计算 (3 测试)
# ============================================================


class TestPhaseRisk:
    """phase_risk 风险预算计算 — 验证基本流与可选分支

    覆盖: 基本流 (无压力测试) / 压力测试分支 / 风险预算再平衡分支
    """

    def test_basic_flow_passes(self, monkeypatch):
        """基本流: rm 就绪 + 无压力测试引擎 → PASS, 返回 risk_status"""
        _patch_module_constants(monkeypatch)
        wf = _make_workflow()
        rm = MagicMock(name="RiskManager")
        rm.mode = "NORMAL"
        rm.position_size_factor = 1.0
        rm.update_drawdown.return_value = {"drawdown": 0.0, "breach": False, "reduce_pct": 0.0}
        wf.rm = rm

        risk_status = wf.phase_risk()

        # 返回值是 risk_status dict
        assert isinstance(risk_status, dict)
        assert risk_status["equity"] == 5_000_000
        assert risk_status["mode"] == "NORMAL"
        assert risk_status["stock_capital"] == 3_000_000
        assert risk_status["hedge_capital"] == 2_000_000
        assert risk_status["day_capital"] == 150_000
        assert risk_status["build_ratio"] == pytest.approx(150_000 / 3_000_000)
        assert risk_status["var_95_limit"] == 0.05
        assert risk_status["var_99_limit"] == 0.08
        # state 同步写入
        risk_state = wf.state["phases"]["risk"]
        assert risk_state["status"] == "PASS"
        assert wf.state["risk_status"] is risk_status
        rm.update_drawdown.assert_called_once_with(5_000_000)

    def test_stress_test_branch_runs(self, monkeypatch):
        """stress_test_engine 非 None + 有持仓 → 执行压力测试并写入 risk_stress_test"""
        _patch_module_constants(monkeypatch)
        wf = _make_workflow()
        rm = MagicMock(name="RiskManager")
        rm.mode = "NORMAL"
        rm.position_size_factor = 1.0
        rm.update_drawdown.return_value = {"drawdown": 0.0, "breach": False}
        wf.rm = rm

        # 压力测试引擎 mock
        worst = MagicMock(name="WorstScenario")
        worst.scenario_name = "2008金融危机"
        worst.portfolio_return = -0.18
        worst.portfolio_pnl = -900_000
        worst.is_breach = True
        stress_engine = MagicMock(name="StressTestEngine")
        stress_engine.run_all_scenarios.return_value = [worst]
        stress_engine.summarize.return_value = {
            "n_scenarios": 5,
            "worst_scenario": "2008金融危机",
            "worst_return": -0.18,
            "worst_pnl": -900_000,
            "best_return": 0.02,
            "avg_return": -0.05,
            "n_breaches": 1,
            "breach_scenarios": ["2008金融危机"],
            "avg_var_change": 0.03,
        }
        stress_engine.get_worst_scenario.return_value = worst
        stress_engine.risk_threshold = -0.15
        wf.stress_test_engine = stress_engine
        wf._get_portfolio_positions_for_stress_test = lambda: [
            {"code": "510300.SH", "amount": 5_000_000},
        ]

        wf.phase_risk()

        stress_state = wf.state["phases"]["risk_stress_test"]
        assert stress_state["n_scenarios"] == 5
        assert stress_state["worst_scenario"] == "2008金融危机"
        assert stress_state["worst_pnl"] == -900_000
        assert stress_state["n_breaches"] == 1
        stress_engine.run_all_scenarios.assert_called_once()
        stress_engine.summarize.assert_called_once()

    def test_risk_budget_rebalance_triggered_when_te_exceeds_budget(self, monkeypatch):
        """risk_budget_opt + barra_decomposer 就绪 + TE>5% → 触发再平衡建议"""
        _patch_module_constants(monkeypatch)
        wf = _make_workflow()
        rm = MagicMock(name="RiskManager")
        rm.mode = "NORMAL"
        rm.position_size_factor = 1.0
        rm.update_drawdown.return_value = {"drawdown": 0.0, "breach": False}
        wf.rm = rm
        # Barra 报告显示 TE=8% > 5% 预算
        wf.state["phases"]["report_barra"] = {"active_risk": 0.08}
        wf.risk_budget_opt = MagicMock(name="RiskBudgetOpt")
        wf.barra_decomposer = MagicMock(name="BarraDecomposer")

        wf.phase_risk()

        assert wf.state["phases"]["risk_budget_rebalance_needed"] is True
        assert wf.state["phases"]["risk_budget_target_te"] == 0.05
        assert wf.state["phases"]["risk_budget_current_te"] == 0.08
