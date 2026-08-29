"""报告归档器 — 将生成的报告归档到按日期组织的目录

归档目录结构:
    BASE_DIR/每日报告归档/YYYY-MM-DD/<archive_name>

主入口文件的 archive_report() helper 调用此模块的 archive_report()。
"""

from __future__ import annotations

import logging
import os
from datetime import date

logger = logging.getLogger(__name__)


def get_archive_dir(base_dir: str, target_date: date | None = None) -> str:
    """获取归档目录路径 (按日期组织), 不创建目录。

    Args:
        base_dir: 项目根目录
        target_date: 归档日期, None 时用今天

    Returns:
        归档目录路径 (如 /path/每日报告归档/2026-08-04/)
    """
    if target_date is None:
        target_date = date.today()
    date_str = target_date.strftime("%Y-%m-%d")
    return os.path.join(base_dir, "每日报告归档", date_str)


def archive_report(
    base_dir: str, archive_name: str, report: str, target_date: date | None = None
) -> str:
    """将报告内容归档到按日期组织的目录, 返回归档文件路径。

    Args:
        base_dir: 项目根目录
        archive_name: 归档文件名 (如 '综合日报_20260804.txt')
        report: 报告文本内容
        target_date: 归档日期, None 时用今天

    Returns:
        归档文件的绝对路径
    """
    archive_dir = get_archive_dir(base_dir, target_date)
    os.makedirs(archive_dir, exist_ok=True)
    archive_path = os.path.join(archive_dir, archive_name)
    try:
        with open(archive_path, "w", encoding="utf-8") as f:
            f.write(report)
        logger.info("报告已归档: %s", archive_path)
    except OSError as e:
        logger.error("报告归档失败: %s (路径: %s)", e, archive_path)
    return archive_path
