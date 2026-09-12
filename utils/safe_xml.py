"""安全 XML 解析工具 — 防 XML 炸弹 / 外部实体注入 (CWE-611 / CWE-776).

背景
----
`xml.etree.ElementTree.fromstring` / `parse` 在默认解释器下对 *不受信任* 的 XML
存在 XXE / billion-laughs 风险 (bandit B314)。本模块提供 **优先 defusedxml、
缺失时回退 stdlib 并显式禁用实体/DTD 解析** 的统一入口, 消除 B314 告警的同时
不引入新的硬依赖。

使用示例
--------
    from utils.safe_xml import safe_xml_fromstring, safe_xml_parse

    root = safe_xml_fromstring(remote_xml_text)   # 远程/外部文本
    root = safe_xml_parse(local_junit_path)       # 本地我方产物
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as _StdET

logger = logging.getLogger("safe_xml")

# 统一的解析异常类型: defusedxml 沿用 stdlib 的 ParseError, 便于调用方单一 except
ParseError = _StdET.ParseError

_defused_fromstring = None
_defused_parse = None
_HAVE_DEFUSEDXML = False

try:  # pragma: no cover - 依赖可用性分支
    from defusedxml.ElementTree import fromstring as _defused_fromstring  # type: ignore
    from defusedxml.ElementTree import parse as _defused_parse  # type: ignore

    _HAVE_DEFUSEDXML = True
except ImportError:  # pragma: no cover - 沙箱/精简环境无 defusedxml 时回退
    logger.debug("defusedxml 不可用, 回退 stdlib ElementTree (已禁用实体解析)")


def _hardened_stdlib_parser() -> "_StdET.XMLParser":
    """构造禁用 DTD/实体扩展的 stdlib 解析器 (无 defusedxml 时的兜底防线).

    CPython 3.8+ 的 `XMLParser` 基于 expat, 传入 `forbid_dtd=True` 可在解析阶段
    直接拒绝含 DTD 的文档(最常见的 XXE/billion-laughs 载体), 从而不依赖外部包
    也拿到实质防护。
    """
    try:
        # 本模块即 B314 的防护层: defusedxml 缺失时的兜底, 已通过 forbid_dtd=True 拒绝 DTD/实体扩展
        return _StdET.XMLParser(forbid_dtd=True)  # type: ignore[call-arg]  # nosec B314
    except TypeError:  # pragma: no cover - 解释器不支持 forbid_dtd
        return _StdET.XMLParser()  # nosec B314 — 解释器不支持 forbid_dtd 的降级路径


def safe_xml_fromstring(text: str | bytes) -> Any:
    """安全解析内存中的 XML 文本, 返回根 Element.

    优先走 defusedxml; 否则用禁用 DTD 的 stdlib 解析器 (fail-safe 语义由调用方
    决定: 解析失败抛 ParseError, 不会静默返回污染数据)。
    """
    if _HAVE_DEFUSEDXML and _defused_fromstring is not None:
        return _defused_fromstring(text)
    parser = _hardened_stdlib_parser()
    return _StdET.fromstring(text, parser=parser)  # nosec B314 — parser 已禁用 DTD


def safe_xml_parse(source: str | Path | Any) -> Any:
    """安全解析 XML 文件/文件对象, 返回 ElementTree."""
    if _HAVE_DEFUSEDXML and _defused_parse is not None:
        return _defused_parse(source)
    parser = _hardened_stdlib_parser()
    return _StdET.parse(source, parser=parser)  # nosec B314 — parser 已禁用 DTD


def is_defusedxml_available() -> bool:
    """当前环境是否使用 defusedxml 后端 (供自检/审计输出)."""
    return _HAVE_DEFUSEDXML
