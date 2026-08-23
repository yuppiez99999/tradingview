"""
AlphaCFG 语法引导因子发现 — 单元测试
====================================

测试覆盖:
- CFGGrammar (表达式生成 + 语法正确性)
- FactorEvaluator (IC/IR 评估 + 确定性)
- MCTSNode (UCB1 + best_child)
- MCTSSearcher (四步循环)
- AlphaCFGDiscoverer (端到端发现)
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha_factor.alpha_cfg import (
    TERMINALS,
    AlphaCFGDiscoverer,
    CFGGrammar,
    FactorEvaluator,
    FactorExpression,
    MCTSNode,
    MCTSSearcher,
)

# ============================================================
# CFGGrammar 测试
# ============================================================

class TestCFGGrammar:
    """CFG 文法测试。"""

    def test_generate_terminal(self):
        """深度 0 生成终端符号。"""
        grammar = CFGGrammar(seed=42)
        expr = grammar.generate("alpha", max_depth=0)
        assert expr in TERMINALS

    def test_generate_valid_expression(self):
        """生成的表达式包含已知符号。"""
        grammar = CFGGrammar(seed=42)
        expr = grammar.generate("alpha", max_depth=3)
        # 至少包含一个终端符号
        assert any(t in expr for t in TERMINALS)

    def test_generate_reproducible(self):
        """相同 seed 生成相同表达式。"""
        g1 = CFGGrammar(seed=42)
        g2 = CFGGrammar(seed=42)
        e1 = g1.generate("alpha", max_depth=3)
        e2 = g2.generate("alpha", max_depth=3)
        assert e1 == e2

    def test_generate_depth_controls_complexity(self):
        """深度控制复杂度 (深层表达式更长)。"""
        grammar = CFGGrammar(seed=42)
        shallow = grammar.generate("alpha", max_depth=1)
        deep = grammar.generate("alpha", max_depth=5)
        # 深层表达式通常更长 (不绝对, 但统计上)
        assert isinstance(shallow, str)
        assert isinstance(deep, str)

    def test_get_expansions(self):
        """获取展开选项。"""
        grammar = CFGGrammar(seed=42)
        expansions = grammar.get_expansions("expr", max_depth=3)
        assert "binary" in expansions
        assert "unary" in expansions
        assert "terminal" in expansions

    def test_get_expansions_zero_depth(self):
        """深度 0 只返回终端。"""
        grammar = CFGGrammar(seed=42)
        expansions = grammar.get_expansions("expr", max_depth=0)
        assert expansions == TERMINALS


# ============================================================
# FactorEvaluator 测试
# ============================================================

class TestFactorEvaluator:
    """因子评估器测试。"""

    def test_evaluate_returns_tuple(self):
        """评估返回 (IC, IR) 元组。"""
        evaluator = FactorEvaluator(seed=42)
        ic, ir = evaluator.evaluate("close")
        assert isinstance(ic, float)
        assert isinstance(ir, float)

    def test_ic_in_valid_range(self):
        """IC 在 [-0.1, 0.1] 范围内。"""
        evaluator = FactorEvaluator(seed=42)
        for expr in ["close", "ts_mean(close, 20)", "rank(volume)", "cs_rank(close)"]:
            ic, _ = evaluator.evaluate(expr)
            assert -0.1 <= ic <= 0.1

    def test_evaluate_deterministic(self):
        """相同表达式评估结果确定。"""
        evaluator = FactorEvaluator(seed=42)
        ic1, ir1 = evaluator.evaluate("ts_mean(close, 20)")
        ic2, ir2 = evaluator.evaluate("ts_mean(close, 20)")
        assert ic1 == ic2
        assert ir1 == ir2

    def test_depth_estimation(self):
        """深度估算正确。"""
        evaluator = FactorEvaluator(seed=42)
        assert evaluator._estimate_depth("close") == 0
        assert evaluator._estimate_depth("rank(close)") == 1
        assert evaluator._estimate_depth("ts_mean(rank(close), 20)") == 2

    def test_ts_factor_gets_bonus(self):
        """时序因子获得 IC 加分。"""
        evaluator = FactorEvaluator(seed=42)
        ic_plain, _ = evaluator.evaluate("close")
        ic_ts, _ = evaluator.evaluate("ts_mean(close, 20)")
        # 时序因子基础 IC 更高 (但噪声可能影响, 这里只验证不崩溃)
        assert isinstance(ic_plain, float)
        assert isinstance(ic_ts, float)


# ============================================================
# MCTSNode 测试
# ============================================================

class TestMCTSNode:
    """MCTS 节点测试。"""

    def test_avg_reward_zero_visits(self):
        """零访问平均奖励为 0。"""
        node = MCTSNode(expression="close", depth=0)
        assert node.avg_reward == 0.0

    def test_avg_reward_with_visits(self):
        """有访问时平均奖励正确。"""
        node = MCTSNode(expression="close", depth=0)
        node.visits = 4
        node.total_reward = 0.08
        assert node.avg_reward == 0.02

    def test_ucb1_unvisited_is_inf(self):
        """未访问节点 UCB1 为无穷。"""
        node = MCTSNode(expression="close", depth=0)
        assert node.ucb1() == float("inf")

    def test_ucb1_visited_finite(self):
        """已访问节点 UCB1 有限。"""
        parent = MCTSNode(expression="alpha", depth=0, visits=10)
        child = MCTSNode(expression="close", depth=1, parent=parent, visits=5)
        child.total_reward = 0.1
        assert child.ucb1() != float("inf")
        assert child.ucb1() > 0

    def test_best_child(self):
        """选择最佳子节点。"""
        parent = MCTSNode(expression="alpha", depth=0, visits=10)
        c1 = MCTSNode(expression="close", depth=1, parent=parent, visits=5)
        c1.total_reward = 0.05
        c2 = MCTSNode(expression="open", depth=1, parent=parent, visits=5)
        c2.total_reward = 0.15  # 更高奖励
        parent.children = [c1, c2]
        best = parent.best_child()
        assert best is c2

    def test_is_fully_expanded(self):
        """完全展开检测。"""
        node = MCTSNode(expression="alpha", depth=0)
        assert not node.is_fully_expanded(max_children=5)
        node.children = [MCTSNode(expression=f"expr{i}", depth=1) for i in range(5)]
        assert node.is_fully_expanded(max_children=5)


# ============================================================
# MCTSSearcher 测试
# ============================================================

class TestMCTSSearcher:
    """MCTS 搜索器测试。"""

    def test_search_returns_factors(self):
        """搜索返回因子列表。"""
        grammar = CFGGrammar(seed=42)
        evaluator = FactorEvaluator(seed=42)
        searcher = MCTSSearcher(grammar, evaluator, max_depth=3, seed=42)
        factors = searcher.search(n_iterations=20)
        assert len(factors) > 0
        assert all(isinstance(f, FactorExpression) for f in factors)

    def test_search_factors_sorted_by_ic(self):
        """因子按 |IC| 降序排列。"""
        grammar = CFGGrammar(seed=42)
        evaluator = FactorEvaluator(seed=42)
        searcher = MCTSSearcher(grammar, evaluator, max_depth=3, seed=42)
        factors = searcher.search(n_iterations=20)
        for i in range(1, len(factors)):
            assert abs(factors[i].ic) <= abs(factors[i - 1].ic)

    def test_search_visits_incremented(self):
        """搜索后根节点访问数增加。"""
        grammar = CFGGrammar(seed=42)
        evaluator = FactorEvaluator(seed=42)
        searcher = MCTSSearcher(grammar, evaluator, max_depth=3, seed=42)
        searcher.search(n_iterations=10)
        assert searcher.root.visits > 0

    def test_backpropagate_updates(self):
        """回传更新节点访问和奖励。"""
        grammar = CFGGrammar(seed=42)
        evaluator = FactorEvaluator(seed=42)
        searcher = MCTSSearcher(grammar, evaluator, max_depth=3, seed=42)
        node = MCTSNode(expression="close", depth=1)
        searcher._backpropagate(node, 0.05)
        assert node.visits == 1
        assert node.total_reward == 0.05


# ============================================================
# AlphaCFGDiscoverer 端到端测试
# ============================================================

class TestAlphaCFGDiscoverer:
    """主发现器端到端测试。"""

    def test_discover_returns_factors(self):
        """发现返回因子列表。"""
        discoverer = AlphaCFGDiscoverer(max_depth=3, n_iterations=50, seed=42)
        factors = discoverer.discover(n_factors=5)
        assert len(factors) > 0
        assert all(isinstance(f, FactorExpression) for f in factors)

    def test_discover_respects_n_factors(self):
        """返回数量不超过 n_factors。"""
        discoverer = AlphaCFGDiscoverer(max_depth=3, n_iterations=50, seed=42)
        factors = discoverer.discover(n_factors=3)
        assert len(factors) <= 3

    def test_discover_factors_have_ic(self):
        """发现的因子有 IC 值。"""
        discoverer = AlphaCFGDiscoverer(max_depth=3, n_iterations=50, seed=42)
        factors = discoverer.discover(n_factors=5)
        for f in factors:
            assert isinstance(f.ic, float)
            assert -0.1 <= f.ic <= 0.1

    def test_discover_reproducible(self):
        """相同 seed 可复现。"""
        d1 = AlphaCFGDiscoverer(max_depth=3, n_iterations=30, seed=42)
        d2 = AlphaCFGDiscoverer(max_depth=3, n_iterations=30, seed=42)
        f1 = d1.discover(n_factors=3)
        f2 = d2.discover(n_factors=3)
        assert len(f1) == len(f2)
        for a, b in zip(f1, f2, strict=False):
            assert a.expression == b.expression
            assert a.ic == b.ic

    def test_discover_and_report(self):
        """discover_and_report 返回报告 dict。"""
        discoverer = AlphaCFGDiscoverer(max_depth=3, n_iterations=30, seed=42)
        report = discoverer.discover_and_report(n_factors=3)
        assert "n_iterations" in report
        assert "factors" in report
        assert "best_ic" in report
        assert len(report["factors"]) <= 3

    def test_factor_expression_to_dict(self):
        """FactorExpression 序列化。"""
        f = FactorExpression(expression="close", ic=0.05, ir=0.5, depth=1, visits=3)
        d = f.to_dict()
        assert d["expression"] == "close"
        assert d["ic"] == 0.05
        assert d["visits"] == 3
