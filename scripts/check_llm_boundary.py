#!/usr/bin/env python
"""LLM 权限边界检测 (AUTO-2, 检测性门禁 — 非阻断).

Production Invariant I-01: 任何 LLM 不得直接产生 execution order。
本脚本扫描执行/下单链路模块, 若其直接 import LLM 模块则报告违例
(提示人工核对是否绕过 decision_gate)。退出码恒 0 (检测性, 转阻断须用户拍板)。

逻辑:
  - 执行链文件集合: automated_execution_system / ai_decision.execution_bridge /
    utils/execution/* / T15 LiveOrderExecutor / broker 适配器
  - LLM 模块标识: glm5* / ai_hedge_fund / llm_evolution / llm_gateway /
    llm_client / litellm
  - AST 级 import 扫描 (含 from X import Y 与 import X), 单向判定
    "执行链文件 → LLM 模块"; LLM 模块 import 风控接口类型不违例。

用法:
  python scripts/check_llm_boundary.py          # 检测 + 报告 (exit 0)
  python scripts/check_llm_boundary.py --json   # JSON 输出
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from datetime import UTC
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 执行/下单链路文件 (相对项目根; 目录 → 递归扫描其下全部 .py)
EXECUTION_CHAIN: tuple[str, ...] = (
    "automated_execution_system.py",
    "ai_decision/execution_bridge.py",
    "ai_decision/decision_gate.py",
    "utils/execution/",
    "utils/risk/",
)

# LLM 模块标识 (顶层包/模块名前缀)
# 2026-09-11 (item 12): 补上 utils.alpha.llm 多 provider 栈 — 它是真实 LLM 入口
# (deepseek/doubao/ds4/glm/ollama/siliconflow + consensus + audit), 之前不在清单
# 属门禁盲区; utils/local_llm.py 与 utils/llm_finetune.py 已归档 (零生产消费方)。
LLM_MODULE_PREFIXES: tuple[str, ...] = (
    "utils.glm5_decision_engine",
    "utils.glm5_client",
    "ai_hedge_fund",
    "quant_modules.ai_hedge_fund",
    "utils.llm_evolution",
    "utils.llm_gateway",
    "utils.llm_client",
    "utils.alpha.llm",
    "litellm",
)


def _is_llm_module(module_name: str) -> bool:
    return any(module_name == p or module_name.startswith(p + ".") for p in LLM_MODULE_PREFIXES)


def _collect_chain_files() -> list[Path]:
    files: list[Path] = []
    for entry in EXECUTION_CHAIN:
        p = PROJECT_ROOT / entry
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(sorted(p.rglob("*.py")))
    return files


def _imports_of(tree: ast.AST) -> list[str]:
    """AST 提取全部 import 的模块名 (import X / from X import Y)."""
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def scan() -> list[dict]:
    """返回违例列表: [{file, llm_module, note}]."""
    violations: list[dict] = []
    for f in _collect_chain_files():
        if not f.exists():
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, OSError, UnicodeDecodeError) as e:
            violations.append(
                {"file": str(f.relative_to(PROJECT_ROOT)), "llm_module": "", "note": f"解析失败: {e}"}
            )
            continue
        rel = str(f.relative_to(PROJECT_ROOT))
        for mod in _imports_of(tree):
            if _is_llm_module(mod):
                violations.append(
                    {
                        "file": rel,
                        "llm_module": mod,
                        "note": "执行链直接 import LLM 模块 — 人工核对是否绕过 decision_gate "
                        "(LLM 输出应为建议/报告, 执行须经 decision_gate → execution_bridge)",
                    }
                )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM 权限边界检测 (检测性, 非阻断)")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    violations = scan()
    ts = datetime_now_iso()
    if args.json:
        print(
            json.dumps(
                {"checked_at": ts, "n_files": len(_collect_chain_files()), "violations": violations},
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print("=" * 70)
        print("LLM 权限边界检测 (I-01, 检测性门禁 — 非阻断)")
        print("=" * 70)
        print(f"  扫描执行链文件: {len(_collect_chain_files())} 个")
        if violations:
            for v in violations:
                print(f"  [!] {v['file']} -> {v['llm_module'] or '(解析失败)'}")
                print(f"      {v['note']}")
        else:
            print("  [OK] 无违例 — 执行链未直接 import 任何 LLM 模块")
        print("-" * 70)
        print(f"  违例数: {len(violations)} (检测性输出, 不阻断; 转阻断须用户拍板)")
        print(f"  时间: {ts}")
        print("=" * 70)
    return 0


def datetime_now_iso() -> str:
    # 审计时间戳用 UTC (带时区); 2026-09-11 item 12 随本脚本纳入版本库时顺带修 DTZ005
    from datetime import datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


if __name__ == "__main__":
    sys.exit(main())
