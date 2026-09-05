"""
conftest.py — 量化交易系统 v8.4 统一测试配置

P2 FIX (2026-07-22): 统一 tests/ 和 v8.3_institutional/tests/ 两套测试
为所有测试文件提供:
  - 自动路径设置
  - 共享 fixture (样本数据/价格矩阵/配置)
  - Mock 工具支持
"""

from __future__ import annotations

import os
import sys
import warnings

import numpy as np
import pandas as pd
import pytest

# ============================================================
# 生产写盘隔离 (2026-09-03 测试污染治理): conftest 级全局防线
# ============================================================
# 背景: 全量测试曾向生产 reports/ 与 D:\QuantData\reports 写入测试产物
# (strategy_registry / broker_audit test_* / shadow_state 等十余目录),
# 并向 degradation_log.jsonl 注入 155 条假降级 — 污染 health score 输入。
# 既往修复 (P3-1b / DQC 审计 / er23) 均为逐文件 fixture, 缺统一防线。
#
# 本 fixture 拦截两条"系统通道":
#   1) utils.path_config.get_reports_dir → tmp_path
#      (含模块级 `from utils.path_config import get_reports_dir` 的
#       已导入引用扇出; 运行时函数级 import 自然生效; tests/ 自身排除 —
#       test_path_config_unit 等需测真实函数)
#   2) utils.degradation_audit.LOG_FILE → tmp_path + 进程内去重标记复位
#
# 已知边界: 107 处模块级硬编码 `_PROJECT_ROOT / "reports"` 的写入无常中枢可拦,
# 其中高频污染源按证据登记到下方 Tier-2 registry, 渐进扩充。
# 证据指针: cairn/LOG.md 2026-09-03 测试污染盲区条目。

