"""V9 单月冒烟测试: 验证 regime-specific 训练在 pipeline 中能正确触发"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("v9_smoke.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("v9_smoke")

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))


def main() -> None:
    """单月冒烟测试: 2024-06-03 (bull regime, 历史崩溃月)"""
    logger.info("=" * 70)
    logger.info("V9 单月冒烟测试: 2024-06-03 (bull regime)")
    logger.info("=" * 70)

    from institutional_pipeline_runner import InstitutionalPipelineRunner, PipelineContext

    symbols = [
        "588000", "688041", "002371", "688981", "300308", "000425", "601088",
        "600276", "600900", "515180", "600036", "518880", "300274", "603019",
        "600089", "688017", "600219", "600019", "000680", "000333", "000408",
        "000975", "002422",
    ]

    ctx = PipelineContext(
        mode="backtest",
        symbols=symbols,
        report_date="2024-06-03",
    )
    runner = InstitutionalPipelineRunner(ctx)

    t0 = time.time()
    logger.info("开始运行 pipeline (as_of=2024-06-03)...")
    result = runner.run()
    elapsed = time.time() - t0

    logger.info("=" * 70)
    logger.info("冒烟测试完成 (耗时 %.1f 秒)", elapsed)
    logger.info("=" * 70)
    logger.info("status: %s", result.get("status"))

    # 验证 V9 训练是否触发
    lgb_models = runner._lgb_models
    logger.info("LGB 模型数: %d", len(lgb_models))

    v9_count = 0
    bull_model_count = 0
    non_bull_model_count = 0
    full_model_count = 0

    for code, info in lgb_models.items():
        is_v9 = info.get("v9_regime_specific", False)
        if is_v9:
            v9_count += 1
            selected = info.get("selected_regime", "?")
            n_bull = info.get("n_bull_samples", 0)
            n_non_bull = info.get("n_non_bull_samples", 0)
            models_by_regime = info.get("models_by_regime", {})
            has_bull = models_by_regime.get("bull") is not None
            has_non_bull = models_by_regime.get("non_bull") is not None
            has_full = models_by_regime.get("full") is not None
            if has_bull:
                bull_model_count += 1
            if has_non_bull:
                non_bull_model_count += 1
            if has_full:
                full_model_count += 1
            signal = info.get("signal", 0)
            logger.info("  %s: V9=%s selected=%s bull_s=%d non_bull_s=%d signal=%.4f",
                        code, is_v9, selected, n_bull, n_non_bull, signal)

    logger.info("\nV9 训练汇总:")
    logger.info("  V9 模型数: %d / %d", v9_count, len(lgb_models))
    logger.info("  bull 模型数: %d", bull_model_count)
    logger.info("  non_bull 模型数: %d", non_bull_model_count)
    logger.info("  full (fallback) 模型数: %d", full_model_count)

    # 验证 2024-06-03 应该是 bull regime (历史上 V6.2 在此月 -5.11%)
    # V9 bull 模型应该给 600276 看跌信号 (隔离测试中 -0.9858)
    code_600276 = lgb_models.get("600276")
    if code_600276:
        logger.info("\n=== 600276 (关键验证) ===")
        logger.info("  selected_regime: %s", code_600276.get("selected_regime"))
        logger.info("  current_regime: %s", code_600276.get("current_regime"))
        logger.info("  signal: %.4f", code_600276.get("signal", 0))
        logger.info("  n_bull_samples: %d", code_600276.get("n_bull_samples", 0))
        logger.info("  n_non_bull_samples: %d", code_600276.get("n_non_bull_samples", 0))

    # 输出权重
    weights = result.get("steps", {}).get("portfolio_decision", {}).get("target_weights", {})
    logger.info("\n权重前 10:")
    sorted_w = sorted(weights.items(), key=lambda x: -abs(x[1]))[:10]
    for code, w in sorted_w:
        logger.info("  %s: %.4f", code, w)

    if v9_count > 0:
        logger.info("\n✓ V9 冒烟测试通过: regime-specific 训练已正确触发")
    else:
        logger.error("\n✗ V9 冒烟测试失败: 没有模型被标记为 v9_regime_specific")
        sys.exit(1)


if __name__ == "__main__":
    main()
