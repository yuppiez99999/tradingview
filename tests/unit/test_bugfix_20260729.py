"""
2026-07-29 三维扫描修复回归测试

覆盖:
- P1: risk_metrics alpha/tracking_error/information_ratio NaN 清洗与长度对齐
- P1-T1: external_data_source._parse_api_float 安全解析 (FRED "." 等)
- P1-02: external_data_source 缓存原子写 (无句柄泄漏/半截文件)
- P2: data_pipeline 前向填充异构记录 KeyError / duplication_rate 除零
- P0-C1: utils.concurrency 原子写并发安全 + process_lock 防重入
- P0-C3: tdx_data_source 单例双重检查锁定
"""

import json
import os
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "v8.3_institutional"))


# ---------------------------------------------------------------------------
# P1: risk_metrics
# ---------------------------------------------------------------------------
class TestRiskMetricsNaNAndAlignment:
    def _mod(self):
        from utils import risk_metrics
        return risk_metrics

    def test_information_ratio_mismatched_length_not_silently_zero(self):
        rm = self._mod()
        rng = np.random.RandomState(42)
        returns = rng.normal(0.001, 0.01, 100) + 0.002  # 明显跑赢
        bench = rng.normal(0.0, 0.01, 80)  # 长度不等
        ir = rm.calculate_information_ratio(returns, bench)
        assert np.isfinite(ir)
        assert ir != 0.0  # 修复前长度不等 -> ValueError 被吞 -> 恒 0

    def test_alpha_with_nan_returns_finite(self):
        rm = self._mod()
        rng = np.random.RandomState(1)
        returns = rng.normal(0.001, 0.01, 100)
        market = rng.normal(0.0005, 0.01, 100)
        returns[10] = np.nan
        market[20] = np.nan
        alpha = rm.calculate_alpha(returns, market)
        assert np.isfinite(alpha)  # 修复前 NaN 静默传播

    def test_tracking_error_with_nan_finite(self):
        rm = self._mod()
        rng = np.random.RandomState(2)
        returns = rng.normal(0.001, 0.01, 60)
        bench = rng.normal(0.001, 0.01, 60)
        returns[5] = np.nan
        te = rm.calculate_tracking_error(returns, bench)
        assert np.isfinite(te)
        assert te >= 0

    def test_align_and_dropna_empty_input(self):
        rm = self._mod()
        a, b = rm._align_and_dropna(np.array([]), np.array([1.0]))
        assert len(a) == 0 and len(b) == 0

    def test_all_nan_returns_zero_not_nan(self):
        rm = self._mod()
        returns = np.full(50, np.nan)
        bench = np.full(50, np.nan)
        assert rm.calculate_information_ratio(returns, bench) == 0.0
        assert rm.calculate_tracking_error(returns, bench) == 0.0
        assert rm.calculate_alpha(returns, bench) == 0.0


# ---------------------------------------------------------------------------
# P1-T1 / P1-02: external_data_source
# ---------------------------------------------------------------------------
class TestExternalDataSourceParsing:
    def _mod(self):
        from utils import external_data_source
        return external_data_source

    @pytest.mark.parametrize("raw,expected", [
        ("1.23", 1.23),
        (4, 4.0),
        (".", None),        # FRED 缺失值哨兵
        ("N/A", None),
        ("", None),
        (None, None),
        (float("nan"), None),
    ])
    def test_parse_api_float(self, raw, expected):
        eds = self._mod()
        assert eds._parse_api_float(raw) == expected

    def test_save_cache_atomic_no_tmp_leftover(self, tmp_path, monkeypatch):
        eds = self._mod()
        mgr = eds.ExternalDataManager()
        # 重定向缓存路径到临时目录
        monkeypatch.setattr(
            mgr, "_cache_path",
            lambda category, key: tmp_path / f"{category}_{key}.json")
        mgr._save_cache("macro", "unit_test_key", {"v": 1})
        cache_file = tmp_path / "macro_unit_test_key.json"
        assert cache_file.exists()
        saved = json.loads(cache_file.read_text(encoding="utf-8"))
        assert saved["data"] == {"v": 1}
        # 不应留下 .tmp 文件
        assert list(tmp_path.rglob("*.tmp")) == []


