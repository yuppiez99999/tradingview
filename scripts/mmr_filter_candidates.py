"""MMR L3 候选过滤: 从 ocr 产出中提取 high/critical 候选缺陷

用法:
    python scripts/mmr_filter_candidates.py \
        --input reports/ocr_reviews/pr_42.json \
        --severity high,critical \
        --output ocr_high_critical.json

    python scripts/mmr_filter_candidates.py \
        --input ocr_results/ocr_pr_review.json \
        --severity high,critical \
        --output ocr_high_critical.json \
        --format consensus

--format consensus: 输出 Finding 列表 (供 consensus.py 的 candidates 参数消费)
--format raw: 输出原始 ocr comment 列表 (默认)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _extract_comments(data: dict | list) -> list[dict]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("comments", "findings", "issues", "results"):
            val = data.get(key)
            if isinstance(val, list):
                return val
        return [data]
    return []


def filter_candidates(
    ocr_data: dict | list,
    severities: list[str],
) -> list[dict]:
    comments = _extract_comments(ocr_data)
    sev_set = {s.strip().lower() for s in severities}
    return [c for c in comments if c.get("severity", "medium").lower() in sev_set]


def to_consensus_findings(filtered: list[dict]) -> list[dict]:
    findings = []
    for i, c in enumerate(filtered):
        findings.append({
            "id": c.get("id", f"OCR{i + 1}"),
            "lens": "correctness",
            "severity": c.get("severity", "medium"),
            "title": c.get("title", c.get("message", "")[:80]),
            "description": c.get("message", c.get("description", "")),
            "evidence": c.get("evidence", c.get("snippet", "")),
            "location": c.get("file", c.get("location", "")),
            "model": "ocr-glm",
        })
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="MMR L3 候选过滤")
    parser.add_argument("--input", required=True, help="ocr 产出 JSON 路径")
    parser.add_argument("--severity", default="high,critical", help="保留的严重度(逗号分隔)")
    parser.add_argument("--output", required=True, help="输出 JSON 路径")
    parser.add_argument("--format", choices=["raw", "consensus"], default="raw",
                        help="输出格式: raw=原始comment | consensus=Finding列表")
    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"[ERROR] 输入文件不存在: {in_path}", file=sys.stderr)
        return 1

    with open(in_path, encoding="utf-8") as f:
        data = json.load(f)

    severities = [s.strip() for s in args.severity.split(",") if s.strip()]
    filtered = filter_candidates(data, severities)

    if args.format == "consensus":
        output = to_consensus_findings(filtered)
    else:
        output = filtered

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[OK] 过滤完成: {len(filtered)} 条 (severity in {severities}) -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
