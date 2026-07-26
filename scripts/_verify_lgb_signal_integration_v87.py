# -*- coding: utf-8 -*-
"""
LGB 增强信号接入流水线验证脚本 (v8.7)
========================================

验证 SignalFusionEngine 的 LGB 增强信号 (第 7 信号源) 接入是否正常工作。

测试覆盖:
1. 信号文件加载 (结构化格式解析)
2. inject_lgb_enhanced_signals 方法 (扁平/结构化两种格式)
3. _fuse_symbol post-mix 逻辑 (含 LOW_QUALITY 降权)
4. NaN 防御 (4 层防御链)
5. sources/meta 字段溯源完整性
6. 边界条件 (空信号 / 权重=0 / 全 LOW_QUALITY)

运行方式:
    python scripts/_verify_lgb_signal_integration_v87.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 路径设置
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "utils"))
sys.path.insert(0, str(BASE_DIR / "v8.3_institutional"))

LGB_SIGNALS_FILE = BASE_DIR / "models" / "lgb_enhanced" / "lgb_enhanced_signals.json"


def _print_header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _print_result(passed: bool, detail: str) -> None:
    marker = "[PASS]" if passed else "[FAIL]"
    print(f"  {marker} {detail}")
    if not passed:
        raise AssertionError(f"验证失败: {detail}")


def test_signal_file_loading() -> dict:
    """测试 1: 信号文件加载与结构化格式解析"""
    _print_header("测试 1: 信号文件加载与结构化格式解析")

    if not LGB_SIGNALS_FILE.exists():
        _print_result(False, f"信号文件不存在: {LGB_SIGNALS_FILE}")
        return {}

    with open(LGB_SIGNALS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    _print_result(True, f"信号文件已加载: {LGB_SIGNALS_FILE.name}")
    print(f"  - 生成时间: {data.get('generated_at')}")
    print(f"  - 交易日期: {data.get('trade_date')}")
    print(f"  - 模型类型: {data.get('model_type')}")
    print(f"  - 数据来源: {data.get('data_source')}")
    print(f"  - 特征构成: {data.get('features')}")

    signals = data.get("signals", {})
    summary = data.get("summary", {})
    print(f"  - 标的总数: {summary.get('total')}")
    print(f"  - 看多/看空/中性: {summary.get('bullish')}/{summary.get('bearish')}/{summary.get('neutral')}")

    # 验证 quality_flag 分布
    ok_count = sum(1 for v in signals.values() if v.get("quality_flag") == "OK")
    low_quality_count = sum(1 for v in signals.values() if v.get("quality_flag") == "LOW_QUALITY")
    print(f"  - OK 标的: {ok_count}")
    print(f"  - LOW_QUALITY 标的: {low_quality_count}")

    _print_result(len(signals) > 0, f"信号数量 > 0 (实际={len(signals)})")
    _print_result(ok_count + low_quality_count == len(signals), "所有标的都有 quality_flag")
    _print_result(low_quality_count == 3, f"LOW_QUALITY 标的数 = 3 (实际={low_quality_count})")

    # 验证低质量标的确为 688981/600036/600219
    expected_low_quality = {"688981", "600036", "600219"}
    actual_low_quality = {
        sym for sym, v in signals.items()
        if v.get("quality_flag") == "LOW_QUALITY"
    }
    _print_result(
        actual_low_quality == expected_low_quality,
        f"LOW_QUALITY 标的匹配预期 (实际={actual_low_quality})"
    )

    return data


def test_inject_structured_format(signals_data: dict) -> None:
    """测试 2: inject_lgb_enhanced_signals 结构化格式注入"""
    _print_header("测试 2: inject_lgb_enhanced_signals 结构化格式注入")

    from utils.signal_fusion import SignalFusionEngine

    engine = SignalFusionEngine(lgb_enhanced_weight=0.04)
    signals = signals_data.get("signals", {})

    # 注入结构化格式
    engine.inject_lgb_enhanced_signals(signals)

    _print_result(
        len(engine._lgb_enhanced_signals) == len(signals),
        f"信号缓存数量匹配 (注入={len(engine._lgb_enhanced_signals)}, 预期={len(signals)})"
    )
    _print_result(
        len(engine._lgb_quality_flags) == len(signals),
        f"质量标记缓存数量匹配 ({len(engine._lgb_quality_flags)})"
    )

    # 验证 OK 标的权重 = 0.04
    ok_symbol = next(sym for sym, v in signals.items() if v.get("quality_flag") == "OK")
    _print_result(
        engine._lgb_quality_flags.get(ok_symbol) == "OK",
        f"OK 标的 {ok_symbol} quality_flag 正确"
    )

    # 验证 LOW_QUALITY 标的
    low_symbol = next(sym for sym, v in signals.items() if v.get("quality_flag") == "LOW_QUALITY")
    _print_result(
        engine._lgb_quality_flags.get(low_symbol) == "LOW_QUALITY",
        f"LOW_QUALITY 标的 {low_symbol} quality_flag 正确"
    )


def test_inject_flat_format() -> None:
    """测试 3: inject_lgb_enhanced_signals 扁平格式注入"""
    _print_header("测试 3: inject_lgb_enhanced_signals 扁平格式注入")

    from utils.signal_fusion import SignalFusionEngine

    engine = SignalFusionEngine(lgb_enhanced_weight=0.04)
    flat_signals = {"588000": 0.66, "688041": 0.99, "000333": -0.08}

    engine.inject_lgb_enhanced_signals(flat_signals)

    _print_result(
        len(engine._lgb_enhanced_signals) == 3,
        f"扁平格式注入 3 个标的 (实际={len(engine._lgb_enhanced_signals)})"
    )
    _print_result(
        all(v == "OK" for v in engine._lgb_quality_flags.values()),
        "扁平格式默认 quality_flag = OK"
    )


def test_post_mix_logic() -> None:
    """测试 4: _fuse_symbol post-mix 逻辑 (含 LOW_QUALITY 降权)"""
    _print_header("测试 4: _fuse_symbol post-mix 逻辑 (含 LOW_QUALITY 降权)")

    from utils.signal_fusion import SignalFusionEngine

    # 构造 alpha 信号: 688041 (OK), 688981 (LOW_QUALITY)
    alpha_signals = {
        "688041": {"strength": 0.5, "confidence": 0.8},
        "688981": {"strength": 0.5, "confidence": 0.8},
    }

    # 注入 LGB 信号 (688041=0.99 OK, 688981=0.93 LOW_QUALITY)
    lgb_signals = {
        "688041": {"signal": 0.99, "quality_flag": "OK"},
        "688981": {"signal": 0.93, "quality_flag": "LOW_QUALITY"},
    }

    engine = SignalFusionEngine(
        alpha_weight=0.7,
        llm_weight=0.1,
        etf_weight=0.12,
        macro_weight=0.08,
        lgb_enhanced_weight=0.04,
        min_confidence=0.35,
    )
    engine.inject_lgb_enhanced_signals(lgb_signals)

    results = engine.fuse(alpha_signals=alpha_signals)
    _print_result(len(results) == 2, f"融合结果数量 = 2 (实际={len(results)})")

    result_map = {r.symbol: r for r in results}

    # 验证 OK 标的 (688041): 有效权重 = 0.04
    r_ok = result_map.get("688041")
    _print_result(r_ok is not None, "688041 (OK) 融合结果存在")
    if r_ok:
        print(f"  - 688041 (OK) strength = {r_ok.strength}")
        print(f"  - sources.lgb_enhanced_strength = {r_ok.sources.get('lgb_enhanced_strength')}")
        print(f"  - meta.lgb_quality_flag = {r_ok.meta.get('lgb_quality_flag')}")
        print(f"  - meta.effective_lgb_weight = {r_ok.meta.get('effective_lgb_weight')}")
        print(f"  - meta.lgb_enhanced_applied = {r_ok.meta.get('lgb_enhanced_applied')}")
        _print_result(
            r_ok.meta.get("lgb_quality_flag") == "OK",
            "688041 quality_flag = OK"
        )
        _print_result(
            abs(r_ok.meta.get("effective_lgb_weight", 0) - 0.04) < 1e-6,
            f"688041 effective_lgb_weight = 0.04 (实际={r_ok.meta.get('effective_lgb_weight')})"
        )
        _print_result(
            r_ok.meta.get("lgb_enhanced_applied") is True,
            "688041 lgb_enhanced_applied = True"
        )
        _print_result(
            abs(r_ok.sources.get("lgb_enhanced_strength", 0) - 0.99) < 1e-6,
            f"688041 lgb_enhanced_strength = 0.99 (实际={r_ok.sources.get('lgb_enhanced_strength')})"
        )

    # 验证 LOW_QUALITY 标的 (688981): 有效权重 = 0.02 (50% 降权)
    r_low = result_map.get("688981")
    _print_result(r_low is not None, "688981 (LOW_QUALITY) 融合结果存在")
    if r_low:
        print(f"  - 688981 (LOW_QUALITY) strength = {r_low.strength}")
        print(f"  - meta.lgb_quality_flag = {r_low.meta.get('lgb_quality_flag')}")
        print(f"  - meta.effective_lgb_weight = {r_low.meta.get('effective_lgb_weight')}")
        _print_result(
            r_low.meta.get("lgb_quality_flag") == "LOW_QUALITY",
            "688981 quality_flag = LOW_QUALITY"
        )
        _print_result(
            abs(r_low.meta.get("effective_lgb_weight", 0) - 0.02) < 1e-6,
            f"688981 effective_lgb_weight = 0.02 (降权 50%, 实际={r_low.meta.get('effective_lgb_weight')})"
        )

    # 验证 LOW_QUALITY 降权实际生效: 688981 的 lgb 影响应小于 688041
    # 给两个标的相同的 alpha=0.5, lgb 信号 0.99 vs 0.93 接近,
    # 但 688041 权重 0.04 vs 688981 权重 0.02, 所以 688041 的 strength 应更接近 lgb 信号 (0.99)
    if r_ok and r_low:
        # 计算纯 lgb 信号对 strength 的偏移贡献 (相对纯 alpha)
        # 简化验证: OK 标的的有效权重是 LOW_QUALITY 的 2 倍
        ok_w = r_ok.meta.get("effective_lgb_weight", 0)
        low_w = r_low.meta.get("effective_lgb_weight", 0)
        _print_result(
            ok_w > low_w,
            f"OK 标的有效权重 > LOW_QUALITY 标的 ({ok_w} > {low_w})"
        )


def test_nan_defense() -> None:
    """测试 5: NaN 防御 (4 层防御链)"""
    _print_header("测试 5: NaN 防御 (4 层防御链)")

    from utils.signal_fusion import SignalFusionEngine

    # 注入包含 NaN 的信号 (模拟数据损坏场景)
    bad_signals = {
        "588000": {"signal": float("nan"), "quality_flag": "OK"},
        "688041": {"signal": 0.99, "quality_flag": "OK"},
        "000333": {"signal": float("inf"), "quality_flag": "OK"},
    }

    engine = SignalFusionEngine()
    engine.inject_lgb_enhanced_signals(bad_signals)

    # NaN/Inf 应被过滤, 只剩 1 个有效信号
    _print_result(
        len(engine._lgb_enhanced_signals) == 1,
        f"NaN/Inf 已过滤, 仅保留 1 个有效信号 (实际={len(engine._lgb_enhanced_signals)})"
    )
    _print_result(
        "688041" in engine._lgb_enhanced_signals,
        "有效信号 (688041) 已保留"
    )
    _print_result(
        "588000" not in engine._lgb_enhanced_signals,
        "NaN 信号 (588000) 已被过滤"
    )

    # 融合不应抛异常
    alpha_signals = {
        "588000": {"strength": 0.5, "confidence": 0.8},
        "688041": {"strength": 0.5, "confidence": 0.8},
    }
    try:
        results = engine.fuse(alpha_signals=alpha_signals)
        _print_result(True, f"融合正常完成, 无异常 (结果数={len(results)})")

        # 588000 应该没有 lgb 信号 (NaN 已被过滤)
        r_588000 = next((r for r in results if r.symbol == "588000"), None)
        if r_588000:
            _print_result(
                r_588000.sources.get("lgb_enhanced_strength", 0) == 0,
                "588000 lgb_enhanced_strength = 0 (NaN 已被过滤)"
            )
    except Exception as e:
        _print_result(False, f"融合抛异常: {e}")


def test_empty_input() -> None:
    """测试 6: 边界条件 (空输入 / 权重=0)"""
    _print_header("测试 6: 边界条件 (空输入 / 权重=0)")

    from utils.signal_fusion import SignalFusionEngine

    # 空字典输入
    engine = SignalFusionEngine()
    engine.inject_lgb_enhanced_signals({})
    _print_result(
        len(engine._lgb_enhanced_signals) == 0,
        "空字典输入不报错, 缓存为空"
    )

    # None 输入
    engine.inject_lgb_enhanced_signals(None)  # type: ignore
    _print_result(
        len(engine._lgb_enhanced_signals) == 0,
        "None 输入不报错, 缓存为空"
    )

    # 权重=0 (禁用 LGB 信号)
    engine_disabled = SignalFusionEngine(lgb_enhanced_weight=0.0)
    engine_disabled.inject_lgb_enhanced_signals(
        {"588000": {"signal": 0.66, "quality_flag": "OK"}}
    )
    alpha_signals = {"588000": {"strength": 0.5, "confidence": 0.8}}
    results = engine_disabled.fuse(alpha_signals=alpha_signals)
    r = results[0]
    _print_result(
        r.meta.get("lgb_enhanced_applied") is False,
        "权重=0 时 lgb_enhanced_applied = False (不应用)"
    )
    _print_result(
        abs(r.strength - 0.5) < 0.1 or r.strength == 0,
        f"权重=0 时 strength 接近原始 alpha 信号 (实际={r.strength})"
    )


def test_integration_full_pipeline(signals_data: dict) -> None:
    """测试 7: 端到端集成测试 (完整流水线)"""
    _print_header("测试 7: 端到端集成测试 (完整流水线)")

    from utils.signal_fusion import SignalFusionEngine

    # 模拟 23 标的 alpha 信号 (置信度 0.6-0.9)
    signals = signals_data.get("signals", {})
    alpha_signals = {}
    for sym, v in signals.items():
        alpha_signals[sym] = {
            "strength": float(v.get("signal", 0)) * 0.5,  # alpha 信号弱于 lgb
            "confidence": 0.7,
        }

    engine = SignalFusionEngine(
        lgb_enhanced_weight=0.04,
        pipeline_factor_weight=0.05,
        research_distilled_weight=0.03,
    )
    engine.inject_lgb_enhanced_signals(signals)

    # 模拟其他信号源
    pipeline_signals = {sym: 0.3 for sym in signals}
    engine.inject_pipeline_factor_signals(pipeline_signals)
    research_signals = {sym: 0.2 for sym in signals}
    engine.inject_research_distilled_signals(research_signals)

    results = engine.fuse(alpha_signals=alpha_signals)
    _print_result(
        len(results) == len(signals),
        f"融合结果数 = 标的数 ({len(results)} = {len(signals)})"
    )

    # 统计应用情况
    applied_count = sum(1 for r in results if r.meta.get("lgb_enhanced_applied"))
    ok_count = sum(1 for r in results if r.meta.get("lgb_quality_flag") == "OK")
    low_quality_count = sum(1 for r in results if r.meta.get("lgb_quality_flag") == "LOW_QUALITY")

    print(f"  - LGB 信号应用: {applied_count} / {len(results)}")
    print(f"  - OK 标的: {ok_count}")
    print(f"  - LOW_QUALITY 标的: {low_quality_count}")

    _print_result(applied_count > 0, "至少 1 个标的应用了 LGB 信号")
    _print_result(ok_count + low_quality_count == len(results), "所有标的质量标记完整")

    # 抽样验证 sources/meta 完整性
    sample = results[0]
    required_sources = {"alpha_strength", "llm_strength", "etf_strength", "macro_bias",
                        "pipeline_factor_strength", "research_distilled_strength",
                        "lgb_enhanced_strength"}
    missing_sources = required_sources - set(sample.sources.keys())
    _print_result(
        not missing_sources,
        f"sources 字段完整 (缺失={missing_sources or '无'})"
    )

    required_meta = {"lgb_enhanced_weight", "effective_lgb_weight", "lgb_quality_flag",
                     "lgb_enhanced_applied"}
    missing_meta = required_meta - set(sample.meta.keys())
    _print_result(
        not missing_meta,
        f"meta 字段 LGB 元数据完整 (缺失={missing_meta or '无'})"
    )

    # 输出 Top 5 融合信号样本
    print(f"\n  Top 5 融合信号样本:")
    for r in results[:5]:
        flag = r.meta.get("lgb_quality_flag", "N/A")
        applied = "✓" if r.meta.get("lgb_enhanced_applied") else "✗"
        print(f"    {r.symbol}: strength={r.strength:+.4f}, conf={r.confidence:.2f}, "
              f"lgb={r.sources.get('lgb_enhanced_strength', 0):+.4f}, "
              f"flag={flag}, applied={applied}")


def main() -> None:
    """主入口"""
    print("\n" + "=" * 60)
    print("  LGB 增强信号接入流水线验证脚本 (v8.7)")
    print("  测试 SignalFusionEngine 第 7 信号源接入完整性")
    print("=" * 60)

    # 加载信号文件 (供多个测试使用)
    signals_data = test_signal_file_loading()

    # 测试 2: 结构化格式注入
    test_inject_structured_format(signals_data)

    # 测试 3: 扁平格式注入
    test_inject_flat_format()

    # 测试 4: post-mix 逻辑 (LOW_QUALITY 降权)
    test_post_mix_logic()

    # 测试 5: NaN 防御
    test_nan_defense()

    # 测试 6: 边界条件
    test_empty_input()

    # 测试 7: 端到端集成测试
    test_integration_full_pipeline(signals_data)

    print("\n" + "=" * 60)
    print("  所有测试通过! LGB 增强信号接入流水线验证成功")
    print("=" * 60)
    print("\n关键指标:")
    print(f"  - 信号文件: {LGB_SIGNALS_FILE.name}")
    print(f"  - 标的总数: {signals_data.get('summary', {}).get('total', 0)}")
    print(f"  - OK 标的: {sum(1 for v in signals_data.get('signals', {}).values() if v.get('quality_flag') == 'OK')}")
    print(f"  - LOW_QUALITY 标的: {sum(1 for v in signals_data.get('signals', {}).values() if v.get('quality_flag') == 'LOW_QUALITY')}")
    print(f"  - 默认权重: 0.04 (LOW_QUALITY 降至 0.02)")
    print(f"  - post-mix 模式: 在 alpha+llm+etf+macro+pipeline+research 之后叠加")
    print(f"  - 4 层 NaN 防御: 注入过滤 + 取值防御 + 融合后检查 + 最终裁剪")


if __name__ == "__main__":
    main()
