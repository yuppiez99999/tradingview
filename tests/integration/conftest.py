"""conftest.py — 集成测试层专用 fixture (tests/integration/)

pytest 自动加载规则: 仅识别名为 conftest.py 的文件
本文件为 tests/integration/ 目录的 conftest.py

提供多模块协作测试所需的环境:
    - guard_chain_modules: 懒加载 Guard 模块
    - production_kill_switch: 复用主 KillSwitch 实例 (模拟 daily_workflow 集成)
    - mock_all_external_sources: mock 所有外部数据源, 模拟网络全不可用
"""
from unittest.mock import MagicMock

import pytest


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


# ============================================================
# Shadow 数据质量闭环集成测试 fixture
# ============================================================
# temp_shadow_env: 创建隔离的临时环境, patch 所有文件路径常量
# 依赖 tests/conftest.py 中的 mock_compute_prediction_drift / mock_drift_report
# 辅助函数见 tests/shadow_helpers.py

import json as _json  # noqa: E402
import sys as _sys  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

# 添加 tests/ 目录到 sys.path (确保 integration 测试也能 import shadow_helpers)
_TESTS_DIR = _Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_TESTS_DIR))


@pytest.fixture
def temp_shadow_env(
    tmp_path,
    monkeypatch,
    mock_compute_prediction_drift,
    mock_drift_report,
):
    """创建隔离的临时 Shadow 环境 (集成测试专用).

    策略:
        1. 创建临时目录结构 (reports/evolution/ + reports/shadow/)
        2. Patch 两个脚本的模块级路径常量指向临时目录
        3. Mock compute_prediction_drift (通过 mock_compute_prediction_drift fixture)
        4. Mock generate_snapshot (从 cleaned.jsonl 读取 real 记录数)

    Returns:
        包含所有临时文件路径的字典:
            root / evolution_dir / shadow_dir / cleaned_file / raw_file /
            drift_alerts / watchdog_log / progress_file / integration_log / mock_report
    """
    # 创建目录结构
    evolution_dir = tmp_path / "reports" / "evolution"
    shadow_dir = tmp_path / "reports" / "shadow"
    evolution_dir.mkdir(parents=True, exist_ok=True)
    shadow_dir.mkdir(parents=True, exist_ok=True)

    # 定义文件路径
    cleaned_file = shadow_dir / "daily_returns_cleaned.jsonl"
    raw_file = shadow_dir / "daily_returns.jsonl"
    drift_alerts = evolution_dir / "drift_alerts.jsonl"
    watchdog_log = evolution_dir / "observation_watchdog.jsonl"
    progress_file = evolution_dir / "observation_progress.json"
    integration_log = evolution_dir / "integration_log.jsonl"

    # Patch observation_watchdog 常量
    monkeypatch.setattr("scripts.observation_watchdog.EVOLUTION_DIR", evolution_dir)
    monkeypatch.setattr("scripts.observation_watchdog.OBSERVATION_PROGRESS_FILE", progress_file)
    monkeypatch.setattr("scripts.observation_watchdog.CLEANED_FILE", cleaned_file)
    monkeypatch.setattr("scripts.observation_watchdog.RAW_SHADOW_FILE", raw_file)
    monkeypatch.setattr("scripts.observation_watchdog.WATCHDOG_LOG_FILE", watchdog_log)

    # Patch integrate_cleaned_to_drift 常量
    monkeypatch.setattr("scripts.integrate_cleaned_to_drift.DEFAULT_CLEANED_INPUT", cleaned_file)
    monkeypatch.setattr("scripts.integrate_cleaned_to_drift.RAW_SHADOW_INPUT", raw_file)
    monkeypatch.setattr("scripts.integrate_cleaned_to_drift.EVOLUTION_DIR", evolution_dir)
    monkeypatch.setattr("scripts.integrate_cleaned_to_drift.DRIFT_ALERTS_FILE", drift_alerts)
    monkeypatch.setattr("scripts.integrate_cleaned_to_drift.OBSERVATION_PROGRESS_FILE", progress_file)
    monkeypatch.setattr("scripts.integrate_cleaned_to_drift.INTEGRATION_LOG_FILE", integration_log)
    # _PROJ 用于 relative_to(), 必须指向 tmp_path 避免 ValueError
    monkeypatch.setattr("scripts.integrate_cleaned_to_drift._PROJ", tmp_path)

    # Patch write_integration_log 的默认参数
    # (函数默认参数在定义时绑定, monkeypatch 模块属性无法修改默认参数)
    from scripts.integrate_cleaned_to_drift import write_integration_log
    monkeypatch.setattr(write_integration_log, "__defaults__", (integration_log,))

    # 设置 compute_prediction_drift 的动态 side_effect
    # (根据实际输入调整 baseline_size / current_size)
    def _dynamic_drift(baseline, current, **kw):
        mock_drift_report.to_dict.return_value["baseline_size"] = int(len(baseline))
        mock_drift_report.to_dict.return_value["current_size"] = int(len(current))
        return mock_drift_report

    mock_compute_prediction_drift.side_effect = _dynamic_drift

    # Mock generate_snapshot (从 cleaned.jsonl 读取 real 记录数)
    def _mock_generate_snapshot():
        real_count = 0
        if cleaned_file.exists():
            with open(cleaned_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = _json.loads(line.strip())
                        if rec.get("quality") == "real":
                            real_count += 1
                    except _json.JSONDecodeError:
                        continue
        return {
            "observation": {
                "days_completed": real_count,
                "required_days": 14,
                "progress_pct": min(100.0, real_count / 14 * 100),
                "samples_collected": real_count,
                "min_samples": 20,
                "ready_for_phase_b": real_count >= 20,
                "start_date": "2026-07-23",
                "estimated_completion": "2026-08-13",
            },
        }

    monkeypatch.setattr("scripts.observation_tracker.generate_snapshot", _mock_generate_snapshot)

    return {
        "root": tmp_path,
        "evolution_dir": evolution_dir,
        "shadow_dir": shadow_dir,
        "cleaned_file": cleaned_file,
        "raw_file": raw_file,
        "drift_alerts": drift_alerts,
        "watchdog_log": watchdog_log,
        "progress_file": progress_file,
        "integration_log": integration_log,
        "mock_report": mock_drift_report,
    }