# Tier-2: 已知硬编码 reports/ 路径常量 (模块名 → [(常量名, 相对路径), ...])
# 来源: 2026-09-02 全量测试 18:00-20:49 + 2026-09-03 测量运行实际落盘产物逆查
# v8.3 phases 已处理 (治理批次 4): generate_daily_trade_plan.REPORTS_DIR + hedge._REPORTS_DIR
# ai_decision 已重构 (治理批次 4): 测试 helper 改用被测模块常量, patch 可同步生效
_HARDCODED_REPORTS_CONSTANTS: dict[str, list[tuple[str, str]]] = {
    "utils.infra.core": [("_AUDIT_LOG_DIR", "strategy_registry")],
    "utils.alpha.kronos_predictor": [("_PREDICTIONS_DIR", "kronos_predictions")],
    "utils.alpha.vibe_backtest_bridge": [("_BACKTEST_DIR", "vibe_backtest")],
    "utils.execution.fills_store": [("_FILLS_DIR", "fills")],
    "utils.risk.risk_bus": [("_AUDIT_LOG_DIR", "risk_bus_audit")],
    "utils.last30days_adapter": [
        ("_AUDIT_DIR", "last30days"),
        # _CACHE_DIR 真实位置在 data/external_cache (非 reports/);
        # 缓存命中会令 search_topic 提前返回不写审计, 曾致跨测试
        # 顺序依赖假红 (test_audit_written_on_success, 2026-09-03 实锤)
        ("_CACHE_DIR", "last30days_cache"),
    ],
    "utils.alpha.vix_data_source": [
        ("_CACHE_DIR", "volatility"),
        ("_CACHE_PATH", "volatility/vix_cache.json"),
    ],
    "utils.llm_client": [("_USAGE_LOG", "llm_usage.jsonl")],
    "scripts.v87_release_gate": [("WAVE7_REPORT_DIR", "wave7")],
    "utils.alpha.llm.base": [("_AUDIT_LOG_DIR", "llm_router")],
    "utils.phase_manager": [("REPORT_DIR", "")],
    "utils.alpha.auto_retrain_scheduler": [("_TASKS_DIR", "auto_retrain")],
    "utils.alpha.mlops_pipeline": [("_LOG_DIR", "mlops")],
    "utils.alpha.drift_monitor": [
        ("_DEFAULT_ALERTS_DIR", "drift_alerts"),
        ("_DEFAULT_REPORTS_DIR", "drift"),
    ],
    "utils.alpha.delayed_label_tracker": [("_DEFAULT_STORAGE_DIR", "delayed_labels")],
    "utils.llm_evolution.knowledge_base": [("_DEFAULT_KB_PATH", "evolution/knowledge_base.jsonl")],
    "utils.execution.broker_adapters": [("_DEFAULT_AUDIT_LOG_DIR", "broker_audit")],

    "scripts.launch_shadow_30day": [
        ("SHADOW_REPORT_DIR", "shadow"),
        ("SHADOW_STATUS_FILE", "shadow/shadow_30day_status.json"),
    ],
    "scripts.phase_b_b4_shadow_runner": [
        ("SHADOW_REPORT_DIR", "shadow"),
        ("SHADOW_STATUS_FILE", "shadow/b4_shadow_status.json"),
    ],
    # shadow 30 天三线 jsonl 写源 (2026-09-05 治理): 测试直调
    # apply_mvsk_shadow_to_mid_layer/_save_qlib_shadow_signal 曾把测试数据
    # (date=""/08-18/09-01) 写进生产 mvsk/qlib jsonl (17:30:46 同批实锤)
    "utils.universe.portfolio_builder": [
        ("MVSK_SHADOW_REPORT_PATH", "shadow/mvsk_p5_daily_diff.jsonl"),
    ],
    "utils.signal_fusion": [
        ("QLIB_SHADOW_REPORT_PATH", "shadow/qlib_lgb_v2_daily.jsonl"),
    ],
    "generate_daily_trade_plan": [("REPORTS_DIR", "")],
    "workflow.phases.hedge": [("_REPORTS_DIR", "")],
    "ai_decision.backtest_replay": [("_REPORT_DIR", "ai_decision")],
    "ai_decision.eod_review": [
        ("_REPORT_DIR", "ai_decision"),
        ("_EXEC_AUDIT_DIR", "ai_decision/execution"),
        ("_TCA_ESTIMATE_DIR", "tca"),
    ],
    "ai_decision.execution_audit": [("_EXEC_AUDIT_DIR", "ai_decision/execution")],
    "ai_decision.grayscale_state": [
        ("_GRAYSCALE_STATE_FILE", "ai_decision/grayscale_state.json"),
    ],
    "ai_decision.orchestrator": [("_AUDIT_DIR", "ai_decision")],
    "ai_decision.dashboard": [
        ("_REPORT_DIR", "ai_decision"),
        ("_EXEC_AUDIT_DIR", "ai_decision/execution"),
        ("_TCA_ESTIMATE_DIR", "tca"),
    ],
}


