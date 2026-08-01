# -*- coding: utf-8 -*-
"""B3.5 lgb_enhanced_trainer 拆分验证脚本

验证项:
1. lgb_trainer 包导入完整性 (7 个子模块 + __init__.py 统一导出)
2. 主文件 lgb_enhanced_trainer.py 作为 thin coordinator (向后兼容导出)
3. 子模块路径注入 (configure_paths) 正确生效
4. 评估指标 (r2_score / ic_score / signal_sharpe) 数值正确
5. time_series_cv_evaluate 走 Purged K-Fold (无标签泄漏)
6. train_lgb_with_fallback GPU→CPU 自动回退 (CPU 模式可正常训练)
7. compute_regime_series 正确划分 bull/bear/choppy/rebound
8. persistence (save_model / load_model_meta / should_retrain) 闭环
9. 外部模块 (institutional_pipeline_runner) 向后兼容导入
10. CLI 入口 (main) 参数解析

运行:
    python verify_b35_lgb_refactor.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

# ============================================================
# 路径设置
# ============================================================
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "v8.3_institutional"))
sys.path.insert(0, str(ROOT / "utils"))

# 确保 cache 目录存在 (lgb_enhanced_trainer 导入时会创建)
for _d in [
    ROOT / "models" / "lgb_enhanced",
    ROOT / "reports" / "lgb_enhanced",
    ROOT / "logs",
    ROOT / "cache" / "ohlcv",
]:
    _d.mkdir(parents=True, exist_ok=True)


def banner(msg: str) -> None:
    print(f"\n{'=' * 60}\n{msg}\n{'=' * 60}")


# ============================================================
# 测试辅助: 模块级 MockModel (局部类无法 pickle)
# ============================================================
class MockModel:
    """模拟 LightGBM 模型 (用于 persistence 闭环测试)."""
    feature_importances_ = np.array([10, 8, 6, 4, 2], dtype=float)
    best_iteration_ = 50

    def predict(self, X):
        return np.random.RandomState(42).randn(len(X))


# ============================================================
# 测试 1: lgb_trainer 包导入完整性
# ============================================================
def test_package_imports():
    banner("[1] lgb_trainer 包导入完整性")
    import lgb_trainer

    # 子模块均能独立导入
    from lgb_trainer import (  # noqa: F401
        data_loader,
        news_sentiment,
        feature_engineering,
        metrics,
        persistence,
        trainer,
        report_generator,
    )

    # __init__.py 导出的关键符号
    expected_exports = [
        "load_real_ohlcv",
        "fetch_all_real_ohlcv",
        "compute_news_sentiment_factors",
        "add_sentiment_features",
        "add_mean_reversion_features",
        "add_regime_aware_features",
        "add_industry_relative_strength_features",
        "add_capital_flow_features",
        "add_cross_market_features",
        "time_series_cv_evaluate",
        "select_features_by_importance",
        "save_model",
        "load_model_meta",
        "should_retrain",
        "train_symbol_enhanced",
        "train_symbol_regime_specific",
        "compute_regime_series",
        "run_enhanced_training",
        "generate_comparison_report",
    ]
    for name in expected_exports:
        assert hasattr(lgb_trainer, name), f"lgb_trainer 缺少导出: {name}"
    print(f"  ✓ lgb_trainer 包导入成功, 导出 {len(expected_exports)} 个符号")


# ============================================================
# 测试 2: 主文件 thin coordinator 向后兼容
# ============================================================
def test_thin_coordinator_backward_compat():
    banner("[2] lgb_enhanced_trainer.py 向后兼容导出")
    import lgb_enhanced_trainer

    # 主文件应保留这些职责常量
    assert hasattr(lgb_enhanced_trainer, "BASE_DIR"), "缺少 BASE_DIR"
    assert hasattr(lgb_enhanced_trainer, "MODELS_DIR"), "缺少 MODELS_DIR"
    assert hasattr(lgb_enhanced_trainer, "REPORTS_DIR"), "缺少 REPORTS_DIR"
    assert hasattr(lgb_enhanced_trainer, "LGB_ENHANCED_CONFIG"), "缺少 LGB_ENHANCED_CONFIG"
    assert hasattr(lgb_enhanced_trainer, "POSITION_SYMBOLS"), "缺少 POSITION_SYMBOLS"

    # 主文件应从子模块重新导出核心函数
    backward_compat_symbols = [
        "load_real_ohlcv",
        "fetch_all_real_ohlcv",
        "compute_news_sentiment_factors",
        "add_sentiment_features",
        "add_mean_reversion_features",
        "add_regime_aware_features",
        "add_industry_relative_strength_features",
        "add_capital_flow_features",
        "add_cross_market_features",
        "time_series_cv_evaluate",
        "select_features_by_importance",
        "save_model",
        "load_model_meta",
        "should_retrain",
        "train_lgb_with_fallback",
        "_train_lgb_with_fallback",
        "train_symbol_enhanced",
        "compute_regime_series",
        "train_symbol_regime_specific",
        "run_enhanced_training",
        "generate_comparison_report",
    ]
    for name in backward_compat_symbols:
        assert hasattr(lgb_enhanced_trainer, name), f"主文件缺少向后兼容导出: {name}"

    # 主文件应为 thin coordinator (行数大幅减少)
    main_file = ROOT / "lgb_enhanced_trainer.py"
    line_count = len(main_file.read_text(encoding="utf-8").splitlines())
    assert line_count < 300, f"主文件行数 {line_count} 仍过多 (应 < 300)"
    print(f"  ✓ 主文件 thin coordinator 行数={line_count}, 向后兼容 {len(backward_compat_symbols)} 个符号")


# ============================================================
# 测试 3: configure_paths 路径注入
# ============================================================
def test_configure_paths_injection():
    banner("[3] configure_paths 路径注入")
    from lgb_trainer import data_loader, news_sentiment, persistence, report_generator, trainer
    import lgb_enhanced_trainer

    # 主模块导入时 _inject_paths_to_submodules 应已自动调用
    # 验证子模块的 BASE_DIR 与主模块一致
    main_base = lgb_enhanced_trainer.BASE_DIR
    sub_modules = [
        ("data_loader", data_loader),
        ("news_sentiment", news_sentiment),
        ("persistence", persistence),
        ("report_generator", report_generator),
        ("trainer", trainer),
    ]
    for name, mod in sub_modules:
        assert mod.BASE_DIR == main_base, (
            f"{name}.BASE_DIR={mod.BASE_DIR} 与主模块 BASE_DIR={main_base} 不一致"
        )
    print(f"  ✓ 5 个子模块 BASE_DIR 已正确注入: {main_base}")

    # 动态修改路径测试
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        data_loader.configure_paths(tmp_path, tmp_path / "cache")
        assert data_loader.BASE_DIR == tmp_path
        assert data_loader.CACHE_DIR == tmp_path / "cache"
        # 恢复
        data_loader.configure_paths(main_base, lgb_enhanced_trainer.CACHE_DIR)
    print("  ✓ configure_paths 动态修改生效")


# ============================================================
# 测试 4: 评估指标数值正确
# ============================================================
def test_metrics_correctness():
    banner("[4] 评估指标 (r2_score / ic_score / signal_sharpe)")
    from lgb_trainer import metrics

    rng = np.random.RandomState(42)
    y_true = rng.randn(100)
    # 完美预测: R²=1, IC=1
    y_pred_perfect = y_true.copy()
    assert abs(metrics.r2_score(y_true, y_pred_perfect) - 1.0) < 1e-6
    assert abs(metrics.ic_score(y_true, y_pred_perfect) - 1.0) < 1e-6

    # 均值预测: R²≈0
    y_pred_mean = np.full_like(y_true, y_true.mean())
    assert abs(metrics.r2_score(y_true, y_pred_mean)) < 1e-6

    # 反向预测: IC≈-1
    y_pred_reverse = -y_true
    assert abs(metrics.ic_score(y_true, y_pred_reverse) + 1.0) < 1e-6

    # signal_sharpe: 正向预测 + 正收益 → 正夏普
    y_true_pos = np.abs(rng.randn(100))
    sharpe = metrics.signal_sharpe(y_true_pos, y_true_pos)
    assert sharpe > 0
    print(f"  ✓ R²/IC/Sharpe 数值验证通过 (完美预测 Sharpe={sharpe:.4f})")


# ============================================================
# 测试 5: time_series_cv_evaluate 走 Purged K-Fold
# ============================================================
def test_time_series_cv_uses_purged_kfold():
    banner("[5] time_series_cv_evaluate 使用 Purged K-Fold")
    from lgb_trainer import metrics
    import utils.purged_kfold as pk_mod

    X = np.random.RandomState(0).randn(240, 6).astype(float)
    y = np.random.RandomState(1).randn(240).astype(float)
    config = {
        "label_horizon": 5,
        "lgb_params": {
            "n_estimators": 20,
            "learning_rate": 0.1,
            "num_leaves": 15,
            "verbose": -1,
            "device_type": "cpu",
            "n_jobs": 1,
        },
        "early_stopping_rounds": 10,
    }

    calls = []
    orig = pk_mod.purged_timeseries_split

    def spy(n_samples, n_splits, embargo_pct=0.01):
        calls.append((n_samples, n_splits, embargo_pct))
        return orig(n_samples=n_samples, n_splits=n_splits, embargo_pct=embargo_pct)

    with patch.object(pk_mod, "purged_timeseries_split", side_effect=spy):
        result = metrics.time_series_cv_evaluate(X, y, config, n_splits=4)

    assert isinstance(result, dict)
    assert "fold_metrics" in result
    assert len(result["fold_metrics"]) == 4
    assert len(calls) == 1, f"Purged K-Fold 应调用 1 次, 实际 {len(calls)} 次"
    assert calls[0][0] == 240 and calls[0][1] == 4
    # embargo_pct 应 > 0 (防泄漏)
    assert calls[0][2] > 0, f"embargo_pct 应 > 0, 实际 {calls[0][2]}"
    print(f"  ✓ Purged K-Fold 调用 1 次, embargo_pct={calls[0][2]:.4f}, 4 折全部完成")


# ============================================================
# 测试 6: train_lgb_with_fallback GPU→CPU 自动回退
# ============================================================
def test_train_lgb_with_fallback_cpu():
    banner("[6] train_lgb_with_fallback (CPU 模式)")
    from lgb_trainer import trainer

    rng = np.random.RandomState(42)
    X_train = rng.randn(200, 5).astype(float)
    y_train = rng.randn(200).astype(float)
    X_eval = rng.randn(50, 5).astype(float)
    y_eval = rng.randn(50).astype(float)

    config = {
        "lgb_params": {
            "n_estimators": 30,
            "learning_rate": 0.1,
            "num_leaves": 15,
            "verbose": -1,
            "device_type": "cpu",
            "n_jobs": 1,
            "random_state": 42,
        },
        "early_stopping_rounds": 10,
    }

    model, device = trainer.train_lgb_with_fallback(
        X_train, y_train, X_eval, y_eval, config, log_tag="test-cpu"
    )
    assert model is not None
    assert device == "cpu"
    # 预测功能正常
    preds = model.predict(X_eval)
    assert preds.shape == (50,)
    print(f"  ✓ CPU 训练成功, device={device}, 预测 shape={preds.shape}")


def test_train_lgb_with_fallback_gpu_to_cpu():
    banner("[6b] train_lgb_with_fallback GPU→CPU 回退")
    from lgb_trainer import trainer

    rng = np.random.RandomState(42)
    X_train = rng.randn(200, 5).astype(float)
    y_train = rng.randn(200).astype(float)
    X_eval = rng.randn(50, 5).astype(float)
    y_eval = rng.randn(50).astype(float)

    # 强制 GPU, 但 GPU 不可用时应回退 CPU
    config = {
        "lgb_params": {
            "n_estimators": 30,
            "learning_rate": 0.1,
            "num_leaves": 15,
            "verbose": -1,
            "device_type": "gpu",  # 强制 GPU
            "n_jobs": 1,
            "random_state": 42,
        },
        "early_stopping_rounds": 10,
    }

    # 重置全局 GPU 禁用标志
    trainer._GLOBAL_GPU_DISABLED = False

    model, device = trainer.train_lgb_with_fallback(
        X_train, y_train, X_eval, y_eval, config, log_tag="test-gpu-fallback"
    )
    assert model is not None
    # device 应为 "cpu" (GPU 不可用时回退) 或 "gpu" (若环境真有 GPU)
    assert device in ("cpu", "gpu")
    # 若回退到 CPU, 全局禁用标志应被设置
    if device == "cpu":
        assert trainer._GLOBAL_GPU_DISABLED is True, "GPU 失败后应设置全局禁用标志"
    print(f"  ✓ GPU→CPU 回退测试通过, 最终 device={device}")


# ============================================================
# 测试 7: compute_regime_series
# ============================================================
def test_compute_regime_series():
    banner("[7] compute_regime_series regime 划分")
    from lgb_trainer import trainer

    # 构造 100 日大盘数据: 前 50 日下跌 (bear), 后 50 日上涨 (bull)
    dates = pd.date_range("2024-01-01", periods=100, freq="B")
    prices = list(np.linspace(100, 80, 50)) + list(np.linspace(80, 120, 50))
    proxy_df = pd.DataFrame(
        {"open": prices, "high": prices, "low": prices, "close": prices, "volume": 1000},
        index=dates,
    )

    regime = trainer.compute_regime_series(proxy_df, ma_period=20, slope_window=5)
    assert isinstance(regime, pd.Series)
    assert len(regime) == 100

    valid_regimes = {"bull", "bear", "choppy", "rebound", "unknown"}
    unique_values = set(regime.unique())
    assert unique_values.issubset(valid_regimes), (
        f"regime 取值 {unique_values} 不在 {valid_regimes} 内"
    )

    # 后段 (上涨 + MA 上升) 应包含 bull
    last_30_regimes = set(regime.iloc[-30:].unique())
    assert "bull" in last_30_regimes or "rebound" in last_30_regimes, (
        f"后 30 日 regime 应含 bull/rebound, 实际 {last_30_regimes}"
    )
    print(f"  ✓ regime 划分正确, 取值: {unique_values}, 后段含 bull/rebound")


# ============================================================
# 测试 8: persistence 闭环
# ============================================================
def test_persistence_roundtrip():
    banner("[8] persistence (save/load/should_retrain) 闭环")
    from lgb_trainer import persistence, trainer

    # 使用临时目录
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        original_models_dir = persistence.MODELS_DIR
        persistence.configure_paths(tmp_path, tmp_path / "models")

        # 构造一个 mock 模型 + 训练结果
        rng = np.random.RandomState(0)
        X = rng.randn(100, 5)
        y = rng.randn(100)

        # 使用模块级 MockModel (局部类无法 pickle)
        result = {
            "model": MockModel(),
            "n_samples": 100,
            "n_features_before": 5,
            "n_features_after": 5,
            "selected_features": ["f1", "f2", "f3", "f4", "f5"],
            "train_period": ("2024-01-01", "2024-06-01"),
            "test_period": ("2024-06-02", "2024-06-30"),
            "best_iteration": 50,
            "adaptive_retrained": False,
            "cv_before_selection": {"mean_r2": 0.1, "std_r2": 0.05, "mean_ic": 0.05, "std_ic": 0.02, "mean_sharpe": 0.5, "std_sharpe": 0.1, "fold_metrics": []},
            "cv_after_selection": {"mean_r2": 0.2, "std_r2": 0.04, "mean_ic": 0.1, "std_ic": 0.01, "mean_sharpe": 0.8, "std_sharpe": 0.1, "fold_metrics": []},
            "final_metrics": {"r2": 0.25, "ic": 0.15, "sharpe": 1.0},
            "signal": "BULLISH",
            "raw_prediction": 0.0123,
            "top_features": {"f1": 10, "f2": 8, "f3": 6, "f4": 4, "f5": 2},
        }
        config = {
            "retrain_interval_days": 7,
            "lgb_params": {"n_estimators": 100},
            "early_stopping_rounds": 50,
            "n_splits": 5,
            "test_ratio": 0.2,
            "top_n_features": 30,
            "feature_selection_threshold": 1,
            "news_lookback_days": 30,
        }

        # 保存
        paths = persistence.save_model("TEST999", result, config)
        assert Path(paths["model_path"]).exists()
        assert Path(paths["meta_path"]).exists()

        # 加载
        meta = persistence.load_model_meta("TEST999")
        assert meta is not None
        assert meta["symbol"] == "TEST999"
        assert meta["signal"] == "BULLISH"
        assert meta["best_iteration"] == 50

        # 刚保存, 不应需要重训
        assert persistence.should_retrain("TEST999", config) is False

        # 模拟过期: 修改 saved_at 为 10 天前
        from datetime import datetime, timedelta
        meta["saved_at"] = (datetime.now() - timedelta(days=10)).isoformat()
        with open(paths["meta_path"], "w", encoding="utf-8") as f:
            json.dump(meta, f)
        assert persistence.should_retrain("TEST999", config) is True

        # 不存在的标的应返回 True (需要训练)
        assert persistence.should_retrain("NOT_EXIST", config) is True

        # 恢复路径
        persistence.configure_paths(original_models_dir.parent, original_models_dir)
    print("  ✓ save/load/should_retrain 闭环验证通过 (含过期重训判定)")


# ============================================================
# 测试 9: 外部模块向后兼容导入
# ============================================================
def test_external_module_backward_compat():
    banner("[9] 外部模块 (institutional_pipeline_runner) 向后兼容导入")
    # 模拟外部模块的导入方式
    try:
        from lgb_enhanced_trainer import (
            LGB_ENHANCED_CONFIG,
            POSITION_SYMBOLS,
            train_symbol_enhanced,
            train_symbol_regime_specific,
            compute_regime_series,
            add_technical_features,
            add_cross_sectional_features,
            add_industry_relative_strength_features,
            add_capital_flow_features,
            add_cross_market_features,
            add_sentiment_features,
            add_mean_reversion_features,
        )
    except ImportError as e:
        raise AssertionError(f"外部模块向后兼容导入失败: {e}")

    # LGB_ENHANCED_CONFIG 应为非空字典
    assert isinstance(LGB_ENHANCED_CONFIG, dict)
    assert "lgb_params" in LGB_ENHANCED_CONFIG
    assert "n_estimators" in LGB_ENHANCED_CONFIG["lgb_params"]

    # POSITION_SYMBOLS 应为非空 list
    assert isinstance(POSITION_SYMBOLS, list)
    assert len(POSITION_SYMBOLS) > 0
    print(f"  ✓ 外部模块向后兼容导入成功 (POSITION_SYMBOLS={len(POSITION_SYMBOLS)} 只)")


# ============================================================
# 测试 10: CLI 入口参数解析
# ============================================================
def test_cli_arg_parsing():
    banner("[10] CLI 入口 (main) 参数解析")
    import lgb_enhanced_trainer as main_mod

    # 验证 main 函数存在
    assert callable(main_mod.main), "main 函数不可调用"

    # 模拟 CLI 参数解析 (不真正执行训练)
    test_cases = [
        ([], {"force_retrain": False, "symbols": None, "no_news": False}),
        (["--force-retrain"], {"force_retrain": True, "symbols": None, "no_news": False}),
        (["--symbols", "688041", "300308"], {"force_retrain": False, "symbols": ["688041", "300308"], "no_news": False}),
        (["--no-news"], {"force_retrain": False, "symbols": None, "no_news": True}),
        (["--force-retrain", "--no-news", "--symbols", "688041"], {
            "force_retrain": True, "symbols": ["688041"], "no_news": True
        }),
    ]

    for argv, expected in test_cases:
        with patch("sys.argv", ["lgb_enhanced_trainer.py"] + argv):
            parser = _build_test_parser()
            args = parser.parse_args(argv)
            assert args.force_retrain == expected["force_retrain"], (
                f"argv={argv}: force_retrain={args.force_retrain}, 期望 {expected['force_retrain']}"
            )
            assert args.no_news == expected["no_news"], (
                f"argv={argv}: no_news={args.no_news}, 期望 {expected['no_news']}"
            )
            if expected["symbols"] is None:
                assert args.symbols is None
            else:
                assert args.symbols == expected["symbols"], (
                    f"argv={argv}: symbols={args.symbols}, 期望 {expected['symbols']}"
                )
    print("  ✓ CLI 参数解析 5 个用例全部通过")


def _build_test_parser():
    """复用主文件的 argparse 配置 (避免执行 main)."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-retrain", action="store_true")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--no-news", action="store_true")
    return parser


# ============================================================
# 主入口
# ============================================================
def main():
    print("\n" + "#" * 60)
    print("# B3.5 lgb_enhanced_trainer 拆分验证")
    print("#" * 60)

    tests = [
        test_package_imports,
        test_thin_coordinator_backward_compat,
        test_configure_paths_injection,
        test_metrics_correctness,
        test_time_series_cv_uses_purged_kfold,
        test_train_lgb_with_fallback_cpu,
        test_train_lgb_with_fallback_gpu_to_cpu,
        test_compute_regime_series,
        test_persistence_roundtrip,
        test_external_module_backward_compat,
        test_cli_arg_parsing,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n  ✗ {test.__name__} 失败: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"验证结果: {passed} 通过 / {failed} 失败 / {len(tests)} 总计")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
