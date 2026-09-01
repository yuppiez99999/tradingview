"""
AlphaCFG 语法引导因子发现 (2026.01)
===================================

文献依据: #1 AlphaCFG: Grammar-Guided Alpha Discovery (2026.01, ★★★★★)
论文: AlphaCFG — 使用上下文无关文法 (CFG) 引导因子发现过程
方法: MCTS (蒙特卡洛树搜索) + CFG 约束在语法空间中搜索有效因子

与 LIT-1.1/1.2 的区别
--------------------
- R&D-Agent-Quant (LIT-1.1): LLM 驱动多智能体因子挖掘
- AlphaForge (LIT-1.2): 已有因子的动态权重组合
- AlphaCFG (LIT-1.5): CFG 语法约束 + MCTS 搜索新因子表达式

核心思想
--------
1. CFG 定义因子表达式的语法空间 (保证语法正确)
2. MCTS 在语法空间中搜索 (UCB1 选择 + CFG 扩展 + 随机 rollout)
3. IC (Information Coefficient) 作为奖励信号回传
4. 迭代搜索发现高 IC 因子

CFG 规则
--------
    alpha  -> expr
    expr   -> binary | unary | ts_expr | cs_expr | terminal
    binary -> expr op expr
    op     -> + | - | * | /
    unary  -> func(expr)
    func   -> rank | scale | sign | abs | log | neg
    ts     -> ts_func(expr, window)
    ts_func-> ts_mean | ts_std | ts_rank | ts_delta | ts_decay
    cs     -> cs_func(expr)
    cs_func-> cs_rank | cs_zscore
    term   -> price | volume | return | high | low | open | close
    window -> 5 | 10 | 20 | 60 | 120

使用示例
--------
    from utils.alpha_factor.alpha_cfg import AlphaCFGDiscoverer

    discoverer = AlphaCFGDiscoverer(max_depth=5, n_iterations=200)
    factors = discoverer.discover(n_factors=10)
    for f in factors:
        print(f"IC={f.ic:.4f}  {f.expression}")
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field

logger = logging.getLogger("alpha_cfg")

# ============================================================
# 常量
# ============================================================

# CFG 终端符号 (基础数据字段)
TERMINALS = ["close", "open", "high", "low", "volume", "returns", "vwap"]

# 二元运算符
BINARY_OPS = ["+", "-", "*", "/"]

# 一元函数
UNARY_FUNCS = ["rank", "scale", "sign", "abs", "log", "neg"]

# 时序函数
TS_FUNCS = ["ts_mean", "ts_std", "ts_rank", "ts_delta", "ts_decay"]

# 截面函数
CS_FUNCS = ["cs_rank", "cs_zscore"]

# 时序窗口
WINDOWS = [5, 10, 20, 60, 120]

# MCTS 参数
DEFAULT_MAX_DEPTH = 5
DEFAULT_N_ITERATIONS = 200
DEFAULT_UCB_C = 1.414  # sqrt(2), UCB1 探索常数
DEFAULT_N_FACTORS = 10


# ============================================================
# 数据结构
# ============================================================


@dataclass
class FactorExpression:
    """因子表达式 (含字符串表示 + IC 评估)。"""

    expression: str
    ic: float = 0.0
    ir: float = 0.0  # Information Ratio
    depth: int = 0
    visits: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "expression": self.expression,
            "ic": round(self.ic, 6),
            "ir": round(self.ir, 6),
            "depth": self.depth,
            "visits": self.visits,
        }


# ============================================================
# CFG 文法
# ============================================================


class CFGGrammar:
    """因子表达式的上下文无关文法。

    提供从非终端符号生成表达式的接口,
    保证生成的表达式语法正确。
    """

    def __init__(self, seed: int = 42) -> None:
        self.rng = random.Random(seed)

    def generate(self, symbol: str = "alpha", max_depth: int = 5) -> str:
        """从非终端符号生成表达式 (递归下降)。"""
        if max_depth <= 0:
            return self._generate_terminal()

        if symbol == "alpha" or symbol == "expr":
            choice = self.rng.choice(
                ["binary", "unary", "ts_expr", "cs_expr", "terminal"]
            )
            if choice == "binary" and max_depth >= 2:
                return self._generate_binary(max_depth)
            if choice == "unary":
                return self._generate_unary(max_depth)
            if choice == "ts_expr":
                return self._generate_ts(max_depth)
            if choice == "cs_expr":
                return self._generate_cs(max_depth)
            return self._generate_terminal()
        return self._generate_terminal()

    def _generate_binary(self, max_depth: int) -> str:
        """生成二元运算: expr op expr。"""
        left = self.generate("expr", max_depth - 1)
        right = self.generate("expr", max_depth - 1)
        op = self.rng.choice(BINARY_OPS)
        return f"({left} {op} {right})"

    def _generate_unary(self, max_depth: int) -> str:
        """生成一元运算: func(expr)。"""
        func = self.rng.choice(UNARY_FUNCS)
        inner = self.generate("expr", max_depth - 1)
        return f"{func}({inner})"

    def _generate_ts(self, max_depth: int) -> str:
        """生成时序运算: ts_func(expr, window)。"""
        func = self.rng.choice(TS_FUNCS)
        inner = self.generate("expr", max_depth - 1)
        window = self.rng.choice(WINDOWS)
        return f"{func}({inner}, {window})"

    def _generate_cs(self, max_depth: int) -> str:
        """生成截面运算: cs_func(expr)。"""
        func = self.rng.choice(CS_FUNCS)
        inner = self.generate("expr", max_depth - 1)
        return f"{func}({inner})"

    def _generate_terminal(self) -> str:
        """生成终端符号。"""
        return self.rng.choice(TERMINALS)

    def get_expansions(self, symbol: str, max_depth: int) -> list[str]:
        """获取非终端符号的所有可能展开 (用于 MCTS 扩展)。"""
        if max_depth <= 0:
            return list(TERMINALS)

        if symbol in ("alpha", "expr"):
            expansions: list[str] = []
            if max_depth >= 2:
                expansions.append("binary")
            expansions.extend(["unary", "ts_expr", "cs_expr", "terminal"])
            return expansions
        return list(TERMINALS)


# ============================================================
# 因子评估器
# ============================================================


class FactorEvaluator:
    """因子评估器 — 计算 IC/IR (合成数据模拟)。

    生产环境应接入真实数据计算 IC;
    此处使用基于表达式特征的合成 IC,
    保证可复现且不依赖外部数据。
    """

    def __init__(self, seed: int = 42) -> None:
        self.rng = random.Random(seed)

    def evaluate(self, expression: str) -> tuple[float, float]:
        """评估因子表达式, 返回 (IC, IR)。

        合成 IC 基于表达式特征的确定性计算:
        - 表达式深度越深, IC 越可能高 (复杂因子)
        - 时序函数加分 (时序动量/反转)
        - 截面函数加分 (截面排序)
        - 随机噪声 (可复现)
        """
        depth = self._estimate_depth(expression)
        has_ts = any(func in expression for func in TS_FUNCS)
        has_cs = any(func in expression for func in CS_FUNCS)
        has_binary = any(op in expression for op in BINARY_OPS)

        # 基础 IC: 深度贡献 + 时序贡献 + 截面贡献
        base_ic = depth * 0.01
        if has_ts:
            base_ic += 0.02
        if has_cs:
            base_ic += 0.015
        if has_binary:
            base_ic += 0.005

        # 确定性噪声 (基于表达式哈希)
        noise = (hash(expression) & 0xFFFF) / 0xFFFF * 0.06 - 0.03
        ic = base_ic + noise
        ic = max(-0.1, min(0.1, ic))  # 限制在 [-0.1, 0.1]

        # IR = IC * sqrt(252) / 假设IC标准差
        ir = ic * math.sqrt(252) / 3.0 if ic != 0 else 0.0
        return round(ic, 6), round(ir, 6)

    def _estimate_depth(self, expression: str) -> int:
        """估算表达式深度 (嵌套层数)。"""
        max_depth = 0
        current_depth = 0
        for char in expression:
            if char == "(":
                current_depth += 1
                max_depth = max(max_depth, current_depth)
            elif char == ")":
                current_depth -= 1
        return max_depth


# ============================================================
# MCTS 节点
# ============================================================


@dataclass
class MCTSNode:
    """蒙特卡洛树搜索节点。

    每个节点对应一个部分表达式 (可能含非终端符号)。
    """

    expression: str
    depth: int
    parent: MCTSNode | None = None
    children: list[MCTSNode] = field(default_factory=list)
    visits: int = 0
    total_reward: float = 0.0
    is_terminal: bool = False

    @property
    def avg_reward(self) -> float:
        """平均奖励。"""
        return self.total_reward / self.visits if self.visits > 0 else 0.0

    def ucb1(self, c: float = DEFAULT_UCB_C) -> float:
        """UCB1 值 (探索 + 利用平衡)。"""
        if self.visits == 0:
            return float("inf")  # 未访问节点优先探索
        if self.parent is None or self.parent.visits == 0:
            return self.avg_reward
        exploitation = self.avg_reward
        exploration = c * math.sqrt(2 * math.log(self.parent.visits) / self.visits)
        return exploitation + exploration

    def best_child(self, c: float = DEFAULT_UCB_C) -> MCTSNode | None:
        """选择 UCB1 值最大的子节点。"""
        if not self.children:
            return None
        return max(self.children, key=lambda child: child.ucb1(c))

    def is_fully_expanded(self, max_children: int = 5) -> bool:
        """是否已完全展开。"""
        return len(self.children) >= max_children


# ============================================================
# MCTS 搜索器
# ============================================================


class MCTSSearcher:
    """蒙特卡洛树搜索器 — 在 CFG 语法空间中搜索高 IC 因子。

    四步循环:
    1. 选择 (Selection): 从根节点用 UCB1 选择到叶节点
    2. 扩展 (Expansion): 从叶节点按 CFG 规则展开新子节点
    3. 模拟 (Simulation): 随机 rollout 生成完整表达式
    4. 回传 (Backpropagation): IC 作为奖励回传到根节点
    """

    def __init__(
        self,
        grammar: CFGGrammar,
        evaluator: FactorEvaluator,
        max_depth: int = DEFAULT_MAX_DEPTH,
        ucb_c: float = DEFAULT_UCB_C,
        seed: int = 42,
    ) -> None:
        self.grammar = grammar
        self.evaluator = evaluator
        self.max_depth = max_depth
        self.ucb_c = ucb_c
        self.rng = random.Random(seed)
        self.root = MCTSNode(expression="alpha", depth=0)

    def search(
        self, n_iterations: int = DEFAULT_N_ITERATIONS
    ) -> list[FactorExpression]:
        """执行 MCTS 搜索, 返回发现的因子列表。"""
        discovered: dict[str, FactorExpression] = {}

        for i in range(n_iterations):
            # 1. 选择
            node = self._select(self.root)
            # 2. 扩展
            child = self._expand(node)
            # 3. 模拟
            expression = self._simulate(child)
            # 4. 评估 + 回传
            ic, ir = self.evaluator.evaluate(expression)
            self._backpropagate(child, abs(ic))  # 奖励 = |IC|

            # 记录发现的因子
            if expression not in discovered:
                discovered[expression] = FactorExpression(
                    expression=expression,
                    ic=ic,
                    ir=ir,
                    depth=self._estimate_depth(expression),
                    visits=1,
                )
            else:
                discovered[expression].visits += 1
                # 更新 IC (取更好的)
                if abs(ic) > abs(discovered[expression].ic):
                    discovered[expression].ic = ic
                    discovered[expression].ir = ir

            if (i + 1) % 50 == 0:
                logger.debug(
                    f"AlphaCFG MCTS: 迭代 {i + 1}/{n_iterations}, "
                    f"已发现 {len(discovered)} 个因子"
                )

        # 按 |IC| 排序
        results = sorted(discovered.values(), key=lambda f: abs(f.ic), reverse=True)
        return results

    def _select(self, node: MCTSNode) -> MCTSNode:
        """选择阶段 — UCB1 遍历到叶节点。"""
        current = node
        while current.children and current.is_fully_expanded():
            best = current.best_child(self.ucb_c)
            if best is None:
                break
            current = best
        return current

    def _expand(self, node: MCTSNode) -> MCTSNode:
        """扩展阶段 — 从 CFG 规则展开新子节点。"""
        if node.depth >= self.max_depth:
            node.is_terminal = True
            return node

        # 生成新表达式
        new_expr = self.grammar.generate("expr", self.max_depth - node.depth)
        child = MCTSNode(
            expression=new_expr,
            depth=node.depth + 1,
            parent=node,
            is_terminal=True,
        )
        node.children.append(child)
        return child

    def _simulate(self, node: MCTSNode) -> str:
        """模拟阶段 — 返回完整表达式 (已在扩展时生成)。"""
        return node.expression

    def _backpropagate(self, node: MCTSNode, reward: float) -> None:
        """回传阶段 — 奖励回传到根节点。"""
        current = node
        while current is not None:
            current.visits += 1
            current.total_reward += reward
            current = current.parent

    def _estimate_depth(self, expression: str) -> int:
        """估算表达式深度。"""
        max_d = 0
        cur = 0
        for ch in expression:
            if ch == "(":
                cur += 1
                max_d = max(max_d, cur)
            elif ch == ")":
                cur -= 1
        return max_d


# ============================================================
# AlphaCFG 主发现器
# ============================================================


class AlphaCFGDiscoverer:
    """AlphaCFG 语法引导因子发现主发现器。

    使用示例:
        discoverer = AlphaCFGDiscoverer(max_depth=5, n_iterations=200)
        factors = discoverer.discover(n_factors=10)
    """

    def __init__(
        self,
        max_depth: int = DEFAULT_MAX_DEPTH,
        n_iterations: int = DEFAULT_N_ITERATIONS,
        ucb_c: float = DEFAULT_UCB_C,
        seed: int = 42,
    ) -> None:
        self.max_depth = max_depth
        self.n_iterations = n_iterations
        self.ucb_c = ucb_c
        self.seed = seed
        self.grammar = CFGGrammar(seed=seed)
        self.evaluator = FactorEvaluator(seed=seed + 1)
        self.searcher = MCTSSearcher(
            grammar=self.grammar,
            evaluator=self.evaluator,
            max_depth=max_depth,
            ucb_c=ucb_c,
            seed=seed + 2,
        )

    def discover(self, n_factors: int = DEFAULT_N_FACTORS) -> list[FactorExpression]:
        """发现 top-N 高 IC 因子。"""
        logger.info(
            f"AlphaCFG: 开始搜索 (depth={self.max_depth}, "
            f"iters={self.n_iterations}, n_factors={n_factors})"
        )

        all_factors = self.searcher.search(self.n_iterations)
        top_factors = all_factors[:n_factors]

        logger.info(
            f"AlphaCFG: 搜索完成, 发现 {len(all_factors)} 个因子, "
            f"返回 top-{len(top_factors)}"
        )
        if top_factors:
            best = top_factors[0]
            logger.info(
                f"AlphaCFG: 最佳因子 IC={best.ic:.4f} IR={best.ir:.4f} "
                f"'{best.expression}'"
            )
        return top_factors

    def discover_and_report(
        self, n_factors: int = DEFAULT_N_FACTORS
    ) -> dict[str, object]:
        """发现因子并生成报告 dict。"""
        factors = self.discover(n_factors)
        return {
            "n_iterations": self.n_iterations,
            "max_depth": self.max_depth,
            "n_discovered": len(factors),
            "factors": [f.to_dict() for f in factors],
            "best_ic": factors[0].ic if factors else 0.0,
            "best_expression": factors[0].expression if factors else "",
        }


# ============================================================
# CLI 入口
# ============================================================


def main() -> None:
    """CLI 入口: 运行 AlphaCFG 因子发现。

    使用方式:
        python -m utils.alpha_factor.alpha_cfg
        python utils/alpha_factor/alpha_cfg.py
    """
    print("=" * 60)
    print("AlphaCFG 语法引导因子发现 (2026.01)")
    print("方法: CFG 文法约束 + MCTS 搜索")
    print("=" * 60)

    discoverer = AlphaCFGDiscoverer(max_depth=5, n_iterations=200)
    factors = discoverer.discover(n_factors=10)

    print(f"\n发现 top-{len(factors)} 因子:")
    print("-" * 60)
    for i, f in enumerate(factors, 1):
        print(
            f"{i:2d}. IC={f.ic:+.4f}  IR={f.ir:+.4f}  depth={f.depth}  "
            f"visits={f.visits}"
        )
        print(f"    {f.expression}")

    print("-" * 60)
    if factors:
        print(f"\n最佳因子: IC={factors[0].ic:+.4f}")
        print(f"表达式: {factors[0].expression}")


if __name__ == "__main__":
    main()
