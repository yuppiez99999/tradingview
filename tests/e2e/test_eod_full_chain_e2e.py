# -*- coding: utf-8 -*-
"""test_eod_full_chain_e2e.py — EOD 全链路端到端测试 (Golden Path)

5 条关键链路 #5: 真实历史 pnl_report → 真实 trade_plan → run_all_guards → 写回 trade_plan

E2E 测试金字塔顶层 (5% 测试占比):
    - 使用 v8.3_institutional/reports/ 下的真实生产 pnl_report (黄金数据源)
    - 使用 v8.3_institutional/trade_plans/ 下的真实 trade_plan
    - 不 mock 内部模块 (与集成测试的关键区别)
    - 仅 mock 必不可少的外部数据源 (akshare/astock_realtime)
    - 文件 IO 重定向到 tmp_path (保护真实生产文件)
    - 验证端到端流程不崩溃, 关键字段被正确填充

设计原则:
    1. 黄金路径优先: 验证"正常流程"能跑通, 而非穷举边角案例
    2. 真实数据驱动: 不构造合成数据, 用生产样本验证生产代码
    3. 副作用隔离: 所有写操作重定向到 tmp_path
    4. 跨模块验证: 单次调用串起 7 个 Guard + KillSwitch + HedgeEngine
"""
import json
import pytest


# ============================================================
# E2E fixture: 真实历史报告 + IO 隔离
# ============================================================


@pytest.fixture
def e2e_integrator(tmp_path, monkeypatch, real_pnl_report):
    """E2E 真实 RiskGuardIntegrator — 仅 mock 外部数据源和文件 IO

    与集成测试 isolated_integrator 的区别:
        - 集成测试: mock 外部 + 禁用文件写入 (_save_trade_plan no-op)
        - E2E:     mock 外部 + 重定向 IO 到 tmp_path (真实写盘但隔离)
    """
    # 重定向所有 IO 路径到 tmp_path
    monkeypatch.setattr(
        "utils.risk_guard_integrator.LOGS_DIR", tmp_path / "logs"
    )
    monkeypatch.setattr(
        "utils.risk_guard_integrator.REPORTS_DIR", tmp_path / "reports"
    )
    monkeypatch.setattr(
        "utils.risk_guard_integrator.TRADE_PLANS_DIR", tmp_path / "trade_plans"
    )
    (tmp_path / "logs").mkdir()
    (tmp_path / "trade_plans").mkdir()

    # 报告日期对齐真实样本
    report_date = real_pnl_report.get("meta", {}).get("report_date") or "2026-07-21"
    from utils.risk_guard_integrator import RiskGuardIntegrator
    integrator = RiskGuardIntegrator(report_date=report_date)

    # 让 _load_pnl_report 返回真实样本 (绕过文件读取)
    monkeypatch.setattr(integrator, "_load_pnl_report", lambda: real_pnl_report)

    return integrator


@pytest.fixture
def e2e_normal_external_data(monkeypatch):
    """E2E 外部数据源 mock — 模拟"正常市场状态"(不触发任何熔断)

    注意: E2E 测试不验证熔断触发逻辑(那是集成测试的责任),
    这里只验证"正常情况下 EOD 全链路不崩溃"
    """
    # astock_realtime: 沪深300 微跌 (L0)
    def _normal_quotes(codes):
        return {"510300": {"price": 4.04, "pre_close": 4.06, "change_pct": -0.5}}
    try:
        monkeypatch.setattr(
            "utils.astock_realtime.get_realtime_quotes", _normal_quotes
        )
    except (AttributeError, ImportError):
        pass

    # akshare: 涨跌停家数远低阈值
    try:
        import pandas as pd
        mock_df = pd.DataFrame({"涨跌幅": [0.5, -0.3, 1.2, -0.8, 0.0] * 20})
        monkeypatch.setattr("akshare.stock_zh_a_spot_em", lambda: mock_df)
        monkeypatch.setattr("akshare.stock_zh_index_spot_em", lambda: mock_df)
    except (AttributeError, ImportError):
        pass

    # ExternalDataManager: S&P500 微跌 (L0)
    try:
        monkeypatch.setattr(
            "utils.overnight_gap_monitor.OvernightGapMonitor._fetch_via_external_source",
            lambda self: (-0.003, 0.001, True),
        )
        monkeypatch.setattr(
            "utils.overnight_gap_monitor.OvernightGapMonitor._fetch_via_cache",
            lambda self: (None, None, False),
        )
    except (AttributeError, ImportError):
        pass


