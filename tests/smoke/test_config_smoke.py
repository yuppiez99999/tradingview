"""GAP-1 配置烟雾测试 — 验证核心配置文件可加载.

设计原则:
    1. 只验证文件可读 + 不抛异常 (不验证 schema 完整性)
    2. JSON 和 YAML 都覆盖
    3. 每个测试 < 1s
"""
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_json(path: str):
    """加载 JSON 配置文件."""
    full_path = _PROJECT_ROOT / path
    if not full_path.exists():
        pytest.skip(f"配置文件不存在: {full_path}")
    with open(full_path, encoding="utf-8") as f:
        return json.load(f)


def _load_yaml(path: str):
    """加载 YAML 配置文件."""
    try:
        import yaml
    except ImportError:
        pytest.skip("yaml 模块未安装")
    full_path = _PROJECT_ROOT / path
    if not full_path.exists():
        pytest.skip(f"配置文件不存在: {full_path}")
    with open(full_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# JSON 配置
def test_gate_thresholds_json():
    """config/gate_thresholds.json 可加载."""
    data = _load_json("config/gate_thresholds.json")
    assert data is not None


def test_shadow_account_config_json():
    """config/shadow_account_config.json 可加载."""
    data = _load_json("config/shadow_account_config.json")
    assert data is not None


def test_positions_json():
    """config/positions.json 可加载."""
    data = _load_json("config/positions.json")
    assert data is not None


# YAML 配置
def test_alert_owners_yaml():
    """config/alert_owners.yaml 可加载 (GAP-6)."""
    data = _load_yaml("config/alert_owners.yaml")
    assert data is not None
    assert "v9_lgb" in data


def test_feature_flags_yaml():
    """v8.3_institutional/config/feature_flags.yaml 可加载."""
    data = _load_yaml("v8.3_institutional/config/feature_flags.yaml")
    assert data is not None


def test_execution_yaml():
    """v8.3_institutional/config/execution.yaml 可加载."""
    data = _load_yaml("v8.3_institutional/config/execution.yaml")
    assert data is not None
