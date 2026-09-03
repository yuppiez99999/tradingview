#!/usr/bin/env python
"""LLM→执行 边界检测器 (AUTO-2, 检测性非阻断).

扫描三条 LLM 运行路径中是否出现「LLM 输出直连 broker/下单」的边界模糊信号，
配合 `docs/LLM权限边界规范.md` 使用。本工具是 *检测性* 辅助，不修改源码，
发现疑似边界问题仅输出 WARNING，返回码恒为 0（不阻断合并），交由人工判定。

核心边界铁律 (docs/LLM权限边界规范.md §0)：
  LLM 输出不得绕过硬风控直接触发真实下单。
  任何新增调用链都必须把 LLM 输出挡在 decision_gate 硬风控门之前。

判定口径 (启发式，AST 函数调用级，需人工二次确认)：
  在同一 .py 内，若同时出现：
    ① LLM 侧「实际下单/执行动作」调用 (place_order/submit_order/broker.execute/
       order_router/executor 等下单指令构造)
  且该文件**未引用 decision_gate / execution_bridge** 硬风控护栏
  → 判定为疑似「LLM→执行直连」WARN。

  报告 / 建议路径 (report/advice/mode/dashboard/eod_review) 若含下单动作调用
  → 同样 WARN。

  说明：纯 import 级共现不告警 (同仓库既有多 infra 文件天然共存)，仅对
  同一文件内出现「实质下单动作函数调用」与「LLM 上下文」做告警，降低噪音。

用法:
    python scripts/check_llm_exec_boundary.py [dir ...] [--json out.json]
    --json: 同时输出机器可读 JSON (供 CI/巡检消费)
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DIRS = [
    "ai_decision",
    "utils",
    "quant_modules/ai_hedge_fund",
    "15_每日工作流",
    "cli/modes",
    "quant_modules",
]

# ---- 真实下单/执行动作调用 (函数/方法名信号) ----
# 仅匹配“实质下达/执行交易指令”的动作，避免命中 ThreadPoolExecutor.submit /
# concurrent.futures 等非交易 submit。
_EXEC_CALL_SIG = re.compile(
    r"(?:place_order|submit_order|submit_orders?\(|send_order|send_orders?\(|"
    r"create_order|build_order|router\.route|execute_order|broker\.|"
    r"\.submit\(.*order|order_router|submit_to_broker|qmt.*submit|"
    r"trade_executor|rebalance_order_executor|order_executor)",
    re.IGNORECASE,
)
# ---- LLM provider / 决策链 上下文信号 (import 级) ----
_LLM_IMPORT_SIG = re.compile(
    r"(llm|glm5|deepseek|moonshot|claude|ai_decision|ModelRouter|FinanceAgentOrch|"
    r"BaseProvider|LlmClient|LlmProvider)",
    re.IGNORECASE,
)
# ---- 硬风控护栏 import (放行标记: 引用这些说明已过门) ----
_GUARD_SIG = re.compile(
    r"(decision_gate|execution_bridge|execution_risk|run_hard_risk|apply_mode)",
    re.IGNORECASE,
)
# ---- 报告/建议路径文件 ----
_REPORT_PATH_SIG = re.compile(r"(report|mode|advice|dashboard|eod_review|review)", re.IGNORECASE)


def _imports_contain(src_imports: list[str], pat: re.Pattern) -> bool:
    return any(pat.search(name) for name in src_imports)


def _file_has_exec_call(tree: ast.AST) -> bool:
    """检查是否调用真实下单/执行动作函数 (排除非交易的 .submit)。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            call_name = ""
            if isinstance(fn, ast.Attribute):
                call_name = fn.attr
            elif isinstance(fn, ast.Name):
                call_name = fn.id
            else:
                continue
            if _EXEC_CALL_SIG.search(call_name):
                return True
    return False


