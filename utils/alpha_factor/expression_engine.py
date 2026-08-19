"""因子表达式引擎 — DSL 解析 + 安全求值

用户用类数学表达式字符串定义因子, 引擎自动解析为 AST 并在横截面/时序数据上求值。

DSL 语法:
    表达式  := 加减表达式
    加减    := 乘除 (('+' | '-') 乘除)*
    乘除    := 幂 (('*' | '/') 幂)*
    幂      := 一元 ('**' 幂)?            -- 右结合
    一元    := ('-' | '+') 一元 | 基元
    基元    := 数字 | 标识符 '(' 参数列 ')' | 标识符 | '(' 表达式 ')'
    参数列  := 表达式 (',' 表达式)*

字段引用:
    价量: close / open / high / low / volume (来自 price_data)
    基本面: pe / pb / roe / revenue_yoy / ... (来自 fundamentals)
    已有因子: MOM_20D / CYQ_PROFIT_RATIO / ... (来自 existing_factors)

内置算子:
    截面类 (输入 dict[str,float] → dict[str,float]):
        rank(x)               百分位排名 [0,1]
        zscore(x)             标准化 (x-mean)/std
        normalize(x)          归一化 x / sum(|x|)
        winsorize(x, n)       MAD 去极值 (默认 n_sigma=3)
        quantile(x, q)        分位数值 (q∈[0,1])
    时序类 (输入字段引用 + 窗口 → dict[str,float]):
        delay(field, n)       n 日前的值
        delta(field, n)       n 日变化量
        mean(field, n)        n 日均值
        std(field, n)         n 日标准差
        max(field, n)         n 日最大值
        min(field, n)         n 日最小值
        sum(field, n)         n 日累加
        slope(field, n)       n 日线性回归斜率
        rank_ts(field, n)     当前值在 n 日内的百分位排名
    二元时序 (两字段 + 窗口):
        correlation(x, y, n)  n 日滚动相关系数
        covariance(x, y, n)   n 日滚动协方差

示例:
    "rank(close / delay(close, 20))"                    — 20 日动量排名
    "zscore(correlation(close, volume, 20))"            — 量价相关性 z-score
    "rank(mean(close, 5)) - rank(mean(close, 20))"       — 均线交叉
    "(close - mean(close, 20)) / std(close, 20)"        — 布林带位置
    "rank(delta(volume, 5) / volume)"                   — 成交量变化比

参考:
    - WorldQuant Alpha101 表达式因子范式
    - GS Sachs 因子表达式引擎
    - 自定义因子表达式引擎评估 (cairn/LOG.md 2026-08-12 W6.4.5)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from utils.alpha_factor.base import FactorValue, winsorize

logger = logging.getLogger(__name__)

# ============================================================
# 1. Tokenizer
# ============================================================

# Token 类型
TT_NUMBER = "NUMBER"
TT_IDENT = "IDENT"
TT_OP = "OP"        # + - * / **
TT_LPAREN = "LPAREN"
TT_RPAREN = "RPAREN"
TT_COMMA = "COMMA"
TT_EOF = "EOF"

# 操作符模式 (长在前: ** 先于 *)
_OP_RE = re.compile(r"\*\*|[+\-*/]")


@dataclass
class Token:
    type: str
    value: str
    pos: int = 0


def tokenize(expr: str) -> list[Token]:
    """将表达式字符串切分为 token 序列"""
    tokens: list[Token] = []
    i = 0
    n = len(expr)
    while i < n:
        c = expr[i]
        # 跳过空白
        if c.isspace():
            i += 1
            continue
        # 数字 (含小数)
        if c.isdigit() or (c == "." and i + 1 < n and expr[i + 1].isdigit()):
            j = i
            while j < n and (expr[j].isdigit() or expr[j] == "."):
                j += 1
            tokens.append(Token(TT_NUMBER, expr[i:j], i))
            i = j
            continue
        # 标识符 (字母开头, 含下划线和数字)
        if c.isalpha() or c == "_":
            j = i
            while j < n and (expr[j].isalnum() or expr[j] == "_"):
                j += 1
            tokens.append(Token(TT_IDENT, expr[i:j], i))
            i = j
            continue
        # 操作符
        m = _OP_RE.match(expr, i)
        if m:
            tokens.append(Token(TT_OP, m.group(), i))
            i = m.end()
            continue
        if c == "(":
            tokens.append(Token(TT_LPAREN, c, i))
            i += 1
            continue
        if c == ")":
            tokens.append(Token(TT_RPAREN, c, i))
            i += 1
            continue
        if c == ",":
            tokens.append(Token(TT_COMMA, c, i))
            i += 1
            continue
        raise SyntaxError(f"表达式引擎: 非法字符 '{c}' (位置 {i})")
    tokens.append(Token(TT_EOF, "", n))
    return tokens


# ============================================================
# 2. AST 节点
# ============================================================


@dataclass
class NumberNode:
    value: float


@dataclass
class FieldNode:
    name: str


@dataclass
class BinaryOpNode:
    op: str
    left: Any
    right: Any


@dataclass
class UnaryOpNode:
    op: str
    operand: Any


@dataclass
class FuncCallNode:
    name: str
    args: list[Any]


ASTNode = NumberNode | FieldNode | BinaryOpNode | UnaryOpNode | FuncCallNode


# ============================================================
# 3. Parser (递归下降)
# ============================================================


class _Parser:
    """递归下降解析器: tokens → AST"""

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def expect(self, ttype: str, tval: str | None = None) -> Token:
        t = self.peek()
        if t.type != ttype or (tval is not None and t.value != tval):
            raise SyntaxError(
                f"期望 {tval or ttype}, 实际 {t.type}:{t.value} (位置 {t.pos})"
            )
        return self.advance()

    # expr := add_expr
    def parse_expr(self) -> ASTNode:
        return self._parse_add()

    # add_expr := mul_expr (('+' | '-') mul_expr)*
    def _parse_add(self) -> ASTNode:
        node = self._parse_mul()
        while self.peek().type == TT_OP and self.peek().value in ("+", "-"):
            op = self.advance().value
            right = self._parse_mul()
            node = BinaryOpNode(op, node, right)
        return node

    # mul_expr := pow_expr (('*' | '/') pow_expr)*
    def _parse_mul(self) -> ASTNode:
        node = self._parse_pow()
        while self.peek().type == TT_OP and self.peek().value in ("*", "/"):
            op = self.advance().value
            right = self._parse_pow()
            node = BinaryOpNode(op, node, right)
        return node

    # pow_expr := unary ('**' pow_expr)?  -- 右结合
    def _parse_pow(self) -> ASTNode:
        base = self._parse_unary()
        if self.peek().type == TT_OP and self.peek().value == "**":
            self.advance()
            exp = self._parse_pow()  # 右结合递归
            return BinaryOpNode("**", base, exp)
        return base

    # unary := ('-' | '+') unary | primary
    def _parse_unary(self) -> ASTNode:
        if self.peek().type == TT_OP and self.peek().value in ("-", "+"):
            op = self.advance().value
            operand = self._parse_unary()
            return UnaryOpNode(op, operand)
        return self._parse_primary()

    # primary := NUMBER | IDENT '(' args ')' | IDENT | '(' expr ')'
    def _parse_primary(self) -> ASTNode:
        t = self.peek()
        if t.type == TT_NUMBER:
            self.advance()
            return NumberNode(float(t.value))
        if t.type == TT_IDENT:
            self.advance()
            # 函数调用
            if self.peek().type == TT_LPAREN:
                self.advance()
                args = self._parse_args()
                self.expect(TT_RPAREN)
                return FuncCallNode(t.value, args)
            # 字段引用
            return FieldNode(t.value)
        if t.type == TT_LPAREN:
            self.advance()
            node = self.parse_expr()
            self.expect(TT_RPAREN)
            return node
        raise SyntaxError(f"意外的 token {t.type}:{t.value} (位置 {t.pos})")

    # args := expr (',' expr)*
    def _parse_args(self) -> list[Any]:
        args: list[Any] = []
        if self.peek().type == TT_RPAREN:
            return args
        args.append(self.parse_expr())
        while self.peek().type == TT_COMMA:
            self.advance()
            args.append(self.parse_expr())
        return args


def parse_expression(expr: str) -> ASTNode:
    """解析表达式字符串 → AST 根节点"""
    tokens = tokenize(expr)
    parser = _Parser(tokens)
    node = parser.parse_expr()
    if parser.peek().type != TT_EOF:
        raise SyntaxError(f"表达式末尾有多余 token: {parser.peek().value}")
    return node


# ============================================================
# 4. 内置算子库
# ============================================================

# ---- 截面算子 (dict[str,float] → dict[str,float]) ----


def _op_rank(x: dict[str, float]) -> dict[str, float]:
    """百分位排名 [0, 1]"""
    if not x:
        return x
    syms = list(x.keys())
    vals = np.array([x[s] for s in syms], dtype=float)
    # 用平均秩处理并列
    order = vals.argsort()
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(vals) + 1)
    # 处理并列: 同值取平均秩
    unique_vals, inverse, counts = np.unique(vals, return_inverse=True, return_counts=True)
    rank_sums = np.zeros(len(unique_vals))
    for i, _v in enumerate(vals):
        rank_sums[inverse[i]] += ranks[i]
    avg_ranks = rank_sums / counts
    final_ranks = avg_ranks[inverse]
    n = len(vals)
    if n > 1:
        final_ranks = (final_ranks - 1) / (n - 1)
    else:
        final_ranks[:] = 0.5
    return {s: float(r) for s, r in zip(syms, final_ranks)}


def _op_zscore(x: dict[str, float]) -> dict[str, float]:
    """标准化 (x - mean) / std"""
    if not x:
        return x
    arr = np.array(list(x.values()), dtype=float)
    mean = arr.mean()
    std = arr.std()
    if std < 1e-12:
        return {s: 0.0 for s in x}
    return {s: float((v - mean) / std) for s, v in x.items()}


def _op_normalize(x: dict[str, float]) -> dict[str, float]:
    """归一化 x / sum(|x|)"""
    if not x:
        return x
    total = sum(abs(v) for v in x.values())
    if total < 1e-12:
        return {s: 0.0 for s in x}
    return {s: float(v / total) for s, v in x.items()}


def _op_winsorize(x: dict[str, float], n_sigma: float = 3.0) -> dict[str, float]:
    """MAD 去极值"""
    return winsorize(x, n_sigma)


def _op_quantile(x: dict[str, float], q: float = 0.5) -> dict[str, float]:
    """返回 x 的 q 分位数值 (截面所有标的都得到同一个分位数值)"""
    if not x:
        return x
    arr = np.array(list(x.values()), dtype=float)
    qv = float(np.quantile(arr, q))
    return {s: qv for s in x}


def _op_abs(x: dict[str, float]) -> dict[str, float]:
    """绝对值"""
    return {s: abs(v) for s, v in x.items()}


def _op_log(x: dict[str, float]) -> dict[str, float]:
    """自然对数 (负值/零值取 nan, 输出替换为 0)"""
    return {s: float(np.log(v)) if v > 0 else 0.0 for s, v in x.items()}


def _op_max(x: dict[str, float], y: dict[str, float]) -> dict[str, float]:
    """逐标的取最大值"""
    return {s: float(max(x.get(s, 0.0), y.get(s, 0.0))) for s in set(x) | set(y)}


def _op_min(x: dict[str, float], y: dict[str, float]) -> dict[str, float]:
    """逐标的取最小值"""
    return {s: float(min(x.get(s, 0.0), y.get(s, 0.0))) for s in set(x) | set(y)}


# ---- 时序算子 (字段引用 + 窗口 → dict[str,float]) ----


def _ts_op_single(
    series_dict: dict[str, list[float]],
    window: int,
    func: Callable[[np.ndarray], float],
) -> dict[str, float]:
    """通用: 对每标的的序列取末尾 window 天做 func 聚合"""
    result: dict[str, float] = {}
    for sym, series in series_dict.items():
        if len(series) < window:
            continue
        arr = np.asarray(series[-window:], dtype=float)
        result[sym] = float(func(arr))
    return result


def _ts_delay(series_dict: dict[str, list[float]], n: int) -> dict[str, float]:
    """n 日前的值"""
    result: dict[str, float] = {}
    for sym, series in series_dict.items():
        if len(series) > n:
            result[sym] = float(series[-1 - n])
    return result


def _ts_delta(series_dict: dict[str, list[float]], n: int) -> dict[str, float]:
    """n 日变化量 = current - delay(n)"""
    result: dict[str, float] = {}
    for sym, series in series_dict.items():
        if len(series) > n:
            result[sym] = float(series[-1] - series[-1 - n])
    return result


def _ts_mean(series_dict: dict[str, list[float]], window: int) -> dict[str, float]:
    return _ts_op_single(series_dict, window, np.mean)


def _ts_std(series_dict: dict[str, list[float]], window: int) -> dict[str, float]:
    return _ts_op_single(series_dict, window, np.std)


def _ts_max(series_dict: dict[str, list[float]], window: int) -> dict[str, float]:
    return _ts_op_single(series_dict, window, np.max)


def _ts_min(series_dict: dict[str, list[float]], window: int) -> dict[str, float]:
    return _ts_op_single(series_dict, window, np.min)


def _ts_sum(series_dict: dict[str, list[float]], window: int) -> dict[str, float]:
    return _ts_op_single(series_dict, window, np.sum)


def _ts_slope(series_dict: dict[str, list[float]], window: int) -> dict[str, float]:
    """n 日线性回归斜率"""
    result: dict[str, float] = {}
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()
    for sym, series in series_dict.items():
        if len(series) < window:
            continue
        y = np.asarray(series[-window:], dtype=float)
        y_mean = y.mean()
        cov = ((x - x_mean) * (y - y_mean)).sum()
        if x_var > 0:
            result[sym] = float(cov / x_var)
        else:
            result[sym] = 0.0
    return result


def _ts_rank(series_dict: dict[str, list[float]], window: int) -> dict[str, float]:
    """当前值在 n 日内的百分位排名"""
    result: dict[str, float] = {}
    for sym, series in series_dict.items():
        if len(series) < window:
            continue
        arr = np.asarray(series[-window:], dtype=float)
        current = arr[-1]
        rank = float(np.sum(arr <= current) / len(arr))
        result[sym] = rank
    return result


def _ts_correlation(
    x_dict: dict[str, list[float]],
    y_dict: dict[str, list[float]],
    window: int,
) -> dict[str, float]:
    """n 日滚动相关系数"""
    result: dict[str, float] = {}
    for sym in x_dict:
        if sym not in y_dict:
            continue
        xs = x_dict[sym]
        ys = y_dict[sym]
        if len(xs) < window or len(ys) < window:
            continue
        x_arr = np.asarray(xs[-window:], dtype=float)
        y_arr = np.asarray(ys[-window:], dtype=float)
        if x_arr.std() < 1e-12 or y_arr.std() < 1e-12:
            result[sym] = 0.0
        else:
            result[sym] = float(np.corrcoef(x_arr, y_arr)[0, 1])
    return result


def _ts_covariance(
    x_dict: dict[str, list[float]],
    y_dict: dict[str, list[float]],
    window: int,
) -> dict[str, float]:
    """n 日滚动协方差"""
    result: dict[str, float] = {}
    for sym in x_dict:
        if sym not in y_dict:
            continue
        xs = x_dict[sym]
        ys = y_dict[sym]
        if len(xs) < window or len(ys) < window:
            continue
        x_arr = np.asarray(xs[-window:], dtype=float)
        y_arr = np.asarray(ys[-window:], dtype=float)
        result[sym] = float(np.cov(x_arr, y_arr)[0, 1])
    return result


# ============================================================
# 5. 求值器
# ============================================================

# 价量字段名 → price_data 中的 key
_PRICE_FIELDS = {"close", "closes", "open", "opens", "high", "highs", "low", "lows", "volume", "volumes"}

# 字段名规范化 (用户写 close, 引擎取 closes 列表)
_FIELD_NORMALIZE = {
    "close": "closes",
    "open": "opens",
    "high": "highs",
    "low": "lows",
    "volume": "volumes",
}


class ExpressionEvaluator:
    """AST 求值器: 在给定数据上计算表达式 → dict[str, float]

    求值模型:
        - 每个子表达式求值为 dict[str, float] (截面值)
        - 时序算子从 price_data 提取历史序列做滚动计算
        - 截面算子对 dict[str, float] 做变换
        - 算术运算逐标的执行
    """

    def __init__(
        self,
        price_data: dict[str, dict[str, list[float]]],
        fundamentals: dict[str, dict[str, float]] | None = None,
        existing_factors: dict[str, FactorValue] | None = None,
    ) -> None:
        self.price_data = price_data
        self.fundamentals = fundamentals or {}
        self.existing_factors = existing_factors or {}
        self.symbols = list(price_data.keys())

    def _resolve_field_series(self, field_name: str) -> dict[str, list[float]]:
        """从 price_data 提取字段的历史序列"""
        key = _FIELD_NORMALIZE.get(field_name, field_name)
        result: dict[str, list[float]] = {}
        for sym, pd in self.price_data.items():
            series = pd.get(key, [])
            if series:
                result[sym] = list(series)
        return result

    def _resolve_field_value(self, field_name: str) -> dict[str, float]:
        """提取字段最新截面值"""
        # 1. 价量字段
        if field_name in _PRICE_FIELDS or _FIELD_NORMALIZE.get(field_name):
            key = _FIELD_NORMALIZE.get(field_name, field_name)
            return {
                sym: float(pd.get(key, [0.0])[-1])
                for sym, pd in self.price_data.items()
                if pd.get(key)
            }
        # 2. 基本面字段
        vals: dict[str, float] = {}
        for sym, fund in self.fundamentals.items():
            if field_name in fund:
                vals[sym] = float(fund[field_name])
        if vals:
            return vals
        # 3. 已有因子
        fval = self.existing_factors.get(field_name)
        if fval and fval.values:
            return dict(fval.values)
        raise KeyError(f"未知字段引用: '{field_name}'")

    def evaluate(self, node: ASTNode) -> dict[str, float]:
        """求值入口"""
        if isinstance(node, NumberNode):
            return {s: node.value for s in self.symbols}
        if isinstance(node, FieldNode):
            return self._resolve_field_value(node.name)
        if isinstance(node, UnaryOpNode):
            operand = self.evaluate(node.operand)
            if node.op == "-":
                return {s: -v for s, v in operand.items()}
            return operand
        if isinstance(node, BinaryOpNode):
            left = self.evaluate(node.left)
            right = self.evaluate(node.right)
            return self._binop(left, node.op, right)
        if isinstance(node, FuncCallNode):
            return self._eval_func(node)
        raise TypeError(f"未知 AST 节点类型: {type(node)}")

    def _binop(
        self, left: dict[str, float], op: str, right: dict[str, float]
    ) -> dict[str, float]:
        """逐标的算术"""
        result: dict[str, float] = {}
        syms = set(left) | set(right)
        for s in syms:
            lv = left.get(s, 0.0)
            rv = right.get(s, 0.0)
            if op == "+":
                result[s] = lv + rv
            elif op == "-":
                result[s] = lv - rv
            elif op == "*":
                result[s] = lv * rv
            elif op == "/":
                result[s] = lv / rv if abs(rv) > 1e-12 else 0.0
            elif op == "**":
                result[s] = float(np.power(lv, rv))
            else:
                raise ValueError(f"未知操作符: {op}")
        return result

    def _eval_func(self, node: FuncCallNode) -> dict[str, float]:
        """函数调用分发"""
        name = node.name
        args = node.args

        # ---- 截面算子 (1 个表达式参数) ----
        if name == "rank":
            return _op_rank(self.evaluate(args[0]))
        if name == "zscore":
            return _op_zscore(self.evaluate(args[0]))
        if name == "normalize":
            return _op_normalize(self.evaluate(args[0]))
        if name == "winsorize":
            n_sigma = self._const(args[1], 3.0) if len(args) > 1 else 3.0
            return _op_winsorize(self.evaluate(args[0]), n_sigma)
        if name == "quantile":
            q = self._const(args[1], 0.5) if len(args) > 1 else 0.5
            return _op_quantile(self.evaluate(args[0]), q)
        if name == "abs":
            return _op_abs(self.evaluate(args[0]))
        if name == "log":
            return _op_log(self.evaluate(args[0]))

        # ---- 二元截面算子 ----
        if name == "max":
            return _op_max(self.evaluate(args[0]), self.evaluate(args[1]))
        if name == "min":
            return _op_min(self.evaluate(args[0]), self.evaluate(args[1]))

        # ---- 时序算子 (字段引用 + 窗口) ----
        ts_single = {
            "delay": _ts_delay,
            "delta": _ts_delta,
            "mean": _ts_mean,
            "std": _ts_std,
            "max_ts": _ts_max,
            "min_ts": _ts_min,
            "sum_ts": _ts_sum,
            "slope": _ts_slope,
            "rank_ts": _ts_rank,
        }
        if name in ts_single:
            field_name = self._require_field(args[0], name)
            window = int(self._const(args[1]))
            series = self._resolve_field_series(field_name)
            return ts_single[name](series, window)

        # ---- 二元时序算子 (两字段 + 窗口) ----
        if name == "correlation":
            fx = self._require_field(args[0], name)
            fy = self._require_field(args[1], name)
            window = int(self._const(args[2]))
            return _ts_correlation(
                self._resolve_field_series(fx),
                self._resolve_field_series(fy),
                window,
            )
        if name == "covariance":
            fx = self._require_field(args[0], name)
            fy = self._require_field(args[1], name)
            window = int(self._const(args[2]))
            return _ts_covariance(
                self._resolve_field_series(fx),
                self._resolve_field_series(fy),
                window,
            )

        raise NameError(f"未知函数: {name}")

    def _const(self, node: ASTNode, default: float | None = None) -> float:
        """从 AST 节点提取常量值"""
        if isinstance(node, NumberNode):
            return node.value
        if default is not None:
            return default
        raise SyntaxError(f"期望常量参数, 实际 {type(node).__name__}")

    def _require_field(self, node: ASTNode, func_name: str) -> str:
        """从 AST 节点提取字段名 (时序算子要求 FieldNode 参数)"""
        if isinstance(node, FieldNode):
            return node.name
        raise SyntaxError(
            f"函数 {func_name}() 要求字段引用参数 (如 close/volume), 实际 {type(node).__name__}"
        )


# ============================================================
# 6. 库集成入口
# ============================================================


@dataclass
class ExpressionFactorSpec:
    """表达式因子规格定义"""

    name: str           # 因子名 (如 "EXPR_MOM_RANK")
    expression: str     # DSL 表达式 (如 "rank(close / delay(close, 20))")
    category: str = "Expression"  # 因子类别


def compute_expression_factors(
    price_data: dict[str, dict[str, list[float]]],
    fundamentals: dict[str, dict[str, float]] | None = None,
    expressions: list[ExpressionFactorSpec] | list[tuple[str, str]] | None = None,
    existing_factors: dict[str, FactorValue] | None = None,
) -> dict[str, FactorValue]:
    """计算表达式因子 (第 14 大类 · Expression)

    Args:
        price_data: {symbol: {"closes": [...], "volumes": [...], ...}}
        fundamentals: {symbol: {"pe": ..., "pb": ..., ...}}
        expressions: 因子规格列表, 每项为 ExpressionFactorSpec 或 (name, expression) 元组
        existing_factors: 已计算的因子 (供表达式引用已有因子值)

    Returns:
        {factor_name: FactorValue} — 每个表达式一个因子, category="Expression"
    """
    if not expressions:
        return {}

    # 规格标准化
    specs: list[ExpressionFactorSpec] = []
    for item in expressions:
        if isinstance(item, ExpressionFactorSpec):
            specs.append(item)
        elif isinstance(item, tuple) and len(item) == 2:
            specs.append(ExpressionFactorSpec(name=item[0], expression=item[1]))
        else:
            logger.warning("表达式因子规格格式不正确, 跳过: %s", item)

    evaluator = ExpressionEvaluator(
        price_data=price_data,
        fundamentals=fundamentals,
        existing_factors=existing_factors,
    )

    result: dict[str, FactorValue] = {}
    for spec in specs:
        try:
            ast = parse_expression(spec.expression)
            values = evaluator.evaluate(ast)
            result[spec.name] = FactorValue(
                name=spec.name,
                category=spec.category,
                values=values,
            )
        except (ValueError, TypeError, KeyError, AttributeError, ZeroDivisionError, RuntimeError, SyntaxError) as e:
            logger.error("表达式因子 %s 求值失败: %s (表达式: %s)", spec.name, e, spec.expression)

    logger.info(
        "表达式因子引擎: 解析 %d 个表达式 → 成功 %d, 失败 %d",
        len(specs), len(result), len(specs) - len(result),
    )
    return result
