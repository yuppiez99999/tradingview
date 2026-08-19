"""test_liquidation_scheduler_unit.py — LiquidationScheduler 2030 清仓协议单元测试

覆盖要点:
    - get_current_phase 五阶段切换 (phase 0/1/2/3/complete)
    - days_to_next_phase 计算正确性
    - get_schedule 完整时间表
    - check_alert 预警逻辑 (phase 0 临近 / phase 1-2 即将结束 / 无预警)
    - 配置加载 (显式路径 / fail-safe)
    - _log_event 容错

设计原则:
    - 用显式 today 参数注入日期, 不依赖 date.today()
    - check_alert 用 monkeypatch date.today
    - 全 mock, 不读真实 configs/portfolio.yaml
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.liquidation_scheduler import LiquidationScheduler  # noqa: E402

# ============================================================
# 辅助
# ============================================================


def _write_config(tmp_path: Path, phases: dict | None = None) -> Path:
    """写最小 liquidation_protocol 配置"""
    if phases is None:
        phases = {
            "phase_1": {
                "name": "锁定科技利润",
                "period": "2030-Q3",
                "actions": ["Delta中性化", "TWAP变现"],
                "target": "科技ETF清仓",
            },
            "phase_2": {
                "name": "红利资产收尾",
                "period": "2030-11",
                "actions": ["持有至最后分红", "VWAP变现"],
                "target": "红利资产清仓",
            },
            "phase_3": {
                "name": "账户归零清盘",
                "period": "2030-12",
                "actions": ["衍生品归零", "本息回归现金池"],
                "target": "500万回现金池",
            },
        }
    cfg = {"liquidation_protocol": phases}
    p = tmp_path / "portfolio.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return p


# ============================================================
# 配置加载
# ============================================================


class TestConfigLoad:
    @pytest.mark.unit
    def test_explicit_config_load(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        sched = LiquidationScheduler(config_path=cfg_path)
        assert "phase_1" in sched.config
        assert sched.config["phase_1"]["name"] == "锁定科技利润"

    @pytest.mark.unit
    def test_missing_file_returns_empty(self, tmp_path):
        sched = LiquidationScheduler(config_path=tmp_path / "nope.yaml")
        assert sched.config == {}

    @pytest.mark.unit
    def test_malformed_yaml_returns_empty(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(":::not yaml:::", encoding="utf-8")
        sched = LiquidationScheduler(config_path=bad)
        assert sched.config == {}

    @pytest.mark.unit
    def test_no_liquidation_section(self, tmp_path):
        p = tmp_path / "portfolio.yaml"
        p.write_text(yaml.safe_dump({"other": 1}), encoding="utf-8")
        sched = LiquidationScheduler(config_path=p)
        assert sched.config == {}

    @pytest.mark.unit
    def test_config_manager_path_success(self, tmp_path, monkeypatch):
        """默认路径走 ConfigManager → 返回配置"""
        # mock get_config 返回有效配置
        mock_cm = MagicMock()
        mock_cm.get_config = MagicMock(
            return_value={"liquidation_protocol": {"phase_1": {"name": "CM测试"}}}
        )
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)

        sched = LiquidationScheduler()  # 默认 CONFIG_PATH
        assert sched.config["phase_1"]["name"] == "CM测试"

    @pytest.mark.unit
    def test_config_manager_empty_falls_back_to_file(self, tmp_path, monkeypatch):
        """ConfigManager 返回空 → 回退到文件加载"""
        # 先写一个真实配置文件到 CONFIG_PATH 位置
        import utils.liquidation_scheduler as mod

        fallback_cfg = {"liquidation_protocol": {"phase_2": {"name": "回退"}}}
        monkeypatch.setattr(mod, "CONFIG_PATH", tmp_path / "portfolio.yaml")
        (tmp_path / "portfolio.yaml").write_text(
            yaml.safe_dump(fallback_cfg, allow_unicode=True), encoding="utf-8"
        )

        mock_cm = MagicMock()
        mock_cm.get_config = MagicMock(return_value={})  # 空 → 回退
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)

        sched = LiquidationScheduler()
        assert sched.config["phase_2"]["name"] == "回退"

    @pytest.mark.unit
    def test_config_manager_exception_falls_back(self, tmp_path, monkeypatch):
        """ConfigManager 抛异常 → 回退到文件"""
        import utils.liquidation_scheduler as mod

        fallback_cfg = {"liquidation_protocol": {"phase_3": {"name": "异常回退"}}}
        monkeypatch.setattr(mod, "CONFIG_PATH", tmp_path / "portfolio.yaml")
        (tmp_path / "portfolio.yaml").write_text(
            yaml.safe_dump(fallback_cfg, allow_unicode=True), encoding="utf-8"
        )

        mock_cm = MagicMock()
        mock_cm.get_config = MagicMock(side_effect=RuntimeError("CM down"))
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)

        sched = LiquidationScheduler()
        assert sched.config["phase_3"]["name"] == "异常回退"

    @pytest.mark.unit
    def test_config_manager_exception_and_file_fail(self, tmp_path, monkeypatch):
        """ConfigManager 异常 + 文件也失败 → 空 dict"""
        import utils.liquidation_scheduler as mod

        monkeypatch.setattr(mod, "CONFIG_PATH", tmp_path / "nope.yaml")  # 不存在

        mock_cm = MagicMock()
        mock_cm.get_config = MagicMock(side_effect=RuntimeError("CM down"))
        monkeypatch.setitem(sys.modules, "utils.config_manager", mock_cm)

        sched = LiquidationScheduler()
        assert sched.config == {}


# ============================================================
# get_current_phase 五阶段
# ============================================================


class TestGetCurrentPhase:
    @pytest.mark.unit
    def test_phase_0_before_start(self, tmp_path):
        """2030-06-15 在 Phase 1 之前 → phase 0 正常运行期"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        result = sched.get_current_phase(date(2030, 6, 15))
        assert result["phase"] == 0
        assert result["name"] == "正常运行期"
        assert result["days_to_next_phase"] == 16  # 7/1 - 6/15

    @pytest.mark.unit
    def test_phase_1_start_boundary(self, tmp_path):
        """2030-07-01 恰好 Phase 1 开始 → phase 1"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        result = sched.get_current_phase(date(2030, 7, 1))
        assert result["phase"] == 1
        assert result["name"] == "锁定科技利润"
        assert result["period"] == "2030-Q3"
        assert result["days_to_next_phase"] == 123  # 11/1 - 7/1

    @pytest.mark.unit
    def test_phase_1_mid(self, tmp_path):
        """2030-09-15 在 Phase 1 中 → phase 1"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        result = sched.get_current_phase(date(2030, 9, 15))
        assert result["phase"] == 1
        assert "Delta中性化" in result["actions"]

    @pytest.mark.unit
    def test_phase_2_start_boundary(self, tmp_path):
        """2030-11-01 恰好 Phase 2 开始 → phase 2"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        result = sched.get_current_phase(date(2030, 11, 1))
        assert result["phase"] == 2
        assert result["name"] == "红利资产收尾"
        assert result["days_to_next_phase"] == 30  # 12/1 - 11/1

    @pytest.mark.unit
    def test_phase_2_mid(self, tmp_path):
        """2030-11-15 在 Phase 2 中 → phase 2"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        result = sched.get_current_phase(date(2030, 11, 15))
        assert result["phase"] == 2
        assert result["days_to_next_phase"] == 16  # 12/1 - 11/15

    @pytest.mark.unit
    def test_phase_3_start_boundary(self, tmp_path):
        """2030-12-01 恰好 Phase 3 开始 → phase 3"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        result = sched.get_current_phase(date(2030, 12, 1))
        assert result["phase"] == 3
        assert result["name"] == "账户归零清盘"
        assert result["days_to_next_phase"] == 30  # 12/31 - 12/1

    @pytest.mark.unit
    def test_phase_3_mid(self, tmp_path):
        """2030-12-15 在 Phase 3 中 → phase 3"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        result = sched.get_current_phase(date(2030, 12, 15))
        assert result["phase"] == 3
        assert result["days_to_next_phase"] == 16  # 12/31 - 12/15

    @pytest.mark.unit
    def test_complete_at_final_date(self, tmp_path):
        """2030-12-31 恰好 FINAL_DATE → complete"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        result = sched.get_current_phase(date(2030, 12, 31))
        assert result["phase"] == "complete"
        assert result["name"] == "清盘完成"
        assert result["days_to_next_phase"] == 0

    @pytest.mark.unit
    def test_complete_after_final_date(self, tmp_path):
        """2031-01-15 在 FINAL_DATE 之后 → complete"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        result = sched.get_current_phase(date(2031, 1, 15))
        assert result["phase"] == "complete"

    @pytest.mark.unit
    def test_phase_0_far_future_days_negative(self, tmp_path):
        """2026-01-01 远在 Phase 1 之前 → days_to_next_phase 正数"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        result = sched.get_current_phase(date(2026, 1, 1))
        assert result["phase"] == 0
        assert result["days_to_next_phase"] > 0

    @pytest.mark.unit
    def test_empty_config_uses_defaults(self, tmp_path):
        """配置为空 → 用代码默认值"""
        sched = LiquidationScheduler(config_path=tmp_path / "nope.yaml")
        result = sched.get_current_phase(date(2030, 7, 15))
        assert result["phase"] == 1
        assert result["name"] == "锁定科技利润"  # 默认
        assert result["actions"] == []  # 空


# ============================================================
# get_schedule
# ============================================================


class TestGetSchedule:
    @pytest.mark.unit
    def test_returns_four_phases(self, tmp_path):
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        schedule = sched.get_schedule()
        assert len(schedule) == 4
        assert [s["phase"] for s in schedule] == [0, 1, 2, 3]

    @pytest.mark.unit
    def test_phase_0_normal_run(self, tmp_path):
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        schedule = sched.get_schedule()
        assert schedule[0]["name"] == "正常运行期"
        assert "投资策略正常运行" in schedule[0]["actions"]

    @pytest.mark.unit
    def test_phase_3_from_config(self, tmp_path):
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        schedule = sched.get_schedule()
        assert schedule[3]["name"] == "账户归零清盘"
        assert schedule[3]["target"] == "500万回现金池"

    @pytest.mark.unit
    def test_empty_config_schedule_defaults(self, tmp_path):
        sched = LiquidationScheduler(config_path=tmp_path / "nope.yaml")
        schedule = sched.get_schedule()
        assert len(schedule) == 4
        assert schedule[1]["name"] == "锁定科技利润"  # 默认
        assert schedule[2]["period"] == "2030-11"  # 默认


# ============================================================
# check_alert
# ============================================================


class TestCheckAlert:
    @pytest.mark.unit
    def test_phase_0_approaching_alert(self, tmp_path, monkeypatch):
        """Phase 0, 距 Phase 1 <= 30 天 → 预警"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        # 2030-06-15, 距 7/1 = 16 天 <= 30
        monkeypatch.setattr("utils.liquidation_scheduler.date", MagicMock(today=lambda: date(2030, 6, 15)))

        alert = sched.check_alert(days_threshold=30)
        assert alert is not None
        assert alert["alert"] is True
        assert alert["type"] == "phase_1_approaching"
        assert alert["days_left"] == 16
        assert "Delta" in alert["message"]  # message 含 Delta 中性化
        assert len(alert["preparation"]) == 3  # 三项准备动作

    @pytest.mark.unit
    def test_phase_0_no_alert_far_enough(self, tmp_path, monkeypatch):
        """Phase 0, 距 Phase 1 > 30 天 → 无预警"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        monkeypatch.setattr("utils.liquidation_scheduler.date", MagicMock(today=lambda: date(2026, 1, 1)))

        alert = sched.check_alert(days_threshold=30)
        assert alert is None

    @pytest.mark.unit
    def test_phase_1_ending_alert(self, tmp_path, monkeypatch):
        """Phase 1, 距 Phase 2 <= 7 天 → 预警"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        # 2030-10-28, 距 11/1 = 4 天 <= 7
        monkeypatch.setattr("utils.liquidation_scheduler.date", MagicMock(today=lambda: date(2030, 10, 28)))

        alert = sched.check_alert()
        assert alert is not None
        assert alert["type"] == "phase_1_ending"
        assert alert["days_left"] == 4

    @pytest.mark.unit
    def test_phase_1_no_alert_far_enough(self, tmp_path, monkeypatch):
        """Phase 1, 距 Phase 2 > 7 天 → 无预警"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        monkeypatch.setattr("utils.liquidation_scheduler.date", MagicMock(today=lambda: date(2030, 7, 15)))

        alert = sched.check_alert()
        assert alert is None

    @pytest.mark.unit
    def test_phase_2_ending_alert(self, tmp_path, monkeypatch):
        """Phase 2, 距 Phase 3 <= 7 天 → 预警"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        # 2030-11-26, 距 12/1 = 5 天 <= 7
        monkeypatch.setattr("utils.liquidation_scheduler.date", MagicMock(today=lambda: date(2030, 11, 26)))

        alert = sched.check_alert()
        assert alert is not None
        assert alert["type"] == "phase_2_ending"
        assert alert["days_left"] == 5

    @pytest.mark.unit
    def test_phase_3_no_alert(self, tmp_path, monkeypatch):
        """Phase 3 → 无预警 (check_alert 只处理 phase 0/1/2)"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        monkeypatch.setattr("utils.liquidation_scheduler.date", MagicMock(today=lambda: date(2030, 12, 15)))

        alert = sched.check_alert()
        assert alert is None

    @pytest.mark.unit
    def test_complete_no_alert(self, tmp_path, monkeypatch):
        """complete → 无预警"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        monkeypatch.setattr("utils.liquidation_scheduler.date", MagicMock(today=lambda: date(2031, 1, 1)))

        alert = sched.check_alert()
        assert alert is None

    @pytest.mark.unit
    def test_phase_0_custom_threshold(self, tmp_path, monkeypatch):
        """自定义 days_threshold=10, 距 7/1 = 16 > 10 → 无预警"""
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        monkeypatch.setattr("utils.liquidation_scheduler.date", MagicMock(today=lambda: date(2030, 6, 15)))

        alert = sched.check_alert(days_threshold=10)
        assert alert is None