@pytest.fixture(autouse=True)
def _isolate_production_report_writes(tmp_path, monkeypatch):
    """系统级隔离: 测试写盘与生产 reports/ 及降级审计日志分流到独立临时根

    隔离根使用独立 mkdtemp 而非 tmp_path: 部分测试断言 tmp_path 初始为空
    (如 test_generate_report_save_to_file 断言 len(files)==1) 或自建
    tmp_path/"reports" 目录 (无 exist_ok), 预创建任何内容都会引入假红。
    """
    import importlib
    import shutil
    import tempfile as _tf
    from pathlib import Path as _P

    import utils.degradation_audit as _da
    import utils.path_config as _pc

    _iso_root = _P(_tf.mkdtemp(prefix="pytest_iso_reports_"))
    _tmp_reports = _iso_root / "reports"

    # --- 通道 1: get_reports_dir 扇出重定向 ---
    _orig = _pc.get_reports_dir

    def _fake_reports_dir():
        return _tmp_reports

    monkeypatch.setattr(_pc, "get_reports_dir", _fake_reports_dir)
    for _mod in list(sys.modules.values()):
        if _mod is None:
            continue
        try:
            _name = getattr(_mod, "__name__", "")
            if not _name or _name.startswith("tests"):
                continue  # tests/ 模块持有真函数引用, 保持可测真实行为
            if getattr(_mod, "get_reports_dir", None) is _orig:
                monkeypatch.setattr(_mod, "get_reports_dir", _fake_reports_dir)
        except Exception:  # noqa: BLE001 — sys.modules 替身模块防御
            # ImportBlocker (tests/e2e/conftest.py 防 scipy 崩溃链) 等替身模块的
            # __getattr__ 抛 ImportError 而非 AttributeError, getattr 默认值兜不住
            # (2026-09-03 回归: 曾致全量 16225 用例 fixture setup 全崩, 0 passed)
            continue

    # --- 通道 2: 降级审计日志重定向 + 去重标记复位 ---
    monkeypatch.setattr(_da, "LOG_FILE", _iso_root / "degradation_log.jsonl")
    _da.reset_dedupe()

    # --- Tier-2: 已知硬编码路径常量重定向 (强制导入确保常量已构造再 patch) ---
    try:
        for _mod_name, _pairs in _HARDCODED_REPORTS_CONSTANTS.items():
            try:
                _mod = sys.modules.get(_mod_name) or importlib.import_module(_mod_name)
            except Exception:  # noqa: BLE001 — 导入失败保持真路径 (fail-safe)
                continue
            for _attr, _rel in _pairs:
                if hasattr(_mod, _attr):
                    _target = _tmp_reports / _rel
                    # 预创建目录: 部分写入方 (如 _save_cache) 不负责 mkdir
                    # (仅对无后缀的目录型目标, 避免给文件型目标误建同名目录)
                    if not _target.suffix:
                        try:
                            _target.mkdir(parents=True, exist_ok=True)
                        except OSError:
                            pass
                    monkeypatch.setattr(_mod, _attr, _target)

        # vix_data_source: 类属性 CACHE_PATH 在模块加载时固化,
        # patch 模块级 _CACHE_PATH 不影响 self.CACHE_PATH 访问 (2026-09-04 治理批次 2)
        try:
            _vds_mod = sys.modules.get("utils.alpha.vix_data_source")
            if _vds_mod is not None and hasattr(_vds_mod, "VixDataSource"):
                monkeypatch.setattr(
                    _vds_mod.VixDataSource, "CACHE_PATH",
                    _tmp_reports / "volatility" / "vix_cache.json",
                )
        except Exception:  # noqa: BLE001
            pass

        # pipeline_data_mixin: ctx 派生 alpha_signals 路径 (运行时值, 需函数级 patch)
        # 2026-09-04 治理批次 2 P3 — 提取 _get_alpha_signals_report_dir(ctx) 模块级函数
        try:
            import utils.pipeline_data_mixin as _pdm
            monkeypatch.setattr(
                _pdm, "_get_alpha_signals_report_dir",
                lambda ctx=None: _tmp_reports / "pipeline",
            )
        except Exception:  # noqa: BLE001
            pass
        yield
    finally:
        _da.reset_dedupe()
        shutil.rmtree(_iso_root, ignore_errors=True)


@pytest.fixture(autouse=True)
def _enable_log_propagate_for_caplog():
    """测试期把 propagate=False 的 logger 临时设为 True, 让 caplog 能捕获

    根因: utils/logger.py Logger.__init__ 设 propagate=False (防生产重复输出),
    但 pytest caplog 挂 root handler → propagate=False 的子 logger 日志不传播
    → caplog 捕获不到 (2026-09-04 测试债: data_provider 等 6F)
    """
    import logging as _logging
    _manager = _logging.Logger.manager
    _saved = {}
    for _name, _lg in _manager.loggerDict.items():
        if isinstance(_lg, _logging.Logger) and not _lg.propagate:
            _saved[_name] = False
            _lg.propagate = True
    try:
        yield
    finally:
        for _name, _prop in _saved.items():
            _lg = _manager.loggerDict.get(_name)
            if isinstance(_lg, _logging.Logger):
                _lg.propagate = _prop


