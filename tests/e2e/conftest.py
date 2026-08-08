"""conftest.py — E2E 测试层专用 fixture (tests/e2e/)

pytest 自动加载规则: 仅识别名为 conftest.py 的文件
本文件为 tests/e2e/ 目录的 conftest.py

提供端到端测试所需的黄金数据加载:
    - e2e_reports_dir: v8.3 真实历史报告目录
    - e2e_trade_plans_dir: v8.3 真实交易计划目录
    - e2e_pnl_reports: 加载所有真实 pnl 报告
    - e2e_trade_plans: 加载所有真实交易计划
"""
import json
import sys
from pathlib import Path

import pytest


# ============================================================
# 环境隔离: 拦截 qlib/lightgbm 加载链, 避免 scipy access violation 崩溃
# ============================================================
# 背景: utils/pipeline/__init__.py → alpha_pipeline.py line 38
#       `from qlib.contrib.model import LGBModel` 触发
#       qlib.contrib.model.__init__ → double_ensemble → lightgbm → scipy.sparse
#       → Windows access violation (不可被 try/except 捕获, 进程崩溃).
# 解决: 在 conftest 加载阶段 (早于 test 文件 import) 注入 ImportBlocker,
#       让 alpha_pipeline.py 的 try/except 走 ImportError 降级分支 (_QLIB_AVAILABLE=False).
class _ImportBlocker:
    """拦截指定模块的属性访问, 让 try/except 走 ImportError 分支."""

    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, attr: str):
        raise ImportError(f"{self._name}.{attr} 被 E2E 测试拦截 (避免 scipy 崩溃链)")

    def __repr__(self) -> str:
        return f"<ImportBlocker:{self._name}>"


# 注入拦截器 (仅当模块未加载时)
for _mod_name in ("qlib", "qlib.contrib", "qlib.contrib.model", "lightgbm"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = _ImportBlocker(_mod_name)


@pytest.fixture(scope="session")
def e2e_reports_dir(project_root):
    """v8.3 真实历史报告目录"""
    return Path(project_root) / "v8.3_institutional" / "reports"


@pytest.fixture(scope="session")
def e2e_trade_plans_dir(project_root):
    """v8.3 真实交易计划目录"""
    return Path(project_root) / "v8.3_institutional" / "trade_plans"


@pytest.fixture(scope="session")
def e2e_pnl_reports(e2e_reports_dir):
    """加载所有真实 pnl 报告 (黄金数据源)

    Yields:
        dict: {filename_stem: report_dict}

    数据缺失时 skip 而非 fail
    """
    if not e2e_reports_dir.exists():
        pytest.skip(f"E2E 报告目录不存在: {e2e_reports_dir}")

    pnl_files = sorted(e2e_reports_dir.glob("daily_pnl_report_*.json"))
    if not pnl_files:
        pytest.skip(f"E2E 无 pnl 报告文件: {e2e_reports_dir}")

    reports = {}
    for f in pnl_files:
        try:
            with open(f, encoding="utf-8") as fp:
                reports[f.stem] = json.load(fp)
        except Exception:
            continue

    if not reports:
        pytest.skip("E2E 报告全部加载失败")

    return reports


@pytest.fixture(scope="session")
def e2e_trade_plans(e2e_trade_plans_dir):
    """加载所有真实交易计划"""
    if not e2e_trade_plans_dir.exists():
        pytest.skip(f"E2E 交易计划目录不存在: {e2e_trade_plans_dir}")

    plan_files = sorted(e2e_trade_plans_dir.glob("trade_plan_*.json"))
    if not plan_files:
        pytest.skip(f"E2E 无交易计划文件: {e2e_trade_plans_dir}")

    plans = {}
    for f in plan_files:
        try:
            with open(f, encoding="utf-8") as fp:
                plans[f.stem] = json.load(fp)
        except Exception:
            continue

    if not plans:
        pytest.skip("E2E 交易计划全部加载失败")

    return plans


# ============================================================
# U5 GAP-2 E2E fixture (full_pipeline + shadow_account_lifecycle)
# ============================================================

@pytest.fixture
def pipeline_config_overrides():
    """PipelineOrchestrator 测试配置覆盖.

    默认 PipelineConfig: data_cleaning/risk_monitor 启用, alpha/backtest/execution 关闭.
    本 fixture 提供一组安全覆盖, E2E 不执行真实交易.

    注: 延迟导入 PipelineConfig 以隔离 lightgbm/scipy 间歇性访问冲突
    (utils.pipeline.__init__ 触发 alpha_pipeline → qlib → lightgbm 导入链).
    """
    # 延迟导入: 仅在实际需要时加载, 避免 conftest 加载阶段触发 lightgbm
    import importlib

    types_mod = importlib.import_module("utils.pipeline.types")
    PipelineConfig = types_mod.PipelineConfig

    return PipelineConfig(
        mode="dry_run",
        data_cleaning_enabled=True,
        alpha_enabled=False,        # E2E 默认不触发模型训练
        backtest_gate_enabled=False,
        execution_enabled=False,    # E2E 不执行真实交易
        risk_monitor_enabled=True,
    )


@pytest.fixture
def sample_daily_returns_14d():
    """15 天观察期收益率序列 (模拟真实波动, 年化约 8-12%).

    用于 ShadowAccountAdapter 正常生命周期测试. 单日波动 ±0.8%, 无 Fail-Fast 触发.
    注: 命名保留 14d (对齐观察期术语), 实际 15 天以满足 MIN_SAMPLES_FOR_DSR=15.
    """
    return [0.005, -0.003, 0.008, -0.002, 0.004,
            -0.006, 0.003, 0.001, -0.004, 0.007,
            -0.005, 0.002, 0.006, -0.003, 0.004]


@pytest.fixture
def extreme_daily_returns_breach():
    """触发 Fail-Fast 的极端收益率序列 (单日 -4% > 3% 阈值).

    用于验证 ShadowAccountAdapter 单日回撤 Fail-Fast 触发.
    """
    return [0.001, 0.002, -0.04, 0.003, 0.001]
