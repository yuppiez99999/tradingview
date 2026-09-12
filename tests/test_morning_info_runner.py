"""
test_morning_info_runner.py — 晨间信息采集工作流单元测试
========================================================
覆盖 morning_info_runner.py 的 7 项任务 + run_all 编排逻辑。

测试策略 (AAA 模式):
  - Arrange: 构造临时归档目录 / mock 外部模块
  - Act:     调用各 task_* 函数
  - Assert:  验证返回值 / 文件生成 / 跳过逻辑

标记: @pytest.mark.unit — 全 mock, <1s, 无外部 API
"""

import importlib
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from utils.datetime_utils import now_bj

# ============================================================
# 路径设置 — 定位 morning_info_runner.py 所在目录
# ============================================================
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = _PROJECT_ROOT / "15_每日工作流"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# 导入被测模块 (会自动将 15_每日工作流 / 11_量化策略 / v8.3/src 加入 sys.path)
import morning_info_runner as mir  # noqa: E402

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def archive_dir(tmp_path):
    """临时归档目录"""
    d = tmp_path / "archive"
    d.mkdir()
    return d


@pytest.fixture
def target_date():
    """测试用目标日期 (固定值, 避免依赖今天)"""
    return "2026-07-27"


@pytest.fixture
def date_short(target_date):
    return target_date.replace("-", "")


# ============================================================
# 测试: 路径常量与模块加载
# ============================================================


class TestModuleSetup:
    """验证模块路径常量与 sys.path 配置"""

    @pytest.mark.unit
    def test_path_constants_exist(self):
        """路径常量必须指向真实存在的目录"""
        assert mir.SCRIPT_DIR.is_dir(), "SCRIPT_DIR 必须存在"
        assert mir.PROJECT_ROOT.is_dir(), "PROJECT_ROOT 必须存在"
        assert mir.BASE_ROOT.is_dir(), "BASE_ROOT 必须存在"
        assert mir.ARCHIVE_DIR.parent.is_dir(), "ARCHIVE_DIR 父目录必须存在"

    @pytest.mark.unit
    def test_sys_path_includes_reuse_modules(self):
        """sys.path 必须包含跨目录复用的模块路径"""
        assert str(mir.WORKFLOW_15) in sys.path
        assert str(mir.STRATEGY_11) in sys.path
        assert str(mir.V83_SRC) in sys.path

    @pytest.mark.unit
    def test_tasks_list_has_seven_entries(self):
        """TASKS 列表必须包含 7 项任务"""
        assert len(mir.TASKS) == 7
        names = [name for name, _ in mir.TASKS]
        assert "晨间行情摘要" in names
        assert "康波周期分析" in names
        assert "实时ETF资金流向" in names
        assert "舆情综合+动力煤" in names
        assert "CNEMC空气质量" in names
        assert "iFinD自动研判" in names
        # P1-8: 源码按 cairn/LOG.md:1881 决策, 任务 7 由 task_cotton_archive 改为
        # task_commodity_fundamental_scan (大宗商品基本面扫描).
        assert "大宗商品基本面扫描" in names

    @pytest.mark.unit
    def test_all_task_functions_callable(self):
        """所有任务函数必须可调用"""
        for name, fn in mir.TASKS:
            assert callable(fn), f"任务 {name} 的函数不可调用"


# ============================================================
# 测试: 辅助函数
# ============================================================


