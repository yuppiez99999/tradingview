"""
大宗商品基本面分析模式
Wind数据综合分析
"""

from __future__ import annotations

import os
import sys

from core.context import (
    BASE_DIR,
    ProgressIndicator,
    logger,
)
from utils.cli_helpers import archive_report, write_report_file
from utils.datetime_utils import now_bj


def run_commodity_fundamentals(args):
    """大宗商品基本面分析模式 - Wind数据综合分析"""
    print("\n💎 大宗商品基本面综合分析")
    print("=" * 70)

    progress = ProgressIndicator("大宗商品基本面分析", 3)

    progress.update(1, "加载Wind数据模块...")
    try:
        # 尝试导入大宗商品基本面综合模块
        sys.path.insert(0, os.path.join(BASE_DIR, "..", "03_投研与策略生成"))
        from 大宗商品基本面综合 import get_copper_fundamentals

        progress.update(2, "获取铜/金/银/原油/铁矿石/动力煤数据...")
        result = get_copper_fundamentals()

        progress.update(3, "生成报告...")
        report_lines = [
            "# 大宗商品基本面分析报告",
            "",
            f"**生成时间**: {now_bj().strftime('%Y-%m-%d %H:%M:%S')}",
            f"**数据来源**: {result.get('数据来源', '未知')}",
            "",
            "---",
            "",
            "## 核心数据",
            "",
        ]

        for k, v in result.items():
            if k in ("更新时间", "数据来源", "警告"):
                continue
            report_lines.append(f"- **{k}**: {v}")

        report_lines.append("")
        report_lines.append(f"**更新时间**: {result.get('更新时间', 'N/A')}")
        if "警告" in result:
            report_lines.append(f"> ⚠️ {result['警告']}")
        report_lines.extend(
            [
                "",
                "---",
                "*本报告由大宗商品基本面分析模块自动生成*",
            ]
        )

        report = "\n".join(report_lines)
        print("\n" + report)

        write_report_file(report, getattr(args, "output", None))
        archive_report(report, "大宗商品基本面")

        progress.complete("✅ 大宗商品基本面分析完成")
        return result

    except ImportError as e:
        progress.complete(f"❌ 大宗商品基本面模块不可用: {e}")
        logger.error(f"导入失败: {e}")
        return None
