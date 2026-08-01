# -*- coding: utf-8 -*-
"""shadow_admission_launcher 单元测试 — T2.4.

验证以下方面:
    1. 配置加载 (走 ConfigManager, HC-5)
    2. 状态文件初始化与读取
    3. 观察期进度计算 (days_elapsed / days_remaining / progress_pct / is_complete)
    4. Stage 2 推进条件评估 (HC-4 阻塞)
    5. Fail-Fast 触发器状态跟踪
    6. 准入标准评估 (DSR / 年化 / 回撤 / Sharpe CV)
    7. 每日 DSR 报告生成
    8. CLI 命令入口 (start/daily/status/evaluate)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

import pytest

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

# 导入被测模块
from scripts.shadow_admission_launcher import (
    DATETIME_FMT,
    _check_stage_2_blockers,
    _compute_observation_progress,
    _load_shadow_config,
    _load_state,
    _save_state,
    _utcnow_iso,
    cmd_daily,
    cmd_evaluate,
    cmd_start,
    cmd_status,
)


# ============================================================
# 测试 fixture
# ============================================================

@pytest.fixture
def temp_state_file(tmp_path: Path) -> Path:
    """临时状态文件路径."""
    return tmp_path / "admission_state.json"


@pytest.fixture
def temp_report_dir(tmp_path: Path) -> Path:
    """临时报告目录."""
    return tmp_path / "shadow"


@pytest.fixture
def sample_state() -> Dict[str, Any]:
    """样本状态: 已启动但未完成观察期."""
    started = (datetime.utcnow() - timedelta(days=3)).strftime(DATETIME_FMT) + "Z"
    return {
        "version": "1.0",
        "task_id": "T2.4",
        "started_at": started,
        "observation_days": 14,
        "min_observation_days": 14,
        "modules": [
            {
                "name": "LLMRouter",
                "task_id": "T2.1",
                "feature_flag": "USE_LLM_REPORT_ANALYZER",
                "fallback": "utils.llm_client.LLMClient",
                "status": "running",
            },
        ],
        "fail_fast_triggered": False,
        "fail_fast_reason": None,
        "fail_fast_triggered_at": None,
        "latest_metrics": None,
        "daily_reports": [],
        "stage_2_promoted": False,
        "stage_2_blocked_reason": "observation_in_progress",
    }


@pytest.fixture
def completed_state(sample_state: Dict[str, Any]) -> Dict[str, Any]:
    """样本状态: 观察期已完成且指标达标."""
    started = (datetime.utcnow() - timedelta(days=15)).strftime(DATETIME_FMT) + "Z"
    sample_state["started_at"] = started
    sample_state["latest_metrics"] = {
        "dsr": 6.5,
        "annual_return": 0.18,
        "max_drawdown": 0.08,
        "sharpe_cv": 0.85,
    }
    return sample_state


@pytest.fixture
def completed_state_failing_metrics(sample_state: Dict[str, Any]) -> Dict[str, Any]:
    """样本状态: 观察期已完成但指标不达标."""
    started = (datetime.utcnow() - timedelta(days=15)).strftime(DATETIME_FMT) + "Z"
    sample_state["started_at"] = started
    sample_state["latest_metrics"] = {
        "dsr": 3.0,           # < 5
        "annual_return": 0.10,  # < 0.15
        "max_drawdown": 0.12,   # > 0.10
        "sharpe_cv": 1.5,       # > 1.0
    }
    return sample_state


@pytest.fixture
def fail_fast_state(sample_state: Dict[str, Any]) -> Dict[str, Any]:
    """样本状态: fail-fast 已触发."""
    sample_state["fail_fast_triggered"] = True
    sample_state["fail_fast_reason"] = "daily_drawdown_exceeded"
    sample_state["fail_fast_triggered_at"] = _utcnow_iso()
    return sample_state


@pytest.fixture
def sample_criteria() -> Dict[str, Any]:
    """样本准入标准."""
    return {
        "min_dsr": 5,
        "min_annual_return": 0.15,
        "max_drawdown": 0.10,
        "max_sharpe_cv": 1.0,
    }


# ============================================================
# 1. 工具函数测试
# ============================================================

class TestUtilities:
    """工具函数测试."""

    def test_utcnow_iso_format(self):
        """_utcnow_iso 返回 ISO 格式带 Z 后缀."""
        s = _utcnow_iso()
        assert s.endswith("Z")
        # 应能被 datetime.fromisoformat 解析 (去 Z 后)
        dt = datetime.fromisoformat(s.rstrip("Z"))
        assert dt is not None

    def test_utcnow_iso_no_double_z(self):
        """_utcnow_iso 不应返回双 Z (回归测试)."""
        s = _utcnow_iso()
        assert not s.endswith("ZZ"), f"双 Z bug: {s}"


class TestStateFileIO:
    """状态文件读写测试."""

    def test_save_and_load_state(self, temp_state_file: Path, sample_state: Dict):
        """保存后能正确读取."""
        _save_state(temp_state_file, sample_state)
        loaded = _load_state(temp_state_file)
        assert loaded is not None
        assert loaded["task_id"] == "T2.4"
        assert loaded["observation_days"] == 14

    def test_load_nonexistent_state(self, temp_state_file: Path):
        """读取不存在的状态文件返回 None."""
        assert _load_state(temp_state_file) is None

    def test_load_corrupt_state(self, temp_state_file: Path):
        """读取损坏的状态文件返回 None (不抛异常)."""
        temp_state_file.write_text("{invalid json}", encoding="utf-8")
        assert _load_state(temp_state_file) is None

    def test_save_creates_parent_dir(self, tmp_path: Path):
        """保存时自动创建父目录."""
        state_file = tmp_path / "nested" / "deep" / "state.json"
        _save_state(state_file, {"task_id": "T2.4"})
        assert state_file.exists()


# ============================================================
# 2. 观察期进度计算测试
# ============================================================

class TestObservationProgress:
    """_compute_observation_progress 测试."""

    def test_zero_days_elapsed(self):
        """刚启动时 days_elapsed=0."""
        started = _utcnow_iso()
        progress = _compute_observation_progress(started, 14)
        assert progress["days_elapsed"] == 0
        assert progress["days_remaining"] == 14
        assert progress["progress_pct"] == 0.0
        assert progress["is_complete"] is False

    def test_mid_progress(self):
        """观察期中段."""
        started = (datetime.utcnow() - timedelta(days=7)).strftime(DATETIME_FMT) + "Z"
        progress = _compute_observation_progress(started, 14)
        assert progress["days_elapsed"] == 7
        assert progress["days_remaining"] == 7
        assert 49.0 <= progress["progress_pct"] <= 51.0
        assert progress["is_complete"] is False

    def test_completed(self):
        """观察期已完成."""
        started = (datetime.utcnow() - timedelta(days=15)).strftime(DATETIME_FMT) + "Z"
        progress = _compute_observation_progress(started, 14)
        assert progress["days_elapsed"] == 15
        assert progress["days_remaining"] == 0
        assert progress["progress_pct"] == 100.0
        assert progress["is_complete"] is True

    def test_exceeded_observation(self):
        """观察期超额 (超过 14 天)."""
        started = (datetime.utcnow() - timedelta(days=30)).strftime(DATETIME_FMT) + "Z"
        progress = _compute_observation_progress(started, 14)
        assert progress["days_elapsed"] == 30
        assert progress["days_remaining"] == 0
        assert progress["progress_pct"] == 100.0  # 上限 100

    def test_invalid_started_at_fallback(self):
        """无效的 started_at 字符串回退到 now (不抛异常)."""
        progress = _compute_observation_progress("invalid-iso", 14)
        assert progress["days_elapsed"] >= 0
        assert progress["is_complete"] is False


# ============================================================
# 3. Stage 2 推进条件评估测试 (HC-4)
# ============================================================

class TestStage2Promotion:
    """_check_stage_2_blockers 测试 (HC-4 阻塞)."""

    def test_block_when_observation_incomplete(self, sample_state, sample_criteria):
        """观察期未完成 → 阻塞 (HC-4)."""
        result = _check_stage_2_blockers(sample_state, sample_criteria)
        assert result["can_promote"] is False
        assert any("observation_days<14" in b for b in result["blockers"])

    def test_block_when_fail_fast_triggered(self, fail_fast_state, sample_criteria):
        """fail-fast 已触发 → 阻塞."""
        # 即使观察期完成, fail_fast 触发也阻塞
        started = (datetime.utcnow() - timedelta(days=15)).strftime(DATETIME_FMT) + "Z"
        fail_fast_state["started_at"] = started
        result = _check_stage_2_blockers(fail_fast_state, sample_criteria)
        assert result["can_promote"] is False
        assert any("fail_fast_triggered=true" in b for b in result["blockers"])

    def test_pass_when_complete_and_metrics_meet(self, completed_state, sample_criteria):
        """观察期完成 + 指标达标 → 可推进."""
        result = _check_stage_2_blockers(completed_state, sample_criteria)
        assert result["can_promote"] is True
        assert len(result["blockers"]) == 0
        # 至少有 6 个 promoters (观察期 + fail_fast + 4 个指标)
        assert len(result["promoters"]) >= 6

    def test_block_when_metrics_fail(self, completed_state_failing_metrics, sample_criteria):
        """观察期完成但指标不达标 → 阻塞."""
        result = _check_stage_2_blockers(completed_state_failing_metrics, sample_criteria)
        assert result["can_promote"] is False
        # 应有 4 个指标阻塞 (DSR / 年化 / 回撤 / Sharpe CV)
        blockers_str = " ".join(result["blockers"])
        assert "dsr=" in blockers_str
        assert "annual_return=" in blockers_str
        assert "max_drawdown=" in blockers_str
        assert "sharpe_cv=" in blockers_str

    def test_dsr_boundary_pass(self, sample_state, sample_criteria):
        """DSR=5 (恰好等于阈值) → 通过."""
        started = (datetime.utcnow() - timedelta(days=15)).strftime(DATETIME_FMT) + "Z"
        sample_state["started_at"] = started
        sample_state["latest_metrics"] = {
            "dsr": 5.0,            # == min_dsr
            "annual_return": 0.20,
            "max_drawdown": 0.08,
            "sharpe_cv": 0.9,
        }
        result = _check_stage_2_blockers(sample_state, sample_criteria)
        assert result["can_promote"] is True

    def test_dsr_boundary_fail(self, sample_state, sample_criteria):
        """DSR=4.99 (小于阈值) → 阻塞."""
        started = (datetime.utcnow() - timedelta(days=15)).strftime(DATETIME_FMT) + "Z"
        sample_state["started_at"] = started
        sample_state["latest_metrics"] = {
            "dsr": 4.99,
            "annual_return": 0.20,
            "max_drawdown": 0.08,
            "sharpe_cv": 0.9,
        }
        result = _check_stage_2_blockers(sample_state, sample_criteria)
        assert result["can_promote"] is False
        assert any("dsr=4.99<5" in b for b in result["blockers"])

    def test_max_drawdown_boundary_pass(self, sample_state, sample_criteria):
        """max_drawdown=0.10 (恰好等于阈值) → 通过 (<=)."""
        started = (datetime.utcnow() - timedelta(days=15)).strftime(DATETIME_FMT) + "Z"
        sample_state["started_at"] = started
        sample_state["latest_metrics"] = {
            "dsr": 6.0,
            "annual_return": 0.20,
            "max_drawdown": 0.10,   # == max_drawdown
            "sharpe_cv": 0.9,
        }
        result = _check_stage_2_blockers(sample_state, sample_criteria)
        assert result["can_promote"] is True


# ============================================================
# 4. 配置加载测试 (HC-5)
# ============================================================

class TestConfigLoading:
    """配置加载测试."""

    def test_load_shadow_config_success(self):
        """能加载 shadow_admission.yaml (走 ConfigManager 4 级优先级)."""
        cfg = _load_shadow_config()
        assert cfg is not None
        assert "settings" in cfg
        assert "admission_criteria" in cfg
        assert "fail_fast" in cfg
        assert "single_factor" in cfg
        assert "factor_combination" in cfg
        assert "modules" in cfg

    def test_config_has_modules(self):
        """配置包含 T2.1/T2.2/T2.3 三个模块."""
        cfg = _load_shadow_config()
        modules = cfg.get("modules", [])
        assert len(modules) == 3
        task_ids = {m["task_id"] for m in modules}
        assert task_ids == {"T2.1", "T2.2", "T2.3"}

    def test_config_observation_days_14(self):
        """观察期 = 14 天 (HC-4)."""
        cfg = _load_shadow_config()
        assert cfg["settings"]["observation_days"] == 14
        assert cfg["settings"]["min_observation_days"] == 14

    def test_config_risk_managed_true(self):
        """单因子和组合都 risk_managed=True (HC-3)."""
        cfg = _load_shadow_config()
        assert cfg["single_factor"]["risk_managed"] is True
        assert cfg["factor_combination"]["risk_managed"] is True

    def test_config_fail_fast_thresholds(self):
        """fail-fast 阈值符合用户硬约束 (3% / 5%)."""
        cfg = _load_shadow_config()
        assert cfg["fail_fast"]["daily_drawdown_threshold"] == 0.03
        assert cfg["fail_fast"]["cumulative_3d_drawdown_threshold"] == 0.05

    def test_config_ic_weighted_lookback_10(self):
        """IC 加权 lookback=10 (HC-7)."""
        cfg = _load_shadow_config()
        assert cfg["factor_combination"]["ic_weighted_lookback"] == 10

    def test_config_admission_criteria(self):
        """准入标准: DSR>=5, 年化>=15%, 回撤<=10%, Sharpe CV<1.0."""
        cfg = _load_shadow_config()
        c = cfg["admission_criteria"]
        assert c["min_dsr"] == 5
        assert c["min_annual_return"] == 0.15
        assert c["max_drawdown"] == 0.10
        assert c["max_sharpe_cv"] == 1.0

    def test_config_single_factor_config_e(self):
        """单因子用 Config_E (target_vol=0.08)."""
        cfg = _load_shadow_config()
        sf = cfg["single_factor"]
        assert sf["config_name"] == "Config_E"
        assert sf["target_vol"] == 0.08
        assert sf["dd_derisk_threshold"] == 0.02
        assert sf["dd_derisk_factor"] == 0.2

    def test_config_combination_config_e_plus1(self):
        """因子组合用 Config_E_plus1 (target_vol=0.07)."""
        cfg = _load_shadow_config()
        fc = cfg["factor_combination"]
        assert fc["config_name"] == "Config_E_plus1"
        assert fc["target_vol"] == 0.07
        assert fc["dd_derisk_threshold"] == 0.018
        assert fc["dd_derisk_factor"] == 0.18


# ============================================================
# 5. CLI 命令测试
# ============================================================

class TestCLICommands:
    """CLI 命令测试 (使用 tmp_path 隔离)."""

    def test_status_when_not_started(self, tmp_path: Path, monkeypatch):
        """未启动时 status 命令返回 0 并提示启动."""
        # 重定向状态文件到临时目录
        monkeypatch.setattr(
            "scripts.shadow_admission_launcher._PROJECT_ROOT",
            tmp_path,
        )
        rc = cmd_status()
        assert rc == 0

    def test_start_creates_state_file(self, tmp_path: Path, monkeypatch):
        """start 命令创建状态文件."""
        monkeypatch.setattr(
            "scripts.shadow_admission_launcher._PROJECT_ROOT",
            tmp_path,
        )
        rc = cmd_start()
        assert rc == 0
        state_file = tmp_path / "reports" / "shadow" / "admission_state.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["task_id"] == "T2.4"
        assert state["observation_days"] == 14
        assert state["fail_fast_triggered"] is False
        assert len(state["modules"]) == 3

    def test_start_idempotent(self, tmp_path: Path, monkeypatch):
        """重复 start 命令返回 1 (已启动)."""
        monkeypatch.setattr(
            "scripts.shadow_admission_launcher._PROJECT_ROOT",
            tmp_path,
        )
        assert cmd_start() == 0
        # 第二次启动应失败
        assert cmd_start() == 1

    def test_daily_generates_report(self, tmp_path: Path, monkeypatch):
        """daily 命令生成每日 DSR 报告."""
        monkeypatch.setattr(
            "scripts.shadow_admission_launcher._PROJECT_ROOT",
            tmp_path,
        )
        # 先启动
        cmd_start()
        # 再生成每日报告
        rc = cmd_daily()
        assert rc == 0
        # 验证报告文件存在
        today = datetime.now().strftime("%Y-%m-%d")
        report_file = tmp_path / "reports" / "shadow" / f"{today}_dsr.json"
        assert report_file.exists()
        report = json.loads(report_file.read_text(encoding="utf-8"))
        assert report["date"] == today
        assert report["task_id"] == "T2.4"
        assert "observation_progress" in report
        assert "metrics" in report

    def test_daily_without_start_fails(self, tmp_path: Path, monkeypatch):
        """未启动时 daily 命令返回 1."""
        monkeypatch.setattr(
            "scripts.shadow_admission_launcher._PROJECT_ROOT",
            tmp_path,
        )
        rc = cmd_daily()
        assert rc == 1

    def test_evaluate_blocked_when_incomplete(self, tmp_path: Path, monkeypatch):
        """观察期未完成时 evaluate 返回 1 (阻塞)."""
        monkeypatch.setattr(
            "scripts.shadow_admission_launcher._PROJECT_ROOT",
            tmp_path,
        )
        cmd_start()
        rc = cmd_evaluate()
        assert rc == 1  # BLOCKED

    def test_evaluate_passes_when_complete(
        self, tmp_path: Path, monkeypatch, completed_state
    ):
        """观察期完成 + 指标达标 → evaluate 返回 0 (可推进)."""
        monkeypatch.setattr(
            "scripts.shadow_admission_launcher._PROJECT_ROOT",
            tmp_path,
        )
        # 先启动 (创建目录结构)
        cmd_start()
        # 覆盖状态文件为已完成状态
        state_file = tmp_path / "reports" / "shadow" / "admission_state.json"
        _save_state(state_file, completed_state)
        # 评估
        rc = cmd_evaluate()
        assert rc == 0  # PASS

    def test_evaluate_blocked_when_metrics_fail(
        self, tmp_path: Path, monkeypatch, completed_state_failing_metrics
    ):
        """观察期完成但指标不达标 → evaluate 返回 1 (阻塞)."""
        monkeypatch.setattr(
            "scripts.shadow_admission_launcher._PROJECT_ROOT",
            tmp_path,
        )
        cmd_start()
        state_file = tmp_path / "reports" / "shadow" / "admission_state.json"
        _save_state(state_file, completed_state_failing_metrics)
        rc = cmd_evaluate()
        assert rc == 1  # BLOCKED

    def test_evaluate_blocked_when_fail_fast(
        self, tmp_path: Path, monkeypatch, fail_fast_state
    ):
        """fail-fast 触发 → evaluate 返回 1 (阻塞)."""
        monkeypatch.setattr(
            "scripts.shadow_admission_launcher._PROJECT_ROOT",
            tmp_path,
        )
        cmd_start()
        state_file = tmp_path / "reports" / "shadow" / "admission_state.json"
        _save_state(state_file, fail_fast_state)
        rc = cmd_evaluate()
        assert rc == 1  # BLOCKED


# ============================================================
# 6. 端到端流程测试
# ============================================================

class TestEndToEnd:
    """端到端流程测试: start → daily → status → evaluate."""

    def test_full_flow_blocked(self, tmp_path: Path, monkeypatch):
        """完整流程: 启动 → 每日报告 → 状态 → 评估(阻塞)."""
        monkeypatch.setattr(
            "scripts.shadow_admission_launcher._PROJECT_ROOT",
            tmp_path,
        )

        # Step 1: 启动
        assert cmd_start() == 0

        # Step 2: 每日报告
        assert cmd_daily() == 0

        # Step 3: 状态查询
        assert cmd_status() == 0

        # Step 4: 评估 (应阻塞, 观察期未完成)
        assert cmd_evaluate() == 1

        # 验证状态文件
        state_file = tmp_path / "reports" / "shadow" / "admission_state.json"
        state = _load_state(state_file)
        assert state is not None
        assert state["fail_fast_triggered"] is False
        assert len(state["daily_reports"]) == 1
        assert state["stage_2_promoted"] is False

    def test_full_flow_pass_after_14_days(
        self, tmp_path: Path, monkeypatch, completed_state
    ):
        """完整流程: 14 天后指标达标 → 评估通过."""
        monkeypatch.setattr(
            "scripts.shadow_admission_launcher._PROJECT_ROOT",
            tmp_path,
        )

        # 启动 + 覆盖状态
        cmd_start()
        state_file = tmp_path / "reports" / "shadow" / "admission_state.json"
        _save_state(state_file, completed_state)

        # 评估应通过
        assert cmd_evaluate() == 0

        # 验证状态已更新
        state = _load_state(state_file)
        assert state["stage_2_promoted"] is True
        assert state["stage_2_blocked_reason"] is None
