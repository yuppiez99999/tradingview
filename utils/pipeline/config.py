"""
流水线配置加载器

从 YAML 文件加载流水线配置，支持环境变量覆盖。
配置优先级: 环境变量 > YAML 文件 > 默认值
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from utils.pipeline.types import PipelineConfig

# 默认配置路径
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "configs" / "pipeline_config.yaml"

# 流水线配置实例（懒加载）
_pipeline_config: PipelineConfig | None = None
_config_loaded = False


def load_pipeline_config(config_path: str | Path | None = None) -> PipelineConfig:
    """加载流水线配置

    Args:
        config_path: 可选，指定 YAML 配置文件路径

    Returns:
        PipelineConfig 实例
    """
    global _pipeline_config, _config_loaded

    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH

    # 默认配置
    config = PipelineConfig()

    # 从 YAML 文件加载
    if path.exists():
        try:
            import yaml

            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            if raw:
                _apply_yaml(config, raw)
        except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
            pass
    # 环境变量覆盖
    _apply_env_overrides(config)

    _pipeline_config = config
    _config_loaded = True
    return config


def get_pipeline_config() -> PipelineConfig:
    """获取当前流水线配置（懒加载）"""
    global _pipeline_config, _config_loaded
    if not _config_loaded:
        return load_pipeline_config()
    return _pipeline_config  # type: ignore


def _apply_yaml(config: PipelineConfig, raw: dict[str, Any]) -> None:
    """将 YAML 字典映射到 PipelineConfig 字段"""
    # 顶层字段
    if "mode" in raw:
        config.mode = str(raw["mode"])
    if "interval_minutes" in raw:
        config.interval_minutes = int(raw["interval_minutes"])

    _apply_data_cleaning(config, raw.get("data_cleaning", {}))
    _apply_alpha(config, raw.get("alpha", {}))
    _apply_backtest_gate(config, raw.get("backtest_gate", {}))
    _apply_execution(config, raw.get("execution", {}))
    _apply_risk_monitor(config, raw.get("risk_monitor", {}))
    _apply_logging(config, raw.get("logging", {}))


def _apply_data_cleaning(config: PipelineConfig, dc: dict[str, Any]) -> None:
    """映射 data_cleaning 段"""
    if "enabled" in dc:
        config.data_cleaning_enabled = bool(dc["enabled"])
    if "min_quality_score" in dc:
        config.min_quality_score = float(dc["min_quality_score"])
    if "multi_source_check" in dc:
        config.multi_source_check = bool(dc["multi_source_check"])
    if "outlier_z_threshold" in dc:
        config.outlier_z_threshold = float(dc["outlier_z_threshold"])
    if "gap_fill_max_days" in dc:
        config.gap_fill_max_days = int(dc["gap_fill_max_days"])


def _apply_alpha(config: PipelineConfig, ap: dict[str, Any]) -> None:
    """映射 alpha 段"""
    if "enabled" in ap:
        config.alpha_enabled = bool(ap["enabled"])
    if "model" in ap:
        config.alpha_model = str(ap["model"])
    if "train_interval_days" in ap:
        config.train_interval_days = int(ap["train_interval_days"])
    if "retrain_on_drift" in ap:
        config.retrain_on_drift = bool(ap["retrain_on_drift"])
    if "horizon" in ap:
        config.horizon = int(ap["horizon"])


def _apply_backtest_gate(config: PipelineConfig, bg: dict[str, Any]) -> None:
    """映射 backtest_gate 段"""
    if "enabled" in bg:
        config.backtest_gate_enabled = bool(bg["enabled"])
    if "min_ic" in bg:
        config.min_ic = float(bg["min_ic"])
    if "min_dsr" in bg:
        config.min_dsr = float(bg["min_dsr"])
    if "max_drawdown" in bg:
        config.max_drawdown = float(bg["max_drawdown"])
    if "walk_forward_windows" in bg:
        config.walk_forward_windows = int(bg["walk_forward_windows"])


def _apply_execution(config: PipelineConfig, ex: dict[str, Any]) -> None:
    """映射 execution 段"""
    if "enabled" in ex:
        config.execution_enabled = bool(ex["enabled"])
    if "algo" in ex:
        config.execution_algo = str(ex["algo"])
    if "max_slippage_bps" in ex:
        config.max_slippage_bps = float(ex["max_slippage_bps"])
    if "slice_minutes" in ex:
        config.slice_minutes = int(ex["slice_minutes"])
    if "dry_run" in ex:
        config.execution_dry_run = bool(ex["dry_run"])
    if "target_exposure" in ex:
        config.execution_target_exposure = float(ex["target_exposure"])
    if "max_order_value" in ex:
        config.execution_max_order_value = float(ex["max_order_value"])
    if "default_algo" in ex:
        config.execution_default_algo = str(ex["default_algo"])


def _apply_risk_monitor(config: PipelineConfig, rm: dict[str, Any]) -> None:
    """映射 risk_monitor 段"""
    if "enabled" in rm:
        config.risk_monitor_enabled = bool(rm["enabled"])
    if "check_interval_seconds" in rm:
        config.risk_check_interval_seconds = int(rm["check_interval_seconds"])
    if "kill_switch_l1_margin" in rm:
        config.kill_switch_l1_margin = float(rm["kill_switch_l1_margin"])
    if "kill_switch_l2_margin" in rm:
        config.kill_switch_l2_margin = float(rm["kill_switch_l2_margin"])
    if "kill_switch_l3_margin" in rm:
        config.kill_switch_l3_margin = float(rm["kill_switch_l3_margin"])
    if "max_daily_drawdown" in rm:
        config.max_daily_drawdown = float(rm["max_daily_drawdown"])
    if "max_total_drawdown" in rm:
        config.max_total_drawdown = float(rm["max_total_drawdown"])


def _apply_logging(config: PipelineConfig, lg: dict[str, Any]) -> None:
    """映射 logging 段"""
    if "level" in lg:
        config.log_level = str(lg["level"])
    if "report_dir" in lg:
        config.report_dir = str(lg["report_dir"])
    if "memory_write" in lg:
        config.memory_write = bool(lg["memory_write"])


def _apply_env_overrides(config: PipelineConfig) -> None:
    """环境变量覆盖配置字段"""
    env_map = {
        "PIPELINE_MODE": ("mode", str),
        "PIPELINE_ALPHA_ENABLED": ("alpha_enabled", lambda v: v.lower() == "true"),
        "PIPELINE_EXECUTION_ENABLED": (
            "execution_enabled",
            lambda v: v.lower() == "true",
        ),
        "PIPELINE_RISK_INTERVAL": ("risk_check_interval_seconds", int),
        "PIPELINE_REPORT_DIR": ("report_dir", str),
    }
    for env_key, (attr, converter) in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            try:
                setattr(config, attr, converter(val))
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ):
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                pass
