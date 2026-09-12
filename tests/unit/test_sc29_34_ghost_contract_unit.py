"""SC-29~34 回归: 幽灵符号 / 幽灵 API / 契约错配 (2026-09-12)

背景 (审查报告 §05 覆盖缺口 4「并行会话 WIP 复扫」顺势深扫交易决策链):
    连续第 N 次命中同一把尺子 —— **"主路径判据对、失败路径/契约路径继续静默"**。
    本批 6 项全部落在**交易决策主链**上, 且全部可实跑复现:

    - SC-29 (P0) `build_plan_executor.get_active_phase` 把计划文件 metadata 的
      `build_phases` (整数=阶段**数量**) 当作 `[{start,duration}]` 列表迭代 ->
      `TypeError: 'int' object is not iterable` -> `get_active_phase` /
      `generate_daily_orders` / `get_build_status` **三处整条崩溃**,
      盘前建仓指令生成链彻底不可用。真实排期事实源 = `phase_summary[].start/duration_days`。
    - SC-30 (P1) `daily_build_and_hedge.get_active_phase` 只认 `execution_plan.phase1..4`,
      而 `_load_plan` 的回退候选 500万建仓计划**没有该段** -> 恒返回
      `(None, "completed")` -> `run()` 见 phase 为空 `return {"status":"no_active_phase"}`
      -> CLI `sys.exit(1)`: **每日建仓+对冲主链整条不可用, 且把"无排期"谎报为"已完成"**。
    - SC-31 (P1) `wind_get_index_data(windcode, days=70)` 为**幽灵 API**
      (真实名 `wind_get_index_kline(windcode, begin_date, end_date)`, 返回 list[dict]);
      且异常元组列了 8 类却**不含 ImportError** -> ImportError 直接穿透
      `_fetch_index_returns` / `_get_market_ma60` / `monitor()` ->
      `assess_market_state` 崩溃 + Gamma 尾部对冲触发器整链失效。
      同源幽灵 `wind_get_etf_quote` 出现在 `utils/theta_engine.py` (备兑看涨现价链)。
    - SC-32 (P2) `from utils.etf_flow_monitor import ETFMonitor` 类不存在
      (真实导出 `ETFRealTimeTracker` / `get_etf_flow_summary`), 被
      `except (ImportError, AttributeError)` 静默吞掉 -> `etf_data` 恒 `{}` ->
      ETF 资金流信号**从未进入建仓决策**。
    - SC-33 (P2) `from lgb_enhanced_trainer import POSITION_SYMBOLS, load_model_meta`
      —— `load_model_meta` 真实归属 `lgb_trainer.persistence`; 幽灵符号被
      `except Exception` + `logger.debug` 静默吞掉 -> "QLib 预热失败 -> 历史模型 CV IC
      兜底预热"这条**唯一兜底路径从未生效**。
    - SC-34 (P2) `cli/modes/risk_monitor.py` **模块顶**导入
      `utils.cli_helpers.load_historical_returns_from_cache` —— 该函数**只有调用方、
      没有实现** -> `--risk-monitor` 模块 ImportError 整块加载失败 ->
      组合相关性监控能力实际缺失。

本套件锁死 6 条不可回归契约:
    1. 阶段排期必须从 phase_summary 真实解析, 且非法/缺失时**告警降级**而非 TypeError
    2. 建仓主链在计划文件缺 execution_plan 时必须能推进 (回退 phase_summary)
    3. 数据源取数函数必须真实存在, 且失败路径必须把 ImportError 纳入降级
    4. ETF 资金流必须走真实入口, 取数失败必须留痕 (不静默为空)
    5. 跨模块 import 的符号必须真实存在 (幽灵符号零容忍)
    6. `load_historical_returns_from_cache` 必须可导入且返回 dict 契约
"""

from __future__ import annotations

import ast
import sys
from datetime import date
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from build_plan_executor import BuildPlanExecutor  # noqa: E402

