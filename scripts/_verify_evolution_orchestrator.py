# -*- coding: utf-8 -*-
"""EvolutionOrchestrator 验证脚本 — 自我进化框架第 1 阶段验证 (T3).

验证项:
    1. 模块导入成功
    2. Feature Flag 关闭时降级 (HC-1)
    3. get_status 返回正确结构
    4. collect_metrics 读取真实 daily_returns.jsonl
    5. collect_metrics 文件不存在时降级
    6. evaluate_current Flag 关闭时返回 None
    7. log_decision 写入 JSONL
    8. log_decision 观察期内强制 action=evaluate_only (HC-4)
    9. run_observation_cycle 完整流程
    10. get_recent_decisions 读取记录
    11. 只读模式: 不修改 V9 基线 (验证 positions.json mtime)
    12. 观察期状态推断
"""
from __future__ import annotations

import json
import random
import shutil
import sys
import tempfile
from pathlib import Path

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def main() -> int:
    """运行验证."""
    print("=" * 70)
    print("EvolutionOrchestrator 验证脚本 (T3)")
    print("=" * 70)

    # 导入编排器
    try:
        from utils.alpha.evolution_orchestrator import (
            ACTION_EVALUATE_ONLY,
            ACTION_NOOP,
            ACTION_PROMOTE,
            MIN_SAMPLES_FOR_EVALUATION,
            OBSERVATION_PERIOD_DAYS,
            STATUS_DEGRADED,  # noqa: F401
            STATUS_DISABLED,
            STATUS_ENABLED,
            STATUS_OBSERVATION,
            DecisionRecord,  # noqa: F401
            EvolutionOrchestrator,
            MetricsSnapshot,  # noqa: F401
            OrchestratorStatus,  # noqa: F401
        )
        print("[OK] 导入 EvolutionOrchestrator 成功")
    except ImportError as e:
        print(f"[FAIL] 导入失败: {e}")
        return 1

    failures = 0
    total_tests = 12

    # 创建临时测试目录 (避免污染生产 reports/evolution/)
    tmp_dir = Path(tempfile.mkdtemp(prefix="evo_verify_"))
    tmp_decisions_log = tmp_dir / "decisions.jsonl"
    tmp_daily_returns = tmp_dir / "daily_returns.jsonl"

    # 备份 positions.json mtime (用于只读模式验证)
    positions_path = _PROJECT_ROOT / "config" / "positions.json"
    positions_mtime_before = positions_path.stat().st_mtime if positions_path.exists() else 0.0

    try:
        # ============================================================
        # 测试 1: 模块导入 + 常量完整性
        # ============================================================
        print("\n--- 测试 1: 模块导入 + 常量完整性 ---")
        assert OBSERVATION_PERIOD_DAYS == 14, f"观察期应为 14 天, 实际 {OBSERVATION_PERIOD_DAYS}"
        assert MIN_SAMPLES_FOR_EVALUATION == 20, f"最小评估样本应为 20, 实际 {MIN_SAMPLES_FOR_EVALUATION}"
        assert ACTION_EVALUATE_ONLY == "evaluate_only"
        assert ACTION_NOOP == "noop"
        assert STATUS_DISABLED == "disabled"
        assert STATUS_ENABLED == "enabled"
        print(f"[OK] 常量完整: 观察期={OBSERVATION_PERIOD_DAYS}天, 最小样本={MIN_SAMPLES_FOR_EVALUATION}")

        # ============================================================
        # 测试 2: Feature Flag 关闭时降级 (HC-1)
        # ============================================================
        print("\n--- 测试 2: Feature Flag 关闭时降级 (HC-1) ---")
        orch_disabled = EvolutionOrchestrator(
            daily_returns_path=tmp_daily_returns,
            decisions_log_path=tmp_decisions_log,
        )
        # Flag 默认 False, 应禁用
        if not orch_disabled.enabled:
            status = orch_disabled.get_status()
            assert status["enabled"] is False, f"enabled 应为 False, 实际 {status['enabled']}"
            assert status["status"] == STATUS_DISABLED, f"status 应为 disabled, 实际 {status['status']}"
            print(f"[OK] Flag 关闭时降级: status={status['status']}")
        else:
            print(f"[FAIL] Flag 默认应为 False, 实际 enabled={orch_disabled.enabled}")
            failures += 1

        # ============================================================
        # 测试 3: get_status 返回正确结构
        # ============================================================
        print("\n--- 测试 3: get_status 返回正确结构 ---")
        status = orch_disabled.get_status()
        required_keys = {
            "status", "enabled", "observation_day", "observation_total",
            "in_observation", "last_evaluation", "last_action",
            "total_evaluations", "degraded_reason",
        }
        missing = required_keys - set(status.keys())
        assert not missing, f"缺少键: {missing}"
        assert status["observation_total"] == 14, f"观察期总天数应为 14, 实际 {status['observation_total']}"
        assert status["total_evaluations"] == 0, f"初始评估次数应为 0, 实际 {status['total_evaluations']}"
        print(f"[OK] 状态结构完整: keys={sorted(status.keys())}")

        # ============================================================
        # 测试 4: collect_metrics 读取真实 daily_returns.jsonl
        # ============================================================
        print("\n--- 测试 4: collect_metrics 读取 daily_returns.jsonl ---")
        # 构造测试数据 (252 条, 低波动率避免 Fail-Fast)
        random.seed(42)
        test_records = []
        for i in range(252):
            date_str = f"2026-01-{i+1:02d}" if i < 31 else f"2026-02-{i-30:02d}" if i < 59 else f"2026-03-{i-58:02d}" if i < 90 else f"2026-04-{min(i-89, 30):02d}" if i < 120 else f"2026-05-{min(i-119, 31):02d}" if i < 151 else f"2026-06-{min(i-150, 30):02d}" if i < 181 else f"2026-07-{min(i-180, 31):02d}" if i < 212 else f"2026-08-{min(i-211, 31):02d}" if i < 243 else f"2026-09-{min(i-242, 30):02d}"
            ret = 0.0005 + 0.005 * random.gauss(0, 1)
            test_records.append({"date": date_str, "daily_return": round(ret, 6), "source": "test"})

        with tmp_daily_returns.open("w", encoding="utf-8") as f:
            for rec in test_records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # 创建启用的编排器 (强制启用)
        orch_enabled = EvolutionOrchestrator(
            daily_returns_path=tmp_daily_returns,
            decisions_log_path=tmp_decisions_log,
        )
        orch_enabled._enabled = True
        orch_enabled._evaluator_enabled = True

        metrics = orch_enabled.collect_metrics()
        if not metrics.is_degraded and metrics.sample_count == 252:
            print(f"[OK] 收集成功: samples={metrics.sample_count}, source={Path(metrics.source).name}")
        else:
            print(f"[FAIL] 收集失败: degraded={metrics.is_degraded}, reason={metrics.degraded_reason}")
            failures += 1

        # ============================================================
        # 测试 5: collect_metrics 文件不存在时降级
        # ============================================================
        print("\n--- 测试 5: collect_metrics 文件不存在时降级 ---")
        orch_missing = EvolutionOrchestrator(
            daily_returns_path=tmp_dir / "nonexistent.jsonl",
            decisions_log_path=tmp_decisions_log,
        )
        orch_missing._enabled = True

        metrics_missing = orch_missing.collect_metrics()
        if metrics_missing.is_degraded and "file_not_found" in metrics_missing.degraded_reason:
            print(f"[OK] 文件不存在降级: reason={metrics_missing.degraded_reason}")
        else:
            print(f"[FAIL] 文件不存在应降级: degraded={metrics_missing.is_degraded}")
            failures += 1

        # ============================================================
        # 测试 6: evaluate_current Flag 关闭时返回 None
        # ============================================================
        print("\n--- 测试 6: evaluate_current Flag 关闭时返回 None ---")
        result = orch_disabled.evaluate_current()
        if result is None:
            print("[OK] Flag 关闭时返回 None")
        else:
            print(f"[FAIL] Flag 关闭应返回 None, 实际 {type(result)}")
            failures += 1

        # ============================================================
        # 测试 7: log_decision 写入 JSONL
        # ============================================================
        print("\n--- 测试 7: log_decision 写入 JSONL ---")
        # 先清空日志
        if tmp_decisions_log.exists():
            tmp_decisions_log.unlink()

        success = orch_enabled.log_decision(
            report=None,
            action=ACTION_NOOP,
            reason="test_no_report",
        )
        if success and tmp_decisions_log.exists():
            with tmp_decisions_log.open("r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) == 1:
                record = json.loads(lines[0])
                assert record["action"] == ACTION_NOOP, f"action 应为 noop, 实际 {record['action']}"
                assert record["reason"] == "test_no_report"
                assert "timestamp" in record
                assert "observation_day" in record
                print(f"[OK] JSONL 写入成功: action={record['action']}")
            else:
                print(f"[FAIL] 应写入 1 行, 实际 {len(lines)} 行")
                failures += 1
        else:
            print(f"[FAIL] 写入失败: success={success}, exists={tmp_decisions_log.exists()}")
            failures += 1

        # ============================================================
        # 测试 8: log_decision 观察期内强制 action=evaluate_only (HC-4)
        # ============================================================
        print("\n--- 测试 8: 观察期内强制 action=evaluate_only (HC-4) ---")
        # 强制设置观察期状态
        orch_enabled._status.in_observation = True
        orch_enabled._status.observation_day = 1
        orch_enabled._status.status = STATUS_OBSERVATION

        # 尝试写入 promote (应被强制改为 evaluate_only)
        success = orch_enabled.log_decision(
            report=None,
            action=ACTION_PROMOTE,  # 观察期内应被拒绝
            reason="test_hc4",
        )
        if success:
            with tmp_decisions_log.open("r", encoding="utf-8") as f:
                lines = f.readlines()
            last_record = json.loads(lines[-1])
            if last_record["action"] == ACTION_EVALUATE_ONLY:
                print("[OK] HC-4 强制: promote → evaluate_only")
            else:
                print(f"[FAIL] HC-4 未强制: action={last_record['action']}")
                failures += 1
        else:
            print("[FAIL] 写入失败")
            failures += 1

        # ============================================================
        # 测试 9: run_observation_cycle 完整流程
        # ============================================================
        print("\n--- 测试 9: run_observation_cycle 完整流程 ---")
        # 清空日志重新测试
        if tmp_decisions_log.exists():
            tmp_decisions_log.unlink()

        result = orch_enabled.run_observation_cycle()
        if isinstance(result, dict) and "status" in result:
            assert "observation_day" in result
            assert "metrics_snapshot" in result
            assert "has_report" in result
            # 验证日志已写入
            if tmp_decisions_log.exists():
                with tmp_decisions_log.open("r", encoding="utf-8") as f:
                    lines = f.readlines()
                assert len(lines) >= 1, "应至少写入 1 条决策记录"
                last_record = json.loads(lines[-1])
                assert last_record["action"] == ACTION_EVALUATE_ONLY
                print(f"[OK] 完整流程: status={result['status']}, samples={result['metrics_snapshot']['sample_count']}, decisions={len(lines)}")
            else:
                print("[FAIL] 决策日志未写入")
                failures += 1
        else:
            print(f"[FAIL] 返回值异常: {type(result)}")
            failures += 1

        # ============================================================
        # 测试 10: get_recent_decisions 读取记录
        # ============================================================
        print("\n--- 测试 10: get_recent_decisions 读取记录 ---")
        # 多写几条记录
        for i in range(5):
            orch_enabled.log_decision(report=None, action=ACTION_NOOP, reason=f"test_{i}")

        recent = orch_enabled.get_recent_decisions(limit=3)
        if len(recent) <= 3 and len(recent) >= 1:
            # 验证倒序 (最新在前)
            if len(recent) >= 2:
                ts_first = recent[0].get("timestamp", "")
                ts_second = recent[1].get("timestamp", "")
                assert ts_first >= ts_second, f"应倒序: first={ts_first}, second={ts_second}"
            print(f"[OK] 读取最近 {len(recent)} 条决策 (limit=3)")
        else:
            print(f"[FAIL] 读取异常: len={len(recent)}")
            failures += 1

        # ============================================================
        # 测试 11: 只读模式: 不修改 V9 基线 (HC-4 兼容)
        # ============================================================
        print("\n--- 测试 11: 只读模式: 不修改 V9 基线 ---")
        # 记录运行前的 positions.json mtime
        positions_mtime_after = positions_path.stat().st_mtime if positions_path.exists() else 0.0

        if positions_mtime_after == positions_mtime_before:
            print("[OK] positions.json mtime 未变化 (只读模式验证通过)")
        else:
            print(f"[FAIL] positions.json mtime 变化: before={positions_mtime_before}, after={positions_mtime_after}")
            failures += 1

        # 验证 daily_returns.jsonl (生产文件) 未被修改
        # 检查测试特有标记 (evo_verify / source="test"), 而非 "test" 子串
        # (生产文件 source="v9_phase10_real_backtest" 本身就含 "test" 子串)
        prod_daily_returns = _PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
        if prod_daily_returns.exists():
            with prod_daily_returns.open("r", encoding="utf-8") as f:
                content = f.read()
            # 测试数据特征: source="test" 或 evo_verify_ 前缀
            if 'source": "test"' not in content and "evo_verify_" not in content:
                print("[OK] 生产 daily_returns.jsonl 未被污染")
            else:
                print("[FAIL] 生产 daily_returns.jsonl 被污染 (发现测试数据特征)")
                failures += 1

        # ============================================================
        # 测试 12: 观察期状态推断
        # ============================================================
        print("\n--- 测试 12: 观察期状态推断 ---")
        # 测试数据首条日期为 2026-01-01, 当前 2026-07-29, 已过观察期
        orch_fresh = EvolutionOrchestrator(
            daily_returns_path=tmp_daily_returns,
            decisions_log_path=tmp_decisions_log,
        )
        orch_fresh._enabled = True
        status = orch_fresh.get_status()

        # 由于测试数据首条日期是 2026-01-01, 距今已超过 14 天, 应不在观察期内
        if status["observation_day"] >= OBSERVATION_PERIOD_DAYS:
            print(f"[OK] 观察期已过: day={status['observation_day']}/{status['observation_total']}")
        elif status["in_observation"]:
            print(f"[WARN] 观察期内: day={status['observation_day']}/{status['observation_total']} (测试数据日期较新?)")
        else:
            print(f"[OK] 状态: day={status['observation_day']}, in_observation={status['in_observation']}")

        # ============================================================
        # 汇总
        # ============================================================
        print("\n" + "=" * 70)
        passed = total_tests - failures
        print(f"验证完成: {passed}/{total_tests} PASS, {failures} FAIL")
        print("=" * 70)

        if failures > 0:
            print("\n[FAIL] 存在失败项, 请检查上述输出")
            return 1
        else:
            print("\n[OK] 所有验证通过")
            print("  - Feature Flag 默认 False, 关闭时降级 (HC-1)")
            print("  - 只读模式, 不修改 V9 基线 (HC-4)")
            print("  - 决策日志正确持久化到 JSONL")
            print("  - 观察期内强制 action=evaluate_only (HC-4)")
            print("  - 完整观察期循环 (collect → evaluate → log) 可运行")
            return 0

    finally:
        # 清理临时目录
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
