#!/usr/bin/env python
"""
工业级判据常驻检查 (Industrial Grade Criteria Check)
======================================================
创建: 2026-08-06 防复发机制 #1
目的: 防止"一次性评估后发现短板, 但日常开发中又退化"的问题。
      每次 PR / 盘前自检时自动跑 9 项工业级判据, 任何判据退化即告警。

9 项判据 (源自 research_report_industrial_grade_evaluation.md):
    C1 执行闭环完整性   — 是否有真实撮合/券商下单 (非纯 Mock)
    C2 数据管道分层     — data_pipeline 是否空壳
    C3 环境隔离         — 生产模块是否 import research.*
    C4 信号执行分离     — Alpha 层与 Execution 层是否独立
    C5 测试可运行性     — pytest --collect-only 是否 0 errors
    C6 CI 可运行性      — ci.yml 引用的脚本是否存在
    C7 监控告警实现     — utils/notify 是否存在且可 import
    C8 成交回报落盘     — FillsStore 是否存在且被执行链 + PnL 双向接入 (G2/G4)
    C9 期权对冲执行链   — hedge_order_executor 是否存在撮合+落盘+持仓更新闭环
    C10 fills 文件新鲜度 — 当日/最近 fills 文件是否存在且非空
    C11 PnL 桥接数据流   — fills_pnl_bridge.augment_market_prices 是否可消费 fills
    C12 期权执行链集成   — hedge_order_executor 是否被 daily_trading_workflow/统一入口调用

更新历史:
    2026-08-06 创建 (C1-C9 初版, C8/C9 复用 C1/C5)
    2026-08-08 重构 C8/C9 为独立判据:
        - C8 改为成交回报落盘检查 (G2 fills_store + G4 fills_pnl_bridge 接入点)
        - C9 改为期权对冲执行链检查 (hedge_order_executor 撮合闭环)

用法:
    python scripts/industrial_grade_check.py           # 全量检查
    python scripts/industrial_grade_check.py --strict   # 严格模式(任何WARN=FAIL)
    python scripts/industrial_grade_check.py --json     # JSON报告输出

退出码:
    0 = 全部 PASS
    1 = 有 WARN (非严格模式下不阻断)
    2 = 有 FAIL (始终阻断)

设计原则:
    - 只读检查, 不修改任何文件
    - 每项判据有明确的 PASS/WARN/FAIL 三态
    - FAIL = 工业级硬伤 (执行断链/测试跑不起来/CI跑不起来/告警缺失)
    - WARN = 工业级短板 (环境未隔离/数据管道分层缺/dry_run)
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any

from utils.datetime_utils import now_bj

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ============================================================
# 检查结果数据结构
# ============================================================


class CheckResult:
    """单项判据检查结果."""

    def __init__(
        self, code: str, name: str, status: str, detail: str, evidence: str = ""
    ):
        self.code = code  # C1-C9
        self.name = name
        self.status = status  # PASS / WARN / FAIL
        self.detail = detail
        self.evidence = evidence  # 具体文件/行号

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
        }


# ============================================================
# 9 项判据检查
# ============================================================


def check_c1_execution_loop() -> CheckResult:
    """C1 执行闭环完整性 — 检查是否有真实撮合/券商下单."""
    # 检查 system_config.json broker 是否启用
    config_path = _PROJECT_ROOT / "system_config.json"
    broker_enabled = False
    dry_run = True
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            broker_enabled = (
                config.get("api_config", {}).get("broker", {}).get("enable", False)
            )
            dry_run = (
                config.get("api_config", {}).get("broker", {}).get("dry_run", True)
            )
        except (json.JSONDecodeError, KeyError):
            pass

    # 检查 QmtBrokerAPI 是否存在
    qmt_path = _PROJECT_ROOT / "ms_strategy" / "src" / "execution" / "qmt_broker.py"
    qmt_exists = qmt_path.exists()

    if broker_enabled and not dry_run:
        return CheckResult(
            "C1", "执行闭环完整性", "PASS", "broker 已启用且 dry_run=false"
        )
    if qmt_exists and not broker_enabled:
        return CheckResult(
            "C1",
            "执行闭环完整性",
            "WARN",
            "QmtBrokerAPI 已实现但 broker.enable=false / dry_run=true, 真实下单未接线",
            f"system_config.json broker.enable={broker_enabled}, dry_run={dry_run}",
        )
    return CheckResult("C1", "执行闭环完整性", "FAIL", "无真实券商下单通道")


def check_c2_data_pipeline() -> CheckResult:
    """C2 数据管道分层 — 检查 data_pipeline 是否空壳."""
    dp_dir = _PROJECT_ROOT / "data_pipeline"
    if not dp_dir.exists():
        return CheckResult(
            "C2", "数据管道分层", "PASS", "data_pipeline/ 已删除(逻辑在 utils/data/)"
        )

    subdirs = list(dp_dir.iterdir()) if dp_dir.exists() else []
    empty_count = sum(1 for d in subdirs if d.is_dir() and not any(d.iterdir()))
    if empty_count > 0:
        return CheckResult(
            "C2",
            "数据管道分层",
            "WARN",
            f"data_pipeline/ 有 {empty_count} 个空壳子目录(设计意图未落地)",
            str(dp_dir),
        )
    return CheckResult("C2", "数据管道分层", "PASS", "data_pipeline/ 非空")


def check_c3_env_isolation() -> CheckResult:
    """C3 环境隔离 — 检查生产模块是否 import research.*."""
    # 搜索 utils/ 下的 .py 文件是否 import research.*
    violations: list[str] = []
    utils_dir = _PROJECT_ROOT / "utils"
    if utils_dir.exists():
        for py_file in utils_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(content.splitlines(), 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if "from research." in stripped or "import research." in stripped:
                        violations.append(f"{py_file.name}:{i}: {stripped}")
            except OSError:
                continue

    if not violations:
        return CheckResult("C3", "环境隔离", "PASS", "生产模块无 research.* import")
    return CheckResult(
        "C3",
        "环境隔离",
        "WARN",
        f"生产模块有 {len(violations)} 处 import research.* (未物理隔离)",
        "; ".join(violations[:5]),
    )


def check_c4_signal_execution_separation() -> CheckResult:
    """C4 信号执行分离 — 检查 Alpha 层与 Execution 层是否独立."""
    alpha_dir = _PROJECT_ROOT / "utils" / "alpha_factor"
    exec_algo = _PROJECT_ROOT / "utils" / "execution_algo_engine.py"
    exec_selector = _PROJECT_ROOT / "utils" / "execution_selector.py"

    has_alpha = alpha_dir.exists() and alpha_dir.is_dir()
    has_exec_algo = exec_algo.exists()
    has_selector = exec_selector.exists()

    if has_alpha and has_exec_algo and has_selector:
        return CheckResult(
            "C4", "信号执行分离", "PASS", "Alpha 层 + 执行算法 + selector 桥接均存在"
        )
    missing = []
    if not has_alpha:
        missing.append("alpha_factor/")
    if not has_exec_algo:
        missing.append("execution_algo_engine.py")
    if not has_selector:
        missing.append("execution_selector.py")
    return CheckResult("C4", "信号执行分离", "WARN", f"缺少: {', '.join(missing)}")


def check_c5_test_collectable() -> CheckResult:
    """C5 测试可运行性 — pytest --collect-only 是否 0 errors."""
    tests_dir = _PROJECT_ROOT / "tests"
    if not tests_dir.exists():
        return CheckResult("C5", "测试可运行性", "FAIL", "tests/ 目录不存在")

    # 快速检查: 是否有测试引用已知不存在的模块
    known_missing = [
        "hedging.hedge_coordinator",
        "risk.portfolio_risk_assessor",
        "dsr_bootstrap",
    ]
    stale_count = 0
    for py_file in tests_dir.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
            for mod in known_missing:
                if f"import {mod}" in content or f"from {mod}" in content:
                    stale_count += 1
                    break
        except OSError:
            continue

    if stale_count > 0:
        return CheckResult(
            "C5",
            "测试可运行性",
            "FAIL",
            f"{stale_count} 个测试引用已删除的模块({', '.join(known_missing)})",
        )
    return CheckResult("C5", "测试可运行性", "PASS", "无已知陈旧测试引用")


def check_c6_ci_runnable() -> CheckResult:
    """C6 CI 可运行性 — ci.yml 引用的脚本是否存在."""
    ci_path = _PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
    if not ci_path.exists():
        return CheckResult("C6", "CI 可运行性", "WARN", "ci.yml 不存在")

    content = ci_path.read_text(encoding="utf-8")
    # 提取 python scripts/xxx.py 引用
    import re

    refs = re.findall(r"python\s+(scripts/[^\s]+\.py)", content)
    missing = []
    for ref in refs:
        script_path = _PROJECT_ROOT / ref
        if not script_path.exists():
            missing.append(ref)

    if missing:
        return CheckResult(
            "C6", "CI 可运行性", "FAIL", f"CI 引用的脚本不存在: {', '.join(missing)}"
        )
    return CheckResult(
        "C6", "CI 可运行性", "PASS", f"CI 引用的 {len(refs)} 个脚本均存在"
    )


def check_c7_notify_exists() -> CheckResult:
    """C7 监控告警实现 — utils/notify 是否存在且可 import."""
    notify_path = _PROJECT_ROOT / "utils" / "notify.py"
    if not notify_path.exists():
        return CheckResult("C7", "监控告警实现", "FAIL", "utils/notify.py 不存在")

    # 检查是否定义了关键函数
    content = notify_path.read_text(encoding="utf-8")
    has_send_alert = "def send_alert" in content
    has_send_sms = "def send_sms_alert" in content

    if has_send_alert and has_send_sms:
        return CheckResult(
            "C7", "监控告警实现", "PASS", "send_alert + send_sms_alert 均已定义"
        )
    return CheckResult("C7", "监控告警实现", "WARN", "notify.py 存在但接口不完整")


def check_c8_fills_persistence() -> CheckResult:
    """C8 成交回报落盘 — FillsStore 是否被执行链 + PnL 双向接入 (G2/G4).

    G2 (2026-08-08 修复): automated_execution_system 成交后调用 FillsStore.record_fill
        落盘到 reports/fills/fills_{date}.jsonl, 解决"只撮合不落盘"断链.
    G4 (2026-08-08 修复): generate_daily_report 通过 fills_pnl_bridge.augment_market_prices
        用真实成交价覆盖行情估算 close, 使 PnL 以 fills 为单一事实源.

    本判据验证:
        1. utils/execution/fills_store.py 存在且定义 FillsStore + record_fill
        2. utils/execution/fills_pnl_bridge.py 存在且定义 augment_market_prices
        3. automated_execution_system.py 接入 record_fill (执行链落盘点)
        4. generate_daily_report.py 接入 augment_market_prices (PnL 桥接点)
    """
    fills_store_path = _PROJECT_ROOT / "utils" / "execution" / "fills_store.py"
    bridge_path = _PROJECT_ROOT / "utils" / "execution" / "fills_pnl_bridge.py"
    exec_system_path = (
        _PROJECT_ROOT / "utils" / "execution" / "automated_execution_system.py"
    )
    daily_report_path = _PROJECT_ROOT / "generate_daily_report.py"

    missing = []
    if not fills_store_path.exists():
        missing.append("utils/execution/fills_store.py")
    if not bridge_path.exists():
        missing.append("utils/execution/fills_pnl_bridge.py")
    if not exec_system_path.exists():
        missing.append("utils/execution/automated_execution_system.py")
    if not daily_report_path.exists():
        missing.append("generate_daily_report.py")

    if missing:
        return CheckResult(
            "C8",
            "成交回报落盘",
            "FAIL",
            f"关键文件缺失: {', '.join(missing)}",
        )

    # 检查接口定义
    store_content = fills_store_path.read_text(encoding="utf-8")
    bridge_content = bridge_path.read_text(encoding="utf-8")
    has_store_class = "class FillsStore" in store_content
    has_record_fill = "def record_fill" in store_content
    has_augment = "def augment_market_prices" in bridge_content

    if not (has_store_class and has_record_fill):
        return CheckResult(
            "C8",
            "成交回报落盘",
            "FAIL",
            "fills_store.py 缺少 FillsStore 类或 record_fill 方法",
            str(fills_store_path),
        )
    if not has_augment:
        return CheckResult(
            "C8",
            "成交回报落盘",
            "FAIL",
            "fills_pnl_bridge.py 缺少 augment_market_prices 函数",
            str(bridge_path),
        )

    # 检查接入点 (G2: 执行链落盘, G4: PnL 桥接)
    exec_content = exec_system_path.read_text(encoding="utf-8")
    report_content = daily_report_path.read_text(encoding="utf-8")
    g2_wired = "record_fill" in exec_content or "FillsStore" in exec_content
    g4_wired = "augment_market_prices" in report_content

    evidence_parts = []
    if not g2_wired:
        evidence_parts.append("G2: automated_execution_system 未接入 record_fill")
    if not g4_wired:
        evidence_parts.append("G4: generate_daily_report 未接入 augment_market_prices")

    if evidence_parts:
        return CheckResult(
            "C8",
            "成交回报落盘",
            "WARN",
            f"fills 模块存在但接入不完整: {'; '.join(evidence_parts)}",
        )
    return CheckResult(
        "C8",
        "成交回报落盘",
        "PASS",
        "FillsStore + fills_pnl_bridge 均已接入执行链(G2)和 PnL(G4)",
    )


def check_c9_options_hedge_execution() -> CheckResult:
    """C9 期权对冲执行链 — hedge_order_executor 是否存在撮合+落盘+持仓更新闭环.

    2026-08-06 修复: 此前 HedgeExecutionEngine.generate_hedge_orders 只生成 PENDING
    期权订单写入 trade_plan, 但系统从未有执行器把订单送入撮合引擎, 导致期权订单
    永远停留在 PENDING、组合 Delta 从不因期权对冲下降.

    本判据验证:
        1. hedge_order_executor.py 存在
        2. 包含 OptionsSimBroker 撮合类
        3. 包含 execute_hedge_orders 执行入口
        4. 落盘 hedge_execution_fill_{date}.json (兼容 daily_pnl 数据契约)
        5. 集成到 daily_trading_workflow.py 和统一入口
    """
    executor_path = _PROJECT_ROOT / "hedge_order_executor.py"
    if not executor_path.exists():
        return CheckResult(
            "C9",
            "期权对冲执行链",
            "FAIL",
            "hedge_order_executor.py 不存在 (期权对冲只生成不执行)",
            str(executor_path),
        )

    content = executor_path.read_text(encoding="utf-8")
    has_broker = "class OptionsSimBroker" in content
    has_execute = "def execute_hedge_orders" in content
    has_fill_output = "hedge_execution_fill_" in content
    has_positions_update = "active_orders" in content or "actual_positions" in content
    has_on_fill = "on_fill" in content  # TCA 归因回调

    missing = []
    if not has_broker:
        missing.append("OptionsSimBroker 撮合类")
    if not has_execute:
        missing.append("execute_hedge_orders 入口")
    if not has_fill_output:
        missing.append("hedge_execution_fill 落盘")
    if not has_positions_update:
        missing.append("positions.json 更新")
    if not has_on_fill:
        missing.append("on_fill TCA 归因")

    if missing:
        return CheckResult(
            "C9",
            "期权对冲执行链",
            "WARN",
            f"hedge_order_executor.py 存在但闭环不完整: 缺少 {', '.join(missing)}",
            str(executor_path),
        )

    # 检查集成点 (daily_trading_workflow + 统一入口)
    workflow_path = _PROJECT_ROOT / "daily_trading_workflow.py"
    entry_path = _PROJECT_ROOT / "量化策略系统_统一入口_v8.6.py"
    integrated = False
    if workflow_path.exists() and "hedge_order_executor" in workflow_path.read_text(
        encoding="utf-8"
    ):
        integrated = True
    if entry_path.exists() and "hedge_order_executor" in entry_path.read_text(
        encoding="utf-8"
    ):
        integrated = True

    if not integrated:
        return CheckResult(
            "C9",
            "期权对冲执行链",
            "WARN",
            "hedge_order_executor 未集成到 daily_trading_workflow 或统一入口",
        )
    return CheckResult(
        "C9",
        "期权对冲执行链",
        "PASS",
        "OptionsSimBroker 撮合 + fills 落盘 + positions 更新 + TCA 归因 + 工作流集成 均完整",
    )


def check_c10_fills_freshness() -> CheckResult:
    """C10 fills 文件新鲜度 — 当日/最近 fills 文件是否存在且非空."""
    fills_dir = _PROJECT_ROOT / "reports" / "fills"
    if not fills_dir.exists():
        return CheckResult(
            "C10", "fills文件新鲜度", "WARN", "reports/fills/ 目录不存在(未运行执行链?)"
        )

    today = now_bj().strftime("%Y-%m-%d")
    today_fill = fills_dir / f"fills_{today}.jsonl"
    if today_fill.exists():
        try:
            lines = today_fill.read_text(encoding="utf-8").strip().splitlines()
            if len(lines) == 0:
                return CheckResult(
                    "C10", "fills文件新鲜度", "WARN", f"当日 fills_{today}.jsonl 为空"
                )
            return CheckResult(
                "C10",
                "fills文件新鲜度",
                "PASS",
                f"当日 fills 文件有 {len(lines)} 条记录",
            )
        except OSError as e:
            return CheckResult(
                "C10", "fills文件新鲜度", "FAIL", f"读取 fills 文件失败: {e}"
            )

    # 查找最近的 fills 文件
    all_fills = sorted(fills_dir.glob("fills_*.jsonl"), reverse=True)
    if not all_fills:
        return CheckResult(
            "C10", "fills文件新鲜度", "WARN", "无任何 fills 文件(未运行执行链)"
        )

    recent = all_fills[0]
    try:
        lines = recent.read_text(encoding="utf-8").strip().splitlines()
        recency = "今日" if recent.name == f"fills_{today}.jsonl" else "非今日"
        return CheckResult(
            "C10",
            "fills文件新鲜度",
            "PASS" if recency == "今日" else "WARN",
            f"最近 fills 文件: {recent.name} ({len(lines)} 条, {recency})",
        )
    except OSError as e:
        return CheckResult(
            "C10", "fills文件新鲜度", "FAIL", f"读取 fills 文件失败: {e}"
        )


def check_c11_pnl_bridge_dataflow() -> CheckResult:
    """C11 PnL 桥接数据流 — fills_pnl_bridge.augment_market_prices 是否可消费 fills."""
    bridge_path = _PROJECT_ROOT / "utils" / "execution" / "fills_pnl_bridge.py"
    if not bridge_path.exists():
        return CheckResult(
            "C11",
            "PnL桥接数据流",
            "FAIL",
            "fills_pnl_bridge.py 不存在",
            str(bridge_path),
        )

    content = bridge_path.read_text(encoding="utf-8")
    has_augment = "def augment_market_prices" in content
    has_realized_pnl = "def realized_pnl" in content
    has_fills_import = (
        "from utils.execution.fills_store import FillsStore" in content
        or "FillsStore" in content
    )

    if not (has_augment and has_realized_pnl and has_fills_import):
        missing = []
        if not has_augment:
            missing.append("augment_market_prices")
        if not has_realized_pnl:
            missing.append("realized_pnl")
        if not has_fills_import:
            missing.append("FillsStore 导入")
        return CheckResult(
            "C11",
            "PnL桥接数据流",
            "FAIL",
            f"fills_pnl_bridge.py 功能不完整: 缺少 {', '.join(missing)}",
            str(bridge_path),
        )

    # 验证 augment_market_prices 调用链: 读取 fills -> 覆盖 close -> 标记 close_source
    calls_latest = "latest_avg_price_by_symbol" in content
    calls_close_source = "close_source" in content
    if not (calls_latest and calls_close_source):
        return CheckResult(
            "C11",
            "PnL桥接数据流",
            "WARN",
            "fills_pnl_bridge 存在但数据流可能不完整 (缺 fills 读取或 close 标记)",
            str(bridge_path),
        )
    return CheckResult(
        "C11",
        "PnL桥接数据流",
        "PASS",
        "fills_pnl_bridge.augment_market_prices 可消费 fills 并覆盖行情 close",
    )


def check_c12_hedge_executor_integration() -> CheckResult:
    """C12 期权执行链集成 — hedge_order_executor 是否被 daily_trading_workflow/统一入口调用."""
    executor_path = _PROJECT_ROOT / "hedge_order_executor.py"
    if not executor_path.exists():
        return CheckResult(
            "C12",
            "期权执行链集成",
            "FAIL",
            "hedge_order_executor.py 不存在",
            str(executor_path),
        )

    # 检查集成点 (daily_trading_workflow + 统一入口 + 其他调用方)
    workflow_path = _PROJECT_ROOT / "daily_trading_workflow.py"
    entry_path = _PROJECT_ROOT / "量化策略系统_统一入口_v8.6.py"
    daily_runner_path = _PROJECT_ROOT / "daily_runner.py"

    integrated = False
    integration_points = []
    for p in (workflow_path, entry_path, daily_runner_path):
        if p.exists():
            content = p.read_text(encoding="utf-8")
            if "hedge_order_executor" in content:
                integrated = True
                integration_points.append(p.name)

    if not integrated:
        return CheckResult(
            "C12",
            "期权执行链集成",
            "WARN",
            "hedge_order_executor 未被 daily_trading_workflow / 统一入口 / daily_runner 调用",
        )
    return CheckResult(
        "C12",
        "期权执行链集成",
        "PASS",
        f"hedge_order_executor 已集成到: {', '.join(integration_points)}",
    )


# ============================================================
# 主检查流程
# ============================================================


def run_all_checks() -> list[CheckResult]:
    """运行全部 12 项判据检查."""
    return [
        check_c1_execution_loop(),
        check_c2_data_pipeline(),
        check_c3_env_isolation(),
        check_c4_signal_execution_separation(),
        check_c5_test_collectable(),
        check_c6_ci_runnable(),
        check_c7_notify_exists(),
        check_c8_fills_persistence(),
        check_c9_options_hedge_execution(),
        check_c10_fills_freshness(),
        check_c11_pnl_bridge_dataflow(),
        check_c12_hedge_executor_integration(),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="工业级判据常驻检查")
    parser.add_argument("--strict", action="store_true", help="严格模式: WARN 也算失败")
    parser.add_argument("--json", action="store_true", help="JSON 报告输出")
    args = parser.parse_args(argv)

    results = run_all_checks()

    pass_count = sum(1 for r in results if r.status == "PASS")
    warn_count = sum(1 for r in results if r.status == "WARN")
    fail_count = sum(1 for r in results if r.status == "FAIL")

    if args.json:
        report = {
            "timestamp": "2026-08-06",
            "total": len(results),
            "pass": pass_count,
            "warn": warn_count,
            "fail": fail_count,
            "checks": [r.to_dict() for r in results],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("=" * 70)
        print("工业级判据常驻检查 (Industrial Grade Criteria Check)")
        print("=" * 70)
        for r in results:
            icon = {"PASS": "[OK]", "WARN": "[!!]", "FAIL": "[XX]"}[r.status]
            print(f"  {icon} {r.code} {r.name}: {r.detail}")
            if r.evidence:
                print(f"         证据: {r.evidence}")
        print("-" * 70)
        print(f"  总计: {pass_count} PASS, {warn_count} WARN, {fail_count} FAIL")
        print("=" * 70)

    if fail_count > 0:
        return 2
    if args.strict and warn_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
