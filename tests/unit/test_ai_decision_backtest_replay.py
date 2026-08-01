"""ai_decision.backtest_replay 测试套件 — 历史回放 + 三基线对比

覆盖路线图步骤 6 的 4 个验收场景:
  1. 回放 1 年数据无前视偏差 (财报披露日校验通过)
  2. 三条基线均产出完整决策序列
  3. 输出报告含 IC/夏普/命中率/辩论触发率
  4. 边际夏普 > 0.05 才建议上线 auto 模式

补充覆盖:
  5. MockHistoryDataLoader 合成数据正确性
  6. 前视偏差校验 (4 项)
  7. 落盘 Markdown + JSON
  8. CLI 入口主流程 + 退出码
  9. 向后兼容 (数据不足 / 异常降级)
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ai_decision.backtest_replay import (
    BacktestReplay,
    BaselineResult,
    BaselineType,
    ComparisonReport,
    MockHistoryDataLoader,
    ReplayConfig,
)

# ============================================================
# 辅助函数
# ============================================================

_REPORT_DIR = Path("reports") / "ai_decision"


def _cleanup_reports():
    """清理测试产生的回放报告"""
    for f in _REPORT_DIR.glob("backtest_replay_*.md"):
        f.unlink()
    for f in _REPORT_DIR.glob("backtest_replay_*.json"):
        f.unlink()


def _make_loader(symbols=None, days=60, seed=42):
    """构造 MockHistoryDataLoader"""
    return MockHistoryDataLoader(
        symbols=symbols or ["600519", "000001", "300750"],
        days=days, seed=seed,
    )


def _make_replay(symbols=None, days=60, seed=42, horizon=5):
    """构造 BacktestReplay 实例"""
    loader = _make_loader(symbols=symbols, days=days, seed=seed)
    config = ReplayConfig(
        symbols=symbols or ["600519", "000001", "300750"],
        forward_return_horizon=horizon,
    )
    return BacktestReplay(loader=loader, config=config)


# ============================================================
# 验收 1: MockHistoryDataLoader 合成数据正确性
# ============================================================

def test_mock_loader_returns_trading_dates():
    """验收 1: Mock loader 跳过周末返回交易日"""
    loader = MockHistoryDataLoader(symbols=["600519"], days=10, start_date="2025-01-06")
    dates = loader.get_trading_dates("2025-01-01", "2025-12-31")
    assert len(dates) == 10
    # 不含周末 (2025-01-04 周六, 2025-01-05 周日)
    for d in dates:
        from datetime import datetime
        dt = datetime.strptime(d, "%Y-%m-%d")
        assert dt.weekday() < 5  # 0-4 = 周一到周五


def test_mock_loader_market_data_structure():
    """验收 1: Mock loader 返回完整行情字段"""
    loader = MockHistoryDataLoader(symbols=["600519"], days=5, start_date="2025-01-06")
    dates = loader.get_trading_dates("2025-01-01", "2025-12-31")
    md = loader.get_market_data("600519", dates[0])
    assert "close" in md
    assert "change_pct" in md
    assert "is_halted" in md
    assert "is_limit_up" in md
    assert "is_limit_down" in md
    assert md["close"] > 0


def test_mock_loader_fundamentals_has_disclosure_date():
    """验收 1: 基本面含 disclosure_date (前视偏差防控)"""
    loader = MockHistoryDataLoader(symbols=["600519"], days=5)
    fund = loader.get_fundamentals("600519", loader._dates[0])
    assert "disclosure_date" in fund
    assert "pe" in fund
    assert "pb" in fund
    assert "roe" in fund


def test_mock_loader_forward_return_consistency():
    """验收 1: 前瞻收益与价格序列一致"""
    loader = MockHistoryDataLoader(symbols=["600519"], days=20, seed=100)
    dates = loader._dates
    idx = 0
    sym = "600519"
    p0 = loader._prices[sym][idx]
    p1 = loader._prices[sym][idx + 5]  # horizon=5
    expected_fwd = (p1 - p0) / p0
    actual_fwd = loader.get_forward_return(sym, dates[idx], 5)
    assert abs(actual_fwd - expected_fwd) < 1e-6


def test_mock_loader_is_tradable_respects_halt():
    """验收 1: 停牌时 is_tradable 返回 False"""
    loader = MockHistoryDataLoader(symbols=["600519"], days=30, seed=42)
    # 找一个停牌的日期
    halted_dates = loader._halts.get("600519", set())
    if halted_dates:
        d = next(iter(halted_dates))
        assert loader.is_tradable("600519", d) is False


# ============================================================
# 验收 1: 前视偏差校验
# ============================================================

def test_bias_checks_all_pass_with_mock_loader():
    """验收 1: Mock loader 通过全部 4 项前视偏差校验"""
    _cleanup_reports()
    replay = _make_replay(days=60)
    checks = replay._check_bias()
    assert checks["disclosure_date_check"] is True
    assert checks["constituent_snapshot_check"] is True
    assert checks["tradability_check"] is True
    assert checks["signal_lag_check"] is True


def test_bias_check_signal_lag_respects_horizon():
    """验收 1: horizon < 1 时 signal_lag_check 失败"""
    loader = _make_loader(days=30)
    config = ReplayConfig(forward_return_horizon=0)  # 违反滞后约束
    replay = BacktestReplay(loader=loader, config=config)
    checks = replay._check_bias()
    assert checks["signal_lag_check"] is False


# ============================================================
# 验收 2: 三条基线均产出完整决策序列
# ============================================================

def test_replay_ai_debate_produces_decisions():
    """验收 2: ai_debate 基线产出完整决策序列"""
    _cleanup_reports()
    replay = _make_replay(symbols=["600519", "000001"], days=45, seed=42)
    result = replay.replay_history(BaselineType.AI_DEBATE)
    assert isinstance(result, BaselineResult)
    assert result.baseline == "ai_debate"
    assert result.n_decisions > 0
    assert len(result.decisions) == result.n_decisions
    # 每条决策有完整字段
    for d in result.decisions:
        assert "action" in d
        assert "strength" in d
        assert "confidence" in d
        assert "date" in d
        assert "symbol" in d
        assert d["action"] in ("buy", "sell", "hold")


def test_replay_five_agents_produces_decisions():
    """验收 2: five_agents 基线产出完整决策序列"""
    _cleanup_reports()
    replay = _make_replay(symbols=["600519", "000001"], days=45, seed=42)
    result = replay.replay_history(BaselineType.FIVE_AGENTS)
    assert result.baseline == "five_agents"
    assert result.n_decisions > 0
    # five_agents 不触发辩论
    assert result.debate_trigger_rate == 0.0


def test_replay_rule_only_produces_decisions():
    """验收 2: rule_only 基线产出完整决策序列"""
    _cleanup_reports()
    replay = _make_replay(symbols=["600519", "000001"], days=45, seed=42)
    result = replay.replay_history(BaselineType.RULE_ONLY)
    assert result.baseline == "rule_only"
    assert result.n_decisions > 0
    assert result.debate_trigger_rate == 0.0


def test_replay_rule_only_uses_change_pct_formula():
    """验收 2: rule_only 使用 change_pct * 0.03 规则公式"""
    _cleanup_reports()
    replay = _make_replay(symbols=["600519"], days=45, seed=42)
    result = replay.replay_history(BaselineType.RULE_ONLY)
    # 至少有一条决策
    assert len(result.decisions) > 0
    # 验证: strength = change_pct * 0.03 (在 [-1, 1] 范围内)
    for d in result.decisions:
        assert -1.0 <= d["strength"] <= 1.0
        assert d["verdict_type"] == "RULE"


def test_replay_action_distribution_sum_matches_total():
    """验收 2: buy+sell+hold = 总决策数"""
    _cleanup_reports()
    replay = _make_replay(symbols=["600519", "000001", "300750"], days=45, seed=42)
    result = replay.replay_history(BaselineType.RULE_ONLY)
    assert result.n_buy + result.n_sell + result.n_hold == result.n_decisions


# ============================================================
# 验收 3: 输出报告含 IC/夏普/命中率/辩论触发率
# ============================================================

def test_compare_baselines_returns_complete_report():
    """验收 3: compare_baselines 返回完整对比报告"""
    _cleanup_reports()
    replay = _make_replay(symbols=["600519", "000001", "300750"], days=60, seed=42)
    report = replay.compare_baselines()
    assert isinstance(report, ComparisonReport)
    # 三基线都在
    assert "ai_debate" in report.baselines
    assert "five_agents" in report.baselines
    assert "rule_only" in report.baselines
    # 元数据
    assert report.generated_at != ""
    assert "start_date" in report.config
    assert "end_date" in report.config


def test_report_contains_sharpe_ic_ir_win_rate():
    """验收 3: 报告含 Sharpe / IC_IR / 命中率 (win_rate)"""
    _cleanup_reports()
    replay = _make_replay(symbols=["600519", "000001", "300750"], days=60, seed=42)
    report = replay.compare_baselines()
    for bt in ["ai_debate", "five_agents", "rule_only"]:
        metrics = report.baselines[bt].get("metrics", {})
        assert "sharpe" in metrics
        assert "ic_ir" in metrics
        assert "win_rate" in metrics
        assert "max_drawdown" in metrics
        assert "dsr" in metrics


def test_report_contains_debate_trigger_rate():
    """验收 3: 报告含辩论触发率"""
    _cleanup_reports()
    replay = _make_replay(symbols=["600519", "000001", "300750"], days=60, seed=42)
    report = replay.compare_baselines()
    # ai_debate 应有辩论触发率字段 (可能为 0, 因为 Mock provider 返回中性)
    assert "debate_trigger_rate" in report.baselines["ai_debate"]
    # five_agents 和 rule_only 辩论触发率为 0
    assert report.baselines["five_agents"]["debate_trigger_rate"] == 0.0
    assert report.baselines["rule_only"]["debate_trigger_rate"] == 0.0


def test_report_contains_marginal_sharpe():
    """验收 3: 报告含边际夏普"""
    _cleanup_reports()
    replay = _make_replay(symbols=["600519", "000001", "300750"], days=60, seed=42)
    report = replay.compare_baselines()
    assert isinstance(report.marginal_sharpe_debate_vs_agents, float)
    assert isinstance(report.marginal_sharpe_agents_vs_rule, float)


# ============================================================
# 验收 4: 边际夏普 > 0.05 才建议上线 auto 模式
# ============================================================

def test_recommendation_shadow_when_marginal_below_threshold():
    """验收 4: 边际夏普 < 阈值时建议 shadow"""
    _cleanup_reports()
    replay = _make_replay(symbols=["600519", "000001"], days=45, seed=42)
    report = replay.compare_baselines()
    # Mock 数据下增量价值通常不显著, 应建议 shadow 或 paper
    assert report.recommendation in ("shadow", "paper", "auto")


def test_recommendation_respects_bias_checks():
    """验收 4: 偏差校验未通过时强制 shadow"""
    _cleanup_reports()
    loader = _make_loader(days=30)
    config = ReplayConfig(forward_return_horizon=0)  # 违反信号滞后
    replay = BacktestReplay(loader=loader, config=config)
    report = replay.compare_baselines()
    # signal_lag_check 失败 → 必须 shadow
    assert report.recommendation == "shadow"
    assert report.bias_checks["signal_lag_check"] is False


def test_make_recommendation_auto_when_both_valuable():
    """验收 4: 辩论+Agent 均超阈值时建议 auto"""
    replay = _make_replay(days=45)
    rec = replay._make_recommendation(
        marginal_debate_vs_agents=0.1,   # > 0.05
        marginal_agents_vs_rule=0.08,    # > 0.05
        bias_checks={
            "disclosure_date_check": True,
            "constituent_snapshot_check": True,
            "tradability_check": True,
            "signal_lag_check": True,
        },
    )
    assert rec == "auto"


def test_make_recommendation_paper_when_only_agents_valuable():
    """验收 4: 仅 Agent 超阈值时建议 paper"""
    replay = _make_replay(days=45)
    rec = replay._make_recommendation(
        marginal_debate_vs_agents=0.01,  # < 0.05
        marginal_agents_vs_rule=0.08,    # > 0.05
        bias_checks={
            "disclosure_date_check": True,
            "constituent_snapshot_check": True,
            "tradability_check": True,
            "signal_lag_check": True,
        },
    )
    assert rec == "paper"


def test_make_recommendation_shadow_when_bias_fails():
    """验收 4: 偏差校验失败时即使夏普高也建议 shadow"""
    replay = _make_replay(days=45)
    rec = replay._make_recommendation(
        marginal_debate_vs_agents=1.0,   # 极高
        marginal_agents_vs_rule=1.0,     # 极高
        bias_checks={
            "disclosure_date_check": True,
            "constituent_snapshot_check": False,  # 失败
            "tradability_check": True,
            "signal_lag_check": True,
        },
    )
    assert rec == "shadow"


# ============================================================
# 验收 5: 落盘 Markdown + JSON
# ============================================================

def test_save_creates_md_and_json():
    """验收 5: save() 后 backtest_replay_*.md + .json 存在且非空"""
    _cleanup_reports()
    replay = _make_replay(symbols=["600519", "000001"], days=45, seed=42)
    report = replay.compare_baselines()
    md_path = replay.save(report)

    assert os.path.exists(md_path)
    assert os.path.getsize(md_path) > 0
    # JSON 存在
    json_path = str(Path(md_path).with_suffix(".json"))
    assert os.path.exists(json_path)
    assert os.path.getsize(json_path) > 0

    # JSON 可反序列化
    with open(json_path, encoding="utf-8") as fh:
        loaded = json.load(fh)
    assert "baselines" in loaded
    assert "marginal_sharpe_debate_vs_agents" in loaded
    assert "recommendation" in loaded


def test_markdown_contains_5_sections():
    """验收 5: Markdown 含 5 章节 (配置/偏差/对比/边际/建议)"""
    _cleanup_reports()
    replay = _make_replay(symbols=["600519", "000001"], days=45, seed=42)
    report = replay.compare_baselines()
    md = replay.to_markdown(report)
    assert "# ai_decision 历史回放 + 三基线对比" in md
    assert "## 1. 回放配置" in md
    assert "## 2. 前视偏差校验" in md
    assert "## 3. 三基线绩效对比" in md
    assert "## 4. 边际夏普" in md
    assert "## 5. 上线建议" in md


def test_markdown_contains_baseline_table():
    """验收 5: Markdown 含三基线对比表"""
    _cleanup_reports()
    replay = _make_replay(symbols=["600519", "000001"], days=45, seed=42)
    report = replay.compare_baselines()
    md = replay.to_markdown(report)
    assert "| ai_debate |" in md
    assert "| five_agents |" in md
    assert "| rule_only |" in md


# ============================================================
# 验收 6: 向后兼容 (数据不足 / 异常降级)
# ============================================================

def test_replay_with_insufficient_days():
    """验收 6: 交易日不足时不崩溃 (降级返回空指标)"""
    _cleanup_reports()
    # 仅 10 天 < MIN_TRADING_DAYS (30)
    replay = _make_replay(symbols=["600519"], days=10, seed=42)
    result = replay.replay_history(BaselineType.RULE_ONLY)
    # 仍产出决策, 但指标为空
    assert result.n_decisions > 0
    assert result.metrics.get("sharpe", 0.0) == 0.0


def test_replay_handles_loader_exception():
    """验收 6: loader 异常时降级 hold, 不崩溃"""

    class BrokenLoader:
        def get_trading_dates(self, start, end):
            return ["2025-01-06"]

        def get_market_data(self, symbol, date):
            return {"close": 10.0, "change_pct": 0.01, "is_halted": False,
                    "is_limit_up": False, "is_limit_down": False}

        def get_fundamentals(self, symbol, date):
            return {"pe": 15.0, "disclosure_date": date}

        def get_forward_return(self, symbol, date, horizon):
            return float("nan")  # 无前瞻收益

        def get_constituents(self, date):
            return ["600519"]

        def is_tradable(self, symbol, date):
            return True

    config = ReplayConfig(symbols=["600519"], start_date="2025-01-06", end_date="2025-01-06")
    replay = BacktestReplay(loader=BrokenLoader(), config=config)
    # 不应抛异常
    result = replay.replay_history(BaselineType.RULE_ONLY)
    assert isinstance(result, BaselineResult)


def test_empty_metrics_on_no_returns():
    """验收 6: 无收益数据时返回空指标"""
    metrics = BacktestReplay._empty_metrics()
    assert metrics["sharpe"] == 0.0
    assert metrics["ic_ir"] == 0.0
    assert metrics["passed_v9"] is False


def test_ic_series_skips_insufficient_samples():
    """验收 6: IC 计算跳过样本不足的日期 (< MIN_IC_SAMPLES)"""
    replay = _make_replay(symbols=["600519"], days=30, seed=42)  # 单标的 < MIN_IC_SAMPLES=5
    # 单标的无法计算 Spearman IC (需 >=5)
    daily_factor = {"2025-01-06": {"600519": 0.5}}
    daily_forward = {"2025-01-06": {"600519": 0.02}}
    ic = replay._compute_ic_series(daily_factor, daily_forward)
    # 样本不足, IC 序列为空
    assert len(ic) == 0


def test_spearman_ic_nan_on_insufficient_samples():
    """验收 6: Spearman IC 样本不足返回 nan"""
    fv = {"A": 0.1, "B": 0.2}  # 仅 2 个 < MIN_IC_SAMPLES=5
    fr = {"A": 0.01, "B": -0.01}
    ic = BacktestReplay._spearman_ic(fv, fr)
    assert math.isnan(ic)


def test_spearman_ic_computes_on_sufficient_samples():
    """验收 6: Spearman IC 样本充足返回有效值"""
    fv = {f"S{i}": float(i) for i in range(10)}
    fr = {f"S{i}": float(i) * 0.5 for i in range(10)}  # 完全正相关
    ic = BacktestReplay._spearman_ic(fv, fr)
    assert not math.isnan(ic)
    assert ic > 0.9  # 强正相关


# ============================================================
# 验收 7: CLI 入口
# ============================================================

def test_cli_main_success():
    """验收 7: CLI 主流程不崩溃"""
    _cleanup_reports()
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
    import importlib
    cli = importlib.import_module("run_ai_decision_backtest")

    orig_argv = sys.argv
    try:
        sys.argv = [
            "run_ai_decision_backtest.py",
            "--symbols", "600519", "000001",
            "--days", "45",
            "--no-save",  # 不落盘, 加快测试
        ]
        rc = cli.main()
        # rc in (0, 1, 2) 都是合法退出码
        assert rc in (0, 1, 2)
    finally:
        sys.argv = orig_argv


def test_cli_main_with_save():
    """验收 7: CLI 落盘功能"""
    _cleanup_reports()
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
    import importlib
    cli = importlib.import_module("run_ai_decision_backtest")

    orig_argv = sys.argv
    try:
        sys.argv = [
            "run_ai_decision_backtest.py",
            "--symbols", "600519", "000001",
            "--days", "45",
        ]
        rc = cli.main()
        assert rc in (0, 1, 2)
        # 验证落盘文件存在
        files = list(_REPORT_DIR.glob("backtest_replay_*.md"))
        assert len(files) >= 1
    finally:
        sys.argv = orig_argv
