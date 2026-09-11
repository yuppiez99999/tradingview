#!/usr/bin/env python
"""因子判定口径影子对照报告 — 输出「口径切换淘汰候选名单」(P1-3 / Issue #13)

背景
----
`utils.factor_research.factor_discovery.FactorValidator` 已改为
「新口径 primary + 旧口径影子双跑」。本脚本把影子对照**落成一份可交付的报告**,
供人工确认淘汰名单后再切换口径 (切换是研究基线重置事件, 不静默执行)。

用法
----
    # 1) 只读现有报告/JSON 产物, 汇总影子名单 (离线, 无网络)
    python scripts/factor_criteria_shadow_report.py --from-json reports/.../factor_discovery_*.json

    # 2) 在本地缓存的标的上重算一遍并直接出报告
    python scripts/factor_criteria_shadow_report.py --universe local_cached --start 2023-01-01

    # 3) 只打印口径差异与名单(不调用数据源) —— 用于确认脚本可用
    python scripts/factor_criteria_shadow_report.py --criteria-only

输出
----
    reports/operations/factor_criteria_shadow_<date>.md   (人读名单)
    reports/operations/factor_criteria_shadow_<date>.json (机读名单)

口径说明 (唯一事实源: config/risk_thresholds.yaml `factor_validation`)
--------------------------------------------------------------------
    新口径: n>=60, |IC|>=0.03, |IR|>=0.5, 评分带符号
    旧口径: n>=5,  |IC|>=0.02, |IR|>=0.2, 评分三项 abs

名单两个维度 (缺一会漏人):
    A 类 旧有效 / 新无效        —— 两口径都能判定, 直接进淘汰候选
    B 类 新口径不可判但旧口径可判 —— 样本数在 [5, 60) 区间, 报告里没有任何 IC 输出,
                                    只按 A 类出名单会漏掉; 处置是"补样本重评"而非淘汰
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from utils.datetime_utils import now_bj

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_OPS_DIR = _PROJECT_ROOT / "reports" / "operations"


def _criteria_snapshot() -> dict:
    """读取当前生效的判定口径 (新口径来自唯一事实源, 旧口径为影子常量)。"""
    from utils.factor_research.factor_discovery import FactorValidator
    from utils.risk_thresholds import ThresholdSource, resolve_config

    cfg, source = resolve_config("factor_validation")
    return {
        "new": {
            "min_samples": FactorValidator.MIN_SAMPLES,
            "ic_effective_threshold": FactorValidator.IC_EFFECTIVE_THRESHOLD,
            "ir_effective_threshold": FactorValidator.IR_EFFECTIVE_THRESHOLD,
            "score_mode": FactorValidator.SCORE_MODE,
        },
        "legacy": {
            "min_samples": FactorValidator.LEGACY_MIN_SAMPLES,
            "ic_effective_threshold": FactorValidator.LEGACY_IC_EFFECTIVE_THRESHOLD,
            "ir_effective_threshold": FactorValidator.LEGACY_IR_EFFECTIVE_THRESHOLD,
        },
        "shadow_legacy_enabled": FactorValidator.SHADOW_LEGACY,
        "config_source": isinstance(source, ThresholdSource)
        and source.describe()
        or str(source),
        "config_from_file": bool(getattr(source, "from_file", False)),
        "config_missing_keys": list(getattr(source, "missing_keys", ()) or ()),
    }


def _build_from_json(path: Path) -> dict:
    """从 factor_discovery JSON 产物里提取影子对照块 (无网络, 只读)。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    shadow = data.get("validation_shadow")
    if shadow is None:
        return {
            "available": False,
            "reason": (
                "该 JSON 产物不含 validation_shadow 字段 —— 生成于 P1-3 影子对照落地之前, "
                "请用当前代码重跑 run_discovery 后再取名单"
            ),
            "source_json": str(path),
        }
    shadow["available"] = True
    shadow["source_json"] = str(path)
    return shadow


def _run_discovery(args) -> dict:
    """重跑一次因子挖掘, 并直接返回影子对照块 (需要行情数据源可用)。"""
    from utils.factor_research import factor_discovery as fd

    out_dir = Path(args.output_dir) if args.output_dir else (_OPS_DIR / "factor_shadow")
    out_dir.mkdir(parents=True, exist_ok=True)

    report = fd.run_discovery(
        universe=args.universe,
        codes=args.codes,
        start_date=args.start,
        end_date=args.end,
        output_dir=str(out_dir),
    )
    end_date = report.end_date
    json_path = out_dir / f"factor_discovery_{report.start_date}_{end_date}.json"
    if not json_path.exists():
        return {
            "available": False,
            "reason": f"未找到产出 JSON: {json_path}",
        }
    return _build_from_json(json_path)


