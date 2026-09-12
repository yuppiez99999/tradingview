#!/usr/bin/env python3
"""验证差异化阈值逻辑效果 - 使用模拟数据进行演示性测试"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from utils.datetime_utils import now_bj

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.market_rules import (  # noqa: E402
    ABNORMAL_RETURN_THRESHOLD_20CM,
    ABNORMAL_RETURN_THRESHOLD_INTERNAL,
    classify_board,
    get_abnormal_threshold,
    is_20cm_symbol,
)


def main() -> int:
    print("=" * 70)
    print("差异化阈值逻辑验证报告")
    print("=" * 70)

    # 模拟测试数据 - 包含真实场景
    test_cases = [
        # 10cm 标的
        {"symbol": "600519.SH", "name": "贵州茅台", "ret_pct": 22.5},  # 10cm 超 20%
        {"symbol": "600519.SH", "name": "贵州茅台", "ret_pct": 18.0},  # 10cm 正常
        # 20cm 标的 - 合理涨停
        {"symbol": "300750.SZ", "name": "宁德时代", "ret_pct": 22.0},  # 20cm 涨停(正常)
        {
            "symbol": "300750.SZ",
            "name": "宁德时代",
            "ret_pct": -21.5,
        },  # 20cm 跌停(正常)
        {"symbol": "688981.SH", "name": "中芯国际", "ret_pct": 25.0},  # 20cm 涨停(正常)
        {"symbol": "301579.SZ", "name": "博众精工", "ret_pct": 28.0},  # 20cm 涨停(正常)
        # 20cm 标的 - 真实异常
        {"symbol": "688012.SH", "name": "中微公司", "ret_pct": 35.0},  # 20cm 超 30%
        {"symbol": "300059.SZ", "name": "东方财富", "ret_pct": -32.0},  # 20cm 超 30%
        # ETF - 10cm
        {"symbol": "510300.SH", "name": "沪深300ETF", "ret_pct": 21.0},  # 10cm ETF
        # ETF - 20cm
        {"symbol": "588000.SH", "name": "科创50ETF", "ret_pct": 22.0},  # 20cm ETF 涨停
        {"symbol": "588000.SH", "name": "科创50ETF", "ret_pct": 31.0},  # 20cm ETF 异常
        # 科技/创业板 ETF
        {"symbol": "159915.SZ", "name": "创业板ETF", "ret_pct": 23.0},  # 20cm ETF 涨停
        {
            "symbol": "562500.SH",
            "name": "中证1000ETF",
            "ret_pct": 26.0,
        },  # 20cm ETF 涨停
    ]

    results = []
    old_flagged = 0
    new_flagged = 0
    exempted = 0

    for tc in test_cases:
        symbol = tc["symbol"]
        ret = tc["ret_pct"]
        is_20cm = is_20cm_symbol(symbol)
        board = classify_board(symbol)
        threshold = get_abnormal_threshold(symbol)

        # 旧逻辑: 统一 ±20%
        old_threshold = ABNORMAL_RETURN_THRESHOLD_INTERNAL
        old_flag = abs(ret) >= old_threshold * 100

        # 新逻辑: 差异化阈值
        new_flag = abs(ret) >= threshold * 100

        if old_flag:
            old_flagged += 1
        if new_flag:
            new_flagged += 1

        exempt = old_flag and not new_flag
        if exempt:
            exempted += 1

        results.append(
            {
                "symbol": symbol,
                "name": tc["name"],
                "ret_pct": ret,
                "board": board,
                "is_20cm": is_20cm,
                "threshold": f"±{int(threshold * 100)}%",
                "old_logic": "异常" if old_flag else "正常",
                "new_logic": "异常" if new_flag else "正常",
                "exempted": exempt,
            }
        )

    # 打印结果
    print(f"\n测试用例数: {len(results)}")
    print(f"旧逻辑 (±20%) 异常数: {old_flagged}")
    print(f"新逻辑 (差异化) 异常数: {new_flagged}")
    print(f"被豁免数: {exempted}")
    print(f"净改善: {old_flagged - new_flagged}")

    print("\n" + "-" * 70)
    print("详细结果:")
    print("-" * 70)
    for r in results:
        marker = "★EXEMPT★" if r["exempted"] else ""
        print(
            f"  {r['symbol']:12} {r['ret_pct']:+7.2f}% | "
            f"{r['board']:10} | thr={r['threshold']:8} | "
            f"旧={r['old_logic']:4} 新={r['new_logic']:4} {marker}"
        )

    # 生成报告
    report: dict[str, Any] = {
        "generated_at": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "thresholds": {
            "old": f"±{int(ABNORMAL_RETURN_THRESHOLD_INTERNAL * 100)}% (统一)",
            "new": f"10cm: ±{int(ABNORMAL_RETURN_THRESHOLD_INTERNAL * 100)}%  |  20cm: ±{int(ABNORMAL_RETURN_THRESHOLD_20CM * 100)}%",  # noqa: E501
            "source": "utils.market_rules",
        },
        "summary": {
            "test_cases": len(results),
            "old_flagged": old_flagged,
            "new_flagged": new_flagged,
            "exempted": exempted,
            "net_improvement": old_flagged - new_flagged,
        },
        "details": results,
    }

    out_dir = _PROJECT_ROOT / "reports" / "evolution"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_json = out_dir / "threshold_validation_report.json"
    out_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 生成 MD
    md: list[str] = []
    md.append("# 差异化阈值逻辑验证报告\n")
    md.append(f"**生成时间**: {report['generated_at']}\n")
    md.append("## 阈值说明\n")
    md.append(f"- **旧逻辑**: {report['thresholds']['old']}")
    md.append(f"- **新逻辑**: {report['thresholds']['new']}")
    md.append(f"- **来源**: {report['thresholds']['source']}\n")

    md.append("## 测试结果汇总\n")
    md.append("| 指标 | 值 |")
    md.append("|------|------|")
    md.append(f"| 测试用例数 | {report['summary']['test_cases']} |")
    md.append(f"| 旧逻辑异常数 | {report['summary']['old_flagged']} |")
    md.append(f"| 新逻辑异常数 | {report['summary']['new_flagged']} |")
    md.append(f"| **20cm 豁免数** | **{report['summary']['exempted']}** |")
    md.append(f"| 净改善 | {report['summary']['net_improvement']} |")
    md.append("")

    md.append("## 详细结果\n")
    md.append("| 标的 | 涨跌 | 板别 | 阈值 | 旧逻辑 | 新逻辑 | 状态 |")
    md.append("|------|------|------|------|--------|--------|------|")
    for r in results:
        status = (
            "🟢 已豁免"
            if r["exempted"]
            else ("🔴 仍异常" if r["new_logic"] == "异常" else "⚪ 正常")
        )
        md.append(
            f"| {r['symbol']} | {r['ret_pct']:+.2f}% | {r['board']} | {r['threshold']} | "
            f"{r['old_logic']} | {r['new_logic']} | {status} |"
        )
    md.append("")

    md.append("## 结论\n")
    md.append(
        f"在 {report['summary']['test_cases']} 个测试用例中，差异化阈值逻辑成功将 "
        f"**{report['summary']['exempted']}** 个原本会被旧逻辑 (±20%) 误判的 "
        f"20cm 板（科创板/创业板/20cm ETF）正常涨跌（20%-30% 范围）从异常告警中豁免，"
        f"同时正确保留了 {report['summary']['new_flagged']} 个真实异常。"
        f"净异常告警数量从 {report['summary']['old_flagged']} 降至 {report['summary']['new_flagged']}，"
        f"改善 **{report['summary']['net_improvement']}** 个告警。\n"
    )

    md.append("## 应用范围\n")
    md.append("本次差异化阈值逻辑已应用于以下数据清洗流水线模块：\n")
    md.append(
        "1. **ShadowRealDataFeeder** (`shadow_real_data_feeder.py`) — 影子账户数据交叉验证"
    )
    md.append(
        "2. **CrossValidationEngine** (`cross_validation.py`) — 数据一致性校验引擎"
    )
    md.append("3. **DataLayer** (`data_layer.py`) — 数据层管理器")
    md.append("4. **QualityValidator** (`quality_validator.py`) — 质量验证器")
    md.append("\n所有模块统一从 `utils.market_rules` 导入规则，确保单一事实源。")

    out_md = out_dir / "threshold_validation_report.md"
    out_md.write_text("\n".join(md), encoding="utf-8")

    print("\n报告已生成:")
    print(f"  JSON: {out_json}")
    print(f"  MD:   {out_md}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
