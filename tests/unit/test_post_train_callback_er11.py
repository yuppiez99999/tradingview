"""ER-1.1: post_train_callback 钩子测试 (Wave 7-ERL Sprint 1)

验证目标:
    1. invoke_post_train_callback: callback=None 时直接返回（向后兼容）
    2. invoke_post_train_callback: callback 正常执行时调用成功
    3. invoke_post_train_callback: callback 抛异常时 fail-safe 降级不阻断
    4. invoke_post_train_callback: 传入训练结果字典正确
    5. run_enhanced_training: post_train_callback 参数向后兼容 (None 时行为不变)
    6. run_lgb_tscv_training: post_train_callback 参数向后兼容 (None 时行为不变)
    7. run_enhanced_training: 训练完成后调用 callback
    8. run_lgb_tscv_training: 训练完成后调用 callback
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from autolearn_trainer import invoke_post_train_callback  # noqa: E402

# ============================================================
# invoke_post_train_callback 单元测试
# ============================================================


class TestInvokePostTrainCallback:
    """invoke_post_train_callback 辅助函数测试."""

    def test_none_callback_returns_immediately(self):
        """callback=None 时直接返回，不抛异常（向后兼容）."""
        result = {"status": "OK", "trained": 5}
        invoke_post_train_callback(None, result)

    def test_callback_executed_with_result(self):
        """callback 正常执行时接收训练结果字典."""
        received = []

        def callback(r):
            received.append(r)

        result = {"status": "OK", "trained": 3, "failed": 0}
        invoke_post_train_callback(callback, result)

        assert len(received) == 1
        assert received[0] is result

    def test_callback_exception_fail_safe(self):
        """callback 抛异常时 fail-safe 降级，不阻断主流程."""

        def callback(r):
            raise RuntimeError("模拟回调失败")

        result = {"status": "OK", "trained": 1}
        invoke_post_train_callback(callback, result)

    def test_callback_exception_does_not_propagate(self):
        """callback 抛不同类型异常均不传播."""
        exceptions = [
            ValueError("值错误"),
            KeyError("键错误"),
            TypeError("类型错误"),
            OSError("系统错误"),
            ImportError("导入错误"),
        ]
        result = {"status": "OK"}
        for exc_factory in exceptions:

            def callback(r, e=exc_factory):
                raise e

            invoke_post_train_callback(callback, result)

    def test_custom_logger_name(self):
        """自定义 logger_name 不影响功能."""

        def callback(r):
            r["callback_called"] = True

        result = {"status": "OK"}
        invoke_post_train_callback(callback, result, logger_name="custom_logger")
        assert result.get("callback_called") is True

    def test_result_dict_passed_correctly(self):
        """训练结果字典完整传入 callback."""
        captured = {}

        def callback(r):
            captured.update(r)

        result = {
            "status": "OK",
            "total": 10,
            "trained": 8,
            "skipped": 1,
            "failed": 1,
            "results": {"600519": {"signal": 0.5}},
            "signals": {"signals": {}},
        }
        invoke_post_train_callback(callback, result)

        assert captured["status"] == "OK"
        assert captured["total"] == 10
        assert captured["trained"] == 8
        assert captured["skipped"] == 1
        assert captured["failed"] == 1
        assert "600519" in captured["results"]


# ============================================================
# run_enhanced_training post_train_callback 参数测试
# ============================================================


class TestRunEnhancedTrainingCallback:
    """run_enhanced_training 的 post_train_callback 参数测试."""

    def test_signature_accepts_post_train_callback(self):
        """run_enhanced_training 签名接受 post_train_callback 参数."""
        import inspect

        from lgb_trainer.trainer import run_enhanced_training

        sig = inspect.signature(run_enhanced_training)
        assert "post_train_callback" in sig.parameters
        assert sig.parameters["post_train_callback"].default is None

    def test_none_callback_backward_compatible(self):
        """post_train_callback=None 时行为不变（向后兼容）— mock 训练流程."""
        from lgb_trainer.trainer import run_enhanced_training

        with (
            patch("lgb_trainer.trainer._build_all_features") as mock_build,
            patch("lgb_trainer.trainer._train_single_symbol") as mock_train,
            patch("lgb_trainer.trainer._generate_integrated_signals") as mock_signals,
        ):
            mock_build.return_value = {}
            mock_train.return_value = ("SKIP", {"status": "SKIP", "symbol": "test"})
            mock_signals.return_value = {"signals": {}}

            with patch("autolearn_trainer.POSITION_SYMBOLS", []):
                result = run_enhanced_training(symbols=[], post_train_callback=None)

            assert result["status"] == "OK"

    def test_callback_invoked_after_training(self):
        """训练完成后调用 callback，传入训练结果."""
        from lgb_trainer.trainer import run_enhanced_training

        received = []

        def callback(r):
            received.append(r)

        with (
            patch("lgb_trainer.trainer._build_all_features") as mock_build,
            patch("lgb_trainer.trainer._train_single_symbol") as mock_train,
            patch("lgb_trainer.trainer._generate_integrated_signals") as mock_signals,
        ):
            mock_build.return_value = {}
            mock_train.return_value = ("SKIP", {"status": "SKIP", "symbol": "test"})
            mock_signals.return_value = {"signals": {}}

            with patch("autolearn_trainer.POSITION_SYMBOLS", []):
                run_enhanced_training(symbols=[], post_train_callback=callback)

            assert len(received) == 1
            assert received[0]["status"] == "OK"

    def test_callback_exception_fail_safe(self):
        """callback 异常时不阻断训练，训练结果正常返回."""
        from lgb_trainer.trainer import run_enhanced_training

        def callback(r):
            raise RuntimeError("模拟进化编排失败")

        with (
            patch("lgb_trainer.trainer._build_all_features") as mock_build,
            patch("lgb_trainer.trainer._train_single_symbol") as mock_train,
            patch("lgb_trainer.trainer._generate_integrated_signals") as mock_signals,
        ):
            mock_build.return_value = {}
            mock_train.return_value = ("SKIP", {"status": "SKIP", "symbol": "test"})
            mock_signals.return_value = {"signals": {}}

            with patch("autolearn_trainer.POSITION_SYMBOLS", []):
                result = run_enhanced_training(symbols=[], post_train_callback=callback)

            assert result["status"] == "OK"


# ============================================================
# run_lgb_tscv_training post_train_callback 参数测试
# ============================================================


class TestRunLgbTscvTrainingCallback:
    """run_lgb_tscv_training 的 post_train_callback 参数测试."""

    def test_signature_accepts_post_train_callback(self):
        """run_lgb_tscv_training 签名接受 post_train_callback 参数."""
        import inspect

        from lgb_tscv_trainer import run_lgb_tscv_training

        sig = inspect.signature(run_lgb_tscv_training)
        assert "post_train_callback" in sig.parameters
        assert sig.parameters["post_train_callback"].default is None

    def test_none_callback_backward_compatible(self):
        """post_train_callback=None 时行为不变（向后兼容）— mock 训练流程."""
        from lgb_tscv_trainer import run_lgb_tscv_training

        with (
            patch("lgb_tscv_trainer.load_ohlcv_history") as mock_ohlcv,
            patch("lgb_tscv_trainer.add_technical_features") as mock_feat,
            patch("lgb_tscv_trainer.add_cross_sectional_features") as mock_cross,
            patch("lgb_tscv_trainer.should_retrain", return_value=False),
            patch("lgb_tscv_trainer.load_model_meta", return_value={"signal": 0}),
        ):
            import pandas as pd

            # P0-M4: 真实 K 线优先 — mock ≥2 个标的通过可用性阈值
            mock_ohlcv.return_value = {
                "600519": pd.DataFrame({"close": [1.0, 2.0]}),
                "600000": pd.DataFrame({"close": [1.0, 2.0]}),
            }
            mock_feat.side_effect = lambda df: df
            mock_cross.side_effect = lambda d: d

            with patch("autolearn_trainer.POSITION_SYMBOLS", []):
                result = run_lgb_tscv_training(symbols=[], post_train_callback=None)

            assert result["status"] == "OK"

    def test_callback_invoked_after_training(self):
        """训练完成后调用 callback，传入训练结果."""
        from lgb_tscv_trainer import run_lgb_tscv_training

        received = []

        def callback(r):
            received.append(r)

        with (
            patch("lgb_tscv_trainer.load_ohlcv_history") as mock_ohlcv,
            patch("lgb_tscv_trainer.add_technical_features") as mock_feat,
            patch("lgb_tscv_trainer.add_cross_sectional_features") as mock_cross,
            patch("lgb_tscv_trainer.should_retrain", return_value=False),
            patch("lgb_tscv_trainer.load_model_meta", return_value={"signal": 0}),
        ):
            import pandas as pd

            mock_ohlcv.return_value = {
                "600519": pd.DataFrame({"close": [1.0, 2.0]}),
                "600000": pd.DataFrame({"close": [1.0, 2.0]}),
            }
            mock_feat.side_effect = lambda df: df
            mock_cross.side_effect = lambda d: d

            with patch("autolearn_trainer.POSITION_SYMBOLS", []):
                run_lgb_tscv_training(symbols=[], post_train_callback=callback)

            assert len(received) == 1
            assert received[0]["status"] == "OK"

    def test_callback_exception_fail_safe(self):
        """callback 异常时不阻断训练，训练结果正常返回."""
        from lgb_tscv_trainer import run_lgb_tscv_training

        def callback(r):
            raise RuntimeError("模拟再平衡失败")

        with (
            patch("lgb_tscv_trainer.load_ohlcv_history") as mock_ohlcv,
            patch("lgb_tscv_trainer.add_technical_features") as mock_feat,
            patch("lgb_tscv_trainer.add_cross_sectional_features") as mock_cross,
            patch("lgb_tscv_trainer.should_retrain", return_value=False),
            patch("lgb_tscv_trainer.load_model_meta", return_value={"signal": 0}),
        ):
            import pandas as pd

            mock_ohlcv.return_value = {
                "600519": pd.DataFrame({"close": [1.0, 2.0]}),
                "600000": pd.DataFrame({"close": [1.0, 2.0]}),
            }
            mock_feat.side_effect = lambda df: df
            mock_cross.side_effect = lambda d: d

            with patch("autolearn_trainer.POSITION_SYMBOLS", []):
                result = run_lgb_tscv_training(symbols=[], post_train_callback=callback)

            assert result["status"] == "OK"
