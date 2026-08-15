"""GNN 供应链因子 — 图构建桥接模块

将 `graph_data_source`（真实边数据源）与 `supply_chain_graph`（图分析引擎）对接，
从持仓列表构建完整的 GNN 关系图谱并运行综合分析。

数据流:
    持仓 symbols
      └─> graph_data_source.get_industry_relationship / get_stock_boards
            └─> build_industry_edges / build_concept_edges / build_thematic_edges (真实边)
      └─> supply_chain_graph.SupplyChainEdge (结构对齐)
            └─> SupplyChainGraph (分析引擎: 中心性/枢纽/瓶颈/风险传染)
            └─> load_default_chains (默认产业链兜底, 融合)

用法:
    python utils/supply_chain_builder.py --symbols 688041,300308,002371
    python utils/supply_chain_builder.py --from-positions   # 从 config/positions.json 读取
"""

from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

# 兼容直接运行时路径 (python utils/supply_chain_builder.py)
_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from graph_data_source import get_graph_data_source  # noqa: E402
from supply_chain_graph import SupplyChainEdge, SupplyChainGraph  # noqa: E402


# Windows 控制台 UTF-8 输出 (幂等 — 已由 graph_data_source 包装则跳过)
def _ensure_utf8_stream() -> None:
    """将 stdout/stderr 包装为 UTF-8; 已包装则跳过 (幂等)."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        if getattr(stream, "encoding", "").lower() == "utf-8":
            continue
        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            continue
        try:
            setattr(sys, name, io.TextIOWrapper(buffer, encoding="utf-8", errors="replace"))
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            pass


_ensure_utf8_stream()

logger = logging.getLogger(__name__)

# 默认持仓 (对应 config/portfolio.yaml 核心标的, 桥接自测用)
DEFAULT_SYMBOLS = ["688041", "300308", "002371", "603019", "300782",
                   "688017", "300274", "000408", "601088", "600276",
                   "600900", "300033"]


class SupplyChainBuilder:
    """供应链图构建器 — 从持仓构建真实关系图谱.

    Args:
        symbols: 标的代码列表
        include_default_chains: 是否融合硬编码默认产业链 (兜底真实边不足)
        include_themes: 是否包含当日题材边
        max_hops: 影响传播最大跳数 (设计文档要求 ≤2 防过度平滑)
    """

    def __init__(
        self,
        symbols: List[str],
        include_default_chains: bool = True,
        include_themes: bool = True,
        max_hops: int = 2,
    ):
        self.symbols = [str(s).strip() for s in symbols if str(s).strip()]
        self.include_default_chains = include_default_chains
        self.include_themes = include_themes
        self.max_hops = max(1, int(max_hops))
        self.data_source = get_graph_data_source()
        self.graph = SupplyChainGraph(max_propagation_hops=self.max_hops)

    # ----------------------------------------------------------
    # 图构建
    # ----------------------------------------------------------
    def build(self) -> Dict[str, Any]:
        """构建关系图谱，返回图结构信息.

        Returns:
            {node_count, edge_count, edge_types, symbols}
        """
        if not self.symbols:
            raise ValueError("symbols 不能为空")

        # 1) 真实边数据源 (行业/概念/题材)
        raw_edges = self.data_source.build_graph_edges(
            self.symbols, include_themes=self.include_themes
        )
        real_count = len(raw_edges)

        # 2) 转换为 SupplyChainEdge 并加入图
        added = self.graph.add_edges(self._to_edges(raw_edges))

        # 3) 默认产业链兜底 (仅补真实边未覆盖的节点对, 融合)
        default_count = 0
        if self.include_default_chains:
            # 先看默认链是否已通过真实边覆盖, 避免重复
            existing = {(e.source, e.target) for e in self.graph.adjacency.values()
                        for e in e}
            for chain_name, edges in SupplyChainGraph.DEFAULT_CHAINS.items():
                for src, tgt, rtype, strength in edges:
                    if (src, tgt) in existing:
                        continue
                    self.graph.add_edge(SupplyChainEdge(
                        source=src, target=tgt, relation_type=rtype,
                        strength=strength, source_info=f"默认_{chain_name}",
                    ))
                    default_count += 1

        return {
            "node_count": len(self.graph.all_nodes),
            "edge_count": len(self.graph.adjacency) and sum(len(v) for v in self.graph.adjacency.values()),
            "real_edges": real_count,
            "default_added": default_count,
            "symbols": self.symbols,
        }

    def _to_edges(self, raw_edges: List[Dict[str, Any]]) -> List[SupplyChainEdge]:
        """将 graph_data_source 输出的字典转换为 SupplyChainEdge."""
        edges = []
        for e in raw_edges:
            try:
                edges.append(SupplyChainEdge(
                    source=str(e["source"]),
                    target=str(e["target"]),
                    relation_type=str(e["relation_type"]),
                    strength=float(e.get("strength", 0.5)),
                    source_info=str(e.get("source_info", "")),
                ))
            except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as exc:
                logger.warning("[Builder] 边转换失败: %s — %s", exc, e)
        return edges

    # ----------------------------------------------------------
    # 分析
    # ----------------------------------------------------------
    def analyze(self) -> Dict[str, Any]:
        """运行综合分析 (中心性/枢纽/瓶颈/风险传染).

        Returns:
            可序列化字典: {graph_info, summary}
        """
        info = self.build()
        summary = self.graph.summarize()
        result = {
            "graph_info": info,
            "summary": summary,
        }
        return result

    def summarize(self) -> str:
        """生成人类可读摘要 (基于 SupplyChainGraph.summarize 的字典)."""
        self.build()  # 确保图已构建
        summary = self.graph.summarize()
        info = {
            "node_count": len(self.graph.all_nodes),
            "edge_count": sum(len(v) for v in self.graph.adjacency.values()),
        }

        lines = []
        lines.append("=" * 70)
        lines.append("GNN 供应链关系图谱摘要")
        lines.append("=" * 70)
        lines.append(f"标的数: {len(self.symbols)} | 节点数: {info['node_count']} | "
                     f"边数: {info['edge_count']}")
        lines.append(f"最大传播跳数: {self.max_hops} (防过度平滑)")

        hubs = summary.get("hubs", [])
        bottlenecks = summary.get("bottlenecks", [])
        lines.append(f"\n[枢纽节点] (度数 ≥ 阈值): {hubs or '无'}")
        lines.append(f"[瓶颈节点] (介数中心性高): {bottlenecks or '无'}")

        top_pr = summary.get("top_pagerank", [])
        if top_pr:
            lines.append("\n[PageRank Top5] (节点影响力)")
            for node, score in top_pr:
                lines.append(f"  {node}: {score:.4f}")

        high_risk = summary.get("high_risk_symbols", [])
        if high_risk:
            lines.append("\n[高风险标的] (传染风险 Top5)")
            for sym, risk in high_risk:
                lines.append(f"  {sym}: {risk:.4f}")

        return "\n".join(lines)

    def propagate(self, source: str, impact: float = 1.0) -> List[Any]:
        """从指定节点传播影响 (验证 Lead-Lag 传导)."""
        self.build()
        return self.graph.propagate_impact(source, impact)


def load_positions_symbols() -> List[str]:
    """从 config/positions.json 或 portfolio.yaml 读取核心持仓代码."""
    config_dir = _DIR.parent / "config"
    # positions.json
    pos_file = config_dir / "positions.json"
    if pos_file.exists():
        try:
            with open(pos_file, encoding="utf-8") as f:
                data = json.load(f)
            # 支持两种结构:
            #   {positions: {code: {...}, ...}}   — positions 为字典, key=代码
            #   {positions: [{code/symbol}, ...]}  — positions 为列表
            codes = []
            positions = data.get("positions", data)
            if isinstance(positions, dict):
                for k, v in positions.items():
                    if k == "meta":
                        continue
                    if isinstance(v, dict):
                        c = v.get("code") or k
                    else:
                        c = k
                    if c:
                        codes.append(str(c))
            elif isinstance(positions, list):
                for item in positions:
                    if isinstance(item, dict):
                        c = item.get("code") or item.get("symbol")
                    else:
                        c = item
                    if c:
                        codes.append(str(c))
            # 剥离交易所后缀 (588080.SH -> 588080), 并过滤空
            clean = []
            for c in codes:
                base = c.split(".")[0].strip()
                if base:
                    clean.append(base)
            return list(dict.fromkeys(clean))
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as exc:
            logger.warning(f"[Builder] positions.json 解析失败: {exc}")
    # portfolio.yaml 兜底
    return DEFAULT_SYMBOLS


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="GNN 供应链关系图谱构建器")
    parser.add_argument("--symbols", type=str, default="",
                        help="标的代码逗号分隔, 如 688041,300308,002371")
    parser.add_argument("--from-positions", action="store_true",
                        help="从 config/positions.json 读取持仓")
    parser.add_argument("--no-themes", action="store_true",
                        help="不含当日题材边")
    parser.add_argument("--no-default", action="store_true",
                        help="不含默认产业链兜底")
    parser.add_argument("--max-hops", type=int, default=2,
                        help="影响传播最大跳数 (默认2, 防过度平滑)")
    parser.add_argument("--propagate", type=str, default="",
                        help="从指定节点传播影响, 如 688041")
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    if args.from_positions or not args.symbols:
        symbols = load_positions_symbols()
    else:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    builder = SupplyChainBuilder(
        symbols=symbols,
        include_default_chains=not args.no_default,
        include_themes=not args.no_themes,
        max_hops=args.max_hops,
    )

    logger.info(builder.summarize())

    if args.propagate:
        logger.info("\n" + "=" * 70)
        logger.info(f"影响传播 (来源 {args.propagate})")
        logger.info("=" * 70)
        paths = builder.propagate(args.propagate)
        if not paths:
            logger.info("  (无传播路径)")
        for p in paths[:15]:
            logger.info(f"  {' -> '.join(p.path)}  强度={p.total_strength:.3f} "
                  f"类型={p.edge_types}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