# ============================================================
# 路径设置 — 确保两个测试目录都能找到核心模块
# ============================================================
# v8.6.7 修复: conftest.py 位于 tests/ 子目录, 需上溯一层才是项目根目录
# 原 _PROJECT_ROOT = os.path.dirname(__file__) → tests/ (错误)
# 新 _PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__)) → 项目根 (正确)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_V83_ROOT = os.path.join(_PROJECT_ROOT, "v8.3_institutional")

for _p in [_PROJECT_ROOT, _V83_ROOT, os.path.join(_V83_ROOT, "src")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ============================================================
# 收集期写盘防护 (2026-09-03): conftest 模块级重定向降级审计日志
# ============================================================
# autouse fixture 只在测试执行期生效; 测试模块在 pytest 收集阶段被 import,
# 其模块级代码 (如 `import daily_trade_executor` 触发 get_config →
# record_degradation) 早于任何 fixture 运行 → 曾致真实 degradation_log.jsonl
# 在收集期被注入条目 (2026-09-03 06:38-06:42 实锤 7 条)。
# 此处在收集开始前把 LOG_FILE 指向进程级临时文件; 测试执行期由
# _isolate_production_report_writes 再按测试重定向到各自 tmp_path。
#
# 通道 3 (2026-09-03 补): get_logger 默认参数在 def 时固化相对 "logs" →
# 测试 import 链 (data_provider 等模块级 get_logger) 向生产 logs/*.log 写入
# (data_provider.log 10:04 测试时段写入实锤)。默认参数固化导致改模块属性
# 无效 → 整函数替换 + sys.modules 扇出; 显式传绝对路径的调用 (如测试
# 传 tmp_path) 保留原意, 避免破坏 test_creates_log_dir 类断言。
try:
    import tempfile as _tempfile
    from pathlib import Path as _Path

    import utils.degradation_audit as _da_module
    import utils.logger as _ul_module

    _iso_logs_root = _Path(_tempfile.mkdtemp(prefix="pytest_iso_logs_"))

    _da_module.LOG_FILE = (
        _Path(_tempfile.mkdtemp(prefix="pytest_degr_log_"))
        / "degradation_log.jsonl"
    )
    _da_module.reset_dedupe()

    _orig_get_logger = _ul_module.get_logger

    def _isolated_get_logger(name, log_dir=None):
        # 仅拦截默认路径 (None / 相对 "logs"); 显式绝对路径保留调用方意图
        if log_dir is None or not _Path(log_dir).is_absolute():
            return _orig_get_logger(name, log_dir=str(_iso_logs_root))
        return _orig_get_logger(name, log_dir=log_dir)

    _ul_module.get_logger = _isolated_get_logger
    _ul_module.setup_loggers = lambda *a, **k: {
        "system": _orig_get_logger("system", log_dir=str(_iso_logs_root)),
        "modules": {},
    }

    for _mod in list(sys.modules.values()):
        if _mod is None:
            continue
        try:
            _mod_name = getattr(_mod, "__name__", "")
            if not _mod_name or _mod_name.startswith("tests"):
                continue
            if getattr(_mod, "get_logger", None) is _orig_get_logger:
                _mod.get_logger = _isolated_get_logger
        except Exception:  # noqa: BLE001 — sys.modules 替身模块防御 (同 P0 修复口径:
            # ImportBlocker 类替身 __getattr__ 抛 ImportError, 逐模块 continue
            # 不让单个异常静默吞掉剩余模块的扇出 patch)
            continue
except Exception:  # noqa: BLE001 — 防护失败不阻断测试收集
    pass

warnings.filterwarnings("ignore")

# ============================================================
# 共享 Fixtures
# ============================================================


@pytest.fixture(scope="session")
def project_root():
    """项目根目录"""
    return _PROJECT_ROOT


@pytest.fixture(scope="session")
def sample_symbols():
    """10 只测试标的代码"""
    return [f"TEST{i:03d}.SZ" for i in range(10)]


@pytest.fixture(scope="session")
def sample_prices(sample_symbols):
    """252 日 * 10 标的的价格矩阵 (有随机游走结构)"""
    np.random.seed(42)
    n_days = 252
    n_symbols = len(sample_symbols)
    returns = np.random.randn(n_days, n_symbols) * 0.02
    prices = 100 * np.exp(np.cumsum(returns, axis=0))
    return pd.DataFrame(prices, columns=sample_symbols)


@pytest.fixture(scope="session")
def sample_returns(sample_prices):
    """对数收益率矩阵"""
    return np.log(sample_prices / sample_prices.shift(1)).dropna()


@pytest.fixture(scope="session")
def sample_ohlcv(sample_symbols):
    """样本 OHLCV 数据 (60 日)"""
    np.random.seed(123)
    n = 60
    data = {}
    for sym in sample_symbols[:3]:
        close = 100 * np.exp(np.cumsum(np.random.randn(n) * 0.015))
        high = close * (1 + np.abs(np.random.randn(n) * 0.02))
        low = close * (1 - np.abs(np.random.randn(n) * 0.02))
        open_ = close * (1 + np.random.randn(n) * 0.005)
        volume = np.random.randint(1e6, 1e7, n)
        df = pd.DataFrame(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            },
            index=pd.date_range("2026-01-01", periods=n, freq="B"),
        )
        data[sym] = df
    return data