# ---------------------------------------------------------------------------
# P2: data_pipeline
# ---------------------------------------------------------------------------
class TestDataPipelineCleaning:
    def _pipeline(self):
        import importlib.util
        mod_path = (PROJECT_ROOT / "v8.3_institutional" / "src" / "data"
                    / "data_pipeline.py")
        spec = importlib.util.spec_from_file_location("dp_under_test", mod_path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            pytest.skip(f"data_pipeline 依赖不可用: {e}")
        return mod

    def test_forward_fill_heterogeneous_records_no_keyerror(self):
        mod = self._pipeline()
        cls = getattr(mod, "DataCleaningLayer", None) or getattr(
            mod, "CleaningLayer", None)
        if cls is None:
            # 找含 _apply_cleaning_rules 的类
            for name in dir(mod):
                obj = getattr(mod, name)
                if isinstance(obj, type) and hasattr(obj, "_apply_cleaning_rules"):
                    cls = obj
                    break
        if cls is None:
            pytest.skip("清洗类未找到")
        try:
            layer = cls()
        except TypeError:
            pytest.skip("清洗类构造需要参数, 跳过实例化")
        # 第二条记录含首条没有的新字段 extra, 修复前 last_known[key] KeyError
        data = [
            {"symbol": "600519", "close": 100.0},
            {"symbol": "600519", "close": None, "extra": None},
        ]
        result = layer._apply_cleaning_rules(list(data))
        assert isinstance(result, list)
        # 前向填充生效
        assert result[1]["close"] == 100.0
        # 新字段无历史值 -> None 而非 KeyError
        assert result[1]["extra"] is None


# ---------------------------------------------------------------------------
# P0-C1: utils.concurrency
# ---------------------------------------------------------------------------
class TestConcurrencyAtomicWrite:
    def test_atomic_write_json_roundtrip(self, tmp_path):
        from utils.concurrency import atomic_write_json, read_json_locked
        target = tmp_path / "positions.json"
        atomic_write_json(target, {"a": 1, "b": [1, 2]})
        assert read_json_locked(target) == {"a": 1, "b": [1, 2]}
        assert list(tmp_path.glob("*.tmp")) == []

    def test_concurrent_writes_never_corrupt(self, tmp_path):
        """20 线程并发写同一文件, 任意时刻读到的都是完整 JSON。"""
        from utils.concurrency import atomic_write_json
        target = tmp_path / "state.json"
        errors = []

        def writer(i):
            try:
                for _ in range(20):
                    atomic_write_json(target, {"writer": i, "payload": "x" * 500})
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def reader():
            for _ in range(100):
                if target.exists():
                    try:
                        with open(target, encoding="utf-8") as f:
                            json.load(f)
                    except json.JSONDecodeError as e:
                        errors.append(e)

        threads = ([threading.Thread(target=writer, args=(i,)) for i in range(10)]
                   + [threading.Thread(target=reader) for _ in range(10)])
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert list(tmp_path.glob("*.tmp")) == []

    def test_read_json_locked_missing_returns_default(self, tmp_path):
        from utils.concurrency import read_json_locked
        assert read_json_locked(tmp_path / "nope.json", default={"d": 1}) == {"d": 1}

    def test_read_json_locked_corrupt_returns_default(self, tmp_path):
        from utils.concurrency import read_json_locked
        bad = tmp_path / "bad.json"
        bad.write_text("{half json", encoding="utf-8")
        assert read_json_locked(bad, default=None) is None

    def test_path_lock_identity(self, tmp_path):
        from utils.concurrency import get_path_lock
        p = tmp_path / "f.json"
        # 同一路径不同写法返回同一把锁
        assert get_path_lock(p) is get_path_lock(str(p))


class TestProcessLock:
    def test_exclusive(self, tmp_path):
        from utils.concurrency import process_lock
        with process_lock("ut_lock", lock_dir=tmp_path) as a1:
            assert a1 is True
            with process_lock("ut_lock", timeout=0.0, lock_dir=tmp_path) as a2:
                assert a2 is False  # 重入被拒绝
        # 释放后可再次获取
        with process_lock("ut_lock", lock_dir=tmp_path) as a3:
            assert a3 is True

    def test_stale_lock_cleanup(self, tmp_path):
        from utils.concurrency import process_lock
        stale = tmp_path / "quant84_ut_stale.lock"
        stale.write_text("999999\n0\n", encoding="utf-8")
        old = 1_000_000.0  # 1970 年代 -> 必然过期
        os.utime(stale, (old, old))
        with process_lock("ut_stale", stale_seconds=60.0, lock_dir=tmp_path) as ok:
            assert ok is True  # 过期锁被自动清理

    def test_lock_file_removed_after_exit(self, tmp_path):
        from utils.concurrency import process_lock
        with process_lock("ut_clean", lock_dir=tmp_path):
            assert (tmp_path / "quant84_ut_clean.lock").exists()
        assert not (tmp_path / "quant84_ut_clean.lock").exists()


# ---------------------------------------------------------------------------
# P0-C3: tdx 单例双重检查锁定
# ---------------------------------------------------------------------------
class TestTdxSingletonThreadSafe:
    def test_concurrent_get_returns_same_instance(self, monkeypatch):
        try:
            from utils import tdx_data_source as tdx
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"tdx_data_source 不可导入: {e}")

        # 用轻量假类避免真实网络连接
        class _Fake:
            pass

        monkeypatch.setattr(tdx, "TDXDataSource", _Fake)
        monkeypatch.setattr(tdx, "_tdx_instance", None)

        results = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            results.append(tdx.get_tdx_source())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 8
        assert all(r is results[0] for r in results)  # 单一实例


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
