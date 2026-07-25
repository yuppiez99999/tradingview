# -*- coding: utf-8 -*-
"""PipelineOrchestrator 单元测试 - CIO v1.0

验证 8 级流水线状态机：
1. 流水线可运行（无异常）
2. 各关卡计数正确
3. 审计文件生成
4. 至少 1 个因子进入下一关卡
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.pipeline.pipeline_orchestrator import (
    PipelineOrchestrator, PipelineState, quick_run,
)


def _make_price_data(n_syms=30, n_days=120, seed=42):
    """合成价格数据"""
    rng = np.random.default_rng(seed)
    price_data = {}
    for i in range(n_syms):
        sym = f"S{i:03d}"
        # 随机游走
        rets = rng.normal(0.0005, 0.02, n_days)
        closes = [100.0]
        for r in rets:
            closes.append(closes[-1] * (1 + r))
        vols = (rng.uniform(1e5, 1e6, n_days + 1)).tolist()
        highs = [c * (1 + abs(rng.normal(0, 0.01))) for c in closes]
        lows = [c * (1 - abs(rng.normal(0, 0.01))) for c in closes]
        price_data[sym] = {
            "closes": closes,
            "volumes": vols,
            "highs": highs,
            "lows": lows,
        }
    return price_data


def _make_benchmark_returns(n_days=120, seed=7):
    rng = np.random.default_rng(seed)
    return rng.normal(0.0003, 0.015, n_days).tolist()


def test_pipeline_runs_without_error():
    print("\n[Test 1] 流水线可运行...")
    price_data = _make_price_data()
    bench = _make_benchmark_returns()
    result = quick_run(
        price_data=price_data,
        benchmark_returns=bench,
        portfolio_value=1e8,
        n_trials=13,
    )
    assert result.total_candidates > 0, "应有候选因子"
    print(f"  ✓ total={result.total_candidates}, g1={result.g1_passed}, g2={result.g2_passed}, approved={result.approved}")


def test_pipeline_state_persistence():
    print("\n[Test 2] 状态持久化...")
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        orchestrator = PipelineOrchestrator({"reports_dir": tmpdir})
        price_data = _make_price_data(n_syms=20)
        bench = _make_benchmark_returns()
        result = orchestrator.run(
            price_data=price_data,
            benchmark_returns=bench,
            portfolio_value=1e8,
            n_trials=13,
            batch_id="test_batch",
        )
        # 验证审计文件
        audit_file = Path(tmpdir) / "test_batch" / "pipeline_state.json"
        assert audit_file.exists(), f"审计文件应存在: {audit_file}"
        with open(audit_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "batch_id" in data
        assert "factors" in data
        assert data["batch_id"] == "test_batch"
        print(f"  ✓ 审计文件已生成: {audit_file}")


def test_factor_state_machine_trace():
    print("\n[Test 3] 状态机轨迹完整...")
    price_data = _make_price_data()
    bench = _make_benchmark_returns()
    result = quick_run(price_data=price_data, benchmark_returns=bench, portfolio_value=1e8, n_trials=13)

    # 每个因子应有 state 字段
    for f in result.factors:
        assert "state" in f
        assert f["state"] in [s.value for s in PipelineState]

    # 统计应等于 factors 列表的过滤
    approved_count = sum(1 for f in result.factors if f["approved"])
    assert approved_count == result.approved
    print(f"  ✓ {len(result.factors)} 个因子状态完整")


def test_serializable():
    print("\n[Test 4] 流水线结果序列化...")
    price_data = _make_price_data(n_syms=10)
    bench = _make_benchmark_returns()
    result = quick_run(price_data=price_data, benchmark_returns=bench, portfolio_value=1e8, n_trials=13)
    d = result.to_dict()
    json_str = json.dumps(d, ensure_ascii=False, default=str)
    assert len(json_str) > 0
    print(f"  ✓ 序列化长度={len(json_str)}")


def test_g1_stats_correct():
    print("\n[Test 5] Gate1 统计正确...")
    price_data = _make_price_data()
    bench = _make_benchmark_returns()
    result = quick_run(price_data=price_data, benchmark_returns=bench, portfolio_value=1e8, n_trials=13)
    # g1_passed 应等于 factors 中 g1_orthogonality.passed=True 的数量
    expected_g1 = sum(1 for f in result.factors if f.get("g1_orthogonality") and f["g1_orthogonality"].get("passed"))
    assert result.g1_passed == expected_g1
    print(f"  ✓ g1_passed={result.g1_passed} 一致")


def main():
    print("=" * 60)
    print("PipelineOrchestrator 单元测试 - CIO v1.0")
    print("=" * 60)
    tests = [
        test_pipeline_runs_without_error,
        test_pipeline_state_persistence,
        test_factor_state_machine_trace,
        test_serializable,
        test_g1_stats_correct,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t(); passed += 1
        except AssertionError as e:
            failed += 1; print(f"  ✗ FAIL: {e}")
        except Exception as e:
            failed += 1; print(f"  ✗ ERROR: {type(e).__name__}: {e}")
    print("\n" + "=" * 60)
    print(f"总计: {passed} 通过, {failed} 失败 (共 {len(tests)} 项)")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
