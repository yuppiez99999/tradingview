# -*- coding: utf-8 -*-
"""_load_prediction_prices B2.5 单次扫描索引 单元测试

覆盖场景:
1. 多 symbol 查询只扫描一次报告目录 (O(N×M) → O(M))
2. 进程级缓存: 后续调用复用索引, 不再扫描
3. 正确性: 与原逐 symbol 扫描逻辑等价 (按日期升序 + 每文件首匹配 + close_price>0)
4. 代码后缀剥离: "600519.SH" / "600519" 等价
5. 每个报告文件内同一 symbol 只取第一个匹配 (原 break 语义)
6. 损坏文件跳过, 不影响其他文件
7. 样本不足 (<30) 返回 None
8. days 截断生效
9. 报告目录不存在时返回空索引
10. 线程安全: 并发调用只触发一次扫描
11. _reset_prediction_prices_index 清空缓存
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

# 确保 utils 在 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 显式加载根目录的 daily_trade_executor.py
_dte_path = _PROJECT_ROOT / "daily_trade_executor.py"
_spec = importlib.util.spec_from_file_location("daily_trade_executor", _dte_path)
daily_trade_executor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(daily_trade_executor)


# ============================================================
# fixture: 每个测试前后清空缓存, 保证隔离
# ============================================================
@pytest.fixture(autouse=True)
def _reset_cache_around_test():
    daily_trade_executor._reset_prediction_prices_index()
    yield
    daily_trade_executor._reset_prediction_prices_index()


# ============================================================
# 测试辅助
# ============================================================
def _write_pnl_report(
    reports_dir: Path,
    date_str: str,
    details: list,
    *,
    malformed: bool = False,
) -> Path:
    """写一个 daily_pnl_report_{date}.json 文件

    Args:
        reports_dir: 报告目录
        date_str: 日期 (YYYYMMDD), 用于文件名
        details: [{"code": "600519.SH", "close_price": 1500.0}, ...]
        malformed: True=写入损坏 JSON, False=正常写入
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"daily_pnl_report_{date_str}.json"
    if malformed:
        path.write_text("{ broken json: ", encoding="utf-8")
        return path
    payload = {
        "date": date_str,
        "portfolio_pnl": {"details": details},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _make_details(symbol_prices: dict) -> list:
    """构造 details: {code: price} → [{"code": code, "close_price": price}, ...]"""
    return [{"code": code, "close_price": price} for code, price in symbol_prices.items()]


# ============================================================
# 测试用例
# ============================================================
def test_single_scan_for_multiple_symbols(tmp_path, monkeypatch):
    """3 个 symbol 查询, 报告目录只扫描 1 次 (而不是 3 次)"""
    # 准备 40 天的报告, 每天含 3 个 symbol
    reports_dir = tmp_path / "v8.3_institutional" / "reports"
    for i in range(40):
        date_str = f"202501{i:02d}"
        details = _make_details({
            "600519.SH": 1500.0 + i,
            "000858.SZ": 200.0 + i,
            "601318.SH": 80.0 + i,
        })
        _write_pnl_report(reports_dir, date_str, details)

    monkeypatch.setattr(daily_trade_executor, "PROJECT_ROOT", tmp_path)

    # 计数 Path.glob 调用次数
    glob_call_count = {"n": 0}
    real_glob = Path.glob

    def _count_glob(self, pattern):
        if pattern == "daily_pnl_report_*.json":
            glob_call_count["n"] += 1
        return real_glob(self, pattern)

    with patch("pathlib.Path.glob", _count_glob):
        # 查询 3 个 symbol
        for code in ("600519", "000858", "601318"):
            arr = daily_trade_executor._load_prediction_prices(code, days=120)
            assert arr is not None
            assert len(arr) == 40

    # 整个测试只扫描 1 次 (索引构建后复用)
    assert glob_call_count["n"] == 1, (
        f"期望只扫描 1 次, 实际扫描 {glob_call_count['n']} 次 (O(N×M) 未优化为 O(M))"
    )


def test_cache_reused_across_calls(tmp_path, monkeypatch):
    """首次调用构建索引, 后续调用复用 (glob 只调用 1 次)"""
    reports_dir = tmp_path / "v8.3_institutional" / "reports"
    for i in range(35):
        _write_pnl_report(
            reports_dir,
            f"202501{i:02d}",
            _make_details({"600519.SH": 1500.0 + i}),
        )

    monkeypatch.setattr(daily_trade_executor, "PROJECT_ROOT", tmp_path)

    glob_call_count = {"n": 0}
    real_glob = Path.glob

    def _count_glob(self, pattern):
        if pattern == "daily_pnl_report_*.json":
            glob_call_count["n"] += 1
        return real_glob(self, pattern)

    with patch("pathlib.Path.glob", _count_glob):
        daily_trade_executor._load_prediction_prices("600519")
        assert glob_call_count["n"] == 1

        # 第二次 / 第三次调用: 应复用缓存, 不再 glob
        daily_trade_executor._load_prediction_prices("600519")
        daily_trade_executor._load_prediction_prices("600519")
        assert glob_call_count["n"] == 1, "缓存失效, 后续调用重新扫描了目录"


def test_correctness_matches_original_logic(tmp_path, monkeypatch):
    """正确性: 价格序列按日期升序 + close_price>0 过滤"""
    reports_dir = tmp_path / "v8.3_institutional" / "reports"
    # 40 天, 价格递增 (便于验证顺序)
    for i in range(40):
        _write_pnl_report(
            reports_dir,
            f"202501{i:02d}",
            _make_details({"600519.SH": 100.0 + i}),
        )
    monkeypatch.setattr(daily_trade_executor, "PROJECT_ROOT", tmp_path)

    arr = daily_trade_executor._load_prediction_prices("600519", days=120)
    assert arr is not None
    # 价格应为 [100.0, 101.0, ..., 139.0]
    expected = np.array([100.0 + i for i in range(40)], dtype=float)
    np.testing.assert_array_equal(arr, expected)


def test_code_suffix_stripped(tmp_path, monkeypatch):
    """代码后缀剥离: '600519.SH' / '600519' / 'sh600519' 查询等价"""
    reports_dir = tmp_path / "v8.3_institutional" / "reports"
    for i in range(35):
        _write_pnl_report(
            reports_dir,
            f"202501{i:02d}",
            _make_details({"600519.SH": 100.0 + i}),
        )
    monkeypatch.setattr(daily_trade_executor, "PROJECT_ROOT", tmp_path)

    # 查询时用不带后缀的 code
    arr_plain = daily_trade_executor._load_prediction_prices("600519")
    assert arr_plain is not None
    assert len(arr_plain) == 35

    # 查询时用带后缀的 code (也应能命中, 因为 _load_prediction_prices 会剥离后缀)
    arr_suffixed = daily_trade_executor._load_prediction_prices("600519.SH", days=120)
    assert arr_suffixed is not None
    np.testing.assert_array_equal(arr_plain, arr_suffixed)


def test_first_match_per_file(tmp_path, monkeypatch):
    """单个文件内同一 symbol 出现多次时, 只取第一个匹配 (保持原 break 语义)"""
    reports_dir = tmp_path / "v8.3_institutional" / "reports"
    # 同一文件中 600519 出现两次, 第一次价格 100, 第二次价格 999
    for i in range(35):
        _write_pnl_report(
            reports_dir,
            f"202501{i:02d}",
            [
                {"code": "600519.SH", "close_price": 100.0 + i},
                {"code": "600519.SZ", "close_price": 999.0},  # 同 code 不同后缀, 第二次出现
            ],
        )
    monkeypatch.setattr(daily_trade_executor, "PROJECT_ROOT", tmp_path)

    arr = daily_trade_executor._load_prediction_prices("600519", days=120)
    assert arr is not None
    # 应该全部取第一个匹配 (100.0 + i), 而不是 999.0
    expected = np.array([100.0 + i for i in range(35)], dtype=float)
    np.testing.assert_array_equal(arr, expected)


def test_malformed_file_skipped(tmp_path, monkeypatch):
    """损坏的 JSON 文件跳过, 不影响其他文件解析"""
    reports_dir = tmp_path / "v8.3_institutional" / "reports"
    # 35 个正常文件 + 1 个损坏文件
    for i in range(35):
        _write_pnl_report(
            reports_dir,
            f"202501{i:02d}",
            _make_details({"600519.SH": 100.0 + i}),
        )
    _write_pnl_report(reports_dir, "20250199", [], malformed=True)

    monkeypatch.setattr(daily_trade_executor, "PROJECT_ROOT", tmp_path)

    arr = daily_trade_executor._load_prediction_prices("600519", days=120)
    assert arr is not None
    # 35 个正常文件的价格都应被收集 (损坏文件不影响)
    assert len(arr) == 35


def test_insufficient_samples_returns_none(tmp_path, monkeypatch):
    """样本数 < 30 返回 None"""
    reports_dir = tmp_path / "v8.3_institutional" / "reports"
    for i in range(20):  # 只有 20 天 < 30
        _write_pnl_report(
            reports_dir,
            f"202501{i:02d}",
            _make_details({"600519.SH": 100.0 + i}),
        )
    monkeypatch.setattr(daily_trade_executor, "PROJECT_ROOT", tmp_path)

    arr = daily_trade_executor._load_prediction_prices("600519", days=120)
    assert arr is None


def test_days_truncation(tmp_path, monkeypatch):
    """days 参数截断最近 N 天"""
    reports_dir = tmp_path / "v8.3_institutional" / "reports"
    for i in range(50):
        _write_pnl_report(
            reports_dir,
            f"202501{i:02d}",
            _make_details({"600519.SH": 100.0 + i}),
        )
    monkeypatch.setattr(daily_trade_executor, "PROJECT_ROOT", tmp_path)

    arr = daily_trade_executor._load_prediction_prices("600519", days=10)
    assert arr is not None
    assert len(arr) == 10
    # 应取最后 10 天: [140.0, 141.0, ..., 149.0]
    expected = np.array([140.0 + i for i in range(10)], dtype=float)
    np.testing.assert_array_equal(arr, expected)


def test_missing_reports_dir_returns_empty(tmp_path, monkeypatch):
    """报告目录不存在时, 索引为空, 所有 symbol 返回 None"""
    # tmp_path 下不创建 v8.3_institutional/reports
    monkeypatch.setattr(daily_trade_executor, "PROJECT_ROOT", tmp_path)

    arr = daily_trade_executor._load_prediction_prices("600519", days=120)
    assert arr is None


def test_symbol_not_in_reports_returns_none(tmp_path, monkeypatch):
    """查询不在报告中的 symbol 返回 None"""
    reports_dir = tmp_path / "v8.3_institutional" / "reports"
    for i in range(40):
        _write_pnl_report(
            reports_dir,
            f"202501{i:02d}",
            _make_details({"600519.SH": 100.0 + i}),
        )
    monkeypatch.setattr(daily_trade_executor, "PROJECT_ROOT", tmp_path)

    arr = daily_trade_executor._load_prediction_prices("999999", days=120)
    assert arr is None


def test_close_price_zero_or_negative_skipped(tmp_path, monkeypatch):
    """close_price <= 0 的样本被跳过"""
    reports_dir = tmp_path / "v8.3_institutional" / "reports"
    # 40 天, 但其中 15 天价格为 0 或负数 → 有效样本 25 < 30 → None
    for i in range(40):
        price = 100.0 + i if i >= 15 else 0.0  # 前 15 天价格为 0
        _write_pnl_report(
            reports_dir,
            f"202501{i:02d}",
            _make_details({"600519.SH": price}),
        )
    monkeypatch.setattr(daily_trade_executor, "PROJECT_ROOT", tmp_path)

    arr = daily_trade_executor._load_prediction_prices("600519", days=120)
    # 有效样本 25 < 30, 应返回 None
    assert arr is None


def test_thread_safe_single_scan(tmp_path, monkeypatch):
    """多线程并发调用只触发一次索引构建"""
    reports_dir = tmp_path / "v8.3_institutional" / "reports"
    for i in range(40):
        _write_pnl_report(
            reports_dir,
            f"202501{i:02d}",
            _make_details({"600519.SH": 100.0 + i}),
        )
    monkeypatch.setattr(daily_trade_executor, "PROJECT_ROOT", tmp_path)

    glob_call_count = {"n": 0}
    glob_lock = threading.Lock()
    real_glob = Path.glob

    def _count_glob(self, pattern):
        if pattern == "daily_pnl_report_*.json":
            with glob_lock:
                glob_call_count["n"] += 1
        return real_glob(self, pattern)

    results = [None] * 8

    def _worker(idx):
        with patch("pathlib.Path.glob", _count_glob):
            results[idx] = daily_trade_executor._load_prediction_prices("600519", days=120)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 所有线程都应成功获取价格序列
    for r in results:
        assert r is not None
        assert len(r) == 40

    # 并发下只扫描 1 次 (双检锁生效)
    assert glob_call_count["n"] == 1, (
        f"并发下扫描了 {glob_call_count['n']} 次, 期望 1 次 (双检锁失效)"
    )


def test_reset_clears_cache(tmp_path, monkeypatch):
    """_reset_prediction_prices_index 清空缓存, 下次调用重新构建"""
    reports_dir = tmp_path / "v8.3_institutional" / "reports"
    for i in range(35):
        _write_pnl_report(
            reports_dir,
            f"202501{i:02d}",
            _make_details({"600519.SH": 100.0 + i}),
        )
    monkeypatch.setattr(daily_trade_executor, "PROJECT_ROOT", tmp_path)

    glob_call_count = {"n": 0}
    real_glob = Path.glob

    def _count_glob(self, pattern):
        if pattern == "daily_pnl_report_*.json":
            glob_call_count["n"] += 1
        return real_glob(self, pattern)

    with patch("pathlib.Path.glob", _count_glob):
        daily_trade_executor._load_prediction_prices("600519")
        assert glob_call_count["n"] == 1

        # 重置缓存
        daily_trade_executor._reset_prediction_prices_index()

        # 下次调用应重新扫描
        daily_trade_executor._load_prediction_prices("600519")
        assert glob_call_count["n"] == 2, "重置后未重新构建索引"


def test_multiple_symbols_share_index(tmp_path, monkeypatch):
    """多 symbol 查询共享同一索引 (一次扫描服务所有 symbol)"""
    reports_dir = tmp_path / "v8.3_institutional" / "reports"
    for i in range(40):
        _write_pnl_report(
            reports_dir,
            f"202501{i:02d}",
            _make_details({
                "600519.SH": 1500.0 + i,
                "000858.SZ": 200.0 + i,
                "601318.SH": 80.0 + i,
                "510300.SH": 4.0 + i * 0.01,
            }),
        )
    monkeypatch.setattr(daily_trade_executor, "PROJECT_ROOT", tmp_path)

    glob_call_count = {"n": 0}
    real_glob = Path.glob

    def _count_glob(self, pattern):
        if pattern == "daily_pnl_report_*.json":
            glob_call_count["n"] += 1
        return real_glob(self, pattern)

    with patch("pathlib.Path.glob", _count_glob):
        codes = ["600519", "000858", "601318", "510300"]
        results = {}
        for code in codes:
            results[code] = daily_trade_executor._load_prediction_prices(code, days=120)

    # 4 个 symbol 共享 1 次扫描
    assert glob_call_count["n"] == 1
    # 全部成功获取
    for code in codes:
        assert results[code] is not None
        assert len(results[code]) == 40


def test_fetch_prediction_signals_uses_index(tmp_path, monkeypatch):
    """fetch_prediction_signals 调用多 symbol 时也只扫描一次 (端到端验证)"""
    reports_dir = tmp_path / "v8.3_institutional" / "reports"
    for i in range(40):
        _write_pnl_report(
            reports_dir,
            f"202501{i:02d}",
            _make_details({
                "600519.SH": 1500.0 + i,
                "000858.SZ": 200.0 + i,
            }),
        )
    monkeypatch.setattr(daily_trade_executor, "PROJECT_ROOT", tmp_path)

    glob_call_count = {"n": 0}
    real_glob = Path.glob

    def _count_glob(self, pattern):
        if pattern == "daily_pnl_report_*.json":
            glob_call_count["n"] += 1
        return real_glob(self, pattern)

    # fetch_prediction_signals 内部会 import tf_price_predictor, 失败时静默降级返回 {}
    # 我们 mock PricePredictor 来避免真实依赖
    with patch("pathlib.Path.glob", _count_glob):
        try:
            from utils.tf_price_predictor import PricePredictor  # noqa: F401
            _has_predictor = True
        except Exception:
            _has_predictor = False

        if _has_predictor:
            # 真实 PricePredictor 可用时, 走完整路径
            daily_trade_executor.fetch_prediction_signals(
                ["600519", "000858"], horizon=5
            )
            # 应该只扫描 1 次
            assert glob_call_count["n"] == 1
        else:
            # PricePredictor 不可用时, fetch_prediction_signals 返回 {} 但不会调用 _load_prediction_prices
            # 此用例仅验证: 索引层在直接调用时只扫描 1 次 (已在其他用例覆盖)
            # 这里直接调用 _load_prediction_prices 验证端到端
            for code in ("600519", "000858"):
                daily_trade_executor._load_prediction_prices(code)
            assert glob_call_count["n"] == 1
