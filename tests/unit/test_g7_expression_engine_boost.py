"""G7 覆盖率冲刺 — utils/alpha_factor/expression_engine.py 单元测试.

目标: 覆盖率从 21.05% → 60%+

测试范围:
    1. Tokenizer (tokenize): 数字/标识符/操作符/括号/逗号/EOF/非法字符
    2. AST 节点 (dataclass)
    3. Parser (递归下降): 加减乘除/幂右结合/一元/函数调用/字段/括号/参数列/异常
    4. parse_expression: 末尾多余 token / 完整流程
    5. 截面算子: rank/zscore/normalize/winsorize/quantile/abs/log/max/min
    6. 时序算子: delay/delta/mean/std/max/min/sum/slope/rank_ts/correlation/covariance
    7. ExpressionEvaluator: resolve_field/evaluate/binop/eval_func/const/require_field
    8. compute_expression_factors: 规格/tuple/非法/异常隔离

运行:
    python -m pytest tests/unit/test_g7_expression_engine_boost.py -v
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from unittest.mock import patch

import numpy as np
import pytest


# ============================================================
# Numpy 2.x + coverage.py 兼容性 workaround
# ============================================================
# Bug: coverage.py 的 sys.settrace 与 numpy 2.x 的 C ufunc umr_sum 冲突,
# 当 initial=np._NoValue (默认哨兵) 时 ndarray.sum() 抛 TypeError.
# 修复: patch Python 层 _sum 函数, 将 _NoValue 转为 None.
@contextmanager
def _numpy_sum_safe():
    """临时 patch numpy._core._methods._sum 以兼容 coverage.py 追踪."""
    try:
        import numpy._core._methods as _m
    except ImportError:
        # numpy 1.x 路径
        import numpy.core._methods as _m  # type: ignore[no-redef]
    _orig = _m._sum

    def _safe_sum(a, axis=None, dtype=None, out=None, keepdims=False, initial=None, where=True):
        if not isinstance(initial, (int, float, complex, type(None))):
            initial = None
        return _orig(a, axis, dtype, out, keepdims, initial, where)

    _m._sum = _safe_sum
    try:
        yield
    finally:
        _m._sum = _orig

# ============================================================
# PROJECT_ROOT sys.path 注入 (使测试文件可独立运行)
# ============================================================
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.alpha_factor.base import FactorValue  # noqa: E402
from utils.alpha_factor.expression_engine import (  # noqa: E402  # noqa: E402
    _FIELD_NORMALIZE,
    _OP_RE,
    _PRICE_FIELDS,
    TT_COMMA,
    TT_EOF,
    TT_IDENT,
    TT_LPAREN,
    TT_NUMBER,
    TT_OP,
    TT_RPAREN,
    BinaryOpNode,
    ExpressionEvaluator,
    ExpressionFactorSpec,
    FieldNode,
    FuncCallNode,
    NumberNode,
    Token,
    UnaryOpNode,
    _op_abs,
    _op_log,
    _op_max,
    _op_min,
    _op_normalize,
    _op_quantile,
    _op_rank,
    _op_winsorize,
    _op_zscore,
    _Parser,
    _ts_correlation,
    _ts_covariance,
    _ts_delay,
    _ts_delta,
    _ts_max,
    _ts_mean,
    _ts_min,
    _ts_op_single,
    _ts_rank,
    _ts_slope,
    _ts_std,
    _ts_sum,
    compute_expression_factors,
    parse_expression,
    tokenize,
)

# ============================================================
# 1. Tokenizer
# ============================================================


class TestTokenizer:
    def test_empty_returns_eof(self) -> None:
        toks = tokenize("")
        assert len(toks) == 1
        assert toks[0].type == TT_EOF
        assert toks[0].value == ""
        assert toks[0].pos == 0

    def test_whitespace_skipped(self) -> None:
        toks = tokenize("   \t\n  ")
        assert len(toks) == 1
        assert toks[0].type == TT_EOF

    def test_integer(self) -> None:
        toks = tokenize("42")
        assert toks[0].type == TT_NUMBER
        assert toks[0].value == "42"
        assert toks[0].pos == 0

    def test_float(self) -> None:
        toks = tokenize("3.14")
        assert toks[0].type == TT_NUMBER
        assert toks[0].value == "3.14"

    def test_leading_dot_float(self) -> None:
        toks = tokenize(".5")
        assert toks[0].type == TT_NUMBER
        assert toks[0].value == ".5"

    def test_identifier_alpha(self) -> None:
        toks = tokenize("close")
        assert toks[0].type == TT_IDENT
        assert toks[0].value == "close"

    def test_identifier_with_underscore_and_digits(self) -> None:
        toks = tokenize("MOM_20D")
        assert toks[0].type == TT_IDENT
        assert toks[0].value == "MOM_20D"

    def test_leading_underscore_identifier(self) -> None:
        toks = tokenize("_foo")
        assert toks[0].type == TT_IDENT
        assert toks[0].value == "_foo"

    def test_power_operator(self) -> None:
        toks = tokenize("**")
        assert toks[0].type == TT_OP
        assert toks[0].value == "**"

    def test_single_ops(self) -> None:
        toks = tokenize("+-*/")
        types_vals = [(t.type, t.value) for t in toks[:-1]]
        assert types_vals == [
            (TT_OP, "+"),
            (TT_OP, "-"),
            (TT_OP, "*"),
            (TT_OP, "/"),
        ]

    def test_power_takes_priority_over_mul(self) -> None:
        # "**" should be matched as a single op, not "*" "*"
        toks = tokenize("a ** b")
        assert toks[1].type == TT_OP and toks[1].value == "**"

    def test_parens_and_comma(self) -> None:
        toks = tokenize("f(a, b)")
        types = [t.type for t in toks]
        assert types == [
            TT_IDENT,
            TT_LPAREN,
            TT_IDENT,
            TT_COMMA,
            TT_IDENT,
            TT_RPAREN,
            TT_EOF,
        ]

    def test_pos_tracking(self) -> None:
        toks = tokenize("  42")
        assert toks[0].pos == 2  # 跳过 2 个空格

    def test_invalid_char_raises(self) -> None:
        with pytest.raises(SyntaxError):
            tokenize("a @ b")

    def test_mixed_expression(self) -> None:
        toks = tokenize("rank(close / delay(close, 20))")
        assert toks[0].type == TT_IDENT
        assert toks[-1].type == TT_EOF

    def test_dot_without_digit_is_invalid(self) -> None:
        # "." 后非数字 → 非法字符
        with pytest.raises(SyntaxError):
            tokenize(". ")


# ============================================================
# 2. AST Nodes (dataclass)
# ============================================================


class TestASTNodes:
    def test_number_node(self) -> None:
        n = NumberNode(3.14)
        assert n.value == 3.14

    def test_field_node(self) -> None:
        n = FieldNode("close")
        assert n.name == "close"

    def test_binary_op_node(self) -> None:
        n = BinaryOpNode("+", NumberNode(1), NumberNode(2))
        assert n.op == "+"
        assert n.left.value == 1
        assert n.right.value == 2

    def test_unary_op_node(self) -> None:
        n = UnaryOpNode("-", NumberNode(5))
        assert n.op == "-"
        assert n.operand.value == 5

    def test_func_call_node(self) -> None:
        n = FuncCallNode("rank", [FieldNode("close")])
        assert n.name == "rank"
        assert len(n.args) == 1


# ============================================================
# 3. Parser (递归下降)
# ============================================================


class TestParser:
    def test_parse_number(self) -> None:
        node = parse_expression("3.14")
        assert isinstance(node, NumberNode)
        assert node.value == 3.14

    def test_parse_field(self) -> None:
        node = parse_expression("close")
        assert isinstance(node, FieldNode)
        assert node.name == "close"

    def test_parse_add(self) -> None:
        node = parse_expression("1 + 2")
        assert isinstance(node, BinaryOpNode)
        assert node.op == "+"

    def test_parse_sub_mul_div(self) -> None:
        for expr, op in [("1 - 2", "-"), ("1 * 2", "*"), ("1 / 2", "/")]:
            node = parse_expression(expr)
            assert isinstance(node, BinaryOpNode)
            assert node.op == op

    def test_parse_left_associative_add(self) -> None:
        # 1 + 2 + 3 → ((1+2)+3) 左结合
        node = parse_expression("1 + 2 + 3")
        assert isinstance(node, BinaryOpNode)
        assert node.op == "+"
        assert isinstance(node.right, NumberNode)
        assert node.right.value == 3
        assert isinstance(node.left, BinaryOpNode)

    def test_parse_precedence_mul_over_add(self) -> None:
        # 1 + 2 * 3 → 1 + (2*3)
        node = parse_expression("1 + 2 * 3")
        assert node.op == "+"
        assert isinstance(node.right, BinaryOpNode)
        assert node.right.op == "*"

    def test_parse_power_right_associative(self) -> None:
        # 2 ** 3 ** 2 → 2 ** (3 ** 2) 右结合
        node = parse_expression("2 ** 3 ** 2")
        assert node.op == "**"
        assert isinstance(node.left, NumberNode) and node.left.value == 2
        assert isinstance(node.right, BinaryOpNode)
        assert node.right.op == "**"

    def test_parse_unary_minus(self) -> None:
        node = parse_expression("-5")
        assert isinstance(node, UnaryOpNode)
        assert node.op == "-"
        assert isinstance(node.operand, NumberNode)

    def test_parse_unary_plus(self) -> None:
        node = parse_expression("+5")
        assert isinstance(node, UnaryOpNode)
        assert node.op == "+"

    def test_parse_double_unary(self) -> None:
        node = parse_expression("--5")
        assert isinstance(node, UnaryOpNode)
        assert isinstance(node.operand, UnaryOpNode)

    def test_parse_paren(self) -> None:
        node = parse_expression("(1 + 2) * 3")
        assert node.op == "*"
        assert isinstance(node.left, BinaryOpNode)
        assert node.left.op == "+"

    def test_parse_func_call_no_args(self) -> None:
        node = parse_expression("foo()")
        assert isinstance(node, FuncCallNode)
        assert node.name == "foo"
        assert node.args == []

    def test_parse_func_call_one_arg(self) -> None:
        node = parse_expression("rank(close)")
        assert isinstance(node, FuncCallNode)
        assert node.name == "rank"
        assert len(node.args) == 1
        assert isinstance(node.args[0], FieldNode)

    def test_parse_func_call_multi_args(self) -> None:
        node = parse_expression("max(close, open)")
        assert isinstance(node, FuncCallNode)
        assert len(node.args) == 2

    def test_parse_func_call_three_args(self) -> None:
        node = parse_expression("correlation(close, volume, 20)")
        assert isinstance(node, FuncCallNode)
        assert len(node.args) == 3
        assert isinstance(node.args[2], NumberNode)

    def test_parse_nested_func(self) -> None:
        node = parse_expression("rank(mean(close, 5))")
        assert isinstance(node, FuncCallNode)
        assert node.name == "rank"
        inner = node.args[0]
        assert isinstance(inner, FuncCallNode)
        assert inner.name == "mean"

    def test_parse_trailing_tokens_raises(self) -> None:
        with pytest.raises(SyntaxError):
            parse_expression("1 + 2 3")

    def test_parse_missing_rparen_raises(self) -> None:
        with pytest.raises(SyntaxError):
            parse_expression("(1 + 2")

    def test_parse_empty_raises(self) -> None:
        with pytest.raises(SyntaxError):
            parse_expression("")

    def test_parse_func_missing_rparen(self) -> None:
        with pytest.raises(SyntaxError):
            parse_expression("foo(a, b")

    def test_parser_expect_with_value_mismatch(self) -> None:
        toks = tokenize("1")
        p = _Parser(toks)
        with pytest.raises(SyntaxError):
            p.expect(TT_OP, "+")

    def test_parser_expect_type_mismatch(self) -> None:
        toks = tokenize("+")
        p = _Parser(toks)
        with pytest.raises(SyntaxError):
            p.expect(TT_NUMBER)

    def test_parser_expect_ok(self) -> None:
        toks = tokenize("+")
        p = _Parser(toks)
        t = p.expect(TT_OP, "+")
        assert t.value == "+"

    def test_parser_peek_and_advance(self) -> None:
        toks = tokenize("1 + 2")
        p = _Parser(toks)
        assert p.peek().type == TT_NUMBER
        first = p.advance()
        assert first.type == TT_NUMBER
        assert p.peek().type == TT_OP

    def test_parser_primary_unexpected_eof(self) -> None:
        toks = tokenize("")
        p = _Parser(toks)
        with pytest.raises(SyntaxError):
            p._parse_primary()


# ============================================================
# 4. 截面算子 (纯函数)
# ============================================================


class TestCrossSectionOps:
    def test_rank_basic(self) -> None:
        x = {"a": 1.0, "b": 2.0, "c": 3.0}
        r = _op_rank(x)
        assert r == {"a": 0.0, "b": 0.5, "c": 1.0}

    def test_rank_empty(self) -> None:
        assert _op_rank({}) == {}

    def test_rank_single(self) -> None:
        r = _op_rank({"a": 5.0})
        assert r == {"a": 0.5}

    def test_rank_ties(self) -> None:
        # 并列值取平均秩
        x = {"a": 1.0, "b": 1.0, "c": 3.0}
        r = _op_rank(x)
        assert abs(r["a"] - 0.25) < 1e-9
        assert abs(r["b"] - 0.25) < 1e-9
        assert abs(r["c"] - 1.0) < 1e-9

    def test_zscore_basic(self) -> None:
        x = {"a": 1.0, "b": 2.0, "c": 3.0}
        r = _op_zscore(x)
        arr = np.array([1, 2, 3])
        expected = (arr - arr.mean()) / arr.std()
        assert abs(r["a"] - expected[0]) < 1e-9

    def test_zscore_empty(self) -> None:
        assert _op_zscore({}) == {}

    def test_zscore_zero_std(self) -> None:
        x = {"a": 5.0, "b": 5.0}
        r = _op_zscore(x)
        assert r == {"a": 0.0, "b": 0.0}

    def test_normalize_basic(self) -> None:
        x = {"a": 1.0, "b": 3.0}
        r = _op_normalize(x)
        assert abs(r["a"] - 0.25) < 1e-9
        assert abs(r["b"] - 0.75) < 1e-9

    def test_normalize_empty(self) -> None:
        assert _op_normalize({}) == {}

    def test_normalize_zero_total(self) -> None:
        x = {"a": 0.0, "b": 0.0}
        r = _op_normalize(x)
        assert r == {"a": 0.0, "b": 0.0}

    def test_normalize_negative(self) -> None:
        x = {"a": -1.0, "b": 1.0}
        r = _op_normalize(x)
        assert abs(r["a"] + 0.5) < 1e-9
        assert abs(r["b"] - 0.5) < 1e-9

    def test_winsorize_basic(self) -> None:
        # 100 是极值, 应被 clip
        x = {"a": 1.0, "b": 2.0, "c": 100.0}
        r = _op_winsorize(x, 3.0)
        assert r["c"] < 100.0
        assert r["a"] == 1.0 and r["b"] == 2.0

    def test_winsorize_default_nsigma(self) -> None:
        x = {"a": 1.0, "b": 2.0}
        r = _op_winsorize(x)
        assert r == x

    def test_winsorize_empty(self) -> None:
        assert _op_winsorize({}, 3.0) == {}

    def test_quantile_basic(self) -> None:
        x = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0}
        r = _op_quantile(x, 0.5)
        assert all(v == 2.5 for v in r.values())

    def test_quantile_empty(self) -> None:
        assert _op_quantile({}) == {}

    def test_quantile_default_q(self) -> None:
        x = {"a": 1.0, "b": 2.0, "c": 3.0}
        r = _op_quantile(x)
        assert all(v == 2.0 for v in r.values())

    def test_abs_op(self) -> None:
        x = {"a": -1.5, "b": 2.0}
        r = _op_abs(x)
        assert r == {"a": 1.5, "b": 2.0}

    def test_log_op_positive(self) -> None:
        x = {"a": float(np.e), "b": 1.0}
        r = _op_log(x)
        assert abs(r["a"] - 1.0) < 1e-9
        assert r["b"] == 0.0

    def test_log_op_non_positive(self) -> None:
        x = {"a": -1.0, "b": 0.0}
        r = _op_log(x)
        assert r["a"] == 0.0
        assert r["b"] == 0.0

    def test_max_op(self) -> None:
        x = {"a": 1.0, "b": 5.0}
        y = {"a": 3.0, "b": 2.0, "c": 4.0}
        r = _op_max(x, y)
        assert r["a"] == 3.0
        assert r["b"] == 5.0
        assert r["c"] == 4.0

    def test_min_op(self) -> None:
        x = {"a": 1.0, "b": 5.0}
        y = {"a": 3.0, "b": 2.0, "c": 4.0}
        r = _op_min(x, y)
        assert r["a"] == 1.0
        assert r["b"] == 2.0
        assert r["c"] == 0.0


# ============================================================
# 5. 时序算子 (纯函数)
# ============================================================


class TestTimeSeriesOps:
    def test_ts_op_single_insufficient_length(self) -> None:
        series = {"a": [1.0, 2.0]}
        r = _ts_op_single(series, 5, np.mean)
        assert r == {}

    def test_ts_op_single_sufficient(self) -> None:
        series = {"a": [1.0, 2.0, 3.0, 4.0]}
        r = _ts_op_single(series, 2, np.mean)
        assert r == {"a": 3.5}

    def test_ts_delay(self) -> None:
        series = {"a": [10.0, 20.0, 30.0, 40.0]}
        r = _ts_delay(series, 1)
        assert r == {"a": 30.0}

    def test_ts_delay_insufficient(self) -> None:
        series = {"a": [10.0]}
        r = _ts_delay(series, 1)
        assert r == {}

    def test_ts_delta(self) -> None:
        series = {"a": [10.0, 20.0, 30.0, 50.0]}
        r = _ts_delta(series, 1)
        assert r == {"a": 20.0}

    def test_ts_delta_insufficient(self) -> None:
        series = {"a": [10.0]}
        r = _ts_delta(series, 1)
        assert r == {}

    def test_ts_mean(self) -> None:
        series = {"a": [1.0, 2.0, 3.0, 4.0, 5.0]}
        r = _ts_mean(series, 3)
        assert r == {"a": 4.0}

    def test_ts_std(self) -> None:
        series = {"a": [1.0, 2.0, 3.0, 4.0, 5.0]}
        r = _ts_std(series, 3)
        assert abs(r["a"] - np.std([3.0, 4.0, 5.0])) < 1e-9

    def test_ts_max(self) -> None:
        series = {"a": [1.0, 5.0, 3.0, 4.0]}
        r = _ts_max(series, 3)
        assert r == {"a": 5.0}

    def test_ts_min(self) -> None:
        series = {"a": [1.0, 5.0, 3.0, 0.0]}
        r = _ts_min(series, 3)
        assert r == {"a": 0.0}

    def test_ts_sum(self) -> None:
        series = {"a": [1.0, 2.0, 3.0, 4.0]}
        r = _ts_sum(series, 3)
        assert r == {"a": 9.0}

    def test_ts_slope_basic(self) -> None:
        # 线性 y = 2x → 斜率 2 (last 3 = [2,4,6])
        # 使用 _numpy_sum_safe 规避 numpy 2.x + coverage.py 冲突
        series = {"a": [0.0, 2.0, 4.0, 6.0]}
        with _numpy_sum_safe():
            r = _ts_slope(series, 3)
        assert abs(r["a"] - 2.0) < 1e-9

    def test_ts_slope_insufficient(self) -> None:
        series = {"a": [1.0, 2.0]}
        with _numpy_sum_safe():
            r = _ts_slope(series, 5)
        assert r == {}

    def test_ts_rank_basic(self) -> None:
        # arr = [10,20,30,5], current=5, count(<=5)=1, /4=0.25
        series = {"a": [10.0, 20.0, 30.0, 5.0]}
        r = _ts_rank(series, 4)
        assert abs(r["a"] - 0.25) < 1e-9

    def test_ts_rank_insufficient(self) -> None:
        series = {"a": [1.0]}
        r = _ts_rank(series, 5)
        assert r == {}

    def test_ts_correlation_basic(self) -> None:
        # 完全正相关
        x = {"a": [1.0, 2.0, 3.0, 4.0, 5.0]}
        y = {"a": [2.0, 4.0, 6.0, 8.0, 10.0]}
        r = _ts_correlation(x, y, 5)
        assert abs(r["a"] - 1.0) < 1e-9

    def test_ts_correlation_missing_sym(self) -> None:
        x = {"a": [1.0, 2.0, 3.0, 4.0, 5.0]}
        y = {"b": [1.0, 2.0, 3.0, 4.0, 5.0]}
        r = _ts_correlation(x, y, 5)
        assert r == {}

    def test_ts_correlation_insufficient_length(self) -> None:
        x = {"a": [1.0, 2.0]}
        y = {"a": [1.0, 2.0, 3.0, 4.0, 5.0]}
        r = _ts_correlation(x, y, 5)
        assert r == {}

    def test_ts_correlation_zero_variance(self) -> None:
        x = {"a": [5.0, 5.0, 5.0, 5.0, 5.0]}
        y = {"a": [1.0, 2.0, 3.0, 4.0, 5.0]}
        r = _ts_correlation(x, y, 5)
        assert r["a"] == 0.0

    def test_ts_covariance_basic(self) -> None:
        x = {"a": [1.0, 2.0, 3.0, 4.0, 5.0]}
        y = {"a": [2.0, 4.0, 6.0, 8.0, 10.0]}
        r = _ts_covariance(x, y, 5)
        expected = np.cov([1.0, 2.0, 3.0, 4.0, 5.0], [2.0, 4.0, 6.0, 8.0, 10.0])[0, 1]
        assert abs(r["a"] - expected) < 1e-9

    def test_ts_covariance_missing_sym(self) -> None:
        x = {"a": [1.0, 2.0, 3.0, 4.0, 5.0]}
        y = {"b": [1.0, 2.0, 3.0, 4.0, 5.0]}
        r = _ts_covariance(x, y, 5)
        assert r == {}

    def test_ts_covariance_insufficient(self) -> None:
        x = {"a": [1.0, 2.0]}
        y = {"a": [1.0, 2.0, 3.0, 4.0, 5.0]}
        r = _ts_covariance(x, y, 5)
        assert r == {}


# ============================================================
# 6. ExpressionEvaluator
# ============================================================


@pytest.fixture
def sample_price_data():
    return {
        "A": {
            "closes": [10.0, 11.0, 12.0, 13.0, 14.0],
            "volumes": [100.0, 200.0, 150.0, 300.0, 250.0],
        },
        "B": {
            "closes": [20.0, 19.0, 18.0, 17.0, 16.0],
            "volumes": [50.0, 60.0, 70.0, 80.0, 90.0],
        },
    }


@pytest.fixture
def sample_fundamentals():
    return {"A": {"pe": 15.0, "pb": 2.0}, "B": {"pe": 30.0, "pb": 4.0}}


@pytest.fixture
def sample_existing_factors():
    return {
        "MOM_20D": FactorValue(
            name="MOM_20D", category="Momentum", values={"A": 0.1, "B": -0.05}
        ),
    }


class TestExpressionEvaluator:
    def test_init_defaults(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        assert ev.fundamentals == {}
        assert ev.existing_factors == {}
        assert ev.symbols == ["A", "B"]

    def test_init_with_optional(
        self, sample_price_data, sample_fundamentals, sample_existing_factors
    ) -> None:
        ev = ExpressionEvaluator(
            sample_price_data, sample_fundamentals, sample_existing_factors
        )
        assert ev.fundamentals == sample_fundamentals
        assert ev.existing_factors is sample_existing_factors

    def test_resolve_field_series_close(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        s = ev._resolve_field_series("close")
        assert s == {"A": [10.0, 11.0, 12.0, 13.0, 14.0], "B": [20.0, 19.0, 18.0, 17.0, 16.0]}

    def test_resolve_field_series_volume(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        s = ev._resolve_field_series("volume")
        assert s["A"] == [100.0, 200.0, 150.0, 300.0, 250.0]

    def test_resolve_field_series_unknown_returns_empty(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        s = ev._resolve_field_series("nonexistent")
        assert s == {}

    def test_resolve_field_value_price(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        v = ev._resolve_field_value("close")
        assert v == {"A": 14.0, "B": 16.0}

    def test_resolve_field_value_fundamental(
        self, sample_price_data, sample_fundamentals
    ) -> None:
        ev = ExpressionEvaluator(sample_price_data, sample_fundamentals)
        v = ev._resolve_field_value("pe")
        assert v == {"A": 15.0, "B": 30.0}

    def test_resolve_field_value_existing_factor(
        self, sample_price_data, sample_existing_factors
    ) -> None:
        ev = ExpressionEvaluator(sample_price_data, existing_factors=sample_existing_factors)
        v = ev._resolve_field_value("MOM_20D")
        assert v == {"A": 0.1, "B": -0.05}

    def test_resolve_field_value_unknown_raises(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        with pytest.raises(KeyError):
            ev._resolve_field_value("unknown_field")

    def test_evaluate_number(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        r = ev.evaluate(NumberNode(42.0))
        assert r == {"A": 42.0, "B": 42.0}

    def test_evaluate_field(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        r = ev.evaluate(FieldNode("close"))
        assert r == {"A": 14.0, "B": 16.0}

    def test_evaluate_unary_minus(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        r = ev.evaluate(UnaryOpNode("-", NumberNode(5.0)))
        assert r == {"A": -5.0, "B": -5.0}

    def test_evaluate_unary_plus(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        r = ev.evaluate(UnaryOpNode("+", NumberNode(5.0)))
        assert r == {"A": 5.0, "B": 5.0}

    def test_evaluate_binary_add(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = BinaryOpNode("+", FieldNode("close"), NumberNode(1.0))
        r = ev.evaluate(node)
        assert r == {"A": 15.0, "B": 17.0}

    def test_evaluate_binary_sub_mul(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = BinaryOpNode("-", FieldNode("close"), NumberNode(1.0))
        assert ev.evaluate(node) == {"A": 13.0, "B": 15.0}
        node = BinaryOpNode("*", FieldNode("close"), NumberNode(2.0))
        assert ev.evaluate(node) == {"A": 28.0, "B": 32.0}

    def test_evaluate_binary_div(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = BinaryOpNode("/", FieldNode("close"), NumberNode(2.0))
        r = ev.evaluate(node)
        assert r == {"A": 7.0, "B": 8.0}

    def test_evaluate_binary_power(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = BinaryOpNode("**", NumberNode(2.0), NumberNode(3.0))
        r = ev.evaluate(node)
        assert r == {"A": 8.0, "B": 8.0}

    def test_evaluate_func_rank(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("rank", [FieldNode("close")])
        r = ev.evaluate(node)
        assert r == {"A": 0.0, "B": 1.0}

    def test_evaluate_func_zscore(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("zscore", [FieldNode("close")])
        r = ev.evaluate(node)
        assert abs(r["A"] + 1.0) < 1e-9
        assert abs(r["B"] - 1.0) < 1e-9

    def test_evaluate_func_normalize(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("normalize", [FieldNode("close")])
        r = ev.evaluate(node)
        assert abs(r["A"] - 14.0 / 30.0) < 1e-9

    def test_evaluate_func_winsorize(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("winsorize", [FieldNode("close")])
        r = ev.evaluate(node)
        assert r == {"A": 14.0, "B": 16.0}

    def test_evaluate_func_winsorize_with_nsigma(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("winsorize", [FieldNode("close"), NumberNode(1.0)])
        r = ev.evaluate(node)
        # n_sigma=1, MAD 法, 数据无极值 → 原值
        assert r == {"A": 14.0, "B": 16.0}

    def test_evaluate_func_quantile(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("quantile", [FieldNode("close"), NumberNode(0.5)])
        r = ev.evaluate(node)
        assert r == {"A": 15.0, "B": 15.0}

    def test_evaluate_func_quantile_default_q(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("quantile", [FieldNode("close")])
        r = ev.evaluate(node)
        assert r == {"A": 15.0, "B": 15.0}

    def test_evaluate_func_abs(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("abs", [FieldNode("close")])
        r = ev.evaluate(node)
        assert r == {"A": 14.0, "B": 16.0}

    def test_evaluate_func_log(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("log", [FieldNode("close")])
        r = ev.evaluate(node)
        assert abs(r["A"] - np.log(14.0)) < 1e-9

    def test_evaluate_func_max(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("max", [FieldNode("close"), NumberNode(15.0)])
        r = ev.evaluate(node)
        assert r == {"A": 15.0, "B": 16.0}

    def test_evaluate_func_min(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("min", [FieldNode("close"), NumberNode(15.0)])
        r = ev.evaluate(node)
        assert r == {"A": 14.0, "B": 15.0}

    def test_evaluate_func_mean_ts(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("mean", [FieldNode("close"), NumberNode(3.0)])
        r = ev.evaluate(node)
        assert r == {"A": 13.0, "B": 17.0}

    def test_evaluate_func_delay(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("delay", [FieldNode("close"), NumberNode(1.0)])
        r = ev.evaluate(node)
        assert r == {"A": 13.0, "B": 17.0}

    def test_evaluate_func_delta(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("delta", [FieldNode("close"), NumberNode(1.0)])
        r = ev.evaluate(node)
        assert r == {"A": 1.0, "B": -1.0}

    def test_evaluate_func_std_ts(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("std", [FieldNode("close"), NumberNode(3.0)])
        r = ev.evaluate(node)
        assert "A" in r and "B" in r

    def test_evaluate_func_max_ts(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("max_ts", [FieldNode("close"), NumberNode(3.0)])
        r = ev.evaluate(node)
        assert r == {"A": 14.0, "B": 18.0}

    def test_evaluate_func_min_ts(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("min_ts", [FieldNode("close"), NumberNode(3.0)])
        r = ev.evaluate(node)
        assert r == {"A": 12.0, "B": 16.0}

    def test_evaluate_func_sum_ts(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("sum_ts", [FieldNode("close"), NumberNode(3.0)])
        r = ev.evaluate(node)
        assert r == {"A": 39.0, "B": 51.0}

    def test_evaluate_func_slope(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("slope", [FieldNode("close"), NumberNode(3.0)])
        # 使用 _numpy_sum_safe 规避 numpy 2.x + coverage.py 冲突
        with _numpy_sum_safe():
            r = ev.evaluate(node)
        assert abs(r["A"] - 1.0) < 1e-9

    def test_evaluate_func_rank_ts(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("rank_ts", [FieldNode("close"), NumberNode(3.0)])
        r = ev.evaluate(node)
        # A: last 3 = [12,13,14], current=14 → rank 3/3=1.0
        assert abs(r["A"] - 1.0) < 1e-9
        # B: last 3 = [18,17,16], current=16 → 1/3
        assert abs(r["B"] - 1.0 / 3.0) < 1e-9

    def test_evaluate_func_correlation(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode(
            "correlation", [FieldNode("close"), FieldNode("volume"), NumberNode(5.0)]
        )
        r = ev.evaluate(node)
        assert "A" in r and "B" in r

    def test_evaluate_func_covariance(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode(
            "covariance", [FieldNode("close"), FieldNode("volume"), NumberNode(5.0)]
        )
        r = ev.evaluate(node)
        assert "A" in r and "B" in r

    def test_evaluate_func_unknown_raises(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        node = FuncCallNode("unknown_func", [FieldNode("close")])
        with pytest.raises(NameError):
            ev.evaluate(node)

    def test_evaluate_unknown_node_raises(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        with pytest.raises(TypeError):
            ev.evaluate("not a node")

    def test_binop_div_zero(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        r = ev._binop({"A": 5.0}, "/", {"A": 0.0})
        assert r == {"A": 0.0}

    def test_binop_unknown_op_raises(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        with pytest.raises(ValueError):
            ev._binop({"A": 1.0}, "^", {"A": 2.0})

    def test_const_number(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        assert ev._const(NumberNode(7.0)) == 7.0

    def test_const_non_number_with_default(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        assert ev._const(FieldNode("x"), 9.0) == 9.0

    def test_const_non_number_no_default_raises(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        with pytest.raises(SyntaxError):
            ev._const(FieldNode("x"))

    def test_require_field_valid(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        assert ev._require_field(FieldNode("close"), "mean") == "close"

    def test_require_field_invalid_raises(self, sample_price_data) -> None:
        ev = ExpressionEvaluator(sample_price_data)
        with pytest.raises(SyntaxError):
            ev._require_field(NumberNode(1.0), "mean")


# ============================================================
# 7. compute_expression_factors (集成入口)
# ============================================================


class TestComputeExpressionFactors:
    def test_empty_expressions_returns_empty(self, sample_price_data) -> None:
        r = compute_expression_factors(sample_price_data, expressions=[])
        assert r == {}

    def test_none_expressions_returns_empty(self, sample_price_data) -> None:
        r = compute_expression_factors(sample_price_data, expressions=None)
        assert r == {}

    def test_spec_form(self, sample_price_data) -> None:
        spec = ExpressionFactorSpec(name="RANK_CLOSE", expression="rank(close)")
        r = compute_expression_factors(sample_price_data, expressions=[spec])
        assert "RANK_CLOSE" in r
        assert r["RANK_CLOSE"].category == "Expression"
        assert r["RANK_CLOSE"].values == {"A": 0.0, "B": 1.0}

    def test_tuple_form(self, sample_price_data) -> None:
        r = compute_expression_factors(
            sample_price_data, expressions=[("MOM", "delta(close, 1)")]
        )
        assert "MOM" in r
        assert r["MOM"].values == {"A": 1.0, "B": -1.0}

    def test_invalid_spec_skipped(self, sample_price_data, caplog) -> None:
        import logging

        with caplog.at_level(logging.WARNING):
            r = compute_expression_factors(
                sample_price_data,
                expressions=["invalid_string"],
            )
        assert r == {}
        assert any("格式不正确" in rec.message for rec in caplog.records)

    def test_failure_isolated(self, sample_price_data, caplog) -> None:
        import logging

        with caplog.at_level(logging.ERROR):
            r = compute_expression_factors(
                sample_price_data,
                expressions=[
                    ("BAD", "1 + "),  # 语法错误
                    ("GOOD", "rank(close)"),
                ],
            )
        assert "GOOD" in r
        assert "BAD" not in r
        assert any("求值失败" in rec.message for rec in caplog.records)

    def test_with_fundamentals_and_existing_factors(
        self, sample_price_data, sample_fundamentals, sample_existing_factors
    ) -> None:
        r = compute_expression_factors(
            sample_price_data,
            fundamentals=sample_fundamentals,
            existing_factors=sample_existing_factors,
            expressions=[("PE_RANK", "rank(pe)")],
        )
        assert "PE_RANK" in r
        assert r["PE_RANK"].values == {"A": 0.0, "B": 1.0}

    def test_log_info_called(self, sample_price_data, caplog) -> None:
        import logging

        with caplog.at_level(logging.INFO):
            r = compute_expression_factors(
                sample_price_data,
                expressions=[("A", "close"), ("B", "open")],
            )
        assert len(r) == 2
        assert any("表达式因子引擎" in rec.message for rec in caplog.records)


# ============================================================
# 8. Mock 隔离 — 验证外部依赖被正确委托
# ============================================================


class TestMockedExternalDeps:
    def test_winsorize_op_uses_base_winsorize(self) -> None:
        """_op_winsorize 委托给 base.winsorize, mock 验证调用链"""
        with patch("utils.alpha_factor.expression_engine.winsorize") as mock_win:
            mock_win.return_value = {"a": 99.0}
            r = _op_winsorize({"a": 1.0, "b": 2.0}, 3.0)
            mock_win.assert_called_once_with({"a": 1.0, "b": 2.0}, 3.0)
            assert r == {"a": 99.0}

    def test_evaluator_evaluate_with_mocked_field_value(self, sample_price_data) -> None:
        """mock _resolve_field_value 验证 evaluate 路由"""
        ev = ExpressionEvaluator(sample_price_data)
        with patch.object(ev, "_resolve_field_value", return_value={"A": 1.0, "B": 2.0}):
            r = ev.evaluate(FieldNode("close"))
            assert r == {"A": 1.0, "B": 2.0}

    def test_compute_expression_factors_with_mocked_evaluator(self, sample_price_data) -> None:
        """mock ExpressionEvaluator.evaluate 验证集成入口"""
        with patch.object(
            ExpressionEvaluator, "evaluate", return_value={"A": 0.5, "B": 0.7}
        ):
            r = compute_expression_factors(
                sample_price_data,
                expressions=[("X", "rank(close)")],
            )
            assert r["X"].values == {"A": 0.5, "B": 0.7}

    def test_compute_expression_factors_parse_failure_logged(
        self, sample_price_data, caplog
    ) -> None:
        """mock parse_expression 抛异常, 验证 try/except 路径"""
        import logging

        with caplog.at_level(logging.ERROR), patch(
            "utils.alpha_factor.expression_engine.parse_expression",
            side_effect=RuntimeError("mock parse error"),
        ):
            r = compute_expression_factors(
                sample_price_data,
                expressions=[("X", "rank(close)")],
            )
        assert r == {}
        assert any("求值失败" in rec.message for rec in caplog.records)


# ============================================================
# 9. 模块级常量与正则
# ============================================================


class TestModuleConstants:
    def test_op_regex_match_power(self) -> None:
        m = _OP_RE.match("**", 0)
        assert m is not None and m.group() == "**"

    def test_op_regex_match_plus(self) -> None:
        m = _OP_RE.match("+", 0)
        assert m is not None and m.group() == "+"

    def test_op_regex_no_match_letter(self) -> None:
        m = _OP_RE.match("a", 0)
        assert m is None

    def test_field_normalize_close(self) -> None:
        assert _FIELD_NORMALIZE["close"] == "closes"
        assert _FIELD_NORMALIZE["volume"] == "volumes"

    def test_price_fields_contains_close(self) -> None:
        assert "close" in _PRICE_FIELDS
        assert "closes" in _PRICE_FIELDS

    def test_token_dataclass_pos_default(self) -> None:
        t = Token(TT_NUMBER, "1")
        assert t.pos == 0
        assert t.type == TT_NUMBER
        assert t.value == "1"
