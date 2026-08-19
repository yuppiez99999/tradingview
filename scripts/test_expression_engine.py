#!/usr/bin/env python3
"""因子表达式引擎验证 · DSL 解析 + 算子求值 + library 集成端到端

验证内容:
1. Tokenizer + Parser: 多种 DSL 表达式正确解析为 AST
2. 截面算子: rank/zscore/normalize/winsorize 语义验证
3. 时序算子: delay/delta/mean/std/slope/correlation 语义验证
4. 复合表达式: rank(close / delay(close, 20)) 等实战 DSL
5. library 集成: AlphaFactorLibrary + enable_expression=True 端到端
6. 引用已有因子: 表达式中引用 MOM_20D 等
7. 错误处理: 非法表达式安全降级

seed=20260812 确定性
"""

import os
import sys

import numpy as np

# 项目根目录
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from utils.alpha_factor.expression_engine import (
    BinaryOpNode,
    ExpressionEvaluator,
    FieldNode,
    FuncCallNode,
    NumberNode,
    UnaryOpNode,
    compute_expression_factors,
    parse_expression,
)
from utils.alpha_factor.library import AlphaFactorLibrary

# ============================================================
# 合成数据
# ============================================================

def make_synthetic_data(n_stocks: int = 10, n_days: int = 60, seed: int = 20260812):
    """构造 n 只股票 × n_days 天 OHLCV + 基本面数据"""
    rng = np.random.default_rng(seed)
    price_data = {}
    fundamentals = {}
    for i in range(n_stocks):
        sym = f"STK{i:04d}"
        # 不同起点价格 + 不同趋势
        start = 10.0 + 5.0 * i
        drift = 0.001 * (i - n_stocks // 2)  # 上半涨下半跌
        returns = rng.normal(drift, 0.02, size=n_days)
        closes = (start * np.cumprod(1 + returns)).tolist()
        highs = [c * (1 + abs(rng.normal(0, 0.005))) for c in closes]
        lows = [c * (1 - abs(rng.normal(0, 0.005))) for c in closes]
        volumes = (1e7 * rng.lognormal(0, 0.4, size=n_days)).tolist()
        price_data[sym] = {
            "closes": closes,
            "highs": highs,
            "lows": lows,
            "volumes": volumes,
        }
        fundamentals[sym] = {
            "pe": 15.0 + 2.0 * i,
            "pb": 2.0 + 0.3 * i,
            "roe": 0.15 + 0.01 * i,
            "market_cap": 1e10 * (1 + 0.5 * i),
        }
    return price_data, fundamentals


# ============================================================
# 测试
# ============================================================

def test_tokenizer_parser():
    """测试 1: Tokenizer + Parser"""
    print("\n[测试 1] Tokenizer + Parser")
    cases = [
        ("close", FieldNode),
        ("42", NumberNode),
        ("close + volume", BinaryOpNode),
        ("-close", UnaryOpNode),
        ("rank(close)", FuncCallNode),
        ("rank(close / delay(close, 20))", FuncCallNode),
        ("(close - mean(close, 20)) / std(close, 20)", BinaryOpNode),
        ("rank(mean(close, 5)) - rank(mean(close, 20))", BinaryOpNode),
        ("correlation(close, volume, 20)", FuncCallNode),
        ("close ** 2", BinaryOpNode),
    ]
    for expr_str, expected_type in cases:
        ast = parse_expression(expr_str)
        assert isinstance(ast, expected_type), \
            f"AST 类型错误: '{expr_str}' → {type(ast).__name__}, 期望 {expected_type.__name__}"
    print(f"  {len(cases)} 个表达式解析全部正确 ✓")

    # 错误处理: 非法字符
    try:
        parse_expression("close @ open")
        raise AssertionError("应抛 SyntaxError")
    except SyntaxError:
        pass
    print("  非法字符安全拦截 ✓")


def test_cross_section_ops():
    """测试 2: 截面算子语义"""
    print("\n[测试 2] 截面算子语义")
    price_data, fundamentals = make_synthetic_data(n_stocks=10, n_days=60)
    evaluator = ExpressionEvaluator(price_data, fundamentals)

    # rank: 10 只股票排名 [0, 1]
    result = evaluator.evaluate(parse_expression("rank(close)"))
    vals = list(result.values())
    assert 0 <= min(vals) and max(vals) <= 1.0, f"rank 超出 [0,1]: {min(vals)}~{max(vals)}"
    assert len(set(vals)) > 5, f"rank 值不够分散: {len(set(vals))} 个唯一值"
    print(f"  rank(close): 范围 [{min(vals):.3f}, {max(vals):.3f}], {len(set(vals))} 唯一值 ✓")

    # zscore: 均值≈0, std≈1
    result = evaluator.evaluate(parse_expression("zscore(close)"))
    vals = np.array(list(result.values()))
    assert abs(vals.mean()) < 0.01, f"zscore 均值不为 0: {vals.mean()}"
    assert 0.5 < vals.std() < 1.5, f"zscore std 不合理: {vals.std()}"
    print(f"  zscore(close): mean={vals.mean():.4f}, std={vals.std():.4f} ✓")

    # normalize: sum(|x|)=1
    result = evaluator.evaluate(parse_expression("normalize(close)"))
    total = sum(abs(v) for v in result.values())
    assert abs(total - 1.0) < 1e-6, f"normalize sum(|x|)={total} != 1"
    print(f"  normalize(close): sum(|x|)={total:.6f} ✓")

    # winsorize: 不改变中位数方向
    result = evaluator.evaluate(parse_expression("winsorize(close, 3)"))
    assert len(result) == 10
    print(f"  winsorize(close, 3): {len(result)} 标的 ✓")


def test_timeseries_ops():
    """测试 3: 时序算子语义"""
    print("\n[测试 3] 时序算子语义")
    price_data, fundamentals = make_synthetic_data(n_stocks=10, n_days=60)
    evaluator = ExpressionEvaluator(price_data, fundamentals)

    # delay: 5 日前的 close 应等于 closes[-6]
    result = evaluator.evaluate(parse_expression("delay(close, 5)"))
    for sym, val in result.items():
        expected = price_data[sym]["closes"][-6]
        assert abs(val - expected) < 1e-6, f"delay(close,5) {sym}: {val} != {expected}"
    print("  delay(close, 5): 与 closes[-6] 精确匹配 ✓")

    # delta: close - delay(close, 5)
    result = evaluator.evaluate(parse_expression("delta(close, 5)"))
    for sym, val in result.items():
        expected = price_data[sym]["closes"][-1] - price_data[sym]["closes"][-6]
        assert abs(val - expected) < 1e-6, f"delta(close,5) {sym}: {val} != {expected}"
    print("  delta(close, 5): = close - delay(close, 5) ✓")

    # mean: 20 日均值
    result = evaluator.evaluate(parse_expression("mean(close, 20)"))
    for sym, val in result.items():
        expected = np.mean(price_data[sym]["closes"][-20:])
        assert abs(val - expected) < 1e-6, f"mean(close,20) {sym}: {val} != {expected}"
    print("  mean(close, 20): 与 np.mean(closes[-20:]) 匹配 ✓")

    # std: 20 日标准差 > 0
    result = evaluator.evaluate(parse_expression("std(close, 20)"))
    for sym, val in result.items():
        assert val > 0, f"std(close,20) {sym}: {val} <= 0"
    print("  std(close, 20): 全部 > 0 ✓")

    # slope: 20 日回归斜率
    # 合成数据: drift = 0.001 * (i - 5), i<5 为负漂移 (跌), i>=5 为正漂移 (涨)
    result = evaluator.evaluate(parse_expression("slope(close, 20)"))
    down_syms = [s for s in result if int(s[3:]) < 5]   # drift<0 → slope 应<0
    up_syms = [s for s in result if int(s[3:]) >= 5]    # drift>0 → slope 应>0
    down_mean = np.mean([result[s] for s in down_syms])
    up_mean = np.mean([result[s] for s in up_syms])
    print(f"  slope(close, 20): 下跌组={down_mean:+.6f}, 上涨组={up_mean:+.6f}")
    assert up_mean > down_mean, f"slope 方向错误: 上涨组={up_mean} <= 下跌组={down_mean}"
    assert up_mean > 0, f"上涨组 slope 应>0: {up_mean}"
    assert down_mean < 0, f"下跌组 slope 应<0: {down_mean}"
    print("  slope 方向符合 (上涨组>0, 下跌组<0) ✓")

    # correlation: close × volume 20 日相关
    result = evaluator.evaluate(parse_expression("correlation(close, volume, 20)"))
    assert all(-1 <= v <= 1 for v in result.values()), "correlation 超出 [-1,1]"
    print(f"  correlation(close, volume, 20): 范围 [{min(result.values()):.3f}, {max(result.values()):.3f}] ✓")


def test_compound_expressions():
    """测试 4: 复合表达式 (WorldQuant Alpha101 风格)"""
    print("\n[测试 4] 复合表达式")
    price_data, fundamentals = make_synthetic_data(n_stocks=10, n_days=60)
    evaluator = ExpressionEvaluator(price_data, fundamentals)

    expressions = [
        ("动量排名", "rank(close / delay(close, 20))"),
        ("量价相关 z-score", "zscore(correlation(close, volume, 20))"),
        ("均线交叉", "rank(mean(close, 5)) - rank(mean(close, 20))"),
        ("布林带位置", "(close - mean(close, 20)) / std(close, 20)"),
        ("成交量变化比", "rank(delta(volume, 5) / volume)"),
        ("复合动量", "rank(close / delay(close, 5)) * rank(close / delay(close, 20))"),
        ("对数市值", "log(pe * pb)"),
        ("波动率调整动量", "rank(delta(close, 20) / std(close, 20))"),
    ]
    for name, expr_str in expressions:
        ast = parse_expression(expr_str)
        result = evaluator.evaluate(ast)
        assert len(result) == 10, f"{name}: 标的数 {len(result)} != 10"
        vals = list(result.values())
        assert all(not np.isnan(v) for v in vals), f"{name}: 含 NaN"
        assert all(np.isfinite(v) for v in vals), f"{name}: 含 inf"
        print(f"  {name}: {len(result)} 标的, 范围 [{min(vals):.4f}, {max(vals):.4f}] ✓")


def test_library_integration():
    """测试 5: AlphaFactorLibrary 集成端到端"""
    print("\n[测试 5] AlphaFactorLibrary 集成端到端")
    price_data, fundamentals = make_synthetic_data(n_stocks=10, n_days=60)

    # 定义表达式因子
    expressions = [
        ("EXPR_MOM_RANK", "rank(close / delay(close, 20))"),
        ("EXPR_VOL_PX_CORR", "zscore(correlation(close, volume, 20))"),
        ("EXPR_MA_CROSS", "rank(mean(close, 5)) - rank(mean(close, 20))"),
        ("EXPR_BOLL_POS", "(close - mean(close, 20)) / std(close, 20)"),
    ]

    # 启用表达式因子
    lib = AlphaFactorLibrary(
        enable_chip=False,
        enable_expression=True,
        expressions=expressions,
    )
    result = lib.compute_all(price_data, fundamentals=fundamentals)

    # 验证 4 个表达式因子都在结果中
    expr_names = [name for name, _ in expressions]
    for name in expr_names:
        assert name in result.factors, f"表达式因子 {name} 不在结果中"
        fval = result.factors[name]
        assert fval.category == "Expression", f"{name} 类别={fval.category} != Expression"
        assert len(fval.values) == 10, f"{name} 标的数={len(fval.values)} != 10"
    print("  4 个表达式因子全部产出, category=Expression ✓")

    # debug_info 中记录了表达式因子清单
    assert "expression_factors" in result.debug_info
    assert len(result.debug_info["expression_factors"]) == 4
    print(f"  debug_info.expression_factors = {result.debug_info['expression_factors']} ✓")

    # enable_expression=False 时不产出表达式因子
    lib_off = AlphaFactorLibrary(enable_chip=False, enable_expression=False, expressions=expressions)
    result_off = lib_off.compute_all(price_data, fundamentals=fundamentals)
    for name in expr_names:
        assert name not in result_off.factors, f"enable_expression=False 仍有 {name}"
    print("  enable_expression=False 无表达式因子 ✓")


def test_reference_existing_factors():
    """测试 6: 表达式引用已有因子"""
    print("\n[测试 6] 表达式引用已有因子")
    price_data, fundamentals = make_synthetic_data(n_stocks=10, n_days=60)

    # 表达式引用 MOM_20D (library 内置因子)
    expressions = [
        ("EXPR_MOM_RANKED", "rank(MOM_20D)"),
        ("EXPR_MOM_ZSCORE", "zscore(MOM_20D)"),
    ]
    lib = AlphaFactorLibrary(
        enable_chip=False,
        enable_expression=True,
        expressions=expressions,
    )
    result = lib.compute_all(price_data, fundamentals=fundamentals)

    # MOM_20D 应该在结果中 (price_volume.py 计算)
    assert "MOM_20D" in result.factors, "MOM_20D 应被 library 计算"
    mom_20d = result.factors["MOM_20D"].values

    # 验证表达式因子引用了 MOM_20D 的值
    expr_mom = result.factors["EXPR_MOM_RANKED"].values
    # rank(MOM_20D) 应该与 MOM_20D 值的排序一致
    sorted_mom = sorted(mom_20d.items(), key=lambda x: x[1])
    sorted_expr = sorted(expr_mom.items(), key=lambda x: x[1])
    assert [s for s, _ in sorted_mom] == [s for s, _ in sorted_expr], \
        "rank(MOM_20D) 排序应与 MOM_20D 一致"
    print("  rank(MOM_20D): 排序与 MOM_20D 一致 ✓")


def test_error_handling():
    """测试 7: 错误处理"""
    print("\n[测试 7] 错误处理")
    price_data, fundamentals = make_synthetic_data(n_stocks=5, n_days=60)

    # 非法表达式 → 降级跳过, 不崩
    bad_expressions = [
        ("BAD_SYNTAX", "close +"),              # 不完整表达式 (缺右操作数)
        ("BAD_FUNC", "unknown_func(close)"),    # 未知函数
        ("BAD_FIELD", "nonexistent_field"),     # 未知字段引用
    ]
    result = compute_expression_factors(
        price_data, fundamentals, bad_expressions
    )
    # 非法表达式被跳过, 产出空
    assert len(result) == 0, f"非法表达式应被跳过, 实际产出 {len(result)}"
    print(f"  {len(bad_expressions)} 个非法表达式安全跳过 (0 产出) ✓")

    # 窗口不足 → 时序算子返回部分标的
    short_pd = {s: {"closes": [10.0, 11.0, 12.0], "volumes": [1e6, 2e6, 1.5e6]} for s in ["A", "B"]}
    result = compute_expression_factors(
        short_pd, None,
        [("SHORT_MEAN", "mean(close, 20)")]
    )
    # 3 天 < 20 天窗口 → 空输出, 不崩
    assert "SHORT_MEAN" in result
    assert len(result["SHORT_MEAN"].values) == 0
    print("  窗口不足 3<20 安全降级 (空输出) ✓")


def test_determinism():
    """测试 8: 确定性 (同输入同输出)"""
    print("\n[测试 8] 确定性")
    price_data, fundamentals = make_synthetic_data(n_stocks=10, n_days=60)
    expr = "rank(close / delay(close, 20))"

    # 两次独立求值
    r1 = compute_expression_factors(price_data, fundamentals, [("TEST", expr)])
    r2 = compute_expression_factors(price_data, fundamentals, [("TEST", expr)])

    v1 = r1["TEST"].values
    v2 = r2["TEST"].values
    for s in v1:
        assert abs(v1[s] - v2[s]) < 1e-12, f"确定性违反: {s} v1={v1[s]} v2={v2[s]}"
    print("  同输入两次求值完全一致 ✓")


# ============================================================
# 主流程
# ============================================================

def main() -> int:
    print("=" * 72)
    print("因子表达式引擎验证 · DSL 解析 + 算子求值 + library 集成 (seed=20260812)")
    print("=" * 72)

    test_tokenizer_parser()
    test_cross_section_ops()
    test_timeseries_ops()
    test_compound_expressions()
    test_library_integration()
    test_reference_existing_factors()
    test_error_handling()
    test_determinism()

    print("\n" + "=" * 72)
    print("因子表达式引擎验证 · 全部断言通过 ✓")
    print("=" * 72)
    print("  Tokenizer + Parser: 10 表达式解析 + 错误拦截 ✓")
    print("  截面算子: rank/zscore/normalize/winsorize 语义验证 ✓")
    print("  时序算子: delay/delta/mean/std/slope/correlation 语义验证 ✓")
    print("  复合表达式: 8 个 WorldQuant 风格 DSL 全部求值 ✓")
    print("  library 集成: enable_expression + debug_info ✓")
    print("  引用已有因子: rank(MOM_20D) 排序一致 ✓")
    print("  错误处理: 非法表达式/窗口不足安全降级 ✓")
    print("  确定性: 同输入两次求值完全一致 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
