"""本地文件读取：txt/md 直读，pdf 探测 pdfplumber -> PyPDF2 fail-open 降级。"""
from __future__ import annotations

import logging
import os

_LOGGER = logging.getLogger("mobius_addon")


def load_text(path: str) -> str:
    """读取本地文件原文。支持 .txt/.md（直读）与 .pdf（探测解析库）。

    Raises:
        ValueError: 不支持的文件类型。
        RuntimeError: PDF 解析库未安装且无文本可提取。
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"文件不存在: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md", ".markdown"):
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    if ext == ".pdf":
        return _load_pdf(path)
    raise ValueError(f"不支持的文件类型: {ext} (支持 .txt/.md/.pdf)")


def _load_pdf(path: str) -> str:
    text = _try_pdfplumber(path)
    if text is not None:
        return text
    text = _try_pypdf2(path)
    if text is not None:
        return text
    raise RuntimeError(
        "PDF 解析库未安装: 请 'pip install pdfplumber' 或 'pip install PyPDF2'，"
        "或将 PDF 转为 txt/md 后导入"
    )


def _try_pdfplumber(path: str) -> str | None:
    try:
        import pdfplumber  # noqa: WPS433
    except ImportError:
        return None
    try:
        parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
        return "\n".join(parts)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("pdfplumber 解析失败: %s", exc)
        return None


def _try_pypdf2(path: str) -> str | None:
    try:
        import PyPDF2  # noqa: WPS433
    except ImportError:
        return None
    try:
        parts = []
        reader = PyPDF2.PdfReader(path)
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return "\n".join(parts)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("PyPDF2 解析失败: %s", exc)
        return None