def _collect_imports(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    # 带模块前缀，便于匹配 decision_gate
                    names.append(f"{node.module}.{alias.name}" if node.module else alias.name)
    return names


def scan_file(path: Path) -> list[dict]:
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src, filename=str(path))
    except (SyntaxError, OSError):
        return []

    imports = _collect_imports(tree)
    has_llm_import = _imports_contain(imports, _LLM_IMPORT_SIG)
    has_guard_import = _imports_contain(imports, _GUARD_SIG)
    has_exec_call = _file_has_exec_call(tree)
    fname = path.name.lower()

    warnings: list[dict] = []

    # 场景 1: LLM import + 真实下单动作调用, 但无硬风控护栏引用 -> 疑似直连
    if has_llm_import and has_exec_call and not has_guard_import:
        warnings.append(
            {
                "file": str(path),
                "rule": "llm_with_order_call_no_guard",
                "severity": "WARN",
                "detail": "文件含 LLM provider import 且出现实质下单/执行动作调用，"
                "但未引用 decision_gate/execution_bridge 硬风控护栏。请人工核查"
                "是否为 LLM→执行直连，若属业务需要请走 decision_gate。",
            }
        )

    # 场景 2: 报告/建议路径文件含下单动作调用 (报告路径不应构造订单)
    if _REPORT_PATH_SIG.search(fname) and has_exec_call:
        warnings.append(
            {
                "file": str(path),
                "rule": "report_path_with_order_call",
                "severity": "WARN",
                "detail": "报告/建议路径文件出现实质下单动作调用。LLM 报告路径"
                "不应构造订单，请确认是否越界。",
            }
        )

    return warnings


def scan_dirs(dirs: list[Path]) -> list[dict]:
    all_warn: list[dict] = []
    for d in dirs:
        if not d.exists():
            continue
        for path in d.rglob("*.py"):
            if any(part.startswith("__pycache__") for part in path.parts):
                continue
            all_warn.extend(scan_file(path))
    return all_warn


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dirs", nargs="*", help="要扫描的目录 (默认若干核心目录)")
    ap.add_argument("--json", metavar="out.json", help="同时输出机器可读 JSON")
    ap.add_argument("--selftest", action="store_true", help="内建自测 (无需 pytest)")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    dirs = [Path(x) for x in args.dirs] if args.dirs else [_PROJECT_ROOT / x for x in _DEFAULT_DIRS]
    warnings = scan_dirs(dirs)

    if args.json:
        out = {
            "tool": "check_llm_exec_boundary",
            "scan_dirs": [str(d) for d in dirs],
            "warning_count": len(warnings),
            "warnings": warnings,
        }
        Path(args.json).write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if warnings:
        print(f"[LLM-BOUNDARY] 检测到 {len(warnings)} 处疑似 LLM→执行 边界告警 (WARN, 非阻断):")
        for w in warnings:
            print(f"  - {w['file']} [{w['rule']}] {w['detail']}")
        print("[LLM-BOUNDARY] 以上为检测性告警，请人工按 docs/LLM权限边界规范.md 复核。")
    else:
        print("[LLM-BOUNDARY] 通过 - 未检测到疑似 LLM→执行 边界越界。")
    # 恒返回 0 (检测性非阻断)
    return 0




# ============================================================
# 内建自测 (--selftest, 无需 pytest)
# ============================================================

_SAMPLE_LLM_EXEC = '''
from utils.llm_client import chat            # LLM provider
def decide_and_trade(symbol):
    advice = chat(f"analyze {symbol}")
    place_order(symbol=symbol, qty=100)      # 直接下单 -> 应被检出
'''

_SAMPLE_SAFE = '''
from ai_decision.decision_gate import run_hard_risk, apply_mode  # 硬风控护栏
from utils.llm_client import chat
def run():
    d = chat("analyze")
    # 经 decision_gate 护栏, 不应被误报
    return apply_mode(d, run_hard_risk(d))
'''


def _selftest() -> int:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "bad_llm_exec.py"
        bad.write_text(_SAMPLE_LLM_EXEC, encoding="utf-8")
        safe = Path(tmp) / "safe_llm_exec.py"
        safe.write_text(_SAMPLE_SAFE, encoding="utf-8")

        bad_warns = scan_file(bad)
        safe_warns = scan_file(safe)

        ok = True
        if not any(w["rule"] == "llm_with_order_call_no_guard" for w in bad_warns):
            print("[SELFTEST] FAIL: 未检出 LLM+下单无护栏 示例")
            ok = False
        if safe_warns:
            print(f"[SELFTEST] FAIL: 经 decision_gate 护栏示例被误报: {safe_warns}")
            ok = False

    if ok:
        print("[SELFTEST] PASS: LLM+下单越界可检出 / 经护栏文件不误报")
        return 0
    return 1

if __name__ == "__main__":
    sys.exit(main())