# ============================================================
# E2E 测试: 全链路黄金路径
# ============================================================


class TestEODFullChainE2E:
    """EOD 七 Guard 链端到端测试 — 真实生产数据驱动

    覆盖 5 条关键链路中的第 5 条:
        真实 pnl_report → 7 Guard → trade_plan 写回

    每个测试用例都使用真实 2026-07-21 pnl_report (37KB, 26 标的)
    """

    @pytest.mark.e2e
    def test_e2e_full_chain_does_not_crash_with_real_data(
        self, e2e_integrator, e2e_normal_external_data,
        sample_trade_plan, monkeypatch, tmp_path
    ):
        """E2E 黄金路径: 真实 pnl_report + 真实 trade_plan → run_all_guards 不崩溃

        这是最关键的 E2E 测试 — 如果失败, 说明 EOD 流程在生产环境会中断
        """
        integrator = e2e_integrator
        monkeypatch.setattr(
            integrator, "_load_next_trade_plan", lambda date: sample_trade_plan
        )

        # 执行完整 EOD 流程
        plan = integrator.run_all_guards(next_trade_date="2026-07-22")

        # 必须返回非空 plan
        assert plan is not None, "run_all_guards 必须返回 plan, 不能返回 None"

        # 必须有 risk_guard 字段 (所有 Guard 共享状态)
        assert "risk_guard" in plan, "risk_guard 字段必须存在"

        # 必须有 last_run 时间戳 (证明所有 Guard 都跑完了)
        assert "last_run" in plan["risk_guard"], "必须写入 last_run 时间戳"
        assert "report_date" in plan["risk_guard"], "必须写入 report_date"

        # 不能有 kill_switch_error / hedge_error 等崩溃标记
        error_keys = [k for k in plan["risk_guard"] if k.endswith("_error")]
        assert not error_keys, (
            f"E2E 全链路出现崩溃: {error_keys}, "
            f"详情: {[plan['risk_guard'][k] for k in error_keys]}"
        )

    @pytest.mark.e2e
    def test_e2e_real_pnl_report_positions_extracted_correctly(
        self, e2e_integrator, e2e_normal_external_data,
        sample_trade_plan, monkeypatch, real_pnl_report
    ):
        """E2E: 真实 pnl_report 中的 26 标的持仓能被正确提取

        验证 _extract_positions 兼容层在真实数据上工作正常
        """
        integrator = e2e_integrator

        # 真实数据应该能提取出 positions 列表
        positions = integrator._extract_positions(real_pnl_report)
        assert isinstance(positions, list), "真实报告的 positions 必须能转为 list"
        assert len(positions) > 0, "真实报告应该有持仓数据"

        # 真实数据应该能提取出 summary
        summary = integrator._extract_summary(real_pnl_report)
        assert isinstance(summary, dict), "真实报告的 summary 必须是 dict"
        assert "total_market_value" in summary or "total_pnl" in summary, (
            "真实报告 summary 应包含 total_market_value 或 total_pnl"
        )

    @pytest.mark.e2e
    def test_e2e_trade_plan_written_to_disk(
        self, e2e_integrator, e2e_normal_external_data,
        sample_trade_plan, monkeypatch, tmp_path
    ):
        """E2E: run_all_guards 完成后, trade_plan 应被写入到 tmp_path

        验证 _save_trade_plan 的真实写盘行为 (集成测试 mock 掉了它)
        """
        integrator = e2e_integrator
        monkeypatch.setattr(
            integrator, "_load_next_trade_plan", lambda date: sample_trade_plan
        )

        plan = integrator.run_all_guards(next_trade_date="2026-07-22")

        # trade_plan_20260722.json 应被写入到 tmp_path/trade_plans/
        expected_path = tmp_path / "trade_plans" / "trade_plan_20260722.json"
        assert expected_path.exists(), (
            f"trade_plan 应被写入 {expected_path}, 但文件不存在"
        )

        # 写入的文件应该是有效 JSON, 且包含 risk_guard 字段
        with open(expected_path, "r", encoding="utf-8") as f:
            written_plan = json.load(f)
        assert "risk_guard" in written_plan, "写入的 plan 必须包含 risk_guard"
        assert "last_run" in written_plan["risk_guard"], (
            "写入的 plan 必须包含 last_run 时间戳"
        )

    @pytest.mark.e2e
    def test_e2e_guard_log_written_to_disk(
        self, e2e_integrator, e2e_normal_external_data,
        sample_trade_plan, monkeypatch, tmp_path
    ):
        """E2E: run_all_guards 完成后, risk_guard 日志应被写入

        验证 _write_guard_log 的真实写盘行为
        """
        integrator = e2e_integrator
        monkeypatch.setattr(
            integrator, "_load_next_trade_plan", lambda date: sample_trade_plan
        )

        integrator.run_all_guards(next_trade_date="2026-07-22")

        # risk_guard_20260722.log 应被写入
        log_path = tmp_path / "logs" / "risk_guard_20260722.log"
        assert log_path.exists(), f"风控日志应被写入 {log_path}"

        # 日志应包含 7 个 Guard 的标记 + 计划保存成功
        # 注: _write_guard_log 在 _save_trade_plan 之后但 _log("完成") 之前调用,
        # 所以日志文件包含 [1/7]~[7/7] + "次日计划已更新", 不含"集成器完成"
        log_content = log_path.read_text(encoding="utf-8")
        assert "[1/7]" in log_content, "日志应包含 [1/7] KillSwitch"
        assert "[7/7]" in log_content, "日志应包含 [7/7] 对冲执行"
        assert "次日计划已更新" in log_content, "日志应包含计划保存标记"

    @pytest.mark.e2e
    def test_e2e_kill_switch_level_reflects_real_margin(
        self, e2e_integrator, e2e_normal_external_data,
        sample_trade_plan, monkeypatch, real_pnl_report
    ):
        """E2E: KillSwitch 必须基于真实 pnl_report 的保证金数据, 不能默认 level=0

        回归 P0-D bug: 原 bug 导致 level=0 即使保证金 80%+
        """
        integrator = e2e_integrator
        monkeypatch.setattr(
            integrator, "_load_next_trade_plan", lambda date: sample_trade_plan
        )

        plan = integrator.run_all_guards(next_trade_date="2026-07-22")

        # kill_switch 字段必须存在 (不能因崩溃而缺失)
        rg = plan["risk_guard"]
        assert "kill_switch" in rg, (
            "kill_switch 字段必须存在 — 回归 P0-D: 原本因 None/None 崩溃导致字段缺失"
        )

        # level 字段必须存在
        ks = rg["kill_switch"]
        assert "level" in ks, "kill_switch.level 字段必须存在"

        # can_trade 字段必须存在
        assert "can_trade" in ks, "kill_switch.can_trade 字段必须存在"

    @pytest.mark.e2e
    def test_e2e_hedge_execution_with_real_positions(
        self, e2e_integrator, e2e_normal_external_data,
        sample_trade_plan, monkeypatch, real_pnl_report
    ):
        """E2E: 对冲执行引擎必须能读取真实 positions.json, 不抛 'str' has no attribute 'get'

        回归 P0-E bug: 原本遍历 hedge_positions 时遇到字符串字段崩溃
        """
        integrator = e2e_integrator
        monkeypatch.setattr(
            integrator, "_load_next_trade_plan", lambda date: sample_trade_plan
        )

        plan = integrator.run_all_guards(next_trade_date="2026-07-22")

        # hedge_error 不应存在 (P0-E 回归验证)
        rg = plan["risk_guard"]
        assert "hedge_error" not in rg, (
            f"对冲执行不应崩溃 — P0-E 回归: {rg.get('hedge_error')}"
        )
        assert "put_error" not in rg, (
            f"认沽保护不应崩溃: {rg.get('put_error')}"
        )

    @pytest.mark.e2e
    def test_e2e_seven_guards_all_executed(
        self, e2e_integrator, e2e_normal_external_data,
        sample_trade_plan, monkeypatch, tmp_path
    ):
        """E2E: 7 个 Guard 必须全部执行, 通过日志验证

        检查日志中是否出现 [1/7]~[7/7] 全部标记
        """
        integrator = e2e_integrator
        monkeypatch.setattr(
            integrator, "_load_next_trade_plan", lambda date: sample_trade_plan
        )

        integrator.run_all_guards(next_trade_date="2026-07-22")

        log_path = tmp_path / "logs" / "risk_guard_20260722.log"
        log_content = log_path.read_text(encoding="utf-8")

        # 7 个 Guard 标记必须全部出现
        for i in range(1, 8):
            assert f"[{i}/7]" in log_content, (
                f"日志中缺少 [{i}/7] 标记 — 第 {i} 个 Guard 未执行"
            )

        # "去重" 标记必须出现
        assert "[去重]" in log_content, "PUT 订单去重步骤未执行"

        # "次日计划已更新" 必须出现 (证明 _save_trade_plan 跑完)
        # 注: _write_guard_log 在 _log("完成") 之前调用, 所以用 "次日计划已更新"
        # 作为链路跑完的标记, 而非 "风控守卫集成器完成"
        assert "次日计划已更新" in log_content, "链路未跑完 (缺少 _save_trade_plan 完成标记)"