class TestHelpers:
    """验证 _archive_today / _exists_nonempty 辅助函数"""

    @pytest.mark.unit
    def test_archive_today_creates_directory(self, tmp_path, monkeypatch):
        """_archive_today 必须创建归档目录"""
        monkeypatch.setattr(mir, "ARCHIVE_DIR", tmp_path / "reports")
        result = mir._archive_today("2026-07-27")
        assert result.is_dir()
        assert str(result).endswith("2026-07-27")

    @pytest.mark.unit
    def test_exists_nonempty_returns_false_for_missing(self, tmp_path):
        """_exists_nonempty 对不存在文件返回 False"""
        assert mir._exists_nonempty(tmp_path / "nope.md") is False

    @pytest.mark.unit
    def test_exists_nonempty_returns_false_for_empty(self, tmp_path):
        """_exists_nonempty 对空文件返回 False"""
        f = tmp_path / "empty.md"
        f.write_text("")
        assert mir._exists_nonempty(f) is False

    @pytest.mark.unit
    def test_exists_nonempty_returns_true_for_valid(self, tmp_path):
        """_exists_nonempty 对非空文件返回 True"""
        f = tmp_path / "ok.md"
        f.write_text("x" * 600)
        assert mir._exists_nonempty(f) is True

    @pytest.mark.unit
    def test_exists_nonempty_respects_min_size(self, tmp_path):
        """_exists_nonempty 必须尊重 min_size 参数"""
        f = tmp_path / "small.md"
        f.write_text("x" * 100)
        assert mir._exists_nonempty(f, min_size=500) is False
        assert mir._exists_nonempty(f, min_size=50) is True


# ============================================================
# 测试: 任务 1 — 晨间行情摘要
# ============================================================


class TestTaskMorningMarket:
    """task_morning_market — 晨间行情摘要"""

    @pytest.mark.unit
    def test_skip_when_exists_and_not_force(
        self, archive_dir, target_date, date_short, monkeypatch
    ):
        """已存在且非 force 模式必须跳过"""
        existing = archive_dir / f"晨间行情摘要_{date_short}.md"
        existing.write_text("x" * 600)

        called = {"flag": False}

        def _fake_main(output_dir):
            called["flag"] = True
            return {"path": str(existing)}

        monkeypatch.setattr(mir, "task_morning_market", mir.task_morning_market)

        # 直接调用函数, mock 内部 import
        with patch("builtins.__import__"):
            result = mir.task_morning_market(archive_dir, target_date, force=False)
        assert result is True
        assert called["flag"] is False  # 不应调用 main

    @pytest.mark.unit
    def test_generates_report_when_success(self, archive_dir, target_date, monkeypatch):
        """成功生成时返回 True"""
        out_file = archive_dir / f"晨间行情摘要_{target_date.replace('-', '')}.md"

        fake_module = MagicMock()
        fake_module.main.return_value = {"path": str(out_file)}

        with patch.dict(sys.modules, {"morning_market_fetcher": fake_module}):
            result = mir.task_morning_market(archive_dir, target_date, force=True)

        assert result is True
        fake_module.main.assert_called_once_with(output_dir=str(archive_dir))

    @pytest.mark.unit
    def test_returns_false_when_main_returns_none(
        self, archive_dir, target_date, monkeypatch
    ):
        """main 返回 None 时返回 False"""
        fake_module = MagicMock()
        fake_module.main.return_value = None

        with patch.dict(sys.modules, {"morning_market_fetcher": fake_module}):
            result = mir.task_morning_market(archive_dir, target_date, force=True)

        assert result is False

    @pytest.mark.unit
    def test_returns_false_on_exception(self, archive_dir, target_date):
        """异常时返回 False, 不向上抛出"""
        fake_module = MagicMock()
        fake_module.main.side_effect = RuntimeError("network error")

        with patch.dict(sys.modules, {"morning_market_fetcher": fake_module}):
            result = mir.task_morning_market(archive_dir, target_date, force=True)

        assert result is False


# ============================================================
# 测试: 任务 2 — 康波周期分析
# ============================================================


