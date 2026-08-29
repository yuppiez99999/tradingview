"""
代码库知识图谱 RAG — 基于 .code-review-graph/graph.db 的 Python 检索封装

把 code-review-graph MCP 已生成的 graph.db (nodes/edges/flows/communities)
暴露为程序化 API, 供 ai_coordinator / AutoResearch Skill 调用, 避免 AI 编码代理
在大仓库里改错文件。

graph.db schema (2026-08-17 探查):
    nodes(id, kind[File/Class/Function/Type/Test], name, qualified_name UNIQUE,
          file_path, line_start, line_end, language, parent_name, params,
          return_type, modifiers, is_test, file_hash, extra, updated_at,
          signature, community_id)
    edges(id, kind[CALLS/IMPORTS_FROM/INHERITS/REFERENCES/CONTAINS/TESTED_BY],
          source_qualified, target_qualified, file_path, line, extra,
          confidence, confidence_tier, updated_at)

设计依据: docs/1设计计划集成到系统内并能完整运行_20260817.md W.A.2
上游: docs/1 (code-graph-rag 接入建议) + .code-review-graph/graph.db (MCP 已生成)
下游: utils/ai_coordinator.py record_decision (影响半径记录) + AutoResearch Skill
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    from ..logging_manager import get_logger
except (ImportError, ValueError):

    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)


logger = get_logger("code_graph_rag")


# ============================================================
# 默认路径
# ============================================================


def _default_db_path() -> str:
    base = Path(__file__).resolve().parent.parent.parent
    return str(base / ".code-review-graph" / "graph.db")


# ============================================================
# 返回类型
# ============================================================


@dataclass
class SymbolLocation:
    """符号位置"""

    kind: str
    name: str
    qualified_name: str
    file_path: str
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    language: Optional[str] = None
    parent_name: Optional[str] = None
    signature: Optional[str] = None
    is_test: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "language": self.language,
            "parent_name": self.parent_name,
            "signature": self.signature,
            "is_test": self.is_test,
        }


@dataclass
class EdgeInfo:
    """边信息 (调用/引用/继承等)"""

    kind: str
    source_qualified: str
    target_qualified: str
    file_path: str
    line: int = 0
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source_qualified": self.source_qualified,
            "target_qualified": self.target_qualified,
            "file_path": self.file_path,
            "line": self.line,
            "confidence": self.confidence,
        }


@dataclass
class ImpactResult:
    """影响半径分析结果"""

    changed_files: list[str] = field(default_factory=list)
    changed_symbols: list[SymbolLocation] = field(default_factory=list)
    impacted_callers: list[EdgeInfo] = field(default_factory=list)
    impacted_importers: list[EdgeInfo] = field(default_factory=list)
    impacted_files: list[str] = field(default_factory=list)

    @property
    def impact_radius(self) -> int:
        return len(set(self.impacted_files))

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed_files": self.changed_files,
            "changed_symbols_count": len(self.changed_symbols),
            "impacted_callers_count": len(self.impacted_callers),
            "impacted_importers_count": len(self.impacted_importers),
            "impacted_files": self.impacted_files,
            "impact_radius": self.impact_radius,
        }


# ============================================================
# CodeGraphRAG 主类
# ============================================================


class CodeGraphRAG:
    """代码库知识图谱 RAG 检索器

    使用方式:
        rag = CodeGraphRAG()
        locs = rag.search_symbol("AlphaHedgeEngine")
        callers = rag.find_callers("AlphaHedgeEngine")
        impact = rag.impact_analysis("utils/signal_fusion.py")
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or _default_db_path()
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"graph.db 不存在: {self.db_path}")
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except (ValueError, TypeError, RuntimeError, OSError):
                pass
            self._conn = None

    def __enter__(self) -> "CodeGraphRAG":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # ------------------------------------------------------------
    # 符号检索
    # ------------------------------------------------------------

    def search_symbol(
        self,
        name: str,
        kind: Optional[str] = None,
        limit: int = 20,
    ) -> list[SymbolLocation]:
        """按符号名检索 (模糊匹配)

        Args:
            name: 符号名 (如 "AlphaHedgeEngine" / "register_source")
            kind: 限定类型 (File/Class/Function/Type/Test), None 为全部
            limit: 最多返回数
        """
        if not name:
            return []
        try:
            conn = self._get_conn()
            sql = "SELECT kind, name, qualified_name, file_path, line_start, line_end, language, parent_name, signature, is_test FROM nodes WHERE name LIKE ?"
            params: list[Any] = [f"%{name}%"]
            if kind:
                sql += " AND kind = ?"
                params.append(kind)
            sql += " ORDER BY kind, name LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_symbol(r) for r in rows]
        except (ValueError, TypeError, KeyError, sqlite3.Error, OSError) as e:
            logger.debug("search_symbol 失败 name=%s: %s", name, e)
            return []

    def get_node_detail(self, qualified_name: str) -> Optional[SymbolLocation]:
        """按 qualified_name 精确查节点"""
        if not qualified_name:
            return None
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT kind, name, qualified_name, file_path, line_start, line_end, language, parent_name, signature, is_test FROM nodes WHERE qualified_name = ?",
                (qualified_name,),
            ).fetchone()
            return self._row_to_symbol(row) if row else None
        except (ValueError, TypeError, KeyError, sqlite3.Error, OSError) as e:
            logger.debug("get_node_detail 失败 qn=%s: %s", qualified_name, e)
            return None

    # ------------------------------------------------------------
    # 调用关系
    # ------------------------------------------------------------

    def find_callers(self, func_name: str, limit: int = 50) -> list[EdgeInfo]:
        """查找谁调用了某函数/类 (CALLS 边, target 是被调方)

        Args:
            func_name: 函数/类名 (模糊匹配 qualified_name)
        """
        return self._find_edges(
            func_name, edge_kind="CALLS", direction="target", limit=limit
        )

    def find_callees(self, func_name: str, limit: int = 50) -> list[EdgeInfo]:
        """查找某函数/类调用了谁 (CALLS 边, source 是调用方)"""
        return self._find_edges(
            func_name, edge_kind="CALLS", direction="source", limit=limit
        )

    def find_importers(self, module_name: str, limit: int = 50) -> list[EdgeInfo]:
        """查找谁 import 了某模块 (IMPORTS_FROM 边)"""
        return self._find_edges(
            module_name, edge_kind="IMPORTS_FROM", direction="target", limit=limit
        )

    def find_inheritors(self, class_name: str, limit: int = 50) -> list[EdgeInfo]:
        """查找谁继承了某类 (INHERITS 边)"""
        return self._find_edges(
            class_name, edge_kind="INHERITS", direction="target", limit=limit
        )

    def _find_edges(
        self,
        name: str,
        edge_kind: str,
        direction: str,
        limit: int,
    ) -> list[EdgeInfo]:
        if not name:
            return []
        try:
            conn = self._get_conn()
            col = "target_qualified" if direction == "target" else "source_qualified"
            if col not in ("target_qualified", "source_qualified"):
                raise ValueError(f"Invalid column: {col}")
            sql = f"SELECT kind, source_qualified, target_qualified, file_path, line, confidence FROM edges WHERE kind = ? AND {col} LIKE ? LIMIT ?"  # noqa: S608 — col 已通过白名单校验, 值均参数化
            rows = conn.execute(sql, (edge_kind, f"%{name}%", limit)).fetchall()
            return [self._row_to_edge(r) for r in rows]
        except (ValueError, TypeError, KeyError, sqlite3.Error, OSError) as e:
            logger.debug("_find_edges 失败 name=%s kind=%s: %s", name, edge_kind, e)
            return []

    # ------------------------------------------------------------
    # 影响半径分析
    # ------------------------------------------------------------

    def impact_analysis(self, change_path: str, max_depth: int = 2) -> ImpactResult:
        """分析改动某文件的影响半径

        Args:
            change_path: 改动文件路径 (相对或绝对, 模糊匹配)
            max_depth: 最大递归深度 (1=直接调用方, 2=调用方的调用方)

        Returns:
            ImpactResult: 含受影响文件列表 + impact_radius
        """
        result = ImpactResult(changed_files=[change_path])
        if not change_path:
            return result

        try:
            conn = self._get_conn()

            changed_nodes = conn.execute(
                "SELECT kind, name, qualified_name, file_path, line_start, line_end, language, parent_name, signature, is_test FROM nodes WHERE file_path LIKE ?",
                (f"%{change_path}%",),
            ).fetchall()
            result.changed_symbols = [self._row_to_symbol(r) for r in changed_nodes]

            impacted_files: set[str] = set()
            caller_qnames: set[str] = {n.qualified_name for n in result.changed_symbols}

            for _depth in range(max_depth):
                if not caller_qnames:
                    break
                next_qnames: set[str] = set()
                placeholders = ",".join("?" for _ in caller_qnames)
                rows = conn.execute(
                    f"SELECT kind, source_qualified, target_qualified, file_path, line, confidence FROM edges WHERE kind IN ('CALLS','IMPORTS_FROM','REFERENCES','INHERITS') AND target_qualified IN ({placeholders})",  # noqa: S608 — placeholders 为 ? 占位符, 值通过参数传入
                    list(caller_qnames),
                ).fetchall()
                for r in rows:
                    edge = self._row_to_edge(r)
                    if edge.kind == "CALLS":
                        result.impacted_callers.append(edge)
                    else:
                        result.impacted_importers.append(edge)
                    if edge.file_path:
                        impacted_files.add(edge.file_path)
                    next_qnames.add(edge.source_qualified)
                caller_qnames = next_qnames - caller_qnames

            result.impacted_files = sorted(impacted_files)
            return result

        except (ValueError, TypeError, KeyError, sqlite3.Error, OSError) as e:
            logger.debug("impact_analysis 失败 path=%s: %s", change_path, e)
            return result

    # ------------------------------------------------------------
    # 统计信息
    # ------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """图谱统计信息"""
        try:
            conn = self._get_conn()
            node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            kind_dist = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT kind, COUNT(*) FROM nodes GROUP BY kind"
                )
            }
            edge_kind_dist = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT kind, COUNT(*) FROM edges GROUP BY kind"
                )
            }
            return {
                "db_path": self.db_path,
                "node_count": node_count,
                "edge_count": edge_count,
                "node_kind_distribution": kind_dist,
                "edge_kind_distribution": edge_kind_dist,
            }
        except (ValueError, TypeError, KeyError, sqlite3.Error, OSError) as e:
            logger.debug("stats 失败: %s", e)
            return {"db_path": self.db_path, "error": str(e)}

    # ------------------------------------------------------------
    # 行 → 数据类
    # ------------------------------------------------------------

    @staticmethod
    def _row_to_symbol(row: sqlite3.Row) -> SymbolLocation:
        return SymbolLocation(
            kind=row["kind"],
            name=row["name"],
            qualified_name=row["qualified_name"],
            file_path=row["file_path"],
            line_start=row["line_start"],
            line_end=row["line_end"],
            language=row["language"],
            parent_name=row["parent_name"],
            signature=row["signature"],
            is_test=bool(row["is_test"]),
        )

    @staticmethod
    def _row_to_edge(row: sqlite3.Row) -> EdgeInfo:
        return EdgeInfo(
            kind=row["kind"],
            source_qualified=row["source_qualified"],
            target_qualified=row["target_qualified"],
            file_path=row["file_path"],
            line=row["line"] or 0,
            confidence=row["confidence"] if "confidence" in row.keys() else 1.0,
        )


# ============================================================
# 便捷函数
# ============================================================

_rag_instance: Optional[CodeGraphRAG] = None


def get_code_graph_rag() -> CodeGraphRAG:
    """获取全局 CodeGraphRAG 单例"""
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = CodeGraphRAG()
    return _rag_instance
