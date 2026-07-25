# -*- coding: utf-8 -*-
"""
V9 Regime-Specific 回测启动脚本
================================

动机:
    V7-Model + V7.1/V7.2 权重后处理均无法将 Window 1 Sharpe CV 降至 <0.5
    根因是单一 LGB 模型被 bear regime 主导 (47% 样本), 在 bull regime 信号失效
    (2024-06-03 bull regime 给 688017/300308 高权重 导致 -5.11% 月度亏损)

方案:
    每个标的训练 bull/non-bull 双模型, 预测时按当前 regime 选择对应模型
    (V9 在 2024-06-03 bull 模型给 600276 -0.9858 强烈看跌信号, IC=0.79)

运行:
    python _run_v9_backtest.py
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("v9_backtest.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("v9_backtest")

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "research"))


def main() -> None:
    """主入口: 运行 V9 regime-specific 回测"""
    logger.info("=" * 80)
    logger.info("V9 Regime-Specific 回测启动")
    logger.info("=" * 80)

    # 验证 V9 开关
    import institutional_pipeline_runner as ipr
    logger.info("V9 配置:")
    logger.info("  _V9_REGIME_SPECIFIC_ENABLED = %s", ipr._V9_REGIME_SPECIFIC_ENABLED)
    logger.info("  _V9_MIN_SAMPLES_PER_REGIME = %d", ipr._V9_MIN_SAMPLES_PER_REGIME)
    logger.info("  _V9_REGIME_PROXY_SYMBOL = %s", ipr._V9_REGIME_PROXY_SYMBOL)
    logger.info("  _V9_REGIME_MA_PERIOD = %d", ipr._V9_REGIME_MA_PERIOD)
    logger.info("  _V9_REGIME_SLOPE_WINDOW = %d", ipr._V9_REGIME_SLOPE_WINDOW)

    if not ipr._V9_REGIME_SPECIFIC_ENABLED:
        logger.error("V9 开关未启用, 退出")
        sys.exit(1)

    # 验证 V9 函数可用
    from lgb_enhanced_trainer import (
        train_symbol_regime_specific,
        compute_regime_series,
        _train_regime_subset,
    )
    logger.info("✓ V9 函数可调用: train_symbol_regime_specific, compute_regime_series")

    # 验证缓存已清除 (确保 V9 重新训练, 而非复用 V6.2 缓存)
    cache_dir = BASE_DIR / "output" / "institutional_pipeline"
    if cache_dir.exists() and any(cache_dir.iterdir()):
        logger.warning("缓存目录非空: %s", cache_dir)
        logger.warning("将复用已有缓存, V9 训练可能被跳过!")
    else:
        logger.info("✓ 缓存已清除, V9 将重新训练所有月份")

    # 运行回测
    from backtest_runner import run_backtest

    # 与 V6.2 基线一致: 2023-07-01 ~ 2025-12-31, 23 标的
    symbols = [
        "588000", "688041", "002371", "688981", "300308", "000425", "601088",
        "600276", "600900", "515180", "600036", "518880", "300274", "603019",
        "600089", "688017", "600219", "600019", "000680", "000333", "000408",
        "000975", "002422",
    ]

    t0 = time.time()
    logger.info("启动 run_backtest: symbols=%d, period=2023-07-01 ~ 2025-12-31",
                len(symbols))
    logger.info("预计耗时较长 (V9 每月每标的训练 2 个模型, 共 ~23×45×2 次训练)")

    try:
        result = run_backtest(
            symbols=symbols,
            start="2023-07-01",
            end="2025-12-31",
            resume=False,  # 强制不复用缓存, V9 重新训练
        )

        elapsed = time.time() - t0
        logger.info("=" * 80)
        logger.info("V9 回测完成 (耗时 %.1f 分钟)", elapsed / 60)
        logger.info("=" * 80)

        # 输出关键指标
        annual_return = result.get("annual_return", 0)
        max_dd = result.get("max_drawdown", 0)
        win_rate = result.get("win_rate", 0)
        months = result.get("months", 0)
        logger.info("关键指标:")
        logger.info("  年化收益: %.4f", annual_return)
        logger.info("  最大回撤: %.4f", max_dd)
        logger.info("  胜率: %.4f", win_rate)
        logger.info("  月数: %d", months)

        # 保存结果
        out_file = BASE_DIR / "output" / "validation_reports" / f"v9_regime_specific_backtest_{time.strftime('%Y%m%d_%H%M%S')}.json"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        logger.info("结果已保存: %s", out_file)

        # 计算并输出 Walk-Forward Sharpe CV (若记录足够)
        records = result.get("records", [])
        if records:
            logger.info("月度记录数: %d", len(records))
            # 输出每月简报
            logger.info("\n月度收益简报:")
            for r in records:
                date = r.get("date", "?")
                ret = r.get("portfolio_return", 0)
                logger.info("  %s: %.4f", date, ret)

    except Exception as e:
        logger.error("V9 回测失败: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
