"""Phase 3 (2026-07-29) 回归测试: Purged K-Fold 集成 / VaR 回测接线 / broker 连接抽离。

覆盖:
  P3-1: lgb_enhanced_trainer.time_series_cv_evaluate 使用 Purged K-Fold (非裸 TimeSeriesSplit)
  P3-2: unified_risk_cockpit.VaRBacktester 含 Basel 交通灯, 且 run_var_backtest 经报告接线生效
  P3-3: daily_workflow._connect_live_broker 抽离 CTP→同花顺连接逻辑, 三处调用点复用
"""
import importlib.util
import sys
import types
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "v8.3_institutional"))

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
from src.risk.unified_risk_cockpit import (
    UnifiedRiskCockpit,
    VaRBacktester,
)

import lgb_enhanced_trainer
from utils.purged_kfold import purged_timeseries_split


def _load_daily_workflow():
    """从文件路径加载 daily_workflow (包名含数字, 无法用普通 import)。"""
    path = ROOT / "v8.3_institutional" / "daily_workflow.py"
    spec = importlib.util.spec_from_file_location(
        "daily_workflow_phase3_test", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ───────────────────────────── P3-1: Purged K-Fold ─────────────────────────────

def test_purged_kfold_disjoint_and_in_range():
    """purged_timeseries_split 返回的折内 train/test 索引不相交且均在范围内。"""
    n = 500
    folds = list(purged_timeseries_split(n_samples=n, n_splits=5, embargo_pct=0.01))
    assert len(folds) == 5
    for train_idx, test_idx in folds:
        assert len(set(train_idx) & set(test_idx)) == 0
        assert min(train_idx) >= 0 and max(train_idx) < n
        assert min(test_idx) >= 0 and max(test_idx) < n


def test_purged_kfold_embrago_removes_overlap():
    """ embargo 在训练集尾部剔除样本, 使 train 最大索引早于 test 最小索引。"""
    n = 200
    folds = list(purged_timeseries_split(n_samples=n, n_splits=4, embargo_pct=0.05))
    for train_idx, test_idx in folds:
        if len(train_idx) and len(test_idx):
            assert max(train_idx) < min(test_idx)


def test_time_series_cv_evaluate_uses_purged():
    """生产训练器的 CV 函数应正常返回 cv_score, 且底层调用 Purged K-Fold。"""
    X = np.random.RandomState(0).randn(240, 6).astype(float)
    y = np.random.RandomState(1).randn(240).astype(float)
    config = {
        "label_horizon": 5,
        "lgb_params": {
            "n_estimators": 20, "learning_rate": 0.1, "num_leaves": 15,
            "verbose": -1, "device_type": "cpu",
        },
        "early_stopping_rounds": 10,
    }

    import utils.purged_kfold as pk_mod
    calls = []
    orig = pk_mod.purged_timeseries_split

    def spy(n_samples, n_splits, embargo_pct=0.01):
        calls.append((n_samples, n_splits))
        return orig(n_samples=n_samples, n_splits=n_splits, embargo_pct=embargo_pct)

    with patch.object(pk_mod, "purged_timeseries_split", side_effect=spy):
        result = lgb_enhanced_trainer.time_series_cv_evaluate(X, y, config, n_splits=4)

    assert isinstance(result, dict)
    # time_series_cv_evaluate 返回 fold_metrics / mean_ic 等, 确认 CV 正常完成
    assert "fold_metrics" in result and len(result["fold_metrics"]) == 4
    assert len(calls) == 1
    assert calls[0][0] == 240 and calls[0][1] == 4


# ───────────────────────── P3-2: VaR 回测 + Basel 交通灯 ────────────────────────

def _make_returns(n=120, violation_idx=None):
    r = np.random.RandomState(42).normal(0.001, 0.01, n)
    if violation_idx:
        for i in violation_idx:
            r[i] = -0.05
    return r.tolist()


def test_var_backtester_has_basel_traffic_light():
    """VaRBacktester.run_tests 返回必须包含 basel_traffic_light, 且 zone 合法。"""
    returns = _make_returns(violation_idx=[10, 20, 30])
    arr = np.array(returns)
    var = (np.percentile(np.abs(arr), 95) * -1.0) * np.ones_like(arr)
    vb = VaRBacktester(confidence=0.99)
    res = vb.run_tests(returns, var.tolist())
    assert res["status"] == "COMPLETE"
    assert "basel_traffic_light" in res
    bl = res["basel_traffic_light"]
    assert bl["zone"] in ("GREEN", "YELLOW", "RED")
    assert "expected_exceptions" in bl


def test_var_backtest_wired_into_report():
    """此前定义却从未调用的 run_var_backtest 现已经 _var_backtest_lines 接入报告。"""
    cockpit = UnifiedRiskCockpit.__new__(UnifiedRiskCockpit)
    cockpit.var_backtester = VaRBacktester(confidence=0.99)
    cockpit._return_history = _make_returns(n=120, violation_idx=[5, 15, 25, 35])
    lines = cockpit._var_backtest_lines()
    assert isinstance(lines, list) and len(lines) > 0
    assert "Basel" in "\n".join(lines)


def test_var_backtest_insufficient_data_skips():
    """数据不足时 run_var_backtest 返回 SKIPPED, 不抛异常。"""
    cockpit = UnifiedRiskCockpit.__new__(UnifiedRiskCockpit)
    cockpit.var_backtester = VaRBacktester(confidence=0.99)
    cockpit._return_history = [0.01, -0.02]
    assert cockpit.run_var_backtest()["status"] == "SKIPPED"


# ───────────────── P3-3: broker 连接逻辑抽离 ─────────────────────

@contextmanager
def _broker_fakes(ctp_instance, ths_instance=None):
    """向 sys.modules 注入假券商模块, 使 daily_workflow 内部的
    `from src.execution.ctp_gateway import CTPGateway` 等导入可解析到 Mock。

    ctp_instance / ths_instance 为函数调用后期望返回的 broker 对象实例。
    """
    stubs = {}
    if "src" not in sys.modules:
        m = types.ModuleType("src")
        stubs["src"] = m
    if "src.execution" not in sys.modules:
        m = types.ModuleType("src.execution")
        stubs["src.execution"] = m
    ctp_mod = types.ModuleType("src.execution.ctp_gateway")
    ctp_mod.CTPGateway = lambda *a, **k: ctp_instance
    stubs["src.execution.ctp_gateway"] = ctp_mod
    if ths_instance is not None:
        ths_mod = types.ModuleType("ths_real_broker")
        ths_mod.THSRealBroker = lambda *a, **k: ths_instance
        stubs["ths_real_broker"] = ths_mod

    saved = {}
    for name, mod in stubs.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = mod
    try:
        yield
    finally:
        for name in stubs:
            if saved[name] is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = saved[name]


def _make_wf():
    dw = _load_daily_workflow()
    wf = dw.DailyWorkflow.__new__(dw.DailyWorkflow)
    wf.config = SimpleNamespace(
        CTP_FRONT_ADDR="", CTP_BROKER_ID="", CTP_USER_ID="",
        CTP_PASSWORD="", CTP_FLOW_PATH="ctp_flow", THS_ACCOUNT="",
    )
    return dw, wf


def test_connect_live_broker_none_when_unconfigured():
    _, wf = _make_wf()
    broker, src = wf._connect_live_broker()
    assert broker is None
    assert src == "none"


def test_connect_live_broker_ctp_priority():
    _dw, wf = _make_wf()
    wf.config.CTP_FRONT_ADDR = "tcp://x"
    wf.config.CTP_USER_ID = "u"
    wf.config.CTP_PASSWORD = "p"
    fake_ctp = MagicMock()
    fake_ctp.is_connected.return_value = True
    with _broker_fakes(ctp_instance=fake_ctp):
        broker, src = wf._connect_live_broker()
    assert broker is fake_ctp
    assert src == "ctp_live"


def test_connect_live_broker_ths_fallback():
    _dw, wf = _make_wf()
    wf.config.THS_ACCOUNT = "acc123"
    fake_ctp = MagicMock()
    fake_ctp.is_connected.return_value = False
    fake_ths = MagicMock()
    fake_ths.connect.return_value = True
    with _broker_fakes(ctp_instance=fake_ctp, ths_instance=fake_ths):
        broker, src = wf._connect_live_broker()
    assert broker is fake_ths
    assert src == "ths_live"


def test_connect_live_broker_called_at_three_sites():
    """三处 broker 选择逻辑均已复用 _connect_live_broker (1 def + 3 call)。"""
    dw = _load_daily_workflow()
    source = Path(dw.__file__).read_text(encoding="utf-8")
    assert source.count("_connect_live_broker") >= 4
    assert "优先级 1: CTP 期货网关" not in source
    assert "优先级 2: 同花顺实盘 (股票/ETF/期权)" not in source
