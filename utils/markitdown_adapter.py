# -*- coding: utf-8 -*-
"""MarkItDown 文档转换适配器 — 28 系统集成层

核心功能:
    将外部文档 (PDF/Word/Excel/PPT/HTML/CSV) 转换为 Markdown,
    供 ai_report_agent.py 在生成报告时引用外部研报/公告。

设计原则:
    1. 子进程调用: markitdown 要求 Python >=3.10, 28系统是 3.8, 通过 py launcher 调用
    2. 懒加载: 首次调用时检查 markitdown 安装状态
    3. 优雅降级: markitdown 不可用时返回提示信息, 不崩溃
    4. 自动安装: 首次使用时自动 pip install markitdown

用法:
    from utils.markitdown_adapter import MarkItDownAdapter

    adapter = MarkItDownAdapter()
    md_text = adapter.convert_to_markdown("research_report.pdf")
    md_text = adapter.convert_url("https://example.com/report.html")

作者: 28 系统 PM
日期: 2026-08-01
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger("markitdown_adapter")

# ============================================================
# 常量配置
# ============================================================

# 支持的文件扩展名
SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt",
    ".xlsx", ".xls", ".csv", ".html", ".htm",
    ".txt", ".xml", ".json", ".md", ".rst",
}

# Python 3.10+ 候选版本 (按优先级)
_PY310_CANDIDATES = ["3.14", "3.13", "3.12", "3.11", "3.10"]

# 超时时间 (秒)
_CONVERT_TIMEOUT = 120
_INSTALL_TIMEOUT = 180


class MarkItDownAdapter:
    """MarkItDown 文档转换适配器.

    通过子进程调用 markitdown CLI, 实现 PDF/Word/Excel/PPT → Markdown 转换.

    Attributes:
        py_version: 可用的 Python 3.10+ 版本
        installed: markitdown 是否已安装

    Usage:
        >>> adapter = MarkItDownAdapter()
        >>> md = adapter.convert_to_markdown("report.pdf")
        >>> print(md[:200])
    """

    _instance: Optional["MarkItDownAdapter"] = None

    def __init__(self) -> None:
        self._py_version: Optional[str] = None
        self._installed: Optional[bool] = None  # None=未检查, True/False=已检查

    @classmethod
    def get_instance(cls) -> "MarkItDownAdapter":
        """获取单例."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ============================================================
    # 环境检测
    # ============================================================

    def _find_python310(self) -> Optional[str]:
        """查找可用的 Python 3.10+ 版本.

        Returns:
            版本号字符串 (如 "3.12"), 或 None
        """
        for version in _PY310_CANDIDATES:
            try:
                result = subprocess.run(
                    ["py", f"-{version}", "--version"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    logger.debug(f"找到 Python {version}: {result.stdout.strip()}")
                    return version
            except (subprocess.SubprocessError, FileNotFoundError):
                continue
        return None

    def _check_installed(self) -> bool:
        """检查 markitdown 是否已安装.

        Returns:
            是否已安装
        """
        if self._installed is not None:
            return self._installed

        if self._py_version is None:
            self._py_version = self._find_python310()

        if self._py_version is None:
            logger.warning("未找到 Python 3.10+, markitdown 不可用")
            self._installed = False
            return False

        try:
            result = subprocess.run(
                ["py", f"-{self._py_version}", "-m", "markitdown", "--version"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                self._installed = True
                logger.info(f"markitdown 已安装 (Python {self._py_version}): {result.stdout.strip()}")
                return True
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        self._installed = False
        return False

    def _ensure_installed(self) -> bool:
        """确保 markitdown 已安装, 必要时自动安装.

        Returns:
            是否可用
        """
        if self._check_installed():
            return True

        if self._py_version is None:
            return False

        logger.info(f"正在安装 markitdown (Python {self._py_version})...")
        try:
            result = subprocess.run(
                ["py", f"-{self._py_version}", "-m", "pip", "install", "markitdown"],
                capture_output=True, text=True, timeout=_INSTALL_TIMEOUT,
            )
            if result.returncode == 0:
                self._installed = True
                logger.info("markitdown 安装成功")
                return True
            logger.error(f"markitdown 安装失败: {result.stderr[:300]}")
        except subprocess.SubprocessError as e:
            logger.error(f"markitdown 安装异常: {e}")

        return False

    # ============================================================
    # 公开接口
    # ============================================================

    @property
    def is_available(self) -> bool:
        """markitdown 是否可用."""
        return self._ensure_installed()

    def convert_to_markdown(self, file_path: str) -> str:
        """将文件转换为 Markdown.

        Args:
            file_path: 文件路径 (PDF/Word/Excel/PPT/HTML等)

        Returns:
            Markdown 文本, 失败返回空字符串
        """
        path = Path(file_path)
        if not path.exists():
            logger.error(f"文件不存在: {file_path}")
            return ""

        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            logger.warning(f"不支持的文件格式: {path.suffix} (支持: {SUPPORTED_EXTENSIONS})")
            return ""

        if not self._ensure_installed():
            logger.warning("markitdown 不可用, 返回文件名占位")
            return f"[文档转换不可用: {path.name}]"

        try:
            result = subprocess.run(
                ["py", f"-{self._py_version}", "-m", "markitdown", str(path)],
                capture_output=True, text=True, timeout=_CONVERT_TIMEOUT,
                encoding="utf-8", errors="replace",
            )
            if result.returncode == 0:
                md_text = result.stdout
                logger.info(f"转换成功: {path.name} → {len(md_text)} 字符 Markdown")
                return md_text
            logger.error(f"转换失败 (exit={result.returncode}): {result.stderr[:300]}")
        except subprocess.TimeoutExpired:
            logger.error(f"转换超时 ({_CONVERT_TIMEOUT}s): {path.name}")
        except subprocess.SubprocessError as e:
            logger.error(f"转换异常: {e}")

        return ""

    def convert_url(self, url: str) -> str:
        """将网页 URL 转换为 Markdown.

        Args:
            url: 网页 URL

        Returns:
            Markdown 文本, 失败返回空字符串
        """
        if not self._ensure_installed():
            return f"[网页转换不可用: {url}]"

        try:
            result = subprocess.run(
                ["py", f"-{self._py_version}", "-m", "markitdown", url],
                capture_output=True, text=True, timeout=_CONVERT_TIMEOUT,
                encoding="utf-8", errors="replace",
            )
            if result.returncode == 0:
                md_text = result.stdout
                logger.info(f"URL转换成功: {url} → {len(md_text)} 字符")
                return md_text
            logger.error(f"URL转换失败: {result.stderr[:300]}")
        except subprocess.TimeoutExpired:
            logger.error(f"URL转换超时: {url}")
        except subprocess.SubprocessError as e:
            logger.error(f"URL转换异常: {e}")

        return ""

    def batch_convert(self, file_paths: list) -> dict:
        """批量转换文件.

        Args:
            file_paths: 文件路径列表

        Returns:
            {file_path: markdown_text} 字典
        """
        results = {}
        for fp in file_paths:
            md = self.convert_to_markdown(fp)
            if md:
                results[fp] = md
        logger.info(f"批量转换完成: {len(results)}/{len(file_paths)} 成功")
        return results

    def get_status(self) -> dict:
        """获取适配器状态.

        Returns:
            状态字典
        """
        return {
            "available": self.is_available,
            "python_version": self._py_version,
            "markitdown_installed": self._installed,
            "supported_formats": sorted(SUPPORTED_EXTENSIONS),
        }


# ============================================================
# 便捷函数
# ============================================================

_default_adapter: Optional[MarkItDownAdapter] = None


def get_adapter() -> MarkItDownAdapter:
    """获取默认适配器单例."""
    global _default_adapter
    if _default_adapter is None:
        _default_adapter = MarkItDownAdapter()
    return _default_adapter


def convert_to_markdown(file_path: str) -> str:
    """便捷函数: 转换文件为 Markdown."""
    return get_adapter().convert_to_markdown(file_path)


def convert_url(url: str) -> str:
    """便捷函数: 转换 URL 为 Markdown."""
    return get_adapter().convert_url(url)


# ============================================================
# CLI 入口 (用于测试)
# ============================================================

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="MarkItDown 文档转换适配器")
    parser.add_argument("file", nargs="?", help="要转换的文件路径")
    parser.add_argument("--url", help="要转换的 URL")
    parser.add_argument("--status", action="store_true", help="显示适配器状态")
    args = parser.parse_args()

    adapter = MarkItDownAdapter()

    if args.status:
        print("MarkItDown 适配器状态:")
        for k, v in adapter.get_status().items():
            print(f"  {k}: {v}")
        sys.exit(0)

    if args.file:
        md = adapter.convert_to_markdown(args.file)
        if md:
            print(md[:2000])
            if len(md) > 2000:
                print(f"\n... (共 {len(md)} 字符, 仅显示前2000)")
        else:
            print("转换失败")
        sys.exit(0 if md else 1)

    if args.url:
        md = adapter.convert_url(args.url)
        if md:
            print(md[:2000])
        else:
            print("URL 转换失败")
        sys.exit(0 if md else 1)

    parser.print_help()