PLAN_FILE = PROJECT_ROOT / "500万建仓计划_20260706.json"


# ============================================================
# SC-29: 阶段排期契约 (metadata.build_phases 是 int, 不是 list)
# ============================================================


class TestSc29PhaseScheduleContract:
    """SC-29: 盘前建仓指令生成必须能在真实计划文件上跑通."""

    @pytest.fixture(scope="class")
    @classmethod
    def executor(cls) -> BuildPlanExecutor:
        return BuildPlanExecutor()

    def test_real_plan_build_phases_is_int_not_list(self):
        """固化事实: 计划文件的 metadata.build_phases 是阶段**数量** (int 4)."""
        import json

        data = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
        assert isinstance(data["metadata"]["build_phases"], int)
        # 真实排期在 phase_summary[]
        assert isinstance(data["phase_summary"], list)
        assert all("start" in p and "duration_days" in p for p in data["phase_summary"])

    def test_get_active_phase_does_not_crash(self, executor: BuildPlanExecutor):
        """SC-29 核心: 旧码在此抛 TypeError: 'int' object is not iterable."""
        phase, idx, status = executor.get_active_phase(date(2026, 9, 13))
        assert status == "active"
        assert phase is not None
        assert idx == 3

    @pytest.mark.parametrize(
        ("target", "expect_status", "expect_idx"),
        [
            (date(2026, 6, 1), "not_started", 0),
            (date(2026, 7, 10), "active", 0),
            (date(2026, 7, 25), "active", 1),
            (date(2026, 8, 20), "active", 2),
            (date(2026, 9, 13), "active", 3),
            (date(2026, 10, 15), "completed", -1),
        ],
    )
    def test_phase_boundaries(
        self, executor: BuildPlanExecutor, target: date, expect_status: str, expect_idx: int
    ):
        _, idx, status = executor.get_active_phase(target)
        assert status == expect_status
        assert idx == expect_idx

    def test_resolve_phase_schedule_from_phase_summary(self, executor: BuildPlanExecutor):
        """排期必须从 phase_summary 解析, 且与文件事实一致."""
        schedule = executor._resolve_phase_schedule(executor.plan_data["phase_summary"])
        assert [s["phase"] for s in schedule] == [1, 2, 3, 4]
        assert schedule[0]["start"] == date(2026, 7, 6)
        assert schedule[0]["duration"] == 10

    def test_invalid_schedule_falls_back_with_warning(
        self, executor: BuildPlanExecutor, caplog: pytest.LogCaptureFixture
    ):
        """SC-29: 非法排期必须告警降级为兜底表, 绝不再 TypeError."""
        import logging

        bad = [{"phase": 1, "start": "not-a-date", "duration_days": 10}]
        with caplog.at_level(logging.WARNING):
            schedule = executor._resolve_phase_schedule(bad)
        assert len(schedule) == 4  # 兜底表
        assert any("非法" in r.message for r in caplog.records)

    def test_non_dict_schedule_entries_fall_back(
        self, executor: BuildPlanExecutor, caplog: pytest.LogCaptureFixture
    ):
        """SC-29: 顶层非 dict (如旧码形态的 int) 必须降级而非崩溃."""
        import logging

        with caplog.at_level(logging.WARNING):
            schedule = executor._resolve_phase_schedule([4])  # type: ignore[list-item]
        assert len(schedule) == 4
        assert any("非对象" in r.message for r in caplog.records)

    def test_negative_duration_falls_back(
        self, executor: BuildPlanExecutor, caplog: pytest.LogCaptureFixture
    ):
        """SC-29: duration<=0 视为非法."""
        import logging

        bad = [{"phase": 1, "start": "2026-07-06", "duration_days": 0}]
        with caplog.at_level(logging.WARNING):
            schedule = executor._resolve_phase_schedule(bad)
        assert len(schedule) == 4
        assert any("非正" in r.message for r in caplog.records)

    def test_empty_phase_summary_uses_fallback(
        self, executor: BuildPlanExecutor, caplog: pytest.LogCaptureFixture
    ):
        """SC-29: 无相位时用兜底表 (仅此时才允许硬编码)."""
        import logging

        with caplog.at_level(logging.WARNING):
            schedule = executor._resolve_phase_schedule([])
        assert len(schedule) == 4
        assert any("无阶段排期" in r.message for r in caplog.records)

    def test_build_status_does_not_crash(self, executor: BuildPlanExecutor):
        """SC-29: get_build_status 也必须不再崩溃."""
        info = executor.get_build_status()
        assert info["build_phases"] == 4
        # progress 是百分数 (0~100), 非比例
        assert 0.0 <= info["progress"] <= 100.0
        assert info["status"] == "active"

    def test_generate_daily_orders_produces_orders(self, executor: BuildPlanExecutor):
        """SC-29 端到端: 旧码在 generate_daily_orders 内崩溃."""
        sheet = executor.generate_daily_orders(date(2026, 9, 13))
        total = len(sheet.morning_orders) + len(sheet.afternoon_orders)
        assert total > 0
        assert sheet.day_capital > 0
        assert sheet.phase_number == 4


