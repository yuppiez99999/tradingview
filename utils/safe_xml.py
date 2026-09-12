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
from xml.parsers import expat as _expat

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


class _HardenedXMLParser:
    """禁用 DTD / 实体声明的 ElementTree 兼容解析器 (defusedxml 缺失时的兜底防线).

    实现说明 (2026-09-12 修正)
    ------------------------
    本模块初版据「CPython 3.8+ XMLParser 支持 forbid_dtd=True」写成
    `_StdET.XMLParser(forbid_dtd=True)`, 但**该参数在 CPython 中并不存在**
    (3.11.2 实测 `TypeError: 'forbid_dtd' is an invalid keyword argument`),
    且 C 加速版 `_elementtree.XMLParser` 不暴露内部 pyexpat 解析器, 无法在其上
    挂 handler —— 于是异常被 `except TypeError` 吞掉并回退到**未加固**的默认
    `XMLParser`, billion-laughs 载荷可正常展开 (实测 `<lolz>&lol3;</lolz>` 被解析)。

    现改为直接驱动 pyexpat: `StartDoctypeDeclHandler` / `EntityDeclHandler`
    任一触发即抛 `ParseError`, 在**解析阶段**拒绝 DTD 与实体声明 —— 与
    defusedxml 的 EntitiesForbidden / DTDForbidden 语义一致, 且零外部依赖。
    """

    def __init__(self, *, target: Any = None, encoding: str | None = None) -> None:
        self.target = target if target is not None else _StdET.TreeBuilder()
        # 本模块即 B314 的防护层: 由我们控制的 pyexpat 解析器 + 显式 DTD/实体拒绝
        parser = _expat.ParserCreate(encoding, "}")  # nosec B314
        parser.StartDoctypeDeclHandler = self._on_doctype
        parser.EntityDeclHandler = self._on_entity
        parser.buffer_text = True
        target_obj = self.target
        parser.StartElementHandler = lambda tag, attrs: target_obj.start(tag, dict(attrs))
        parser.EndElementHandler = lambda tag: target_obj.end(tag)
        parser.CharacterDataHandler = target_obj.data
        self._parser = parser

    @staticmethod
    def _on_doctype(name: str, sysid: Any, pubid: Any, has_internal_subset: bool) -> None:
        raise ParseError(f"DTD forbidden: <!DOCTYPE {name}>")

    @staticmethod
    def _on_entity(
        name: str,
        is_parameter: bool,
        value: Any,
        base: Any,
        sysid: Any,
        pubid: Any,
        notation: Any,
    ) -> None:
        raise ParseError(f"entities forbidden: {name}")

    # --- ElementTree.XMLParser 兼容接口 (供 fromstring/parse 使用) ---
    def feed(self, data: str | bytes) -> None:
        self._parse(data, False)

    def close(self) -> Any:
        self._parse(b"", True)
        return self.target.close()

    def _parse(self, data: str | bytes, is_final: bool) -> None:
        """驱动 pyexpat 并把语法错误归一为 `ParseError`.

        pyexpat 抛的是 `xml.parsers.expat.ExpatError`, 而 defusedxml 与调用方
        约定的是 `ElementTree.ParseError`(二者互不继承) —— 若不归一, 调用方按
        `except ParseError` 写的容错分支会在"格式错误"这一最常见路径上失效。
        这里显式抬升为 `ParseError` 且保留行/列信息。
        """
        try:
            self._parser.Parse(data, is_final)
        except _expat.ExpatError as exc:
            raise ParseError(
                f"{exc} (line {self._parser.ErrorLineNumber}, "
                f"column {self._parser.ErrorColumnNumber})"
            ) from exc


def safe_xml_fromstring(text: str | bytes) -> Any:
    """安全解析内存中的 XML 文本, 返回根 Element.

    优先走 defusedxml; 否则用禁用 DTD 的 stdlib 解析器 (fail-safe 语义由调用方
    决定: 解析失败抛 ParseError, 不会静默返回污染数据)。
    """
    if _HAVE_DEFUSEDXML and _defused_fromstring is not None:
        return _defused_fromstring(text)
    return _StdET.fromstring(text, parser=_HardenedXMLParser())  # nosec B314 — parser 已拒绝 DTD/实体


def safe_xml_parse(source: str | Path | Any) -> Any:
    """安全解析 XML 文件/文件对象, 返回 ElementTree."""
    if _HAVE_DEFUSEDXML and _defused_parse is not None:
        return _defused_parse(source)
    return _StdET.parse(source, parser=_HardenedXMLParser())  # nosec B314 — parser 已拒绝 DTD/实体


def is_defusedxml_available() -> bool:
    """当前环境是否使用 defusedxml 后端 (供自检/审计输出)."""
    return _HAVE_DEFUSEDXML
