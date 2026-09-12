#!/usr/bin/env python3
"""Mock 数据集验证 - 构造 20cm 涨停 + 10cm 跌停等场景验证差异化阈值逻辑"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

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


def build_mock_dataset() -> list[dict[str, Any]]:
    """构造覆盖关键场景的 Mock 数据集。

    场景分布:
      - 20cm 涨停 (20% ≤ ret < 30%, 应被豁免)
      - 20cm 跌停 (-30% < ret ≤ -20%, 应被豁免)
      - 20cm 真实异常 (|ret| > 30%, 仍异常)
      - 10cm 涨停 (ret < 20%, 正常)
      - 10cm 跌停 (-20% < ret ≤ -10%, 正常)
      - 10cm 真实异常 (|ret| ≥ 20%, 仍异常)
      - 20cm ETF 涨停
      - 10cm ETF 异常
    """
    return [
        # === 20cm 标的 - 涨停 (应被豁免) ===
        {
            "symbol": "300750.SZ",
            "name": "宁德时代",
            "ret_pct": 20.00,
            "scenario": "20cm 涨停",
        },
        {
            "symbol": "300750.SZ",
            "name": "宁德时代",
            "ret_pct": 21.50,
            "scenario": "20cm 涨停",
        },
        {
            "symbol": "688981.SH",
            "name": "中芯国际",
            "ret_pct": 25.00,
            "scenario": "20cm 涨停",
        },
        {
            "symbol": "301579.SZ",
            "name": "博众精工",
            "ret_pct": 28.50,
            "scenario": "20cm 涨停",
        },
        {
            "symbol": "300059.SZ",
            "name": "东方财富",
            "ret_pct": 29.99,
            "scenario": "20cm 涨停(临界)",
        },
        # === 20cm 标的 - 跌停 (应被豁免) ===
        {
            "symbol": "300750.SZ",
            "name": "宁德时代",
            "ret_pct": -20.00,
            "scenario": "20cm 跌停",
        },
        {
            "symbol": "688981.SH",
            "name": "中芯国际",
            "ret_pct": -25.00,
            "scenario": "20cm 跌停",
        },
        {
            "symbol": "301579.SZ",
            "name": "博众精工",
            "ret_pct": -29.50,
            "scenario": "20cm 跌停",
        },
        # === 20cm 真实异常 (|ret| > 30%, 仍应被标记) ===
        {
            "symbol": "688012.SH",
            "name": "中微公司",
            "ret_pct": 30.01,
            "scenario": "20cm 超阈值",
        },
        {
            "symbol": "688012.SH",
            "name": "中微公司",
            "ret_pct": 35.00,
            "scenario": "20cm 真异常",
        },
        {
            "symbol": "300059.SZ",
            "name": "东方财富",
            "ret_pct": -32.00,
            "scenario": "20cm 真异常",
        },
        {
            "symbol": "300059.SZ",
            "name": "东方财富",
            "ret_pct": -45.00,
            "scenario": "20cm 极端异常",
        },
        # === 10cm 标的 - 正常涨停 (ret < 20%) ===
        {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "ret_pct": 10.00,
            "scenario": "10cm 涨停(正常)",
        },
        {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "ret_pct": 9.99,
            "scenario": "10cm 涨停(临界)",
        },
        {
            "symbol": "601318.SH",
            "name": "中国平安",
            "ret_pct": 5.50,
            "scenario": "10cm 正常波动",
        },
        {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "ret_pct": 0.00,
            "scenario": "10cm 无波动",
        },
        # === 10cm 标的 - 跌停 (应被标记, 触发 20% 阈值) ===
        {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "ret_pct": -10.00,
            "scenario": "10cm 跌停(正常)",
        },
        {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "ret_pct": -9.99,
            "scenario": "10cm 跌停(临界)",
        },
        {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "ret_pct": -15.00,
            "scenario": "10cm 中度下跌",
        },
        {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "ret_pct": -18.50,
            "scenario": "10cm 接近阈值",
        },
        # === 10cm 真实异常 (|ret| ≥ 20%, 仍应被标记) ===
        {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "ret_pct": 20.00,
            "scenario": "10cm 真异常",
        },
        {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "ret_pct": -20.00,
            "scenario": "10cm 真异常",
        },
        {
            "symbol": "601318.SH",
            "name": "中国平安",
            "ret_pct": -25.00,
            "scenario": "10cm 真异常",
        },
        {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "ret_pct": 22.50,
            "scenario": "10cm 真异常",
        },
        # === 20cm ETF - 涨停 (应被豁免) ===
        {
            "symbol": "588000.SH",
            "name": "科创50ETF",
            "ret_pct": 20.00,
            "scenario": "20cm ETF 涨停",
        },
        {
            "symbol": "588000.SH",
            "name": "科创50ETF",
            "ret_pct": 25.50,
            "scenario": "20cm ETF 涨停",
        },
        {
            "symbol": "562500.SH",
            "name": "中证1000ETF",
            "ret_pct": 26.00,
            "scenario": "20cm ETF 涨停",
        },
        {
            "symbol": "562500.SH",
            "name": "中证1000ETF",
            "ret_pct": 28.00,
            "scenario": "20cm ETF 涨停",
        },
        # 159915 易方达创业板ETF — 显式白名单中的 20cm 创业板 ETF (修正后应被豁免)
        {
            "symbol": "159915.SZ",
            "name": "创业板ETF",
            "ret_pct": 22.00,
            "scenario": "20cm ETF 涨停",
        },
        {
            "symbol": "159977.SZ",
            "name": "国泰创业板ETF",
            "ret_pct": 27.50,
            "scenario": "20cm ETF 涨停",
        },
        # === 20cm ETF - 真实异常 ===
        {
            "symbol": "588000.SH",
            "name": "科创50ETF",
            "ret_pct": 31.00,
            "scenario": "20cm ETF 真异常",
        },
        {
            "symbol": "588000.SH",
            "name": "科创50ETF",
            "ret_pct": -35.00,
            "scenario": "20cm ETF 真异常",
        },
        {
            "symbol": "159915.SZ",
            "name": "创业板ETF",
            "ret_pct": -32.00,
            "scenario": "20cm ETF 真异常",
        },
        # === 10cm ETF - 正常涨停 ===
        {
            "symbol": "510300.SH",
            "name": "沪深300ETF",
            "ret_pct": 5.00,
            "scenario": "10cm ETF 涨停",
        },
        {
            "symbol": "510050.SH",
            "name": "50ETF",
            "ret_pct": 9.99,
            "scenario": "10cm ETF 涨停",
        },
        # 159919 嘉实沪深300ETF — 159 段但非创业板, 应走 10cm
        {
            "symbol": "159919.SZ",
            "name": "沪深300ETF",
            "ret_pct": 8.50,
            "scenario": "10cm ETF 涨停",
        },
        # === 10cm ETF - 真实异常 ===
        {
            "symbol": "510300.SH",
            "name": "沪深300ETF",
            "ret_pct": 21.00,
            "scenario": "10cm ETF 真异常",
        },
        {
            "symbol": "510050.SH",
            "name": "50ETF",
            "ret_pct": -22.50,
            "scenario": "10cm ETF 真异常",
        },
        # 159919 (10cm ETF) 超 20% 应被标记
        {
            "symbol": "159919.SZ",
            "name": "沪深300ETF",
            "ret_pct": 23.00,
            "scenario": "10cm ETF 真异常",
        },
        # === 临界值测试 ===
        {
            "symbol": "300750.SZ",
            "name": "宁德时代",
            "ret_pct": 19.99,
            "scenario": "20cm 接近涨停阈值(正常)",
        },
        {
            "symbol": "300750.SZ",
            "name": "宁德时代",
            "ret_pct": -19.99,
            "scenario": "20cm 接近跌停阈值(正常)",
        },
        {
            "symbol": "688981.SH",
            "name": "中芯国际",
            "ret_pct": 30.00,
            "scenario": "20cm 临界(>=30% 异常)",
        },
    ]


def evaluate(tc: dict[str, Any]) -> dict[str, Any]:
    symbol = tc["symbol"]
    ret = tc["ret_pct"]
    is_20cm = is_20cm_symbol(symbol)
    board = classify_board(symbol)
    threshold = get_abnormal_threshold(symbol)

    # 旧逻辑: 统一 ±20%
    old_flag = abs(ret) >= ABNORMAL_RETURN_THRESHOLD_INTERNAL * 100
    # 新逻辑: 差异化阈值
    new_flag = abs(ret) >= threshold * 100

    exempted = old_flag and not new_flag

    # 判定期望行为是否符合
    if is_20cm:
        if abs(ret) < 20:
            # 20cm 标的 < 20%: 应正常
            correct = not new_flag
        elif abs(ret) < 30:
            # 20cm 标的 20%-30%: 应被豁免
            correct = exempted
        else:
            # 20cm 标的 >= 30%: 仍应异常
            correct = new_flag
    else:
        if abs(ret) < 20:
            # 10cm 标的 < 20%: 应正常
            correct = not new_flag
        else:
            # 10cm 标的 >= 20%: 仍应异常
            correct = new_flag

    return {
        "symbol": symbol,
        "name": tc["name"],
        "scenario": tc["scenario"],
        "ret_pct": ret,
        "board": board,
        "is_20cm": is_20cm,
        "threshold_new": f"±{int(threshold * 100)}%",
        "threshold_old": "±20%",
        "old_flagged": old_flag,
        "new_flagged": new_flag,
        "exempted": exempted,
        "correct": correct,
    }


def main() -> int:
    print("=" * 80)
    print("Mock 数据集验证: 差异化阈值逻辑")
    print("=" * 80)

    dataset = build_mock_dataset()
    print(f"\nMock 数据集大小: {len(dataset)} 条")
    print(
        f"  20cm 标的涨停/跌停:    {sum(1 for d in dataset if is_20cm_symbol(d['symbol']) and 20 <= abs(d['ret_pct']) < 30)} 条"  # noqa: E501
    )
    print(
        f"  20cm 标的真实异常:     {sum(1 for d in dataset if is_20cm_symbol(d['symbol']) and abs(d['ret_pct']) >= 30)} 条"  # noqa: E501
    )
    print(
        f"  10cm 标的跌停/异常:    {sum(1 for d in dataset if not is_20cm_symbol(d['symbol']) and abs(d['ret_pct']) >= 10)} 条"  # noqa: E501
    )

    results = [evaluate(tc) for tc in dataset]

    old_total = sum(1 for r in results if r["old_flagged"])
    new_total = sum(1 for r in results if r["new_flagged"])
    exempted = sum(1 for r in results if r["exempted"])
    correct = sum(1 for r in results if r["correct"])
    incorrect = [r for r in results if not r["correct"]]

    print("\n" + "=" * 80)
    print("验证结果汇总")
    print("=" * 80)
    print(f"  旧逻辑 (±20%) 异常数:      {old_total}")
    print(f"  新逻辑 (差异化) 异常数:    {new_total}")
    print(f"  被豁免 (20cm 20%-30%):     {exempted}")
    print(f"  逻辑正确数:                {correct}/{len(results)}")
    print(f"  逻辑错误数:                {len(incorrect)}")

    print("\n" + "-" * 80)
    print("分类详情:")
    print("-" * 80)
    scenarios: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        scenarios.setdefault(r["scenario"], []).append(r)

    for scenario, items in sorted(scenarios.items()):
        old = sum(1 for r in items if r["old_flagged"])
        new = sum(1 for r in items if r["new_flagged"])
        exmp = sum(1 for r in items if r["exempted"])
        print(
            f"  [{scenario:25s}] 共 {len(items):2} 条 | 旧异常={old} 新异常={new} 豁免={exmp}"
        )

    print("\n" + "-" * 80)
    print("被豁免标的明细 (旧逻辑异常 → 新逻辑正常):")
    print("-" * 80)
    print(f"  {'标的':12} {'涨跌':>8}  {'板别':6} {'阈值':8}  {'场景'}")
    for r in results:
        if r["exempted"]:
            print(
                f"  {r['symbol']:12} {r['ret_pct']:+7.2f}%  {r['board']:6} {r['threshold_new']:8}  {r['scenario']}"
            )

    print("\n" + "-" * 80)
    print("仍异常标的明细 (真实异常, 保留告警):")
    print("-" * 80)
    print(f"  {'标的':12} {'涨跌':>8}  {'板别':6} {'阈值':8}  {'场景'}")
    for r in results:
        if r["new_flagged"]:
            print(
                f"  {r['symbol']:12} {r['ret_pct']:+7.2f}%  {r['board']:6} {r['threshold_new']:8}  {r['scenario']}"
            )

    # 断言
    print("\n" + "=" * 80)
    print("断言检查")
    print("=" * 80)
    assertions = [
        (
            "20cm 涨停场景全部被豁免",
            all(
                r["exempted"]
                for r in results
                if "20cm 涨停" in r["scenario"]
                and "临界" not in r["scenario"]
                and "ETF 涨停" not in r["scenario"]
            ),
        ),
        (
            "20cm ETF 涨停场景全部被豁免",
            all(r["exempted"] for r in results if r["scenario"] == "20cm ETF 涨停"),
        ),
        (
            "20cm 跌停场景全部被豁免",
            all(r["exempted"] for r in results if "20cm 跌停" in r["scenario"]),
        ),
        (
            "20cm 真异常场景全部仍异常",
            all(
                r["new_flagged"]
                for r in results
                if "20cm 真异常" in r["scenario"] or "20cm 超阈值" in r["scenario"]
            ),
        ),
        (
            "20cm ETF 真异常场景全部仍异常",
            all(
                r["new_flagged"] for r in results if r["scenario"] == "20cm ETF 真异常"
            ),
        ),
        (
            "10cm 真异常场景全部仍异常",
            all(r["new_flagged"] for r in results if "10cm 真异常" in r["scenario"]),
        ),
        (
            "10cm ETF 真异常场景全部仍异常",
            all(
                r["new_flagged"] for r in results if r["scenario"] == "10cm ETF 真异常"
            ),
        ),
        (
            "10cm 涨停/跌停(正常)场景未被误判",
            all(
                not r["new_flagged"]
                for r in results
                if "10cm" in r["scenario"]
                and "真异常" not in r["scenario"]
                and "ETF" not in r["scenario"]
            ),
        ),
        (
            "20cm 临界(>=30%) 被正确标记异常",
            all(r["new_flagged"] for r in results if "20cm 临界" in r["scenario"]),
        ),
    ]

    all_pass = True
    for desc, ok in assertions:
        mark = "✓ PASS" if ok else "✗ FAIL"
        print(f"  {mark}  {desc}")
        if not ok:
            all_pass = False

    # 生成报告
    report: dict[str, Any] = {
        "generated_at": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "thresholds": {
            "old": f"±{int(ABNORMAL_RETURN_THRESHOLD_INTERNAL * 100)}% (统一)",
            "new": f"10cm: ±{int(ABNORMAL_RETURN_THRESHOLD_INTERNAL * 100)}%  |  20cm: ±{int(ABNORMAL_RETURN_THRESHOLD_20CM * 100)}%",  # noqa: E501
            "source": "utils.market_rules",
        },
        "summary": {
            "dataset_size": len(dataset),
            "old_flagged": old_total,
            "new_flagged": new_total,
            "exempted": exempted,
            "net_improvement": old_total - new_total,
            "logic_correct": correct,
            "logic_incorrect": len(incorrect),
        },
        "assertions": [{"desc": d, "passed": ok} for d, ok in assertions],
        "details": results,
    }

    out_dir = _PROJECT_ROOT / "reports" / "evolution"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "mock_threshold_test_report.json"
    out_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 生成 MD
    md: list[str] = []
    md.append("# Mock 数据集验证报告 — 差异化阈值逻辑\n")
    md.append(f"**生成时间**: {report['generated_at']}\n")
    md.append("## 阈值说明\n")
    md.append(f"- **旧逻辑**: {report['thresholds']['old']}")
    md.append(f"- **新逻辑**: {report['thresholds']['new']}")
    md.append(f"- **来源**: {report['thresholds']['source']}\n")

    md.append("## Mock 数据集覆盖场景\n")
    md.append("| 场景 | 用例数 |")
    md.append("|------|--------|")
    for scenario, items in sorted(scenarios.items()):
        md.append(f"| {scenario} | {len(items)} |")
    md.append("")

    md.append("## 验证结果汇总\n")
    md.append("| 指标 | 值 |")
    md.append("|------|------|")
    md.append(f"| 数据集大小 | {report['summary']['dataset_size']} |")
    md.append(f"| 旧逻辑异常数 (±20%) | {report['summary']['old_flagged']} |")
    md.append(f"| 新逻辑异常数 (差异化) | {report['summary']['new_flagged']} |")
    md.append(f"| **20cm 豁免数** | **{report['summary']['exempted']}** |")
    md.append(f"| 净改善 | {report['summary']['net_improvement']} |")
    md.append(
        f"| 逻辑正确 | {report['summary']['logic_correct']}/{report['summary']['dataset_size']} |"
    )
    md.append(f"| 逻辑错误 | {report['summary']['logic_incorrect']} |")
    md.append("")

    md.append("## 断言检查\n")
    md.append("| 状态 | 断言 |")
    md.append("|------|------|")
    for a in report["assertions"]:
        status = "✓ PASS" if a["passed"] else "✗ FAIL"
        md.append(f"| {status} | {a['desc']} |")
    md.append("")

    md.append("## 被豁免标的 (旧逻辑异常 → 新逻辑正常)\n")
    md.append("| 标的 | 名称 | 涨跌 | 板别 | 场景 |")
    md.append("|------|------|------|------|------|")
    for r in results:
        if r["exempted"]:
            md.append(
                f"| {r['symbol']} | {r['name']} | {r['ret_pct']:+.2f}% | {r['board']} | {r['scenario']} |"
            )
    md.append("")

    md.append("## 仍异常标的 (真实异常, 保留告警)\n")
    md.append("| 标的 | 名称 | 涨跌 | 板别 | 阈值 | 场景 |")
    md.append("|------|------|------|------|------|------|")
    for r in results:
        if r["new_flagged"]:
            md.append(
                f"| {r['symbol']} | {r['name']} | {r['ret_pct']:+.2f}% | {r['board']} | {r['threshold_new']} | {r['scenario']} |"  # noqa: E501
            )
    md.append("")

    md.append("## 结论\n")
    md.append(
        f"在 {report['summary']['dataset_size']} 条 Mock 数据上验证："
        f"差异化阈值逻辑正确豁免了 {report['summary']['exempted']} 个 20cm 板（科创板/创业板/20cm ETF）"
        f"在 20%-30% 范围内的合理涨跌停，同时正确保留了 {report['summary']['new_flagged']} 个真实异常。"
        f"全部断言通过：**{'是' if all_pass else '否'}**。"
    )

    out_md = out_dir / "mock_threshold_test_report.md"
    out_md.write_text("\n".join(md), encoding="utf-8")

    print("\n报告已生成:")
    print(f"  JSON: {out_json}")
    print(f"  MD:   {out_md}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