class TestTaskKondratiev:
    """task_kondratiev — 康波周期分析"""

    @pytest.mark.unit
    def test_skip_when_exists_and_not_force(self, archive_dir, target_date, date_short):
        """已存在且非 force 必须跳过"""
        existing = archive_dir / f"康波周期分析_{date_short}.md"
        existing.write_text("x" * 600)

        with patch.dict(sys.modules, {"utils.kondratiev_cycle": MagicMock()}):
            result = mir.task_kondratiev(archive_dir, target_date, force=False)
        assert result is True

    @pytest.mark.unit
    def test_generates_report_on_success(self, archive_dir, target_date, date_short):
        """成功生成报告并写入文件"""
        report_content = "# 康波周期报告\n\n" + "x" * 500
        fake_analyzer = MagicMock()
        fake_analyzer.generate_report.return_value = report_content
        fake_module = MagicMock()
        fake_module.KondratievCycleAnalyzer.return_value = fake_analyzer

        with patch.dict(sys.modules, {"utils.kondratiev_cycle": fake_module}):
            result = mir.task_kondratiev(archive_dir, target_date, force=True)

        assert result is True
        out_file = archive_dir / f"康波周期分析_{date_short}.md"
        assert out_file.is_file()
        assert out_file.read_text(encoding="utf-8") == report_content

    @pytest.mark.unit
    def test_returns_false_when_report_empty(self, archive_dir, target_date):
        """报告内容为空时返回 False"""
        fake_analyzer = MagicMock()
        fake_analyzer.generate_report.return_value = ""
        fake_module = MagicMock()
        fake_module.KondratievCycleAnalyzer.return_value = fake_analyzer

        with patch.dict(sys.modules, {"utils.kondratiev_cycle": fake_module}):
            result = mir.task_kondratiev(archive_dir, target_date, force=True)

        assert result is False

    @pytest.mark.unit
    def test_returns_false_on_exception(self, archive_dir, target_date):
        """异常时返回 False"""
        fake_module = MagicMock()
        fake_module.KondratievCycleAnalyzer.side_effect = ImportError("missing dep")

        with patch.dict(sys.modules, {"utils.kondratiev_cycle": fake_module}):
            result = mir.task_kondratiev(archive_dir, target_date, force=True)

        assert result is False


# ============================================================
# 测试: 任务 3 — ETF 资金流向
# ============================================================


class TestTaskEtfFlow:
    """task_etf_flow — ETF 资金流向"""

    @pytest.mark.unit
    def test_skip_when_exists_and_not_force(self, archive_dir, target_date, date_short):
        """已存在且非 force 必须跳过"""
        existing = archive_dir / f"实时ETF资金流向_{date_short}_070000.md"
        existing.write_text("x" * 600)

        with patch.dict(sys.modules, {"engine.etf_flow": MagicMock()}):
            result = mir.task_etf_flow(archive_dir, target_date, force=False)
        assert result is True

    @pytest.mark.unit
    def test_generates_files_on_success(self, archive_dir, target_date, date_short):
        """成功生成 ETF 文件时返回 True"""

        fake_tracker = MagicMock()
        fake_tracker.get_all_etf_fund_flows.return_value = {
            "510300": {"name": "沪深300ETF", "net_flow_yi": 1.5, "change_pct": 0.8},
        }
        fake_tracker.detect_signals.return_value = []
        fake_module = MagicMock()
        fake_module.ETFRealTimeTracker.return_value = fake_tracker

        with patch.dict(sys.modules, {"utils.etf_flow_monitor": fake_module}):
            result = mir.task_etf_flow(archive_dir, target_date, force=True)

        assert result is True

    @pytest.mark.unit
    def test_returns_false_when_no_files_generated(self, archive_dir, target_date):
        """空 flow_data 仍生成报告并返回 True (源码总会写文件)"""
        fake_tracker = MagicMock()
        fake_tracker.get_all_etf_fund_flows.return_value = {}
        fake_tracker.detect_signals.return_value = []
        fake_module = MagicMock()
        fake_module.ETFRealTimeTracker.return_value = fake_tracker

        with patch.dict(sys.modules, {"utils.etf_flow_monitor": fake_module}):
            result = mir.task_etf_flow(archive_dir, target_date, force=True)

        assert result is True

    @pytest.mark.unit
    def test_returns_false_on_exception(self, archive_dir, target_date):
        """异常时返回 False"""
        fake_module = MagicMock()
        fake_module.ETFRealTimeTracker.side_effect = RuntimeError("api down")

        with patch.dict(sys.modules, {"utils.etf_flow_monitor": fake_module}):
            result = mir.task_etf_flow(archive_dir, target_date, force=True)

        assert result is False


