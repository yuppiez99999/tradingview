#!/usr/bin/env python3
"""
Pipeline 因子信号离线生成脚本
================================

创建日期: 2026-07-26
创建原因: 顶级对冲基金审计 P0-A 深度修复 — 因子流水线真实接入生产（影子账户层）
审计文档: docs/HEDGE_FUND_AUDIT_2026-07-26_SYSTEM_BUGS_AND_AUTOMATION.md
修复记录: docs/AUDIT_FIX_CHANGELOG_2026-07-26.md

职责:
- 离线运行 PipelineOrchestrator 验证因子组合
- 提取最新 IC 加权组合信号（{symbol: signal}）
- 保存到 models/pipeline_factor_signals/pipeline_factor_signals_{trade_date}.json
- 由 daily_workflow Phase 5 (phase_signal) 在 07:00 加载应用

部署:
- Windows 任务计划: QuantPipelineFactor_06AM, 每交易日 06:00 触发
- 必须在 QuantWorkflow_07AM (07:00) 之前完成

用法:
    python scripts/run_pipeline_factor_offline.py
    python scripts/run_pipeline_factor_offline.py --date 2026-07-27
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# 将项目根目录加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def setup_logging(verbose: bool = False) -> None:
    """配置日志格式"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> int:
    """主入口

    Returns:
        0 成功 / 1 失败
    """
    parser = argparse.ArgumentParser(
        description="Pipeline 因子信号离线生成 (v8.6.4 P0-A 深度修复)",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="交易日期 YYYY-MM-DD，默认今日",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="显示 DEBUG 日志",
    )
    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger("run_pipeline_factor_offline")

    trade_date = args.date or datetime.now().strftime("%Y-%m-%d")
    logger.info("=" * 70)
    logger.info("Pipeline 因子信号离线生成")
    logger.info(f"  trade_date: {trade_date}")
    logger.info(f"  started_at: {datetime.now().isoformat()}")
    logger.info("=" * 70)

    try:
        # 延迟导入，避免日志配置前导入触发默认日志
        from utils.portfolio_optimizer import PortfolioOptimizer

        # 初始化优化器
        opt = PortfolioOptimizer()
        logger.info(f"\n[1/3] 信号输出目录: {opt.signals_dir}")

        # 运行离线流水线
        logger.info("\n[2/3] 运行 PipelineOrchestrator（可能耗时数分钟）...")
        success = opt.run_offline_pipeline(trade_date=trade_date)

        if not success:
            logger.info("\n[3/3] ❌ 失败: 因子信号生成失败，详见日志")
            logger.error("Pipeline 因子信号生成失败")
            return 1

        # 验证输出
        output_path = opt.signals_dir / f"pipeline_factor_signals_{trade_date}.json"
        if not output_path.exists():
            logger.info(f"\n[3/3] ❌ 失败: 信号文件未生成 {output_path}")
            return 1

        import json

        with open(output_path, encoding="utf-8") as f:
            data = json.load(f)

        stats = data.get("stats", {})
        combo = data.get("factor_combination", {})
        logger.info("\n[3/3] ✅ 成功: 因子信号已生成")
        logger.info(f"  文件: {output_path}")
        logger.info(f"  生成时间: {data.get('generated_at', 'N/A')}")
        logger.info(
            f"  因子组合: {combo.get('factor_a', '?')} + {combo.get('factor_b', '?')}"
        )
        logger.info(f"  组合方法: {combo.get('method', 'N/A')}")
        logger.info(f"  lookback: {combo.get('lookback', 'N/A')}")
        logger.info(f"  combined_ic_ir: {combo.get('combined_ic_ir', 0):+.4f}")
        logger.info(f"  live_dsr: {combo.get('live_dsr', 0):+.4f}")
        logger.info(f"  max_drawdown: {combo.get('max_drawdown', 0):.4f}")
        logger.info(
            f"  权重: w_a={combo.get('w_a', 0):+.4f}, w_b={combo.get('w_b', 0):+.4f}"
        )
        logger.info("\n  信号统计:")
        logger.info(f"    标的数: {stats.get('n_symbols', 0)}")
        logger.info(f"    正信号: {stats.get('n_positive', 0)}")
        logger.info(f"    负信号: {stats.get('n_negative', 0)}")
        logger.info(f"    最大值: {stats.get('max_signal', 0):+.4f}")
        logger.info(f"    最小值: {stats.get('min_signal', 0):+.4f}")
        logger.info(f"    均值:   {stats.get('avg_signal', 0):+.4f}")

        logger.info("Pipeline 因子信号生成成功: %s", output_path.name)
        return 0

    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ) as e:

        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.info(f"\n[3/3] ❌ 异常: {e}")
        logger.error("Pipeline 因子信号生成异常: %s", e, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
