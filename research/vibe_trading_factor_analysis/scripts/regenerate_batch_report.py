# -*- coding: utf-8 -*-
"""从已持久化的 pipeline_state.json 重新生成批次报告 Markdown

用途：
- 修复报告 bug 后无需重跑流水线
- CIO 视角反复分析已有批次结果

用法:
    python regenerate_batch_report.py <batch_id>
    python regenerate_batch_report.py first_batch_20260725_103736
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "vibe_trading"


def regenerate(batch_id: str, symbols: list[str] = None, n_trials: int = None) -> int:
    """从 pipeline_state.json 重新生成报告

    Args:
        batch_id: 批次 ID
        symbols: 数据标的列表（不传则从 log 推断）
        n_trials: 多重检验基数

    Returns:
        0=成功, 1=失败
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s | %(message)s")
    logger = logging.getLogger("regenerate")

    state_file = REPORTS_DIR / batch_id / "pipeline_state.json"
    if not state_file.exists():
        logger.error("找不到批次状态文件: %s", state_file)
        return 1

    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)

    logger.info("加载批次状态: %s (factors=%d)", batch_id, len(state.get("factors", [])))

    # 推断 symbols/n_trials（如未指定）
    if symbols is None:
        symbols = ["<从历史日志推断>"] * 23  # 占位
    if n_trials is None:
        n_trials = max(state.get("total_candidates", 1), 13)

    # 构造伪 PipelineResult 调用 _write_batch_report_md
    from research.vibe_trading_factor_analysis.scripts.run_first_batch import _write_batch_report_md

    # 构造一个简单的 namespace 对象
    class _Result:
        pass

    result = _Result()
    for k, v in state.items():
        setattr(result, k, v)

    md_path = _write_batch_report_md(result, symbols, n_trials)
    print(f"\n报告重新生成: {md_path}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python regenerate_batch_report.py <batch_id>")
        sys.exit(2)
    sys.exit(regenerate(sys.argv[1]))
