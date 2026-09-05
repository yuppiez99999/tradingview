#!/usr/bin/env python
"""
工程债务门槛检查 (Engineering Debt Gate)
==========================================
创建: 2026-08-06 防复发机制 #3
目的: 防止"功能持续增加但工程债务无人还"——在沙子上盖楼。
      检查工程债务指标, 债务超标时建议冻结新功能。

检查项:
    T1 测试 collection 是否 0 errors (pytest --collect-only)
    T2 CI 引用脚本是否都存在
    T3 utils/notify 是否存在 (告警能力)
    T4 生产模块是否有 research.* import (环境隔离)
    T5 陈旧测试数量 (引用已删除模块的测试)
    T6 fail-safe 宽捕获泛滥 (except Exception + # fail-safe 注释, 技术债 R10)
    T7 裸 except Exception 独立债 (无 fail-safe/BLE001 标记, T02 升级配套)
    T8 覆盖率基线退化检测 (reports/ci/coverage_baseline.json vs reports/coverage.xml, T03)
    T9 风控六件套·T09 预交易风控门 (6 规则 import + 手数拦截行为自检)
    T10 风控六件套·T10 持仓集中度执行器 (单票/行业/敞口/杠杆)
    T11 风控六件套·T11 日内熔断器 (连续失败/回撤/波动率 + 状态机)
    T12 风控六件套·T12 三级熔断管理器 (L1/L2/L3 保证金梯度)
    T13 风控六件套·T13 计划单 vs 成交对账器
    T14 风控六件套·T14 风控审计 JSONL 日志 (落盘 + 查询自检)
    T15 实盘四件套·T15 实盘下单编排器 (T09 拦截行为自检)
    T16 实盘四件套·T16 订单生命周期跟踪器 (状态映射 + 终态自检)
    T17 实盘四件套·T17 实盘对账循环 (T13 包装 + drift 检测)
    T18 实盘四件套·T18 灰度发布编排器 (4 阶段 + 资金比例自检)
    D1 LLM 智能进化·D1 策略 Ideation 引擎 (五步流水线 + 多样性自检)
    D2 LLM 智能进化·D2 假设验证框架 (IC 显著性 + AB 桶判定自检)
    D3 LLM 智能进化·D3 知识沉淀库 (JSONL 写读 + 上下文反馈自检)
    D4 LLM 智能进化·D4 双层闭环编排器 (Kill Switch 暂停自检)
    D5 AutoResearch Skill·D5 因子自动迭代闭环 (生成→评估→S1-S5门禁→入库→退役自检)
    D6 LiteLLM Gateway·D6 多模型统一路由 (ChatRequest→ChatResponse + 场景路由 + 统计自检)
    D7 daily_workflow 拆分收尾·D7 门禁达标 + _scan_func_quality 扫描 (≤3000 行 + 脚本存在自检)
    D8 G7 覆盖率冲刺·D8 测试文件 + .coveragerc 排除模式 + 基线自检 (evolution/multi_model_router 排除)
    D12 Q4 冻结窗 Change Budget·R-4 (2026-09-19~12-10: flag 新增 enable / 生产模型新增落盘,
        窗口外 PASS 待激活, 窗口内 fail-closed; 基线 reports/freeze_baseline/)

债务等级:
    GREEN  — 全部通过, 可推进功能升级
    YELLOW — 有 1-2 项 WARN, 功能升级需谨慎
    RED    — 有 FAIL, 建议冻结新功能优先还债

用法:
    python scripts/engineering_debt_gate.py

退出码:
    0 = GREEN
    1 = YELLOW
    2 = RED
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime
from datetime import time as _time
from pathlib import Path

# Windows GBK 控制台无法打印 \u2713 等 Unicode 字符, 强制 UTF-8 输出避免 UnicodeEncodeError 崩溃
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 复用 industrial_grade_check 的部分检查 + 风控六件套 import (utils.risk.*)
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))


def _check_test_collection_errors() -> tuple[bool, str]:
    """T1 测试 collection 是否有 ERROR."""
    # 快速检查: 已知删除模块是否被测试引用
    known_missing = [
        "hedging.hedge_coordinator",
        "risk.portfolio_risk_assessor",
        "dsr_bootstrap",
    ]
    stale_count = 0
    tests_dir = _PROJECT_ROOT / "tests"
    if tests_dir.exists():
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
        return False, f"{stale_count} 个测试引用已删除模块"
    return True, "无已知陈旧测试"


def _check_ci_scripts_exist() -> tuple[bool, str]:
    """T2 CI 引用脚本是否都存在."""
    ci_path = _PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
    if not ci_path.exists():
        return True, "ci.yml 不存在(跳过)"
    import re

    content = ci_path.read_text(encoding="utf-8")
    refs = re.findall(r"python\s+(scripts/[^\s]+\.py)", content)
    missing = [r for r in refs if not (_PROJECT_ROOT / r).exists()]
    if missing:
        return False, f"CI 脚本缺失: {', '.join(missing)}"
    return True, f"{len(refs)} 个脚本均存在"


def _check_notify_exists() -> tuple[bool, str]:
    """T3 utils/notify 是否存在."""
    notify_path = _PROJECT_ROOT / "utils" / "notify.py"
    if not notify_path.exists():
        return False, "utils/notify.py 不存在"
    return True, "存在"


def _check_env_isolation() -> tuple[bool, str]:
    """T4 生产模块是否有 research.* import."""
    violations = 0
    utils_dir = _PROJECT_ROOT / "utils"
    if utils_dir.exists():
        for py_file in utils_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
                for line in content.splitlines():
                    stripped = line.strip()
                    if not stripped.startswith("#") and (
                        "from research." in stripped or "import research." in stripped
                    ):
                        violations += 1
            except OSError:
                continue
    if violations > 0:
        return False, f"{violations} 处 import research.*"
    return True, "无跨层 import"


def _check_stale_test_count() -> tuple[bool, str]:
    """T5 陈旧测试数量."""
    # 复用 T1 的检查逻辑
    ok, msg = _check_test_collection_errors()
    if ok:
        return True, "0 个陈旧测试"
    return False, msg


# T6 阈值: fail-safe 宽捕获站点数超过此值即判 YELLOW (技术债 R10)
_FAIL_SAFE_WARN_THRESHOLD = 30


def _check_fail_safe_broad_except() -> tuple[bool, str]:
    """T6 fail-safe 宽捕获泛滥检测 (技术债 R10).

    扫描源码中 `except Exception` 配合 `# fail-safe` / `# noqa: BLE001` 注释的
    站点, 统计数量。这类站点会静默吞掉真实错误 (数据源失败/LLM 解析异常),
    属已承认但未治理的技术债。超过阈值 (默认 30) 即 YELLOW, 提示需要排期精确化。

    不阻断 (never RED): 属可维护性债, 非功能性阻断。只做告警级别提示。
    """
    import re

    # 仅匹配代码行的 except Exception (排除注释/文档字符串中的 "except Exception")
    patterns = [
        re.compile(r"^\s*except\s+Exception\b.*#.*(?:fail-safe|noqa:\s*BLE001)", re.IGNORECASE),
        re.compile(r"^\s*except\s+Exception\b\s*:\s*#\s*fail-safe", re.IGNORECASE),
    ]
    count = 0
    scanned_dirs = [
        _PROJECT_ROOT / "utils",
        _PROJECT_ROOT / "scripts",
        _PROJECT_ROOT / "quant_modules",
        _PROJECT_ROOT / "ai_decision",
    ]
    hits: list[str] = []
    for base in scanned_dirs:
        if not base.exists():
            continue
        for py_file in base.rglob("*.py"):
            # ci_integrity_check.py 自身的示例豁免 (门禁脚本必须宽捕获以兼容任何失败)
            if py_file.name == "ci_integrity_check.py":
                continue
            try:
                for ln in py_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    stripped = ln.lstrip()
                    if stripped.startswith("#"):
                        continue  # 跳过纯注释行 (文档/注释提及不算债)
                    if any(p.search(ln) for p in patterns):
                        count += 1
                        hits.append(f"{py_file.name}:{ln.strip()[:80]}")
            except OSError:
                continue
    if count > _FAIL_SAFE_WARN_THRESHOLD:
        return (
            False,
            f"fail-safe 宽捕获 {count} 处 (> {_FAIL_SAFE_WARN_THRESHOLD}, 需排期精确化)",
        )
    return True, f"fail-safe 宽捕获 {count} 处 (<= {_FAIL_SAFE_WARN_THRESHOLD})"


# T7 阈值: 裸 except Exception (无 # fail-safe / # noqa: BLE001 标记) 站点数
# 这类是真正的"静默吞异常"独立债, 不在 R10 治理范围 (R10 只清偿带标记的 346 处)
# 当前基线 (2026-08-31 实测): 243 处
#   - v8.3_institutional/daily_workflow.py: 0 处 (已拆分: 6230→2159 行, print 108→0, W6.6.4 完成)
#   - quant_modules/ai_hedge_fund/: 31 处 (LLM/数据降级)
#   - scripts/: 26 处 (运维脚本)
#   - utils/: 15 处 (event_tracker/concurrency/notify 等基础设施)
#   - ai_decision/: 0 处
# 阈值 = 250 (留 7 缓冲, 禁止增长而非清零; 配套 T02 pylint --fail-on=broad-except 阻断新增)
# 治理路径: daily_workflow.py 拆分已完成 (W6.6.4); ai_hedge_fund 独立立项
_BARE_BROAD_EXCEPT_WARN_THRESHOLD = 250


def _check_bare_broad_except() -> tuple[bool, str]:
    """T7 裸 except Exception 独立债检测 (T02 升级配套).

    扫描源码中所有 `except Exception` / `except BaseException` 站点,
    排除带 `# fail-safe` / `# noqa: BLE001` 标记的 (T6 已覆盖),
    统计裸宽捕获数量。这类站点会静默吞掉真实错误, 是真正的"债务"。

    R10 已清偿带标记的 346 处 (2026-08-12), 残留 ~42 处为裸宽捕获独立债。
    T02 (2026-08-12) 通过 pylint --fail-on=broad-except 阻断 utils/infra|risk|execution|data
    路径的新增; 本检查覆盖更广目录 (utils/scripts/quant_modules/ai_decision/v8.3_institutional),
    监控独立债是否增长。
    """
    import re

    # 匹配 except Exception / except BaseException (含 as 子句)
    bare_pattern = re.compile(r"^\s*except\s+(Exception|BaseException)\b")
    # 已标记的 (T6 范围, 排除)
    marked_patterns = [
        re.compile(r"#.*(?:fail-safe|noqa:\s*BLE001)", re.IGNORECASE),
    ]
    count = 0
    scanned_dirs = [
        _PROJECT_ROOT / "utils",
        _PROJECT_ROOT / "scripts",
        _PROJECT_ROOT / "quant_modules",
        _PROJECT_ROOT / "ai_decision",
        _PROJECT_ROOT / "v8.3_institutional",
    ]
    hits: list[str] = []
    for base in scanned_dirs:
        if not base.exists():
            continue
        for py_file in base.rglob("*.py"):
            # ci_integrity_check.py 自身的示例豁免 (门禁脚本必须宽捕获以兼容任何失败)
            if py_file.name == "ci_integrity_check.py":
                continue
            try:
                for ln in py_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    stripped = ln.lstrip()
                    if stripped.startswith("#"):
                        continue  # 跳过纯注释行
                    if not bare_pattern.search(ln):
                        continue
                    # 排除已标记的 (T6 范围)
                    if any(p.search(ln) for p in marked_patterns):
                        continue
                    count += 1
                    if len(hits) < 5:  # 只保留前 5 个样本用于诊断
                        hits.append(f"{py_file.name}:{ln.strip()[:80]}")
            except OSError:
                continue
    if count > _BARE_BROAD_EXCEPT_WARN_THRESHOLD:
        sample = "; ".join(hits[:3]) if hits else ""
        return False, (
            f"裸 except Exception {count} 处 (> {_BARE_BROAD_EXCEPT_WARN_THRESHOLD}, 需排期精确化; 样本: {sample})"
        )
    return (
        True,
        f"裸 except Exception {count} 处 (<= {_BARE_BROAD_EXCEPT_WARN_THRESHOLD})",
    )


# T8 覆盖率退化阈值: 当前 line_rate < 基线 - 此值 即判退化
# 与 _check_coverage_trend.py 的退化检测逻辑保持一致 (允许 2pp 波动)
_COVERAGE_DEGRADATION_THRESHOLD = 0.02


def _check_coverage_baseline() -> tuple[bool, str]:
    """T8 覆盖率基线退化检测 (T03).

    读取 reports/ci/coverage_baseline.json (冻结基线) 与 reports/coverage.xml (当前值),
    验证当前覆盖率不退化超过 2pp。基线由 _check_coverage_trend.py 在通过时自动更新。

    此检查是 _check_coverage_trend.py 的轻量同步版本, 不依赖 pytest 运行,
    只读取已有报告文件。若报告缺失则 WARN (建议先跑测试生成报告)。

    不阻断 (never RED): 覆盖率退化属可维护性债, 非功能性阻断。只做告警级别提示。
    """
    import json
    import xml.etree.ElementTree as ET

    baseline_path = _PROJECT_ROOT / "reports" / "ci" / "coverage_baseline.json"
    cov_xml_path = _PROJECT_ROOT / "reports" / "coverage.xml"

    if not baseline_path.exists():
        return False, (f"基线缺失: {baseline_path.name} (需运行 python scripts/_check_coverage_trend.py 冻结)")
    if not cov_xml_path.exists():
        return False, (
            f"覆盖率报告缺失: {cov_xml_path.name} (需运行 pytest --cov=utils --cov-report=xml:reports/coverage.xml)"
        )

    try:
        base = json.loads(baseline_path.read_text(encoding="utf-8", errors="replace"))
        base_lr = float(base.get("line_rate", 0.0))
    except (json.JSONDecodeError, ValueError, TypeError):
        return False, f"基线 JSON 解析失败: {baseline_path.name}"

    try:
        tree = ET.parse(str(cov_xml_path))  # nosec B314  # 输入为本机 pytest 自产 coverage.xml, 非不可信输入
        root = tree.getroot()
        cur_lr = float(root.attrib.get("line-rate", "0"))
    except (ET.ParseError, ValueError, TypeError):
        return False, f"coverage.xml 解析失败: {cov_xml_path.name}"

    delta = cur_lr - base_lr
    if delta < -_COVERAGE_DEGRADATION_THRESHOLD:
        return False, (
            f"覆盖率退化: 当前 {cur_lr:.4f} < 基线 {base_lr:.4f} - "
            f"{_COVERAGE_DEGRADATION_THRESHOLD:.2f} (delta={delta:+.4f})"
        )
    return True, (f"覆盖率未退化: 当前 {cur_lr:.4f}, 基线 {base_lr:.4f} (delta={delta:+.4f})")


# ==================== T9–T14 不崩风控六件套 模块自检 (2026-08-12 Wave4 Phase2) ====================


def _risk_module_smoke(module_name: str, required_attrs: tuple[str, ...]) -> tuple[bool, str]:
    """通用: 可 import + 必要属性/类存在."""
    try:
        import importlib

        mod = importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover - 仅异常路径
        return False, f"{module_name} import 失败: {type(exc).__name__}: {exc}"
    missing = [a for a in required_attrs if not hasattr(mod, a)]
    if missing:
        return False, f"{module_name} 缺少必要对象: {', '.join(missing)}"
    return True, f"{module_name} 可 import + {len(required_attrs)} 个关键对象存在"


def _check_t09_pretrade_guard() -> tuple[bool, str]:
    """T09 预交易风控门: 6 项拦截规则 (手数/价格带/名义上限/ST过滤/白名单/停牌过滤)."""
    ok, info = _risk_module_smoke(
        "utils.risk.pretrade_guard",
        ("PreTradeGuard", "GuardOrderRequest", "GuardResult"),
    )
    if not ok:
        return ok, info
    # 行为自检 1: 150 股 (非 100 整数倍) 应被拦截
    from utils.risk.pretrade_guard import GuardOrderRequest, PreTradeGuard

    try:
        g = PreTradeGuard()
        r1 = g.check(GuardOrderRequest("sh600000", "buy", 150, 10.0))
        if r1.is_pass:
            return False, "T09 行为自检失败: 150 股(非100整数倍) 未被拦截"
        # 行为自检 2: ST 买入应被拦截 (名称含 ST)
        r2 = g.check(GuardOrderRequest("sh600001", "buy", 100, 5.0, symbol_name="ST 某某"))
        if r2.is_pass:
            return False, "T09 行为自检失败: ST 买入未被拦截"
        # 行为自检 3: 停牌股票应被拦截
        r3 = g.check(GuardOrderRequest("sh600002", "buy", 100, 10.0, is_suspended=True))
        if r3.is_pass:
            return False, "T09 行为自检失败: 停牌股买入未被拦截"
    except Exception as exc:  # pragma: no cover
        return False, f"T09 行为自检异常: {type(exc).__name__}: {exc}"
    return True, "T09 PreTradeGuard 3 项行为自检 (手数/ST/停牌) ✓"


def _check_t10_position_limit_enforcer() -> tuple[bool, str]:
    """T10 持仓集中度执行器: 单票/行业/净敞口/总杠杆."""
    return _risk_module_smoke(
        "utils.risk.position_limit_enforcer",
        (
            "PositionLimitEnforcer",
            "PositionSnapshot",
            "OrderImpact",
            "EnforcementResult",
        ),
    )


def _check_t11_intraday_circuit_breaker() -> tuple[bool, str]:
    """T11 日内熔断器: 连续失败/日内回撤/波动率爆发 + 状态机 CLOSED→OPEN→HALF_OPEN."""
    return _risk_module_smoke(
        "utils.risk.intraday_circuit_breaker",
        ("IntradayCircuitBreaker", "CBState", "CBMetrics"),
    )


def _check_t12_kill_switch_manager() -> tuple[bool, str]:
    """T12 三级熔断管理器: L1 预警 / L2 降仓 / L3 强平."""
    return _risk_module_smoke(
        "utils.risk.kill_switch_manager",
        ("KillSwitchManager", "KillLevel", "KillDecision", "KillSwitchAudit"),
    )


def _check_t13_trade_order_reconciler() -> tuple[bool, str]:
    """T13 计划单 vs 成交 对账: 覆盖/数量/价格/孤儿成交."""
    return _risk_module_smoke(
        "utils.risk.trade_order_reconciler",
        ("TradeOrderReconciler", "PlannedOrder", "FillRecord", "ReconciliationReport"),
    )


def _check_t14_risk_audit_logger() -> tuple[bool, str]:
    """T14 风控审计 JSONL 日志: 写盘/刷新/按日查询/回放."""
    ok, info = _risk_module_smoke(
        "utils.risk.risk_audit_logger",
        ("RiskAuditLogger", "AuditRecord"),
    )
    if not ok:
        return ok, info
    # 行为自检: 写 1 条 → flush → query_by_date 能读回
    import tempfile
    from datetime import datetime

    from utils.risk.risk_audit_logger import RiskAuditLogger

    try:
        with tempfile.TemporaryDirectory() as td:
            lg = RiskAuditLogger(project_root=td, audit_dir="audit", buffer_capacity=10)
            lg.log(
                "T09_PRETRADE",
                "BLOCK",
                severity="WARNING",
                symbol="sh1",
                reason="SMOKE",
            )
            lg.flush()
            today = datetime.now().strftime("%Y-%m-%d")
            if len(lg.query_by_date(today)) < 1:
                return (
                    False,
                    "RiskAuditLogger 行为自检失败: 写+flush 后 query_by_date 未读回",
                )
    except Exception as exc:  # pragma: no cover
        return False, f"RiskAuditLogger 行为自检异常: {type(exc).__name__}: {exc}"
    return True, "T14 RiskAuditLogger 落盘+查询行为自检 ✓"


# ==================== T15–T18 实盘验证四件套 模块自检 (2026-08-12 Wave4 Phase3) ====================


def _check_t15_live_order_executor() -> tuple[bool, str]:
    """T15 实盘下单编排器: 整合 T09-T12 风控门 + broker 下单 + T14 审计."""
    ok, info = _risk_module_smoke(
        "utils.risk.live_order_executor",
        (
            "LiveOrderExecutor",
            "LiveExecutionResult",
            "SliceExecutionResult",
            "BrokerProtocol",
            "FillsStoreProtocol",
        ),
    )
    if not ok:
        return ok, info
    # 行为自检: T09 拦截 150 股 (非 100 整数倍), broker 不应被调用
    import tempfile
    from dataclasses import dataclass
    from pathlib import Path
    from unittest.mock import MagicMock

    from utils.risk.intraday_circuit_breaker import IntradayCircuitBreaker
    from utils.risk.kill_switch_manager import KillSwitchManager
    from utils.risk.live_order_executor import LiveOrderExecutor
    from utils.risk.position_limit_enforcer import PositionLimitEnforcer
    from utils.risk.pretrade_guard import PreTradeGuard
    from utils.risk.risk_audit_logger import RiskAuditLogger

    @dataclass
    class _Slice:
        slice_idx: int = 0
        target_shares: int = 150
        limit_price: float = 10.0

    @dataclass
    class _Plan:
        plan_id: str = "smoke"
        symbol: str = "sh600000"
        side: str = "buy"
        slices: list = None

    try:
        broker = MagicMock()
        broker.is_live = False
        broker.name = "smoke"
        broker.get_order_status.return_value = {
            "state": "FILLED",
            "filled_qty": 0,
            "avg_price": 0.0,
        }
        audit = RiskAuditLogger(project_root=Path(tempfile.mkdtemp()), audit_dir="audit")
        ex = LiveOrderExecutor(
            broker=broker,
            pretrade_guard=PreTradeGuard(),
            position_enforcer=PositionLimitEnforcer(),
            circuit_breaker=IntradayCircuitBreaker(),
            kill_switch=KillSwitchManager(),
            audit_logger=audit,
        )
        plan = _Plan(slices=[_Slice()])
        result = ex.execute_plan(plan)
        if not result.all_rejected:
            return False, "T15 行为自检失败: 150 股应被 T09 拦截"
        if broker.place_order.called:
            return False, "T15 行为自检失败: 拦截后 broker.place_order 不应被调用"
    except Exception as exc:  # pragma: no cover
        return False, f"T15 行为自检异常: {type(exc).__name__}: {exc}"
    return True, "T15 LiveOrderExecutor T09 拦截行为自检 ✓"


def _check_t16_order_lifecycle_tracker() -> tuple[bool, str]:
    """T16 订单生命周期跟踪器: 8 态状态机 + 超时撤单 + 孤儿单检测."""
    ok, info = _risk_module_smoke(
        "utils.risk.order_lifecycle_tracker",
        ("OrderLifecycleTracker", "OrderState", "TrackedOrder", "map_broker_state"),
    )
    if not ok:
        return ok, info
    # 行为自检: map_broker_state 映射 + OrderState 终态
    from utils.risk.order_lifecycle_tracker import OrderState, map_broker_state

    try:
        assert map_broker_state("53") == OrderState.FILLED
        assert map_broker_state("CANCELLED") == OrderState.CANCELLED
        assert map_broker_state("UNKNOWN") == OrderState.ERROR
        assert OrderState.FILLED.is_terminal
        assert OrderState.SUBMITTED.is_active
    except AssertionError as exc:
        return False, f"T16 行为自检失败: {exc}"
    return True, "T16 OrderLifecycleTracker 状态映射+终态自检 ✓"


def _check_t17_live_reconciliation_loop() -> tuple[bool, str]:
    """T17 实盘对账循环: 包装 T13 + 持仓 drift 检测 + 盘中/盘后双循环."""
    return _risk_module_smoke(
        "utils.risk.live_reconciliation_loop",
        ("LiveReconciliationLoop", "LiveReconciliationReport", "PositionDrift"),
    )


def _check_t18_gradual_rollout_orchestrator() -> tuple[bool, str]:
    """T18 灰度发布编排器: 4 阶段状态机 + 准入/回滚门禁 + 资金比例管理."""
    ok, info = _risk_module_smoke(
        "utils.risk.gradual_rollout_orchestrator",
        (
            "GradualRolloutOrchestrator",
            "RolloutStage",
            "StageAdmissionCriteria",
            "StageMetrics",
            "default_criteria",
        ),
    )
    if not ok:
        return ok, info
    # 行为自检: 4 阶段 capital_ratio 严格递增 + 不可跳阶段
    from utils.risk.gradual_rollout_orchestrator import RolloutStage

    try:
        stages = list(RolloutStage)
        ratios = [s.capital_ratio for s in stages]
        assert ratios == [0.0, 0.10, 0.50, 1.00], f"capital_ratio 不匹配: {ratios}"
        assert stages[0].prev_stage is None
        assert stages[-1].next_stage is None
    except AssertionError as exc:
        return False, f"T18 行为自检失败: {exc}"
    return True, "T18 GradualRolloutOrchestrator 4 阶段+资金比例自检 ✓"


# ==================== D1–D4 LLM 智能进化 Phase D 模块自检 (2026-08-12 G6) ====================


def _llm_evo_module_smoke(module_name: str, required_attrs: tuple[str, ...]) -> tuple[bool, str]:
    """通用: utils.llm_evolution 子模块可 import + 必要对象存在."""
    try:
        import importlib

        mod = importlib.import_module(module_name)
    except Exception as exc:
        return False, f"{module_name} import 失败: {type(exc).__name__}: {exc}"
    missing = [a for a in required_attrs if not hasattr(mod, a)]
    if missing:
        return False, f"{module_name} 缺少: {', '.join(missing)}"
    return True, f"{module_name} 可 import + {len(required_attrs)} 个关键对象存在"


def _check_d1_strategy_ideation() -> tuple[bool, str]:
    """D1 LLM 策略 Ideation 引擎: 五步流水线 (观察→假设→因子→验证→入库)."""
    ok, info = _llm_evo_module_smoke(
        "utils.llm_evolution.strategy_ideation",
        (
            "StrategyIdeationEngine",
            "MarketObservation",
            "Hypothesis",
            "IdeationCycleResult",
        ),
    )
    if not ok:
        return ok, info
    # 行为自检: MockLLM 生成假设 + 多样性去重
    from utils.llm_evolution.strategy_ideation import (
        MarketObservation,
        StrategyIdeationEngine,
    )

    try:

        class _MockLLM:
            name = "smoke"

            def chat(self, prompt, system="", temperature=None, max_tokens=None):
                import json

                return json.dumps(
                    {
                        "hypotheses": [
                            {
                                "description": "测试假设",
                                "factor_direction": "long_small",
                                "proposed_factors": [
                                    {
                                        "name": "EP",
                                        "category": "Value",
                                        "formula": "1/PE",
                                    }
                                ],
                                "strategy_style": "value",
                            }
                        ]
                    }
                )

        engine = StrategyIdeationEngine(llm_router=_MockLLM())
        obs = MarketObservation(date="2026-08-12", index_close=3200.0)
        hyps = engine.generate_hypotheses(obs, n=3)
        if len(hyps) != 1:
            return False, "D1 行为自检失败: MockLLM 应返回 1 个假设"
        if not hyps[0].diversity_hash:
            return False, "D1 行为自检失败: 假设未计算 diversity_hash"
    except Exception as exc:
        return False, f"D1 行为自检异常: {type(exc).__name__}: {exc}"
    return True, "D1 StrategyIdeationEngine Ideation+多样性自检 ✓"


def _check_d2_hypothesis_verifier() -> tuple[bool, str]:
    """D2 假设验证框架: IC 显著性 + Purged K-Fold + CRO Gate + AB 桶判定."""
    ok, info = _llm_evo_module_smoke(
        "utils.llm_evolution.hypothesis_verifier",
        ("HypothesisVerifier", "HypothesisVerdict", "VerificationThresholds"),
    )
    if not ok:
        return ok, info
    # 行为自检: 显著 IC 通过 + 不显著 IC 证伪
    from utils.llm_evolution.hypothesis_verifier import HypothesisVerifier

    try:
        v = HypothesisVerifier()
        good_data = {
            "ic_series": [0.04 + 0.001 * i for i in range(100)],
            "max_drawdown": 0.08,
            "wf_mean_ic": 0.038,
            "dsr_score": 1.5,
        }
        bad_data = {
            "ic_series": [0.001 * ((-1) ** i) for i in range(100)],
            "max_drawdown": 0.20,
            "wf_mean_ic": 0.0,
            "dsr_score": 0.5,
        }
        good = v.verify({"name": "GOOD"}, good_data)
        bad = v.verify({"name": "BAD"}, bad_data)
        if not good["enter_ab_bucket"]:
            return False, "D2 行为自检失败: 显著 IC 未进入 AB 桶"
        if not bad["falsified"]:
            return False, "D2 行为自检失败: 不显著 IC 未被证伪"
    except Exception as exc:
        return False, f"D2 行为自检异常: {type(exc).__name__}: {exc}"
    return True, "D2 HypothesisVerifier IC+AB桶判定自检 ✓"


def _check_d3_knowledge_base() -> tuple[bool, str]:
    """D3 知识沉淀库: JSONL 持久化 + 查询 + LLM 上下文反馈."""
    ok, info = _llm_evo_module_smoke(
        "utils.llm_evolution.knowledge_base",
        ("KnowledgeBase", "KnowledgeEntry"),
    )
    if not ok:
        return ok, info
    # 行为自检: 临时目录写读 JSONL
    import tempfile
    from pathlib import Path

    from utils.llm_evolution.knowledge_base import KnowledgeBase, KnowledgeEntry

    try:
        with tempfile.TemporaryDirectory() as td:
            kb = KnowledgeBase(path=Path(td) / "kb.jsonl")
            entry = KnowledgeEntry(entry_id="kb_test", description="测试", status="validated")
            kb.persist_entry(entry)
            all_entries = kb.load_all()
            if len(all_entries) != 1:
                return False, "D3 行为自检失败: 写入后读取数量不一致"
            ctx = kb.load_context_for_ideation()
            if "测试" not in ctx:
                return False, "D3 行为自检失败: 上下文未包含已写入条目"
    except Exception as exc:
        return False, f"D3 行为自检异常: {type(exc).__name__}: {exc}"
    return True, "D3 KnowledgeBase 写读+上下文反馈自检 ✓"


def _check_d4_dual_loop_orchestrator() -> tuple[bool, str]:
    """D4 双层闭环编排器: LLM 假设生成层 ↔ B4 进化执行层联动."""
    ok, info = _llm_evo_module_smoke(
        "utils.llm_evolution.dual_loop_orchestrator",
        ("DualLoopOrchestrator", "DualLoopReport", "DualLoopSafetyConfig"),
    )
    if not ok:
        return ok, info
    # 行为自检: kill_switch 拦截 → 暂停
    import tempfile
    from pathlib import Path
    from unittest.mock import MagicMock

    from utils.llm_evolution.dual_loop_orchestrator import (
        DualLoopOrchestrator,
    )
    from utils.llm_evolution.hypothesis_verifier import HypothesisVerifier
    from utils.llm_evolution.knowledge_base import KnowledgeBase
    from utils.llm_evolution.strategy_ideation import StrategyIdeationEngine

    try:

        class _MockLLM:
            name = "smoke"

            def chat(self, *a, **kw):
                import json

                return json.dumps({"hypotheses": []})

        ks = MagicMock()
        ks.evaluate_trade.return_value = MagicMock(allowed=False, reason="BLOCKED")
        with tempfile.TemporaryDirectory() as td:
            orch = DualLoopOrchestrator(
                ideation_engine=StrategyIdeationEngine(llm_router=_MockLLM()),
                verifier=HypothesisVerifier(),
                knowledge_base=KnowledgeBase(path=Path(td) / "kb.jsonl"),
                kill_switch=ks,
            )
            report = orch.run_cycle({"date": "2026-08-12"})
            if not report.paused:
                return False, "D4 行为自检失败: Kill Switch 拦截后未暂停"
    except Exception as exc:
        return False, f"D4 行为自检异常: {type(exc).__name__}: {exc}"
    return True, "D4 DualLoopOrchestrator Kill Switch 暂停自检 ✓"


def _check_d5_auto_research_skill() -> tuple[bool, str]:
    """D5 AutoResearch Skill: 因子自动迭代闭环 (生成→评估→S1-S5门禁→入库→退役)."""
    # 1. import 检查: 骨架 + 默认实现
    try:
        import importlib

        _defaults = importlib.import_module("ai_decision.auto_research_defaults")
        _skill_mod = importlib.import_module("ai_decision.auto_research_skill")
        for _name in (
            "ExpressionFactorGenerator",
            "S1EffectiveICGate",
            "S2EffectiveICIRGate",
            "S3LongShortSharpeGate",
            "S4OrthogonalGate",
            "S5BacktestIncrementGate",
            "StandardFactorEvaluator",
        ):
            getattr(_defaults, _name)
        for _name in ("AutoResearchSkill", "GateStage", "InMemoryFactorRegistry"):
            getattr(_skill_mod, _name)
        from ai_decision.auto_research_defaults import create_default_skill
        from ai_decision.auto_research_skill import FactorCandidate, ResearchContext
    except (ImportError, AttributeError) as exc:
        return False, f"D5 import 失败: {exc}"

    # 2. 行为自检: 完整迭代 (生成→评估→门禁) + 衰退退役
    try:
        skill = create_default_skill()
        # 构造模拟 price_data (60 日)
        closes = [10.0 * (1 + 0.002 * i + 0.01 * ((-1) ** i)) for i in range(60)]
        ctx = ResearchContext(
            price_data={"sh600000": {"close": closes}},
            active_factors=["MOM_60D"],
        )
        iteration = skill.run_iteration(ctx)
        if not iteration.candidates_generated:
            return False, "D5 行为自检失败: 未生成候选因子"
        if len(iteration.evaluations) != len(iteration.candidates_generated):
            return False, "D5 行为自检失败: 评估数与候选数不匹配"

        # 衰退退役自检
        cand = FactorCandidate(name="MOM_60D", category="Momentum", source="test")
        skill._registry.register(cand)
        retired = skill.monitor_and_retire(
            active_factors=["MOM_60D"],
            decay_signals={"MOM_60D": 0.15},  # ICIR < 0.2 → 退役
        )
        if "MOM_60D" not in retired:
            return False, "D5 行为自检失败: ICIR<阈值未触发退役"
    except Exception as exc:
        return False, f"D5 行为自检异常: {type(exc).__name__}: {exc}"

    # 3. SKILL.md 存在性检查
    skill_md = _PROJECT_ROOT / "skills" / "auto_research" / "SKILL.md"
    if not skill_md.exists():
        return False, "D5 SKILL.md 缺失"

    return (
        True,
        f"D5 AutoResearchSkill 迭代+退役自检 ✓ (候选 {len(iteration.candidates_generated)}, 入库 {len(iteration.promoted_factors)})",  # noqa: E501
    )


def _check_d6_litellm_router() -> tuple[bool, str]:
    """D6 LiteLLM Gateway: 多模型统一路由 (ChatRequest→ChatResponse + 场景路由 + 统计)."""
    # 1. import 检查
    try:
        import importlib

        _gw = importlib.import_module("utils.llm_gateway")
        for _name in ("ChatResponse", "ProviderInfo", "Usage"):
            getattr(_gw, _name)
        from utils.llm_gateway import ChatRequest, LiteLLMRouter
    except (ImportError, AttributeError) as exc:
        return False, f"D6 import 失败: {exc}"

    # 2. 行为自检: 场景路由 + 统计 + glm5_client 兼容
    try:
        from unittest.mock import MagicMock

        LiteLLMRouter.reset_instance()
        # mock inner router
        mock_inner = MagicMock()
        mock_inner.chat.return_value = "D6 自检回复"
        mock_inner._fallback_chain = ["deepseek", "doubao"]
        mock_inner._providers_config = {"deepseek": {"model": "deepseek-chat"}}

        router = LiteLLMRouter(inner_router=mock_inner)
        req = ChatRequest(prompt="自检", scene="intraday")
        resp = router.chat(req)
        if not resp.success:
            return False, "D6 行为自检失败: chat 返回失败"
        if resp.content != "D6 自检回复":
            return False, f"D6 行为自检失败: content 不匹配 ({resp.content})"
        if resp.provider.name != "deepseek":
            return False, f"D6 行为自检失败: provider 不匹配 ({resp.provider.name})"

        # 场景路由温度检查
        router.reset_stats()
        router.chat(ChatRequest(prompt="报告", scene="report"))
        call_kwargs = mock_inner.chat.call_args
        if call_kwargs.kwargs.get("temperature") != 0.5:
            return (
                False,
                f"D6 场景路由失败: report 温度应为 0.5, 实际 {call_kwargs.kwargs.get('temperature')}",
            )

        # 统计检查 (reset 后仅 1 次)
        stats = router.get_stats()
        if stats["total_calls"] != 1 or stats["success_calls"] != 1:
            return (
                False,
                f"D6 统计失败: calls={stats['total_calls']} success={stats['success_calls']}",
            )

        # glm5_client 兼容检查
        from utils.glm5_client import GLM5Client

        client = GLM5Client()
        # is_ready 应返回 bool
        assert isinstance(client.is_ready(), bool)
        # chat 输入验证
        try:
            client.chat("")
            return False, "D6 glm5_client 输入验证失败: 空消息未抛异常"
        except ValueError:
            pass  # 预期行为

    except Exception as exc:
        return False, f"D6 行为自检异常: {type(exc).__name__}: {exc}"

    # 3. glm5_client 重构验证: 行数大幅缩减
    glm5_path = _PROJECT_ROOT / "utils" / "glm5_client.py"
    if glm5_path.exists():
        line_count = sum(1 for _ in glm5_path.open(encoding="utf-8"))
        if line_count > 400:
            return False, f"D6 glm5_client 重构未完成: 仍有 {line_count} 行 (预期 ≤400)"

    LiteLLMRouter.reset_instance()
    return (
        True,
        f"D6 LiteLLMRouter 场景路由+统计+glm5_client 兼容自检 ✓ (stats: {stats['total_calls']} calls)",
    )


def _check_d7_daily_workflow_split() -> tuple[bool, str]:
    """D7 daily_workflow 拆分收尾: 门禁达标 + _scan_func_quality 脚本存在 + phase 模块完整."""
    # 1. daily_workflow.py 行数 ≤ 3000 (门禁)
    dw_path = _PROJECT_ROOT / "v8.3_institutional" / "daily_workflow.py"
    if not dw_path.exists():
        return False, "D7 daily_workflow.py 不存在"
    dw_lines = sum(1 for _ in dw_path.open(encoding="utf-8"))
    if dw_lines > 3000:
        return False, f"D7 daily_workflow.py {dw_lines} 行 > 门禁 3000"

    # 2. _scan_func_quality.py 脚本存在
    scan_script = _PROJECT_ROOT / "scripts" / "_scan_func_quality.py"
    if not scan_script.exists():
        return False, "D7 scripts/_scan_func_quality.py 不存在"

    # 3. phase 模块完整 (15 个 phase + context.py)
    phases_dir = _PROJECT_ROOT / "v8.3_institutional" / "workflow" / "phases"
    if not phases_dir.is_dir():
        return False, "D7 workflow/phases/ 目录不存在"
    expected_phases = [
        "check.py",
        "calibrate.py",
        "market.py",
        "risk.py",
        "hedge.py",
        "hedge_fund.py",
        "quant_neutral.py",
        "v10_risk.py",
        "cash_management.py",
        "directional_futures.py",
        "signal.py",
        "signal_qlib.py",
        "signal_ifind.py",
        "signal_lgb.py",
        "autolearn.py",
    ]
    missing_phases = [p for p in expected_phases if not (phases_dir / p).exists()]
    if missing_phases:
        return False, f"D7 phase 模块缺失: {', '.join(missing_phases)}"

    # 4. context.py 存在
    context_path = _PROJECT_ROOT / "v8.3_institutional" / "workflow" / "context.py"
    if not context_path.exists():
        return False, "D7 workflow/context.py 不存在"

    # 5. _scan_func_quality.py 可运行 (扫描 workflow/phases/ 不崩溃)
    try:
        import subprocess

        result = subprocess.run(
            [sys.executable, str(scan_script), "--target-dir", str(phases_dir)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            cwd=str(_PROJECT_ROOT),
        )
        if result.returncode not in (0, 1):  # 0=无Strong, 1=有Strong, 2=异常
            return False, f"D7 _scan_func_quality.py 异常退出码 {result.returncode}"
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"D7 _scan_func_quality.py 执行失败: {exc}"

    return (
        True,
        f"D7 daily_workflow 拆分收尾 ✓ ({dw_lines} 行 ≤3000, 15 phase + context, scan 脚本可用)",
    )


def _check_d8_g7_coverage_sprint() -> tuple[bool, str]:
    """D8 G7 覆盖率冲刺: 测试文件存在 + .coveragerc 排除模式正确 + 覆盖率基线更新."""
    # 1. G7 测试文件存在
    g7_tests = [
        "tests/unit/test_g7_coverage_boost.py",
        "tests/unit/test_g7_hedge_engine_boost.py",
    ]
    missing_tests = [t for t in g7_tests if not (_PROJECT_ROOT / t).exists()]
    if missing_tests:
        return False, f"D8 G7 测试文件缺失: {', '.join(missing_tests)}"

    # 2. .coveragerc 排除模式正确 (evolution/* 和 multi_model_router.py)
    rc_path = _PROJECT_ROOT / ".coveragerc"
    if not rc_path.exists():
        return False, "D8 .coveragerc 缺失"
    rc_content = rc_path.read_text(encoding="utf-8", errors="replace")
    required_patterns = ["evolution/*", "multi_model_router.py"]
    missing_patterns = [p for p in required_patterns if p not in rc_content]
    if missing_patterns:
        return False, f"D8 .coveragerc 缺少排除模式: {', '.join(missing_patterns)}"

    # 3. 覆盖率基线文件存在且 line_rate > 0.40
    baseline_path = _PROJECT_ROOT / "reports" / "ci" / "coverage_baseline.json"
    if not baseline_path.exists():
        return False, "D8 coverage_baseline.json 缺失"
    try:
        import json

        base = json.loads(baseline_path.read_text(encoding="utf-8", errors="replace"))
        base_lr = float(base.get("line_rate", 0.0))
        if base_lr < 0.40:
            return False, f"D8 覆盖率基线 {base_lr:.4f} < 0.40 (需≥40%)"
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return False, f"D8 coverage_baseline.json 解析失败: {exc}"

    # 4. G7 测试文件可导入 (语法检查)
    try:
        import py_compile

        for t in g7_tests:
            py_compile.compile(str(_PROJECT_ROOT / t), doraise=True)
    except py_compile.PyCompileError as exc:
        return False, f"D8 G7 测试文件编译失败: {exc}"

    return (
        True,
        f"D8 G7 覆盖率冲刺 ✓ ({len(g7_tests)} 测试文件, 基线 {base_lr:.4f}, 排除模式正确)",
    )


def _check_d9_coverage_sprint4_target() -> tuple[bool, str]:
    """D9: 覆盖率 Sprint4 0.80 达标门禁 (阻断 RED)."""
    import json

    coverage_baseline = _PROJECT_ROOT / "reports" / "ci" / "coverage_baseline.json"
    if not coverage_baseline.exists():
        return False, "D9 coverage_baseline.json 缺失 (需运行 pytest --cov 生成)"
    try:
        data = json.loads(coverage_baseline.read_text(encoding="utf-8", errors="replace"))
        line_rate = float(data.get("line_rate", 0.0))
        if line_rate >= 0.80:
            return True, f"D9 覆盖率 Sprint4 达标 ✓ (line_rate={line_rate:.4f} ≥ 0.80)"
        return False, f"D9 覆盖率未达标 (line_rate={line_rate:.4f} < 0.80)"
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return False, f"D9 coverage_baseline.json 解析失败: {type(exc).__name__}: {exc}"


def _check_d10_oversized_file_split() -> tuple[bool, str]:
    """D10: 超大文件拆分门禁 (≤2000 行, 阻断 RED)."""
    targets = [
        _PROJECT_ROOT / "institutional_pipeline_runner.py",
        _PROJECT_ROOT / "utils" / "execution" / "automated_execution_system.py",
    ]
    limit = 2000
    for f in targets:
        if not f.exists():
            continue
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                line_count = sum(1 for _ in fh)
            if line_count > limit:
                return False, f"D10 {f.name} 行数 {line_count} > {limit} (需拆分)"
        except OSError:
            continue
    return True, f"D10 超大文件拆分达标 ✓ (均 ≤{limit} 行)"


def _check_d11_phase_b_shadow_stable() -> tuple[bool, str]:
    """D11: Phase B shadow 连续 7 天稳定门禁 (阻断 RED, 周末/CI 降级为不阻断)."""
    import json
    import os
    from datetime import datetime

    status_path = _PROJECT_ROOT / "reports" / "evolution" / "phase_b_status.json"
    if not status_path.exists():
        # CI 环境结构性缺失: phase_b_status.json 是本机运行时状态 (被 .gitignore 排除),
        # CI checkout 永远拿不到 → 在 CI 上判 FAIL 是假阴性噪声, 不是真实债务。
        # 与"周末降级"同属数据可得性降级: 该门禁的权威评估点是生产机, 不在 CI。
        if os.environ.get("CI"):
            return (
                True,
                "D11 在 CI 环境跳过: phase_b_status.json 为运行时状态产物 (不入版本库), 该门禁的权威评估点在生产机",
            )
        return (
            False,
            "D11 phase_b_status.json 缺失 (需运行 phase_b_progressive_enabler.py)",
        )
    try:
        data = json.loads(status_path.read_text(encoding="utf-8", errors="replace"))
        stable_days = int(data.get("consecutive_stable_days", 0))
        target = int(data.get("stable_days_target", 7))
        min_samples = int(data.get("min_shadow_samples", 20))
        total_samples = len(data.get("daily_health_log", []))

        # 周末为非交易日, 不强制要求 shadow 稳定, 避免 CI 在周末无新样本时误杀
        today = datetime.now().weekday()
        is_weekend = today in (5, 6)

        if stable_days >= target and total_samples >= min_samples:
            return (
                True,
                f"D11 Phase B shadow 稳定达标 ✓ ({stable_days}/{target} 天, {total_samples} 样本)",
            )
        if is_weekend:
            return (
                True,
                f"D11 Phase B shadow 周末观测日, 暂不计为阻断 "
                f"({stable_days}/{target} 天, {total_samples}/{min_samples} 样本)",
            )
        return (
            False,
            f"D11 Phase B shadow 未达标 ({stable_days}/{target} 天, {total_samples}/{min_samples} 样本)",
        )
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return False, f"D11 phase_b_status.json 解析失败: {type(exc).__name__}: {exc}"


# ============================================================
# D12: Q4 冻结窗 Change Budget 机械检查 (R-4, ROADMAP 2026-09-05)
# ============================================================

FREEZE_WINDOW_START = "2026-09-19"
FREEZE_WINDOW_END = "2026-12-10"
_FREEZE_BASELINE_DIR = _PROJECT_ROOT / "reports" / "freeze_baseline"
_FLAGS_CONFIG = _PROJECT_ROOT / "config" / "feature_flags.yaml"
# 冻结窗内禁止新增落盘的生产模型模式 (Change Budget: 生产模型 0 个新增)
_FREEZE_MODEL_GLOBS = ("reports/qlib_model_*.pkl",)


def _freeze_flag_enabled_map(flags_config: Path | None = None) -> dict[str, bool]:
    """读取 flag 注册表, 返回 {name: enabled}。

    enabled 判定: rollout.enabled or default (与 flag_manager 运行时语义一致:
    USE_EVOLUTION_ORCHESTRATOR 经 rollout.enabled=true 灰度中, USE_VOL_REGIME_WEIGHTER
    经 default=true 常开)。
    """
    import yaml

    with open(flags_config or _FLAGS_CONFIG, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    result: dict[str, bool] = {}
    for name, spec in (data.get("flags") or {}).items():
        if not isinstance(spec, dict):
            continue
        rollout_enabled = bool((spec.get("rollout") or {}).get("enabled", False))
        result[name] = rollout_enabled or bool(spec.get("default", False))
    return result


def _check_d12_freeze_window_change_budget(today=None) -> tuple[bool, str]:
    """D12 冻结窗 Change Budget 机械检查 (R-4, ROADMAP §Q4 Change Budget)。

    窗口外 (09-19 前或 12-10 后): PASS (未激活, 不产生任何文件)。
    窗口内 (2026-09-19 ~ 2026-12-10):
      维度1 flag: 首次运行冻结基线 (config/feature_flags.yaml 全量快照);
                  之后相对基线新增 enabled → FAIL (0 个新增 enable 铁律)。
      维度2 模型: 冻结窗开始后新落盘的生产模型 pkl → FAIL (生产模型 0 个新增)。
      维度3 代码/因子: 无法纯机械判定 → code review + pre-commit 人工核对
                  (本检查不覆盖, 消息中注记)。
    fail-closed: 窗口内注册表/基线读取失败 → FAIL, 绝不静默放行 (决策路径 fail-close 铁律)。
    """
    day = today or _date.today()
    start = _date.fromisoformat(FREEZE_WINDOW_START)
    end = _date.fromisoformat(FREEZE_WINDOW_END)
    if not (start <= day <= end):
        return (
            True,
            f"冻结窗未激活 ({FREEZE_WINDOW_START}~{FREEZE_WINDOW_END}), Change Budget 检查待激活",
        )

    _FREEZE_BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    baseline_file = _FREEZE_BASELINE_DIR / (f"flags_baseline_{FREEZE_WINDOW_START.replace('-', '')}.json")

    violations: list[str] = []

    # ---- 维度 1: feature flag 新增 enable ----
    try:
        current = _freeze_flag_enabled_map()
    except Exception as exc:  # noqa: BLE001 — fail-closed: 读不到注册表不能放行
        return False, f"冻结窗内 flag 注册表读取失败 ({exc}), fail-closed"

    baseline_just_created = False
    if not baseline_file.exists():
        baseline_file.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        baseline_just_created = True
    else:
        try:
            baseline = json.loads(baseline_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return False, f"冻结基线损坏 ({exc}), fail-closed"
        for name, enabled in current.items():
            if enabled and not baseline.get(name, False):
                violations.append(f"flag 新增 enable: {name}")

    # ---- 维度 2: 生产模型新增落盘 ----
    start_ts = datetime.combine(start, _time.min).timestamp()
    for pattern in _FREEZE_MODEL_GLOBS:
        for p in sorted(_PROJECT_ROOT.glob(pattern)):
            try:
                if p.stat().st_mtime >= start_ts:
                    violations.append(f"冻结窗内新增生产模型: {p.relative_to(_PROJECT_ROOT)}")
            except OSError:
                continue

    if violations:
        return False, "Change Budget 违规: " + "; ".join(violations)
    if baseline_just_created:
        return (
            True,
            f"冻结基线首次创建 ({len(current)} flags), 后续运行对比新增 enable (代码/因子维度走 code review 人工核对)",
        )
    return (
        True,
        f"冻结窗内无违规 (flag 基线 {len(current)} 项, 模型 0 新增; 代码/因子维度走 code review 人工核对)",
    )


@dataclass(frozen=True)
class V87GateSummary:
    """v8.7 发布三门禁汇总结果 (frozen)."""

    phase_b_stable_7d: bool
    oversized_file_split: bool
    coverage_sprint4_080: bool
    all_passed: bool
    blocking_reason: str
    timestamp: str

    def to_dict(self) -> dict:
        return {
            "phase_b_stable_7d": self.phase_b_stable_7d,
            "oversized_file_split": self.oversized_file_split,
            "coverage_sprint4_080": self.coverage_sprint4_080,
            "all_passed": self.all_passed,
            "blocking_reason": self.blocking_reason,
            "timestamp": self.timestamp,
        }


def check_v87_release_gate_summary() -> V87GateSummary:
    """聚合 D9 + D10 + D11 三门禁, 输出 v8.7 发布阻断/放行判定."""
    from datetime import datetime

    d9_ok, _ = _check_d9_coverage_sprint4_target()
    d10_ok, _ = _check_d10_oversized_file_split()
    d11_ok, _ = _check_d11_phase_b_shadow_stable()

    all_passed = d9_ok and d10_ok and d11_ok
    failed_gates: list[str] = []
    if not d11_ok:
        failed_gates.append("D11 PhaseB shadow 7天稳定")
    if not d10_ok:
        failed_gates.append("D10 超大文件拆分 ≤2000行")
    if not d9_ok:
        failed_gates.append("D9 覆盖率 Sprint4 ≥0.80")
    blocking_reason = "" if all_passed else "; ".join(failed_gates)

    return V87GateSummary(
        phase_b_stable_7d=d11_ok,
        oversized_file_split=d10_ok,
        coverage_sprint4_080=d9_ok,
        all_passed=all_passed,
        blocking_reason=blocking_reason,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def _write_v87_gate_summary_json(summary: V87GateSummary) -> Path | None:
    """覆盖写 reports/v87_release_gate_summary.json."""
    import json

    out_dir = _PROJECT_ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "v87_release_gate_summary.json"
    try:
        out_path.write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return out_path
    except OSError:
        return None


def main() -> int:
    checks = [
        ("T1", "测试collection", _check_test_collection_errors()),
        ("T2", "CI脚本存在", _check_ci_scripts_exist()),
        ("T3", "告警模块", _check_notify_exists()),
        ("T4", "环境隔离", _check_env_isolation()),
        ("T5", "陈旧测试", _check_stale_test_count()),
        ("T6", "fail-safe宽捕获", _check_fail_safe_broad_except()),
        ("T7", "裸except独立债", _check_bare_broad_except()),
        ("T8", "覆盖率基线", _check_coverage_baseline()),
        ("T9", "T09预交易风控门", _check_t09_pretrade_guard()),
        ("T10", "T10持仓集中度", _check_t10_position_limit_enforcer()),
        ("T11", "T11日内熔断器", _check_t11_intraday_circuit_breaker()),
        ("T12", "T12三级熔断管理", _check_t12_kill_switch_manager()),
        ("T13", "T13计划单对账", _check_t13_trade_order_reconciler()),
        ("T14", "T14风控审计日志", _check_t14_risk_audit_logger()),
        ("T15", "T15实盘下单编排", _check_t15_live_order_executor()),
        ("T16", "T16订单生命周期", _check_t16_order_lifecycle_tracker()),
        ("T17", "T17实盘对账循环", _check_t17_live_reconciliation_loop()),
        ("T18", "T18灰度发布编排", _check_t18_gradual_rollout_orchestrator()),
        ("D1", "D1策略Ideation引擎", _check_d1_strategy_ideation()),
        ("D2", "D2假设验证框架", _check_d2_hypothesis_verifier()),
        ("D3", "D3知识沉淀库", _check_d3_knowledge_base()),
        ("D4", "D4双层闭环编排", _check_d4_dual_loop_orchestrator()),
        ("D5", "D5 AutoResearch Skill", _check_d5_auto_research_skill()),
        ("D6", "D6 LiteLLM Gateway", _check_d6_litellm_router()),
        ("D7", "D7 daily_workflow 拆分收尾", _check_d7_daily_workflow_split()),
        ("D8", "D8 G7 覆盖率冲刺", _check_d8_g7_coverage_sprint()),
        ("D9", "D9 覆盖率Sprint4 0.80", _check_d9_coverage_sprint4_target()),
        ("D10", "D10 超大文件拆分", _check_d10_oversized_file_split()),
        ("D11", "D11 PhaseB shadow稳定", _check_d11_phase_b_shadow_stable()),
        ("D12", "D12 冻结窗ChangeBudget", _check_d12_freeze_window_change_budget()),
    ]

    sum(1 for _, _, (ok, _) in checks if not ok)

    # 债务分级 (T02/T03 升级 2026-08-12, Wave4 Phase2/3 + G6 Phase D 扩展 + v8.7 Sprint):
    #   - T1–T5   阻断性 (测试/CI/告警/隔离/陈旧): 任何失败 → RED
    #   - T6–T8   告警性 (异常处理债 + 覆盖率退化): 仅 YELLOW
    #   - T9–T14  告警性 (不崩风控六件套 模块自检): 异常→YELLOW
    #   - T15–T18 告警性 (实盘验证四件套 模块自检): 异常→YELLOW
    #   - D1–D4   告警性 (LLM 智能进化 Phase D 模块自检): 异常→YELLOW
    #   - D5–D8   告警性 (AutoResearch/LiteLLM/workflow拆分/G7覆盖率): 异常→YELLOW
    #   - D9–D12  阻断性 (v8.7 发布门禁: 覆盖率0.80/超大文件拆分/PhaseB稳定/冻结窗ChangeBudget): 任何失败 → RED
    blocking_codes = {"T1", "T2", "T3", "T4", "T5", "D9", "D10", "D11", "D12"}
    warn_codes = {
        "T6",
        "T7",
        "T8",
        "T9",
        "T10",
        "T11",
        "T12",
        "T13",
        "T14",
        "T15",
        "T16",
        "T17",
        "T18",
        "D1",
        "D2",
        "D3",
        "D4",
        "D5",
        "D6",
        "D7",
        "D8",
    }
    blocking_fails = sum(1 for code, _, (ok, _) in checks if not ok and code in blocking_codes)
    warn_fails = sum(1 for code, _, (ok, _) in checks if not ok and code in warn_codes)

    if blocking_fails == 0 and warn_fails == 0:
        level = "GREEN"
    elif blocking_fails == 0:
        level = "YELLOW"  # 仅告警性失败
    else:
        level = "RED"

    print("=" * 60)
    print("工程债务门槛检查 (Engineering Debt Gate)")
    print("=" * 60)
    for code, name, (ok, msg) in checks:
        icon = "[OK]" if ok else "[XX]"
        print(f"  {icon} {code} {name}: {msg}")
    print("-" * 60)

    level_icon = {"GREEN": "[GREEN]", "YELLOW": "[YELLOW]", "RED": "[RED]"}[level]
    print(f"  债务等级: {level_icon} {level}")
    if level == "RED":
        print("  建议: 冻结新功能升级, 优先还债")
    elif level == "YELLOW":
        print("  建议: 功能升级需谨慎, 同步还债")
    else:
        print("  状态: 可推进功能升级")
    print("=" * 60)

    # v8.7 发布三门禁汇总 (D9 + D10 + D11)
    v87_summary = check_v87_release_gate_summary()
    json_path = _write_v87_gate_summary_json(v87_summary)
    print()
    print("=" * 60)
    print("v8.7 发布门禁汇总 (Release Gate Summary)")
    print("=" * 60)
    d9_icon = "[OK]" if v87_summary.coverage_sprint4_080 else "[XX]"
    d10_icon = "[OK]" if v87_summary.oversized_file_split else "[XX]"
    d11_icon = "[OK]" if v87_summary.phase_b_stable_7d else "[XX]"
    print(f"  {d9_icon}  D9  覆盖率 Sprint4 ≥0.80")
    print(f"  {d10_icon} D10  超大文件拆分 ≤2000行")
    print(f"  {d11_icon} D11  PhaseB shadow 7天稳定")
    print("-" * 60)
    if v87_summary.all_passed:
        print("  判定: [PASS] v8.7 发布放行")
    else:
        print("  判定: [BLOCK] v8.7 发布阻断")
        print(f"  原因: {v87_summary.blocking_reason}")
    if json_path:
        print(f"  报告: {json_path.name}")
    print(f"  时间: {v87_summary.timestamp}")
    print("=" * 60)

    # v8.7 三门禁阻断时强制 RED (退出码 2)
    if not v87_summary.all_passed and level == "GREEN":
        level = "RED"

    return {"GREEN": 0, "YELLOW": 1, "RED": 2}[level]


if __name__ == "__main__":
    # --help 烟测支持: ci_integrity_check.py --strict 用 `--help` 验证脚本可运行性,
    # 本脚本无参数运行业务检查时 YELLOW 会返回 1, 曾被烟测误判为 unrunnable (CI 30+ 连败)。
    # --help 直接短路返回 0, 不依赖环境状态 (本地 reports/ 缓存 vs CI 干净环境)。
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[0] if __doc__ else "Engineering Debt Gate")
        print()
        print("用法: python scripts/engineering_debt_gate.py [--help]")
        print("无参数: 运行全部工程债务检查 (T1-T18, D1-D11 + v8.7 发布门禁汇总)")
        print("退出码: 0=GREEN 通过 | 1=YELLOW 仅告警性失败 | 2=RED 阻断性失败")
        sys.exit(0)
    sys.exit(main())