# ============================================================
# SC-30: daily_build_and_hedge 排期回退 (不得冒充 completed)
# ============================================================


class TestSc30ActivePhaseNoFalseCompleted:
    """SC-30: 计划文件缺 execution_plan 时, 必须回退 phase_summary 而非谎报 completed."""

    def _system(self):
        from utils.execution.daily_build_and_hedge import DailyBuildHedgeSystem

        return DailyBuildHedgeSystem(
            target_date=date(2026, 9, 13), dry_run=True
        )

    def test_plan_file_has_no_execution_plan(self):
        """固化事实: 500万建仓计划没有 execution_plan 段."""
        import json

        data = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
        assert "execution_plan" not in data

    def test_falls_back_to_phase_summary(self):
        """SC-30 核心: 旧码返回 (None, "completed")."""
        phase, key = self._system().get_active_phase()
        assert phase is not None
        assert key == "phase4"
        assert phase.get("phase") == 4

    def test_no_schedule_is_explicit_not_completed(self):
        """SC-30: 完全没有排期事实源时, 状态必须是 no_schedule, 不得冒充 completed."""
        sys_ = self._system()
        sys_.plan_data = {"stock_etf_account": {"positions": []}}
        phase, key = sys_.get_active_phase()
        assert phase is None
        assert key == "no_schedule"
        assert key != "completed"

    def test_phase_from_summary_ignores_malformed(self):
        """SC-30: 畸形条目跳过并告警, 不影响合法条目."""
        sys_ = self._system()
        sys_.plan_data = {
            "phase_summary": [
                {"phase": 1, "start": "bad", "duration_days": 10},
                {"phase": 4, "start": "2026-09-01", "duration_days": 20},
            ]
        }
        phase, key = sys_.get_active_phase()
        assert phase is not None
        assert key == "phase4"


# ============================================================
# SC-31: 幽灵 API (wind_get_index_data / wind_get_etf_quote)
# ============================================================