@pytest.fixture(scope="session")
def sample_sector_weights(sample_symbols):
    """样本行业权重"""
    return pd.Series(
        np.random.dirichlet(np.ones(len(sample_symbols))),
        index=sample_symbols,
    )


@pytest.fixture(scope="session")
def mock_config():
    """Mock 配置字典 (避免依赖 YAML 文件)"""
    return {
        "account": {
            "total_capital": 5_000_000,
            "stock_etf_capital": 4_000_000,
            "hedge_capital": 1_000_000,
        },
        "risk": {
            "max_single_position": 0.08,
            "max_sector_exposure": 0.30,
            "target_volatility": 0.10,
            "max_drawdown": 0.12,
        },
        "execution": {
            "slippage_bps": 5,
            "commission_rate": 0.0003,
        },
    }


# ============================================================
# Pytest 配置
# ============================================================


def pytest_configure(config):
    """注册自定义标记"""
    # 原有标记
    config.addinivalue_line("markers", "slow: 标记慢速测试 (>5s)")
    config.addinivalue_line("markers", "integration: 标记需要外部 API 的集成测试")
    config.addinivalue_line("markers", "smoke: 标记冒烟测试 (核心路径)")
    # v8.6.7 测试金字塔新增标记
    config.addinivalue_line("markers", "unit: 单元测试 (全 mock, <1s)")
    config.addinivalue_line("markers", "e2e: 端到端测试 (真实历史数据)")
    config.addinivalue_line("markers", "regression: 回归测试 (对应已修复的 bug 编号)")
    config.addinivalue_line("markers", "p0: P0 致命级 bug 回归")
    config.addinivalue_line("markers", "p1: P1 风险缺口级 bug 回归")
    config.addinivalue_line("markers", "bug(id): 关联 bug 编号, 如 @pytest.mark.bug('P0-E')")
    # ECC GAP-7/8/6 新增标记 (2026-07-29)
    config.addinivalue_line(
        "markers",
        "reproducibility: 可复现性测试 (ECC GAP-7, 同 config+seed+dataset 重跑一致性)",
    )
    config.addinivalue_line(
        "markers",
        "contract: 数据契约测试 (ECC GAP-8, 字段/类型/null/point-in-time 校验)",
    )
    config.addinivalue_line("markers", "drift: 漂移监控测试 (ECC GAP-6, sim_mode 激活 + KS/PSI + 延迟标签)")

    # P0-2 (2026-09-01): 单元/smoke 测试默认离线 — QUANT_OFFLINE=1 短路
    # utils/external_data_source 的全部外网请求 (FRED/Treasury/CoinGecko 等),
    # 由各 API 类既有 fail-safe 降级返回 None; 传 --run-integration 时不设置,
    # 保留 integration/e2e 用例的真实网络行为; 外部已显式设 1 时不覆盖
    if not config.getoption("--run-integration"):
        os.environ.setdefault("QUANT_OFFLINE", "1")

    import logging

    logging.getLogger("matplotlib").setLevel(logging.WARNING)


