"""live_scheduler Kronos 集成单元测试.

测试目标:
    1. Flag 透传 (HC-1): USE_KRONOS_PREDICTOR=False 时 _enrich_with_kronos 返回 disabled
    2. 失败安全: 模块导入失败/模型不可用时返回 error, 不影响 predictions
    3. 丰富预测: flag 启用且模型可用时, predictions 被注入 kronos_ 前缀因子
    4. 端到端: run_ml_signal_scan 集成 Kronos 后不破坏原 PricePredictor 流程
    5. 不可变性: Kronos 异常不修改已有 predictions 字段

设计:
    - mock PricePredictor (避免真实模型加载)
    - mock KronosPredictor 单例 (避免真实模型下载)
    - mock feature_flags.is_enabled 控制 flag
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from live_scheduler import _enrich_with_kronos, run_ml_signal_scan


# ============================================================
# 1. Flag 透传 (HC-1)
# ============================================================
class TestFlagGate:
    """USE_KRONOS_PREDICTOR=False 时必须降级."""

    def test_flag_disabled_returns_disabled_status(self) -> None:
        predictions = {"510300.SH": {"predicted_return": 0.05, "confidence": 0.8}}
        with patch("utils.infra.feature_flags.is_enabled", return_value=False):
            meta = _enrich_with_kronos(predictions, ["510300.SH"])
        assert meta["status"] == "disabled"
        assert meta["enriched_count"] == 0
        # predictions 不应被修改
        assert predictions["510300.SH"] == {"predicted_return": 0.05, "confidence": 0.8}

    def test_flag_disabled_no_kronos_import(self) -> None:
        """flag 关闭时不应导入 KronosPredictor."""
        predictions = {}
        with patch("utils.infra.feature_flags.is_enabled", return_value=False), patch(
            "utils.alpha.kronos_predictor.KronosPredictor"
        ) as mock_kronos:
            _enrich_with_kronos(predictions, [])
            mock_kronos.get_instance.assert_not_called()


# ============================================================
# 2. 失败安全
# ============================================================
class TestFailSafe:
    """Kronos 异常必须降级为 error 状态, 不影响 predictions."""

    def test_import_error_returns_error_status(self) -> None:
        predictions = {"510300.SH": {"predicted_return": 0.05}}
        with patch("utils.infra.feature_flags.is_enabled", return_value=True), patch(
            "builtins.__import__",
            side_effect=ImportError("No module named 'utils.alpha.kronos_predictor'"),
        ):
            # 由于 _enrich_with_kronos 内部用 from ... import, 需要模拟导入失败
            # 这里用 patch 整个模块导入路径
            meta = _enrich_with_kronos(predictions, ["510300.SH"])
        # 应返回 error 或 disabled (取决于哪个 import 失败)
        assert meta["status"] in ("error", "disabled")
        # predictions 不变
        assert predictions["510300.SH"]["predicted_return"] == 0.05

    def test_model_unavailable_returns_error(self) -> None:
        predictions = {"510300.SH": {"predicted_return": 0.05}}
        mock_predictor = MagicMock()
        mock_predictor.available = False
        mock_predictor.init_error = "model file not found"

        with patch("utils.infra.feature_flags.is_enabled", return_value=True), patch(
            "utils.alpha.kronos_predictor.KronosPredictor.get_instance",
            return_value=mock_predictor,
        ):
            meta = _enrich_with_kronos(predictions, ["510300.SH"])
        assert meta["status"] == "error"
        assert "model file not found" in meta["error"]
        # predictions 不变
        assert predictions["510300.SH"] == {"predicted_return": 0.05}

    def test_predict_exception_does_not_block_others(self) -> None:
        """单个标的预测失败不阻断其他标的."""
        predictions = {
            "510300.SH": {"predicted_return": 0.05},
            "510050.SH": {"predicted_return": 0.03},
        }
        mock_predictor = MagicMock()
        mock_predictor.available = True
        # 第一个标的抛异常, 第二个返回正常
        mock_predictor.predict_batch.side_effect = [
            RuntimeError("timeout"),
            {"510050.SH": {"RET_5D": 0.02, "DIR": 1.0}},
        ]

        with patch("utils.infra.feature_flags.is_enabled", return_value=True), patch(
            "utils.alpha.kronos_predictor.KronosPredictor.get_instance",
            return_value=mock_predictor,
        ):
            meta = _enrich_with_kronos(predictions, ["510300.SH", "510050.SH"])
        # 第二个标的应被丰富
        assert meta["enriched_count"] == 1
        assert "kronos_RET_5D" in predictions["510050.SH"]


# ============================================================
# 3. 丰富预测
# ============================================================
class TestEnrichment:
    """flag 启用且模型可用时正确注入因子."""

    def test_factors_added_with_kronos_prefix(self) -> None:
        predictions = {"510300.SH": {"predicted_return": 0.05, "confidence": 0.8}}
        mock_predictor = MagicMock()
        mock_predictor.available = True
        mock_predictor.predict_batch.return_value = {
            "510300.SH": {"RET_5D": 0.02, "DIR": 1.0, "VOL": 0.15}
        }

        with patch("utils.infra.feature_flags.is_enabled", return_value=True), patch(
            "utils.alpha.kronos_predictor.KronosPredictor.get_instance",
            return_value=mock_predictor,
        ):
            meta = _enrich_with_kronos(predictions, ["510300.SH"])

        assert meta["status"] == "enabled"
        assert meta["enriched_count"] == 1
        # 因子应以 kronos_ 前缀添加
        assert "kronos_RET_5D" in predictions["510300.SH"]
        assert "kronos_DIR" in predictions["510300.SH"]
        assert "kronos_VOL" in predictions["510300.SH"]
        # 原有字段不丢失
        assert predictions["510300.SH"]["predicted_return"] == 0.05
        assert predictions["510300.SH"]["confidence"] == 0.8
        # factors_added 列表
        assert "kronos_RET_5D" in meta["factors_added"]

    def test_no_overwrite_existing_fields(self) -> None:
        """Kronos 因子不应覆盖已有字段."""
        predictions = {"510300.SH": {"predicted_return": 0.05}}
        mock_predictor = MagicMock()
        mock_predictor.available = True
        mock_predictor.predict_batch.return_value = {
            "510300.SH": {"predicted_return": 0.99}  # 同名键
        }

        with patch("utils.infra.feature_flags.is_enabled", return_value=True), patch(
            "utils.alpha.kronos_predictor.KronosPredictor.get_instance",
            return_value=mock_predictor,
        ):
            _enrich_with_kronos(predictions, ["510300.SH"])
        # 原值不被覆盖 (kronos_ 前缀)
        assert predictions["510300.SH"]["predicted_return"] == 0.05
        assert predictions["510300.SH"]["kronos_predicted_return"] == 0.99

    def test_empty_kronos_result_skipped(self) -> None:
        """Kronos 返回空字典时该标的不计入 enriched_count."""
        predictions = {"510300.SH": {"predicted_return": 0.05}}
        mock_predictor = MagicMock()
        mock_predictor.available = True
        mock_predictor.predict_batch.return_value = {"510300.SH": {}}

        with patch("utils.infra.feature_flags.is_enabled", return_value=True), patch(
            "utils.alpha.kronos_predictor.KronosPredictor.get_instance",
            return_value=mock_predictor,
        ):
            meta = _enrich_with_kronos(predictions, ["510300.SH"])
        assert meta["enriched_count"] == 0

    def test_multiple_codes_enriched(self) -> None:
        predictions = {
            "510300.SH": {"predicted_return": 0.05},
            "510050.SH": {"predicted_return": 0.03},
        }
        mock_predictor = MagicMock()
        mock_predictor.available = True
        mock_predictor.predict_batch.return_value = {
            "510300.SH": {"RET_5D": 0.02},
            "510050.SH": {"RET_5D": 0.01},
        }

        with patch("utils.infra.feature_flags.is_enabled", return_value=True), patch(
            "utils.alpha.kronos_predictor.KronosPredictor.get_instance",
            return_value=mock_predictor,
        ):
            meta = _enrich_with_kronos(predictions, ["510300.SH", "510050.SH"])
        assert meta["enriched_count"] == 2
        assert "kronos_RET_5D" in predictions["510300.SH"]
        assert "kronos_RET_5D" in predictions["510050.SH"]


# ============================================================
# 4. 端到端 (run_ml_signal_scan)
# ============================================================
class TestRunMlSignalScanIntegration:
    """run_ml_signal_scan 集成 Kronos 后不破坏原流程."""

    def test_kronos_disabled_runs_successfully(self) -> None:
        """flag 关闭时 run_ml_signal_scan 正常运行, kronos_integration.status=disabled."""
        mock_predictor = MagicMock()
        mock_predictor.predict.return_value = {"predicted_return": 0.05, "confidence": 0.8}

        with patch("utils.tf_price_predictor.PricePredictor", return_value=mock_predictor), patch(
            "utils.infra.feature_flags.is_enabled", return_value=False
        ):
            result = run_ml_signal_scan(dry_run=True)

        assert result["status"] == "OK"
        assert result["data"]["kronos_integration"]["status"] == "disabled"
        # 原预测结果存在
        assert result["data"]["n_predictions"] > 0

    def test_kronos_enabled_enriches_predictions(self) -> None:
        """flag 启用时 run_ml_signal_scan 的 predictions 包含 kronos_ 因子."""
        mock_predictor = MagicMock()
        mock_predictor.predict.return_value = {"predicted_return": 0.05, "confidence": 0.8}

        mock_kronos = MagicMock()
        mock_kronos.available = True
        mock_kronos.predict_batch.return_value = {
            "510300.SH": {"RET_5D": 0.02, "DIR": 1.0}
        }

        with patch("utils.tf_price_predictor.PricePredictor", return_value=mock_predictor), patch(
            "utils.infra.feature_flags.is_enabled", return_value=True
        ), patch(
            "utils.alpha.kronos_predictor.KronosPredictor.get_instance",
            return_value=mock_kronos,
        ):
            result = run_ml_signal_scan(dry_run=True)

        assert result["status"] == "OK"
        kronos_meta = result["data"]["kronos_integration"]
        assert kronos_meta["status"] == "enabled"
        # 至少有一个标的被丰富
        assert kronos_meta["enriched_count"] >= 1
        # 检查 predictions 中是否包含 kronos_ 因子
        predictions = result["data"]["predictions"]
        has_kronos_factor = any(
            k.startswith("kronos_") for pred in predictions.values() for k in pred
        )
        assert has_kronos_factor

    def test_kronos_error_does_not_block_scan(self) -> None:
        """Kronos 异常时 run_ml_signal_scan 仍返回 OK."""
        mock_predictor = MagicMock()
        mock_predictor.predict.return_value = {"predicted_return": 0.05, "confidence": 0.8}

        with patch("utils.tf_price_predictor.PricePredictor", return_value=mock_predictor), patch(
            "utils.infra.feature_flags.is_enabled", return_value=True
        ), patch(
            "utils.alpha.kronos_predictor.KronosPredictor.get_instance",
            side_effect=RuntimeError("model load failed"),
        ):
            result = run_ml_signal_scan(dry_run=True)

        # 主流程仍成功
        assert result["status"] == "OK"
        assert result["data"]["n_predictions"] > 0
        # Kronos 集成标记为 error
        assert result["data"]["kronos_integration"]["status"] == "error"