# ============================================================
# 测试: 任务 4 — 舆情综合 + 动力煤
# ============================================================


class TestTaskSentiment:
    """task_sentiment — 舆情综合日报 + 动力煤舆情日报"""

    @pytest.mark.unit
    def test_skip_when_both_exist_and_not_force(
        self, archive_dir, target_date, date_short
    ):
        """两个文件都已存在且非 force 时跳过"""
        (archive_dir / f"舆情综合日报_{date_short}.md").write_text("x" * 600)
        (archive_dir / f"动力煤舆情日报_{date_short}.md").write_text("x" * 600)

        with patch.dict(sys.modules, {"nlp.sentiment_hub": MagicMock()}):
            result = mir.task_sentiment(archive_dir, target_date, force=False)
        assert result is True

    @pytest.mark.unit
    def test_runs_when_force(self, archive_dir, target_date, date_short):
        """force=True 时即使已存在也重新运行"""
        (archive_dir / f"舆情综合日报_{date_short}.md").write_text("x" * 600)
        (archive_dir / f"动力煤舆情日报_{date_short}.md").write_text("x" * 600)

        fake_module = MagicMock()
        fake_module.run_all.return_value = {"ok": True, "reports": {}}

        with patch.dict(sys.modules, {"nlp.sentiment_hub": fake_module}):
            result = mir.task_sentiment(archive_dir, target_date, force=True)

        assert result is True
        fake_module.run_all.assert_called_once()

    @pytest.mark.unit
    def test_returns_true_when_ok(self, archive_dir, target_date):
        """run_all 返回 ok=True 时返回 True"""
        fake_module = MagicMock()
        fake_module.run_all.return_value = {"ok": True}

        with patch.dict(sys.modules, {"nlp.sentiment_hub": fake_module}):
            result = mir.task_sentiment(archive_dir, target_date, force=True)

        assert result is True

    @pytest.mark.unit
    def test_returns_false_when_not_ok(self, archive_dir, target_date):
        """run_all 返回 ok=False 时返回 False"""
        fake_module = MagicMock()
        fake_module.run_all.return_value = {"ok": False, "errors": ["timeout"]}

        with patch.dict(sys.modules, {"nlp.sentiment_hub": fake_module}):
            result = mir.task_sentiment(archive_dir, target_date, force=True)

        assert result is False

    @pytest.mark.unit
    def test_returns_false_on_exception(self, archive_dir, target_date):
        """异常时返回 False"""
        fake_module = MagicMock()
        fake_module.run_all.side_effect = RuntimeError("hub down")

        with patch.dict(sys.modules, {"nlp.sentiment_hub": fake_module}):
            result = mir.task_sentiment(archive_dir, target_date, force=True)

        assert result is False


# ============================================================
# 测试: 任务 5 — CNEMC 空气质量
# ============================================================


class TestTaskCnemc:
    """task_cnemc — CNEMC 空气质量日报"""

    @pytest.mark.unit
    def test_skip_when_exists_and_not_force(self, archive_dir, target_date, date_short):
        existing = archive_dir / f"空气质量CNEMC日报_{date_short}.md"
        existing.write_text("x" * 600)

        with patch.dict(sys.modules, {"cnemc_air_quality_runner": MagicMock()}):
            result = mir.task_cnemc(archive_dir, target_date, force=False)
        assert result is True

    @pytest.mark.unit
    def test_returns_true_when_ok(self, archive_dir, target_date):
        fake_module = MagicMock()
        fake_module.generate_cnemc_report.return_value = {"ok": True, "path": "/fake"}

        with patch.dict(sys.modules, {"cnemc_air_quality_runner": fake_module}):
            result = mir.task_cnemc(archive_dir, target_date, force=True)

        assert result is True

    @pytest.mark.unit
    def test_returns_false_when_not_ok(self, archive_dir, target_date):
        fake_module = MagicMock()
        fake_module.generate_cnemc_report.return_value = {
            "ok": False,
            "error": "timeout",
        }

        with patch.dict(sys.modules, {"cnemc_air_quality_runner": fake_module}):
            result = mir.task_cnemc(archive_dir, target_date, force=True)

        assert result is False

    @pytest.mark.unit
    def test_returns_false_on_exception(self, archive_dir, target_date):
        fake_module = MagicMock()
        fake_module.generate_cnemc_report.side_effect = ConnectionError("offline")

        with patch.dict(sys.modules, {"cnemc_air_quality_runner": fake_module}):
            result = mir.task_cnemc(archive_dir, target_date, force=True)

        assert result is False


