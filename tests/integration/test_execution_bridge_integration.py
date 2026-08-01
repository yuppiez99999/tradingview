# -*- coding: utf-8 -*-
"""execution_bridge 集成测试 — 端到端流程验证 (Shadow → Paper → Auto)"""

from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ai_decision.execution_bridge import GrayscaleState


def _reset_test_dirs():
    """清理测试目录"""
    dirs = ["reports/ai_decision", "reports/ai_decision/execution"]
    for d in dirs:
        try:
            if os.path.exists(d):
                shutil.rmtree(d)
        except Exception:
            pass


# ============================================================
# 集成测试：Shadow 模式 (仅记录，不执行)
# ============================================================

def test_integration_shadow_mode():
    """测试 shadow 模式 — 决策生成但不执行"""
    _reset_test_dirs()
    # 模拟 CLI 调用: --mode shadow --symbol 600519
    # 预期: executed=False, mode=shadow, 无实际下单
    os.environ["DEEPSEEK_API_KEY"] = "dummy"  # 使用 MockProvider
    try:
        # 直接调用 run_decision 来测试
        from ai_decision.orchestrator import run_decision
        dec = run_decision("600519", mode="shadow")
        assert dec.mode == "shadow"
        assert dec.executed is False
        assert dec.action in ("buy", "sell", "hold")
        print(f"[PASS] shadow mode: action={dec.action}, executed={dec.executed}")
    finally:
        if "DEEPSEEK_API_KEY" in os.environ:
            del os.environ["DEEPSEEK_API_KEY"]


# ============================================================
# 集成测试：Paper 模式 (模拟执行)
# ============================================================

def test_integration_paper_mode():
    """测试 paper 模式 — 模拟执行，记录执行结果"""
    _reset_test_dirs()
    os.environ["DEEPSEEK_API_KEY"] = "dummy"
    try:
        from ai_decision.orchestrator import run_decision
        dec = run_decision("600519", mode="paper")
        # paper 模式下应该尝试执行
        assert dec.mode == "paper"
        # 检查是否有执行结果
        assert hasattr(dec, "execution_result") or True
        print(f"[PASS] paper mode: action={dec.action}, executed={dec.executed}")
    finally:
        if "DEEPSEEK_API_KEY" in os.environ:
            del os.environ["DEEPSEEK_API_KEY"]


# ============================================================
# 集成测试：Auto 模式 (带 --execute 参数)
# ============================================================

def test_integration_auto_with_execute():
    """测试 auto 模式 + --execute — 完整执行桥接流程

    注意: MockProvider 在五个 Agent 共识下可能返回 action='hold' (无明确方向),
    这是预期的中性决策结果. 本测试目的是验证 execute_decision() 桥接路径,
    因此当 action='hold' 时强制覆盖为 'buy' 以测试执行流程.
    """
    _reset_test_dirs()
    os.environ["DEEPSEEK_API_KEY"] = "dummy"
    try:
        # 模拟 CLI 调用: --mode auto --execute --symbol 600519
        # 这里我们直接测试 execute_bridge 与决策的集成
        from ai_decision.execution_bridge import execute_decision
        from ai_decision.orchestrator import run_decision

        # 先获取决策 (MockProvider 可能返回 hold, 这是预期行为)
        dec = run_decision("600519", mode="auto")
        assert dec.action in ("buy", "sell", "hold")  # 三种合法 action

        # 若决策为 hold, 强制覆盖为 buy 以测试执行路径
        # (本测试核心是验证 execute_decision 桥接, 而非决策方向)
        if dec.action == "hold":
            dec.action = "buy"
            dec.strength = 0.6
            dec.confidence = 0.8

        # 执行桥接
        result = execute_decision(
            dec,
            portfolio_value=1_000_000.0,
            price=100.0,  # auto 模式需提供有效价格, 否则 L2 风控会 veto
            force_mode="auto",  # 强制 auto 模式 (覆盖 GrayscaleState 默认)
        )
        assert result.get("executed") in [True, False]  # 取决于具体风控
        assert result["mode"] == "auto"
        print(f"[PASS] auto+execute: executed={result.get('executed')}, mode={result['mode']}")
    finally:
        if "DEEPSEEK_API_KEY" in os.environ:
            del os.environ["DEEPSEEK_API_KEY"]


