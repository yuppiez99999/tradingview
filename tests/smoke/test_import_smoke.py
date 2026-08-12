"""GAP-1 导入烟雾测试 — 验证核心模块可导入.

设计原则:
    1. 只验证导入成功 (不调用业务逻辑)
    2. 每个测试 < 2s
    3. 失败立即暴露 (导入失败 = P0 级问题)
"""
import importlib

import pytest

pytestmark = pytest.mark.smoke


# ECC 新增模块 (GAP-6/7/8)
@pytest.mark.parametrize("module_path", [
    "research.lgbm_reproducibility",
    "utils.alpha.data_contract",
    "utils.alpha.drift_monitor",
    "utils.alpha.delayed_label_tracker",
    "utils.config_manager",
    "utils.alpha.model_registry",
])
def test_module_importable(module_path):
    """核心模块可导入."""
    mod = importlib.import_module(module_path)
    assert mod is not None, f"模块导入失败: {module_path}"


def test_drift_monitor_exports_sim_mode_class():
    """drift_monitor 导出 SimModeDriftMonitor 类."""
    from utils.alpha.drift_monitor import SimModeDriftMonitor
    assert SimModeDriftMonitor is not None


def test_data_contract_exports_v9_contract():
    """data_contract 导出 V9_DEFAULT_CONTRACT 实例."""
    from utils.alpha.data_contract import V9_DEFAULT_CONTRACT
    assert V9_DEFAULT_CONTRACT is not None
    assert V9_DEFAULT_CONTRACT.contract_name == "v9_lgb_data_contract"


def test_reproducibility_exports_training_config():
    """lgbm_reproducibility 导出 TrainingConfig 数据类."""
    from utils.lgbm_reproducibility import TrainingConfig
    config = TrainingConfig(model_name="test", seed=42)
    assert config.model_name == "test"