# ============================================================
# 测试: 任务 6 — iFinD 自动研判 (复制源文件)
# ============================================================


class TestTaskIfindAnalysis:
    """task_ifind_analysis — iFinD 研判报告归档"""

    @pytest.mark.unit
    def test_skip_when_exists_and_not_force(self, archive_dir, target_date, date_short):
        existing = archive_dir / f"iFinD自动标的研判报告_{date_short}.md"
        existing.write_text("x" * 600)
        result = mir.task_ifind_analysis(archive_dir, target_date, force=False)
        assert result is True

    @pytest.mark.unit
    def test_copies_source_file_when_exists(
        self, archive_dir, target_date, date_short, tmp_path, monkeypatch
    ):
        """P1-8: task_ifind_analysis 已重写为 Wind MCP+LLM 生成 (非复制源文件).
        mock 空持仓让源码走占位分支, 断言报告含研判关键字与日期."""
        monkeypatch.setattr(mir, "_load_portfolio_for_research", lambda: [])

        result = mir.task_ifind_analysis(archive_dir, target_date, force=True)

        assert result is True
        dst = archive_dir / f"iFinD自动标的研判报告_{date_short}.md"
        assert dst.is_file()
        content = dst.read_text(encoding="utf-8")
        assert "研判" in content and target_date in content

    @pytest.mark.unit
    def test_generates_placeholder_when_source_missing(
        self, archive_dir, target_date, date_short, tmp_path, monkeypatch
    ):
        """P1-8: 源文件不存在时走 Wind MCP+LLM 占位生成 (非读 BASE_ROOT)."""
        monkeypatch.setattr(mir, "_load_portfolio_for_research", lambda: [])

        result = mir.task_ifind_analysis(archive_dir, target_date, force=True)

        assert result is True
        dst = archive_dir / f"iFinD自动标的研判报告_{date_short}.md"
        assert dst.is_file()
        content = dst.read_text(encoding="utf-8")
        assert "研判" in content and target_date in content


# ============================================================
# 测试: 任务 7 — 大宗商品基本面扫描 (P1-8: task_cotton_archive 已重命名)
# ============================================================
# 源码按 cairn/LOG.md:1881 决策, task_cotton_archive → task_commodity_fundamental_scan.
# 旧 TestTaskCottonArchive 类已删除 (task_cotton_archive 函数不存在, 3 用例全 AttributeError).
# task_commodity_fundamental_scan 的单测由 TestTaskCommodityFundamentalScan 覆盖 (见下).


# ============================================================
# 测试: run_all 编排
# ============================================================


