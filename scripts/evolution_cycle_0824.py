"""8/24 首次进化循环执行 (非实盘, 仅生成再平衡建议 + 200万ETF首笔定投计划).

执行步骤:
    1. 确认 Feature Flags 已启用 (USE_EVOLUTION_ORCHESTRATOR + USE_STRATEGY_EVALUATOR)
    2. 调用 EvolutionOrchestratorV2.run_cycle() 执行完整进化循环
    3. 提取 weight_adjustments 作为再平衡建议
    4. 生成 200万ETF首笔定投计划 (按 config/portfolio_200w_etf.yaml 权重分配)
    5. 汇总结果写入 每日报告归档/2026-08-24/

安全保证:
    - L3 进化层 HC-4 人工审批闸门: executed=False, 绝不自动执行
    - L2 影子验证: DSR < 阈值自动 rollback, 不进入实盘
    - 200万ETF定投: 仅生成计划文档, 不提交任何实盘订单
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yaml

from utils.infra.feature_flags import is_enabled

_REPORT_DIR = _ROOT / "每日报告归档" / "2026-08-24"
_REPORT_DIR.mkdir(parents=True, exist_ok=True)


def _section(title: str) -> str:
    return f"\n{'=' * 60}\n{title}\n{'=' * 60}"


def _check_feature_flags() -> dict:
    """检查 Feature Flags 状态."""
    flags = {
        "USE_EVOLUTION_ORCHESTRATOR": is_enabled("USE_EVOLUTION_ORCHESTRATOR"),
        "USE_STRATEGY_EVALUATOR": is_enabled("USE_STRATEGY_EVALUATOR"),
    }
    return flags


def _run_evolution_cycle() -> dict:
    """执行进化循环, 返回结果 dict."""
    result = {
        "status": "skipped",
        "level": "",
        "action": "",
        "executed": False,
        "reason": "",
        "weight_adjustments": {},
        "metrics_snapshot": {},
        "evaluator_report": {},
        "guard_decision": {},
        "timestamp": now_bj().isoformat(),
    }
    try:
        from utils.evolution.orchestrator import EvolutionOrchestratorV2

        orch = EvolutionOrchestratorV2()
        if not orch.enabled:
            result["reason"] = "Feature Flag USE_EVOLUTION_ORCHESTRATOR 未启用"
            return result

        cycle = orch.run_cycle()
        result.update(
            {
                "status": cycle.status,
                "level": cycle.level,
                "action": cycle.action,
                "executed": cycle.executed,
                "reason": cycle.reason,
                "weight_adjustments": cycle.weight_adjustments,
                "metrics_snapshot": cycle.metrics_snapshot,
                "evaluator_report": cycle.evaluator_report,
                "guard_decision": cycle.guard_decision,
                "timestamp": cycle.timestamp or now_bj().isoformat(),
            }
        )

        try:
            health = orch.get_loop_health_metrics()
            result["loop_health"] = health
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
            result["loop_health"] = {"error": str(e)}

    except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
        result["status"] = "error"
        result["reason"] = str(e)
    return result


def _generate_etf_dca_plan() -> dict:
    """生成 200万ETF首笔定投计划 (非实盘)."""
    config_path = _ROOT / "config" / "portfolio_200w_etf.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    core_etfs = config.get("core_holdings", [])
    satellite_etfs = config.get("satellite_holdings", [])
    total_capital = config.get("portfolio", {}).get("total_capital", 0)
    phase1 = config.get("execution_phases", {}).get("phase_1_build", {})

    monthly_dca = 120000  # 12万元/月
    core_weight_sum = sum(e.get("weight", 0) for e in core_etfs)

    core_allocations = []
    for etf in core_etfs:
        w = etf.get("weight", 0)
        alloc = monthly_dca * w / core_weight_sum if core_weight_sum > 0 else 0
        core_allocations.append(
            {
                "code": etf["code"],
                "name": etf["name"],
                "weight": round(w, 4),
                "allocation_cny": round(alloc, 2),
                "allocation_wan": round(alloc / 10000, 2),
            }
        )

    satellite_allocations = []
    satellite_weight_sum = sum(
        e.get("weight_base", e.get("weight", 0)) for e in satellite_etfs
    )
    satellite_dca = monthly_dca * 0.25  # 卫星仓占25%
    for etf in satellite_etfs:
        w = etf.get("weight_base", etf.get("weight", 0))
        alloc = (
            satellite_dca * w / satellite_weight_sum if satellite_weight_sum > 0 else 0
        )
        satellite_allocations.append(
            {
                "code": etf["code"],
                "name": etf["name"],
                "weight": round(w, 4),
                "allocation_cny": round(alloc, 2),
                "allocation_wan": round(alloc / 10000, 2),
            }
        )

    return {
        "config_file": str(config_path.name),
        "total_capital_wan": round(total_capital / 10000, 0),
        "monthly_dca_wan": round(monthly_dca / 10000, 0),
        "phase_1_period": phase1.get("period", "N/A"),
        "core_allocations": core_allocations,
        "satellite_allocations": satellite_allocations,
        "core_total_wan": round(sum(a["allocation_wan"] for a in core_allocations), 2),
        "satellite_total_wan": round(
            sum(a["allocation_wan"] for a in satellite_allocations), 2
        ),
        "note": "仅生成计划, 未提交实盘订单",
    }


def _build_report(flags: dict, cycle_result: dict, etf_plan: dict) -> str:
    """构建 Markdown 报告."""
    now = now_bj().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 8/24 首次进化循环执行报告",
        "",
        f"> 生成时间: {now}",
        "> 模式: 非实盘 (仅生成建议, 不提交订单)",
        "",
        "## 1. Feature Flags 状态",
        "",
        "| Flag | 状态 |",
        "|------|------|",
    ]
    for flag, enabled in flags.items():
        status = "✅ 已启用" if enabled else "❌ 未启用"
        lines.append(f"| `{flag}` | {status} |")

    lines.extend(
        [
            "",
            "## 2. 进化循环结果",
            "",
            f"- **状态**: `{cycle_result['status']}`",
            f"- **层级**: `{cycle_result['level'] or 'N/A'}`",
            f"- **动作**: `{cycle_result['action'] or 'N/A'}`",
            f"- **是否执行**: {cycle_result['executed']}",
            f"- **原因**: {cycle_result['reason'] or 'N/A'}",
            f"- **时间戳**: {cycle_result['timestamp']}",
            "",
        ]
    )

    wa = cycle_result.get("weight_adjustments", {})
    if wa:
        lines.extend(
            [
                "### 2.1 再平衡建议 (weight_adjustments)",
                "",
                "| 标的 | 权重乘子 |",
                "|------|----------|",
            ]
        )
        for code, mult in wa.items():
            lines.append(f"| {code} | {mult:.4f} |")
        lines.append("")
    else:
        lines.extend(
            [
                "### 2.1 再平衡建议",
                "",
                "本次循环未产生权重调整建议 (no_action 或无需进化).",
                "",
            ]
        )

    health = cycle_result.get("loop_health", {})
    if health:
        lines.extend(
            [
                "### 2.2 闭环健康度指标",
                "",
                "| 指标 | 值 |",
                "|------|----|",
            ]
        )
        for k, v in health.items():
            if isinstance(v, float):
                lines.append(f"| {k} | {v:.4f} |")
            else:
                lines.append(f"| {k} | {v} |")
        lines.append("")

    lines.extend(
        [
            "## 3. 200万ETF首笔定投计划 (非实盘)",
            "",
            f"- **配置文件**: `{etf_plan['config_file']}`",
            f"- **总资金**: {etf_plan['total_capital_wan']:.0f} 万元",
            f"- **月定投额**: {etf_plan['monthly_dca_wan']:.0f} 万元",
            f"- **Phase 1 周期**: {etf_plan['phase_1_period']}",
            f"- **备注**: {etf_plan['note']}",
            "",
            "### 3.1 核心仓分配 (75%)",
            "",
            "| 代码 | 名称 | 权重 | 金额(元) | 金额(万) |",
            "|------|------|------|----------|----------|",
        ]
    )
    for a in etf_plan["core_allocations"]:
        lines.append(
            f"| {a['code']} | {a['name']} | {a['weight']:.1%} | "
            f"{a['allocation_cny']:,.0f} | {a['allocation_wan']:.2f} |"
        )
    lines.append(f"| **合计** | | | | **{etf_plan['core_total_wan']:.2f}** |")

    lines.extend(
        [
            "",
            "### 3.2 卫星仓分配 (25%)",
            "",
            "| 代码 | 名称 | 权重 | 金额(元) | 金额(万) |",
            "|------|------|------|----------|----------|",
        ]
    )
    for a in etf_plan["satellite_allocations"]:
        lines.append(
            f"| {a['code']} | {a['name']} | {a['weight']:.1%} | "
            f"{a['allocation_cny']:,.0f} | {a['allocation_wan']:.2f} |"
        )
    lines.append(f"| **合计** | | | | **{etf_plan['satellite_total_wan']:.2f}** |")

    lines.extend(
        [
            "",
            "## 4. 安全声明",
            "",
            "- 本次执行为**非实盘模式**, 仅生成再平衡建议和定投计划.",
            "- L3 进化层受 HC-4 人工审批闸门保护, `executed=False`, 绝不自动执行.",
            "- L2 影子验证: DSR < 阈值时自动 rollback, 不进入实盘.",
            "- 200万ETF定投: 仅生成计划文档, 未提交任何实盘订单.",
            "- 后续: 完成全部计划后, 再接入实盘执行.",
            "",
        ]
    )

    return "\n".join(lines)


def main() -> None:
    print(_section("8/24 首次进化循环执行 (非实盘)"))

    print("\n[1] 检查 Feature Flags...")
    flags = _check_feature_flags()
    for flag, enabled in flags.items():
        status = "✅" if enabled else "❌"
        print(f"  {status} {flag} = {enabled}")

    if not flags["USE_EVOLUTION_ORCHESTRATOR"]:
        print("\n⚠️ USE_EVOLUTION_ORCHESTRATOR 未启用, 进化循环将返回 disabled.")

    print("\n[2] 执行进化循环 run_cycle()...")
    cycle_result = _run_evolution_cycle()
    print(f"  status: {cycle_result['status']}")
    print(f"  level: {cycle_result['level'] or 'N/A'}")
    print(f"  executed: {cycle_result['executed']}")
    print(f"  reason: {cycle_result['reason'] or 'N/A'}")
    wa = cycle_result.get("weight_adjustments", {})
    if wa:
        print(f"  weight_adjustments: {len(wa)} 个标的")
        for code, mult in list(wa.items())[:5]:
            print(f"    {code}: {mult:.4f}")
    else:
        print("  weight_adjustments: 无 (本次循环无需调整)")

    print("\n[3] 生成200万ETF首笔定投计划...")
    etf_plan = _generate_etf_dca_plan()
    print(f"  总资金: {etf_plan['total_capital_wan']:.0f} 万元")
    print(f"  月定投: {etf_plan['monthly_dca_wan']:.0f} 万元")
    print(
        f"  核心仓: {len(etf_plan['core_allocations'])} 只 → {etf_plan['core_total_wan']:.2f} 万"
    )
    print(
        f"  卫星仓: {len(etf_plan['satellite_allocations'])} 只 → {etf_plan['satellite_total_wan']:.2f} 万"
    )

    print("\n[4] 生成报告...")
    report_md = _build_report(flags, cycle_result, etf_plan)
    report_path = _REPORT_DIR / "进化循环_20260824.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)
    print(f"  报告: {report_path}")

    result_json = {
        "flags": flags,
        "cycle_result": cycle_result,
        "etf_plan": etf_plan,
        "generated_at": now_bj().isoformat(),
    }
    json_path = _REPORT_DIR / "进化循环_20260824.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result_json, f, ensure_ascii=False, indent=2, default=str)
    print(f"  JSON: {json_path}")

    print(_section("8/24 首次进化循环执行完成"))
    print("注意: 本次为非实盘模式, 未提交任何实盘订单.")


if __name__ == "__main__":
    main()
