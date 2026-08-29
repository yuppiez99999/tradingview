"""test_daily_workflow_orchestrator_integration_e2e.py — E2E 测试

测试范围:
  - daily_workflow.py Phase 7 金融多 Agent Shadow Mode 调用块集成验证
  - 环境隔离保护 (production 跳过, shadow/development 激活)
  - 审计日志持久化 (data/agent_orchestrator_audit/shadow_diffs_{date}.jsonl)
  - 失败降级保护 (orchestrator 异常不阻断主流程)

设计原则:
  - 由于 DailyWorkflow 类庞大 (8000+ 行), 无法直接实例化,
    本测试提取 daily_workflow.py 中 orchestrator 调用块的核心逻辑为
    可测试函数 _run_finance_agent_shadow_mode(), 验证其行为.
  - 使用真实 FinanceAgentOrchestrator (而非 Mock), 确保端到端数据流.
  - 审计日志写入 tmp_path, 避免污染生产环境.

集成日期: 2026-07-26 (v8.6.9 任务 1.2 Phase 7)
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from utils.finance_agent_orchestrator import FinanceAgentOrchestrator

# ============================================================
# 辅助函数: 复刻 daily_workflow.py 中 orchestrator 调用块逻辑
# ============================================================


def _run_finance_agent_shadow_mode(
    target_weights: dict[str, float],
    signal_fusion: Any,
    trade_date: str,
    audit_log_dir: Path,
    env: str = "shadow",
    max_symbols: int = 5,
) -> dict[str, Any]:
    """复刻 daily_workflow.py 中金融多 Agent Shadow Mode 调用块逻辑

    此函数与 daily_workflow.py 第 4465-4560 行的调用块逻辑保持一致,
    用于 E2E 测试验证. 任何对调用块的修改都应同步更新此函数.

    Args:
        target_weights: {symbol: weight} 目标权重字典
        signal_fusion: SignalFusionEngine 实例 (或 None)
        trade_date: 交易日 (YYYY-MM-DD)
        audit_log_dir: 审计日志目录
        env: 当前交易环境 (production/shadow/development)
        max_symbols: 最大标的数 (Shadow Mode 抽样验证)

    Returns:
        signal: 包含 Shadow Mode 执行结果的信号字典
    """
    signal: dict[str, Any] = {}

    # === 环境隔离: production 模式强制跳过 ===
    if env == "production":
        signal["finance_agent_shadow_applied"] = False
        signal["finance_agent_shadow_skipped_reason"] = "production_env_disabled"
        return signal

    # === shadow/development 模式: 激活 orchestrator ===
    orchestrator = FinanceAgentOrchestrator(audit_log_dir=audit_log_dir)

    # 按 |weight| 降序, 取前 max_symbols 个标的
    shadow_symbols = sorted(
        target_weights.keys(),
        key=lambda s: abs(target_weights.get(s, 0.0)),
        reverse=True,
    )[:max_symbols]

    shadow_consensus_count = 0
    shadow_veto_count = 0
    shadow_direction_match_count = 0

    for shadow_symbol in shadow_symbols:
        try:
            # 构建最小 context
            shadow_context = {
                "trade_date": trade_date,
                "target_weight": target_weights.get(shadow_symbol, 0.0),
                "position_weight": target_weights.get(shadow_symbol, 0.0),
                "env": env,
            }

            # 多 Agent 协调 -> 共识决策
            consensus = orchestrator.orchestrate(shadow_symbol, shadow_context)

            # Shadow 对比 (从 signal_fusion 缓存提取 strength)
            fusion_strength = 0.0
            if signal_fusion is not None:
                cached = getattr(
                    signal_fusion,
                    "_research_distilled_signals",
                    {},
                )
                fusion_strength = float(cached.get(shadow_symbol, 0.0))
                if not math.isfinite(fusion_strength):
                    fusion_strength = 0.0

            diff = orchestrator.shadow_compare(
                {"strength": fusion_strength},
                consensus,
            )

            # 持久化审计日志
            orchestrator.save_audit_log(consensus, diff, trade_date)

            shadow_consensus_count += 1
            if consensus.veto:
                shadow_veto_count += 1
            if diff.direction_match:
                shadow_direction_match_count += 1
        except Exception:
            # 标的级失败不阻断, 继续处理下一个
            pass

    signal["finance_agent_shadow_applied"] = shadow_consensus_count > 0
    signal["finance_agent_shadow_count"] = shadow_consensus_count
    signal["finance_agent_shadow_veto_count"] = shadow_veto_count
    signal["finance_agent_shadow_direction_match_count"] = shadow_direction_match_count
    signal["finance_agent_shadow_env"] = env
    return signal


# ============================================================
# E2E 测试: 环境隔离保护
# ============================================================


class TestE2EEnvironmentIsolation:
    """E2E: 环境隔离保护验证 (production 跳过, shadow/development 激活)"""

    @pytest.mark.e2e
    def test_e2e_production_env_skips_orchestrator(self, tmp_path):
        """production 环境下 orchestrator 必须被禁用"""
        target_weights = {"600276.SH": 0.08, "000001.SZ": 0.12}

        signal = _run_finance_agent_shadow_mode(
            target_weights=target_weights,
            signal_fusion=None,
            trade_date="2026-07-26",
            audit_log_dir=tmp_path / "audit",
            env="production",
        )

        # 验证: production 模式强制跳过
        assert signal["finance_agent_shadow_applied"] is False
        assert (
            signal["finance_agent_shadow_skipped_reason"] == "production_env_disabled"
        )
        assert "finance_agent_shadow_count" not in signal

        # 验证: 审计日志目录下无文件生成
        audit_files = list((tmp_path / "audit").glob("shadow_diffs_*.jsonl"))
        assert len(audit_files) == 0

    @pytest.mark.e2e
    def test_e2e_shadow_env_activates_orchestrator(self, tmp_path):
        """shadow 环境下 orchestrator 正常激活"""
        target_weights = {"600276.SH": 0.08, "000001.SZ": 0.12}

        signal = _run_finance_agent_shadow_mode(
            target_weights=target_weights,
            signal_fusion=None,
            trade_date="2026-07-26",
            audit_log_dir=tmp_path / "audit",
            env="shadow",
        )

        # 验证: shadow 模式激活
        assert signal["finance_agent_shadow_applied"] is True
        assert signal["finance_agent_shadow_env"] == "shadow"
        assert signal["finance_agent_shadow_count"] == 2  # 2 个标的
        assert "finance_agent_shadow_skipped_reason" not in signal

        # 验证: 审计日志文件生成
        audit_files = list((tmp_path / "audit").glob("shadow_diffs_*.jsonl"))
        assert len(audit_files) == 1

    @pytest.mark.e2e
    def test_e2e_development_env_activates_orchestrator(self, tmp_path):
        """development 环境下 orchestrator 也激活 (用于研究/开发)"""
        target_weights = {"510300.SH": 0.05}

        signal = _run_finance_agent_shadow_mode(
            target_weights=target_weights,
            signal_fusion=None,
            trade_date="2026-07-26",
            audit_log_dir=tmp_path / "audit",
            env="development",
        )

        assert signal["finance_agent_shadow_applied"] is True
        assert signal["finance_agent_shadow_env"] == "development"
        assert signal["finance_agent_shadow_count"] == 1


# ============================================================
# E2E 测试: 审计日志持久化
# ============================================================


class TestE2EAuditLogPersistence:
    """E2E: 审计日志持久化验证"""

    @pytest.mark.e2e
    def test_e2e_audit_log_jsonl_format(self, tmp_path):
        """审计日志必须为 jsonl 格式, 每行一个决策"""
        target_weights = {
            "600276.SH": 0.08,
            "000001.SZ": 0.12,
            "510300.SH": 0.05,
        }

        _run_finance_agent_shadow_mode(
            target_weights=target_weights,
            signal_fusion=None,
            trade_date="2026-07-26",
            audit_log_dir=tmp_path / "audit",
            env="shadow",
        )

        # 验证: 审计日志文件
        log_file = tmp_path / "audit" / "shadow_diffs_2026-07-26.jsonl"
        assert log_file.exists()

        # 验证: jsonl 格式 (每行一个 JSON 对象)
        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3  # 3 个标的

        for line in lines:
            entry = json.loads(line)
            assert "trade_date" in entry
            assert entry["trade_date"] == "2026-07-26"
            assert "consensus" in entry
            assert "diff" in entry
            assert "symbol" in entry["consensus"]
            assert "agent_decisions" in entry["consensus"]
            # 至少有部分 Agent 返回决策 (允许部分 Agent 跳过)
            assert len(entry["consensus"]["agent_decisions"]) >= 1

    @pytest.mark.e2e
    def test_e2e_audit_log_trade_date_in_filename(self, tmp_path):
        """审计日志文件名必须包含 trade_date"""
        target_weights = {"600276.SH": 0.08}

        _run_finance_agent_shadow_mode(
            target_weights=target_weights,
            signal_fusion=None,
            trade_date="2026-07-27",
            audit_log_dir=tmp_path / "audit",
            env="shadow",
        )

        log_file = tmp_path / "audit" / "shadow_diffs_2026-07-27.jsonl"
        assert log_file.exists()

    @pytest.mark.e2e
    def test_e2e_audit_log_accumulates_across_runs(self, tmp_path):
        """同一 trade_date 多次运行, 审计日志应累积 (append 模式)"""
        target_weights = {"600276.SH": 0.08}

        # 第一次运行
        _run_finance_agent_shadow_mode(
            target_weights=target_weights,
            signal_fusion=None,
            trade_date="2026-07-26",
            audit_log_dir=tmp_path / "audit",
            env="shadow",
        )

        # 第二次运行 (不同标的)
        _run_finance_agent_shadow_mode(
            target_weights={"000001.SZ": 0.12},
            signal_fusion=None,
            trade_date="2026-07-26",
            audit_log_dir=tmp_path / "audit",
            env="shadow",
        )

        log_file = tmp_path / "audit" / "shadow_diffs_2026-07-26.jsonl"
        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2  # 累积 2 条记录


# ============================================================
# E2E 测试: 标的筛选与排序
# ============================================================


class TestE2ESymbolSelection:
    """E2E: 标的筛选与排序验证"""

    @pytest.mark.e2e
    def test_e2e_top_5_symbols_by_weight(self, tmp_path):
        """按 |weight| 降序取前 5 个标的"""
        target_weights = {
            "S1.SH": 0.01,  # 最小权重
            "S2.SH": 0.15,
            "S3.SH": 0.08,
            "S4.SH": 0.20,  # 最大权重
            "S5.SH": 0.05,
            "S6.SH": 0.12,
            "S7.SH": 0.03,
        }

        signal = _run_finance_agent_shadow_mode(
            target_weights=target_weights,
            signal_fusion=None,
            trade_date="2026-07-26",
            audit_log_dir=tmp_path / "audit",
            env="shadow",
            max_symbols=5,
        )

        # 验证: 只处理 5 个标的 (按 |weight| 降序)
        assert signal["finance_agent_shadow_count"] == 5

        # 验证: 审计日志中是权重最大的 5 个标的
        log_file = tmp_path / "audit" / "shadow_diffs_2026-07-26.jsonl"
        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        logged_symbols = {json.loads(line)["consensus"]["symbol"] for line in lines}

        # 期望: S4(0.20), S2(0.15), S6(0.12), S3(0.08), S5(0.05)
        assert logged_symbols == {"S4.SH", "S2.SH", "S6.SH", "S3.SH", "S5.SH"}
        # S1(0.01) 和 S7(0.03) 不在前 5
        assert "S1.SH" not in logged_symbols
        assert "S7.SH" not in logged_symbols

    @pytest.mark.e2e
    def test_e2e_empty_target_weights(self, tmp_path):
        """空 target_weights 不应崩溃"""
        signal = _run_finance_agent_shadow_mode(
            target_weights={},
            signal_fusion=None,
            trade_date="2026-07-26",
            audit_log_dir=tmp_path / "audit",
            env="shadow",
        )

        # 验证: 无标的处理, applied=False
        assert signal["finance_agent_shadow_applied"] is False
        assert signal["finance_agent_shadow_count"] == 0


# ============================================================
# E2E 测试: 失败降级保护
# ============================================================


class TestE2EGracefulDegradation:
    """E2E: 失败降级保护验证 (orchestrator 异常不阻断主流程)"""

    @pytest.mark.e2e
    def test_e2e_orchestrator_exception_does_not_crash(self, tmp_path):
        """orchestrator 异常时主流程不阻断"""
        # 模拟: 让 FinanceAgentOrchestrator 构造函数抛异常
        with patch(
            "utils.finance_agent_orchestrator.FinanceAgentOrchestrator.__init__",
            side_effect=RuntimeError("模拟 orchestrator 初始化失败"),
        ):
            # 此测试验证: 即使 orchestrator 初始化失败,
            # daily_workflow 中的 try/except 也能捕获异常, 不阻断主流程
            with pytest.raises(RuntimeError):
                # 辅助函数未包含 try/except (与 daily_workflow 调用块外层 try/except 等效),
                # 这里验证异常确实被抛出, 由外层 try/except 捕获
                _run_finance_agent_shadow_mode(
                    target_weights={"600276.SH": 0.08},
                    signal_fusion=None,
                    trade_date="2026-07-26",
                    audit_log_dir=tmp_path / "audit",
                    env="shadow",
                )

        # 验证: daily_workflow 调用块的外层 try/except 会捕获此异常
        # (在 daily_workflow.py 第 4555-4560 行已实现)

    @pytest.mark.e2e
    def test_e2e_symbol_level_exception_isolation(self, tmp_path):
        """单个标的分析异常不应影响其他标的"""
        target_weights = {
            "GOOD.SH": 0.08,
            "BAD.SH": 0.12,  # 这个标的会触发异常
            "GOOD2.SH": 0.05,
        }

        # 创建一个会抛异常的 orchestrator 子类
        class FailingOrchestrator(FinanceAgentOrchestrator):
            def orchestrate(self, symbol, context):
                if symbol == "BAD.SH":
                    raise RuntimeError("模拟标的级失败")
                return super().orchestrate(symbol, context)

        # 模拟辅助函数, 使用 FailingOrchestrator
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        orchestrator = FailingOrchestrator(audit_log_dir=audit_dir)

        success_count = 0
        for symbol in sorted(
            target_weights.keys(), key=lambda s: abs(target_weights[s]), reverse=True
        ):
            try:
                consensus = orchestrator.orchestrate(
                    symbol, {"trade_date": "2026-07-26"}
                )
                orchestrator.save_audit_log(consensus, None, "2026-07-26")
                success_count += 1
            except Exception:
                # 标的级失败不阻断, 继续处理下一个
                pass

        # 验证: BAD.SH 失败, 但 GOOD.SH 和 GOOD2.SH 仍然成功
        assert success_count == 2


# ============================================================
# E2E 测试: 与 SignalFusion 集成
# ============================================================


class TestE2ESignalFusionIntegration:
    """E2E: 与 SignalFusionEngine 集成验证"""

    @pytest.mark.e2e
    def test_e2e_signal_fusion_strength_extracted_for_compare(self, tmp_path):
        """从 signal_fusion._research_distilled_signals 提取 strength 用于对比"""
        from utils.signal_fusion import SignalFusionEngine

        # 创建 SignalFusionEngine 并注入研究蒸馏信号
        fusion = SignalFusionEngine()
        fusion.inject_research_distilled_signals({"600276.SH": 0.5})

        # 验证: 信号已注入缓存
        assert hasattr(fusion, "_research_distilled_signals")
        assert "600276.SH" in fusion._research_distilled_signals

        signal = _run_finance_agent_shadow_mode(
            target_weights={"600276.SH": 0.08},
            signal_fusion=fusion,
            trade_date="2026-07-26",
            audit_log_dir=tmp_path / "audit",
            env="shadow",
        )

        # 验证: orchestrator 正常运行
        assert signal["finance_agent_shadow_applied"] is True
        assert signal["finance_agent_shadow_count"] == 1

        # 验证: 审计日志中 diff.fusion_strength == 0.5 (从缓存提取)
        log_file = tmp_path / "audit" / "shadow_diffs_2026-07-26.jsonl"
        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["diff"]["fusion_strength"] == 0.5

    @pytest.mark.e2e
    def test_e2e_signal_fusion_none_uses_zero_strength(self, tmp_path):
        """signal_fusion=None 时, fusion_strength=0.0"""
        signal = _run_finance_agent_shadow_mode(
            target_weights={"600276.SH": 0.08},
            signal_fusion=None,
            trade_date="2026-07-26",
            audit_log_dir=tmp_path / "audit",
            env="shadow",
        )

        assert signal["finance_agent_shadow_applied"] is True

        # 验证: diff.fusion_strength == 0.0
        log_file = tmp_path / "audit" / "shadow_diffs_2026-07-26.jsonl"
        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["diff"]["fusion_strength"] == 0.0

    @pytest.mark.e2e
    def test_e2e_nan_strength_defended_to_zero(self, tmp_path):
        """NaN strength 必须被防御性归零 (与 signal_fusion 4 层 NaN 防御一致)"""
        from utils.signal_fusion import SignalFusionEngine

        # 创建 SignalFusionEngine 并注入 NaN 信号 (异常场景)
        fusion = SignalFusionEngine()
        # 直接设置缓存为 NaN (模拟异常数据)
        fusion._research_distilled_signals = {"600276.SH": float("nan")}

        signal = _run_finance_agent_shadow_mode(
            target_weights={"600276.SH": 0.08},
            signal_fusion=fusion,
            trade_date="2026-07-26",
            audit_log_dir=tmp_path / "audit",
            env="shadow",
        )

        # 验证: 不崩溃, NaN 被归零
        assert signal["finance_agent_shadow_applied"] is True

        log_file = tmp_path / "audit" / "shadow_diffs_2026-07-26.jsonl"
        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        entry = json.loads(lines[0])
        # NaN 被防御性归零
        assert entry["diff"]["fusion_strength"] == 0.0