def pytest_addoption(parser):
    """添加命令行选项"""
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="运行 integration/e2e 标记的测试 (默认跳过)",
    )


def pytest_collection_modifyitems(config, items):
    """默认跳过 integration/e2e 测试, 除非显式传 --run-integration"""
    if config.getoption("--run-integration"):
        return
    skip_marker = pytest.mark.skip(
        reason="integration/e2e 测试, 需传 --run-integration 启用"
    )
    for item in items:
        if "integration" in item.keywords or "e2e" in item.keywords:
            item.add_marker(skip_marker)


# ============================================================
# v8.6.7 测试金字塔新增 fixtures
# ============================================================

import json  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402


@pytest.fixture(scope="session")
def reports_dir():
    """v8.3 真实历史报告目录 (E2E 黄金数据源)"""
    return Path(_PROJECT_ROOT) / "v8.3_institutional" / "reports"


@pytest.fixture(scope="session")
def trade_plans_dir():
    """v8.3 交易计划目录"""
    return Path(_PROJECT_ROOT) / "v8.3_institutional" / "trade_plans"


@pytest.fixture(scope="session")
def real_pnl_report(reports_dir):
    """加载真实生产报告 daily_pnl_report_2026-07-21.json (26 标的完整格式)

    用于 E2E 测试和报告兼容性单元测试的黄金样本
    """
    path = reports_dir / "daily_pnl_report_2026-07-21.json"
    if not path.exists():
        pytest.skip(f"真实报告文件不存在: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def sample_pnl_report_full():
    """完整格式 pnl_report (portfolio_pnl.details, 3 标的)

    用于测试 _extract_positions 兼容层
    """
    return {
        "meta": {"report_date": "2026-07-21"},
        "portfolio_pnl": {
            "summary": {
                "total_cost": 1_000_000,
                "total_market_value": 1_050_000,
                "total_pnl": 50_000,
                "total_pnl_pct": 5.0,
                "margin_used": 600_000,
                "total_equity": 1_500_000,
            },
            "details": [
                {
                    "code": "588080.SH",
                    "name": "科创50ETF",
                    "market_value": 350_000,
                    "pnl": 20_000,
                    "daily_pnl_pct": 6.0,
                    "cost_amount": 330_000,
                },
                {
                    "code": "512880.SH",
                    "name": "证券ETF",
                    "market_value": 400_000,
                    "pnl": 15_000,
                    "daily_pnl_pct": 3.9,
                    "cost_amount": 385_000,
                },
                {
                    "code": "510050.SH",
                    "name": "上证50ETF",
                    "market_value": 300_000,
                    "pnl": 15_000,
                    "daily_pnl_pct": 5.3,
                    "cost_amount": 285_000,
                },
            ],
        },
    }


@pytest.fixture
def sample_pnl_report_simplified():
    """简化格式 pnl_report (顶层 positions, list of dicts)

    模拟无横杠文件名 daily_pnl_report_YYYYMMDD.json 的结构
    """
    return {
        "date": "2026-07-21",
        "summary": {
            "total_cost": 800_000,
            "total_market_value": 840_000,
            "total_pnl": 40_000,
            "total_pnl_pct": 5.0,
        },
        "positions": [
            {
                "code": "588080.SH",
                "pnl": 20_000,
                "market_value": 350_000,
                "daily_pnl_pct": 6.0,
            },
            {
                "code": "512880.SH",
                "pnl": 20_000,
                "market_value": 490_000,
                "daily_pnl_pct": 4.2,
            },
        ],
    }


@pytest.fixture
def sample_pnl_report_broken_p0d():
    """P0-D bug 重现样本: margin_used/total_equity 为 None"""
    return {
        "portfolio_pnl": {
            "summary": {
                "total_cost": 1_000_000,
                "total_market_value": 1_050_000,
                "margin_used": None,  # bug 触发条件
                "total_equity": None,  # bug 触发条件
                "positions": {},
            }
        }
    }


@pytest.fixture
def sample_trade_plan():
    """标准测试交易计划 (含 BUY + SELL 订单, 用于 L2/L3 过滤测试)"""
    return {
        "trade_date": "2026-07-22",
        "phase": {"daily_capital": 150_000, "day_capital": 150_000},
        "execution_plan": {
            "morning_orders": [
                {
                    "symbol": "588080.SH",
                    "direction": "BUY",
                    "shares": 1000,
                    "est_amount": 100_000,
                },
                {
                    "symbol": "512880.SH",
                    "direction": "SELL",
                    "shares": 500,
                    "est_amount": 50_000,
                },
            ],
            "afternoon_orders": [
                {
                    "symbol": "510050.SH",
                    "direction": "BUY",
                    "shares": 2000,
                    "est_amount": 200_000,
                },
            ],
        },
        "market_state": {},
        "risk_guard": {},
        "hedge_config": {"layers": {"layer1_futures": {"ratio": 0.15}}},
    }


@pytest.fixture
def broken_hedge_positions_p0e():
    """P0-E bug 重现样本: hedge_positions 含字符串字段 (description/hedge_mode)"""
    return {
        "meta": {"total_capital": 5_000_000, "hedge_capital": 1_000_000},
        "positions": {"588080.SH": {"shares": 14100, "est_price": 1.95, "sector": "科技"}},
        "hedge_positions": {
            "description": "200万纯期权对冲 — 无期货空头",  # P0-E bug 触发: str
            "hedge_mode": "OPTIONS_ONLY",  # P0-E bug 触发: str
            "ETF_put_options": {
                "instrument": "510050 Put",
                "exchange": "SSE",
                "target_contracts": 60,
                "premium_budget": 900_000,
                "strike": "OTM_5%",
            },
            "ETF_put_options_2": {
                "instrument": "588080 Put",
                "exchange": "SSE",
                "target_contracts": 25,
                "premium_budget": 300_000,
            },
        },
    }


@pytest.fixture
def broker_callback_mock():
    """模拟券商执行回调 (替代真实 QMT/CTP 接口)

    签名: callback(level: int, actions: list) -> Dict
    """
    callback = MagicMock(name="broker_callback")
    callback.return_value = {
        "executed": True,
        "orders_sent": 5,
        "broker_status": "OK",
    }
    return callback


@pytest.fixture
def mock_astock_realtime(monkeypatch):
    """mock utils.astock_realtime.get_realtime_quotes

    返回沪深300ETF -5.2% (触发 L2) 的模拟行情
    """

    def _fake_quotes(codes):
        return {
            "510300": {
                "price": 3.85,
                "pre_close": 4.06,
                "change_pct": -5.2,  # 触发 L2
            }
        }

    # 多种可能的导入路径
    try:
        monkeypatch.setattr("utils.astock_realtime.get_realtime_quotes", _fake_quotes)
    except (AttributeError, ImportError):
        pass


@pytest.fixture
def mock_akshare_unavailable(monkeypatch):
    """模拟 akshare 不可用 (触发 fail-closed 路径)"""

    def _raise(*args, **kwargs):
        raise ImportError("akshare not installed (mocked)")

    try:
        monkeypatch.setattr("akshare.stock_zh_a_spot_em", _raise, raising=False)
        monkeypatch.setattr("akshare.stock_zh_index_spot_em", _raise, raising=False)
    except (AttributeError, ImportError):
        pass


@pytest.fixture
def mock_external_data_unavailable(monkeypatch):
    """模拟 ExternalDataManager 不可用 (触发 overnight_gap fail-closed)"""

    def _raise(*args, **kwargs):
        raise ConnectionError("ExternalDataSource unavailable (mocked)")

    try:
        monkeypatch.setattr(
            "utils.external_data_source.ExternalDataManager",
            _raise,
            raising=False,
        )
    except (AttributeError, ImportError):
        pass


# ============================================================
# v8.6.7 跨层共享 fixture (unit + integration + e2e 都可用)
# ============================================================
# 这些 fixture 原本在 tests/unit/conftest.py, 现提升到顶层 conftest
# 使 integration 测试也能复用 KillSwitch / 环境变量隔离


@pytest.fixture
def tmp_kill_switch_log(tmp_path, monkeypatch):
    """隔离 kill_switch_events.jsonl 写入, 避免污染 logs/

    monkeypatch utils.kill_switch.KILL_SWITCH_LOG 指向 tmp_path
    """
    tmp_log = tmp_path / "kill_switch_events.jsonl"

    try:
        import utils.kill_switch as ks_module

        monkeypatch.setattr(ks_module, "KILL_SWITCH_LOG", tmp_log)
    except ImportError:
        pass

    return tmp_log


@pytest.fixture
def clean_env(monkeypatch):
    """清理 KillSwitch / Guard 相关环境变量, 保证测试可重现

    清理:
        - KILL_SWITCH_SIM_MODE
        - KILL_SWITCH_MARGIN_RATIO
        - TRADING_ENV
    """
    for var in ("KILL_SWITCH_SIM_MODE", "KILL_SWITCH_MARGIN_RATIO", "TRADING_ENV"):
        monkeypatch.delenv(var, raising=False)
    return None


@pytest.fixture
def production_env(monkeypatch):
    """设置 TRADING_ENV=production, 触发 fail-closed 路径"""
    monkeypatch.setenv("TRADING_ENV", "production")
    return "production"


# ============================================================
# Shadow 数据质量闭环共享 fixture (unit + integration)
# ============================================================
# 这些 fixture 供 Shadow 门槛测试的两层防护链复用:
#   - mock_drift_report: 模拟 DriftReport 对象
#   - mock_compute_prediction_drift: patch compute_prediction_drift
# 辅助函数见 tests/shadow_helpers.py

import sys as _sys  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

# 添加 tests/ 目录到 sys.path, 使 shadow_helpers 可被 import
_TESTS_DIR = _Path(__file__).resolve().parent
if str(_TESTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_TESTS_DIR))

