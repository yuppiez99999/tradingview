"""MMR 代码质量+安全扫描: 按风险优先级对核心模块做 LLM 语义深扫

用法:
    # 扫描 P0 核心模块 (交易/风控/AI决策), correctness + adversarial 双 lens
    python scripts/mmr_code_scan.py --priority p0 --lens all

    # 扫描指定目录
    python scripts/mmr_code_scan.py --target utils/risk/ --lens correctness

    # 扫描单个文件
    python scripts/mmr_code_scan.py --file v8.3_institutional/dual_model_judge.py --lens adversarial

    # 限制扫描文件数 (按风险排序取 top N)
    python scripts/mmr_code_scan.py --priority p0 --lens all --limit 5

    # 仅列出待扫文件, 不实际调用 LLM (dry-run)
    python scripts/mmr_code_scan.py --priority p0 --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

RISK_PRIORITY: dict[str, list[str]] = {
    "p0": [
        "v8.3_institutional",
        "ai_decision",
    ],
    "p1": [
        "utils/risk",
        "utils/backtest",
        "ms_strategy/src",
    ],
    "p2": [
        "utils/alpha",
        "utils/infra",
        "scripts",
    ],
}

SKIP_DIRS = {
    "__pycache__", ".git", "external", "temp", "qlib_env",
    "tests", "research/references", "node_modules", ".venv",
}

MAX_CHARS_PER_FILE = 8000


def collect_files(targets: list[str], limit: int | None = None) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        tpath = PROJECT_ROOT / target
        if tpath.is_file() and tpath.suffix == ".py":
            files.append(tpath)
            continue
        if tpath.is_dir():
            for f in tpath.rglob("*.py"):
                if any(skip in str(f) for skip in SKIP_DIRS):
                    continue
                files.append(f)
    files = sorted(set(files), key=lambda p: (len(str(p)), str(p)))
    if limit:
        files = files[:limit]
    return files


def scan_file(
    fpath: Path,
    lenses: list[str],
    output_dir: Path,
) -> dict:
    sys.path.insert(0, str(PROJECT_ROOT))
    from utils.alpha.llm.consensus import run_consensus

    content = fpath.read_text(encoding="utf-8", errors="replace")[:MAX_CHARS_PER_FILE]
    rel_path = str(fpath.relative_to(PROJECT_ROOT)).replace("\\", "/")
    results: list[dict] = []

    for lens in lenses:
        out_file = output_dir / f"{fpath.stem}_{lens}.json"
        r = run_consensus(
            artifact=content,
            lens=lens,
            models=["deepseek", "glm"],
            judge_mode="vote",
            output_path=out_file,
        )
        results.append({
            "lens": lens,
            "confirmed": len(r.confirmed_findings),
            "mode": r.mode,
            "success": r.success,
            "findings": [
                {
                    "id": f.id, "severity": f.severity,
                    "title": f.title, "description": f.description,
                    "location": f.location, "model": f.model,
                }
                for f in r.confirmed_findings
            ],
        })

    total_confirmed = sum(r["confirmed"] for r in results)
    return {
        "file": rel_path,
        "lenses": lenses,
        "total_confirmed": total_confirmed,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="MMR 代码质量+安全扫描")
    parser.add_argument("--priority", choices=["p0", "p1", "p2", "all"],
                        help="风险优先级 (p0=核心 p1=关键 p2=支撑 all=全部)")
    parser.add_argument("--target", help="指定目录或文件路径 (覆盖 --priority)")
    parser.add_argument("--file", help="扫描单个文件")
    parser.add_argument("--lens", default="correctness",
                        help="lens (correctness/testing/adversarial/all, 逗号分隔)")
    parser.add_argument("--limit", type=int, help="限制扫描文件数")
    parser.add_argument("--dry-run", action="store_true", help="仅列出待扫文件")
    parser.add_argument("--output", default="reports/mmr_reviews/code_scan",
                        help="输出目录")
    args = parser.parse_args()

    if args.file:
        targets = [args.file]
    elif args.target:
        targets = [args.target]
    elif args.priority:
        if args.priority == "all":
            targets = [v for vals in RISK_PRIORITY.values() for v in vals]
        else:
            targets = RISK_PRIORITY[args.priority]
    else:
        parser.error("需要 --priority / --target / --file 之一")

    files = collect_files(targets, args.limit)

    if args.lens == "all":
        lenses = ["correctness", "adversarial"]
    else:
        lenses = [s.strip() for s in args.lens.split(",") if s.strip()]

    print("=== MMR Code Scan ===")
    print(f"Targets: {targets}")
    print(f"Lenses: {lenses}")
    print(f"Files: {len(files)}")

    if args.dry_run:
        for f in files:
            rel = f.relative_to(PROJECT_ROOT)
            size = f.stat().st_size
            print(f"  {rel} ({size:,} bytes)")
        print(f"\n[DRY-RUN] {len(files)} files, estimated cost: ~${len(files) * len(lenses) * 0.02:.2f}")
        return 0

    output_dir = PROJECT_ROOT / args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    all_results: list[dict] = []
    total_confirmed = 0

    for i, fpath in enumerate(files, 1):
        rel = fpath.relative_to(PROJECT_ROOT)
        print(f"\n[{i}/{len(files)}] {rel} ...", end=" ", flush=True)
        try:
            result = scan_file(fpath, lenses, output_dir)
            all_results.append(result)
            total_confirmed += result["total_confirmed"]
            print(f"{result['total_confirmed']} confirmed")
        except (ValueError, TypeError, RuntimeError, OSError) as exc:
            print(f"ERROR: {exc}")
            all_results.append({"file": str(rel), "error": str(exc)})

    summary = {
        "scan_time": datetime.now().isoformat(),
        "targets": targets,
        "lenses": lenses,
        "files_scanned": len(files),
        "total_confirmed": total_confirmed,
        "results": all_results,
    }
    summary_path = output_dir / f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n=== Summary ===")
    print(f"Files scanned: {len(files)}")
    print(f"Total confirmed findings: {total_confirmed}")
    print(f"Report: {summary_path}")

    if total_confirmed > 0:
        print("\nConfirmed findings by file:")
        for r in all_results:
            if r.get("total_confirmed", 0) > 0:
                print(f"  {r['file']}: {r['total_confirmed']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
