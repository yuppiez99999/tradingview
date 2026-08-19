"""死代码归档脚本 — 将死代码文件移动到 _archive/dead_code/YYYY-MM-DD/

用法:
    # Dry-run 模式 (默认, 只打印不移动)
    python archive_dead_code.py

    # 执行归档 (实际移动文件)
    python archive_dead_code.py --execute

    # 仅归档高置信度 (排除中置信度 verify_* 脚本)
    python archive_dead_code.py --execute --high-only

设计原则:
    1. 默认 Dry-run, 必须显式 --execute 才移动
    2. 保留原始路径结构 (移到 _archive/dead_code/DATE/ 下保持相对路径)
    3. 生成归档清单 manifest.json (可回滚)
    4. 不删除 .gitignore 已忽略的 _archive/ 内容 (那是已归档的)
    5. 不删除 utils/_legacy/ (兼容层, 严禁删除)
    6. 不处理 qlib/ research/ qlib_env/ (第三方)

回滚:
    python archive_dead_code.py --rollback _archive/dead_code/2026-08-04/manifest.json
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ============================================================
# 死代码清单 (从 _dead_code_final.json 加载)
# ============================================================

DEAD_CODE_LIST = [
    # === 高置信度: 破损 import + 未被引用 (4 个) ===
    {"file": "tests\\unit\\test_gate_manager.py", "reason": "未被引用 + 破损 import (gate_manager)", "confidence": "HIGH", "category": "broken_unreferenced"},
    {"file": "tests\\unit\\test_predict_annual_return_struct.py", "reason": "未被引用 + 破损 import (predict_annual_return)", "confidence": "HIGH", "category": "broken_unreferenced"},
    {"file": "tests\\unit\\test_predict_dynamic.py", "reason": "未被引用 + 破损 import (gate_manager, predict_annual_return)", "confidence": "HIGH", "category": "broken_unreferenced"},
    {"file": "tests\\verify_gtja191_risk_report.py", "reason": "未被引用 + 破损 import (enhanced_risk_manager)", "confidence": "HIGH", "category": "broken_unreferenced"},

    # === 高置信度: 根目录临时脚本 (16 个) ===
    {"file": "_analyze_syspath.py", "reason": "根目录临时脚本 (_analyze_*)", "confidence": "HIGH", "category": "temp_script"},
    {"file": "_analyze_syspath_patterns.py", "reason": "根目录临时脚本 (_analyze_*)", "confidence": "HIGH", "category": "temp_script"},
    {"file": "_analyze_type_ignore.py", "reason": "根目录临时脚本 (_analyze_*)", "confidence": "HIGH", "category": "temp_script"},
    {"file": "_check_pkg_config.py", "reason": "根目录临时脚本 (_check_*)", "confidence": "HIGH", "category": "temp_script"},
    {"file": "_check_syntax.py", "reason": "根目录临时脚本 (_check_*)", "confidence": "HIGH", "category": "temp_script"},
    {"file": "_fix_merged_lines.py", "reason": "根目录临时脚本 (_fix_*)", "confidence": "HIGH", "category": "temp_script"},
    {"file": "_fix_root_type_ignore.py", "reason": "根目录临时脚本 (_fix_*)", "confidence": "HIGH", "category": "temp_script"},
    {"file": "_fix_type_ignore_batch.py", "reason": "根目录临时脚本 (_fix_*)", "confidence": "HIGH", "category": "temp_script"},
    {"file": "_fix_type_ignore_safe.py", "reason": "根目录临时脚本 (_fix_*)", "confidence": "HIGH", "category": "temp_script"},
    {"file": "_scan_bare_only.py", "reason": "根目录临时脚本 (_scan_*)", "confidence": "HIGH", "category": "temp_script"},
    {"file": "_scan_dir.py", "reason": "根目录临时脚本 (_scan_*)", "confidence": "HIGH", "category": "temp_script"},
    {"file": "_scan_subtypes.py", "reason": "根目录临时脚本 (_scan_*)", "confidence": "HIGH", "category": "temp_script"},
    {"file": "_scan_typeignore_syspath.py", "reason": "根目录临时脚本 (_scan_*)", "confidence": "HIGH", "category": "temp_script"},
    {"file": "_test_cninfo.py", "reason": "根目录临时脚本 (_test_*)", "confidence": "HIGH", "category": "temp_script"},
    {"file": "_test_fix.py", "reason": "根目录临时脚本 (_test_*)", "confidence": "HIGH", "category": "temp_script"},

    # === 高置信度: 本次扫描自身产生的临时脚本 (2 个, 扫描完成后可归档) ===
    {"file": "_scan_dead_code.py", "reason": "根目录临时脚本 (本次扫描自身)", "confidence": "HIGH", "category": "temp_script"},
    {"file": "_refine_dead_code.py", "reason": "根目录临时脚本 (本次扫描自身)", "confidence": "HIGH", "category": "temp_script"},

    # === 中置信度: 未被引用的 verify_* 脚本 (7 个, 需人工确认) ===
    {"file": "scripts\\verify_evolution_v2.py", "reason": "未被引用的 verify_* 脚本", "confidence": "MEDIUM", "category": "unreferenced_verify"},
    {"file": "scripts\\verify_p0_fixes.py", "reason": "未被引用的 verify_* 脚本", "confidence": "MEDIUM", "category": "unreferenced_verify"},
    {"file": "scripts\\verify_p1_fixes.py", "reason": "未被引用的 verify_* 脚本", "confidence": "MEDIUM", "category": "unreferenced_verify"},
    {"file": "scripts\\verify_path_config.py", "reason": "未被引用的 verify_* 脚本", "confidence": "MEDIUM", "category": "unreferenced_verify"},
    {"file": "scripts\\verify_v867_fixes.py", "reason": "未被引用的 verify_* 脚本", "confidence": "MEDIUM", "category": "unreferenced_verify"},
    {"file": "verify_b33_hedge_refactor.py", "reason": "未被引用 + hedge_engine_v59 模块已迁移到 utils/hedge_engine.py", "confidence": "MEDIUM", "category": "unreferenced_verify"},
    {"file": "verify_b35_lgb_refactor.py", "reason": "未被引用的 verify_* 脚本 (lgb 重构验证)", "confidence": "MEDIUM", "category": "unreferenced_verify"},
]


def archive(execute: bool = False, high_only: bool = False) -> None:
    """执行归档"""
    archive_date = date.today().isoformat()
    archive_root = ROOT / "_archive" / "dead_code" / archive_date

    # 过滤
    items = DEAD_CODE_LIST
    if high_only:
        items = [i for i in items if i["confidence"] == "HIGH"]


    if execute:
        archive_root.mkdir(parents=True, exist_ok=True)

    manifest = {
        "archive_date": archive_date,
        "archive_root": str(archive_root.relative_to(ROOT)),
        "mode": "EXECUTE" if execute else "DRY-RUN",
        "files": [],
    }

    moved = 0
    skipped = 0
    for item in items:
        src = ROOT / item["file"]
        if not src.exists():
            skipped += 1
            continue

        # 目标路径: 保持相对路径结构
        rel = item["file"]
        # 规范化路径分隔符
        rel_norm = rel.replace("\\", "/")
        dst = archive_root / rel_norm


        if execute:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))

        manifest["files"].append({
            "src": item["file"],
            "dst": str(dst.relative_to(ROOT)),
            "reason": item["reason"],
            "confidence": item["confidence"],
            "category": item["category"],
        })
        moved += 1

    # 保存 manifest (即使 dry-run 也保存, 供查看)
    if execute:
        manifest_path = archive_root / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)



def rollback(manifest_path: str) -> None:
    """从 manifest 恢复文件"""
    mf = ROOT / manifest_path
    if not mf.exists():
        return

    with open(mf, encoding="utf-8") as f:
        manifest = json.load(f)


    restored = 0
    for item in manifest["files"]:
        src = ROOT / item["dst"]
        dst = ROOT / item["src"]
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        restored += 1



def main():
    parser = argparse.ArgumentParser(description="死代码归档脚本")
    parser.add_argument("--execute", action="store_true", help="实际执行归档 (默认 dry-run)")
    parser.add_argument("--high-only", action="store_true", help="仅归档高置信度 (排除中置信度 verify_*)")
    parser.add_argument("--rollback", type=str, help="从 manifest 回滚")
    args = parser.parse_args()

    if args.rollback:
        rollback(args.rollback)
    else:
        archive(execute=args.execute, high_only=args.high_only)


if __name__ == "__main__":
    main()