from shadow_helpers import make_mock_drift_report as _make_mock_report  # noqa: E402


@pytest.fixture
def mock_drift_report():
    """模拟 DriftReport 对象 (每个测试独立实例).

    提供 severity.value / drift_score / psi / to_dict() 等接口.
    默认值: severity=low, drift_score=0.15, psi=0.08.
    测试可修改返回值自定义行为.
    """
    return _make_mock_report()


@pytest.fixture
def mock_compute_prediction_drift(monkeypatch, mock_drift_report):
    """Patch compute_prediction_drift, 返回 MagicMock.

    默认 return_value=mock_drift_report.
    测试可修改 return_value 或设置 side_effect 自定义行为.

    不需要此 mock 的测试 (如 n < 5 的 skipped 场景) 不传入此 fixture 参数即可,
    fixture 不会激活, 不会 patch.
    """
    mock = MagicMock(return_value=mock_drift_report)
    monkeypatch.setattr("utils.alpha.drift_monitor.compute_prediction_drift", mock)
    return mock


# ============================================================
# Fallback: pytest-benchmark optional plugin
# If pytest-benchmark is not installed in the environment, provide
# a no-op `benchmark` fixture so performance tests still run.
# ============================================================
try:
    import pytest_benchmark  # noqa: F401
except Exception:

    @pytest.fixture
    def benchmark():
        """No-op fallback for `benchmark(func, *args, **kwargs)`.

        When `pytest-benchmark` is unavailable, this simply calls the
        provided callable and returns its result so tests don't error
        due to a missing fixture.
        """

        def _runner(func, *args, **kwargs):
            return func(*args, **kwargs)

        return _runner