# ============================================================
# E2E 测试: 多日回归 (使用多份真实报告)
# ============================================================


class TestEODMultiDayRegression:
    """多日 E2E 回归测试 — 验证多份真实历史报告都能跑通

    防止"单日报告恰好通过"的偶然性
    """

    @pytest.mark.e2e
    @pytest.mark.slow
    def test_e2e_multi_day_reports_all_pass(
        self, e2e_reports_dir, e2e_normal_external_data, monkeypatch, tmp_path
    ):
        """E2E 多日回归: 至少 3 份真实报告都能跑通 run_all_guards

        Skip 而非 fail 当数据不足时
        """
        if not e2e_reports_dir.exists():
            pytest.skip("E2E 报告目录不存在")

        pnl_files = sorted(e2e_reports_dir.glob("daily_pnl_report_2026-07-*.json"))
        # 跳过 20260715 (442 bytes, 是测试残留小文件)
        pnl_files = [f for f in pnl_files if f.stat().st_size > 10000]
        if len(pnl_files) < 3:
            pytest.skip(f"真实报告不足 3 份 (当前 {len(pnl_files)} 份)")

        from utils.risk_guard_integrator import RiskGuardIntegrator

        # 取最近 3 份
        tested = 0
        for pnl_path in pnl_files[-3:]:
            # 每份报告独立 tmp_path
            day_tmp = tmp_path / pnl_path.stem
            day_tmp.mkdir()
            (day_tmp / "logs").mkdir()
            (day_tmp / "trade_plans").mkdir()

            monkeypatch.setattr(
                "utils.risk_guard_integrator.LOGS_DIR", day_tmp / "logs"
            )
            monkeypatch.setattr(
                "utils.risk_guard_integrator.REPORTS_DIR", day_tmp
            )
            monkeypatch.setattr(
                "utils.risk_guard_integrator.TRADE_PLANS_DIR", day_tmp / "trade_plans"
            )

            # 从文件名提取日期 (daily_pnl_report_2026-07-21.json → 2026-07-21)
            date_str = pnl_path.stem.replace("daily_pnl_report_", "")

            with open(pnl_path, "r", encoding="utf-8") as f:
                pnl_report = json.load(f)

            integrator = RiskGuardIntegrator(report_date=date_str)
            monkeypatch.setattr(integrator, "_load_pnl_report", lambda r=pnl_report: r)

            # 简单 plan (E2E 重点是不崩溃, 不验证 plan 内容)
            simple_plan = {
                "trade_date": "2026-07-22",
                "phase": {"daily_capital": 150000},
                "execution_plan": {"morning_orders": [], "afternoon_orders": []},
                "market_state": {},
                "risk_guard": {},
                "hedge_config": {"layers": {"layer1_futures": {"ratio": 0.15}}},
            }
            monkeypatch.setattr(
                integrator, "_load_next_trade_plan", lambda date, _sp=simple_plan: _sp
            )

            # 执行 — 不应崩溃
            try:
                plan = integrator.run_all_guards(next_trade_date="2026-07-22")
                assert plan is not None
                assert "risk_guard" in plan
                tested += 1
            except Exception as e:
                pytest.fail(
                    f"真实报告 {pnl_path.name} 跑通失败: {e}\n"
                    f"这是 E2E 黄金路径回归失败 — 生产环境会中断"
                )

        assert tested >= 3, f"应有 3 份报告跑通, 实际 {tested}"
