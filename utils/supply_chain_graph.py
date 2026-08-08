"""供应链关系图谱 v1.0

模仿 Bloomberg MAPS / S&P Global Market Intelligence 的供应链网络分析

核心能力:
1. 供应链关系建模 — 供应商/客户/竞争对手/合作伙伴
2. 影响传播 — 上游冲击向下游传播
3. 中心性分析 — 关键节点识别 (瓶颈/枢纽)
4. 风险传染 — 单点风险沿网络扩散评估
5. 路径搜索 — 两标的间最短关联路径

参考:
- Acemoglu et al. (2012) "The Network Origins of Aggregate Fluctuations"
- Herskovic (2018) "Firms' Strategic Default and the Risk of Chain Reactions"

A股适配:
- 数据源: 上市公司公告 (前五大客户/供应商) + 行业协会 + 招股说明书
- 行业链: 算力链 (芯片-服务器-云-应用) / 新能源链 (锂-电池-车-充电) 等
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================


@dataclass
class SupplyChainEdge:
    """供应链关系边"""

    source: str  # 上游标的
    target: str  # 下游标的
    relation_type: str  # SUPPLIER / CUSTOMER / COMPETITOR / PARTNER
    # 关系强度 [0, 1]
    strength: float = 0.5
    # 业务占比 (source 对 target 的营收占比, 如果是 SUPPLIER 关系)
    revenue_share: float = 0.0
    # 关系起始日期
    start_date: str = ""
    # 来源
    source_info: str = ""  # "2024年报" / "公告" 等
    # ===== 非对称传导弹性 (2026-08-03 深化) =====
    # 上游涨价 → 下游传导弹性 (通常较高: 涨价易传导成本)
    # 上游降价 → 下游传导弹性 (通常较低: 长协合同限制, 降价红利滞后)
    # None 时回退到 strength (对称)
    up_elasticity: float | None = None
    down_elasticity: float | None = None


@dataclass
class NodeMetrics:
    """节点中心性指标"""

    symbol: str
    # 度中心性
    in_degree: int = 0
    out_degree: int = 0
    total_degree: int = 0
    # 介数中心性 (经过该节点的最短路径数)
    betweenness_centrality: float = 0.0
    # 特征向量中心性 (连接到的节点重要性)
    eigenvector_centrality: float = 0.0
    # PageRank
    pagerank: float = 0.0
    # 聚类系数
    clustering_coefficient: float = 0.0
    # 关键节点标记
    is_hub: bool = False  # 枢纽节点
    is_bottleneck: bool = False  # 瓶颈节点


@dataclass
class PropagationPath:
    """影响传播路径"""

    source: str
    target: str
    path: list[str]  # 路径节点序列
    total_strength: float  # 累计传播强度
    hops: int  # 跳数
    edge_types: list[str]  # 各段边类型


@dataclass
class SupplyChainResult:
    """供应链分析结果"""

    nodes: list[str] = field(default_factory=list)
    edges: list[SupplyChainEdge] = field(default_factory=list)
    metrics: dict[str, NodeMetrics] = field(default_factory=dict)
    # 关键节点
    hubs: list[str] = field(default_factory=list)
    bottlenecks: list[str] = field(default_factory=list)
    # 风险传染评估
    risk_contagion: dict[str, float] = field(default_factory=dict)  # {symbol: risk_score}
    # 网络统计
    network_density: float = 0.0
    avg_path_length: float = 0.0
    num_components: int = 0


# ============================================================
# 供应链关系图谱
# ============================================================


class SupplyChainGraph:
    """供应链关系图谱

    用法:
        graph = SupplyChainGraph()
        graph.add_edge(SupplyChainEdge(source="002475", target="300308",
                                       relation_type="SUPPLIER", strength=0.7))
        # 影响传播
        paths = graph.propagate_impact("002475", max_hops=3)
        # 中心性
        metrics = graph.compute_centrality()
    """

    # A股主要产业链 (默认数据)
    DEFAULT_CHAINS = {
        # 算力链
        "compute_chain": [
            ("688981", "300308", "SUPPLIER", 0.6),  # 中芯国际 → 中际旭创
            ("300308", "000977", "SUPPLIER", 0.5),  # 中际旭创 → 浪潮信息
            ("000977", "600519", "CUSTOMER", 0.3),  # 浪潮信息 → (酒企客户)
        ],
        # 新能源车链
        "nev_chain": [
            ("002475", "300750", "SUPPLIER", 0.7),  # 立讯精密 → 宁德时代
            ("300750", "002594", "SUPPLIER", 0.6),  # 宁德时代 → 比亚迪
            ("002594", "002126", "SUPPLIER", 0.4),  # 比亚迪 → 银轮股份
        ],
        # 半导体链
        "semi_chain": [
            ("688981", "002049", "SUPPLIER", 0.7),  # 中芯国际 → 紫光国微
            ("002049", "300142", "SUPPLIER", 0.5),  # 紫光国微 → 沃森生物(疫苗业务合作)
            ("688981", "300142", "SUPPLIER", 0.3),
        ],
    }

    def __init__(
        self,
        # 传播参数
        decay_per_hop: float = 0.6,  # 每跳衰减
        max_propagation_hops: int = 4,  # 最大传播跳数
        # 关键节点阈值
        hub_degree_threshold: int = 5,  # 枢纽节点度数阈值
        bottleneck_betweenness_threshold: float = 0.3,
        # PageRank 参数
        pagerank_damping: float = 0.85,
        pagerank_iterations: int = 100,
    ):
        self.decay_per_hop = float(decay_per_hop)
        self.max_hops = int(max_propagation_hops)
        self.hub_threshold = int(hub_degree_threshold)
        self.bottleneck_threshold = float(bottleneck_betweenness_threshold)
        self.pr_damping = float(pagerank_damping)
        self.pr_iterations = int(pagerank_iterations)

        # 邻接表
        self.adjacency: dict[str, list[SupplyChainEdge]] = defaultdict(list)
        self.reverse_adjacency: dict[str, list[SupplyChainEdge]] = defaultdict(list)
        self.all_nodes: set[str] = set()

    # ------------------------------------------------------------
    # 图构建
    # ------------------------------------------------------------

    def add_edge(self, edge: SupplyChainEdge) -> None:
        """添加关系边"""
        self.adjacency[edge.source].append(edge)
        self.reverse_adjacency[edge.target].append(edge)
        self.all_nodes.add(edge.source)
        self.all_nodes.add(edge.target)

    def add_edges(self, edges: list[SupplyChainEdge]) -> int:
        """批量添加边"""
        count = 0
        for e in edges:
            try:
                self.add_edge(e)
                count += 1
            except Exception as exc:  # P2 模块 fail-safe, 待后续精确化  # noqa: BLE001
                logger.warning("[SupplyChain] 添加边失败: %s", exc)
        return count

    def load_default_chains(self) -> int:
        """加载默认产业链"""
        count = 0
        for chain_name, edges in self.DEFAULT_CHAINS.items():
            for src, tgt, rtype, strength in edges:
                self.add_edge(
                    SupplyChainEdge(
                        source=src,
                        target=tgt,
                        relation_type=rtype,
                        strength=strength,
                        source_info=f"默认_{chain_name}",
                    )
                )
                count += 1
        logger.info("[SupplyChain] 加载默认产业链: %d 条边", count)
        return count

    # ------------------------------------------------------------
    # 影响传播
    # ------------------------------------------------------------

    def propagate_impact(
        self,
        source: str,
        impact_strength: float = 1.0,
        max_hops: int | None = None,
    ) -> list[PropagationPath]:
        """从源节点传播影响

        Args:
            source: 源标的
            impact_strength: 初始冲击强度 [0, 1]
            max_hops: 最大跳数 (None 用默认)

        Returns:
            List[PropagationPath] — 所有受影响节点的传播路径
        """
        max_hops = max_hops or self.max_hops
        if source not in self.all_nodes:
            return []

        # BFS 传播
        paths: list[PropagationPath] = []
        visited: set[str] = {source}
        queue: deque = deque([(source, [source], impact_strength, [], [])])

        while queue:
            current, path, strength, edge_types, edge_strengths = queue.popleft()
            if len(path) - 1 >= max_hops:
                continue

            for edge in self.adjacency.get(current, []):
                next_node = edge.target
                if next_node in visited:
                    continue
                # 衰减
                new_strength = strength * self.decay_per_hop * edge.strength
                if new_strength < 0.01:
                    continue
                visited.add(next_node)
                new_path = [*path, next_node]
                new_edge_types = [*edge_types, edge.relation_type]
                paths.append(
                    PropagationPath(
                        source=source,
                        target=next_node,
                        path=new_path,
                        total_strength=new_strength,
                        hops=len(new_path) - 1,
                        edge_types=new_edge_types,
                    )
                )
                queue.append((next_node, new_path, new_strength, new_edge_types, [*edge_strengths, edge.strength]))

        return paths

    def propagate_asymmetric_impact(
        self,
        source: str,
        impact_strength: float = 1.0,
        direction: str = "up",
        max_hops: int | None = None,
    ) -> list[PropagationPath]:
        """非对称传导传播 (深化: 区分涨价/降价传导).

        核心: 供应链传导具有非对称性 —
          - 上游涨价 (direction="up") 易向下游传导成本 (up_elasticity 高)
          - 上游降价 (direction="down") 下游不一定立即受益 (down_elasticity 低, 长协合同限制)

        边弹性选择:
          - up 传导  → edge.up_elasticity (回退 strength)
          - down 传导 → edge.down_elasticity (回退 strength)

        Args:
            source: 源标的
            impact_strength: 初始冲击强度 [0,1]
            direction: "up" (涨价传导) / "down" (降价传导)
            max_hops: 最大跳数

        Returns:
            List[PropagationPath] — 非对称传播路径
        """
        max_hops = max_hops or self.max_hops
        if source not in self.all_nodes:
            return []
        if direction not in ("up", "down"):
            raise ValueError("direction 必须是 'up'(涨价传导) 或 'down'(降价传导)")

        def _edge_elasticity(edge: SupplyChainEdge) -> float:
            """取指定方向的传导弹性 (非对称)."""
            if direction == "up":
                e = edge.up_elasticity if edge.up_elasticity is not None else edge.strength
            else:
                e = edge.down_elasticity if edge.down_elasticity is not None else edge.strength
            return max(0.0, float(e))

        paths: list[PropagationPath] = []
        visited: set[str] = {source}
        queue: deque = deque([(source, [source], impact_strength, [])])

        while queue:
            current, path, strength, edge_types = queue.popleft()
            if len(path) - 1 >= max_hops:
                continue
            for edge in self.adjacency.get(current, []):
                next_node = edge.target
                if next_node in visited:
                    continue
                elasticity = _edge_elasticity(edge)
                new_strength = strength * self.decay_per_hop * elasticity
                if new_strength < 0.01:
                    continue
                visited.add(next_node)
                new_path = [*path, next_node]
                new_edge_types = [*edge_types, edge.relation_type]
                paths.append(
                    PropagationPath(
                        source=source,
                        target=next_node,
                        path=new_path,
                        total_strength=new_strength,
                        hops=len(new_path) - 1,
                        edge_types=new_edge_types,
                    )
                )
                queue.append((next_node, new_path, new_strength, new_edge_types))
        return paths

    def get_asymmetry_ratio(
        self,
        source: str,
        impact_strength: float = 1.0,
        max_hops: int | None = None,
    ) -> dict[str, float]:
        """计算指定源节点的传导不对称度.

        asymmetry_ratio = 涨价传导强度 / 降价传导强度
        >1 表示涨价传导更强 (符合非对称: 涨价易传导, 降价红利滞后)

        Returns:
            {asymmetry_ratio, up_total, down_total, affected_up, affected_down}
        """
        up_paths = self.propagate_asymmetric_impact(source, impact_strength, "up", max_hops)
        down_paths = self.propagate_asymmetric_impact(source, impact_strength, "down", max_hops)
        up_total = sum(p.total_strength for p in up_paths)
        down_total = sum(p.total_strength for p in down_paths)
        ratio = (up_total / down_total) if down_total > 0 else float("inf")
        return {
            "source": source,
            "asymmetry_ratio": ratio,
            "up_total": up_total,
            "down_total": down_total,
            "affected_up": len({p.target for p in up_paths}),
            "affected_down": len({p.target for p in down_paths}),
        }

    def get_affected_symbols(
        self,
        source: str,
        impact_threshold: float = 0.05,
    ) -> dict[str, float]:
        """获取受影响标的及强度"""
        paths = self.propagate_impact(source)
        affected: dict[str, float] = {}
        for p in paths:
            if p.total_strength >= impact_threshold:
                # 取最大传播强度
                if p.target not in affected or p.total_strength > affected[p.target]:
                    affected[p.target] = p.total_strength
        return affected

    # ------------------------------------------------------------
    # 中心性分析
    # ------------------------------------------------------------

    def compute_centrality(self) -> dict[str, NodeMetrics]:
        """计算所有节点中心性指标"""
        metrics: dict[str, NodeMetrics] = {}
        for node in self.all_nodes:
            m = NodeMetrics(symbol=node)
            # 度中心性
            m.out_degree = len(self.adjacency.get(node, []))
            m.in_degree = len(self.reverse_adjacency.get(node, []))
            m.total_degree = m.in_degree + m.out_degree
            metrics[node] = m

        # 介数中心性 (简化: 所有节点对的最短路径中经过该节点的次数)
        self._compute_betweenness(metrics)

        # PageRank
        self._compute_pagerank(metrics)

        # 关键节点标记
        for m in metrics.values():
            m.is_hub = m.total_degree >= self.hub_threshold
            m.is_bottleneck = m.betweenness_centrality > self.bottleneck_threshold

        return metrics

    def _compute_betweenness(self, metrics: dict[str, NodeMetrics]) -> None:
        """简化版介数中心性 (BFS)"""
        nodes = list(self.all_nodes)
        between_count: dict[str, int] = defaultdict(int)
        total_paths = 0

        for source in nodes:
            # BFS 找到所有可达目标
            visited: set[str] = {source}
            queue: deque = deque([(source, [source])])
            while queue:
                current, path = queue.popleft()
                for edge in self.adjacency.get(current, []):
                    next_node = edge.target
                    if next_node in visited:
                        continue
                    visited.add(next_node)
                    new_path = [*path, next_node]
                    # 路径中间节点 +1
                    for intermediate in new_path[1:-1]:
                        between_count[intermediate] += 1
                    total_paths += 1
                    queue.append((next_node, new_path))

        # 归一化
        for node, m in metrics.items():
            m.betweenness_centrality = between_count[node] / total_paths if total_paths > 0 else 0.0

    def _compute_pagerank(self, metrics: dict[str, NodeMetrics]) -> None:
        """PageRank 计算"""
        nodes = list(self.all_nodes)
        n = len(nodes)
        if n == 0:
            return

        # 出度
        out_degrees = {node: max(len(self.adjacency.get(node, [])), 1) for node in nodes}
        # 初始化
        pr = {node: 1.0 / n for node in nodes}

        # 迭代
        for _ in range(self.pr_iterations):
            new_pr: dict[str, float] = {}
            dangling_sum = sum(pr[node] for node in nodes if not self.adjacency.get(node))
            for node in nodes:
                rank = (1 - self.pr_damping) / n
                rank += self.pr_damping * dangling_sum / n
                # 入边
                for edge in self.reverse_adjacency.get(node, []):
                    src = edge.source
                    rank += self.pr_damping * pr[src] / out_degrees[src] * edge.strength
                new_pr[node] = rank
            pr = new_pr

        # 归一化
        total_pr = sum(pr.values())
        if total_pr > 0:
            for node in nodes:
                metrics[node].pagerank = pr[node] / total_pr

    # ------------------------------------------------------------
    # 风险传染评估
    # ------------------------------------------------------------

    def assess_risk_contagion(
        self,
        shock_sources: dict[str, float],  # {symbol: shock_magnitude}
    ) -> dict[str, float]:
        """评估单点风险沿网络的传染

        Args:
            shock_sources: 冲击源 {symbol: shock_magnitude [0, 1]}

        Returns:
            {symbol: total_risk_score}
        """
        total_risk: dict[str, float] = defaultdict(float)
        for source, magnitude in shock_sources.items():
            affected = self.get_affected_symbols(source)
            for sym, strength in affected.items():
                total_risk[sym] += magnitude * strength
            # 源点本身
            total_risk[source] += magnitude
        return dict(total_risk)

    # ------------------------------------------------------------
    # 综合分析
    # ------------------------------------------------------------

    def analyze(self) -> SupplyChainResult:
        """综合分析"""
        result = SupplyChainResult()
        result.nodes = list(self.all_nodes)
        result.edges = []
        for _src, edges in self.adjacency.items():
            result.edges.extend(edges)

        # 中心性
        result.metrics = self.compute_centrality()
        result.hubs = [s for s, m in result.metrics.items() if m.is_hub]
        result.bottlenecks = [s for s, m in result.metrics.items() if m.is_bottleneck]

        # 网络密度
        n = len(self.all_nodes)
        if n > 1:
            max_edges = n * (n - 1)
            result.network_density = len(result.edges) / max_edges

        # 风险传染 (假设每个节点有 0.1 的基础风险)
        base_risk = {node: 0.1 for node in self.all_nodes}
        result.risk_contagion = self.assess_risk_contagion(base_risk)

        # 连通分量数 (简化: DFS)
        result.num_components = self._count_components()

        return result

    def _count_components(self) -> int:
        """连通分量数"""
        visited: set[str] = set()
        count = 0
        for node in self.all_nodes:
            if node in visited:
                continue
            # DFS
            stack = [node]
            while stack:
                n = stack.pop()
                if n in visited:
                    continue
                visited.add(n)
                for edge in self.adjacency.get(n, []):
                    if edge.target not in visited:
                        stack.append(edge.target)
                for edge in self.reverse_adjacency.get(n, []):
                    if edge.source not in visited:
                        stack.append(edge.source)
            count += 1
        return count

    # ------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------

    def get_relations(self, symbol: str) -> dict[str, list[SupplyChainEdge]]:
        """获取标的所有关系"""
        return {
            "outgoing": list(self.adjacency.get(symbol, [])),
            "incoming": list(self.reverse_adjacency.get(symbol, [])),
        }

    def find_path(self, source: str, target: str) -> list[str] | None:
        """BFS 找最短路径"""
        if source not in self.all_nodes or target not in self.all_nodes:
            return None
        visited: set[str] = {source}
        queue: deque = deque([(source, [source])])
        while queue:
            current, path = queue.popleft()
            if current == target:
                return path  # type: ignore[misc]
            for edge in self.adjacency.get(current, []):
                next_node = edge.target
                if next_node in visited:
                    continue
                visited.add(next_node)
                queue.append((next_node, [*path, next_node]))
        return None

    def summarize(self, result: SupplyChainResult | None = None) -> dict[str, Any]:
        """生成摘要"""
        result = result or self.analyze()
        return {
            "total_nodes": len(result.nodes),
            "total_edges": len(result.edges),
            "network_density": result.network_density,
            "num_components": result.num_components,
            "hubs": result.hubs,
            "bottlenecks": result.bottlenecks,
            "top_pagerank": sorted(
                [(s, m.pagerank) for s, m in result.metrics.items()],
                key=lambda x: -x[1],
            )[:5],
            "top_betweenness": sorted(
                [(s, m.betweenness_centrality) for s, m in result.metrics.items()],
                key=lambda x: -x[1],
            )[:5],
            "high_risk_symbols": sorted(result.risk_contagion.items(), key=lambda x: -x[1])[:5],
        }