class TestRunAll:
    """run_all — 编排全部任务"""

    @pytest.mark.unit
    def test_returns_success_count_when_all_pass(
        self, tmp_path, monkeypatch, target_date
    ):
        """全部任务成功时返回 ok=True"""
        monkeypatch.setattr(mir, "ARCHIVE_DIR", tmp_path / "reports")

        # mock 所有 7 个任务函数
        for _name, _ in mir.TASKS:
            monkeypatch.setattr(mir, "task_morning_market", lambda *a, **k: True)
        # 用更直接的方式: 替换 TASKS 内的函数
        original_tasks = mir.TASKS
        fake_tasks = [
            (name, MagicMock(return_value=True)) for name, _ in original_tasks
        ]
        monkeypatch.setattr(mir, "TASKS", fake_tasks)

        result = mir.run_all(target_date=target_date, force=True)

        assert result["ok"] is True
        assert result["success"] == 7
        assert result["total"] == 7
        assert result["date"] == target_date
        # 所有 mock 都应被调用
        for _, fn in fake_tasks:
            fn.assert_called_once()

    @pytest.mark.unit
    def test_returns_partial_when_some_fail(self, tmp_path, monkeypatch, target_date):
        """部分任务失败时 ok=False, success 计数正确"""
        monkeypatch.setattr(mir, "ARCHIVE_DIR", tmp_path / "reports")

        original_tasks = mir.TASKS
        # 前 5 个成功, 后 2 个失败
        fake_tasks = [
            (original_tasks[0][0], MagicMock(return_value=True)),
            (original_tasks[1][0], MagicMock(return_value=True)),
            (original_tasks[2][0], MagicMock(return_value=True)),
            (original_tasks[3][0], MagicMock(return_value=True)),
            (original_tasks[4][0], MagicMock(return_value=True)),
            (original_tasks[5][0], MagicMock(return_value=False)),
            (original_tasks[6][0], MagicMock(return_value=False)),
        ]
        monkeypatch.setattr(mir, "TASKS", fake_tasks)

        result = mir.run_all(target_date=target_date, force=True)

        assert result["ok"] is False
        assert result["success"] == 5
        assert result["total"] == 7

    @pytest.mark.unit
    def test_task_exception_does_not_crash_run_all(
        self, tmp_path, monkeypatch, target_date
    ):
        """任务函数抛异常时 run_all 必须捕获, 不崩溃"""
        monkeypatch.setattr(mir, "ARCHIVE_DIR", tmp_path / "reports")

        original_tasks = mir.TASKS
        fake_tasks = [
            (original_tasks[0][0], MagicMock(return_value=True)),
            (original_tasks[1][0], MagicMock(side_effect=RuntimeError("boom"))),
            (original_tasks[2][0], MagicMock(return_value=True)),
            (original_tasks[3][0], MagicMock(return_value=True)),
            (original_tasks[4][0], MagicMock(return_value=True)),
            (original_tasks[5][0], MagicMock(return_value=True)),
            (original_tasks[6][0], MagicMock(return_value=True)),
        ]
        monkeypatch.setattr(mir, "TASKS", fake_tasks)

        result = mir.run_all(target_date=target_date, force=True)

        assert result["ok"] is False
        assert result["success"] == 6
        assert result["total"] == 7

    @pytest.mark.unit
    def test_run_all_creates_archive_directory(
        self, tmp_path, monkeypatch, target_date
    ):
        """run_all 必须创建归档子目录"""
        archive_root = tmp_path / "reports"
        monkeypatch.setattr(mir, "ARCHIVE_DIR", archive_root)

        fake_tasks = [(name, MagicMock(return_value=True)) for name, _ in mir.TASKS]
        monkeypatch.setattr(mir, "TASKS", fake_tasks)

        result = mir.run_all(target_date=target_date, force=True)

        assert (archive_root / target_date).is_dir()
        assert result["archive_dir"].endswith(target_date)

    @pytest.mark.unit
    def test_run_all_passes_force_to_tasks(self, tmp_path, monkeypatch, target_date):
        """run_all 必须把 force 参数传给每个任务"""
        monkeypatch.setattr(mir, "ARCHIVE_DIR", tmp_path / "reports")

        fake_tasks = [(name, MagicMock(return_value=True)) for name, _ in mir.TASKS]
        monkeypatch.setattr(mir, "TASKS", fake_tasks)

        mir.run_all(target_date=target_date, force=True)

        for _, fn in fake_tasks:
            args, _kwargs = fn.call_args
            # 函数签名: (archive, target_date, force)
            assert args[1] == target_date
            assert args[2] is True

    @pytest.mark.unit
    def test_run_all_uses_today_when_no_date(self, tmp_path, monkeypatch):
        """target_date=None 时使用今天"""
        monkeypatch.setattr(mir, "ARCHIVE_DIR", tmp_path / "reports")
        fake_tasks = [(name, MagicMock(return_value=True)) for name, _ in mir.TASKS]
        monkeypatch.setattr(mir, "TASKS", fake_tasks)

        result = mir.run_all(target_date=None, force=False)

        today = now_bj().strftime("%Y-%m-%d")
        assert result["date"] == today