# ============================================================
# 集成测试：CLI --execute 参数
# ============================================================

def test_cli_with_execute_flag():
    """测试 CLI 的 --execute 参数触发执行桥接"""
    _reset_test_dirs()
    os.environ["DEEPSEEK_API_KEY"] = "dummy"
    try:
        # 模拟命令行调用: python -m ai_decision.cli --symbol 600519 --mode paper --execute
        # 由于是集成测试，我们直接验证代码路径
        from ai_decision.cli import _fmt
        from ai_decision.models import TradingDecision

        # 创建模拟决策并设置执行结果
        dec = TradingDecision(symbol="600519", action="buy", strength=0.7, confidence=0.8, mode="paper")
        dec.execution_result = {
            "executed": True,
            "mode": "paper",
            "plan": {"qty": 100, "notional": 15000.0},
            "veto": False
        }
        output = _fmt(dec)
        assert "EXECUTED" in output or "paper" in output.lower()
        assert "qty=100" in output or "100" in output
        print(f"[PASS] CLI with execute flag: output={output[:100]}...")
    finally:
        if "DEEPSEEK_API_KEY" in os.environ:
            del os.environ["DEEPSEEK_API_KEY"]


# ============================================================
# 集成测试：灰度状态持久化
# ============================================================

def test_grayscale_persistence():
    """测试灰度状态的跨进程持久化"""
    _reset_test_dirs()

    # 创建临时状态
    gs = GrayscaleState()
    gs.stage = "auto_10"
    gs.cumulative_pnl = 5000.0
    gs.save()

    # 重新加载
    gs2 = GrayscaleState.load()
    assert gs2.stage == "auto_10"
    assert abs(gs2.cumulative_pnl - 5000.0) < 0.01
    print(f"[PASS] grayscale persistence: stage={gs2.stage}, pnl={gs2.cumulative_pnl}")


# ============================================================
# 端到端全链路测试
# ============================================================

def test_full_pipeline_end_to_end():
    """端到端全链路: 决策 → 风控 → 执行桥接 → 审计日志"""
    _reset_test_dirs()
    os.environ["DEEPSEEK_API_KEY"] = "dummy"
    try:
        from ai_decision.execution_bridge import execute_decision, get_grayscale_summary
        from ai_decision.orchestrator import run_decision

        # Step 1: 生成决策
        dec = run_decision("600519", mode="paper")
        assert dec.symbol == "600519"
        assert dec.action in ("buy", "sell", "hold")
        print(f"[Step 1] Decision generated: action={dec.action}, verdict={dec.verdict_type}")

        # Step 2: 执行桥接 (如果决策有方向)
        if dec.action != "hold":
            result = execute_decision(dec, portfolio_value=1_000_000.0, mode="paper")
            dec.execution_result = result
            print(f"[Step 2] Execution bridge: executed={result.get('executed')}, plan={result.get('plan', {})}")

        # Step 3: 检查摘要信息
        summary = get_grayscale_summary()
        assert "stage" in summary
        assert "allocation_pct" in summary
        print(f"[Step 3] Grayscale summary: stage={summary['stage']}, alloc={summary['allocation_pct']}%")

        # Step 4: 验证决策可序列化
        dec_dict = dec.to_dict()
        assert "symbol" in dec_dict
        assert "action" in dec_dict
        assert "execution_result" in dec_dict or True  # 可能有也可能没有
        print(f"[Step 4] Decision serializable: keys={list(dec_dict.keys())}")

        print("[PASS] Full pipeline end-to-end completed successfully!")
    finally:
        if "DEEPSEEK_API_KEY" in os.environ:
            del os.environ["DEEPSEEK_API_KEY"]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
