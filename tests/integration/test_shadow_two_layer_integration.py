"""集成测试: 模拟真实数据流验证 14 天看门狗 + 5 天集成器联动

测试目标:
    验证从"文件读取 → 门槛检查 → 触发决策 → 漂移检测 → 告警写入"的完整链路.
    不 mock 内部逻辑, 只 mock 最外层边界 (compute_prediction_drift / generate_snapshot).

共享 fixture (tests/integration/conftest.py):
    - temp_shadow_env: 隔离的临时环境 (patch 路径常量 + mock 外部调用)

辅助函数 (tests/shadow_helpers.py):
    - make_real_records / make_mixed_records / write_cleaned_jsonl / write_progress_json / read_jsonl

关联文档: cairn/shadow-data-quality-loop.md §4.3 门槛设计: 两层防护
关联脚本: scripts/observation_watchdog.py / scripts/integrate_cleaned_to_drift.py
"""
from __future__ import annotations

import json
import os
import sys

import pytest

# 项目根目录加入 sys.path
_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from shadow_helpers import (
    make_mixed_records,
    make_real_records,
    read_jsonl,
    write_cleaned_jsonl,
    write_progress_json,
)

# ============================================================
# 集成测试: 完整数据流联动
# ============================================================


class TestTwoLayerIntegration:
    """集成测试: 14 天看门狗 + 5 天集成器在真实数据流下的联动."""

    @pytest.mark.integration
    def test_full_flow_6_days_no_trigger(self, temp_shadow_env):
        """场景 1: 6 天未达标 → 看门狗拦截, 集成器不被调用.

        数据流: progress(6天) + cleaned(6条real) → 看门狗 GATE-A/B 都 FAIL
        预期: triggered=False, drift_alerts.jsonl 不存在, watchdog_log 有 1 条
        """
        env = temp_shadow_env
        write_progress_json(env["progress_file"], days_completed=6)
        write_cleaned_jsonl(env["cleaned_file"], make_real_records(6))

        from scripts.observation_watchdog import run_watchdog
        result = run_watchdog(required_days=14, dry_run=False, force_trigger=False)

        # 看门狗门槛判定
        assert result["gates"]["gate_a_passed"] is False
        assert result["gates"]["gate_b_passed"] is False
        assert result["gates"]["all_passed"] is False

        # 触发决策
        assert result["trigger_result"]["triggered"] is False

        # 集成器未被调用 → 无告警文件
        assert not env["drift_alerts"].exists()

        # 看门狗日志已持久化 (扁平结构, 不是嵌套的 gates)
        watchdog_entries = read_jsonl(env["watchdog_log"])
        assert len(watchdog_entries) == 1
        assert watchdog_entries[0]["all_passed"] is False

    @pytest.mark.integration
    def test_full_flow_14_days_triggers(self, temp_shadow_env):
        """场景 2: 14 天达标 → 看门狗放行 → 集成器执行 → 写入告警.

        数据流: progress(14天) + cleaned(14条real) → 看门狗 GATE-A/B 都 PASS
        预期: triggered=True, drift_alerts.jsonl 有 1 条 completed 告警,
              integration_log 有 1 条, watchdog_log 有 1 条
        """
        env = temp_shadow_env
        write_progress_json(env["progress_file"], days_completed=14)
        write_cleaned_jsonl(env["cleaned_file"], make_real_records(14))

        from scripts.observation_watchdog import run_watchdog
        result = run_watchdog(required_days=14, dry_run=False, force_trigger=False)

        # 看门狗门槛判定
        assert result["gates"]["all_passed"] is True

        # 触发决策
        assert result["trigger_result"]["triggered"] is True
        assert result["trigger_result"]["alert_status"] == "completed"

        # 集成器写入告警
        alerts = read_jsonl(env["drift_alerts"])
        assert len(alerts) == 1
        assert alerts[0]["status"] == "completed"
        assert alerts[0]["data_source"] == "shadow_daily_returns_cleaned"
        assert alerts[0]["n_baseline"] == 8  # 14 * 0.6 = 8.4 → 8
        assert alerts[0]["n_current"] == 6   # 14 - 8 = 6
        # 数据质量元信息
        assert alerts[0]["data_quality"]["real"] == 14
        assert alerts[0]["data_quality"]["excluded_non_real"] == 0
        # 文件溯源
        assert "cleaned_file_hash" in alerts[0]
        assert alerts[0]["cleaned_file_hash"] != "unknown"

        # 集成日志
        log_entries = read_jsonl(env["integration_log"])
        assert len(log_entries) == 1
        assert log_entries[0]["alert_status"] == "completed"

        # 看门狗日志 (扁平结构)
        watchdog_entries = read_jsonl(env["watchdog_log"])
        assert len(watchdog_entries) == 1
        assert watchdog_entries[0]["trigger_result"]["triggered"] is True

    @pytest.mark.integration
    def test_force_trigger_3_samples_skipped_by_integrator(self, temp_shadow_env):
        """场景 3: 强制触发 + 3 条样本 → 看门狗放行, 集成器兜底返回 skipped.

        数据流: progress(3天) + cleaned(3条real) + force_trigger=True
        预期: 看门狗门槛 FAIL 但 force 跳过 → 集成器执行 → 3 < 5 返回 skipped
        验证: drift_alerts.jsonl 有 1 条 skipped 告警 (集成器仍写入, 但标记 skipped)
        """
        env = temp_shadow_env
        write_progress_json(env["progress_file"], days_completed=3)
        write_cleaned_jsonl(env["cleaned_file"], make_real_records(3))

        from scripts.observation_watchdog import run_watchdog
        result = run_watchdog(required_days=14, dry_run=False, force_trigger=True)

        # 看门狗门槛确实 FAIL
        assert result["gates"]["all_passed"] is False

        # 但 force_trigger 跳过门槛, 触发了集成器
        assert result["trigger_result"]["triggered"] is True
        assert result["trigger_result"]["alert_status"] == "skipped"

        # 集成器写入 skipped 告警 (3 < 5, 不执行漂移检测)
        alerts = read_jsonl(env["drift_alerts"])
        assert len(alerts) == 1
        assert alerts[0]["status"] == "skipped"
        assert "insufficient_real_data" in alerts[0]["reason"]
        assert "3" in alerts[0]["reason"]

    @pytest.mark.integration
    def test_force_trigger_5_samples_executes(self, temp_shadow_env):
        """场景 4: 强制触发 + 5 条样本 → 集成器执行 (5 >= 5 刚好过兜底门槛).

        数据流: progress(5天) + cleaned(5条real) + force_trigger=True
        预期: 看门狗门槛 FAIL 但 force 跳过 → 集成器执行 → 5 >= 5 执行漂移检测
        验证: drift_alerts.jsonl 有 1 条 completed 告警, baseline=3, current=2
        """
        env = temp_shadow_env
        write_progress_json(env["progress_file"], days_completed=5)
        write_cleaned_jsonl(env["cleaned_file"], make_real_records(5))

        from scripts.observation_watchdog import run_watchdog
        result = run_watchdog(required_days=14, dry_run=False, force_trigger=True)

        # 看门狗门槛 FAIL
        assert result["gates"]["all_passed"] is False

        # force 跳过门槛, 集成器执行
        assert result["trigger_result"]["triggered"] is True
        assert result["trigger_result"]["alert_status"] == "completed"

        # 告警写入, 切分 3+2
        alerts = read_jsonl(env["drift_alerts"])
        assert len(alerts) == 1
        assert alerts[0]["status"] == "completed"
        assert alerts[0]["n_baseline"] == 3  # max(2, int(5*0.6))=3
        assert alerts[0]["n_current"] == 2   # 5 - 3 = 2

    @pytest.mark.integration
    def test_idempotent_alert_writing(self, temp_shadow_env):
        """场景 5: 连续运行两次 → 告警幂等替换, 不堆积.

        数据流: 14 天达标, 连续运行 run_watchdog 两次
        预期: drift_alerts.jsonl 仍只有 1 条当日告警 (第二次替换第一次)
        """
        env = temp_shadow_env
        write_progress_json(env["progress_file"], days_completed=14)
        write_cleaned_jsonl(env["cleaned_file"], make_real_records(14))

        from scripts.observation_watchdog import run_watchdog

        # 第一次运行
        run_watchdog(required_days=14, dry_run=False, force_trigger=False)
        alerts_after_first = read_jsonl(env["drift_alerts"])
        assert len(alerts_after_first) == 1

        # 第二次运行 (同日, 应替换不追加)
        run_watchdog(required_days=14, dry_run=False, force_trigger=False)
        alerts_after_second = read_jsonl(env["drift_alerts"])
        assert len(alerts_after_second) == 1  # 仍只有 1 条

        # 看门狗日志有 2 条 (每次运行都记录)
        watchdog_entries = read_jsonl(env["watchdog_log"])
        assert len(watchdog_entries) == 2

        # 集成日志也有 2 条 (每次触发都记录)
        log_entries = read_jsonl(env["integration_log"])
        assert len(log_entries) == 2

    @pytest.mark.integration
    def test_data_quality_filtering_gate_b_blocks(self, temp_shadow_env):
        """场景 6: 混合数据 (10 real + 4 backtest) → GATE-B 拦截.

        数据流: progress(14天) + cleaned(10 real + 4 backtest)
        预期: GATE-A PASS (days=14), GATE-B FAIL (real=10 < 14), 不触发
        验证: 回测回填数据不被计入 real, GATE-B 正确拦截
        """
        env = temp_shadow_env
        write_progress_json(env["progress_file"], days_completed=14)
        write_cleaned_jsonl(env["cleaned_file"], make_mixed_records(real_n=10, backtest_n=4))

        from scripts.observation_watchdog import run_watchdog
        result = run_watchdog(required_days=14, dry_run=False, force_trigger=False)

        # GATE-A 通过 (日历天数达标)
        assert result["gates"]["gate_a_passed"] is True

        # GATE-B 拦截 (真实数据只有 10 条, < 14)
        assert result["gates"]["gate_b_passed"] is False
        assert result["gates"]["real_count"] == 10

        # 不触发
        assert result["trigger_result"]["triggered"] is False
        assert not env["drift_alerts"].exists()

        # 看门狗日志记录了质量分布
        watchdog_entries = read_jsonl(env["watchdog_log"])
        assert watchdog_entries[0]["quality_stats"]["real"] == 10
        assert watchdog_entries[0]["quality_stats"]["backtest"] == 4

    @pytest.mark.integration
    def test_force_trigger_4_samples_boundary_skipped(self, temp_shadow_env):
        """场景 7: 强制触发 + 4 条样本 → 集成器兜底 skipped (4 < 5, 边界值).

        数据流: progress(4天) + cleaned(4条real) + force_trigger=True
        预期: 集成器返回 skipped (4 < 5, 差一条)
        """
        env = temp_shadow_env
        write_progress_json(env["progress_file"], days_completed=4)
        write_cleaned_jsonl(env["cleaned_file"], make_real_records(4))

        from scripts.observation_watchdog import run_watchdog
        result = run_watchdog(required_days=14, dry_run=False, force_trigger=True)

        assert result["trigger_result"]["triggered"] is True
        assert result["trigger_result"]["alert_status"] == "skipped"

        alerts = read_jsonl(env["drift_alerts"])
        assert len(alerts) == 1
        assert alerts[0]["status"] == "skipped"
        assert "4" in alerts[0]["reason"]

    @pytest.mark.integration
    def test_dry_run_no_files_written(self, temp_shadow_env):
        """场景 8: dry_run 模式 → 不写任何文件.

        数据流: 14 天达标 + dry_run=True
        预期: 不触发集成器, 不写 watchdog_log, 不写 drift_alerts
        """
        env = temp_shadow_env
        write_progress_json(env["progress_file"], days_completed=14)
        write_cleaned_jsonl(env["cleaned_file"], make_real_records(14))

        from scripts.observation_watchdog import run_watchdog
        result = run_watchdog(required_days=14, dry_run=True, force_trigger=False)

        # 门槛达标
        assert result["gates"]["all_passed"] is True

        # 但 dry_run 不触发
        assert result["trigger_result"]["triggered"] is False

        # 无文件写入
        assert not env["drift_alerts"].exists()
        assert not env["watchdog_log"].exists()
        assert not env["integration_log"].exists()

    @pytest.mark.integration
    def test_end_to_end_data_integrity(self, temp_shadow_env):
        """场景 9: 端到端数据完整性验证 — 告警条目包含完整溯源信息.

        数据流: 14 天达标 → 集成器执行 → 验证告警条目的所有字段
        预期: 告警含 data_quality / cleaned_file_hash / baseline_dates / current_dates
        """
        env = temp_shadow_env
        records = make_real_records(14)
        write_progress_json(env["progress_file"], days_completed=14)
        write_cleaned_jsonl(env["cleaned_file"], records)

        from scripts.observation_watchdog import run_watchdog
        run_watchdog(required_days=14, dry_run=False, force_trigger=False)

        alerts = read_jsonl(env["drift_alerts"])
        assert len(alerts) == 1
        alert = alerts[0]

        # 核心字段
        assert alert["status"] == "completed"
        assert alert["model_name"] == "v9_lgb"
        assert alert["model_version"] == "observation_period"
        assert alert["data_source"] == "shadow_daily_returns_cleaned"

        # 漂移指标
        assert "drift_score" in alert
        assert "psi" in alert
        assert "severity" in alert

        # 切分信息
        assert alert["n_baseline"] == 8
        assert alert["n_current"] == 6
        assert len(alert["baseline_dates"]) == 8
        assert len(alert["current_dates"]) == 6

        # 基线日期应早于当前日期 (时序正确)
        assert alert["baseline_dates"][-1] < alert["current_dates"][0]

        # 数据质量元信息
        assert alert["data_quality"]["real"] == 14
        assert alert["data_quality"]["excluded_non_real"] == 0

        # 文件溯源
        assert alert["cleaned_file_hash"] != "unknown"
        assert alert["integration_version"] == "w13c_day5_v1"

        # 集成日志的 hash 应与告警一致
        log_entries = read_jsonl(env["integration_log"])
        assert log_entries[0]["cleaned_file_hash"] == alert["cleaned_file_hash"]

    @pytest.mark.integration
    def test_progress_refreshed_after_trigger(self, temp_shadow_env):
        """场景 10: 触发后 observation_progress.json 被刷新.

        数据流: 预写 progress(days=14) → 触发 → refresh_observation_progress 覆盖
        预期: progress.json 被 generate_snapshot 的返回值覆盖,
              days_completed 与 cleaned.jsonl 的 real 记录数一致
        """
        env = temp_shadow_env
        write_progress_json(env["progress_file"], days_completed=14)
        write_cleaned_jsonl(env["cleaned_file"], make_real_records(14))

        from scripts.observation_watchdog import run_watchdog
        run_watchdog(required_days=14, dry_run=False, force_trigger=False)

        # progress.json 应被刷新 (由 refresh_observation_progress 写入)
        with open(env["progress_file"], encoding="utf-8") as f:
            refreshed = json.load(f)

        # mock generate_snapshot 从 cleaned.jsonl 读取 real_count=14
        assert refreshed["observation"]["days_completed"] == 14
        assert refreshed["observation"]["samples_collected"] == 14
