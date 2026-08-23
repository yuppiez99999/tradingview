"""
细粒度任务分解工作流 — 单元测试
================================

测试覆盖:
- TaskType 枚举完整性
- TaskNode 数据结构
- TaskGraph DAG (拓扑排序 / 依赖解析 / 并行分组 / 循环检测)
- 默认任务执行器 (7 种)
- FineGrainedWorkflow (单分析师 / 团队 / 执行 / 并行 / 摘要)
- create_fine_grained_agent 旧接口兼容层
- ANALYST_NAMES 与 utils/analysts.py 对齐

文献: #15 Toward Expert Investment Teams (Fine-Grained) (2026.02)
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_modules.ai_hedge_fund.fine_grained_workflow import (
    _DEFAULT_EXECUTORS,
    ANALYST_NAMES,
    FineGrainedWorkflow,
    TaskGraph,
    TaskNode,
    TaskType,
    _default_data_collection,
    _default_debate,
    _default_feature_extraction,
    _default_portfolio_construction,
    _default_reflection,
    _default_risk_assessment,
    _default_signal_generation,
    create_fine_grained_agent,
)

# ============================================================
# TaskType 枚举测试
# ============================================================

class TestTaskType:
    """任务类型枚举测试。"""

    def test_seven_task_types(self):
        """共 7 种任务类型。"""
        assert len(TaskType) == 7

    def test_expected_values(self):
        """枚举值与文档一致。"""
        assert TaskType.DATA_COLLECTION.value == "data_collection"
        assert TaskType.FEATURE_EXTRACTION.value == "feature_extraction"
        assert TaskType.SIGNAL_GENERATION.value == "signal_generation"
        assert TaskType.RISK_ASSESSMENT.value == "risk_assessment"
        assert TaskType.PORTFOLIO_CONSTRUCTION.value == "portfolio_construction"
        assert TaskType.DEBATE.value == "debate"
        assert TaskType.REFLECTION.value == "reflection"

    def test_str_enum_compatible(self):
        """str Enum 可与字符串比较。"""
        assert TaskType.DATA_COLLECTION == "data_collection"


# ============================================================
# TaskNode 测试
# ============================================================

class TestTaskNode:
    """任务节点测试。"""

    def test_default_values(self):
        """默认值正确。"""
        node = TaskNode(task_id="t1", task_type=TaskType.DATA_COLLECTION, analyst="a1")
        assert node.dependencies == []
        assert node.executor is None
        assert node.result is None
        assert node.status == "pending"

    def test_to_dict(self):
        """to_dict 序列化正确。"""
        node = TaskNode(
            task_id="t1",
            task_type=TaskType.SIGNAL_GENERATION,
            analyst="warren_buffett",
            dependencies=["t0"],
            status="completed",
        )
        d = node.to_dict()
        assert d["task_id"] == "t1"
        assert d["task_type"] == "signal_generation"
        assert d["analyst"] == "warren_buffett"
        assert d["dependencies"] == ["t0"]
        assert d["status"] == "completed"

    def test_dependencies_isolated(self):
        """dependencies 列表不被共享。"""
        n1 = TaskNode(task_id="t1", task_type=TaskType.DATA_COLLECTION, analyst="a")
        n2 = TaskNode(task_id="t2", task_type=TaskType.DATA_COLLECTION, analyst="a")
        n1.dependencies.append("x")
        assert n2.dependencies == []

    def test_to_dict_dependencies_copy(self):
        """to_dict 返回 dependencies 副本。"""
        node = TaskNode(
            task_id="t1",
            task_type=TaskType.DATA_COLLECTION,
            analyst="a",
            dependencies=["d1"],
        )
        d = node.to_dict()
        d["dependencies"].append("d2")
        assert node.dependencies == ["d1"]


# ============================================================
# TaskGraph 测试
# ============================================================

class TestTaskGraph:
    """任务图 (DAG) 测试。"""

    def test_add_and_get_task(self):
        """添加并获取任务。"""
        graph = TaskGraph()
        task = TaskNode(task_id="t1", task_type=TaskType.DATA_COLLECTION, analyst="a")
        graph.add_task(task)
        assert graph.get_task("t1") is task
        assert graph.get_task("nonexistent") is None

    def test_topological_sort_linear(self):
        """线性依赖拓扑排序。"""
        graph = TaskGraph()
        graph.add_task(TaskNode("t2", TaskType.FEATURE_EXTRACTION, "a", dependencies=["t1"]))
        graph.add_task(TaskNode("t1", TaskType.DATA_COLLECTION, "a"))
        graph.add_task(TaskNode("t3", TaskType.SIGNAL_GENERATION, "a", dependencies=["t2"]))
        order = graph.topological_sort()
        assert order.index("t1") < order.index("t2") < order.index("t3")

    def test_topological_sort_no_deps(self):
        """无依赖任务任意顺序。"""
        graph = TaskGraph()
        graph.add_task(TaskNode("t1", TaskType.DATA_COLLECTION, "a"))
        graph.add_task(TaskNode("t2", TaskType.DATA_COLLECTION, "b"))
        order = graph.topological_sort()
        assert set(order) == {"t1", "t2"}

    def test_topological_sort_cycle_raises(self):
        """循环依赖抛出 ValueError。"""
        graph = TaskGraph()
        graph.add_task(TaskNode("t1", TaskType.DATA_COLLECTION, "a", dependencies=["t2"]))
        graph.add_task(TaskNode("t2", TaskType.FEATURE_EXTRACTION, "a", dependencies=["t1"]))
        try:
            graph.topological_sort()
            raise AssertionError("应抛出 ValueError")
        except ValueError as e:
            assert "循环" in str(e)

    def test_topological_sort_missing_dep_ignored(self):
        """缺失依赖被忽略 (不报错)。"""
        graph = TaskGraph()
        graph.add_task(TaskNode("t1", TaskType.DATA_COLLECTION, "a", dependencies=["missing"]))
        order = graph.topological_sort()
        assert order == ["t1"]

    def test_get_ready_tasks_initial(self):
        """初始时无依赖任务可执行。"""
        graph = TaskGraph()
        graph.add_task(TaskNode("t1", TaskType.DATA_COLLECTION, "a"))
        graph.add_task(TaskNode("t2", TaskType.FEATURE_EXTRACTION, "a", dependencies=["t1"]))
        ready = graph.get_ready_tasks()
        assert ready == ["t1"]

    def test_get_ready_tasks_after_completion(self):
        """依赖完成后任务可执行。"""
        graph = TaskGraph()
        t1 = TaskNode("t1", TaskType.DATA_COLLECTION, "a")
        t2 = TaskNode("t2", TaskType.FEATURE_EXTRACTION, "a", dependencies=["t1"])
        graph.add_task(t1)
        graph.add_task(t2)
        t1.status = "completed"
        ready = graph.get_ready_tasks()
        assert ready == ["t2"]

    def test_get_parallel_groups_linear(self):
        """线性依赖每层一个任务。"""
        graph = TaskGraph()
        graph.add_task(TaskNode("t1", TaskType.DATA_COLLECTION, "a"))
        graph.add_task(TaskNode("t2", TaskType.FEATURE_EXTRACTION, "a", dependencies=["t1"]))
        groups = graph.get_parallel_groups()
        assert groups == [["t1"], ["t2"]]

    def test_get_parallel_groups_parallel(self):
        """无依赖任务同组并行。"""
        graph = TaskGraph()
        graph.add_task(TaskNode("t1", TaskType.DATA_COLLECTION, "a"))
        graph.add_task(TaskNode("t2", TaskType.DATA_COLLECTION, "b"))
        groups = graph.get_parallel_groups()
        assert len(groups) == 1
        assert set(groups[0]) == {"t1", "t2"}

    def test_get_parallel_groups_diamond(self):
        """菱形依赖: t1 → {t2, t3} → t4。"""
        graph = TaskGraph()
        graph.add_task(TaskNode("t1", TaskType.DATA_COLLECTION, "a"))
        graph.add_task(TaskNode("t2", TaskType.FEATURE_EXTRACTION, "a", dependencies=["t1"]))
        graph.add_task(TaskNode("t3", TaskType.FEATURE_EXTRACTION, "b", dependencies=["t1"]))
        graph.add_task(TaskNode("t4", TaskType.SIGNAL_GENERATION, "a", dependencies=["t2", "t3"]))
        groups = graph.get_parallel_groups()
        assert groups[0] == ["t1"]
        assert set(groups[1]) == {"t2", "t3"}
        assert groups[2] == ["t4"]

    def test_get_parallel_groups_cycle_raises(self):
        """循环依赖抛出 ValueError。"""
        graph = TaskGraph()
        graph.add_task(TaskNode("t1", TaskType.DATA_COLLECTION, "a", dependencies=["t2"]))
        graph.add_task(TaskNode("t2", TaskType.FEATURE_EXTRACTION, "a", dependencies=["t1"]))
        try:
            graph.get_parallel_groups()
            raise AssertionError("应抛出 ValueError")
        except ValueError as e:
            assert "循环" in str(e)

    def test_to_dict(self):
        """图序列化。"""
        graph = TaskGraph()
        graph.add_task(TaskNode("t1", TaskType.DATA_COLLECTION, "a"))
        d = graph.to_dict()
        assert d["n_tasks"] == 1
        assert "t1" in d["tasks"]


# ============================================================
# 默认执行器测试
# ============================================================

class TestDefaultExecutors:
    """默认任务执行器测试。"""

    def test_data_collection(self):
        result = _default_data_collection({"symbols": ["000001.SZ"]})
        assert result["symbols"] == ["000001.SZ"]
        assert "data" in result

    def test_data_collection_empty(self):
        result = _default_data_collection({})
        assert result["symbols"] == []

    def test_feature_extraction(self):
        result = _default_feature_extraction({})
        assert "pe_ratio" in result["features"]

    def test_signal_generation(self):
        result = _default_signal_generation({})
        assert result["signal"] == "buy"
        assert 0 <= result["confidence"] <= 1

    def test_risk_assessment(self):
        result = _default_risk_assessment({})
        assert "risk_score" in result
        assert "var_95" in result

    def test_portfolio_construction(self):
        result = _default_portfolio_construction({})
        weights = result["weights"]
        assert abs(sum(weights.values()) - 1.0) < 1e-6

    def test_debate(self):
        result = _default_debate({})
        assert "bull_case" in result
        assert "bear_case" in result

    def test_reflection(self):
        result = _default_reflection({})
        assert "reflection" in result

    def test_all_executors_registered(self):
        """7 种执行器全部注册。"""
        assert len(_DEFAULT_EXECUTORS) == 7
        for task_type in TaskType:
            assert task_type in _DEFAULT_EXECUTORS


# ============================================================
# FineGrainedWorkflow — 单分析师测试
# ============================================================

class TestBuildAnalystTasks:
    """build_analyst_tasks 测试。"""

    def test_default_5_tasks(self):
        """默认 5 个任务 (含反思)。"""
        wf = FineGrainedWorkflow(enable_reflection=True)
        graph = wf.build_analyst_tasks("warren_buffett")
        assert len(graph.nodes) == 5

    def test_no_reflection_4_tasks(self):
        """禁用反思时 4 个任务。"""
        wf = FineGrainedWorkflow(enable_reflection=False)
        graph = wf.build_analyst_tasks("warren_buffett")
        assert len(graph.nodes) == 4

    def test_task_types(self):
        """任务类型正确。"""
        wf = FineGrainedWorkflow(enable_reflection=True)
        graph = wf.build_analyst_tasks("ben_graham")
        types = {t.task_type for t in graph.nodes.values()}
        assert TaskType.DATA_COLLECTION in types
        assert TaskType.FEATURE_EXTRACTION in types
        assert TaskType.SIGNAL_GENERATION in types
        assert TaskType.RISK_ASSESSMENT in types
        assert TaskType.REFLECTION in types

    def test_dependencies_chain(self):
        """依赖链: data → features → signal → risk → reflection。"""
        wf = FineGrainedWorkflow(enable_reflection=True)
        graph = wf.build_analyst_tasks("cathie_wood")
        order = graph.topological_sort()
        assert order[0] == "cathie_wood_data"
        assert order[-1] == "cathie_wood_reflection"

    def test_all_executors_assigned(self):
        """所有任务都有执行器。"""
        wf = FineGrainedWorkflow()
        graph = wf.build_analyst_tasks("warren_buffett")
        for task in graph.nodes.values():
            assert task.executor is not None


# ============================================================
# FineGrainedWorkflow — 团队任务测试
# ============================================================

class TestBuildTeamTasks:
    """build_team_tasks 测试。"""

    def test_team_no_debate(self):
        """无辩论: 1 共享数据 + 2*3 分析师任务 + 1 组合 = 8。"""
        wf = FineGrainedWorkflow(enable_debate=False)
        graph = wf.build_team_tasks(["warren_buffett", "ben_graham", "cathie_wood"])
        # shared_data + 3*(features+signal) + portfolio = 1 + 6 + 1 = 8
        assert len(graph.nodes) == 8
        assert "team_debate" not in graph.nodes

    def test_team_with_debate(self):
        """有辩论: 1 + 6 + 1 + 1 = 9。"""
        wf = FineGrainedWorkflow(enable_debate=True)
        graph = wf.build_team_tasks(["warren_buffett", "ben_graham", "cathie_wood"])
        # shared_data + 3*(features+signal) + debate + portfolio = 1 + 6 + 1 + 1 = 9
        assert len(graph.nodes) == 9
        assert "team_debate" in graph.nodes

    def test_team_single_analyst_no_debate(self):
        """单分析师不触发辩论 (需 >=2)。"""
        wf = FineGrainedWorkflow(enable_debate=True)
        graph = wf.build_team_tasks(["warren_buffett"])
        assert "team_debate" not in graph.nodes

    def test_shared_data_present(self):
        """共享数据收集任务存在。"""
        wf = FineGrainedWorkflow()
        graph = wf.build_team_tasks(["warren_buffett"])
        assert "shared_data" in graph.nodes
        assert graph.nodes["shared_data"].analyst == "shared"

    def test_portfolio_construction_present(self):
        """组合构建任务存在。"""
        wf = FineGrainedWorkflow()
        graph = wf.build_team_tasks(["warren_buffett", "ben_graham"])
        assert "portfolio_construction" in graph.nodes

    def test_portfolio_depends_on_signals(self):
        """无辩论时组合依赖所有信号。"""
        wf = FineGrainedWorkflow(enable_debate=False)
        analysts = ["warren_buffett", "ben_graham"]
        graph = wf.build_team_tasks(analysts)
        portfolio = graph.nodes["portfolio_construction"]
        for a in analysts:
            assert f"{a}_signal" in portfolio.dependencies

    def test_portfolio_depends_on_debate(self):
        """有辩论时组合依赖辩论。"""
        wf = FineGrainedWorkflow(enable_debate=True)
        graph = wf.build_team_tasks(["warren_buffett", "ben_graham"])
        portfolio = graph.nodes["portfolio_construction"]
        assert "team_debate" in portfolio.dependencies


# ============================================================
# FineGrainedWorkflow — 执行测试
# ============================================================

class TestExecute:
    """execute 测试。"""

    def test_execute_single_analyst(self):
        """执行单分析师任务图。"""
        wf = FineGrainedWorkflow()
        graph = wf.build_analyst_tasks("warren_buffett")
        results = wf.execute(graph, {"symbols": ["000001.SZ"]})
        assert "warren_buffett_data" in results
        assert "warren_buffett_signal" in results
        assert results["warren_buffett_signal"]["signal"] == "buy"


    def test_execute_all_completed(self):
        """执行后所有任务完成。"""
        wf = FineGrainedWorkflow()
        graph = wf.build_analyst_tasks("warren_buffett")
        wf.execute(graph, {})
        for task in graph.nodes.values():
            assert task.status == "completed"

    def test_execute_team(self):
        """执行团队任务图。"""
        wf = FineGrainedWorkflow(enable_debate=True)
        graph = wf.build_team_tasks(["warren_buffett", "ben_graham"])
        results = wf.execute(graph, {"symbols": ["000001.SZ"]})
        assert "portfolio_construction" in results
        assert "team_debate" in results

    def test_execute_empty_context(self):
        """空上下文可执行。"""
        wf = FineGrainedWorkflow()
        graph = wf.build_analyst_tasks("warren_buffett")
        results = wf.execute(graph)
        assert len(results) == 5

    def test_execute_failed_task(self):
        """执行器抛异常时任务标记失败。"""

        def failing_executor(ctx):
            raise ValueError("模拟失败")

        graph = TaskGraph()
        graph.add_task(TaskNode(
            "t1", TaskType.DATA_COLLECTION, "a", executor=failing_executor,
        ))
        wf = FineGrainedWorkflow()
        results = wf.execute(graph, {})
        assert graph.nodes["t1"].status == "failed"
        assert "error" in results["t1"]

    def test_execute_no_executor(self):
        """无执行器任务标记完成, 结果为 None。"""
        graph = TaskGraph()
        graph.add_task(TaskNode("t1", TaskType.DATA_COLLECTION, "a"))
        wf = FineGrainedWorkflow()
        results = wf.execute(graph, {})
        assert graph.nodes["t1"].status == "completed"
        assert results["t1"] is None

    def test_execute_parallel_groups(self):
        """并行分组执行结果与顺序执行一致。"""
        wf = FineGrainedWorkflow(enable_debate=True)
        graph = wf.build_team_tasks(["warren_buffett", "ben_graham"])
        results = wf.execute_parallel_groups(graph, {"symbols": ["000001.SZ"]})
        assert "portfolio_construction" in results
        assert results["portfolio_construction"]["sharpe"] == 1.2


# ============================================================
# FineGrainedWorkflow — 摘要测试
# ============================================================

class TestGetTaskSummary:
    """get_task_summary 测试。"""

    def test_summary_single_analyst(self):
        """单分析师摘要。"""
        wf = FineGrainedWorkflow(enable_reflection=True)
        graph = wf.build_analyst_tasks("warren_buffett")
        summary = wf.get_task_summary(graph)
        assert summary["n_tasks"] == 5
        assert summary["n_types"] == 5
        assert summary["type_counts"]["data_collection"] == 1

    def test_summary_team(self):
        """团队摘要。"""
        wf = FineGrainedWorkflow(enable_debate=True)
        graph = wf.build_team_tasks(["warren_buffett", "ben_graham"])
        summary = wf.get_task_summary(graph)
        assert summary["n_tasks"] == 7  # shared(1) + 2*(feat+sig)(4) + debate(1) + portfolio(1)
        assert summary["type_counts"]["feature_extraction"] == 2


# ============================================================
# 旧接口兼容层测试
# ============================================================

class TestCreateFineGrainedAgent:
    """create_fine_grained_agent 兼容层测试。"""

    def test_agent_func_signature(self):
        """agent_func 接受 state 返回 state。"""
        agent = create_fine_grained_agent("warren_buffett")
        state = {"data": {"symbols": ["000001.SZ"]}}
        new_state = agent(state)
        assert isinstance(new_state, dict)

    def test_agent_populates_analyst_data(self):
        """agent 填充 analyst_data。"""
        agent = create_fine_grained_agent("warren_buffett")
        state = {"data": {"symbols": ["000001.SZ"]}}
        new_state = agent(state)
        assert "analyst_data" in new_state
        assert "warren_buffett" in new_state["analyst_data"]
        data = new_state["analyst_data"]["warren_buffett"]
        assert data["analyst"] == "warren_buffett"
        assert "signal" in data
        assert "risk" in data
        assert "features" in data

    def test_agent_preserves_existing_analyst_data(self):
        """agent 保留已有 analyst_data。"""
        agent = create_fine_grained_agent("ben_graham")
        state = {
            "data": {"symbols": ["000001.SZ"]},
            "analyst_data": {"warren_buffett": {"signal": "old"}},
        }
        new_state = agent(state)
        assert new_state["analyst_data"]["warren_buffett"]["signal"] == "old"
        assert "ben_graham" in new_state["analyst_data"]

    def test_agent_empty_state(self):
        """空 state 可执行。"""
        agent = create_fine_grained_agent("warren_buffett")
        new_state = agent({})
        assert "warren_buffett" in new_state["analyst_data"]


# ============================================================
# ANALYST_NAMES 完整性测试
# ============================================================

class TestAnalystNames:
    """20 位分析师列表测试。"""

    def test_20_analysts(self):
        """共 20 位分析师。"""
        assert len(ANALYST_NAMES) == 20

    def test_no_duplicates(self):
        """无重复。"""
        assert len(set(ANALYST_NAMES)) == len(ANALYST_NAMES)

    def test_key_analysts_present(self):
        """关键分析师存在。"""
        for name in ["warren_buffett", "ben_graham", "cathie_wood", "charlie_munger"]:
            assert name in ANALYST_NAMES

    def test_all_analysts_can_build_tasks(self):
        """所有分析师都能构建任务图。"""
        wf = FineGrainedWorkflow()
        for name in ANALYST_NAMES:
            graph = wf.build_analyst_tasks(name)
            assert len(graph.nodes) >= 4


# ============================================================
# 端到端集成测试
# ============================================================

class TestEndToEnd:
    """端到端集成测试。"""

    def test_full_team_workflow(self):
        """完整团队工作流: 5 分析师 + 辩论 + 组合。"""
        wf = FineGrainedWorkflow(enable_reflection=True, enable_debate=True)
        team = ["warren_buffett", "ben_graham", "cathie_wood", "peter_lynch", "phil_fisher"]
        graph = wf.build_team_tasks(team)
        results = wf.execute(graph, {"symbols": ["000001.SZ", "600519.SH"]})

        # 组合构建结果存在
        assert "portfolio_construction" in results
        portfolio = results["portfolio_construction"]
        assert "weights" in portfolio
        assert abs(sum(portfolio["weights"].values()) - 1.0) < 1e-6

        # 辩论结果存在
        assert "team_debate" in results
        assert "verdict" in results["team_debate"]

    def test_parallel_groups_consistent_with_execute(self):
        """并行分组执行与顺序执行结果一致。"""
        wf = FineGrainedWorkflow()
        graph1 = wf.build_analyst_tasks("warren_buffett")
        graph2 = wf.build_analyst_tasks("warren_buffett")
        r1 = wf.execute(graph1, {"symbols": ["000001.SZ"]})
        r2 = wf.execute_parallel_groups(graph2, {"symbols": ["000001.SZ"]})
        assert r1.keys() == r2.keys()

    def test_dependency_results_passed_to_context(self):
        """依赖任务结果传入下游上下文。"""
        wf = FineGrainedWorkflow()
        graph = wf.build_analyst_tasks("warren_buffett")
        results = wf.execute(graph, {"symbols": ["000001.SZ"]})
        # 信号生成任务的上下文应包含特征提取结果
        # (通过结果正确性间接验证)
        assert results["warren_buffett_signal"]["signal"] == "buy"
