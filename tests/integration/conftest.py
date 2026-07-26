# -*- coding: utf-8 -*-
"""conftest.py — 集成测试层专用 fixture (tests/integration/)

pytest 自动加载规则: 仅识别名为 conftest.py 的文件
本文件为 tests/integration/ 目录的 conftest.py

提供多模块协作测试所需的环境:
    - guard_chain_modules: 懒加载 Guard 模块
    - production_kill_switch: 复用主 KillSwitch 实例 (模拟 daily_workflow 集成)
    - mock_all_external_sources: mock 所有外部数据源, 模拟网络全不可用
"""
import pytest
from unittest.mock import MagicMock


@pytest.fixture
def guard_chain_modules():
    """懒加载 Guard 模块

    返回 dict: {guard_name: module}
    任一模块导入失败时 skip 而非 fail (单模块失败由单元测试覆盖)
    """
    modules = {}
    import_map = {
        "kill_switch": "utils.kill_switch",
        "overnight_gap": "utils.overnight_gap_monitor",
        "market_circuit_breaker": "utils.market_circuit_breaker",
        "hedge_execution": "utils.hedge_execution_engine",
        "risk_guard_integrator": "utils.risk_guard_integrator",
        "portfolio_optimizer": "utils.portfolio_optimizer",
    }
    missing = []
    for name, mod_path in import_map.items():
        try:
            mod = __import__(mod_path, fromlist=[name])
            modules[name] = mod
        except ImportError as e:
            missing.append(f"{mod_path}: {e}")

    if missing:
        pytest.skip(f"Guard 模块不可用: {missing}")
    return modules


@pytest.fixture
def production_kill_switch(clean_env):
    """复用主 KillSwitch 实例 (模拟 daily_workflow.py 集成模式)

    - 注册 broker_callback (mock)
    - 与 daily_workflow.py 中 `getattr(self, 'ks', None) or KillSwitch()` 逻辑一致
    """
    from utils.kill_switch import KillSwitch
    ks = KillSwitch()

    callback = MagicMock(return_value={"executed": True, "orders_sent": 3})
    ks.set_broker_callback(callback)

    return ks


@pytest.fixture
def mock_all_external_sources(monkeypatch):
    """mock 所有外部数据源, 模拟网络全不可用场景

    用于测试 fail-closed 路径:
        - astock_realtime.get_realtime_quotes → 返回空 dict
        - akshare.stock_zh_a_spot_em → 抛 ImportError
        - akshare.stock_zh_index_spot_em → 抛 ImportError
        - ExternalDataManager → 抛 ConnectionError
    """
    def _empty_quotes(codes):
        return {}

    try:
        monkeypatch.setattr(
            "utils.astock_realtime.get_realtime_quotes", _empty_quotes
        )
    except (AttributeError, ImportError):
        pass

    def _raise(*args, **kwargs):
        raise ImportError("akshare not installed (mocked)")

    try:
        monkeypatch.setattr("akshare.stock_zh_a_spot_em", _raise, raising=False)
        monkeypatch.setattr("akshare.stock_zh_index_spot_em", _raise, raising=False)
    except (AttributeError, ImportError):
        pass

    try:
        monkeypatch.setattr(
            "utils.external_data_source.ExternalDataManager",
            _raise,
            raising=False,
        )
    except (AttributeError, ImportError):
        pass
