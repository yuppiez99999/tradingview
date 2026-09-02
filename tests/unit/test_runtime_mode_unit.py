"""utils/runtime_mode 统一三态开关单测 (P1-1)

覆盖: env 读取、编程覆盖优先级、reset、DailyWorkflow 融合逻辑、
CLI env 预设 (QUANT_DRY_RUN/QUANT_SANDBOX)。
"""

import pytest

from utils.runtime_mode import (
    current_mode,
    env_flag,
    is_dry_run,
    is_offline,
    is_sandbox,
    reset_mode,
    set_mode,
)


@pytest.fixture(autouse=True)
def _clean_env_and_overrides(monkeypatch):
    """每个测试前清空三态 env 与编程覆盖, 避免相互污染"""
    for var in ("QUANT_OFFLINE", "QUANT_DRY_RUN", "QUANT_SANDBOX"):
        monkeypatch.delenv(var, raising=False)
    reset_mode()
    yield
    reset_mode()


class TestEnvReading:
    def test_defaults_all_false(self):
        assert not is_offline()
        assert not is_dry_run()
        assert not is_sandbox()

    @pytest.mark.parametrize("value", ["1", "true", "YES", "On"])
    def test_offline_env_variants(self, monkeypatch, value):
        monkeypatch.setenv("QUANT_OFFLINE", value)
        assert is_offline()

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
    def test_offline_env_falsy(self, monkeypatch, value):
        monkeypatch.setenv("QUANT_OFFLINE", value)
        assert not is_offline()

    def test_dry_run_env(self, monkeypatch):
        monkeypatch.setenv("QUANT_DRY_RUN", "1")
        assert is_dry_run()

    def test_sandbox_env(self, monkeypatch):
        monkeypatch.setenv("QUANT_SANDBOX", "1")
        assert is_sandbox()

    def test_three_states_independent(self, monkeypatch):
        """三态互不蕴含: 只开 OFFLINE 不开 DRY_RUN/SANDBOX"""
        monkeypatch.setenv("QUANT_OFFLINE", "1")
        assert is_offline() and not is_dry_run() and not is_sandbox()

    def test_env_read_at_call_time(self, monkeypatch):
        """运行中/conftest 动态设置 env 也能生效 (非 import 时缓存)"""
        assert not is_offline()
        monkeypatch.setenv("QUANT_OFFLINE", "1")
        assert is_offline()


class TestProgrammaticOverride:
    def test_set_mode_overrides_env(self, monkeypatch):
        monkeypatch.setenv("QUANT_DRY_RUN", "1")
        set_mode(dry_run=False)  # 显式覆盖 env
        assert not is_dry_run()

    def test_set_mode_partial(self, monkeypatch):
        monkeypatch.setenv("QUANT_OFFLINE", "1")
        monkeypatch.setenv("QUANT_DRY_RUN", "1")
        set_mode(offline=False)  # 只覆盖 offline, dry_run 仍走 env
        assert not is_offline()
        assert is_dry_run()

    def test_reset_mode_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("QUANT_SANDBOX", "1")
        set_mode(sandbox=False)
        assert not is_sandbox()
        reset_mode()
        assert is_sandbox()  # 回落到 env

    def test_env_flag_helper(self, monkeypatch):
        monkeypatch.setenv("QUANT_DRY_RUN", "1")
        assert env_flag("QUANT_DRY_RUN") is True
        assert env_flag("QUANT_OFFLINE") is False


class TestCurrentMode:
    def test_snapshot_contains_all_three(self, monkeypatch):
        monkeypatch.setenv("QUANT_DRY_RUN", "1")
        snap = current_mode()
        assert snap["dry_run"] is True
        assert snap["offline"] is False
        assert snap["sandbox"] is False
        assert snap["source"]["dry_run"] == "env"

    def test_snapshot_source_override(self):
        set_mode(sandbox=True)
        snap = current_mode()
        assert snap["sandbox"] is True
        assert snap["source"]["sandbox"] == "override"


class TestExternalDataSourceDelegation:
    """P0-2 的 _offline_mode 应委托本模块 (语义不变)"""

    def test_offline_mode_delegates(self, monkeypatch):
        from utils.external_data_source import _offline_mode

        monkeypatch.setenv("QUANT_OFFLINE", "true")
        assert _offline_mode() is True
        monkeypatch.setenv("QUANT_OFFLINE", "0")
        assert _offline_mode() is False

    def test_programmatic_override_short_circuits_network(self, monkeypatch):
        """编程覆盖也能短路外网 (不依赖 env)"""
        from utils.external_data_source import _session_get

        set_mode(offline=True)
        with pytest.raises(ConnectionError, match="QUANT_OFFLINE"):
            _session_get("https://api.example.com/test")


class TestDailyWorkflowFusion:
    """v8.3 DailyWorkflow 与全局开关取或 (防漏传参数触发实盘)

    注: v8.3_institutional 目录名含 "." 且数字开头, 不是合法包名,
    与 tests/e2e/test_eod_dry_run.py 同法 — sys.path 插入目录后导入。
    """

    @pytest.fixture(autouse=True)
    def _import_workflow(self, monkeypatch):
        from pathlib import Path

        v83_dir = Path(__file__).resolve().parents[2] / "v8.3_institutional"
        monkeypatch.syspath_prepend(str(v83_dir))
        from daily_workflow import DailyWorkflow  # noqa: F401

        self.DailyWorkflow = DailyWorkflow

    def test_global_dry_run_forces_workflow_dry_run(self, monkeypatch):
        monkeypatch.setenv("QUANT_DRY_RUN", "1")
        wf = self.DailyWorkflow(trade_date="2026-08-29")
        assert wf.dry_run is True
        assert wf.state["dry_run"] is True

    def test_explicit_sim_mode_still_works(self, monkeypatch):
        monkeypatch.delenv("QUANT_SANDBOX", raising=False)
        wf = self.DailyWorkflow(trade_date="2026-08-29", sim_mode=True)
        # 测试环境无 sim_broker_integration 时 sim_mode 会降级 False (既有行为),
        # 用 _sim_mode_requested 验证请求被正确接收
        assert wf._sim_mode_requested is True

    def test_no_env_no_flags_normal_mode(self):
        wf = self.DailyWorkflow(trade_date="2026-08-29")
        assert wf.dry_run is False
        assert wf.sim_mode is False
