"""
LightGBM 增强训练器 — Thin Coordinator (B3.5 重构)
====================================================

本文件原为 2739 行 God Object, 已按职责拆分为 7 个聚焦模块 (lgb_trainer 包):

    lgb_trainer/
    ├── __init__.py             # 包入口, 统一导出
    ├── data_loader.py          # 真实 OHLCV 数据加载 (Wind MCP > 新浪 HTTP)
    ├── news_sentiment.py       # 新闻情绪因子 (Wind MCP 优先, 新浪回退)
    ├── feature_engineering.py  # 扩展特征工程 (均值回归/Regime/行业/资金/跨市场)
    ├── metrics.py              # 评估指标 + 时间序列交叉验证 (Purged K-Fold)
    ├── persistence.py          # 模型持久化 (save/load/retrain 判定)
    ├── trainer.py              # 训练器核心 (单标的 + V9 regime-specific + 流程编排)
    └── report_generator.py     # 三方对比报告生成

本文件保留职责:
    1. 路径常量定义 (BASE_DIR / MODELS_DIR / REPORTS_DIR / LOG_DIR / CACHE_DIR)
    2. 训练配置 (LGB_ENHANCED_CONFIG) — 作为权威配置源
    3. sys.path 初始化 (导入 autolearn_trainer / utils 等依赖)
    4. 注入路径/配置到子模块 (configure_paths)
    5. 向后兼容导出 (institutional_pipeline_runner / system_integration / live_scheduler 等)
    6. CLI 入口 (main)

用法:
    python lgb_enhanced_trainer.py                    # 训练全部持仓
    python lgb_enhanced_trainer.py --symbols 688041   # 训练单个标的
    python lgb_enhanced_trainer.py --force-retrain    # 强制重训
    python lgb_enhanced_trainer.py --no-news         # 跳过新闻因子 (加速)
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ============================================================
# 路径常量 (权威定义, 由 configure_paths 注入到各子模块)
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
MODELS_DIR = BASE_DIR / "models" / "lgb_enhanced"
REPORTS_DIR = BASE_DIR / "reports" / "lgb_enhanced"
LOG_DIR = BASE_DIR / "logs"
CACHE_DIR = BASE_DIR / "cache" / "ohlcv"

for _d in [MODELS_DIR, REPORTS_DIR, LOG_DIR, CACHE_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# sys.path 初始化 (子模块依赖 autolearn_trainer / utils 等)
# Wave 3 第三阶段: 改用 utils.path_config.setup_sys_path() 统一管理
sys.path.insert(0, str(BASE_DIR))  # bootstrap: 确保 utils 包可导入
from utils.path_config import setup_sys_path  # noqa: E402

setup_sys_path()  # noqa: E402  # 统一注入 v8.3 根 / v8.3 src / utils

# 复用旧训练器的标的清单和特征工程 (供 institutional_pipeline_runner 等外部模块导入)
from autolearn_trainer import (  # noqa: E402
    POSITION_SYMBOLS,
)

# ============================================================
# 增强训练配置 (权威配置源, 子模块通过 configure_paths 注入)
# ============================================================
LGB_ENHANCED_CONFIG: dict[str, Any] = {
    "lookback_days": 500,
    "min_samples": 150,
    "test_ratio": 0.2,
    "n_splits": 5,
    "top_n_features": 30,  # 保留 Top 30 (放宽让情绪因子有机会入选)
    "feature_selection_threshold": 1,  # 阈值降低到 1 (从3降到1, 让弱信号特征也能入选)
    "label_horizon": 5,  # V6: 标签horizon=5日 (替代次日收益率, 提升震荡市IC)
    "retrain_interval_days": 7,
    "model_quality_threshold": {
        "min_cv_r2": -0.3,
        "min_cv_ic": 0.0,
        "min_cv_sharpe": 0.0,
    },
    "lgb_params": {
        "n_estimators": 2000,  # 大幅增加
        "learning_rate": 0.005,  # 更小学习率
        "max_depth": 6,
        "num_leaves": 31,
        "min_child_samples": 30,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "reg_alpha": 0.1,
        "reg_lambda": 0.5,
        "random_state": 42,
        "verbose": -1,
        "n_jobs": -1,
        "device_type": "gpu",  # v8.7 启用 GPU 加速 (LightGBM 4.3.0 OpenCL)
        "gpu_platform_id": 0,  # GPU 平台 ID
        "gpu_device_id": 0,  # GPU 设备 ID
    },
    "early_stopping_rounds": 200,  # 放宽 50→200
    "news_lookback_days": 250,  # v4.1: 30→250天, 匹配OHLCV回看, 确保滚动特征有统计意义
    "news_cache_hours": 6,  # 新闻缓存有效期 (小时)
    "adaptive_retrain_threshold": 5,  # best_iter <= 5 触发自适应重训
    "adaptive_retrain_lr": 0.001,  # 自适应重训学习率 (0.005→0.001)
    "adaptive_retrain_n_estimators": 5000,  # 配合更小学习率, 增加估计器
}

logger = logging.getLogger("lgb_enhanced")


# ============================================================
# 子模块路径注入 (configure_paths)
# ============================================================
def _inject_paths_to_submodules() -> None:
    """将主模块的路径/配置注入到 lgb_trainer 各子模块。

    子模块默认使用 `Path(__file__).resolve().parent.parent` 作为 BASE_DIR,
    在主模块 import 后立即调用此函数, 确保所有子模块使用一致的路径。
    """
    from lgb_trainer import data_loader, news_sentiment, persistence, report_generator, trainer

    data_loader.configure_paths(BASE_DIR, CACHE_DIR)
    news_sentiment.configure_paths(BASE_DIR, CACHE_DIR, LGB_ENHANCED_CONFIG)
    persistence.configure_paths(BASE_DIR, MODELS_DIR)
    report_generator.configure_paths(BASE_DIR, REPORTS_DIR, LGB_ENHANCED_CONFIG)
    trainer.configure_paths(BASE_DIR, MODELS_DIR)


# 首次导入时立即注入路径
_inject_paths_to_submodules()


# ============================================================
# 向后兼容导出 (从 lgb_trainer 子模块重新导出)
# ============================================================
# 外部模块 (institutional_pipeline_runner / system_integration / live_scheduler /
# daily_workflow / tests) 通过 `from lgb_enhanced_trainer import X` 访问以下符号,
# 此处统一从 lgb_trainer 包重新导出, 保持向后兼容。

# ── 数据层 ──

# ── 新闻情绪因子 ──

# ── 扩展特征工程 ──

# ── 评估指标 + 时间序列交叉验证 ──

# ── 模型持久化 ──

# ── 训练器核心 ──
from lgb_trainer.report_generator import (  # noqa: E402
    generate_comparison_report,
)
from lgb_trainer.trainer import (  # noqa: E402
    run_enhanced_training,
)


# ============================================================
# CLI 入口
# ============================================================
def main() -> None:
    """CLI 入口: 解析参数 → 配置日志 → 执行训练 → 生成报告。"""
    parser = argparse.ArgumentParser(
        description="LightGBM 增强训练 (真实OHLCV + 情绪因子 + 放宽早停)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--force-retrain", action="store_true", help="强制重训所有标的")
    parser.add_argument("--symbols", nargs="+", default=None, help="指定标的代码 (默认全部持仓)")
    parser.add_argument("--no-news", action="store_true", help="跳过新闻情绪因子 (加速训练)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(
                LOG_DIR / f"lgb_enhanced_{datetime.now():%Y%m%d}.log",
                encoding="utf-8",
            ),
            logging.StreamHandler(sys.stdout),
        ],
    )

    symbols = POSITION_SYMBOLS
    if args.symbols:
        symbols = [s for s in POSITION_SYMBOLS if s[0] in args.symbols]

    # GitHub 集成钩子: unsloth 本地 LLM 可用时, 记录 GPU 信息供训练决策 (2026-08-21)
    try:
        from utils.unsloth_adapter import get_unsloth_adapter, is_unsloth_available
        if is_unsloth_available():
            gpu_info = get_unsloth_adapter().get_gpu_info()
            logger.info(f"unsloth 本地 LLM 可用: {gpu_info}")
        else:
            logger.debug("unsloth 不可用, 训练走标准 LightGBM 路径")
    except (ImportError, RuntimeError, OSError, ValueError) as _e:
        logger.debug(f"unsloth 钩子跳过: {_e}")

    result = run_enhanced_training(
        symbols=symbols,
        force_retrain=args.force_retrain,
        use_news=not args.no_news,
    )

    if result["status"] == "OK":
        report_path = generate_comparison_report(result)
        logger.info(f"\n✓ 训练完成, 对比报告: {report_path}")
        logger.info("✓ 信号文件: models/lgb_enhanced/lgb_enhanced_signals.json")
        sys.exit(0)
    else:
        logger.info("\n✗ 训练失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
