"""KronosPredictor 单元测试.

测试目标:
    1. Flag 透传 (HC-1): USE_KRONOS_PREDICTOR=False 时返回空, 不加载模型
    2. 失败安全: 模型不可用时返回空字典, 不抛异常
    3. 因子提取: 模型可用时返回正确的因子字典
    4. 批量预测: predict_batch 正确处理多标的
    5. 审计: 预测结果写入 JSONL
    6. 元数据: get_model_id / get_metadata / get_health 正确

注意: 单元测试 mock 模型加载, 不下载真实模型 (集成测试才用真实模型).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from utils.alpha.kronos_predictor import (
    KronosPredictConfig,
    KronosPredictor,
)


# ============================================================
# 测试数据: 模拟历史 K 线 (60 天)
# ============================================================
def _make_hist_df(days: int = 60) -> pd.DataFrame:
    """生成模拟历史 K 线数据."""
    dates = pd.date_range("2026-01-01", periods=days, freq="D")
    close = [100.0 + i * 0.5 for i in range(days)]
    return pd.DataFrame(
        {
            "open": [c - 0.3 for c in close],
            "high": [c + 0.5 for c in close],
            "low": [c - 0.5 for c in close],
            "close": close,
            "volume": [1_000_000 + i * 1000 for i in range(days)],
            "amount": [c * 1_000_000 for c in close],
        },
        index=dates,
    )


def _make_pred_df(pred_len: int = 20) -> pd.DataFrame:
    """生成模拟 Kronos 预测结果 (预测价格上涨, 高于历史末值 129.5)."""
    close = [135.0 + i * 0.3 for i in range(pred_len)]
    return pd.DataFrame(
        {
            "open": [c - 0.2 for c in close],
            "high": [c + 0.4 for c in close],
            "low": [c - 0.4 for c in close],
            "close": close,
            "volume": [1_200_000 + i * 500 for i in range(pred_len)],
        }
    )


# ============================================================
# Fixture
# ============================================================
@pytest.fixture(autouse=True)
def _reset_singleton():
    """每个测试前重置单例, 避免状态污染."""
    KronosPredictor.reset_instance()
    yield
    KronosPredictor.reset_instance()


# ============================================================
# 测试 1: Flag 关闭时不加载模型
# ============================================================
def test_flag_disabled_returns_empty() -> None:
    """USE_KRONOS_PREDICTOR=False 时 predict 返回空字典, 不触发模型加载."""
    with patch("utils.alpha.kronos_predictor.is_enabled", return_value=False):
        predictor = KronosPredictor()
        result = predictor.predict(_make_hist_df(), symbol="600519.SH")
        assert result == {}
        assert predictor.available is False
        # 模型加载不应被触发
        assert predictor._model_wrapper is None


# ============================================================
# 测试 2: Flag 启用但模型不可用时降级
# ============================================================
def test_model_unavailable_degrades_gracefully() -> None:
    """Flag=True 但模型加载失败时返回空字典, 不抛异常."""
    with patch("utils.alpha.kronos_predictor.is_enabled", return_value=True):
        predictor = KronosPredictor()
        # mock 模型加载失败
        with patch.object(KronosPredictor, "_load_kronos_model", return_value=False):
            result = predictor.predict(_make_hist_df(), symbol="600519.SH")
            assert result == {}
            assert predictor.available is False
            assert predictor.error_msg is not None


# ============================================================
# 测试 3: 模型可用时返回正确因子
# ============================================================
def test_predict_returns_factors_when_available() -> None:
    """Flag=True 且模型可用时返回包含 KRONOS_* 因子的字典."""
    with patch("utils.alpha.kronos_predictor.is_enabled", return_value=True):
        predictor = KronosPredictor()
        # mock 模型加载成功
        with patch.object(KronosPredictor, "_load_kronos_model", return_value=True):
            # mock 模型推理
            mock_wrapper = type(
                "MockWrapper",
                (),
                {"predict": lambda self, **kwargs: _make_pred_df()},
            )()
            predictor._model_wrapper = mock_wrapper
            predictor._available = True

            result = predictor.predict(_make_hist_df(), symbol="600519.SH")

            # 验证因子键存在
            assert "KRONOS_RET_5D" in result
            assert "KRONOS_RET_20D" in result
            assert "KRONOS_DIR" in result
            assert "KRONOS_CONF" in result
            assert "KRONOS_MOM" in result
            assert "KRONOS_PREMIUM" in result
            # 方向应为正 (预测价格上涨)
            assert result["KRONOS_DIR"] == 1.0
            assert result["KRONOS_RET_5D"] > 0


# ============================================================
# 测试 4: 数据不足时返回空
# ============================================================
def test_insufficient_data_returns_empty() -> None:
    """历史数据 < 30 天时返回空字典."""
    with patch("utils.alpha.kronos_predictor.is_enabled", return_value=True):
        predictor = KronosPredictor()
        with patch.object(KronosPredictor, "_load_kronos_model", return_value=True):
            predictor._available = True
            short_df = _make_hist_df(days=20)
            result = predictor.predict(short_df, symbol="000001.SZ")
            assert result == {}


# ============================================================
# 测试 5: 缺少必要列时返回空
# ============================================================
def test_missing_columns_returns_empty() -> None:
    """数据缺少 OHLCV 列时返回空字典."""
    with patch("utils.alpha.kronos_predictor.is_enabled", return_value=True):
        predictor = KronosPredictor()
        with patch.object(KronosPredictor, "_load_kronos_model", return_value=True):
            predictor._available = True
            bad_df = pd.DataFrame({"close": [100, 101, 102]})
            result = predictor.predict(bad_df, symbol="600519.SH")
            assert result == {}


# ============================================================
# 测试 6: 批量预测
# ============================================================
def test_predict_batch() -> None:
    """predict_batch 正确处理多标的."""
    with patch("utils.alpha.kronos_predictor.is_enabled", return_value=True):
        predictor = KronosPredictor()
        with patch.object(KronosPredictor, "_load_kronos_model", return_value=True):
            mock_wrapper = type(
                "MockWrapper",
                (),
                {"predict": lambda self, **kwargs: _make_pred_df()},
            )()
            predictor._model_wrapper = mock_wrapper
            predictor._available = True

            symbols = ["600519.SH", "000001.SZ", "510300.SH"]
            df_map = {s: _make_hist_df() for s in symbols}
            df_map["000001.SZ"] = df_map["000001.SZ"].drop(columns=["open"])

            results = predictor.predict_batch(symbols, df_map)
            assert len(results) == 3
            assert "KRONOS_RET_5D" in results["600519.SH"]
            assert results["000001.SZ"] == {}  # 缺少 open 列
            assert "KRONOS_RET_5D" in results["510300.SH"]


# ============================================================
# 测试 7: 审计日志写入
# ============================================================
def test_audit_log_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """预测结果写入 JSONL 审计日志."""
    monkeypatch.setattr("utils.alpha.kronos_predictor._PREDICTIONS_DIR", tmp_path)
    with patch("utils.alpha.kronos_predictor.is_enabled", return_value=True):
        predictor = KronosPredictor(KronosPredictConfig(audit_enabled=True))
        with patch.object(KronosPredictor, "_load_kronos_model", return_value=True):
            mock_wrapper = type(
                "MockWrapper",
                (),
                {"predict": lambda self, **kwargs: _make_pred_df()},
            )()
            predictor._model_wrapper = mock_wrapper
            predictor._available = True

            predictor.predict(_make_hist_df(), symbol="600519.SH")

            audit_files = list(tmp_path.glob("600519_SH_*.jsonl"))
            assert len(audit_files) == 1
            line = audit_files[0].read_text(encoding="utf-8").strip()
            record = json.loads(line)
            assert record["symbol"] == "600519.SH"
            assert record["model_id"] == "kronos-small"
            assert "KRONOS_RET_5D" in record["factors"]
            assert "latency_ms" in record


# ============================================================
# 测试 8: 元数据与健康状态
# ============================================================
def test_metadata_and_health() -> None:
    """get_model_id / get_metadata / get_health 返回正确结构."""
    predictor = KronosPredictor(KronosPredictConfig(model_size="base"))
    assert predictor.get_model_id() == "kronos-base"

    meta = predictor.get_metadata()
    assert meta["model_id"] == "kronos-base"
    assert meta["model_size"] == "base"
    assert meta["flag_name"] == "USE_KRONOS_PREDICTOR"
    assert meta["params_m"] == 48.0

    health = predictor.get_health()
    assert health["predict_count"] == 0
    assert health["error_count"] == 0
    assert health["error_rate"] == 0.0


# ============================================================
# 测试 9: 推理异常时降级 (fail-safe)
# ============================================================
def test_inference_exception_returns_empty() -> None:
    """模型推理抛异常时返回空字典, 不阻断主流程."""
    with patch("utils.alpha.kronos_predictor.is_enabled", return_value=True):
        predictor = KronosPredictor()
        with patch.object(KronosPredictor, "_load_kronos_model", return_value=True):
            mock_wrapper = type(
                "MockWrapper",
                (),
                {
                    "predict": lambda self, **kwargs: (_ for _ in ()).throw(
                        RuntimeError("GPU OOM")
                    )
                },
            )()
            predictor._model_wrapper = mock_wrapper
            predictor._available = True

            result = predictor.predict(_make_hist_df(), symbol="600519.SH")
            assert result == {}
            assert predictor._error_count == 1