def _render_markdown(payload: dict, criteria: dict, date_str: str) -> str:
    lines: list[str] = []
    lines.append("# 因子判定口径影子对照 · 淘汰候选名单")
    lines.append("")
    lines.append(f"**生成时间**: {now_bj().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**口径事实源**: `{criteria['config_source']}`")
    if not criteria["config_from_file"]:
        lines.append(
            "> ⚠️ 口径未从 `config/risk_thresholds.yaml` 读到, 当前为**模块内默认值** "
            "—— 请确认是否为预期, 否则名单口径可能与配置不一致。"
        )
    lines.append("")
    lines.append("| 口径 | 样本门槛 | \\|IC\\| | \\|IR\\| | 评分 |")
    lines.append("|---|---|---|---|---|")
    n, lg = criteria["new"], criteria["legacy"]
    lines.append(
        f"| **新 (primary)** | {n['min_samples']} | {n['ic_effective_threshold']} | "
        f"{n['ir_effective_threshold']} | `{n['score_mode']}` |"
    )
    lines.append(
        f"| 旧 (影子, 仅报告) | {lg['min_samples']} | {lg['ic_effective_threshold']} | "
        f"{lg['ir_effective_threshold']} | abs |"
    )
    lines.append("")
    lines.append(
        "> 本报告**只输出名单, 不改变任何判定**, 也不写因子库。"
        "口径切换属研究基线重置事件, 须人工确认名单后执行。"
    )
    lines.append("")

    if not payload.get("available", False):
        lines.append("## ⛔ 无法生成名单")
        lines.append("")
        lines.append(f"- 原因: {payload.get('reason', '未知')}")
        if payload.get("source_json"):
            lines.append(f"- 输入: `{payload['source_json']}`")
        lines.append("")
        return "\n".join(lines)

    a_list = payload.get("legacy_effective_new_ineffective", [])
    b_list = payload.get("new_undecidable_but_legacy_decidable", [])
    both = payload.get("undecidable_in_both", [])
    total = payload.get("total_candidates", len(a_list) + len(b_list))

    lines.append("## 冲击面总览")
    lines.append("")
    lines.append(f"- **口径切换影响因子总数: {total}**")
    lines.append(f"- A 类 (旧有效 / 新无效): **{len(a_list)}** —— 直接淘汰候选")
    lines.append(
        f"- B 类 (新口径不可判, 旧口径可判): **{len(b_list)}** —— 补足样本后重评, 不直接淘汰"
    )
    lines.append(
        f"- 两口径均不可判 (n < {lg['min_samples']}): {len(both)} —— 与口径切换无关"
    )
    lines.append("")
    lines.append("> **为什么必须分开列**: B 类因子在新口径下**没有任何 IC/评分输出**, "
                 "因此不会出现在任何因子表格里。仅按「旧有效 & 新无效」出名单会把它们整体漏掉。")
    lines.append(">")
    lines.append("> 机器可读名单在产出 JSON 的 `validation_shadow` 块: "
                 "A 类 = `legacy_effective_new_ineffective`, "
                 "B 类 = `new_undecidable_but_legacy_decidable`, "
                 "两类均不可判 = `undecidable_in_both` (仅因子名)。")
    lines.append("")

    lines.append("## A 类 · 旧有效 / 新无效 (直接淘汰候选)")
    lines.append("")
    if a_list:
        lines.append("| 因子名 | 类别 | IC均值 | IC_IR | 样本 | 新评分 | 旧评分 |")
        lines.append("|--------|------|--------|-------|------|--------|--------|")
        for r in a_list:
            lines.append(
                f"| {r['factor_name']} | {r['category']} | {r['ic_mean']:.4f} | "
                f"{r['ic_ir']:.3f} | {r['n_samples']} | "
                f"{r['new_score']:.1f} | {r['legacy_score']:.1f} |"
            )
        lines.append("")
    else:
        lines.append("_无_")
        lines.append("")

    lines.append("## B 类 · 新口径不可判 / 旧口径可判 (补样本重评)")
    lines.append("")
    if b_list:
        lines.append("| 因子名 | 类别 | 样本数 | 距新口径缺口 |")
        lines.append("|--------|------|--------|--------------|")
        for r in b_list:
            lines.append(
                f"| {r['factor_name']} | {r['category']} | {r['n_samples']} | "
                f"需补 {r['sample_gap']} 日 |"
            )
        lines.append("")
    else:
        lines.append("_无_")
        lines.append("")

    lines.append("## 执行前检查单")
    lines.append("")
    lines.append("1. 确认 A 类名单无误杀 (逐因子的经济含义复核, 不只看统计量)")
    lines.append("2. 确认 B 类处置方式 = 补样本, 而非随 A 类一起淘汰")
    lines.append("3. 切口径时**同步**更新 `tests/unit/test_factor_discovery_unit.py` 的断言")
    lines.append("4. 切口径后 `shadow_legacy` 可保留一轮, 便于回滚对照")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="因子判定口径影子对照报告")
    parser.add_argument("--from-json", default=None, help="从既有 JSON 产物提取名单 (离线)")
    parser.add_argument("--universe", default="local_cached", help="标的池 (需重跑时使用)")
    parser.add_argument("--codes", default=None, help="自定义代码 (逗号分隔)")
    parser.add_argument("--start", default="2023-01-01", help="起始日期")
    parser.add_argument("--end", default=None, help="结束日期")
    parser.add_argument("--output-dir", default=None, help="产出目录 (默认 reports/operations/factor_shadow)")
    parser.add_argument("--criteria-only", action="store_true", help="只打印口径与名单生成能力, 不取数")
    args = parser.parse_args()

    criteria = _criteria_snapshot()
    date_str = now_bj().strftime("%Y-%m-%d")

    if args.criteria_only:
        print("当前生效口径:")
        print(json.dumps(criteria, ensure_ascii=False, indent=2))
        return 0

    if args.from_json:
        payload = _build_from_json(Path(args.from_json))
    else:
        payload = _run_discovery(args)

    md = _render_markdown(payload, criteria, date_str)

    _OPS_DIR.mkdir(parents=True, exist_ok=True)
    md_path = _OPS_DIR / f"factor_criteria_shadow_{date_str}.md"
    md_path.write_text(md, encoding="utf-8")
    json_path = _OPS_DIR / f"factor_criteria_shadow_{date_str}.json"
    json_path.write_text(
        json.dumps(
            {"generated_at": date_str, "criteria": criteria, "shadow": payload},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(md)
    print(f"\n[输出] {md_path}")
    print(f"[输出] {json_path}")
    return 0 if payload.get("available", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
