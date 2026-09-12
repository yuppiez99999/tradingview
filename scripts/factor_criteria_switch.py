#!/usr/bin/env python
"""因子判定口径切换执行器 (P1-3 / Issue #13, 2026-09-12)

用途
----
`utils.factor_research.factor_discovery.FactorValidator` 当前为「**新口径 primary +
旧口径影子双跑**」。要真正"切口径"(即退役旧口径影子), 必须**人工确认 A/B 名单**后
再执行 —— 因为这是**研究基线重置事件**, 不是 bug 修复:

    A 类 (旧有效 / 新无效)  直接淘汰候选 —— 必须逐因子经济含义复核 (防误杀)
    B 类 (新不可判 / 旧可判) 处置 = **补样本后重评**, 不是淘汰

本脚本让"确认之后"的那一步变成**机械化 + 可审计 + fail-closed**, 而不是手改配置。

用法
----
    # 1) 干跑: 只校验名单可用性与完备性, 不做任何改动 (默认行为)
    python scripts/factor_criteria_switch.py \
        --list reports/operations/factor_criteria_shadow_2026-09-13.json

    # 2) 确认后正式执行 (需显式 --apply + --confirmed-by)
    python scripts/factor_criteria_switch.py \
        --list reports/operations/factor_criteria_shadow_2026-09-13.json \
        --apply --confirmed-by "安然 2026-09-14"

fail-closed 判据 (任一不满足即 RC=1, 绝不静默出"没有要淘汰的")
--------------------------------------------------------------
1. 名单文件必须存在且为 JSON;
2. `shadow.available` 必须为 true —— **空名单最危险**, 会被误读为"无需淘汰";
3. A/B 名单结构必须齐全 (含计数), 且 `A + B == total_candidates`;
4. `--apply` 必须同时给出 `--confirmed-by` (确认责任人), 否则拒绝执行;
5. 切换前后写决策记录 (含名单规模、确认人、口径快照), 供审计回溯。
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

_CONFIG_PATH = _PROJECT_ROOT / "config" / "risk_thresholds.yaml"
_OPS_DIR = _PROJECT_ROOT / "reports" / "operations"

_A_KEY = "legacy_effective_new_ineffective"
_B_KEY = "new_undecidable_but_legacy_decidable"
_BOTH_KEY = "undecidable_in_both"


def load_and_validate(list_path: Path) -> tuple[dict | None, list[str]]:
    """加载并校验名单; 返回 ``(shadow_or_None, 问题清单)``。

    fail-closed: 任何问题都返回 ``(None, problems)``, 调用方据此拒绝执行。
    """
    problems: list[str] = []
    if not list_path.exists():
        return None, [f"名单文件不存在: {list_path}"]
    try:
        payload = json.loads(list_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return None, [f"名单文件不可解析: {exc}"]

    shadow = payload.get("shadow") if isinstance(payload, dict) else None
    if not isinstance(shadow, dict):
        return None, ["缺 shadow 块 (非 factor_criteria_shadow_report.py 产物?)"]

    if not shadow.get("available", False):
        problems.append(
            "shadow.available=false —— 名单不可用: "
            f"{shadow.get('reason', '未知原因')}。"
            "空/不可用名单不得被当作'无需淘汰' (fail-closed)"
        )
        return None, problems

    a_list = shadow.get(_A_KEY)
    b_list = shadow.get(_B_KEY)
    if not isinstance(a_list, list) or not isinstance(b_list, list):
        problems.append(f"名单结构缺 {_A_KEY} / {_B_KEY} (须为数组)")
        return None, problems

    total = shadow.get("total_candidates", len(a_list) + len(b_list))
    if int(total) != len(a_list) + len(b_list):
        problems.append(
            f"total_candidates({total}) != A({len(a_list)}) + B({len(b_list)}) —— 名单不完整"
        )
    if problems:
        return None, problems
    return shadow, []


def _current_criteria() -> dict:
    from utils.factor_research.factor_discovery import FactorValidator
    from utils.risk_thresholds import resolve_config

    cfg, source = resolve_config("factor_validation")
    return {
        "min_samples": FactorValidator.MIN_SAMPLES,
        "ic_effective_threshold": FactorValidator.IC_EFFECTIVE_THRESHOLD,
        "ir_effective_threshold": FactorValidator.IR_EFFECTIVE_THRESHOLD,
        "score_mode": FactorValidator.SCORE_MODE,
        "shadow_legacy": FactorValidator.SHADOW_LEGACY,
        "source": source.describe(),
        "file_cfg": cfg,
    }


def _apply_switch(yaml_text: str) -> tuple[str, list[str]]:
    """把 ``shadow_legacy: true`` 改为 false (退役旧口径影子)。返回 (新文本, 变更说明)。"""
    changes: list[str] = []
    lines = yaml_text.splitlines(keepends=True)
    in_factor_section = False
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("factor_validation:"):
            in_factor_section = True
            continue
        if in_factor_section and stripped and not line.startswith((" ", "\t")):
            in_factor_section = False  # 进入下一顶层段
        if in_factor_section and stripped.startswith("shadow_legacy:"):
            value = stripped.split(":", 1)[1].strip().split("#")[0].strip()
            if value.lower() == "false":
                changes.append("shadow_legacy 已是 false (幂等, 无需改动)")
                return yaml_text, changes
            lines[idx] = line.replace("shadow_legacy: true", "shadow_legacy: false")
            if "shadow_legacy: false" not in lines[idx]:
                # 容错: 值非标准 true/True
                head = line.split("shadow_legacy:", 1)[0]
                tail = "\n"
                lines[idx] = f"{head}shadow_legacy: false{tail}"
            changes.append("shadow_legacy: true → false (旧口径影子退役)")
            return "".join(lines), changes
    changes.append("⚠ 未在文件中找到 factor_validation.shadow_legacy 行 —— 未改动")
    return yaml_text, changes


def _render_switch_record(
    shadow: dict, criteria: dict, confirmed_by: str, changes: list[str], applied: bool
) -> str:
    a_list = shadow[_A_KEY]
    b_list = shadow[_B_KEY]
    lines = [
        "# 因子判定口径切换记录 (P1-3 / Issue #13)",
        "",
        f"**生成时间**: {now_bj().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**确认人**: {confirmed_by}",
        f"**执行状态**: {'✅ 已执行 (--apply)' if applied else '🟡 干跑 (未改动任何文件)'}",
        f"**名单来源**: `{shadow.get('source_json', shadow.get('source', '未记录'))}`",
        "",
        "## 口径快照 (切换前)",
        "",
        f"- 新口径 (primary): n>={criteria['min_samples']} / |IC|>={criteria['ic_effective_threshold']} "
        f"/ |IR|>={criteria['ir_effective_threshold']} / score={criteria['score_mode']}",
        f"- 影子退役前: `shadow_legacy={criteria['shadow_legacy']}`",
        f"- 事实源: {criteria['source']}",
        "",
        "## 名单规模 (切换冲击面)",
        "",
        f"- A 类 (旧有效 / 新无效, 直接淘汰候选): **{len(a_list)}**",
        f"- B 类 (新不可判 / 旧可判, 处置=补样本重评): **{len(b_list)}**",
        f"- 合计冲击面: **{len(a_list) + len(b_list)}**",
        "",
        "## 改动",
        "",
    ]
    lines += [f"- {c}" for c in changes] or ["- (无)"]
    lines += [
        "",
        "## 后续必做 (切换不等于完成)",
        "",
        "1. 同步更新 `tests/unit/test_factor_discovery_unit.py` 中对旧口径影子行为的断言",
        "   (当前断言锁的是「新口径 primary + 旧口径影子并存」);",
        "2. A 类名单逐因子经济含义复核结论留档 (统计量之外的理由);",
        "3. B 类名单转入「补样本重评」待办, **不得**混入淘汰。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="因子判定口径切换执行器 (fail-closed)")
    parser.add_argument("--list", required=True, help="影子对照名单 JSON (factor_criteria_shadow_*.json)")
    parser.add_argument("--apply", action="store_true", help="正式执行切换 (默认仅干跑)")
    parser.add_argument("--confirmed-by", default=None, help="确认责任人 (--apply 时必填)")
    parser.add_argument("--record-dir", default=str(_OPS_DIR), help="切换记录输出目录")
    args = parser.parse_args()

    list_path = Path(args.list)
    if not list_path.is_absolute():
        list_path = _PROJECT_ROOT / list_path

    shadow, problems = load_and_validate(list_path)
    if shadow is None:
        print("[FAIL] 名单校验未通过, 拒绝执行 (fail-closed):")
        for p in problems:
            print(f"  - {p}")
        return 1

    criteria = _current_criteria()
    changes: list[str] = []
    applied = False

    if args.apply:
        if not args.confirmed_by:
            print("[FAIL] --apply 必须同时给出 --confirmed-by (确认责任人), 否则拒绝执行")
            return 1
        if criteria["shadow_legacy"] is False:
            print("[INFO] shadow_legacy 已为 false (口径已切换), 幂等跳过文件改动")
            changes = ["shadow_legacy 已是 false (幂等)"]
        else:
            original = _CONFIG_PATH.read_text(encoding="utf-8")
            updated, changes = _apply_switch(original)
            if updated != original:
                _CONFIG_PATH.write_text(updated, encoding="utf-8", newline="\n")
        applied = True
    else:
        changes = ["干跑: 未改动 config/risk_thresholds.yaml (加 --apply --confirmed-by 执行)"]

    record = _render_switch_record(shadow, criteria, args.confirmed_by or "(未提供)", changes, applied)
    record_dir = Path(args.record_dir)
    record_dir.mkdir(parents=True, exist_ok=True)
    date_str = now_bj().strftime("%Y-%m-%d")
    record_path = record_dir / f"factor_criteria_switch_record_{date_str}.md"
    record_path.write_text(record, encoding="utf-8")

    print(record)
    print(f"\n[输出] {record_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
