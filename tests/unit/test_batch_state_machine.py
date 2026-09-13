"""建仓批次状态机（shadow 只读渲染器）单测 — v9.5 缺口③

覆盖（与接入方案 §5.7 F1 路径 C / 方略 v9.5 §五 + v9.4 对向条款对齐）:
    T01 批一未启动 → T+0 基础配置渲染
    T02 批二下跌触发（含 45% 上限校核标记）
    T03 下跌带边界（-5% / -8% 含界；-4.9% / -8.1% 不含）
    T04 下跌基准「孰高」规则
    T05 下跌路径 PE 分位约束（<55%）
    T06 对向条款触发 + 三等分切片
    T07 对向分批间隔 ≥5 个交易日
    T08 12 个月时间窗失效
    T09 风控优先：L1 阻断新增权益（批三·收缩）
    T10 组合回撤 -6% 触发收缩态
    T11 熔断等级未知 → 保守阻断
    T12 恢复·回补（≤L2 且修复 ≥50%，每批 ≤12 万）
    T13 末期收官窗口（2029Q4 起）
    T14 预验收上限（45%）/ 验收后（55%）校核
    T15 输入缺失时不触发（保守）
    T16 输出 schema 与 JSON 可序列化
    T17 模块源文件不含自身文件名词元（对齐「生产路径 0 命中」验收）
    T18 shadow 入口 demo 冒烟（子进程，产出 batch_plan_{date}.json）
    T19 shadow 入口缺省数据源时拒绝运行（RC=2）
    T20 口径参数可覆盖（窗口 6 个月示例）

放置于 tests/unit/（禁 tests/e2e/，祖先目录关键字会令用例天生被 skip）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.execution.batch_state_machine import (  # noqa: E402
    BatchInputs,
    BatchPolicy,
    render_batch_plan,
)

_SCRIPT = _PROJECT_ROOT / "scripts" / "shadow_batch_plan.py"


def _inp(**over: Any) -> BatchInputs:
    base: dict[str, Any] = {
        "as_of": "2026-12-15",
        "batch1_done": True,
        "b1_entry_date": "2026-10-01",
        "cns_level": "L0",
        "portfolio_dd_pct": -0.038,
        "dd_since_last_invest_pct": -0.06,
        "intraday_cum_dd_pct": -0.02,
        "pe_pct_8y": 0.50,
        "close_above_ma250": False,
        "ma250_slope_20d": -0.01,
    }
    base.update(over)
    return BatchInputs(**base)


def test_t01_batch1_not_started_proposes_base_allocation() -> None:
    rep = render_batch_plan(_inp(batch1_done=False, b1_entry_date=None))
    assert rep["state"]["batch1"] == "not_started"
    assert rep["state"]["batch2"] == "not_applicable"
    amounts = {a["asset"]: a["amount_wan"] for a in rep["proposed_actions"]}
    assert amounts == {"510300": 45.0, "515080": 30.0, "satellite": 15.0, "treasury_gold": 60.0}
    assert rep["projection"]["cum_equity_pct"] == 30.0
    assert all(c["status"] == "ok" for c in rep["constraints"])


def test_t02_pullback_triggers_with_cap_flag() -> None:
    rep = render_batch_plan(_inp())
    assert rep["state"]["batch2"] == "triggered_down"
    assert rep["triggers"]["pullback_path"]["met"] is True
    amounts = {a["asset"]: a["amount_wan"] for a in rep["proposed_actions"]}
    assert amounts == {"510300": 30.0, "515080": 15.0, "510500": 15.0, "treasury_gold": 15.0}
    assert rep["projection"]["cum_equity_pct"] == 50.0
    assert rep["projection"]["cum_equity_pct_labeled"] == 55.0
    cap = next(c for c in rep["constraints"] if c["code"] == "EQUITY_CAP")
    assert cap["status"] == "violation"
    assert cap["limit_pct"] == 45.0


@pytest.mark.parametrize(
    ("dd", "expected"),
    [(-0.049, False), (-0.050, True), (-0.080, True), (-0.081, False)],
)
def test_t03_pullback_band_boundaries(dd: float, expected: bool) -> None:
    rep = render_batch_plan(_inp(dd_since_last_invest_pct=dd, intraday_cum_dd_pct=-0.01))
    assert rep["triggers"]["pullback_path"]["met"] is expected


def test_t04_effective_dd_takes_deeper_source() -> None:
    rep = render_batch_plan(_inp(dd_since_last_invest_pct=-0.03, intraday_cum_dd_pct=-0.06))
    assert rep["triggers"]["pullback_path"]["conditions"]["dd_effective"] == -0.06
    assert rep["triggers"]["pullback_path"]["met"] is True


def test_t05_pullback_requires_pe_below_55() -> None:
    rep = render_batch_plan(_inp(pe_pct_8y=0.56))
    assert rep["triggers"]["pullback_path"]["met"] is False
    assert rep["triggers"]["pullback_path"]["conditions"]["pe_ok"] is False


def test_t06_opposite_clause_triggers_with_slice() -> None:
    rep = render_batch_plan(
        _inp(
            dd_since_last_invest_pct=-0.02,
            intraday_cum_dd_pct=-0.01,
            close_above_ma250=True,
            ma250_slope_20d=0.0,
            pe_pct_8y=0.60,
        )
    )
    assert rep["state"]["batch2"] == "triggered_opposite"
    assert rep["triggers"]["opposite_path"]["met"] is True
    tranches = rep["triggers"]["opposite_path"]["tranches"]
    assert tranches["done"] == 0
    assert tranches["next_eligible"] is True
    amounts = {a["asset"]: a["amount_wan"] for a in rep["proposed_actions"] if a["stage"] == "批二·对向"}
    assert amounts == {"510300": 10.0, "515080": 5.0, "510500": 5.0}


def test_t07_opposite_gap_rule_blocks_early_tranche() -> None:
    common: dict[str, Any] = {
        "dd_since_last_invest_pct": -0.02,
        "intraday_cum_dd_pct": -0.01,
        "close_above_ma250": True,
        "ma250_slope_20d": 0.001,
        "pe_pct_8y": 0.60,
        "opposite_tranches_done": 1,
    }
    early = render_batch_plan(_inp(**common, opposite_last_tranche_date="2026-12-13"))
    assert early["triggers"]["opposite_path"]["tranches"]["next_eligible"] is False
    assert not [a for a in early["proposed_actions"] if a["stage"] == "批二·对向"]

    ok = render_batch_plan(_inp(**common, opposite_last_tranche_date="2026-12-05"))
    assert ok["triggers"]["opposite_path"]["tranches"]["next_eligible"] is True
    assert [a for a in ok["proposed_actions"] if a["stage"] == "批二·对向"]


def test_t08_window_expiry_blocks_batch2() -> None:
    rep = render_batch_plan(_inp(b1_entry_date="2025-11-30", as_of="2026-12-01"))
    assert rep["state"]["batch2"] == "window_expired"
    assert not [a for a in rep["proposed_actions"] if a["stage"].startswith("批二")]
    assert any("留存现金层" in n for n in rep["notes"])


def test_t09_reduce_mode_l1_blocks_equity() -> None:
    rep = render_batch_plan(_inp(cns_level="L1"))
    assert rep["state"]["reduce_mode"] is True
    assert rep["state"]["batch2"] == "blocked_reduce"
    assert rep["triggers"]["pullback_path"]["blocked_by"] == "risk_level"
    stages = {a["stage"] for a in rep["proposed_actions"]}
    assert stages == {"批三·收缩"}
    assert not [a for a in rep["proposed_actions"] if a["stage"].startswith("批二")]


def test_t10_reduce_mode_by_portfolio_dd() -> None:
    rep = render_batch_plan(_inp(cns_level="L0", portfolio_dd_pct=-0.062))
    assert rep["state"]["reduce_mode"] is True
    assert rep["state"]["batch2"] == "blocked_reduce"


def test_t11_unknown_level_conservative_block() -> None:
    rep = render_batch_plan(_inp(cns_level="L?"))
    assert rep["state"]["batch2"] == "blocked_level_unknown"
    assert not [a for a in rep["proposed_actions"] if a["stage"].startswith("批二")]
    assert rep["warnings"]


def test_t12_recovery_requires_l2_and_rebound() -> None:
    ok = render_batch_plan(_inp(cns_level="L2", rebound_from_low_pct=0.55))
    assert ok["state"]["recovery"]["eligible"] is True
    acts = [a for a in ok["proposed_actions"] if a["stage"] == "恢复·回补"]
    assert len(acts) == 1
    assert acts[0]["amount_wan"] == 12.0

    low_rebound = render_batch_plan(_inp(cns_level="L2", rebound_from_low_pct=0.45))
    assert low_rebound["state"]["recovery"]["eligible"] is False

    blocked = render_batch_plan(_inp(cns_level="L3", rebound_from_low_pct=0.55))
    assert blocked["state"]["recovery"]["eligible"] is False
    assert blocked["state"]["recovery"]["blocked_by"] == "risk_level"


def test_t13_final_stage_window() -> None:
    rep = render_batch_plan(_inp(as_of="2029-10-05"))
    assert rep["state"]["final_stage"] is True
    assert any("末期收官" in n for n in rep["notes"])
    rep_before = render_batch_plan(_inp(as_of="2029-09-30"))
    assert rep_before["state"]["final_stage"] is False


def test_t14_cap_status_pre_and_post_acceptance() -> None:
    pre = render_batch_plan(_inp(acceptance_passed=False))
    cap_pre = next(c for c in pre["constraints"] if c["code"] == "EQUITY_CAP")
    assert cap_pre["status"] == "violation"
    assert cap_pre["limit_pct"] == 45.0

    post = render_batch_plan(_inp(acceptance_passed=True))
    cap_post = next(c for c in post["constraints"] if c["code"] == "EQUITY_CAP")
    assert cap_post["status"] == "ok"
    assert cap_post["limit_pct"] == 55.0


def test_t15_missing_inputs_do_not_trigger() -> None:
    rep = render_batch_plan(
        BatchInputs(as_of="2026-12-15", batch1_done=True, b1_entry_date="2026-10-01", cns_level="L0")
    )
    assert rep["triggers"]["pullback_path"]["conditions"]["data_ok"] is False
    assert rep["triggers"]["opposite_path"]["conditions"]["data_ok"] is False
    assert rep["triggers"]["pullback_path"]["met"] is False
    assert rep["state"]["batch2"] == "awaiting"


def test_t16_output_schema_and_serializable() -> None:
    rep = render_batch_plan(_inp())
    for key in (
        "schema_version",
        "render_date",
        "mode",
        "state",
        "window",
        "triggers",
        "proposed_actions",
        "constraints",
        "notes",
    ):
        assert key in rep
    assert json.loads(json.dumps(rep))["schema_version"] == "1.0"


def test_t17_module_source_has_no_self_reference_token() -> None:
    src = (_PROJECT_ROOT / "utils" / "execution" / "batch_state_machine.py").read_text(encoding="utf-8")
    assert "batch_state_machine" not in src
    assert "exit_calendar" not in src


def test_t18_entry_script_demo_smoke(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--demo", "--date", "2026-09-13", "--out-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_PROJECT_ROOT),
        timeout=180,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = tmp_path / "batch_plan_2026-09-13.json"
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["mode"] == "shadow"
    assert payload["state"]["batch2"] == "triggered_down"


def test_t19_entry_script_requires_explicit_source(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--date", "2026-09-13", "--out-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_PROJECT_ROOT),
        timeout=120,
        check=False,
    )
    assert proc.returncode == 2


def test_t20_policy_override_window_months() -> None:
    pol = BatchPolicy(window_months=6)
    rep = render_batch_plan(_inp(as_of="2027-04-02"), policy=pol)
    assert rep["state"]["batch2"] == "window_expired"
