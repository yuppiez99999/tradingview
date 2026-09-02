"""
诊断脚本: 定位 alpha=mock 的根因
================================
直接调用 pipeline 一阶段, 详细记录每一步输出,
找出 LGB 训练失败/动量 IC 失败的具体原因。
"""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

# Ensure project root in sys.path before importing local modules
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "v8.3_institutional"))
import institutional_pipeline_runner as ipr  # noqa: E402
from institutional_pipeline_runner import (  # noqa: E402
    InstitutionalPipelineRunner,
    PipelineContext,
)

_HAS_LGB = getattr(ipr, "_HAS_LGB", False)
_LGB_IMPORT_ERR = getattr(ipr, "_LGB_IMPORT_ERR", "LGB import succeeded (no error)")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("diagnose")


def main() -> int:
    symbols = [
        "600519",
        "000858",
        "601318",
        "000001",
        "600036",
        "601398",
        "600276",
        "000063",
    ]
    date_str = "2024-01-01"

    logger.info("=" * 80)
    logger.info("诊断 alpha=mock 根因: as_of=%s", date_str)
    logger.info("  _HAS_LGB=%s _LGB_IMPORT_ERR=%s", _HAS_LGB, _LGB_IMPORT_ERR)
    logger.info("=" * 80)

    ctx = PipelineContext(mode="backtest", symbols=symbols, report_date=date_str)
    runner = InstitutionalPipelineRunner(ctx)

    # 1) 检查历史缓存
    logger.info("-" * 80)
    logger.info("[1] 历史缓存检查")
    cache = runner._historical_cache
    logger.info("  _historical_cache 大小: %d", len(cache))
    for s in symbols:
        df = cache.get(s)
        if df is None:
            logger.warning("  %s: 缓存为 None (LGB/动量 IC 都需要历史数据)", s)
        else:
            logger.info(
                "  %s: %d 行, columns=%s, 范围 %s ~ %s",
                s,
                len(df),
                list(df.columns)[:6],
                df.index[0] if len(df) else "N/A",
                df.index[-1] if len(df) else "N/A",
            )

    # 2) 检查 LGB 模型训练
    logger.info("-" * 80)
    logger.info("[2] LGB Walk-forward 训练")
    if not _HAS_LGB:
        logger.error("  LGB 不可用: %s", _LGB_IMPORT_ERR)
    else:
        try:
            lgb_result = runner._lgb_walkforward_train()
            logger.info("  LGB 训练结果: %s", lgb_result)
            logger.info("  _lgb_models 大小: %d", len(runner._lgb_models))
            for code, info in runner._lgb_models.items():
                cv = info.get("cv_after_selection", {})
                logger.info(
                    "    %s: cv_ic=%s signal=%s",
                    code,
                    cv.get("mean_ic"),
                    info.get("signal"),
                )
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error("  LGB 训练异常:\n%s", traceback.format_exc())

    # 3) 检查 alpha 评估
    logger.info("-" * 80)
    logger.info("[3] Alpha 评估 (_real_alpha_evaluation)")
    try:
        alpha_report = runner._real_alpha_evaluation()
        logger.info(
            "  category=%s active_factors=%d",
            alpha_report.get("category"),
            alpha_report.get("active_factors"),
        )
        evals = alpha_report.get("evaluations", [])
        logger.info("  evaluations 数量: %d", len(evals))
        for e in evals[:10]:
            logger.info(
                "    %s: ic=%.4f category=%s",
                e.get("factor_name"),
                e.get("ic_1d"),
                e.get("category"),
            )
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.error("  Alpha 评估异常:\n%s", traceback.format_exc())

    # 4) 调用完整 _step_alpha_evaluation (含 provenance 判断)
    logger.info("-" * 80)
    logger.info("[4] _step_alpha_evaluation 完整流程 (provenance)")
    try:
        report = runner._step_alpha_evaluation()
        if isinstance(report, dict):
            logger.info(
                "  最终 category=%s active=%d",
                report.get("category"),
                report.get("active_factors"),
            )
        else:
            logger.info(
                "  report type=%s, attrs=%s",
                type(report).__name__,
                {a: getattr(report, a, None) for a in ("category", "active_factors")},
            )
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.error("  _step_alpha_evaluation 异常:\n%s", traceback.format_exc())

    logger.info("=" * 80)
    logger.info("诊断完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
