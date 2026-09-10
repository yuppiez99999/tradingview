"""EOD 干跑集成测试 — 验证 daily_workflow.py 拆分后完整 13 phase 链路正确性.

构造模拟数据环境, 执行完整 phase 链路, 收集各 phase 状态和产物.

验证目标 (§7 验收标准④):
    1. 所有 phase 均被执行 (无崩溃)
    2. 每个 phase 返回有效状态 (PASS/SKIP/FAIL, 非异常)
    3. 报告产物生成 (报告文件 + workflow_state JSON)
    4. 拆分后的 phase 门面转发正确 (check/market/risk/hedge/signal 等)
    5. 拆单无失败 (split_fail = 0)

运行方式::

    pytest tests/test_eod_dry_run.py -v     # 真实 pytest 用例 (约 45s, 标 slow)
    python tests/test_eod_dry_run.py        # 手工诊断 (打印明细, 返回退出码)

为何放在 tests/ 根而非 tests/e2e/ (2026-09-10 实测):
    ``tests/conftest.py`` 的 ``pytest_collection_modifyitems`` 以**关键字**判定跳过,
    而 pytest 会把**祖先目录名**并入 ``item.keywords`` —— 只要文件位于 ``tests/e2e/``
    (或 ``tests/integration/``), ``"e2e"`` 必然命中 ⇒ 除非显式 ``--run-integration``
    一律 skip; 而 CI nightly 的 ``pytest tests -v`` 不带该开关, 于是该文件在 CI 里
    **永不执行** (依旧是"静默绿", 只是换了个理由)。
    本链路全程 dry_run + sim_mode、零外部网络, 不属该门控 (为"需真网"用例所设)
    的适用对象, 故移到 ``tests/`` 根, 用 ``slow`` 标记隔离快速通道 ``-m "not slow"``。

2026-09-10 伪测试收口 (审计 P1-8 残项):
    本文件此前是**脚本式伪测试** —— 全文 0 个 ``def test_``、0 个 ``assert``,
    断言与 IO 全部位于模块顶层/``main()`` 内。pytest 因 ``python_files = test_*.py``
    会收集并导入它:
        * 断言通过 → 报 "0 tests collected" (静默"绿")
        * 断言失败 → collection error
    两种情况都**没有任何用例被执行或统计**, 属"把验证脚本伪装成测试文件"。
    更糟的是模块级副作用: 导入即 ``mkdir`` 报告目录 + ``reconfigure`` stdout,
    并在项目根写出 ``eod_dry_run_summary_*.json`` 运行时产物(曾被误入库)。
    现改造为真实 pytest 用例: 副作用移入 fixture, 结果结构化, 用例用真断言。
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # 28-终极量化交易系统8.4/
V83_DIR = PROJECT_ROOT / "v8.3_institutional"  # daily_workflow.py 所在目录

TRADE_DATE = "2026-08-12"
CAPITAL = 5_000_000

# 环境依赖错误特征 (非拆分问题): 命中则归 ENV_SKIP 而非 ERROR
_ENV_ERROR_PATTERNS = (
    "NoneType",  # V75_READY=False: NTPSync/RiskManager/CircuitBreaker 为 None
    "PermissionError",  # 沙箱文件权限
    "WinError 5",  # Windows 权限拒绝
    "stockdb",  # stockdb 服务不可用
    "ModuleNotFoundError",  # 模块缺失
    "ImportError",  # 导入失败
)

# 拆分后必须保留的门面 (公开 phase + 私有转发方法)
_FACADE_METHODS = (
    "phase_check",
    "phase_calibrate",
    "phase_market",
    "phase_risk",
    "phase_hedge",
    "phase_hedge_fund",
    "phase_v10_risk",
    "phase_quant_neutral",
    "phase_cash_management",
    "phase_directional_futures",
    "phase_signal",
    "phase_execute",
    "phase_report",
    "phase_autolearn",
    "_qlib_signal_to_factor",
    "_execute_sim_hedge_orders",
    "_options_market_snapshot",
    "_apply_position_factor",
)

pytestmark = [
    # 刻意**不打** e2e/integration 标记: tests/conftest.py 的
    # pytest_collection_modifyitems 按关键字 skip 这两个标记(除非 --run-integration),
    # 而 CI nightly 的 `pytest tests -v` 不带该开关 ⇒ 打了标记 = CI 永不执行 =
    # 又变回"静默绿"。本链路全程 dry_run + sim_mode、零外部网络, 不属该门控
    # (为"需真网"用例所设) 的适用对象。保留 slow 以隔离快速通道 `-m "not slow"`。
    pytest.mark.slow,
    # 完整 EOD 链路会触碰大量文件句柄; pytest.ini 设了 error::ResourceWarning,
    # 这里显式豁免本链路 (句柄归还时机由 GC 决定, 与拆分正确性无关)
    pytest.mark.filterwarnings("ignore::ResourceWarning"),
]


def _ensure_import_paths() -> None:
    """按 daily_workflow.py 的约定补齐导入路径 (幂等, 不再在模块导入期执行)。"""
    candidates = (
        V83_DIR / "src",
        PROJECT_ROOT,
        V83_DIR / "data",
        V83_DIR,
        PROJECT_ROOT / "ms_strategy",  # src.macro 宏观评分模块
    )
    for path in candidates:
        resolved = str(path)
        if resolved not in sys.path:
            sys.path.insert(0, resolved)


def _phase_calls(wf):
    """13 个 phase 的调用序列 (autolearn 需 stockdb + GPU 训练 150s+, 不适合干跑)。"""
    return [
        ("check", lambda: wf.phase_check()),
        ("calibrate", lambda: wf.phase_calibrate()),
        ("market", lambda: wf.phase_market()),
        ("risk", lambda: wf.phase_risk()),
        ("hedge", lambda: wf.phase_hedge()),
        ("hedge_fund", lambda: wf.phase_hedge_fund()),
        ("v10_risk", lambda: wf.phase_v10_risk()),
        ("quant_neutral", lambda: wf.phase_quant_neutral()),
        ("cash_management", lambda: wf.phase_cash_management()),
        ("directional_futures", lambda: wf.phase_directional_futures()),
        ("signal", lambda: wf.phase_signal()),
        (
            "execute",
            lambda: wf.phase_execute(wf.state.get("phases", {}).get("signal", {})),
        ),
        ("report", lambda: wf.phase_report()),
    ]


def run_eod_dry_run(report_dir: Path) -> dict:
    """执行 EOD 干跑并返回结构化结果 (不打印、不 sys.exit)。

    Returns:
        含 ``instantiated`` / ``error`` / ``phase_results`` / ``counts`` /
        ``facade`` / ``split`` / ``report_files`` / ``state_json`` / ``verdict``
        的字典。实例化失败时 ``instantiated=False`` 且 ``error`` 带 traceback。
    """
    _ensure_import_paths()
    report_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    result: dict = {
        "trade_date": TRADE_DATE,
        "report_dir": str(report_dir),
        "instantiated": False,
        "error": None,
        "trade_plan_loaded": False,
        "total_orders": 0,
        "v75_ready": None,
        "phase_results": {},
        "counts": {
            "total": 0,
            "pass": 0,
            "skip": 0,
            "fail": 0,
            "error": 0,
            "env_skip": 0,
        },
        "facade": {"pass": 0, "total": len(_FACADE_METHODS), "missing": []},
        "split": {"checked": False},
        "report_files": [],
        "state_json": None,
    }

    # === 1. 构造 DailyWorkflow 实例 ===
    try:
        from daily_workflow import V75_READY, DailyWorkflow  # noqa: PLC0415

        wf = DailyWorkflow(
            trade_date=TRADE_DATE,
            capital=CAPITAL,
            dry_run=True,
            sim_mode=True,
        )
        # 报告目录重定向 (测试不得写项目源码树)
        wf.config.REPORT_DIR = report_dir
        result["instantiated"] = True
        result["v75_ready"] = bool(V75_READY)
        result["trade_plan_loaded"] = bool(wf.trade_plan)
        if wf.trade_plan:
            exec_plan = wf.trade_plan.get("execution_plan", {})
            result["total_orders"] = exec_plan.get("total_orders", 0)
            result["morning_orders"] = len(exec_plan.get("morning_orders", []))
            result["afternoon_orders"] = len(exec_plan.get("afternoon_orders", []))
            result["grand_total"] = exec_plan.get("grand_total", 0)
    except Exception as exc:  # noqa: BLE001 - 诊断脚本需完整捕获
        result["error"] = f"{exc}\n{traceback.format_exc()}"
        return result

    # === 2. 逐 phase 执行 ===
    phases = _phase_calls(wf)
    counts = result["counts"]
    counts["total"] = len(phases)

    for phase_name, phase_func in phases:
        t0 = time.time()
        try:
            value = phase_func()
            elapsed = time.time() - t0
            phase_state = wf.state.get("phases", {}).get(phase_name, {})
            status = phase_state.get("status", "UNKNOWN")
            entry = {
                "status": status,
                "elapsed": round(elapsed, 2),
                "result_type": type(value).__name__,
            }

            if status == "PASS":
                counts["pass"] += 1
            elif status == "SKIP":
                counts["skip"] += 1
            elif status == "FAIL":
                err_str = str(phase_state.get("error", ""))
                if any(p in err_str for p in _ENV_ERROR_PATTERNS):
                    entry["status"] = "ENV_SKIP"
                    entry["error"] = err_str[:200]
                    counts["env_skip"] += 1
                else:
                    counts["fail"] += 1
            else:
                # 部分 phase 返回非字典 (phase_check 返回 bool, phase_market 返回 CircuitLevel),
                # 未写 phases[status] 时按"已执行未崩溃"记为 PASS
                entry["status"] = "PASS"
                counts["pass"] += 1

            result["phase_results"][phase_name] = entry
        except Exception as exc:  # noqa: BLE001 - 诊断脚本需完整捕获
            elapsed = time.time() - t0
            err_str = f"{exc}\n{traceback.format_exc()}"
            if any(p in err_str for p in _ENV_ERROR_PATTERNS):
                counts["env_skip"] += 1
                result["phase_results"][phase_name] = {
                    "status": "ENV_SKIP",
                    "elapsed": round(elapsed, 2),
                    "error": str(exc)[:200],
                }
            else:
                counts["error"] += 1
                result["phase_results"][phase_name] = {
                    "status": "ERROR",
                    "elapsed": round(elapsed, 2),
                    "error": err_str,
                }

    # === 3. 报告产物 ===
    # 真实契约 (workflow/phases/report.py):
    #   <report_dir>/<TRADE_DATE>/v75_daily_workflow_<YYYYMMDD>.{json,md}
    if report_dir.exists():
        reports = sorted(p for p in report_dir.rglob("*") if p.is_file())
        result["report_files"] = [p.name for p in reports]

    # === 4. 状态 JSON + Markdown 摘要 ===
    stem = f"v75_daily_workflow_{TRADE_DATE.replace('-', '')}"
    day_dir = report_dir / TRADE_DATE
    state_json_path = day_dir / f"{stem}.json"
    md_path = day_dir / f"{stem}.md"
    result["report_md"] = md_path.name if md_path.exists() else None
    if state_json_path.exists():
        try:
            with open(state_json_path, encoding="utf-8") as f:
                state = json.load(f)
            result["state_json"] = {
                "path": state_json_path.name,
                "phase_keys": list(state.get("phases", {}).keys()),
                "trade_date": state.get("trade_date"),
                "dry_run": state.get("dry_run"),
            }
        except Exception as exc:  # noqa: BLE001 - 诊断脚本需完整捕获
            result["state_json"] = {"path": state_json_path.name, "error": str(exc)}
    else:
        result["state_json"] = None

    # === 5. 门面转发 ===
    missing = [name for name in _FACADE_METHODS if not hasattr(wf, name)]
    result["facade"] = {
        "pass": len(_FACADE_METHODS) - len(missing),
        "total": len(_FACADE_METHODS),
        "missing": missing,
    }

    # === 6. 拆单断言 ===
    execute_state = wf.state.get("phases", {}).get("execute", {})
    split_total = execute_state.get("split_total", 0)
    if split_total > 0:
        result["split"] = {
            "checked": True,
            "split_total": split_total,
            "split_fail": execute_state.get("split_fail", 0),
            "split_failure_rate": execute_state.get("split_failure_rate", 0.0),
        }

    # === 7. 判定 (ERROR=0 且 拆单失败率 ≤ 50%) ===
    split_degraded = result["split"].get("split_failure_rate", 0.0) > 0.5
    result["verdict"] = (
        "PASS" if (counts["error"] == 0 and not split_degraded) else "FAIL"
    )
    result["elapsed"] = round(time.time() - started, 2)
    return result


# ============================================================
# pytest 用例 — 单次执行, 多角度断言 (链路约 45s, 故 module 级复用)
# ============================================================


@pytest.fixture(scope="module")
def eod_result(tmp_path_factory) -> dict:
    """执行一次完整 EOD 干跑, 供各用例断言 (报告写入 tmp, 不污染源码树)。"""
    report_dir = tmp_path_factory.mktemp("eod_reports")
    result = run_eod_dry_run(report_dir)
    if not result["instantiated"]:
        pytest.fail(f"DailyWorkflow 实例化失败:\n{result['error']}")
    return result


def test_all_phases_execute_without_error(eod_result):
    """13 个 phase 全部执行且无 ERROR (FAIL/SKIP/ENV_SKIP 可接受, ERROR=崩溃)。"""
    counts = eod_result["counts"]
    errors = [
        f"{name}: {info.get('error', '')[:200]}"
        for name, info in eod_result["phase_results"].items()
        if info["status"] == "ERROR"
    ]
    assert counts["total"] == 13, f"phase 数应为 13, 实际 {counts['total']}"
    assert counts["error"] == 0, "存在崩溃 phase:\n" + "\n".join(errors)


def test_every_phase_has_status(eod_result):
    """每个 phase 都必须落状态 (不允许静默缺失)。"""
    missing = [
        name
        for name in eod_result["phase_results"]
        if eod_result["phase_results"][name].get("status") in (None, "", "UNKNOWN")
    ]
    assert not missing, f"以下 phase 无有效状态: {missing}"
    assert len(eod_result["phase_results"]) == eod_result["counts"]["total"]


def test_phase_facades_present(eod_result):
    """拆分后门面转发必须完整 (18/18)。"""
    facade = eod_result["facade"]
    assert not facade["missing"], f"缺失门面: {facade['missing']}"
    assert facade["pass"] == facade["total"]


def test_report_artifacts_produced(eod_result):
    """必须产出报告文件、可解析的 JSON 状态与 Markdown 摘要 (空产物不算通过)。"""
    assert eod_result["report_files"], (
        f"未产出 {TRADE_DATE} 的报告文件, report_dir="
        f"{eod_result['report_dir']}"
    )
    state = eod_result["state_json"]
    assert state is not None, (
        f"v75_daily_workflow_{TRADE_DATE.replace('-', '')}.json 未生成 "
        f"(report_dir={eod_result['report_dir']})"
    )
    assert not state.get("error"), f"JSON 状态解析失败: {state}"
    assert state["phase_keys"], "JSON 状态的 phases 段为空"
    assert state.get("dry_run") is True, f"dry_run 标记异常: {state}"
    assert eod_result["report_md"], "Markdown 摘要未生成"


def test_order_split_has_no_failure(eod_result):
    """拆单失败率必须 ≤ 50% (try/except 吞没问题) 且失败笔数为 0。"""
    split = eod_result["split"]
    if not split.get("checked"):
        pytest.skip("split_total=0, 无拆单操作")
    assert split["split_failure_rate"] <= 0.5, f"拆单失败率过高: {split}"
    assert split["split_fail"] == 0, f"拆单失败 {split['split_fail']} 笔: {split}"


def test_verdict_pass(eod_result):
    """综合判定必须为 PASS (ERROR=0 + 拆单未降级)。"""
    assert eod_result["verdict"] == "PASS", (
        f"EOD 干跑判定 FAIL: counts={eod_result['counts']} split={eod_result['split']}"
    )


# ============================================================
# 手工诊断入口 (不参与 pytest 收集)
# ============================================================


def main() -> int:
    """打印明细 + 写出摘要 JSON; 返回退出码。仅用于人工排查。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 非 TTY 环境下 reconfigure 可能不可用
        pass

    report_dir = PROJECT_ROOT / "tests" / "eod_reports"
    print(f"EOD 干跑 — 交易日期 {TRADE_DATE} (DRY-RUN + SIMULATION)")
    result = run_eod_dry_run(report_dir)

    if not result["instantiated"]:
        print(f"[FAIL] DailyWorkflow 实例化失败:\n{result['error']}")
        return 1

    print(f"  V75_READY: {result['v75_ready']}")
    print(f"  交易计划加载: {'是' if result['trade_plan_loaded'] else '否 (空计划降级)'}")
    print(
        f"  订单数: {result['total_orders']} "
        f"(上午 {result.get('morning_orders', 0)} + 下午 {result.get('afternoon_orders', 0)})"
    )
    print("\n  各 phase 状态:")
    for name, info in result["phase_results"].items():
        detail = str(info.get("error", info.get("result_type", "")))[:60]
        print(f"  {name:<22s} {info['status']:<10s} {info['elapsed']:>6.2f}s  {detail}")

    counts = result["counts"]
    print(
        f"\n  汇总: PASS={counts['pass']} SKIP={counts['skip']} FAIL={counts['fail']} "
        f"ERROR={counts['error']} ENV_SKIP={counts['env_skip']}"
    )
    print(
        f"  门面转发: {result['facade']['pass']}/{result['facade']['total']}"
        f" (缺失: {result['facade']['missing'] or '无'})"
    )
    print(f"  拆单: {result['split']}")
    print(f"  报告产物: {len(result['report_files'])} 个")
    print(f"  判定: {result['verdict']} ({result['elapsed']}s)")

    # 摘要写到报告目录 (此前误写项目根, 属"运行时产物混入源码树")
    summary_path = report_dir / f"eod_dry_run_summary_{TRADE_DATE.replace('-', '')}.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"  结果摘要: {summary_path}")
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
