"""报告项 15 (批次三) 回归: 模拟/实盘对账任务执行器.

覆盖 `utils/risk/trade_reconciliation_runner.py` 与 CLI / EOD 阶段适配层:
    - 计划单提取 (合成 order_id / scope 语义 / 标的归一化)
    - 成交回报载入 (meta.order_id 关联 / 非法行跳过)
    - 关联模式判定与降级 (order_id → symbol_side 聚合)
    - status 语义 (no_plan / no_planned_orders / no_fills 绝不假 PASS)
    - T17 持仓 drift 装配 (无 broker / 有 broker)
    - 严格模式退出码

标记 ✗修复前 的用例在"对账任务未接入"的旧状态下无从运行 (模块不存在) → 天然为红。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from utils.risk.trade_order_reconciler import FillRecord, PlannedOrder
from utils.risk.trade_reconciliation_runner import (
    aggregate_symbol_side,
    build_planned_orders,
    format_summary,
    linkage_coverage,
    load_fill_records,
    load_local_position_book,
    reconcile_date,
    report_path_for,
    run_position_drift,
    strict_exit_code,
)


def _plan_with_orders() -> dict[str, Any]:
    return {
        "execution_plan": {
            "morning_orders": [
                {
                    "code": "588000",
                    "side": "BUY",
                    "shares": 5100,
                    "limit_price": 1.234,
                },
                {
                    "code": "600519.SH",
                    "side": "SELL",
                    "shares": 200,
                    "limit_price": 1680.0,
                },
            ],
            "afternoon_orders": [
                {"code": "159915", "side": "BUY", "shares": 2000, "limit_price": None}
            ],
        },
        "hedge_execution": {
            "options_orders": [
                {
                    "order_id": "OPT-0001",
                    "underlying_code": "510050.SH",
                    "direction": "BUY",
                    "contracts": 3,
                    "premium_per_unit": 0.05,
                }
            ],
            "covered_call_orders": [
                {
                    "order_id": "OPT-0002",
                    "underlying_code": "510300",
                    "direction": "SELL",
                    "contracts": 2,
                    "premium_per_unit": 0.03,
                }
            ],
        },
    }


# ============================================================
# 计划单提取
# ============================================================
def test_build_planned_orders_synthesizes_order_id_and_normalizes_symbol():
    orders = build_planned_orders(_plan_with_orders(), "2026-09-10", scope="etf")
    assert len(orders) == 3
    assert [o.order_id for o in orders] == [
        "20260910_morning_588000_BUY_01",
        "20260910_morning_600519_SELL_02",
        "20260910_afternoon_159915_BUY_01",
    ]
    # 代码归一化: 600519.SH → 600519
    assert orders[1].symbol == "600519"
    assert orders[1].side == "SELL"
    assert orders[1].planned_qty == 200
    assert orders[1].limit_price == 1680.0
    assert orders[2].limit_price is None


def test_build_planned_orders_scope_all_keeps_option_order_id():
    orders = build_planned_orders(_plan_with_orders(), "2026-09-10", scope="all")
    ids = {o.order_id for o in orders}
    assert {"OPT-0001", "OPT-0002"} <= ids
    opt = next(o for o in orders if o.order_id == "OPT-0001")
    assert opt.symbol == "510050"
    assert opt.side == "BUY"
    assert opt.planned_qty == 3


def test_build_planned_orders_rejects_bad_scope():
    with pytest.raises(ValueError):
        build_planned_orders(_plan_with_orders(), "2026-09-10", scope="nope")


def test_build_planned_orders_skips_zero_qty_and_blank_code():
    plan = {
        "execution_plan": {
            "morning_orders": [
                {"code": "", "side": "BUY", "shares": 100},
                {"code": "588000", "side": "BUY", "shares": 0},
            ]
        }
    }
    assert build_planned_orders(plan, "2026-09-10") == []


# ============================================================
# 成交载入
# ============================================================
def test_load_fill_records_reads_meta_order_id(tmp_path: Path):
    p = tmp_path / "fills_2026-09-10.jsonl"
    p.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-09-10T09:35:00",
                        "symbol": "588000.SH",
                        "side": "BUY",
                        "filled_qty": 5100,
                        "avg_price": 1.235,
                        "meta": {"order_id": "20260910_morning_588000_BUY_01"},
                    }
                ),
                json.dumps(
                    {
                        "symbol": "159915",
                        "side": "BUY",
                        "filled_qty": 1000,
                        "avg_price": 2.0,
                    }
                ),
                "{ not-json",
                "",
            ]
        ),
        encoding="utf-8",
    )
    fills = load_fill_records("2026-09-10", p)
    assert len(fills) == 2
    assert fills[0].order_id == "20260910_morning_588000_BUY_01"
    assert fills[0].symbol == "588000"
    assert fills[1].order_id == ""  # 无 meta.order_id → 空 (触发降级判定)


def test_load_fill_records_missing_file_returns_empty(tmp_path: Path):
    assert load_fill_records("2026-09-10", tmp_path / "nope.jsonl") == []


def test_linkage_coverage():
    assert linkage_coverage([]) == 0.0
    assert linkage_coverage([FillRecord("f1", "o1", "588000", "BUY", 1, 1.0)]) == 1.0
    mixed = [
        FillRecord("f1", "o1", "588000", "BUY", 1, 1.0),
        FillRecord("f2", "", "159915", "BUY", 1, 2.0),
    ]
    assert linkage_coverage(mixed) == 0.5


# ============================================================
# 聚合降级
# ============================================================
def test_aggregate_symbol_side_merges_qty_and_vwap():
    orders = [
        PlannedOrder("a", "588000", "BUY", 1000, 1.0),
        PlannedOrder("b", "588000", "BUY", 2000, None),
        PlannedOrder("c", "159915", "SELL", 500, 2.0),
    ]
    fills = [
        FillRecord("f1", "", "588000", "BUY", 1000, 1.1),
        FillRecord("f2", "", "588000", "BUY", 1500, 1.3),
    ]
    agg_o, agg_f = aggregate_symbol_side(orders, fills)
    omap = {o.order_id: o for o in agg_o}
    assert omap["588000|BUY"].planned_qty == 3000
    assert omap["159915|SELL"].planned_qty == 500
    fmap = {f.order_id: f for f in agg_f}
    assert fmap["588000|BUY"].filled_qty == 2500
    # VWAP = (1000*1.1 + 1500*1.3) / 2500 = 1.22
    assert fmap["588000|BUY"].avg_price == pytest.approx(1.22)


# ============================================================
# 主入口: status 语义 (绝不假 PASS)
# ============================================================
def test_reconcile_date_no_plan(tmp_path: Path):
    report = reconcile_date(
        "2026-09-10", plan_path=tmp_path / "none.json", write=True, output_dir=tmp_path
    )
    assert report["status"] == "no_plan"
    assert report["verified"] is False
    assert (tmp_path / "reconciliation_2026-09-10_etf.json").exists()


def test_report_path_separates_scope(tmp_path: Path):
    """不同 scope 报告不得同名 (否则 --scope all 会静默覆盖 EOD 的 --scope etf 报告)."""
    assert report_path_for("2026-09-10", "etf", tmp_path) != report_path_for(
        "2026-09-10", "all", tmp_path
    )
    plan_p = tmp_path / "trade_plan_20260910.json"
    plan_p.write_text(json.dumps(_plan_with_orders()), encoding="utf-8")
    reconcile_date("2026-09-10", plan_path=plan_p, write=True, output_dir=tmp_path)
    reconcile_date(
        "2026-09-10", plan_path=plan_p, scope="all", write=True, output_dir=tmp_path
    )
    assert (tmp_path / "reconciliation_2026-09-10_etf.json").exists()
    assert (tmp_path / "reconciliation_2026-09-10_all.json").exists()


def test_reconcile_date_no_planned_orders(tmp_path: Path):
    plan_p = tmp_path / "trade_plan_20260910.json"
    plan_p.write_text(json.dumps({"execution_plan": {"morning_orders": []}}), encoding="utf-8")
    report = reconcile_date(
        "2026-09-10",
        plan_path=plan_p,
        fills_path=tmp_path / "none.jsonl",
        write=False,
    )
    assert report["status"] == "no_planned_orders"
    assert report["verified"] is False


def test_reconcile_date_no_fills_is_not_pass(tmp_path: Path):
    plan_p = tmp_path / "trade_plan_20260910.json"
    plan_p.write_text(json.dumps(_plan_with_orders()), encoding="utf-8")
    report = reconcile_date(
        "2026-09-10",
        plan_path=plan_p,
        fills_path=tmp_path / "none.jsonl",
        write=False,
    )
    assert report["status"] == "no_fills"
    assert report["verified"] is False
    assert report["issues_count"] > 0
    assert report["planned_count"] == 3


def test_reconcile_date_order_level_pass(tmp_path: Path):
    """订单级全通过: 计划单 order_id 与成交 order_id 完全对齐."""
    plan_p = tmp_path / "trade_plan_20260910.json"
    plan_p.write_text(json.dumps(_plan_with_orders()), encoding="utf-8")
    orders = build_planned_orders(_plan_with_orders(), "2026-09-10", scope="etf")
    fills_p = tmp_path / "fills_2026-09-10.jsonl"
    lines = []
    for o in orders:
        lines.append(
            json.dumps(
                {
                    "symbol": o.symbol,
                    "side": o.side,
                    "filled_qty": o.planned_qty,
                    "avg_price": o.limit_price or 1.0,
                    "meta": {"order_id": o.order_id},
                }
            )
        )
    fills_p.write_text("\n".join(lines), encoding="utf-8")

    report = reconcile_date(
        "2026-09-10",
        plan_path=plan_p,
        fills_path=fills_p,
        write=True,
        output_dir=tmp_path,
    )
    assert report["linkage_mode"] == "order_id"
    assert report["linkage_degraded"] is False
    assert report["status"] == "ok"
    assert report["verified"] is True, report["issues"]
    assert report["issues_count"] == 0


def test_reconcile_date_degrades_to_symbol_side(tmp_path: Path):
    """成交侧无 order_id → 降级 (symbol, side) 聚合, 报告显式标记降级."""
    plan_p = tmp_path / "trade_plan_20260910.json"
    plan_p.write_text(json.dumps(_plan_with_orders()), encoding="utf-8")
    fills_p = tmp_path / "fills_2026-09-10.jsonl"
    fills_p.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "symbol": "588000",
                        "side": "BUY",
                        "filled_qty": 5100,
                        "avg_price": 1.234,
                    }
                ),
                json.dumps(
                    {
                        "symbol": "600519",
                        "side": "SELL",
                        "filled_qty": 200,
                        "avg_price": 1680.0,
                    }
                ),
                json.dumps(
                    {
                        "symbol": "159915",
                        "side": "BUY",
                        "filled_qty": 2000,
                        "avg_price": 1.0,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    report = reconcile_date(
        "2026-09-10", plan_path=plan_p, fills_path=fills_p, write=False
    )
    assert report["linkage_mode"] == "symbol_side"
    assert report["linkage_degraded"] is True
    assert report["linkage_coverage"] == 0.0
    assert report["verified"] is True, report["issues"]
    assert any("降级" in n for n in report["notes"])


def test_reconcile_date_detects_qty_deviation(tmp_path: Path):
    plan_p = tmp_path / "trade_plan_20260910.json"
    plan_p.write_text(json.dumps(_plan_with_orders()), encoding="utf-8")
    orders = build_planned_orders(_plan_with_orders(), "2026-09-10", scope="etf")
    fills_p = tmp_path / "fills_2026-09-10.jsonl"
    fills_p.write_text(
        json.dumps(
            {
                "symbol": orders[0].symbol,
                "side": orders[0].side,
                "filled_qty": orders[0].planned_qty // 2,  # 50% 缺口
                "avg_price": 1.234,
                "meta": {"order_id": orders[0].order_id},
            }
        ),
        encoding="utf-8",
    )
    report = reconcile_date(
        "2026-09-10", plan_path=plan_p, fills_path=fills_p, write=False
    )
    assert report["verified"] is False
    assert any("QTY_DEVIATION" in i for i in report["issues"])


def test_reconcile_date_scope_all_notes_hedge_gap(tmp_path: Path):
    plan_p = tmp_path / "trade_plan_20260910.json"
    plan_p.write_text(json.dumps(_plan_with_orders()), encoding="utf-8")
    report = reconcile_date(
        "2026-09-10",
        plan_path=plan_p,
        fills_path=tmp_path / "none.jsonl",
        scope="all",
        write=False,
    )
    assert any("FillsStore" in n for n in report["notes"])


# ============================================================
# T17 持仓 drift
# ============================================================
def test_load_local_position_book(tmp_path: Path):
    p = tmp_path / "positions.json"
    p.write_text(
        json.dumps(
            {
                "positions": {
                    "588080.SH": {"shares": 15600},
                    "159915": {"total_shares": 2000},
                    "510300.SH": {"shares": 0},
                }
            }
        ),
        encoding="utf-8",
    )
    book = load_local_position_book(p)
    assert book == {"588080": 15600, "159915": 2000}


def test_run_position_drift_without_broker():
    out = run_position_drift(None, local_book={"588000": 100})
    assert out["available"] is False
    assert "跳过" in out["note"]


class _FakeBroker:
    def __init__(self, positions: dict[str, int]) -> None:
        self._positions = positions

    def get_positions(self) -> dict[str, int]:
        return self._positions


def test_run_position_drift_detects_halt():
    broker = _FakeBroker({"588000": 0})
    out = run_position_drift(
        broker, local_book={"588000": 1000}, drift_halt_pct=0.10
    )
    assert out["available"] is True
    assert out["halt_count"] == 1
    assert out["verdict"] == "halt"


def test_run_position_drift_clean_pass():
    broker = _FakeBroker({"588000": 1000})
    out = run_position_drift(broker, local_book={"588000": 1000})
    assert out["available"] is True
    assert out["drift_count"] == 0
    assert out["verdict"] == "pass"


# ============================================================
# 摘要 / 退出码 / EOD 适配层
# ============================================================
def test_format_summary_contains_key_fields():
    report = {
        "date": "2026-09-10",
        "scope": "etf",
        "status": "no_fills",
        "linkage_mode": "order_id",
        "linkage_coverage": 0.0,
        "planned_count": 3,
        "fills_count": 0,
        "issues_count": 3,
        "unexpected_fills": [],
        "verified": False,
        "notes": ["当日无任何成交回报"],
        "issues": ["[ORDER_COVERAGE] x"],
    }
    text = format_summary(report)
    assert "2026-09-10" in text and "no_fills" in text and "verified=False" in text


def test_strict_exit_code_non_strict_always_zero(monkeypatch):
    monkeypatch.delenv("RECONCILE_STRICT", raising=False)
    assert strict_exit_code({"verified": False}) == 0


def test_strict_exit_code_strict_env(monkeypatch):
    monkeypatch.setenv("RECONCILE_STRICT", "1")
    assert strict_exit_code({"verified": False}) == 1
    assert strict_exit_code({"verified": True}) == 0


def test_eod_phase4_93_skip_and_run(tmp_path: Path, monkeypatch):
    """EOD 适配层: --skip-reconcile 跳过; 正常执行返回 True 并记录 summary."""
    import importlib.util
    import sys
    import types

    root = Path(__file__).resolve().parents[2]
    eod_path = root / "15_每日工作流" / "run_daily_eod_workflow.py"
    if not eod_path.exists():
        pytest.skip("EOD 工作流脚本不存在")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("_eod_wf_p15", eod_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001  # 环境依赖缺失 → 跳过 (非缺陷)
        pytest.skip(f"EOD 模块导入失败: {exc}")

    summary: dict[str, Any] = {"phases": {}}
    assert (
        mod.run_phase4_93_reconciliation(
            "2026-09-10", summary, types.SimpleNamespace(skip_reconcile=True)
        )
        is False
    )
    assert summary["phases"]["phase4_93_reconciliation"]["skipped"] is True

    summary2: dict[str, Any] = {"phases": {}}
    # 重定向报告输出目录, 避免测试污染仓库 reports/reconciliation/
    monkeypatch.setattr(
        "utils.risk.trade_reconciliation_runner.OUTPUT_DIR", tmp_path
    )
    ok = mod.run_phase4_93_reconciliation(
        "2026-09-11", summary2, types.SimpleNamespace(skip_reconcile=False)
    )
    assert ok is True
    rec = summary2["phases"]["phase4_93_reconciliation"]
    assert rec["success"] is True
    assert "verified" in rec and "linkage_mode" in rec