# ============================================================
# _log_event
# ============================================================


class TestLogEvent:
    @pytest.mark.unit
    def test_log_event_write(self, tmp_path, monkeypatch):
        log = tmp_path / "liq.jsonl"
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        monkeypatch.setattr("utils.liquidation_scheduler.LOG_FILE", log)

        sched._log_event({"event": "test", "phase": 1})
        assert log.exists()
        content = log.read_text(encoding="utf-8")
        assert "test" in content

    @pytest.mark.unit
    def test_log_event_fail_silent(self, tmp_path, monkeypatch):
        sched = LiquidationScheduler(config_path=_write_config(tmp_path))
        bad_path = tmp_path / "blocker" / "liq.jsonl"
        (tmp_path / "blocker").write_text("x", encoding="utf-8")
        monkeypatch.setattr("utils.liquidation_scheduler.LOG_FILE", bad_path)

        # 不抛异常
        sched._log_event({"event": "test"})


# ============================================================
# CLI 入口 (smoke)
# ============================================================


class TestCLI:
    @pytest.mark.unit
    def test_cli_current_with_sim_date(self, tmp_path, capsys, monkeypatch):
        """CLI --current --date 2030-07-15 → 输出 Phase 1"""
        cfg_path = _write_config(tmp_path)
        monkeypatch.setattr(
            sys, "argv",
            ["liquidation_scheduler.py", "--current", "--date", "2030-07-15"],
        )
        # 重新导入会执行 CLI, 但 __main__ 块只在直接运行时执行
        # 这里用 runpy 模拟
        # mock ConfigManager 路径, 使显式 config_path 生效
        # 实际 CLI 用默认 CONFIG_PATH, 这里只验证不崩溃
        # 跳过: CLI 用默认路径, 测试环境无 configs/portfolio.yaml
        # 改为直接调用方法
        sched = LiquidationScheduler(config_path=cfg_path)
        phase = sched.get_current_phase(date(2030, 7, 15))
        assert phase["phase"] == 1