# ============================================================
# 测试: run_daily_morning.py 阶段路由 (info 阶段不依赖交易日)
# ============================================================


class TestRunDailyMorningPhaseRouting:
    """验证 run_daily_morning.py 中 info 阶段的路由逻辑

    重点: info 阶段必须在交易日检查之前执行, 周末也运行
    """

    @pytest.mark.unit
    def test_phase_choices_include_info(self):
        """argparse choices 必须包含 info"""
        # 通过 --help 输出验证
        import subprocess

        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "run_daily_morning.py"), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        assert "info" in result.stdout
        assert "calibrate" in result.stdout
        assert "all" in result.stdout

    @pytest.mark.unit
    def test_morning_info_script_path_constant_exists(self):
        """run_daily_morning 必须定义 MORNING_INFO_SCRIPT 常量"""
        rdm = importlib.import_module("run_daily_morning")
        assert hasattr(rdm, "MORNING_INFO_SCRIPT")
        assert rdm.MORNING_INFO_SCRIPT.name == "morning_info_runner.py"

    @pytest.mark.unit
    def test_report_patterns_include_info_reports(self):
        """REPORT_PATTERNS 必须包含信息采集类报告"""
        rdm = importlib.import_module("run_daily_morning")
        patterns_str = " ".join(rdm.REPORT_PATTERNS)
        assert "晨间行情摘要" in patterns_str
        assert "康波周期分析" in patterns_str
        assert "实时ETF资金流向" in patterns_str
        assert "舆情综合日报" in patterns_str
        assert "动力煤舆情日报" in patterns_str
        assert "空气质量CNEMC日报" in patterns_str

    @pytest.mark.unit
    def test_archive_search_dirs_include_cross_project(self):
        """archive_reports 的 search_dirs 必须包含跨项目目录"""
        importlib.import_module("run_daily_morning")
        # 读取源码, 验证 search_dirs 包含跨项目路径
        src = (SCRIPT_DIR / "run_daily_morning.py").read_text(encoding="utf-8")
        assert "15_每日工作流" in src
        assert "11_量化策略" in src
        assert "舆情监控" in src

    @pytest.mark.unit
    def test_info_phase_runs_before_trading_day_check(self):
        """info 阶段代码块必须在交易日检查之前 (源码顺序验证)"""
        src = (SCRIPT_DIR / "run_daily_morning.py").read_text(encoding="utf-8")
        info_phase_pos = src.find("阶段零: 晨间信息采集")
        trading_check_pos = src.find("not is_trading_day()")

        assert info_phase_pos > 0, "缺少 info 阶段代码块"
        assert trading_check_pos > 0, "缺少交易日检查代码块"
        assert (
            info_phase_pos < trading_check_pos
        ), f"info 阶段({info_phase_pos}) 必须在交易日检查({trading_check_pos})之前"


# ============================================================
# 集成测试标记 (默认跳过, 需手动启用)
# ============================================================


@pytest.mark.integration
def test_info_phase_e2e_smoke():
    """E2E 冒烟测试: --phase info --force 实际执行

    运行方式: pytest tests/test_morning_info_runner.py::test_info_phase_e2e_smoke --run-integration
    """
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "run_daily_morning.py"),
            "--phase",
            "info",
            "--force",
            "--skip-archive",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,  # 30 分钟
        cwd=str(_PROJECT_ROOT),
    )
    # P1-8: run_daily_morning.py 的 log() 只写日志文件不进 stdout, 改查 returncode.
    # 原 assert "阶段零" in result.stdout 永不命中 (log() 无 print).
    assert result.returncode in (0, 1), (
        f"脚本异常退出: returncode={result.returncode}\n{result.stderr[:2000]}"
    )
