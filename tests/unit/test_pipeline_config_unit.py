"""Pipeline 配置加载器单元测试.

被测模块: utils/pipeline/config.py
覆盖目标: >=85% line + branch

测试范围:
- load_pipeline_config 默认/自定义路径
- _apply_yaml 各段映射
- _apply_env_overrides 环境变量覆盖
- get_pipeline_config 懒加载
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.pipeline.config import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    _apply_alpha,
    _apply_backtest_gate,
    _apply_data_cleaning,
    _apply_env_overrides,
    _apply_execution,
    _apply_logging,
    _apply_risk_monitor,
    _apply_yaml,
    get_pipeline_config,
    load_pipeline_config,
)
from utils.pipeline.types import PipelineConfig  # noqa: E402


class PipelineConfigTest:
    """PipelineConfig 加载/映射全方法单元测试"""

    # ============================================================
    # load_pipeline_config
    # ============================================================

    def test_load_default(self):
        """默认路径加载不崩溃"""
        cfg = load_pipeline_config()
        assert isinstance(cfg, PipelineConfig)
        assert cfg.mode in ("auto", "manual", "dry_run")

    def test_load_nonexistent_path(self):
        """不存在的配置路径返回默认值"""
        cfg = load_pipeline_config("/nonexistent/path/config.yaml")
        assert isinstance(cfg, PipelineConfig)
        # 默认值
        assert cfg.min_quality_score == 80.0

    def test_load_custom_path(self, tmp_path):
        """自定义 YAML 路径加载"""
        import yaml as yaml_lib
        config_file = tmp_path / "custom.yaml"
        config_file.write_text(yaml_lib.dump({
            "mode": "manual",
            "data_cleaning": {"min_quality_score": 90.0, "multi_source_check": False},
            "alpha": {"enabled": True, "model": "lightgbm"},
            "risk_monitor": {"kill_switch_l3_margin": 0.8},
        }), encoding="utf-8")
        cfg = load_pipeline_config(config_file)
        assert cfg.mode == "manual"
        assert cfg.min_quality_score == 90.0
        assert cfg.multi_source_check is False
        assert cfg.alpha_enabled is True
        assert cfg.alpha_model == "lightgbm"
        assert cfg.kill_switch_l3_margin == 0.8

    def test_load_corrupt_yaml(self, tmp_path):
        """损坏的 YAML 文件返回默认值"""
        config_file = tmp_path / "corrupt.yaml"
        config_file.write_text(":::corrupt:::not:yaml:content:::", encoding="utf-8")
        cfg = load_pipeline_config(config_file)
        assert isinstance(cfg, PipelineConfig)

    def test_get_pipeline_config_lazy(self):
        """get_pipeline_config 懒加载"""
        cfg = get_pipeline_config()
        assert isinstance(cfg, PipelineConfig)

    # ============================================================
    # _apply_yaml
    # ============================================================

    def test_apply_yaml_top_level(self):
        """顶层字段映射"""
        cfg = PipelineConfig()
        _apply_yaml(cfg, {"mode": "dry_run", "interval_minutes": 30})
        assert cfg.mode == "dry_run"
        assert cfg.interval_minutes == 30

    def test_apply_yaml_empty(self):
        """空字典不修改配置"""
        cfg = PipelineConfig()
        original_mode = cfg.mode
        _apply_yaml(cfg, {})
        assert cfg.mode == original_mode

    def test_apply_yaml_all_sections(self):
        """所有段映射"""
        cfg = PipelineConfig()
        _apply_yaml(cfg, {
            "data_cleaning": {"enabled": False, "min_quality_score": 85, "outlier_z_threshold": 2.5, "gap_fill_max_days": 5},
            "alpha": {"enabled": True, "model": "lstm", "train_interval_days": 10, "retrain_on_drift": False, "horizon": 3},
            "backtest_gate": {"enabled": True, "min_ic": 0.05, "min_dsr": 1.5, "max_drawdown": 0.1, "walk_forward_windows": 8},
            "execution": {"enabled": True, "algo": "vwap", "max_slippage_bps": 5, "slice_minutes": 3, "dry_run": False, "target_exposure": 0.9, "max_order_value": 300000, "default_algo": "vwap"},
            "risk_monitor": {"enabled": False, "check_interval_seconds": 60, "kill_switch_l1_margin": 0.4, "kill_switch_l2_margin": 0.6, "kill_switch_l3_margin": 0.7, "max_daily_drawdown": 0.03, "max_total_drawdown": 0.12},
            "logging": {"level": "DEBUG", "report_dir": "custom/reports", "memory_write": False},
        })
        assert cfg.data_cleaning_enabled is False
        assert cfg.alpha_enabled is True
        assert cfg.alpha_model == "lstm"
        assert cfg.backtest_gate_enabled is True
        assert cfg.execution_enabled is True
        assert cfg.execution_algo == "vwap"
        assert cfg.risk_monitor_enabled is False
        assert cfg.risk_check_interval_seconds == 60
        assert cfg.log_level == "DEBUG"
        assert cfg.report_dir == "custom/reports"
        assert cfg.memory_write is False

    # ============================================================
    # _apply_data_cleaning
    # ============================================================

    def test_apply_data_cleaning_full(self):
        cfg = PipelineConfig()
        _apply_data_cleaning(cfg, {"enabled": False, "min_quality_score": 95, "multi_source_check": False, "outlier_z_threshold": 4.0, "gap_fill_max_days": 7})
        assert cfg.data_cleaning_enabled is False
        assert cfg.min_quality_score == 95.0
        assert cfg.multi_source_check is False
        assert cfg.outlier_z_threshold == 4.0
        assert cfg.gap_fill_max_days == 7

    def test_apply_data_cleaning_empty(self):
        cfg = PipelineConfig()
        orig = cfg.min_quality_score
        _apply_data_cleaning(cfg, {})
        assert cfg.min_quality_score == orig

    # ============================================================
    # _apply_alpha
    # ============================================================

    def test_apply_alpha_full(self):
        cfg = PipelineConfig()
        _apply_alpha(cfg, {"enabled": True, "model": "transformer", "train_interval_days": 15, "retrain_on_drift": False, "horizon": 7})
        assert cfg.alpha_enabled is True
        assert cfg.alpha_model == "transformer"
        assert cfg.train_interval_days == 15
        assert cfg.retrain_on_drift is False
        assert cfg.horizon == 7

    def test_apply_alpha_empty(self):
        cfg = PipelineConfig()
        orig = cfg.alpha_model
        _apply_alpha(cfg, {})
        assert cfg.alpha_model == orig

    # ============================================================
    # _apply_backtest_gate
    # ============================================================

    def test_apply_backtest_gate_full(self):
        cfg = PipelineConfig()
        _apply_backtest_gate(cfg, {"enabled": True, "min_ic": 0.04, "min_dsr": 1.2, "max_drawdown": 0.12, "walk_forward_windows": 10})
        assert cfg.backtest_gate_enabled is True
        assert cfg.min_ic == 0.04
        assert cfg.min_dsr == 1.2
        assert cfg.max_drawdown == 0.12
        assert cfg.walk_forward_windows == 10

    def test_apply_backtest_gate_empty(self):
        cfg = PipelineConfig()
        orig = cfg.min_ic
        _apply_backtest_gate(cfg, {})
        assert cfg.min_ic == orig

    # ============================================================
    # _apply_execution
    # ============================================================

    def test_apply_execution_full(self):
        cfg = PipelineConfig()
        _apply_execution(cfg, {"enabled": True, "algo": "smart", "max_slippage_bps": 8, "slice_minutes": 2, "dry_run": True, "target_exposure": 0.88, "max_order_value": 400000, "default_algo": "smart"})
        assert cfg.execution_enabled is True
        assert cfg.execution_algo == "smart"
        assert cfg.max_slippage_bps == 8.0
        assert cfg.slice_minutes == 2
        assert cfg.execution_dry_run is True
        assert cfg.execution_target_exposure == 0.88
        assert cfg.execution_max_order_value == 400000.0
        assert cfg.execution_default_algo == "smart"

    def test_apply_execution_empty(self):
        cfg = PipelineConfig()
        orig = cfg.execution_algo
        _apply_execution(cfg, {})
        assert cfg.execution_algo == orig

    # ============================================================
    # _apply_risk_monitor
    # ============================================================

    def test_apply_risk_monitor_full(self):
        cfg = PipelineConfig()
        _apply_risk_monitor(cfg, {"enabled": False, "check_interval_seconds": 120, "kill_switch_l1_margin": 0.45, "kill_switch_l2_margin": 0.55, "kill_switch_l3_margin": 0.85, "max_daily_drawdown": 0.04, "max_total_drawdown": 0.10})
        assert cfg.risk_monitor_enabled is False
        assert cfg.risk_check_interval_seconds == 120
        assert cfg.kill_switch_l1_margin == 0.45
        assert cfg.kill_switch_l2_margin == 0.55
        assert cfg.kill_switch_l3_margin == 0.85
        assert cfg.max_daily_drawdown == 0.04
        assert cfg.max_total_drawdown == 0.10

    def test_apply_risk_monitor_empty(self):
        cfg = PipelineConfig()
        orig = cfg.kill_switch_l1_margin
        _apply_risk_monitor(cfg, {})
        assert cfg.kill_switch_l1_margin == orig

    # ============================================================
    # _apply_logging
    # ============================================================

    def test_apply_logging_full(self):
        cfg = PipelineConfig()
        _apply_logging(cfg, {"level": "WARNING", "report_dir": "/tmp/reports", "memory_write": False})
        assert cfg.log_level == "WARNING"
        assert cfg.report_dir == "/tmp/reports"
        assert cfg.memory_write is False

    def test_apply_logging_empty(self):
        cfg = PipelineConfig()
        orig = cfg.log_level
        _apply_logging(cfg, {})
        assert cfg.log_level == orig

    # ============================================================
    # _apply_env_overrides
    # ============================================================

    def test_env_override_mode(self):
        cfg = PipelineConfig()
        with patch.dict("os.environ", {"PIPELINE_MODE": "manual"}):
            _apply_env_overrides(cfg)
        assert cfg.mode == "manual"

    def test_env_override_alpha_enabled(self):
        cfg = PipelineConfig()
        with patch.dict("os.environ", {"PIPELINE_ALPHA_ENABLED": "true"}):
            _apply_env_overrides(cfg)
        assert cfg.alpha_enabled is True

    def test_env_override_alpha_disabled(self):
        cfg = PipelineConfig()
        with patch.dict("os.environ", {"PIPELINE_ALPHA_ENABLED": "false"}):
            _apply_env_overrides(cfg)
        assert cfg.alpha_enabled is False

    def test_env_override_execution_enabled(self):
        cfg = PipelineConfig()
        with patch.dict("os.environ", {"PIPELINE_EXECUTION_ENABLED": "true"}):
            _apply_env_overrides(cfg)
        assert cfg.execution_enabled is True

    def test_env_override_risk_interval(self):
        cfg = PipelineConfig()
        with patch.dict("os.environ", {"PIPELINE_RISK_INTERVAL": "45"}):
            _apply_env_overrides(cfg)
        assert cfg.risk_check_interval_seconds == 45

    def test_env_override_report_dir(self):
        cfg = PipelineConfig()
        with patch.dict("os.environ", {"PIPELINE_REPORT_DIR": "/custom/dir"}):
            _apply_env_overrides(cfg)
        assert cfg.report_dir == "/custom/dir"

    def test_env_override_invalid_int(self):
        """无效整数环境变量不崩溃"""
        cfg = PipelineConfig()
        with patch.dict("os.environ", {"PIPELINE_RISK_INTERVAL": "not_a_number"}):
            _apply_env_overrides(cfg)
        # 应保持默认值
        assert cfg.risk_check_interval_seconds == 30

    def test_env_override_no_vars(self):
        """无环境变量时不修改"""
        cfg = PipelineConfig()
        orig_mode = cfg.mode
        env_keys = ["PIPELINE_MODE", "PIPELINE_ALPHA_ENABLED", "PIPELINE_EXECUTION_ENABLED", "PIPELINE_RISK_INTERVAL", "PIPELINE_REPORT_DIR"]
        clean_env = {k: v for k, v in __import__("os").environ.items() if k not in env_keys}
        with patch.dict("os.environ", clean_env, clear=True):
            _apply_env_overrides(cfg)
        assert cfg.mode == orig_mode