class TestSc31GhostWindApi:
    """SC-31: 数据源函数必须真实存在; 失败路径必须降级而非穿透."""

    def test_ghost_api_absent_from_real_module(self):
        """固化事实: wind_mcp_fetcher 不含 wind_get_index_data / wind_get_etf_quote."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "wind_mcp_fetcher", PROJECT_ROOT / "tools" / "wind_mcp_fetcher.py"
        )
        assert spec is not None and spec.loader is not None
        wmf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wmf)

        assert not hasattr(wmf, "wind_get_index_data")
        assert not hasattr(wmf, "wind_get_etf_quote")
        # 真实入口
        assert hasattr(wmf, "wind_get_index_kline")
        assert hasattr(wmf, "fetch_realtime_price")

    @pytest.mark.parametrize(
        "path",
        [
            "utils/execution/daily_build_and_hedge.py",
            "utils/gamma_engine.py",
            "utils/theta_engine.py",
        ],
    )
    def test_no_ghost_api_reference_in_source(self, path: str):
        """SC-31: 生产源码不得再引用幽灵 API (注释除外, 故按 AST 判 import 名)."""
        tree = ast.parse((PROJECT_ROOT / path).read_text(encoding="utf-8"))
        ghost_names = {"wind_get_index_data", "wind_get_etf_quote"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "wind_mcp_fetcher":
                for alias in node.names:
                    assert alias.name not in ghost_names, (
                        f"{path}:{node.lineno} 仍引用幽灵 API {alias.name}"
                    )

    def test_index_returns_degrades_on_import_error(self, monkeypatch):
        """SC-31 核心: ImportError 必须被降级为 None, 不得穿透."""
        from utils.execution import daily_build_and_hedge as dbh

        def _boom(*_a, **_kw):
            raise ImportError("simulated ghost api")

        monkeypatch.setattr(
            dbh.DailyBuildHedgeSystem, "_fetch_index_kline_wind", staticmethod(_boom)
        )
        system = dbh.DailyBuildHedgeSystem(target_date=date(2026, 9, 13), dry_run=True)
        assert system._fetch_index_returns("000300.SH") is None

    def test_gamma_ma60_degrades_on_import_error(self, monkeypatch):
        """SC-31: Gamma 触发器在数据源 ImportError 时必须返回 None 而非崩溃."""
        from utils import gamma_engine as ge

        def _boom(*_a, **_kw):
            raise ImportError("simulated ghost api")

        monkeypatch.setattr(ge.GammaEngine, "_fetch_index_kline_wind", staticmethod(_boom))
        engine = ge.GammaEngine.__new__(ge.GammaEngine)
        assert ge.GammaEngine._get_market_ma60(engine) is None


# ============================================================
# SC-32: ETFMonitor 幽灵类 -> ETF 资金流从未进决策
# ============================================================


class TestSc32EtfFlowRealEntry:
    """SC-32: ETF 资金流必须走真实入口, 且取数失败留痕."""

    def test_ghost_class_absent(self):
        """固化事实: etf_flow_monitor 不含 ETFMonitor."""
        import utils.etf_flow_monitor as efm

        assert not hasattr(efm, "ETFMonitor")
        assert hasattr(efm, "get_etf_flow_summary")
        assert hasattr(efm, "ETFRealTimeTracker")

    def test_no_ghost_etf_monitor_import_in_source(self):
        """SC-32: 生产源码不得再 import 该幽灵类."""
        tree = ast.parse(
            (PROJECT_ROOT / "utils/execution/daily_build_and_hedge.py").read_text(
                encoding="utf-8"
            )
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "utils.etf_flow_monitor":
                for alias in node.names:
                    assert alias.name != "ETFMonitor", f"L{node.lineno} 仍引用幽灵类"

    def test_import_failure_is_logged_not_silent(self, monkeypatch, caplog):
        """SC-32 核心: 取数失败必须 WARNING 留痕, 且不阻断主链."""
        import logging

        import utils.etf_flow_monitor as efm
        from utils.execution import daily_build_and_hedge as dbh

        def _boom():
            raise RuntimeError("simulated flow fetch failure")

        monkeypatch.setattr(efm, "get_etf_flow_summary", _boom)
        system = dbh.DailyBuildHedgeSystem(target_date=date(2026, 9, 13), dry_run=True)
        monkeypatch.setattr(system, "_fetch_index_returns", lambda *a, **k: None)
        monkeypatch.setattr(system, "_fetch_vix", lambda *a, **k: 18.5, raising=False)
        with caplog.at_level(logging.WARNING):
            state = system.assess_market_state()
        assert isinstance(state, dict)
        assert any("ETF 资金流取数失败" in r.message for r in caplog.records)


# ============================================================
# SC-33: lgb_enhanced_trainer 幽灵符号
# ============================================================


class TestSc33LgbGhostSymbol:
    """SC-33: 跨模块 import 的符号必须真实存在."""

    def test_load_model_meta_real_home(self):
        """固化事实: load_model_meta 归属 lgb_trainer.persistence, 不在 lgb_enhanced_trainer."""
        import lgb_enhanced_trainer as let
        from lgb_trainer.persistence import load_model_meta

        assert callable(load_model_meta)
        assert not hasattr(let, "load_model_meta")

    def test_position_symbols_reexport_still_works(self):
        """POSITION_SYMBOLS 由 lgb_enhanced_trainer 再导出, 该用法须保留可用."""
        from lgb_enhanced_trainer import POSITION_SYMBOLS

        assert isinstance(POSITION_SYMBOLS, list)

    def test_no_ghost_symbol_import_in_system_integration(self):
        """SC-33: system_integration 不得再从 lgb_enhanced_trainer 取 load_model_meta."""
        tree = ast.parse(
            (PROJECT_ROOT / "system_integration.py").read_text(encoding="utf-8")
        )
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "lgb_enhanced_trainer"
            ):
                names = {a.name for a in node.names}
                assert "load_model_meta" not in names, f"L{node.lineno} 仍引用幽灵符号"

    def test_fallback_failure_is_warning_level(self):
        """SC-33: 兜底路径失败必须 WARNING 级 (原 debug 级长期隐形)."""
        src = (PROJECT_ROOT / "system_integration.py").read_text(encoding="utf-8")
        assert "历史模型 IC 预热失败" in src
        assert 'logger.warning(f"历史模型 IC 预热失败' in src


# ============================================================
# SC-34: load_historical_returns_from_cache 有调用方无实现
# ============================================================


class TestSc34ReturnsFromCacheImplemented:
    """SC-34: 被 CLI 模块顶层导入的函数必须有真实实现."""

    def test_function_importable(self):
        """SC-34 核心: 旧码在此 ImportError -> --risk-monitor 整块加载失败."""
        from utils.cli_helpers import load_historical_returns_from_cache

        assert callable(load_historical_returns_from_cache)

    def test_empty_codes_returns_empty_dict(self):
        """契约: 空输入返回 {}, 不抛异常."""
        from utils.cli_helpers import load_historical_returns_from_cache

        assert load_historical_returns_from_cache([]) == {}
        assert load_historical_returns_from_cache(None) == {}

    def test_returns_dict_of_lists_on_fetch_failure(self, monkeypatch):
        """契约: 取数失败返回 dict (可判空), 不抛异常."""
        import utils.data_provider as dp
        from utils.cli_helpers import load_historical_returns_from_cache

        def _boom(*_a, **_kw):
            raise RuntimeError("simulated fetch failure")

        monkeypatch.setattr(dp, "get_historical_data", _boom)
        result = load_historical_returns_from_cache(["588000"], lookback_days=60)
        assert isinstance(result, dict)
        assert result == {}

    def test_warns_when_coverage_incomplete(self, monkeypatch, caplog):
        """SC-34: 覆盖不全须留痕 (不静默返回部分结果)."""
        import logging

        import utils.data_provider as dp
        from utils.cli_helpers import load_historical_returns_from_cache

        monkeypatch.setattr(dp, "get_historical_data", lambda *a, **k: None)
        with caplog.at_level(logging.WARNING):
            load_historical_returns_from_cache(["588000"])
        assert any("历史收益率覆盖不全" in r.message for r in caplog.records)

    def test_risk_monitor_module_imports_without_local_helper(self):
        """SC-34: risk_monitor 的本地导入名必须与 cli_helpers 实现一致."""
        from utils import cli_helpers

        src = (
            PROJECT_ROOT / "cli" / "modes" / "risk_monitor.py"
        ).read_text(encoding="utf-8")
        assert "load_historical_returns_from_cache" in src
        assert hasattr(cli_helpers, "load_historical_returns_from_cache")
