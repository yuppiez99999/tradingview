"""
细粒度任务分解工作流 (Fine-Grained Task Decomposition)
=====================================================

文献依据: #15 Toward Expert Investment Teams (Fine-Grained) (2026.02, ★★★★★)
论文: 将 20 位分析师的纯角色模拟重构为细粒度任务分解架构

核心思想
--------
旧架构: 每个分析师是一个单一角色 agent (warren_buffett_agent(state) → state)
新架构: 每个分析师分解为多个子任务, 通过任务图 (DAG) 编排

任务类型
--------
1. data_collection    — 数据收集 (基本面/技术面/情绪/宏观)
2. feature_extraction — 特征提取 (因子计算/指标生成)
3. signal_generation  — 信号生成 (买入/卖出/持有)
4. risk_assessment    — 风险评估 (VaR/波动率/相关性)
5. portfolio_construction — 组合构建 (权重优化)
6. debate             — 多空辩论 (多空对抗)
7. reflection         — 自反思 (决策复盘)

设计原则
--------
1. 旧接口兼容: 保留 20 分析师的 agent_func 接口
2. 任务可复用: 数据收集任务可被多个分析师共享
3. 并行执行: 无依赖的任务可并行
4. 任务图可视化: 支持 DAG 拓扑排序

使用示例
--------
    from quant_modules.ai_hedge_fund.fine_grained_workflow import FineGrainedWorkflow

    workflow = FineGrainedWorkflow()
    task_graph = workflow.build_analyst_tasks("warren_buffett")
    results = workflow.execute(task_graph, market_data)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("ai_hedge_fund.fine_grained")

# ============================================================
# 任务类型枚举
# ============================================================

class TaskType(str, Enum):
    """细粒度任务类型。"""
    DATA_COLLECTION = "data_collection"
    FEATURE_EXTRACTION = "feature_extraction"
    SIGNAL_GENERATION = "signal_generation"
    RISK_ASSESSMENT = "risk_assessment"
    PORTFOLIO_CONSTRUCTION = "portfolio_construction"
    DEBATE = "debate"
    REFLECTION = "reflection"


# ============================================================
# 任务节点
# ============================================================

@dataclass
class TaskNode:
    """单个任务节点 (DAG 中的一个节点)。

    Attributes:
        task_id: 唯一任务标识
        task_type: 任务类型
        analyst: 所属分析师 (如 "warren_buffett")
        dependencies: 依赖的任务 ID 列表
        executor: 任务执行函数 (接受 context 返回 result)
        result: 任务执行结果 (执行后填充)
        status: 任务状态 (pending/running/completed/failed)
    """
    task_id: str
    task_type: TaskType
    analyst: str
    dependencies: list[str] = field(default_factory=list)
    executor: Optional[Any] = None  # Callable[[dict], Any]
    result: Any = None
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "analyst": self.analyst,
            "dependencies": list(self.dependencies),
            "status": self.status,
        }


# ============================================================
# 任务图 (DAG)
# ============================================================

class TaskGraph:
    """任务图 — 有向无环图 (DAG)。

    支持拓扑排序, 依赖解析, 并行执行调度。
    """

    def __init__(self) -> None:
        self.nodes: dict[str, TaskNode] = {}

    def add_task(self, task: TaskNode) -> None:
        """添加任务节点。"""
        self.nodes[task.task_id] = task

    def get_task(self, task_id: str) -> Optional[TaskNode]:
        """获取任务节点。"""
        return self.nodes.get(task_id)

    def topological_sort(self) -> list[str]:
        """拓扑排序 (Kahn 算法)。

        Returns:
            按依赖顺序排列的 task_id 列表
        Raises:
            ValueError: 如果存在循环依赖
        """
        in_degree: dict[str, int] = {tid: 0 for tid in self.nodes}
        adj: dict[str, list[str]] = defaultdict(list)

        for task in self.nodes.values():
            for dep in task.dependencies:
                if dep in self.nodes:
                    adj[dep].append(task.task_id)
                    in_degree[task.task_id] += 1

        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        result: list[str] = []

        while queue:
            tid = queue.pop(0)
            result.append(tid)
            for neighbor in adj[tid]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(result) != len(self.nodes):
            raise ValueError("任务图存在循环依赖")
        return result

    def get_ready_tasks(self) -> list[str]:
        """获取当前可执行的任务 (依赖已完成的 pending 任务)。"""
        ready: list[str] = []
        for task in self.nodes.values():
            if task.status != "pending":
                continue
            if all(self.nodes[dep].status == "completed" for dep in task.dependencies if dep in self.nodes):
                ready.append(task.task_id)
        return ready

    def get_parallel_groups(self) -> list[list[str]]:
        """获取可并行执行的任务分组 (按层级)。"""
        groups: list[list[str]] = []
        remaining = set(self.nodes.keys())

        while remaining:
            current_group: list[str] = []
            for tid in list(remaining):
                task = self.nodes[tid]
                deps = [d for d in task.dependencies if d in remaining]
                if not deps:
                    current_group.append(tid)
            if not current_group:
                raise ValueError("任务图存在循环依赖")
            groups.append(current_group)
            remaining -= set(current_group)

        return groups

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_tasks": len(self.nodes),
            "tasks": {tid: task.to_dict() for tid, task in self.nodes.items()},
        }


# ============================================================
# 任务执行器 (默认实现)
# ============================================================

def _default_data_collection(context: dict[str, Any]) -> dict[str, Any]:
    """默认数据收集任务 (mock)。"""
    return {"data": "mock_market_data", "symbols": context.get("symbols", [])}


def _default_feature_extraction(context: dict[str, Any]) -> dict[str, Any]:
    """默认特征提取任务 (mock)。"""
    return {"features": ["pe_ratio", "pb_ratio", "roe", "momentum"]}


def _default_signal_generation(context: dict[str, Any]) -> dict[str, Any]:
    """默认信号生成任务 (mock)。"""
    return {"signal": "buy", "confidence": 0.7, "reasoning": "mock_signal"}


def _default_risk_assessment(context: dict[str, Any]) -> dict[str, Any]:
    """默认风险评估任务 (mock)。"""
    return {"risk_score": 0.3, "var_95": -0.05, "max_drawdown": -0.15}


def _default_portfolio_construction(context: dict[str, Any]) -> dict[str, Any]:
    """默认组合构建任务 (mock)。"""
    return {"weights": {"A": 0.4, "B": 0.3, "C": 0.3}, "sharpe": 1.2}


def _default_debate(context: dict[str, Any]) -> dict[str, Any]:
    """默认辩论任务 (mock)。"""
    return {"bull_case": "估值低", "bear_case": "增长放缓", "verdict": "buy"}


def _default_reflection(context: dict[str, Any]) -> dict[str, Any]:
    """默认自反思任务 (mock)。"""
    return {"reflection": "决策合理", "improvement": "可增加情绪因子"}


_DEFAULT_EXECUTORS = {
    TaskType.DATA_COLLECTION: _default_data_collection,
    TaskType.FEATURE_EXTRACTION: _default_feature_extraction,
    TaskType.SIGNAL_GENERATION: _default_signal_generation,
    TaskType.RISK_ASSESSMENT: _default_risk_assessment,
    TaskType.PORTFOLIO_CONSTRUCTION: _default_portfolio_construction,
    TaskType.DEBATE: _default_debate,
    TaskType.REFLECTION: _default_reflection,
}


# ============================================================
# 细粒度工作流
# ============================================================

# 20 位分析师列表 (与 utils/analysts.py 对齐)
ANALYST_NAMES = [
    "aswath_damodaran", "ben_graham", "bill_ackman", "cathie_wood",
    "charlie_munger", "michael_burry", "mohnish_pabrai", "nassim_taleb",
    "peter_lynch", "phil_fisher", "rakesh_jhunjhunwala", "stanley_druckenmiller",
    "warren_buffett", "fundamentals", "growth", "hedge",
    "sentiment", "news_sentiment", "technicals", "valuation",
]


class FineGrainedWorkflow:
    """细粒度任务分解工作流。

    将 20 位分析师的纯角色模拟重构为细粒度任务分解架构。
    每个分析师分解为: 数据收集 → 特征提取 → 信号生成 → 风险评估

    使用示例:
        workflow = FineGrainedWorkflow()
        task_graph = workflow.build_analyst_tasks("warren_buffett")
        results = workflow.execute(task_graph, {"symbols": ["000001.SZ"]})
    """

    def __init__(self, enable_reflection: bool = True,
                 enable_debate: bool = False) -> None:
        self.enable_reflection = enable_reflection
        self.enable_debate = enable_debate

    def build_analyst_tasks(self, analyst: str) -> TaskGraph:
        """为单个分析师构建任务图。

        任务分解:
        1. data_collection — 收集该分析师所需数据
        2. feature_extraction — 提取该分析师关注的特征
        3. signal_generation — 基于特征生成信号
        4. risk_assessment — 评估信号风险
        5. reflection (可选) — 自反思决策
        """
        graph = TaskGraph()

        # 1. 数据收集
        graph.add_task(TaskNode(
            task_id=f"{analyst}_data",
            task_type=TaskType.DATA_COLLECTION,
            analyst=analyst,
            executor=_DEFAULT_EXECUTORS[TaskType.DATA_COLLECTION],
        ))

        # 2. 特征提取 (依赖数据收集)
        graph.add_task(TaskNode(
            task_id=f"{analyst}_features",
            task_type=TaskType.FEATURE_EXTRACTION,
            analyst=analyst,
            dependencies=[f"{analyst}_data"],
            executor=_DEFAULT_EXECUTORS[TaskType.FEATURE_EXTRACTION],
        ))

        # 3. 信号生成 (依赖特征提取)
        graph.add_task(TaskNode(
            task_id=f"{analyst}_signal",
            task_type=TaskType.SIGNAL_GENERATION,
            analyst=analyst,
            dependencies=[f"{analyst}_features"],
            executor=_DEFAULT_EXECUTORS[TaskType.SIGNAL_GENERATION],
        ))

        # 4. 风险评估 (依赖信号生成)
        graph.add_task(TaskNode(
            task_id=f"{analyst}_risk",
            task_type=TaskType.RISK_ASSESSMENT,
            analyst=analyst,
            dependencies=[f"{analyst}_signal"],
            executor=_DEFAULT_EXECUTORS[TaskType.RISK_ASSESSMENT],
        ))

        # 5. 自反思 (可选, 依赖风险评估)
        if self.enable_reflection:
            graph.add_task(TaskNode(
                task_id=f"{analyst}_reflection",
                task_type=TaskType.REFLECTION,
                analyst=analyst,
                dependencies=[f"{analyst}_risk"],
                executor=_DEFAULT_EXECUTORS[TaskType.REFLECTION],
            ))

        return graph

    def build_team_tasks(self, analysts: list[str]) -> TaskGraph:
        """为多个分析师构建团队任务图 (含共享数据收集 + 辩论)。

        架构:
        - 共享数据收集 (一次收集, 多分析师复用)
        - 各分析师并行特征提取 + 信号生成
        - 多空辩论 (可选)
        - 组合构建 (依赖所有分析师信号)
        """
        graph = TaskGraph()

        # 0. 共享数据收集
        graph.add_task(TaskNode(
            task_id="shared_data",
            task_type=TaskType.DATA_COLLECTION,
            analyst="shared",
            executor=_DEFAULT_EXECUTORS[TaskType.DATA_COLLECTION],
        ))

        # 1. 各分析师并行任务
        for analyst in analysts:
            graph.add_task(TaskNode(
                task_id=f"{analyst}_features",
                task_type=TaskType.FEATURE_EXTRACTION,
                analyst=analyst,
                dependencies=["shared_data"],
                executor=_DEFAULT_EXECUTORS[TaskType.FEATURE_EXTRACTION],
            ))
            graph.add_task(TaskNode(
                task_id=f"{analyst}_signal",
                task_type=TaskType.SIGNAL_GENERATION,
                analyst=analyst,
                dependencies=[f"{analyst}_features"],
                executor=_DEFAULT_EXECUTORS[TaskType.SIGNAL_GENERATION],
            ))

        # 2. 多空辩论 (可选, 依赖所有信号)
        signal_deps = [f"{a}_signal" for a in analysts]
        if self.enable_debate and len(analysts) >= 2:
            graph.add_task(TaskNode(
                task_id="team_debate",
                task_type=TaskType.DEBATE,
                analyst="team",
                dependencies=signal_deps,
                executor=_DEFAULT_EXECUTORS[TaskType.DEBATE],
            ))
            portfolio_deps = ["team_debate"]
        else:
            portfolio_deps = signal_deps

        # 3. 组合构建 (依赖所有信号或辩论结果)
        graph.add_task(TaskNode(
            task_id="portfolio_construction",
            task_type=TaskType.PORTFOLIO_CONSTRUCTION,
            analyst="team",
            dependencies=portfolio_deps,
            executor=_DEFAULT_EXECUTORS[TaskType.PORTFOLIO_CONSTRUCTION],
        ))

        return graph

    def execute(self, graph: TaskGraph,
                context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """执行任务图 (按拓扑顺序)。

        Args:
            graph: 任务图
            context: 执行上下文 (市场数据等)

        Returns:
            各任务的执行结果 {task_id: result}
        """
        if context is None:
            context = {}

        order = graph.topological_sort()
        results: dict[str, Any] = {}

        for task_id in order:
            task = graph.nodes[task_id]
            task.status = "running"

            # 合并依赖结果到上下文
            task_context = {**context}
            for dep_id in task.dependencies:
                if dep_id in results:
                    task_context[dep_id] = results[dep_id]

            # 执行任务
            if task.executor is not None:
                try:
                    task.result = task.executor(task_context)
                    task.status = "completed"
                    results[task_id] = task.result
                except (ValueError, KeyError, TypeError, AttributeError,
                        OSError, RuntimeError) as e:
                    task.status = "failed"
                    task.result = {"error": str(e)}
                    results[task_id] = task.result
                    logger.warning(f"任务 {task_id} 执行失败: {e}")
            else:
                task.status = "completed"
                results[task_id] = None

        return results

    def execute_parallel_groups(self, graph: TaskGraph,
                                context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """按并行分组执行任务图。

        返回各任务的执行结果, 同组任务可并行执行。
        """
        if context is None:
            context = {}

        groups = graph.get_parallel_groups()
        results: dict[str, Any] = {}

        for group in groups:
            for task_id in group:
                task = graph.nodes[task_id]
                task.status = "running"

                task_context = {**context}
                for dep_id in task.dependencies:
                    if dep_id in results:
                        task_context[dep_id] = results[dep_id]

                if task.executor is not None:
                    try:
                        task.result = task.executor(task_context)
                        task.status = "completed"
                        results[task_id] = task.result
                    except (ValueError, KeyError, TypeError, AttributeError,
                            OSError, RuntimeError) as e:
                        task.status = "failed"
                        task.result = {"error": str(e)}
                        results[task_id] = task.result
                else:
                    task.status = "completed"
                    results[task_id] = None

        return results

    def get_task_summary(self, graph: TaskGraph) -> dict[str, Any]:
        """获取任务图摘要。"""
        type_counts: dict[str, int] = defaultdict(int)
        for task in graph.nodes.values():
            type_counts[task.task_type.value] += 1

        return {
            "n_tasks": len(graph.nodes),
            "n_types": len(type_counts),
            "type_counts": dict(type_counts),
            "parallel_groups": len(graph.get_parallel_groups()),
        }


# ============================================================
# 旧接口兼容层
# ============================================================

def create_fine_grained_agent(analyst_name: str) -> Any:
    """创建细粒度分析师 agent (兼容旧 agent_func 接口)。

    旧接口: agent_func(state) → state
    新接口: 内部使用任务图, 对外保持相同签名

    Returns:
        兼容旧接口的 agent 函数
    """
    workflow = FineGrainedWorkflow()

    def agent_func(state: dict[str, Any]) -> dict[str, Any]:
        """兼容旧接口的 agent 函数。"""
        graph = workflow.build_analyst_tasks(analyst_name)
        context = {"state": state, "symbols": state.get("data", {}).get("symbols", [])}
        results = workflow.execute(graph, context)

        # 将结果合并到 state
        analyst_data = {
            "analyst": analyst_name,
            "signal": results.get(f"{analyst_name}_signal", {}),
            "risk": results.get(f"{analyst_name}_risk", {}),
            "features": results.get(f"{analyst_name}_features", {}),
        }
        if "analyst_data" not in state:
            state["analyst_data"] = {}
        state["analyst_data"][analyst_name] = analyst_data
        return state

    return agent_func


# ============================================================
# CLI 入口
# ============================================================

def main() -> None:
    """CLI 入口: 演示细粒度任务分解工作流。"""
    print("=" * 60)
    print("细粒度任务分解工作流 (Fine-Grained Task Decomposition)")
    print("文献: #15 Toward Expert Investment Teams (2026.02)")
    print("=" * 60)

    workflow = FineGrainedWorkflow(enable_reflection=True, enable_debate=True)

    # 1. 单分析师任务分解
    print("\n--- 1. 单分析师任务分解 (warren_buffett) ---")
    graph = workflow.build_analyst_tasks("warren_buffett")
    summary = workflow.get_task_summary(graph)
    print(f"任务数: {summary['n_tasks']}")
    print(f"任务类型: {summary['type_counts']}")
    print(f"并行分组: {summary['parallel_groups']}")

    order = graph.topological_sort()
    print(f"执行顺序: {' → '.join(order)}")

    results = workflow.execute(graph, {"symbols": ["000001.SZ"]})
    print("\n执行结果:")
    for tid, result in results.items():
        print(f"  {tid}: {result}")

    # 2. 团队任务分解
    print("\n--- 2. 团队任务分解 (3 分析师 + 辩论) ---")
    team = ["warren_buffett", "ben_graham", "cathie_wood"]
    team_graph = workflow.build_team_tasks(team)
    team_summary = workflow.get_task_summary(team_graph)
    print(f"任务数: {team_summary['n_tasks']}")
    print(f"任务类型: {team_summary['type_counts']}")
    print(f"并行分组: {team_summary['parallel_groups']}")

    groups = team_graph.get_parallel_groups()
    print("\n并行执行分组:")
    for i, group in enumerate(groups, 1):
        print(f"  层 {i}: {group}")

    team_results = workflow.execute(team_graph, {"symbols": ["000001.SZ"]})
    print(f"\n组合构建结果: {team_results.get('portfolio_construction')}")

    # 3. 旧接口兼容
    print("\n--- 3. 旧接口兼容 ---")
    agent = create_fine_grained_agent("warren_buffett")
    state = {"data": {"symbols": ["000001.SZ"]}}
    new_state = agent(state)
    print(f"分析师: {new_state['analyst_data']['warren_buffett']['analyst']}")
    print(f"信号: {new_state['analyst_data']['warren_buffett']['signal']}")


if __name__ == "__main__":
    main()
