"""utils.cli_helpers — CLI 辅助函数 (降级版)

修复日期: 2026-08-04
修复原因: utils/cli_helpers.py 缺失, 17 个 cli/modes 文件导入失败,
          导致 cli.modes 包无法加载, CLI 入口不可用。

设计原则:
    1. 降级实现: 所有函数提供最小可用功能, 保证导入不报错
    2. 不破坏: 若原始模块后续恢复, 降级实现可被覆盖
    3. 实用优先: write_report_file/archive_report 实际写入文件,
       其他函数返回合理默认值
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger("cli_helpers")

_BASE_DIR = Path(__file__).resolve().parent.parent


def write_report_file(content: str, filename: str, subdir: str = "reports") -> str:
    """写报告文件到 reports/ 目录

    Args:
        content: 报告内容
        filename: 文件名
        subdir: 子目录 (默认 reports)

    Returns:
        写入的文件路径
    """
    report_dir = _BASE_DIR / subdir
    report_dir.mkdir(parents=True, exist_ok=True)
    filepath = report_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info("报告已写入: %s", filepath)
    return str(filepath)


def archive_report(filepath: str, archive_subdir: str = "archive") -> str:
    """归档报告到 archive 目录

    Args:
        filepath: 原始文件路径
        archive_subdir: 归档子目录

    Returns:
        归档后的文件路径
    """
    src = Path(filepath)
    if not src.exists():
        logger.warning("归档失败, 文件不存在: %s", filepath)
        return filepath

    archive_dir = _BASE_DIR / "reports" / archive_subdir
    archive_dir.mkdir(parents=True, exist_ok=True)
    dst = archive_dir / src.name
    shutil.copy2(src, dst)
    logger.info("报告已归档: %s -> %s", src, dst)
    return str(dst)


def get_stock_name(code: str) -> str:
    """获取股票名称 (降级: 返回代码本身)

    Args:
        code: 股票代码

    Returns:
        股票名称 (降级返回代码)
    """
    # 尝试从 positions.json 读取名称
    try:
        import json

        positions_file = _BASE_DIR / "config" / "positions.json"
        if positions_file.exists():
            with open(positions_file, encoding="utf-8") as f:
                data = json.load(f)
            positions = data.get("positions", {})
            pos = positions.get(code, {})
            name: str = pos.get("name", "")
            if name:
                return name
    except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
        pass

    return str(code)


def log_execution_summary(mode_name: str, summary_dict: dict) -> None:
    """记录执行摘要 (降级: 打印到控制台)

    Args:
        mode_name: 模式名称
        summary_dict: 摘要字典
    """
    print(f"\n📋 {mode_name} 执行摘要:")
    for key, value in summary_dict.items():
        print(f"  {key}: {value}")


def get_ml_signal_section(
    code: str | None = None, return_raw: bool = False
) -> str | None:
    """获取 ML 信号部分 (降级: 返回空字符串或 None)

    与 量化策略系统_统一入口_v8.6.py 中的完整版签名对齐, 支持 return_raw 参数.
    降级模式下 ML 模块不可用, return_raw=True 返回 None (调用方走"暂无信号"分支).

    Args:
        code: 股票代码 (降级模式未使用)
        return_raw: 为 True 时完整版返回 (report, result) tuple; 降级返回 None

    Returns:
        return_raw=False: ML 信号文本 (降级返回空字符串)
        return_raw=True: (report, result) tuple (降级返回 None)
    """
    if return_raw:
        return None
    return ""


def get_etf_flow_data() -> dict:
    """获取 ETF 资金流数据 (降级: 返回空字典)

    Returns:
        ETF 资金流数据字典
    """
    return {}


def get_portfolio_quotes() -> dict:
    """获取组合行情 (降级: 返回空字典)

    Returns:
        组合行情字典
    """
    return {}


def get_archive_dir() -> Path:
    """获取归档目录路径

    Returns:
        归档目录 Path
    """
    archive_dir = _BASE_DIR / "reports" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    return archive_dir
